"""Compactar tablas Delta locales usando ``OPTIMIZE`` (Delta-native compaction).

Consolida archivos pequeños (uno por llamada API) en archivos más grandes,
idealmente uno por partición (ej. uno por fecha para ``bronze/opensky``).

Uso::

    python scripts/compact_delta.py
    python scripts/compact_delta.py --vacuum        # también limpia archivos viejos
    python scripts/compact_delta.py --dry-run        # solo muestra info
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DELTA_ROOT = Path("data/raw")

# (ruta_relativa, descripción)
TABLES: list[tuple[str, str]] = [
    ("bronze/opensky", "OpenSky raw arrivals/departures"),
    ("bronze/schedules_aerodatabox", "AeroDataBox schedules"),
    ("bronze/weather_openmeteo", "Open-Meteo weather"),
    ("silver/flights", "Clean flights"),
    ("silver/aircraft", "Aircraft registry"),
    ("silver/weather", "Weather data"),
    ("silver/schedules", "Structured schedules"),
    ("gold/flights", "Gold flights"),
    ("gold/aircraft", "Gold aircraft"),
    ("gold/weather", "Gold weather"),
]


def compact_table(table_path: str, vacuum: bool = False) -> bool:
    """Compacta una tabla Delta local usando ``OPTIMIZE``.

    Returns:
        ``True`` si se compactó, ``False`` si no existía.
    """
    from deltalake import DeltaTable

    path = str(DELTA_ROOT / table_path)
    if not Path(path).exists():
        logger.info("  [%s] no existe, saltando", table_path)
        return False

    try:
        dt = DeltaTable(path)
    except Exception as e:
        logger.warning("  [%s] error al abrir: %s", table_path, e)
        return False

    n_rows = dt.count()
    if n_rows == 0:
        logger.info("  [%s] 0 filas, saltando", table_path)
        return False

    parts = dt.metadata().partition_columns
    before = len(dt.file_uris())
    logger.info(
        "  [%s] %d filas en %d archivos, particiones: %s",
        table_path, n_rows, before,
        parts if parts else "(ninguna)",
    )

    try:
        dt.optimize.compact()
        logger.info("  [%s] OPTIMIZE completado", table_path)
    except Exception as e:
        logger.warning("  [%s] OPTIMIZE falló: %s", table_path, e)
        return False

    after = len(DeltaTable(path).file_uris())
    logger.info("  [%s] %d archivos → %d archivos", table_path, before, after)

    if vacuum:
        try:
            logger.info("  [%s] VACUUM (retención 7 días)...", table_path)
            dt.vacuum(retention_hours=7 * 24)
            logger.info("  [%s] VACUUM completado", table_path)
        except Exception as e:
            logger.warning("  [%s] VACUUM falló (no crítico): %s", table_path, e)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Compactar tablas Delta locales")
    parser.add_argument(
        "--vacuum", action="store_true",
        help="Ejecutar VACUUM después de compactar",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar información")
    args = parser.parse_args()

    logger.info("=== COMPACTACIÓN DELTA LOCAL ===")

    compacted = 0
    for table_path, _desc in TABLES:
        path = str(DELTA_ROOT / table_path)
        if not Path(path).exists():
            logger.info("  [%s] no existe", table_path)
            continue

        if args.dry_run:
            from deltalake import DeltaTable
            try:
                dt = DeltaTable(path)
                n_rows = dt.count()
                n_files = len(dt.file_uris())
                parts = dt.metadata().partition_columns
                logger.info(
                    "  [%s] %d filas, %d archivos, particiones: %s",
                    table_path, n_rows, n_files,
                    parts if parts else "(ninguna)",
                )
            except Exception as e:
                logger.info("  [%s] error: %s", table_path, e)
        else:
            if compact_table(table_path, vacuum=args.vacuum):
                compacted += 1

    logger.info("=== Compactadas %d tablas ===", compacted)


if __name__ == "__main__":
    main()
