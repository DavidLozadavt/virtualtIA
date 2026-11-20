"""
scripts/eval_nlu.py — Evaluación offline de la comprensión de Lyra (NexiService y SchoolSena).

Reproduce el camino de `POST /chat` hasta el punto en que se llamaría al modelo
externo: guard → limpieza del habla → detect_intent → interceptores. No llama a
OpenAI ni escribe en `lyra_db`. Las herramientas de SchoolSena se sustituyen por
datos fijos (su API exige JWT) y la reserva de NexiService no inserta nada.

Cada turno termina en una de estas salidas:

    local-ok        resuelto sin modelo y con la capacidad esperada
    local-wrong     resuelto sin modelo pero con otra capacidad (el peor caso)
    clarify         Lyra pregunta antes de actuar
    fallback        respuesta genérica de "no te entendí"
    gate            nada local lo resolvió: con el modelo encendido iría a GPT

Uso:
    python scripts/eval_nlu.py eval/schoolsena_heldout.yaml [más archivos] [--verbose] [--json out.json]

Formato de los casos (YAML):
    - id: s001
      role: aprendiz            # sólo SchoolSena
      turns:
        - say: "que clases tengo hoy"
          expect: schedule:today        # una etiqueta o una lista de aceptables
"""

import argparse
import asyncio
import collections
import json
import sys
import time
import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings  # noqa: E402

settings.LLM_EXTERNAL_ENABLED = False  # nunca se sale a la API desde la evaluación


# ── Stubs ─────────────────────────────────────────────────────────────────────

_DAYS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]


def _school_fixture(tool: str):
    """
    Respuesta CRUDA de cada endpoint `lyra/*` de SchoolSena, con la forma exacta
    de `LyraController` (modelos con sus relaciones, envolturas `data`, campos
    extra como `timestamp`, `moneda` o `numeroCuentaBancaria`). Pasa por el mismo
    `_safe_result` que las herramientas reales: si un presentador no sabe leer
    el payload, la evaluación lo ve.
    """
    now = datetime.now()
    today = _DAYS[now.weekday()]
    tomorrow = _DAYS[(now.weekday() + 1) % 7]
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d 10:15:00")

    def clase(i, dia, ini, fin, materia, codigo, fid):
        return {"id": i, "horaInicial": ini, "horaFinal": fin, "dia": {"id": 1, "dia": dia},
                "gradoMateria": {"id": 3, "materia": {"id": 9, "nombreMateria": materia}},
                "ficha": {"id": fid, "codigo": codigo}}

    schedule = [clase(1, today, "07:00:00", "09:00:00", "Programación", "2558", 21),
                clase(2, tomorrow, "10:00:00", "12:00:00", "Inglés", "2601", 23)]
    return {
        "get_clases_hoy": {"data": schedule[:1]},
        "get_horario": {"role": "estudiante", "data": schedule},
        "get_actividades_pendientes": [{"tituloActividad": "TALLER DE BASES DE DATOS", "descripcionActividad": "DDL parte 1",
                                        "nombreMateria": "Bases de datos"}],
        "get_notas": [
            {"tituloActividad": "EXAMEN DE LOGICA DE PROGRAMACION", "nombreMateria": "Programación",
             "calificacionNumerica": "4.5", "calificacionEstandart": None, "fechaCalificacion": yesterday},
            {"tituloActividad": "PROYECTO TRIKI CON JAVA SCRIPT", "nombreMateria": "Programación",
             "calificacionNumerica": None, "calificacionEstandart": "A", "fechaCalificacion": "2026-05-04 14:59:15"},
        ],
        "get_fichas_activas": [{"id": 21, "codigo": "2558", "aperturarPrograma": {"programa": {"nombrePrograma": "ADSO"}}},
                               {"id": 23, "codigo": "2601", "aperturarPrograma": {"programa": {"nombrePrograma": "Contabilidad"}}}],
        "get_estudiantes_ficha": {"ficha_id": 21, "codigo": "2558", "total_estudiantes": 28},
        "get_entregas": [{"id": 1, "nombre1": "Ana", "apellido1": "Ruiz", "archivo": "a.pdf", "fechaCalificacion": yesterday}],
        "get_nomina_resumen": {"total_nomina_mensual": 9000, "total_contratos": 1, "moneda": "COP",
                               "timestamp": "2026-11-17T10:00:00.000000Z"},
        "get_lista_instructores": [{"id": 1, "nombre": "Carlos Gómez", "tipo": "Prestación de servicios", "fecha_fin": "2026-12-01"}],
        "get_contratos_vencimiento": [{"id": 5, "numeroContrato": "CT-24", "fechaFinalContrato": "2026-12-01",
                                       "numeroCuentaBancaria": "123456789012", "idpersona": 7,
                                       "persona": {"id": 7, "nombre1": "Carlos", "nombre2": None, "apellido1": "Gómez", "apellido2": "Ruiz"}}],
        "get_resumen_admin": {"regional": "Cauca", "instructores_activos": 12, "fichas_en_ejecucion": 8,
                              "aprendices_matriculados": 240, "sedes_activas": 3, "timestamp": "2026-11-17T10:00:00.000000Z"},
    }.get(tool, [])


