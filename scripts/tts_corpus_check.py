"""
scripts/tts_corpus_check.py — ¿Qué le llega al motor de voz?

Pasa las respuestas REALES de Lyra (lyra_messages, rol assistant) de un proyecto
por la limpieza de voz y cuenta los restos que un motor TTS lee mal: guion bajo,
markdown, símbolos de moneda, "COP", porcentajes, fechas numéricas, cifras con
separadores, marcadores internos [BIZ:..], timestamps.

Uso: python scripts/tts_corpus_check.py [nexiservice|schoolsena] [--new] [--show N]
"""

import re
import sys

sys.path.insert(0, ".")

from core.database import get_connection  # noqa: E402

RESIDUE = {
    "guion bajo": re.compile(r"\w_\w|(?<!\w)_|_(?!\w)"),
    "markdown": re.compile(r"\*|`|^#|\]\(", re.M),
    "marcador interno": re.compile(r"\[[A-Z]+:[^\]]*\]"),
    "simbolo moneda": re.compile(r"\$\s?\d"),
    "COP": re.compile(r"\bCOP\b"),
    "porcentaje": re.compile(r"\d\s?%"),
    "fecha numerica": re.compile(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b"),
    "cifra con separador": re.compile(r"\b\d{1,3}(?:[.,]\d{3})+\b"),
    "timestamp": re.compile(r"\d{4}-\d{2}-\d{2}T\d"),
}


def replies(project: str):
    with get_connection() as c, c.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT m.content FROM lyra_messages m JOIN lyra_conversations c ON c.id=m.conversation_id "
            "WHERE m.role='assistant' AND c.project_slug=%s AND m.created_at < '2026-11-16 21:00' AND m.content NOT LIKE '[{%%'", (project,))
        return [r["content"] for r in cur.fetchall() if r["content"]]


def main():
    project = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "schoolsena"
    use_new = "--new" in sys.argv
    show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 0
    from core.voice_engine import _clean_for_tts
    if use_new:
        from core.speech_text import to_speech

        def speak(t):
            return _clean_for_tts(to_speech(t))
    else:
        speak = _clean_for_tts
    texts = replies(project)
    bad = 0
    counts = {k: 0 for k in RESIDUE}
    examples = []
    for t in texts:
        spoken = speak(t)
        hits = [k for k, rx in RESIDUE.items() if rx.search(spoken)]
        for k in hits:
            counts[k] += 1
        if hits:
            bad += 1
            if len(examples) < show:
                examples.append((hits, t[:160], spoken[:220]))
    print(f"{project} ({'nuevo' if use_new else 'actual'}): {len(texts)} respuestas reales, "
          f"{bad} con restos ({bad / max(1, len(texts)):.1%})")
    for k, v in counts.items():
        if v:
            print(f"   {k:20} {v}")
    for hits, raw, spoken in examples:
        print(f"\n  {hits}\n   escrito: {raw!r}\n   voz:     {spoken!r}")


if __name__ == "__main__":
    main()
