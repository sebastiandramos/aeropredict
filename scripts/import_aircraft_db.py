"""Importación de la base de datos de aeronaves de OpenSky.

Descarga el CSV público (opcional con --download) e importa a MongoDB.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from aeropredict.opensky.config import get_opensky_aircraft_db_path
from aeropredict.sources.aircraft_db import (
    download_aircraft_csv,
    import_aircraft_to_mongodb,
    parse_aircraft_csv,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Importar aircraft DB de OpenSky")
    parser.add_argument("--download", action="store_true",
                        help="Descargar CSV fresco desde OpenSky")
    parser.add_argument("--path", default=None,
                        help=f"Ruta al CSV (default: {get_opensky_aircraft_db_path()})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostrar stats sin importar")
    args = parser.parse_args()

    csv_path = args.path or get_opensky_aircraft_db_path()

    # -- Download --
    if args.download:
        logger.info("Descargando aircraft DB...")
        try:
            csv_path = download_aircraft_csv(dest_path=csv_path)
        except Exception as e:
            logger.error("Error descargando CSV: %s", e)
            logger.info(
                "Descarga manual: visita https://opensky-network.org/datasets/metadata/ "
                "y busca el archivo aircraft-database-comprehensive-*.csv"
            )
            return
    else:
        if not Path(csv_path).exists():
            logger.error("CSV no encontrado: %s", csv_path)
            logger.info("Usa --download para descargarlo automáticamente")
            return

    # -- Parse --
    logger.info("Parseando CSV: %s", csv_path)
    try:
        records = parse_aircraft_csv(csv_path)
    except Exception as e:
        logger.error("Error parseando CSV: %s", e)
        return

    if not records:
        logger.warning("CSV vacío")
        return

    # Stats
    manufacturers = {r["manufacturer"] for r in records if r["manufacturer"]}
    operators = {r["operator"] for r in records if r["operator"]}
    logger.info("Registros: %d | Fabricantes: %d | Operadores: %d",
                len(records), len(manufacturers), len(operators))

    if args.dry_run:
        logger.info("Dry-run: no se importó nada")
        return

    # -- Import a MongoDB --
    logger.info("Importando a MongoDB...")
    imported = import_aircraft_to_mongodb(csv_path)
    logger.info("Importados %d registros a MongoDB", imported)

    # Mostrar tamaño del archivo
    size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    logger.info("Tamaño CSV: %.1f MB", size_mb)


if __name__ == "__main__":
    main()
