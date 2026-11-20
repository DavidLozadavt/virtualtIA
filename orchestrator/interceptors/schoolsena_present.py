"""
orchestrator/interceptors/schoolsena_present.py — Cómo se muestra cada respuesta de SchoolSena.

El formateador genérico de `_run` adivinaba el nombre de cada fila buscando
"nombre", "nombreMateria" o "tituloActividad". Con los datos reales eso fallaba
en silencio: los contratos (un `Contract` con su `persona`) salían como 24
viñetas vacías, las notas mostraban la actividad pero no la nota, y la nómina
listaba "Moneda: COP" y un timestamp crudo, que la voz leía tal cual.

Aquí hay un presentador por herramienta, escrito contra los campos que
devuelve `LyraController` del backend de SchoolSena. Cada uno devuelve el texto
visible (markdown); lo que se dice en voz alta sale de ese texto por la
normalización de voz (`core/speech_format`), sin tocar los datos.

Nunca se muestran campos sensibles que el backend incluya de paso (p. ej.
`numeroCuentaBancaria` dentro del contrato).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional

from core.speech_format import format_price

_DAY = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"}


def _date(value: Any) -> Optional[date]:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def short_date(value: Any) -> str:
    d = _date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def _person(p: Any) -> str:
    if not isinstance(p, dict):
        return ""
    parts = [p.get("nombre1"), p.get("nombre2"), p.get("apellido1"), p.get("apellido2")]
    return " ".join(x.strip() for x in parts if isinstance(x, str) and x.strip())


def _grade(item: dict) -> str:
    num = item.get("calificacionNumerica")
    std = item.get("calificacionEstandart")
    if num not in (None, ""):
        try:
            return f"{float(num):.1f}".replace(".", ",")
        except (TypeError, ValueError):
            return str(num)
    if std not in (None, ""):
        return str(std)
    return ""


def present_notas(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    rows = [r for r in data if isinstance(r, dict)]
    filt = ctx.get("grades_filter") or {}
    on_day: Optional[date] = filt.get("date")
    words = [w for w in filt.get("words") or []]

    def matches(r: dict) -> bool:
        if on_day and _date(r.get("fechaCalificacion")) != on_day:
            return False
        if words:
            from core.semantic.morphology import normalize
            hay = normalize(f"{r.get('tituloActividad') or ''} {r.get('nombreMateria') or ''}")
            return all(w in hay for w in words)
        return True

    selected = [r for r in rows if matches(r)]
    unmatched_words = ""
    if words and not selected:
        # Ninguna actividad o materia tiene esas palabras: no se responde "sin
        # notas" por eso. Se dice y se muestra lo que hay (respetando la fecha).
        unmatched_words = (f"No encontré una actividad o materia con «{' '.join(words)}»; "
                           "te muestro lo que hay.\n\n")
        words = []
        selected = [r for r in rows if matches(r)]
    graded = [r for r in selected if _grade(r)]
    lines = []
    for r in (graded or selected)[:12]:
        lines.append(_nota_line(r))

    # Lo que se pidió y lo que hay se dicen sin adornos: si no hay nada con esa
    # fecha, se dice; no se presentan otras notas como si fueran las pedidas.
    asked = ""
    if on_day:
        asked = "ayer" if (date.today() - on_day).days == 1 else f"el {on_day.strftime('%d/%m/%Y')}"
    note = ""
    if filt.get("mentions_exam"):
        if any("tipoActividad" in r for r in rows):
            # El backend ya dice qué actividades son evaluaciones (cuestionarios).
            examenes = [r for r in selected if (r.get("tipoActividad") or "").lower() == "cuestionario"]
            if examenes:
                selected = examenes
                graded = [r for r in selected if _grade(r)]
                lines = [_nota_line(r) for r in (graded or selected)[:12]]
            elif selected:
                note = ("\n\nNinguna de estas actividades está registrada como examen (cuestionario): "
                        "son las calificaciones que tienes.")
        else:
            note = ("\n\nEl sistema no marca qué actividades son exámenes: te muestro las calificaciones "
                    "por fecha de calificación.")
    if on_day and not selected:
        latest = sorted((r for r in rows if _date(r.get("fechaCalificacion"))),
                        key=lambda r: _date(r.get("fechaCalificacion")), reverse=True)
        tail = ""
        if latest:
            tail = f" Tu última calificación registrada es del {short_date(latest[0].get('fechaCalificacion'))}."
        return f"No encontré calificaciones registradas {asked}.{tail}{note}"
    if not rows:
        return "Todavía no tienes calificaciones registradas."
    if not graded:
        head = f"Tus actividades calificadas {asked}".strip() if asked else "Tus actividades"
        return unmatched_words + f"📊 **{head}:**\n" + "\n".join(lines) + "\n\nNinguna tiene nota numérica registrada todavía." + note
    head = f"Tus calificaciones de {asked}" if asked and asked != "ayer" else (
        "Tus calificaciones de ayer" if asked else "Tus calificaciones")
    more = f"\n(mostrando 12 de {len(graded)})" if len(graded) > 12 else ""
    return unmatched_words + f"📊 **{head}:**\n" + "\n".join(lines) + more + note


def _materia(nombre: str) -> str:
    """
    "592376 - 03  DESARROLLAR PROCESOS LÓGICOS A TRAVÉS DE…" → "Desarrollar procesos lógicos a través de…".

    El código de la competencia y las mayúsculas sostenidas no aportan al leerlo
    ni al oírlo; el nombre se acorta para que la línea siga siendo una línea.
    """
    import re as _re
    t = _re.sub(r"^\s*\d+\s*-\s*\d+\s+", "", nombre or "").strip()
    if t.isupper():
        t = t.capitalize()
    return t if len(t) <= 60 else t[:57].rsplit(" ", 1)[0] + "…"


def _nota_line(r: dict) -> str:
    title = (r.get("tituloActividad") or "Actividad").strip()
    materia = _materia(r.get("nombreMateria") or "")
    nota = _grade(r)
    when = short_date(r.get("fechaCalificacion"))
    line = f"- **{title.capitalize() if title.isupper() else title}**"
    if (r.get("tipoActividad") or "").lower() == "cuestionario":
        line += " · examen"
    if materia:
        line += f" ({materia})"
    line += f": {nota}" if nota else ": sin nota registrada"
    if when:
        line += f" · calificada el {when}"
    return line


def present_instructores(data: Any, ctx: dict) -> Optional[str]:
    """
    Quien tiene contrato en estado activo, separando lo que la base mezcla:

      - contratos vigentes (con fecha futura o a término indefinido);
      - contratos en estado activo cuya fecha de fin ya pasó: terminaron y nadie
        los cerró en el sistema. Se cuentan aparte en vez de listarse como activos;
      - la fecha de relleno de los indefinidos (2070) se dice "término indefinido".

    Con más de 10 se muestran 10 y se ofrece la lista completa.
    """
    if not isinstance(data, list):
        return None
    rows = [r for r in data if isinstance(r, dict)]
    if not rows:
        return None
    if not any("vigente" in r for r in rows):
        return None  # backend anterior: formateo genérico
    vigentes = sorted([r for r in rows if r.get("vigente")], key=lambda r: (r.get("nombre") or "").lower())
    vencidos = [r for r in rows if not r.get("vigente")]
    completa = bool(ctx.get("full_list"))
    tope = len(vigentes) if completa else 10

    def linea(r):
        fin = "término indefinido" if r.get("indefinido") else (
            f"hasta el {short_date(r.get('fecha_fin'))}" if r.get("fecha_fin") else "sin fecha de fin")
        tipo = (r.get("tipo") or "").strip()
        tipo = tipo.capitalize() if tipo.isupper() else tipo
        return f"- **{(r.get('nombre') or 'Sin nombre').strip()}**" + (f" · {tipo}" if tipo and tipo != "N/A" else "") + f" · {fin}"

    partes = [f"👨‍🏫 **{len(vigentes)} instructores con contrato vigente:**"]
    partes.append("\n".join(linea(r) for r in vigentes[:tope]))
    if len(vigentes) > tope:
        partes.append(f"…y {len(vigentes) - tope} más. Pídeme «lista completa de instructores» para verlos todos.")
    if vencidos:
        partes.append(f"⚠️ Además, {len(vencidos)} contrato{'s' if len(vencidos) != 1 else ''} sigue"
                      f"{'n' if len(vencidos) != 1 else ''} marcado{'s' if len(vencidos) != 1 else ''} como "
                      f"activo{'s' if len(vencidos) != 1 else ''} aunque su fecha de fin ya pasó; conviene revisarlo"
                      f"{'s' if len(vencidos) != 1 else ''} en Contratos.")
    return "\n".join(p for p in partes if p)


def present_fichas(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    rows = [r for r in data if isinstance(r, dict)]
    if not rows or not any("activa" in r for r in rows):
        return None
    activas = [r for r in rows if r.get("activa")]
    if not activas:
        return (f"Tu centro tiene {len(rows)} ficha{'s' if len(rows) != 1 else ''} registrada"
                f"{'s' if len(rows) != 1 else ''}, pero ninguna activa: su programa no está abierto o sus clases ya terminaron.")
    lineas = [f"- **Ficha {r.get('codigo') or r.get('id')}** · {r.get('programa') or 'programa sin nombre'}"
              + (f" · clases hasta el {short_date(r.get('fin_clases'))}" if r.get("fin_clases") else "")
              for r in activas[:15]]
    mas = f"\n(mostrando 15 de {len(activas)})" if len(activas) > 15 else ""
    return f"📋 **{len(activas)} ficha{'s' if len(activas) != 1 else ''} activa{'s' if len(activas) != 1 else ''}:**\n" + "\n".join(lineas) + mas


def present_contratos(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    rows = [r for r in data if isinstance(r, dict)]
    if not rows:
        return "✅ Ningún contrato vence en los próximos 30 días."
    rows.sort(key=lambda r: str(r.get("fechaFinalContrato") or ""))
    lines = []
    for r in rows[:15]:
        who = _person(r.get("persona")) or "Sin nombre registrado"
        num = r.get("numeroContrato")
        when = short_date(r.get("fechaFinalContrato"))
        line = f"- **{who}**" + (f" · contrato {num}" if num else "") + (f" · vence el {when}" if when else "")
        lines.append(line)
    more = f"\n(mostrando 15 de {len(rows)})" if len(rows) > 15 else ""
    return f"⚠️ **{len(rows)} contratos vencen en los próximos 30 días:**\n" + "\n".join(lines) + more


def present_nomina(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    total = data.get("total_nomina_mensual")
    n = data.get("total_contratos")
    if n and not total:
        # Contratos activos sin salario registrado: la suma da 0, y "la nómina es
        # de $0" sería afirmar un dato que no existe.
        return (f"💰 Hay {n} contrato{'s' if n != 1 else ''} activo{'s' if n != 1 else ''}, pero ninguno tiene "
                "salario registrado, así que no puedo calcular la nómina mensual.")
    amount = format_price(total, fallback="sin dato") if total is not None else "sin dato"
    tail = (f", repartida en {n} contrato{'s' if n != 1 else ''} activo{'s' if n != 1 else ''}"
            if n is not None else "")
    return f"💰 La nómina mensual es de **{amount}**{tail}."


_ADMIN_LABELS = {
    "regional": "Regional",
    "instructores_activos": "Instructores activos",
    "fichas_en_ejecucion": "Fichas en ejecución",
    "aprendices_matriculados": "Aprendices matriculados",
    "sedes_activas": "Sedes activas",
}


def present_resumen(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    lines = [f"- **{label}:** {data[k]}" for k, label in _ADMIN_LABELS.items() if data.get(k) not in (None, "")]
    if not lines:
        return None
    return "📊 **Estado general del centro:**\n" + "\n".join(lines)


def present_estudiantes(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    total = data.get("total_estudiantes")
    code = data.get("codigo") or data.get("ficha_id")
    if total is None:
        return None
    if not data.get("codigo"):
        return f"No encontré la ficha {code} en tu centro."
    return f"👥 La ficha **{code}** tiene **{total}** aprendi{'z' if total == 1 else 'ces'} matriculado{'' if total == 1 else 's'}."


def present_entregas(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    rows = [r for r in data if isinstance(r, dict)]
    if not rows:
        return None  # el texto de "nadie ha entregado" lo pone el llamador
    delivered = [r for r in rows if r.get("archivo")]
    lines = []
    for r in rows[:20]:
        who = _person(r) or "Aprendiz sin nombre"
        state = "entregó" if r.get("archivo") else "sin archivo"
        nota = _grade(r)
        lines.append(f"- **{who}**: {state}" + (f" · nota {nota}" if nota else ""))
    more = f"\n(mostrando 20 de {len(rows)})" if len(rows) > 20 else ""
    return f"📥 **{len(delivered)} de {len(rows)} aprendices entregaron:**\n" + "\n".join(lines) + more


def present_pendientes(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    rows = [r for r in data if isinstance(r, dict)]
    if not rows:
        return None
    rows.sort(key=lambda r: str(r.get("fechaLimite") or "9999"))
    ahora = datetime.now()

    def limite(r):
        try:
            return datetime.fromisoformat(str(r.get("fechaLimite"))[:19])
        except (TypeError, ValueError):
            return None

    def linea(r):
        title = (r.get("tituloActividad") or "Actividad").strip()
        title = title.capitalize() if title.isupper() else title
        materia = _materia(r.get("nombreMateria") or "")
        when = short_date(r.get("fechaLimite"))
        return f"- **{title}**" + (f" ({materia})" if materia else "") + (f" · hasta el {when}" if when else "")

    # Lo que aún se puede entregar y lo que ya venció se dicen por separado:
    # mezclarlos hacía parecer "pendiente" algo cuyo plazo terminó hace semanas.
    por_entregar = [r for r in rows if not limite(r) or limite(r) >= ahora]
    vencidas = [r for r in rows if limite(r) and limite(r) < ahora]
    partes = []
    if por_entregar:
        partes.append(f"📝 **Por entregar ({len(por_entregar)}):**\n" + "\n".join(linea(r) for r in por_entregar[:10]))
    if vencidas:
        partes.append(f"⏰ **Con el plazo vencido ({len(vencidas)}):**\n" + "\n".join(linea(r) for r in vencidas[:10]))
    return "\n\n".join(partes)


# ── Gestión académica: listas que se muestran, se cuentan o se leen ──────────

def _title(text: Any) -> str:
    """'CENTRO AGROPECUARIO' → 'Centro Agropecuario' (los nombres vienen en mayúsculas)."""
    t = " ".join(str(text or "").split())
    if t.isupper():
        menores = {"de", "del", "la", "las", "los", "y", "e", "en", "para", "el"}
        t = " ".join(w.lower() if i and w.lower() in menores else w.capitalize() for i, w in enumerate(t.lower().split()))
    return t


def _hour(value: Any) -> str:
    return str(value or "")[:5]


def _listing(rows: list, ctx: dict, singular: str, plural: str, line, spoken, empty: str,
             genero: str = "m", limit: int = 15) -> str:
    """
    La misma lista en tres actos:
    - list:  viñetas para leer en pantalla;
    - count: sólo cuántos hay;
    - read:  una frase corrida, pensada para la voz (sin viñetas ni símbolos).
    """
    act = ctx.get("school_act") or "list"
    n = len(rows)
    if not n:
        return empty
    nombre = singular if n == 1 else plural
    if act == "count":
        return f"Hay {n} {nombre} registrad{'o' if genero == 'm' else 'a'}{'s' if n != 1 else ''}."
    if act == "read":
        partes = [spoken(r) for r in rows[:limit]]
        from tools.nexiservice import natural_list
        cola = f" y {n - limit} más" if n > limit else ""
        return f"{'Es' if n == 1 else 'Son'} {n} {nombre}: {natural_list(partes)}{cola}."
    lineas = [line(r) for r in rows[:limit]]
    mas = f"\n(mostrando {limit} de {n}; dime «léemelos» o «lista completa» si quieres más)" if n > limit else ""
    return f"**{n} {nombre}:**\n" + "\n".join(lineas) + mas


def present_centros(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    return _listing(
        [r for r in data if isinstance(r, dict)], ctx, "centro de formación", "centros de formación",
        lambda r: f"- **{_title(r.get('nombre'))}**" + (f" · {_title(r.get('ciudad'))}" if r.get("ciudad") else "")
        + (f" · {r['direccion']}" if r.get("direccion") else ""),
        lambda r: _title(r.get("nombre")) + (f" (en {_title(r.get('ciudad'))})" if r.get("ciudad") else ""),
        "Tu empresa no tiene centros de formación registrados en SchoolSena.")


def present_sedes(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    return _listing(
        [r for r in data if isinstance(r, dict)], ctx, "sede", "sedes",
        lambda r: f"- **{_title(r.get('nombre'))}**" + (f" · {_title(r.get('centro'))}" if r.get("centro") else "")
        + (f" · {r['direccion']}" if r.get("direccion") else ""),
        lambda r: _title(r.get("nombre")),
        "Tu empresa no tiene sedes registradas en SchoolSena.", genero="f")


def present_ambientes(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    return _listing(
        [r for r in data if isinstance(r, dict)], ctx, "ambiente de formación", "ambientes de formación",
        lambda r: f"- **{_title(r.get('nombre'))}**" + (f" · {_title(r.get('tipo'))}" if r.get("tipo") else "")
        + (f" · capacidad {r['capacidad']}" if r.get("capacidad") else "") + (f" · sede {_title(r.get('sede'))}" if r.get("sede") else ""),
        lambda r: _title(r.get("nombre")) + (f", en la sede {_title(r.get('sede'))}" if r.get("sede") else ""),
        "No hay ambientes de formación registrados en las sedes de tu empresa. Se registran en la sección "
        "Ambientes (infraestructura de cada sede).")


def present_periodos(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    rows = [r for r in data.get("periodos") or [] if isinstance(r, dict)]
    propios = data.get("alcance") == "propios"
    vacio = ("No encontré periodos asociados a tu matrícula." if propios
             else "Tu empresa no tiene periodos académicos registrados.")
    texto = _listing(
        rows, ctx, "periodo", "periodos",
        lambda r: f"- **{r.get('nombre')}** · del {short_date(r.get('fechaInicial'))} al {short_date(r.get('fechaFinal'))}"
        + (" · vigente" if r.get("vigente") else ""),
        lambda r: f"{r.get('nombre')}" + (", vigente" if r.get("vigente") else ""),
        vacio)
    if rows and propios:
        texto = "Los periodos de tu matrícula. " + texto
    return texto


def present_jornadas(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, list):
        return None
    return _listing(
        [r for r in data if isinstance(r, dict)], ctx, "jornada", "jornadas",
        lambda r: f"- **{_title(r.get('nombre'))}** · {_hour(r.get('horaInicial'))} a {_hour(r.get('horaFinal'))}"
        + (f" · {_title(r.get('centro'))}" if r.get("centro") else "")
        + (f" · {str(r['estado']).lower()}" if r.get("estado") else ""),
        lambda r: f"{_title(r.get('nombre'))}, de {_hour(r.get('horaInicial'))} a {_hour(r.get('horaFinal'))}",
        "No hay jornadas registradas en los centros de tu empresa.", genero="f")


def present_planeacion(data: Any, ctx: dict) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    redes = [r for r in data.get("redes") or [] if isinstance(r, dict)]
    sin_red = int(data.get("programas_sin_red") or 0)
    cola = (f"\n\nAdemás hay {sin_red} programa{'s' if sin_red != 1 else ''} sin red asignada."
            if sin_red else "")
    texto = _listing(
        redes, ctx, "red de conocimiento", "redes de conocimiento",
        lambda r: f"- **{_title(r.get('nombre'))}** · {r.get('programas') or 0} programa{'s' if (r.get('programas') or 0) != 1 else ''}",
        lambda r: f"{_title(r.get('nombre'))}, con {r.get('programas') or 0} programa{'s' if (r.get('programas') or 0) != 1 else ''}",
        "La planeación académica de tu empresa no tiene redes de conocimiento registradas.", genero="f")
    return texto + cola


PRESENTERS: dict[str, Callable[[Any, dict], Optional[str]]] = {
    "get_lista_instructores": present_instructores,
    "get_fichas_activas": present_fichas,
    "get_entregas": present_entregas,
    "get_actividades_pendientes": present_pendientes,
    "get_notas": present_notas,
    "get_contratos_vencimiento": present_contratos,
    "get_nomina_resumen": present_nomina,
    "get_resumen_admin": present_resumen,
    "get_estudiantes_ficha": present_estudiantes,
    "get_centros_formacion": present_centros,
    "get_sedes": present_sedes,
    "get_ambientes": present_ambientes,
    "get_periodos": present_periodos,
    "get_jornadas": present_jornadas,
    "get_planeacion": present_planeacion,
}
