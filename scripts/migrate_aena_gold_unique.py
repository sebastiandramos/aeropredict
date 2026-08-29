"""Migración del unique key de ``gold.aena_infovuelos``.

Añade ``scheduled_local`` al constraint UNIQUE existente, que actualmente es
``(snapshot_at_utc, flight_number, aena_airport_iata, flight_type)`` y provoca que
los vuelos recurrentes (mismo número de vuelo en un mismo snapshot con distinta
fecha programada) colisionen y se pierdan en el ``ON CONFLICT ... DO NOTHING``.

Nuevo unique key: ``(snapshot_at_utc, flight_number, aena_airport_iata,
flight_type, scheduled_local)``.

Seguridad:
- No asume el nombre autogenerado de la constraint: lo busca en ``pg_constraint``.
- Verifica que no haya duplicados según el NUEVO key antes de alterar.
- Corre en modo ``--dry-run`` por defecto (solo inspecciona). ``--apply`` altera.

Uso:
    doppler run -- python scripts/migrate_aena_gold_unique.py            # dry-run
    doppler run -- python scripts/migrate_aena_gold_unique.py --apply    # aplicar
"""
from __future__ import annotations

import argparse
import logging

import psycopg2

from aeropredict.opensky.config import get_postgres_uri

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

TABLE = "gold.aena_infovuelos"
NEW_COLUMNS = (
    "snapshot_at_utc",
    "flight_number",
    "aena_airport_iata",
    "flight_type",
    "scheduled_local",
)

DUP_CHECK_SQL = f"""
SELECT COUNT(*) AS dupes
FROM (
    SELECT {", ".join(NEW_COLUMNS)}
    FROM {TABLE}
    GROUP BY {", ".join(NEW_COLUMNS)}
    HAVING COUNT(*) > 1
) AS d;
"""

# Busca constraints UNIQUE de una columna anteponiendo (info schématica genérica).
FIND_UNIQUE_SQL = """
SELECT con.conname
FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
JOIN pg_attribute att ON att.attrelid = con.conrelid
                        AND att.attnum = ANY (con.conkey)
WHERE con.contype = 'u'
  AND nsp.nspname = 'gold'
  AND rel.relname = 'aena_infovuelos'
GROUP BY con.conname
ORDER BY con.conname;
"""


def _get_conn(apply: bool):
    conn = psycopg2.connect(get_postgres_uri())
    conn.autocommit = True
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description="Migración unique key de gold.aena_infovuelos")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar el ALTER (defecto: dry-run, solo inspecciona).",
    )
    args = parser.parse_args()

    conn = _get_conn(args.apply)
    with conn.cursor() as cur:
        # 1. ¿Duplicados según el nuevo key?
        cur.execute(DUP_CHECK_SQL)
        row = cur.fetchone()
        dupes = row[0] if row else 0
        logger.info("Duplicados según nuevo key %s: %d", NEW_COLUMNS, dupes)
        if dupes:
            logger.error(
                "Hay %d duplicados según el nuevo key. Aborto: revisar antes de migrar.",
                dupes,
            )
            conn.close()
            raise SystemExit(1)

        # 2. Constraint actual
        cur.execute(FIND_UNIQUE_SQL)
        existing = [r[0] for r in cur.fetchall()]
        logger.info("Constraints UNIQUE actuales en gold.aena_infovuelos:")
        for name in existing:
            logger.info("    - %s", name)
        if not existing:
            logger.warning("No se encontró ningún constraint UNIQUE. Nada que migrar.")
            conn.close()
            return

        if not args.apply:
            logger.info("MODO DRY-RUN: no se ha alterado nada. Usa --apply para migrar.")
            conn.close()
            return

        # 3. Alter: dropear UNIQUEs existentes y crear el nuevo con scheduled_local
        for name in existing:
            logger.info("Dropeando constraint UNIQUE %s...", name)
            cur.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "{name}"')

        new_cols_sql = ", ".join(NEW_COLUMNS)
        logger.info(
            "Creando nuevo UNIQUE (%s)...",
            new_cols_sql,
        )
        cur.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT uq_aena_infovuelos_unique "
            f"UNIQUE ({new_cols_sql})"
        )
        logger.info("Migración aplicada correctamente.")

    conn.close()


if __name__ == "__main__":
    main()
