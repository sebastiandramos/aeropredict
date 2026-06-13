#!/usr/bin/env python3
"""Script: Sincroniza tablas Delta de R2 → local.

Lee cada tabla Delta conocida desde R2 (o backend cloud configurado en
OPENSKY_DELTA_ROOT) y la escribe en ``data/raw/`` con ``mode="overwrite"``.

Esto permite que los scripts downstream (bronze_to_silver, silver_to_gold)
trabajen con datos locales sin consumir egress de R2.

Uso:
    python scripts/sync_r2_to_local.py [--dry-run]

Flujo:
    Obtiene delta_root → itera tablas conocidas → si existen en R2
    → lee todas las filas → escribe en local
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

from aeropredict.opensky.config import get_delta_root, get_storage_options
from aeropredict.opensky.logging_config import setup_daily_logger

logger = logging.getLogger("sync_r2_to_local")

LOCAL_ROOT = str(Path("data/raw"))

TABLES_TO_SYNC: list[str] = [
    "bronze/opensky",
    "bronze/schedules",
    "bronze/weather",
    "silver/flights",
    "silver/state_vectors",
    "silver/tracks",
    "system/empty_airport_cache",
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza tablas Delta de R2 → local",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra lo que sincronizaría")
    return parser.parse_args(argv)


def _sync_table(rel_path: str, dry_run: bool) -> bool:
    """Sincroniza una tabla Delta de R2 a local.

    Args:
        rel_path: Ruta relativa (ej. ``bronze/opensky``).
        dry_run: Si es True, solo simula.

    Returns:
        ``True`` si se sincronizó, ``False`` si no existía en R2.
    """
    from deltalake import DeltaTable, write_deltalake

    r2_uri = f"{get_delta_root()}/{rel_path}"
    local_uri = str(Path(LOCAL_ROOT, rel_path))

    r2_opts = get_storage_options()
    if not r2_opts:
        logger.warning("No hay storage_options para R2, saltando")
        return False

    try:
        dt = DeltaTable(r2_uri, storage_options=r2_opts)
    except Exception as exc:
        logger.info("  %s: no existe en R2 (%s)", rel_path, exc)
        return False

    version = dt.version()
    partition_cols: list[str] = list(dt.metadata().partition_columns)

    if dry_run:
        logger.info(
            "  %s: v%d, %d particiones [%s] → %s",
            rel_path, version,
            len(partition_cols) if partition_cols else 0,
            ", ".join(partition_cols) if partition_cols else "(sin partición)",
            local_uri,
        )
        return True

    logger.info(
        "  %s: v%d → %s",
        rel_path, version, local_uri,
    )
    table: pa.Table = dt.to_pyarrow_table()
    logger.info("    Filas: %d", table.num_rows)

    write_deltalake(
        local_uri,
        table,
        partition_by=partition_cols if partition_cols else None,
        mode="overwrite",
    )
    logger.info("    ✓ %s", local_uri)
    return True


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)

    delta_root = get_delta_root()
    logger.info("=" * 60)
    logger.info("Sync R2 → Local")
    logger.info("R2 root: %s", delta_root)
    logger.info("Local root: %s", LOCAL_ROOT)
    logger.info("Dry-run: %s", args.dry_run)
    logger.info("=" * 60)

    synced = 0
    skipped = 0
    start = time.time()

    for rel_path in TABLES_TO_SYNC:
        if _sync_table(rel_path, args.dry_run):
            synced += 1
        else:
            skipped += 1

    elapsed = time.time() - start

    logger.info("=" * 60)
    logger.info("SYNC %s: %d tablas sincronizadas, %d saltadas | %.1fs",
                "DRY RUN" if args.dry_run else "COMPLETADO", synced, skipped, elapsed)
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
