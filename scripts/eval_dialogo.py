"""
scripts/eval_dialogo.py — Conversaciones completas por el camino real de /chat.

Cada escenario corre en una conversación nueva de `ChatService` (modelo externo
apagado, base local `vt_inventario`, escribe en `lyra_db` local) y cada turno se
comprueba contra lo que exige el YAML (ver la cabecera de eval/dialogos_fase3.yaml).

Además de las exigencias de cada turno se miden, por separado:
    intención   la intención resuelta está entre las aceptables
    entidades   los negocios mostrados / la entidad sobre la que se actúa son los correctos
    seguridad   no hay reserva ni navegación que el turno no pidió
    turno       todo lo exigido en el turno se cumple
    escenario   todos los turnos del escenario se cumplen

Uso:
    python scripts/eval_dialogo.py eval/dialogos_fase3.yaml [--verbose] [--json out.json] [--only d01]
"""
import argparse
import asyncio
import json
import logging
import re
import sys
import unicodedata
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings  # noqa: E402

settings.LLM_EXTERNAL_ENABLED = False

_BOOKING_RE = re.compile(r"agend\w* (tu )?cita|a que hora te gustaria|tienen la agenda libre|a nombre de quien")


def plain(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


class IntentSpy(logging.Handler):
    """Lee la intención con la que el interceptor atendió el turno."""
    def __init__(self):
        super().__init__(logging.INFO)
        self.intents = []

    def emit(self, record):
        m = re.search(r"\[INTERCEPTOR\] intent=(\S+)", record.getMessage())
        if m:
            self.intents.append(m.group(1))


def shown_businesses(r: dict) -> list:
    p = r.get("voice_action_payload") or {}
    if p.get("businesses"):
        return p["businesses"]
    for prop in r.get("properties") or []:
        if isinstance(prop, dict) and prop.get("businesses"):
            return prop["businesses"]
    return []


def acted_entity(r: dict):
    p = r.get("voice_action_payload") or {}
    m = re.search(r"/empresa/(\d+)", p.get("url") or "")
    if m:
        return int(m.group(1))
    if r.get("voice_action") == "fly_to_business" and p.get("business_id"):
        return int(p["business_id"])
    return None


def check_turn(exp: dict, r: dict, intent: str, history: list) -> dict:
    reply = plain(r.get("reply") or "")
    action = r.get("voice_action")
    biz = shown_businesses(r)
    fails = {"intent": [], "entities": [], "safety": [], "other": []}

    if exp.get("intent") and intent not in exp["intent"]:
        fails["intent"].append(f"intent={intent} ∉ {exp['intent']}")
    if "action" in exp:
        ok = exp["action"]
        if ok == "none" or ok == ["none"]:
            if action:
                fails["safety"].append(f"acción inesperada {action}")
        elif action not in ok:
            fails["other"].append(f"acción {action} ∉ {ok}")
    for fa in exp.get("forbid_action") or []:
        if action == fa:
            fails["safety"].append(f"acción prohibida {fa}")
    if exp.get("biz_cat"):
        if not biz:
            fails["entities"].append("no mostró negocios")
        for b in biz:
            if not re.search(exp["biz_cat"], plain(b.get("category") or "")):
                fails["entities"].append(f"categoría ajena: {b.get('name')} ({b.get('category')})")
    if exp.get("biz_name_not"):
        for b in biz:
            if re.search(exp["biz_name_not"], plain(b.get("name") or "")):
                fails["entities"].append(f"negocio no permitido: {b.get('name')}")
    if exp.get("target_from"):
        t, pos = exp["target_from"]
        lista = history[t - 1] if t - 1 < len(history) else []
        esperado = lista[pos - 1]["id"] if len(lista) >= pos else None
        got = acted_entity(r)
        if got is None and biz and len(biz) == 1:
            got = biz[0].get("id")
        if esperado is None or got != esperado:
            fails["entities"].append(f"entidad {got} ≠ esperada {esperado}")
    if exp.get("reply_re") and not re.search(exp["reply_re"], reply):
        fails["other"].append(f"respuesta sin /{exp['reply_re']}/")
    if exp.get("reply_not_re") and re.search(exp["reply_not_re"], reply):
        fails["other"].append(f"respuesta con /{exp['reply_not_re']}/")
    if exp.get("clarify"):
        if action or biz or "?" not in (r.get("reply") or ""):
            fails["safety" if action else "other"].append("no es una aclaración breve")
    if exp.get("no_booking") and (intent == "request_appointment" or _BOOKING_RE.search(reply)):
        fails["safety"].append("entró en reserva sin pedirla")
    return fails


async def run(path, verbose=False, only=None):
    from scripts.replay_turns import service
    from api.schemas import ChatRequest

    logging.disable(logging.NOTSET)  # replay_turns lo apaga al importarse
    for name in list(logging.root.manager.loggerDict):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    spy = IntentSpy()
    icpt = logging.getLogger("lyra.interceptors.nexiservice")
    icpt.handlers = [spy]
    icpt.propagate = False
    icpt.setLevel(logging.INFO)

    svc = service()
    escenarios = yaml.safe_load(open(path, encoding="utf-8"))
    tot = {"turnos": 0, "turno_ok": 0, "intent_n": 0, "intent_ok": 0, "ent_n": 0, "ent_ok": 0,
           "seg_n": 0, "seg_ok": 0, "esc": 0, "esc_ok": 0}
    detalle = []
    for esc in escenarios:
        if only and not esc["id"].startswith(only):
            continue
        cid = str(uuid.uuid4())
        history, esc_ok = [], True
        for i, turn in enumerate(esc["turns"], start=1):
            spy.intents.clear()
            req = ChatRequest(project_id=esc.get("project", "nexiservice"), user_id="eval-dialogo",
                              conversation_id=cid, message=turn["say"], active_city="Popayán")
            resp = await svc.process_message(req, session_id=cid, auth_header=None)
            r = resp.model_dump()
            intent = spy.intents[-1] if spy.intents else None
            fails = check_turn(turn, r, intent, history)
            history.append(shown_businesses(r))
            ok = not any(fails.values())
            esc_ok &= ok
            tot["turnos"] += 1
            tot["turno_ok"] += ok
            if turn.get("intent"):
                tot["intent_n"] += 1
                tot["intent_ok"] += not fails["intent"]
            if any(k in turn for k in ("biz_cat", "biz_name_not", "target_from")):
                tot["ent_n"] += 1
                tot["ent_ok"] += not fails["entities"]
            if any(k in turn for k in ("no_booking", "forbid_action", "action", "clarify")):
                tot["seg_n"] += 1
                tot["seg_ok"] += not fails["safety"]
            detalle.append({"id": esc["id"], "turn": i, "say": turn["say"], "intent": intent,
                            "action": r.get("voice_action"), "reply": r.get("reply"),
                            "fails": {k: v for k, v in fails.items() if v}})
            if verbose or not ok:
                marca = "ok " if ok else "XX "
                print(f"{marca}{esc['id']} t{i} «{turn['say']}» intent={intent} action={r.get('voice_action')}")
                if not ok:
                    print("     ", {k: v for k, v in fails.items() if v})
                    print("      L:", (r.get("reply") or "")[:220].replace("\n", " | "))
        tot["esc"] += 1
        tot["esc_ok"] += esc_ok

    pct = lambda a, b: f"{a}/{b} ({100 * a / b:.1f} %)" if b else "—"
    print("\n== Resumen")
    print("  escenarios resueltos completos:", pct(tot["esc_ok"], tot["esc"]))
    print("  turnos correctos:              ", pct(tot["turno_ok"], tot["turnos"]))
    print("  intención:                     ", pct(tot["intent_ok"], tot["intent_n"]))
    print("  entidades:                     ", pct(tot["ent_ok"], tot["ent_n"]))
    print("  seguridad de acciones:         ", pct(tot["seg_ok"], tot["seg_n"]))
    return tot, detalle


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--only")
    a = ap.parse_args()
    logging.disable(logging.NOTSET)
    logging.getLogger().setLevel(logging.CRITICAL)
    for name in list(logging.root.manager.loggerDict):
        if name != "lyra.interceptors.nexiservice":
            logging.getLogger(name).setLevel(logging.CRITICAL)
    out = {}
    for f in a.files:
        out[f] = asyncio.run(run(f, a.verbose, a.only))
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
