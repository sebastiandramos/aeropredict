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
        logger.debug("Checkpoint MongoDB: conectando a %s", uri)
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


# ── Par clave → valor genérico ─────────────────────────────────────
# Documento: {_id: key, value: value}. El value puede ser cualquier
# tipo BSON (p.ej. un ObjectId de cursor), sin stringificar.

def get_checkpoint_value(collection_name: str, key: str) -> Any:
    """Lee un valor de checkpoint genérico, o None si no existe."""
    col = _collection(collection_name)
    doc = col.find_one({"_id": key})
    if doc is None:
        return None
    return doc.get("value")


def set_checkpoint_value(collection_name: str, key: str, value: Any) -> None:
    """Guarda/actualiza un valor de checkpoint genérico (upsert)."""
    col = _collection(collection_name)
    col.replace_one(
        {"_id": key},
        {"_id": key, "value": value},
        upsert=True,
    )
    logger.info("Checkpoint [%s] actualizado: %s", collection_name, key)


def clear_checkpoints(collection_name: str) -> None:
    """Elimina todos los checkpoints de una colección (reset)."""
    col = _collection(collection_name)
    col.delete_many({})
