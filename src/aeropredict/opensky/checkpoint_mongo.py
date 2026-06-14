"""Checkpoints en MongoDB para idempotencia del pipeline.

Cada script usa una colección separada bajo ``checkpoints.*``:

  - ``checkpoints.bronze_extract``:  {date: [airport_codes]}
  - ``checkpoints.bronze_to_silver``: {dates_done: [date_strs]}

La conexión MongoDB se hace vía ``config.get_mongo_uri()``, lazy, y se
reusa en todos los módulos de la sesión.
"""

from __future__ import annotations

import logging
from typing import Any

import pymongo
from pymongo.collection import Collection

from .config import get_mongo_uri

logger = logging.getLogger(__name__)

_client: pymongo.MongoClient[Any] | None = None


def _connect() -> pymongo.database.Database[Any]:
    global _client
    if _client is None:
        uri = get_mongo_uri()
        logger.info("Checkpoint MongoDB: conectando a %s", uri)
        _client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        _client.admin.command("ping")
    return _client.get_database()


def _collection(name: str) -> Collection[Any]:
    return _connect()[f"checkpoints_{name}"]


# ── Diccionario fecha → lista ──────────────────────────────────────
# Usado por extract_to_bronze.py → colección "bronze_extract"
# Documento: {_id: "YYYY-MM-DD", airports: ["LEMD", "LEBL", ...]}

def get_checkpoint_dict(collection_name: str) -> dict[str, list[str]]:
    """Carga checkpoint: {date_str: [airport_code, ...]}."""
    col = _collection(collection_name)
    out: dict[str, list[str]] = {}
    for doc in col.find({}, {"_id": 1, "airports": 1}):
        if doc.get("airports"):
            out[str(doc["_id"])] = list(doc["airports"])
    return out


def save_checkpoint_dict_entry(
    collection_name: str,
    date_str: str,
    airports: list[str],
) -> None:
    """Guarda/actualiza los aeropuertos extraídos para una fecha."""
    col = _collection(collection_name)
    col.update_one(
        {"_id": date_str},
        {"$addToSet": {"airports": {"$each": airports}}},
        upsert=True,
    )
    logger.info(
        "Checkpoint [%s] actualizado: %s → %d aeropuertos",
        collection_name, date_str, len(airports),
    )


# ── Conjunto de fechas ─────────────────────────────────────────────
# Usado por bronze_to_silver.py → colección "bronze_to_silver"
# Documento único: {_id: "dates_done", dates: ["YYYY-MM-DD", ...]}

def get_checkpoint_set(collection_name: str) -> set[str]:
    """Carga checkpoint como set de strings."""
    col = _collection(collection_name)
    doc = col.find_one({"_id": "dates_done"})
    if doc and doc.get("dates"):
        return set(doc["dates"])
    return set()


def add_to_checkpoint_set(collection_name: str, value: str) -> None:
    """Añade un string al set checkpoint."""
    col = _collection(collection_name)
    col.update_one(
        {"_id": "dates_done"},
        {"$addToSet": {"dates": value}},
        upsert=True,
    )
    logger.info("Checkpoint [%s] actualizado: añadido %s", collection_name, value)
