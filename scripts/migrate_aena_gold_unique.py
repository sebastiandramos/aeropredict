"""Migración del unique key de ``gold.aena_infovuelos`` (CLI).

Thin wrapper sobre ``aeropredict.opensky.migrate_aena_gold_unique``: parsea
flags, conecta a PostgreSQL y delega la lógica en la librería.

Añade ``scheduled_local`` al constraint UNIQUE existente, que actualmente es
``(snapshot_at_utc, flight_number, aena_airport_iata, flight_type)`` y provoca que
los vuelos recurrentes (mismo número de vuelo en un mismo snapshot con distinta
fecha programada) colisionen y se pierdan en el ``ON CONFLICT ... DO NOTHING``.

Nuevo unique key: ``(snapshot_at_utc, flight_number, aena_airport_iata,
flight_type, scheduled_local)``.

Uso:
    doppler run -- python scripts/migrate_aena_gold_unique.py            # dry-run
    doppler run -- python scripts/migrate_aena_gold_unique.py --apply    # aplicar
"""
from __future__ import annotations

import argparse
import logging
import sys

import psycopg2

from aeropredict.opensky.config import get_postgres_uri
from aeropredict.opensky.migrate_aena_gold_unique import (
    AENA_UNIQUE_COLUMNS,
    count_aena_duplicates,
    get_aena_unique_constraints,
    migrate_aena_gold_unique,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migración unique key de gold.aena_infovuelos")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar el ALTER (defecto: dry-run, solo inspecciona).",
    )
    args = parser.parse_args(argv)

    conn = psycopg2.connect(get_postgres_uri())
    conn.autocommit = True
    try:
        # 1. ¿Duplicados según el nuevo key?
        dupes = count_aena_duplicates(conn)
        logger.info("Duplicados según nuevo key %s: %d", AENA_UNIQUE_COLUMNS, dupes)
        if dupes:
            logger.error(
                "Hay %d duplicados según el nuevo key. Aborto: revisar antes de migrar.",
                dupes,
            )
            return 1

        # 2. Constraint actual
        constraints = get_aena_unique_constraints(conn)
        logger.info("Constraints UNIQUE actuales en gold.aena_infovuelos:")
        for name, cols in constraints:
            logger.info("    - %s (%s)", name, ", ".join(cols))
        if not constraints:
            logger.warning("No se encontró ningún constraint UNIQUE.")

        if not args.apply:
            logger.info("MODO DRY-RUN: no se ha alterado nada. Usa --apply para migrar.")
            return 0

        # 3. Migrar: dropear UNIQUEs existentes y crear el nuevo con scheduled_local
        migrate_aena_gold_unique(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
