#!/usr/bin/env python3
"""Unified daily pipeline: Extract → Bronze → Silver → Gold.

Single entry point that runs the full pipeline with checkpoint support.
Replaces running the 3 scripts separately.

Uso:
    python scripts/daily_extract.py [--days N] [--dry-run]
        [--skip-bronze] [--skip-silver] [--skip-gold]

Flujo:
    1. extract_to_bronze.py (API → Delta Lake)
    2. bronze_to_silver.py (Delta Lake → MongoDB)
    3. silver_to_gold.py (MongoDB → PostgreSQL)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from aeropredict.opensky.logging_config import setup_daily_logger

logger = logging.getLogger("daily_extract")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline diario unificado: Extract → Bronze → Silver → Gold",
    )
    parser.add_argument("--days", type=int, default=1, help="Días hacia atrás (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Simula sin ejecutar")
    parser.add_argument("--skip-bronze", action="store_true", help="Salta extracción Bronze")
    parser.add_argument("--skip-silver", action="store_true", help="Salta Bronze→Silver")
    parser.add_argument("--skip-gold", action="store_true", help="Salta Silver→Gold")
    parser.add_argument("--backfill", action="store_true", help="Modo backfill para Bronze")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)

    logger.info("=" * 60)
    logger.info("PIPELINE DIARIO UNIFICADO")
    logger.info("Days: %d | Dry-run: %s", args.days, args.dry_run)
    logger.info("=" * 60)

    start = time.time()
    results: dict[str, int] = {}

    # Step 1: Extract to Bronze
    if not args.skip_bronze:
        logger.info("--- Paso 1/3: Extract → Bronze ---")
        from scripts.extract_to_bronze import main as bronze_main

        bronze_args = ["--days", str(args.days)]
        if args.dry_run:
            bronze_args.append("--dry-run")
        if args.backfill:
            bronze_args.append("--backfill")

        rc = bronze_main(bronze_args)
        results["bronze"] = rc
        if rc != 0:
            logger.error("Bronze falló (rc=%d), deteniendo pipeline", rc)
            return rc
    else:
        logger.info("--- Paso 1/3: Bronze SKIP ---")

    # Step 2: Bronze to Silver
    if not args.skip_silver:
        logger.info("--- Paso 2/3: Bronze → Silver ---")
        from scripts.bronze_to_silver import main as silver_main

        silver_args = []
        if args.dry_run:
            silver_args.append("--dry-run")

        rc = silver_main(silver_args)
        results["silver"] = rc
        if rc != 0:
            logger.error("Silver falló (rc=%d), deteniendo pipeline", rc)
            return rc
    else:
        logger.info("--- Paso 2/3: Silver SKIP ---")

    # Step 3: Silver to Gold
    if not args.skip_gold:
        logger.info("--- Paso 3/3: Silver → Gold ---")
        from scripts.silver_to_gold import main as gold_main

        gold_args = []
        if args.dry_run:
            gold_args.append("--dry-run")

        rc = gold_main(gold_args)
        results["gold"] = rc
        if rc != 0:
            logger.error("Gold falló (rc=%d)", rc)
            return rc
    else:
        logger.info("--- Paso 3/3: Gold SKIP ---")

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETADO: %s | %.1fs", results, elapsed)
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
