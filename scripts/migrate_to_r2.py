"""Migrar tablas Bronze locales a Cloudflare R2."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pyarrow as pa
from deltalake.writer import write_deltalake

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DELTA_ROOT = Path("data/raw")
R2_BUCKET = os.environ.get("R2_BUCKET_NAME", "aeropredict-landing-zone")

STORAGE_OPTIONS = {
    "AWS_ENDPOINT_URL": os.environ["R2_ENDPOINT_URL"],
    "AWS_ACCESS_KEY_ID": os.environ["R2_ACCESS_KEY_ID"],
    "AWS_SECRET_ACCESS_KEY": os.environ["R2_SECRET_ACCESS_KEY"],
    "AWS_REGION": "auto",
    "aws_conditional_put": "etag",
}

BRONZE_TABLES = [
    "opensky",
    "schedules_aerodatabox",
    "weather_openmeteo",
]
SILVER_TABLES = [
    "aircraft",
    "weather",
    "schedules",
]

def read_delta(path: str) -> pa.Table | None:
    """Lee una tabla Delta local."""
    from deltalake import DeltaTable
    try:
        dt = DeltaTable(path)
        data = dt.to_pyarrow_table()
        logger.info("  Leídas %d filas de %s", len(data), path)
        return data
    except Exception as e:
        logger.warning("  No se pudo leer %s: %s", path, e)
        return None

def write_to_r2(table_name: str, data: pa.Table, prefix: str) -> None:
    """Escribe una tabla PyArrow en R2 como Delta."""
    r2_path = f"s3://{R2_BUCKET}/{prefix}/{table_name}"
    try:
        write_deltalake(
            r2_path,
            data,
            mode="append",
            storage_options=STORAGE_OPTIONS,
        )
        logger.info("  Escritas %d filas en %s", len(data), r2_path)
    except Exception as e:
        logger.error("  Error escribiendo %s: %s", r2_path, e)

def main() -> None:
    logger.info("=== Migrando Bronze a R2 ===")
    logger.info("Bucket: %s", R2_BUCKET)
    logger.info("Endpoint: %s", STORAGE_OPTIONS["AWS_ENDPOINT_URL"][:50] + "...")

    for table in BRONZE_TABLES:
        local = str(DELTA_ROOT / "bronze" / table)
        logger.info("[bronze/%s]", table)
        data = read_delta(local)
        if data is not None:
            write_to_r2(table, data, "bronze")
        else:
            logger.info("  → Saltado")

    logger.info("=== Verificando Silver ===")
    for table in SILVER_TABLES:
        local = str(DELTA_ROOT / "silver" / table)
        if not Path(local).exists():
            logger.info("  [silver/%s] no existe localmente", table)
            continue
        logger.info("[silver/%s]", table)
        data = read_delta(local)
        if data is not None:
            write_to_r2(table, data, "silver")

    logger.info("=== Migración completada ===")

if __name__ == "__main__":
    main()
