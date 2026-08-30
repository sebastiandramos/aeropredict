"""Backup completo de la colección MongoDB ``aena_infovuelos`` a R2 (Delta/parquet).

Script de mantenimiento que vuelca TODA la colección Silver ``aena_infovuelos`` a
Cloudflare R2 como tabla Delta, antes de cualquier operación de deduplicación o
borrado. No destructivo: solo lectura de Mongo + escritura a R2.

Uso:
    doppler run -- python scripts/backup_aena_infovuelos.py [--tag <nombre>]

Requisitos:
    - Doppler CLI autenticado (provee MONGODB_URI y R2_* creds).
    - R2_* env vars presentes (R2_ENDPOINT_URL, R2_ACCESS_KEY_ID,
      R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME).
"""
from __future__ import annotations

import argparse
import logging
import os
import uuid
from datetime import UTC, datetime

import pyarrow as pa
import pymongo
from bson import ObjectId
from deltalake.writer import write_deltalake

from aeropredict.opensky.config import get_mongo_uri

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

COLLECTION = "aena_infovuelos"
R2_BUCKET = os.environ.get("R2_BUCKET_NAME", "aeropredict-landing-zone")

STORAGE_OPTIONS = {
    "AWS_ENDPOINT_URL": os.environ["R2_ENDPOINT_URL"],
    "AWS_ACCESS_KEY_ID": os.environ["R2_ACCESS_KEY_ID"],
    "AWS_SECRET_ACCESS_KEY": os.environ["R2_SECRET_ACCESS_KEY"],
    "AWS_REGION": "auto",
    "aws_conditional_put": "etag",
}


def _normalize(doc: dict) -> dict:
    """Convierte tipos BSON que PyArrow no serializa (ObjectId) a string.

    Si el doc contuviera anidados con ObjectId u otros BSON no-Arrow, se
    recorren recursivamente. Devuelve un dict Arrow-safe.
    """
    out: dict = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _normalize(v)
        elif isinstance(v, list):
            out[k] = [
                _normalize(x)
                if isinstance(x, dict)
                else (str(x) if isinstance(x, ObjectId) else x)
                for x in v
            ]
        else:
            out[k] = v
    return out


def read_collection(uri: str) -> pa.Table:
    """Lee toda la colección aena_infovuelos y la convierte a PyArrow."""
    logger.info("Conectando a MongoDB...")
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client.get_database()
    coll = db[COLLECTION]
    total = coll.estimated_document_count()
    logger.info("Colección %s: ~%d docs", COLLECTION, total)

    # Nota: no usar no_cursor_timeout — Atlas tier M0 lo prohíbe (error 8000).
    # Con batch_size=5000 y 487K docs la lectura termina en <10 min (timeout default).
    cursor = coll.find(batch_size=5000)
    try:
        docs: list[dict] = []
        for i, doc in enumerate(cursor):
            # Normaliza tipos no-Arrow (ObjectId -> str) para poder construir el pa.Table.
            docs.append(_normalize(doc))
            if (i + 1) % 100_000 == 0:
                logger.info("  Leídos %d docs...", i + 1)
    finally:
        cursor.close()
    client.close()

    logger.info("Total leído: %d docs", len(docs))
    return pa.Table.from_pylist(docs)


def write_to_r2(table: pa.Table, tag: str) -> str:
    """Escribe la tabla en R2 como Delta, bajo backup/aena_infovuelos/<tag>."""
    r2_path = f"s3://{R2_BUCKET}/backup/aena_infovuelos/{tag}"
    write_deltalake(
        r2_path,
        table,
        mode="append",
        storage_options=STORAGE_OPTIONS,
    )
    logger.info("Backup escrito en %s (%d filas)", r2_path, len(table))
    return r2_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup de aena_infovuelos a R2")
    parser.add_argument("--tag", default=None, help="Etiqueta del backup (default: timestamp)")
    args = parser.parse_args()

    tag = args.tag or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    logger.info("=== Backup aena_infovuelos a R2 ===")
    logger.info("R2 bucket: %s", R2_BUCKET)

    uri = get_mongo_uri()
    table = read_collection(uri)
    write_to_r2(table, tag)

    logger.info("=== Backup completado (tag=%s) ===", tag)


if __name__ == "__main__":
    main()
