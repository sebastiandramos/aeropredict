"""Migración del unique key de ``gold.notam`` (CLI).

Thin wrapper sobre ``aeropredict.opensky.migrate_notam_gold_unique``: parsea flags,
conecta a PostgreSQL y delega la lógica en la librería.

La constraint original ``UNIQUE (layer, feature_json)`` indexa una columna JSONB,
que PostgreSQL limita a 2704 B por entrada de índice btree; los features GeoJSON
de NOTAM no tienen tamaño acotado y rompen el INSERT. Se pasa a indexar el hash
MD5 del JSON: ``UNIQUE (layer, feature_hash)``.

Uso:
    doppler run -- python scripts/migrate_notam_gold_unique.py            # dry-run
    doppler run -- python scripts/migrate_notam_gold_unique.py --apply    # aplicar
"""
from __future__ import annotations

import argparse
import logging
import sys

import psycopg2

from aeropredict.opensky.config import get_postgres_uri
from aeropredict.opensky.migrate_notam_gold_unique import (
    NOTAM_TARGET_INDEX,
    get_notam_unique_index_defs,
    migrate_notam_gold_unique,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migración unique key de gold.notam")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar el ALTER (defecto: dry-run, solo inspecciona).",
    )
    args = parser.parse_args(argv)

    conn = psycopg2.connect(get_postgres_uri())
    conn.autocommit = True
    try:
        defs = get_notam_unique_index_defs(conn)
        logger.info("Índices UNIQUE actuales en gold.notam:")
        for name, indexdef in defs:
            logger.info("    - %s: %s", name, indexdef)
        if not defs:
            logger.warning("No se encontró ningún índice UNIQUE.")

        if any(name == NOTAM_TARGET_INDEX for name, _ in defs):
            logger.info("gold.notam ya tiene el unique key (layer, feature_hash). Nada que hacer.")
            return 0

        if not args.apply:
            logger.info("MODO DRY-RUN: no se ha alterado nada. Usa --apply para migrar.")
            return 0

        migrate_notam_gold_unique(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
