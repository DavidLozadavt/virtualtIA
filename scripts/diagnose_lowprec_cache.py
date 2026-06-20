"""
scripts/diagnose_lowprec_cache.py — Diagnóstico (solo lectura).

Cuenta cuántas entradas de location_cache están guardadas como VÁLIDAS
(is_valid = 1) con un location_type de baja precisión
(GEOMETRIC_CENTER / APPROXIMATE / NOMINATIM_LOW). Esas entradas, servidas desde
cache, eran un bypass del guard _NEVER_AUTOACCEPT (incluyen el caso del bug:
'Cl. 4' truncado asociado a un GEOMETRIC_CENTER).

NO modifica nada. Imprime conteos, una muestra y el SQL de limpieza propuesto
(para ejecutar manualmente tras revisión).

Uso:  python scripts/diagnose_lowprec_cache.py
"""

from core.database import get_connection

LOW_PRECISION = ("GEOMETRIC_CENTER", "APPROXIMATE", "NOMINATIM_LOW")
_IN = ", ".join(f"'{t}'" for t in LOW_PRECISION)


def main() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Total de entradas válidas en la tabla.
            cur.execute("SELECT COUNT(*) AS n FROM location_cache WHERE is_valid = 1")
            total_valid = cur.fetchone()["n"]

            # Desglose por location_type de baja precisión.
            cur.execute(
                f"""
                SELECT location_type,
                       COUNT(*)              AS entries,
                       COALESCE(SUM(query_count), 0) AS total_hits
                FROM location_cache
                WHERE is_valid = 1
                  AND location_type IN ({_IN})
                GROUP BY location_type
                ORDER BY entries DESC
                """
            )
            breakdown = cur.fetchall()

            # Total de baja precisión.
            cur.execute(
                f"""
                SELECT COUNT(*) AS n, COALESCE(SUM(query_count), 0) AS hits
                FROM location_cache
                WHERE is_valid = 1 AND location_type IN ({_IN})
                """
            )
            row = cur.fetchone()
            low_total, low_hits = row["n"], row["hits"]

            # Muestra (incluye el patrón del bug: queries cortas tipo 'Cl. 4').
            cur.execute(
                f"""
                SELECT canonical_query, location_type, confidence,
                       query_count, display_name
                FROM location_cache
                WHERE is_valid = 1 AND location_type IN ({_IN})
                ORDER BY query_count DESC
                LIMIT 20
                """
            )
            sample = cur.fetchall()

    print("=" * 70)
    print("DIAGNÓSTICO location_cache — entradas de baja precisión (is_valid=1)")
    print("=" * 70)
    print(f"Total entradas válidas en cache : {total_valid}")
    print(f"Baja precisión (válidas)        : {low_total}  "
          f"({low_hits} hits acumulados)")
    pct = (low_total / total_valid * 100) if total_valid else 0
    print(f"Proporción del cache afectada    : {pct:.1f}%")
    print("-" * 70)
    print("Desglose por location_type:")
    for b in breakdown:
        print(f"  {b['location_type']:<18} entries={b['entries']:<6} "
              f"hits={b['total_hits']}")
    print("-" * 70)
    print("Muestra (top por query_count):")
    for s in sample:
        print(f"  {s['canonical_query']!r:<28} [{s['location_type']}] "
              f"conf={s['confidence']} hits={s['query_count']}")
    print("=" * 70)
    print("LIMPIEZA PROPUESTA (NO ejecutada) — invalida SOLO estas entradas:")
    print(f"""
  UPDATE location_cache
     SET is_valid = 0
   WHERE is_valid = 1
     AND location_type IN ({_IN});
""")
    print("Efecto: _db_get filtra is_valid=1 → estas pasan a 'miss' → se "
          "re-geocodifican y cruzan el guard _NEVER_AUTOACCEPT. No toca las "
          "entradas ROOFTOP / RANGE_INTERPOLATED / NOMINATIM_HIGH.")


if __name__ == "__main__":
    main()