_BAD_REPLY = (
    ("viñeta vacía", re.compile(r"^\s*-\s*$", re.MULTILINE)),
    ("clave interna", re.compile(r"\w+_\w+")),
    ("timestamp crudo", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:")),
    ("moneda cruda", re.compile(r"\bCOP\b")),
    ("cuenta bancaria", re.compile(r"123456789012")),
)


def content_issues(project: str, reply: str) -> list:
    """Defectos de presentación en una respuesta de SchoolSena."""
    if project != "schoolsena":
        return []
    return [name for name, rx in _BAD_REPLY if rx.search(reply or "")]


class Recorder:
    def __init__(self):
        self.calls = []
        self.stage_c = False

    def reset(self):
        self.calls = []


REC = Recorder()


def install_stubs():
    """Sustituye las herramientas con efectos; devuelve cómo deshacerlo."""
    from tools import schoolsena as ss_tools
    import tools.nexiservice as nexi_tools

    saved = ({n: e["handler"] for n, e in ss_tools.TOOLS_REGISTRY.items()}, nexi_tools.confirm_appointment)

    def restore():
        for n, h in saved[0].items():
            ss_tools.TOOLS_REGISTRY[n]["handler"] = h
        nexi_tools.confirm_appointment = saved[1]

    # Capacidades efectivas simuladas por perfil: lo que devolvería el backend
    # (`lyra/capacidades`) con los permisos típicos de cada uno. Es consulta
    # interna, no una acción del turno: no se registra.
    _APRENDIZ = ["clases", "horario", "notas", "pendientes", "periodos_propios"]
    _INSTRUCTOR = _APRENDIZ + ["fichas", "estudiantes", "entregas"]
    _GESTION = _INSTRUCTOR + ["resumen", "nomina", "instructores", "contratos", "centros", "sedes",
                              "ambientes", "periodos", "jornadas", "planeacion"]

    async def fake_caps(params=None, context=None, **kw):
        rol = str(((context or {}).get("user_data") or {}).get("role") or "").lower()
        consultas = (_GESTION if any(x in rol for x in ("coord", "admin")) else
                     _INSTRUCTOR if any(x in rol for x in ("instructor", "docente")) else
                     _APRENDIZ if any(x in rol for x in ("estudiante", "aprendiz")) else [])
        # Permisos típicos del perfil (los de sus roles en la base local).
        import json as _json
        from pathlib import Path as _P
        todos = list(_json.loads((_P(__file__).resolve().parent.parent / "projects" / "school_inventario.json")
                                 .read_text(encoding="utf-8"))["permisos"])
        aprendiz = ["GESTION_APRENDIZ", "DASHBOARD", "AMBIENTE_VIRTUAL", "AULA_VIRTUAL_APRENDIZ_CLASES",
                    "AULA_VIRTUAL_APRENDIZ_ACTIVIDADES", "AULA_VIRTUAL_APRENDIZ_GRUPOS", "AULA_VIRTUAL_APRENDIZ_BIBLIOTECA"]
        instructor = aprendiz + [p for p in todos if p.startswith("AULA_VIRTUAL_INSTRUCTOR")] + [
            "GESTION_INSTRUCTOR", "GESTION_INSTRUCTOR_PAGO", "GESTION_NOTAS_ACADEMICAS", "GESTION_PERIODOS"]
        coordinacion = [p for p in todos if not p.startswith(("AULA_VIRTUAL_INSTRUCTOR", "AULA_VIRTUAL_APRENDIZ"))]
        permisos = (coordinacion if consultas is _GESTION else instructor if consultas is _INSTRUCTOR else
                    aprendiz if consultas is _APRENDIZ else [])
        if consultas is _GESTION:
            consultas = consultas + ["regionales", "eventos", "crear_evento", "formularios", "carpetas",
                                     "configuracion_pagos", "crear_formulario"]
        return {"result": {"consultas": consultas, "secciones": [], "permisos": permisos}, "error": None}

    for name, entry in ss_tools.TOOLS_REGISTRY.items():
        if name == "get_capacidades":
            entry["handler"] = fake_caps
            continue

        async def handler(params=None, context=None, _n=name, **kw):
            ctx = context or {}
            REC.calls.append({
                "tool": _n,
                "params": dict(params or {}),
                "timeframe": ctx.get("timeframe"),
                "day": ctx.get("target_day_override"),
            })
            return ss_tools._safe_result(_school_fixture(_n))
        entry["handler"] = handler

    async def fake_confirm(**kwargs):
        REC.calls.append({"tool": "confirm_appointment", "params": {k: str(v) for k, v in kwargs.items() if k != "user_data"}})
        return {"success": True, "message": "Reserva simulada", "appointment_id": 0}

    nexi_tools.confirm_appointment = fake_confirm
    return restore


# ── Ejecución de un turno ─────────────────────────────────────────────────────

ROLE_TOKEN_ROLES = {
    "aprendiz": "estudiante", "estudiante": "estudiante",
    "instructor": "instructor", "coordinador": "coordinador", "admin": "admin",
    "invitado": "invitado",
}

# Menú real que el front de SchoolSena envió en la prueba E2E (usuario de la
# base local), el mismo que usa scripts/chat_battery.py.
from chat_battery import SS_NAV  # noqa: E402


async def run_turn(project: str, text: str, state: dict, role: str, history: list):
    from orchestrator.guards import check_message
    from orchestrator.intent_router import detect_intent
    from orchestrator.interceptors import manager as interceptor_manager
    from orchestrator.tool_runner import run_agent_loop
    from core.semantic import ConversationState
    from orchestrator.context_builder import load_project_config, build_system_prompt

    REC.reset()
    REC.stage_c = False
    guarded = check_message(text)
    if guarded:
        return {"label": "guard", "reply": guarded["reply"], "intent": None, "gate": False}

    personality = "sena" if project == "schoolsena" else "nexo"
    cfg = load_project_config(project, personality)
    from core.semantic.utterance import prepare_for_understanding
    understood_text = prepare_for_understanding(text, project, cfg)
    user = {"id": 0, "external_user_id": "eval-user", "trust_level": 1, "name": "Prueba Eval",
            "role": ROLE_TOKEN_ROLES.get(role or "", role or "")}
    last_assistant = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
    # Igual que chat_service: SchoolSena no pasa por el router de NexiService.
    local_intent = {"intent": None, "args": {}} if project == "schoolsena" else detect_intent(
        understood_text, project,
        current_context={
            "last_assistant_msg": last_assistant,
            "semantic_state": state.get(ConversationState.STORAGE_KEY),
            "role": user["role"],
            "active_company_id": None,
        },
    )
    messages = build_system_prompt(
        project_config=cfg, user_trust_level=1, user_data=user,
        conversation_history=history, user_message=understood_text,
        local_intent=local_intent,
    )

    gate = {"hit": False}
    original = interceptor_manager.run_pre_llm_interceptors

    async def spy(*a, **k):
        res = await original(*a, **k)
        gate["hit"] = res is None
        return res

    interceptor_manager.run_pre_llm_interceptors = spy
    try:
        out = await run_agent_loop(
            engine=None, messages=messages, project_id=project, project_config=cfg,
            auth_header="Bearer eval", user_data=user, conversation_id="eval",
            tool_registry=_registry(project),
            local_intent=local_intent, final_data=state,
            nav_catalog=SS_NAV if project == "schoolsena" else [],
            current_path="/",
        )
    finally:
        interceptor_manager.run_pre_llm_interceptors = original

    fd = out.get("final_data") or {}
    reply = out.get("reply") or fd.get("reply") or ""
    return {
        "reply": reply,
        "intent": local_intent.get("intent"),
        "gate": gate["hit"],
        "final_data": fd,
        "calls": list(REC.calls),
        "label": classify(project, local_intent, fd, list(REC.calls), gate["hit"], reply),
        "stage_c": REC.stage_c,
    }


_REGISTRIES = {}


def _registry(project):
    from orchestrator.tool_registry import ToolRegistry
    if project not in _REGISTRIES:
        _REGISTRIES[project] = ToolRegistry.for_project(project)
    return _REGISTRIES[project]


def classify(project, intent, fd, calls, gate, reply):
    """Traduce lo que hizo Lyra a una etiqueta comparable con lo esperado."""
    if gate:
        return "gate"
    if project == "schoolsena":
        nav = fd.get("voice_action") == "navigate" or fd.get("nav_target") or fd.get("nav_not_found")
        if nav:
            return "nav"
        if fd.get("access_denied"):
            return "denied"
        if fd.get("school_outcome") in ("greeting", "help", "profile", "out_of_scope", "clarify", "denied",
                                        "permissions", "not_integrated", "create_flow", "module_row",
                                        "offer_declined", "create_cancelled", "summary", "create_paused"):
            return fd["school_outcome"]
        if calls:
            c = calls[-1]
            tool = c["tool"]
            if tool in ("get_horario", "get_clases_hoy"):
                tf = c.get("timeframe") or ("today" if tool == "get_clases_hoy" else "week")
                if tool == "get_horario" and c.get("day") and tf == "tomorrow":
                    tf = "tomorrow"
                if "fichas asignadas" in (reply or "").lower():
                    return "fichas_mine"
                return f"schedule:{tf}"
            return {
                "get_actividades_pendientes": "activities",
                "get_notas": "grades",
                "get_fichas_activas": "fichas_active",
                "get_estudiantes_ficha": "students",
                "get_entregas": "deliveries",
                "get_nomina_resumen": "payroll",
                "get_lista_instructores": "instructors",
                "get_contratos_vencimiento": "contracts",
                "get_resumen_admin": "dashboard",
            }.get(tool, tool)
        if fd.get("is_greeting"):
            low = (reply or "").lower()
            return "help" if ("puedo ayudarte con" in low and "hola" not in low) else "greeting"
        if fd.get("profile"):
            return "profile"
        if fd.get("geo_info"):
            return "geo_info"
        if fd.get("fallback"):
            return "fallback"
        if fd.get("needs_clarification") or fd.get("nav_options"):
            return "clarify"
        return "other"

    # NexiService: la etiqueta es la intención resuelta, más la marca de
    # aclaración o de respuesta genérica cuando corresponde.
    # Sin intención concreta el interceptor de NexiService conversa (handler
    # conversacional): para la evaluación es una respuesta "conversation".
    name = (intent or {}).get("intent") or "conversation"
    if name in ("semantic_clarify",) or fd.get("needs_clarification"):
        return f"clarify"
    if fd.get("_nexi_outcome"):
        return fd["_nexi_outcome"]
    return name


# greeting_named (fase 3): saludo que además propone el negocio que se parece a la
# palabra dicha ("hola vida"); es una respuesta social, sin acción.
LABEL_GROUPS = {"SOCIAL": ["greeting", "conversation", "farewell", "greeting_named"]}

OUTCOME_GROUPS = {"gate": "gate", "clarify": "clarify", "fallback": "fallback", "other": "fallback"}


async def evaluate(files, verbose=False, stage_c=False):
    """
    stage_c=True cuenta los turnos de NexiService que llamarían a la etapa C
    semántica (core/semantic/llm_resolver), que corre dentro de detect_intent y
    antes de la compuerta. No se llama a la API: un contador responde NINGUNO.
    """
    restore = install_stubs()
    if stage_c:
        from core.semantic import llm_resolver

        def counter(prompt):
            REC.stage_c = True
            return '{"categorias": [], "confianza": 0.0}'
        llm_resolver.set_completion_fn(counter)
    try:
        return await _evaluate(files, verbose)
    finally:
        restore()
        if stage_c:
            llm_resolver.set_completion_fn(None)


async def _evaluate(files, verbose=False):
    results = []
    for f in files:
        cases = yaml.safe_load(Path(f).read_text(encoding="utf-8")) or []
        project = "schoolsena" if "school" in Path(f).name else "nexiservice"
        for case in cases:
            state, history = {}, []
            for i, turn in enumerate(case["turns"]):
                t0 = time.perf_counter()
                try:
                    r = await run_turn(project, turn["say"], state, case.get("role"), history)
                except Exception as e:  # un caso que revienta cuenta como error, no tumba la batería
                    r = {"label": "crash", "reply": repr(e), "gate": False, "intent": None, "final_data": state}
                ms = (time.perf_counter() - t0) * 1000
                state = r.get("final_data") or state
                for k in ("reply", "properties", "voice_action", "voice_action_payload", "needs_clarification",
                          "clarification_question", "is_greeting", "access_denied", "fallback", "profile",
                          "geo_info", "error", "fichas_asignadas", "_nexi_outcome", "nav_options", "school_outcome",
                          "quick_replies", "needs_auth", "nav_target", "nav_not_found", "nav_unavailable",
                          "_escalation_reason", "_fallback_reply", "llm_error"):
                    # Lo que describe sólo a este turno no se arrastra al siguiente.
                    if k not in ("_school",):
                        state.pop(k, None)
                history = history + [{"role": "user", "content": turn["say"]},
                                     {"role": "assistant", "content": r.get("reply") or ""}]
                expect = turn.get("expect")
                if expect is None:
                    continue
                accepted = [expect] if isinstance(expect, str) else list(expect)
                # Clases de etiquetas equivalentes: una respuesta social es
                # social, se llame greeting o conversation.
                for alias, members in LABEL_GROUPS.items():
                    if alias in accepted:
                        accepted = [a for a in accepted if a != alias] + members
                got = r["label"]
                if got in accepted:
                    outcome = "local-ok" if got not in ("gate",) else "gate-expected"
                elif got in ("gate",):
                    outcome = "gate"
                elif got in ("clarify",):
                    outcome = "clarify"
                elif got in ("fallback", "other"):
                    outcome = "fallback"
                else:
                    outcome = "local-wrong"
                results.append({
                    "file": Path(f).name, "id": case["id"], "turn": i, "say": turn["say"],
                    "role": case.get("role"), "expect": accepted, "got": got, "outcome": outcome,
                    "ms": round(ms, 1), "reply": (r.get("reply") or "")[:160], "stage_c": r.get("stage_c", False),
                    "issues": content_issues(project, r.get("reply") or "") + (
                        ["nota ausente"] if got == "grades" and not re.search(r": (?:\d,\d|[A-E])\b", r.get("reply") or "")
                        and "No encontré calificaciones" not in (r.get("reply") or "") else []),
                })
                if verbose and outcome != "local-ok":
                    print(f"[{outcome:11}] {case['id']}#{i} ({case.get('role') or '-'}) «{turn['say']}» "
                          f"esperado={accepted} obtenido={got} | {(r.get('reply') or '')[:90]!r}")
    return results


def summarize(results):
    by_file = collections.defaultdict(list)
    for r in results:
        by_file[r["file"]].append(r)
    for f, rs in by_file.items():
        n = len(rs)
        c = collections.Counter(r["outcome"] for r in rs)
        ok = c["local-ok"] + c["gate-expected"]
        lat = sorted(r["ms"] for r in rs)
        p50 = lat[len(lat) // 2]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"\n== {f}: {n} turnos evaluados")
        print(f"   acierto           {ok:4d}  {ok / n:6.1%}")
        for k in ("local-wrong", "clarify", "fallback", "gate", "gate-expected"):
            print(f"   {k:17} {c[k]:4d}  {c[k] / n:6.1%}")
        sc = sum(1 for r in rs if r.get("stage_c"))
        to_gpt = sum(1 for r in rs if r["outcome"] == "gate" or r.get("stage_c"))
        print(f"   etapa C semántica {sc:4d}  {sc / n:6.1%}  (sólo se mide con --stage-c)")
        print(f"   resuelto sin GPT  {n - to_gpt:4d}  {(n - to_gpt) / n:6.1%}")
        print(f"   latencia local    p50={p50:.0f} ms  p95={p95:.0f} ms")
        bad = [r for r in rs if r.get("issues")]
        if bad:
            print(f"   DEFECTOS de presentación: {len(bad)}")
            for r in bad[:10]:
                print(f"     {r['id']}#{r['turn']} {r['issues']}: {r['reply'][:100]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--stage-c", action="store_true", help="contar llamadas a la etapa C semántica")
    a = ap.parse_args()
    import logging
    logging.disable(logging.CRITICAL)
    results = asyncio.run(evaluate(a.files, a.verbose, a.stage_c))
    summarize(results)
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
