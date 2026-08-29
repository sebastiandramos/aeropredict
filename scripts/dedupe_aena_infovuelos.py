"""Deduplicación no destructiva de la colección MongoDB ``aena_infovuelos``.

Elimina las *re-capturas* de un vuelo cuyo estado importante ya ha sido visto,
conservando la evolución temporal de cada vuelo programado.

Estado importante (lo que cuenta como "cambio" que justifica conservar un doc):
    ``status``, ``terminal``, ``estimated_local``
Los cambios de ``gate_first``/``gate_second`` (y ``checkin_*``) NO se consideran
importantes, de modo que un vuelo que solo cambia de puerta no genera docs nuevos.

Para cada grupo de vuelo definido por (flight_number, aena_airport_iata,
flight_type, scheduled_local):
    1. Ordena sus docs por snapshot_at_utc asc (y ingested_at como desempate).
    2. Conserva el primero de cada estado importante DISTINTO (evolución).
    3. Elimina el resto (re-capturas con estado ya visto).

Seguridad: por defecto corre en modo ``--dry-run`` (solo cuenta lo que se
borraría). Para aplicar el borrado usar ``--apply``. Se recomienda ejecutar
antes ``scripts/backup_aena_infovuelos.py`` para volcar la colección a R2.

Uso:
    doppler run -- python scripts/dedupe_aena_infovuelos.py            # dry-run
    doppler run -- python scripts/dedupe_aena_infovuelos.py --apply    # aplicar

Requisitos:
    - Doppler CLI autenticado (provee MONGODB_URI).
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict

import pymongo

from aeropredict.opensky.config import get_mongo_uri

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

COLLECTION = "aena_infovuelos"

# Campos a traer para decidir (más _id implícito)
PROJECTION = {
    "snapshot_at_utc": 1,
    "ingested_at": 1,
    "flight_number": 1,
    "aena_airport_iata": 1,
    "flight_type": 1,
    "scheduled_local": 1,
    "status": 1,
    "terminal": 1,
    "estimated_local": 1,
}

# Campos cuyo cambio se considera "importante" (generan un doc nuevo)
IMPORTANT_STATE = ("status", "terminal", "estimated_local")

BATCH = 5000  # docs a procesar en cada bulk delete


def _group_key(doc: dict) -> tuple:
    return (
        doc.get("flight_number"),
        doc.get("aena_airport_iata"),
        doc.get("flight_type"),
        doc.get("scheduled_local"),
    )


def _state_key(doc: dict) -> tuple:
    return tuple(doc.get(f) for f in IMPORTANT_STATE)


def _sort_key(doc: dict) -> tuple:
    # snapshot_at_utc como principal, ingested_at como desempate estable
    return (doc.get("snapshot_at_utc") or "", doc.get("ingested_at") or "")


def compute_ids_to_delete(docs: list[dict]) -> tuple[list[object], int, int]:
    """Devuelve (ids_a_eliminar, docs_conservados, grupos_totales)."""
    groups: dict = defaultdict(list)
    for doc in docs:
        groups[_group_key(doc)].append(doc)

    ids_to_delete: list[object] = []
    kept = 0
    for _gkey, group in groups.items():
        # Orden temporal estable dentro del grupo
        group.sort(key=_sort_key)
        seen_states: set = set()
        for doc in group:
            skey = _state_key(doc)
            if skey in seen_states:
                ids_to_delete.append(doc["_id"])
            else:
                seen_states.add(skey)
                kept += 1

    return ids_to_delete, kept, len(groups)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dedupe de aena_infovuelos en MongoDB")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar el borrado (defecto: dry-run, solo reporta).",
    )
    args = parser.parse_args()

    uri = get_mongo_uri()
    logger.info("Conectando a MongoDB...")
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client.get_database()
    coll = db[COLLECTION]
    total_before = coll.estimated_document_count()
    logger.info("Colección %s: ~%d docs", COLLECTION, total_before)

    # Nota: no usar no_cursor_timeout (Atlas M0 lo prohíbe, error 8000).
    # OJO: pasar la proyección como kwarg (como primer positional sería el filtro y
    # no devolvería nada).
    cursor = coll.find({}, projection=PROJECTION, batch_size=5000)
    try:
        docs = list(cursor)
    finally:
        cursor.close()
    client.close()
    logger.info("Leídos %d docs para análisis.", len(docs))

    ids_to_delete, kept, n_groups = compute_ids_to_delete(docs)
    logger.info("Grupos de vuelo (flight+airport+type+sched_local): %d", n_groups)
    logger.info("Docs a CONSERVAR (evolución de estado): %d", kept)
    logger.info("Docs a ELIMINAR (re-capturas): %d", len(ids_to_delete))
    if docs:
        logger.info(
            "Reducción: %d → %d (%.0f%% menos)",
            len(docs),
            kept,
            100.0 * len(ids_to_delete) / len(docs),
        )

    if not args.apply:
        logger.info("MODO DRY-RUN: no se ha borrado nada. Usa --apply para ejecutar.")
        return

    # Aplicar borrado por chunks
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client.get_database()
    coll = db[COLLECTION]
    deleted_total = 0
    for i in range(0, len(ids_to_delete), BATCH):
        chunk = ids_to_delete[i : i + BATCH]
        res = coll.delete_many({"_id": {"$in": chunk}})
        deleted_total += res.deleted_count
        logger.info("  Eliminados %d docs (acumulado %d)...", res.deleted_count, deleted_total)
    total_after = coll.estimated_document_count()
    client.close()

    logger.info("=== Dedupe aplicado ===")
    logger.info("Total antes: ~%d", total_before)
    logger.info("Total después: ~%d", total_after)
    logger.info("Eliminados: %d", deleted_total)


if __name__ == "__main__":
    main()
