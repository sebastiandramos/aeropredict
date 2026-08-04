#!/usr/bin/env python3
"""Script 4/5: Sync entity tables from MongoDB (Silver) → PostgreSQL (Gold).

Copies the ``flights``, ``aircraft``, ``weather``, ``aena_infovuelos``,
``metar``, ``holidays``, ``eurocontrol_pru``, ``notam``, ``airports`` and
``runways`` collections from MongoDB into ``gold.*`` tables in PostgreSQL.

Usage:
    python scripts/silver_to_gold_entities.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from pymongo import MongoClient

from aeropredict.opensky.checkpoint_mongo import (
    add_to_checkpoint_set,
    clear_checkpoints,
    get_checkpoint_value,
    set_checkpoint_value,
)
from aeropredict.opensky.config import get_mongo_uri
from aeropredict.opensky.storage_gold import (
    _get_conn as get_gold_conn,
)
from aeropredict.opensky.storage_gold import (
    write_aena_infovuelos_gold,
    write_aircraft_gold,
    write_airports_gold,
    write_eurocontrol_pru_gold,
    write_flights_gold_raw,
    write_holidays_gold,
    write_metar_gold,
    write_notam_gold,
    write_runways_gold,
    write_weather_gold,
)

CHECKPOINT_COLLECTION = "silver_to_gold_entities"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("silver_to_gold_entities")

# Campos relevantes de flights en MongoDB
FLIGHTS_FIELDS = {
    "icao24": 1,
    "callsign": 1,
    "first_seen": 1,
    "last_seen": 1,
    "flight_date": 1,
    "est_departure_airport": 1,
    "est_arrival_airport": 1,
    "departure_airport_horiz_distance": 1,
    "departure_airport_vert_distance": 1,
    "arrival_airport_horiz_distance": 1,
    "arrival_airport_vert_distance": 1,
    "departure_airport_candidates_count": 1,
    "arrival_airport_candidates_count": 1,
    "_id": 0,
}

# Campos relevantes de aircraft en MongoDB
AIRCRAFT_FIELDS = {
    "icao24": 1,
    "typecode": 1,
    "manufacturer": 1,
    "operator": 1,
    "first_flight_date": 1,
    "icao_aircraft_type": 1,
    "registration": 1,
    "serial_number": 1,
    "_id": 0,
}

# Campos relevantes de weather en MongoDB
WEATHER_FIELDS = {
    "airport_code": 1,
    "timestamp": 1,
    "flight_date": 1,
    "temperature_2m": 1,
    "precipitation": 1,
    "wind_speed_10m": 1,
    "wind_gusts_10m": 1,
    "visibility": 1,
    "cloud_cover": 1,
    "relative_humidity_2m": 1,
    "_id": 0,
}

# Campos relevantes de aena_infovuelos en MongoDB
AENA_FIELDS = {
    "snapshot_at_utc": 1,
    "flight_number": 1,
    "aena_airport_iata": 1,
    "flight_type": 1,
    "source": 1,
    "query_airport_iata": 1,
    "query_flight_type": 1,
    "raw_flight_number": 1,
    "airline_iata": 1,
    "airline_icao": 1,
    "airline_name": 1,
    "icao24_airport": 1,
    "other_airport_iata": 1,
    "other_city": 1,
    "scheduled_date": 1,
    "scheduled_time": 1,
    "scheduled_local": 1,
    "estimated_date": 1,
    "estimated_time": 1,
    "estimated_local": 1,
    "status": 1,
    "terminal": 1,
    "gate_first": 1,
    "gate_second": 1,
    "checkin_from": 1,
    "checkin_to": 1,
    "aircraft_type": 1,
    "_id": 1,
}

# Campos relevantes de metar en MongoDB
METAR_FIELDS = {
    "icao_id": 1,
    "raw_ob": 1,
    "receipt_time": 1,
    "obs_time": 1,
    "temp": 1,
    "dewp": 1,
    "wdir": 1,
    "wspd": 1,
    "wgst": 1,
    "visib": 1,
    "altim": 1,
    "flt_cat": 1,
    "clouds_base": 1,
    "_id": 0,
}

# Campos relevantes de holidays en MongoDB
HOLIDAYS_FIELDS = {
    "date": 1,
    "name": 1,
    "local_name": 1,
    "country_code": 1,
    "is_global": 1,
    "counties": 1,
    "types": 1,
    "source": 1,
    "subdivision": 1,
    "_id": 0,
}

# Campos relevantes de eurocontrol_pru en MongoDB (columnas dinámicas)
EUROCONTROL_FIELDS = None

# Campos relevantes de notam en MongoDB
NOTAM_FIELDS = {
    "feature": 1,
    "layer": 1,
    "snapshot_at": 1,
    "_id": 0,
}

# Campos relevantes de airports en MongoDB
AIRPORTS_FIELDS = {
    "ident": 1,
    "type": 1,
    "name": 1,
    "latitude_deg": 1,
    "longitude_deg": 1,
    "elevation_ft": 1,
    "iso_country": 1,
    "iso_region": 1,
    "municipality": 1,
    "iata_code": 1,
    "icao_code": 1,
    "_id": 0,
}

# Campos relevantes de runways en MongoDB
RUNWAYS_FIELDS = {
    "airport_ident": 1,
    "length_ft": 1,
    "width_ft": 1,
    "surface": 1,
    "le_ident": 1,
    "he_ident": 1,
    "le_heading_degT": 1,
    "he_heading_degT": 1,
    "_id": 0,
}


def _stats() -> dict[str, int]:
    """Cuenta documentos en MongoDB y PostgreSQL."""
    mongo = MongoClient(get_mongo_uri())
    mdb = mongo.get_database()
    pg = get_gold_conn()

    stats: dict[str, int] = {}

    for col in (
        "flights",
        "aircraft",
        "weather",
        "aena_infovuelos",
        "metar",
        "holidays",
        "eurocontrol_pru",
        "notam",
        "airports",
        "runways",
    ):
        stats[f"mongo_{col}"] = mdb[col].count_documents({})
        with pg.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM gold.{col}")
            stats[f"gold_{col}"] = cur.fetchone()[0]

    mongo.close()
    return stats


def _sync_entity(
    mdb: Any,
    collection: str,
    fields: dict[str, int] | None,
    write_fn: Any,
    checkpoint_name: str,
) -> int:
    """Sync una entidad desde MongoDB a Gold.

    Siempre sincroniza (las funciones write usan ON CONFLICT / upsert,
    por lo que es seguro re-ejecutar). El checkpoint se usa solo como
    registro histórico, no para saltarse la sincronización.

    Si ``fields`` es None se leen todos los campos del documento
    (colecciones de esquema dinámico como eurocontrol_pru).
    """
    if fields is None:
        docs = list(mdb[collection].find({}))
    else:
        docs = list(mdb[collection].find({}, fields))
    logger.info("  %s: %d documentos", collection, len(docs))
    if docs:
        n = write_fn(docs)
        add_to_checkpoint_set(CHECKPOINT_COLLECTION, checkpoint_name)
        logger.info("  Gold %s: %d escritos", checkpoint_name, n)
        return n
    return 0


def _sync_entity_incremental(
    mdb: Any,
    collection: str,
    fields: dict[str, int],
    write_fn: Any,
    cursor_key: str,
    page_size: int = 2000,
) -> int:
    """Sync incremental de una entidad vía cursor ObjectId (_id).

    Sin cursor guardado → sync completo (todas las docs de una vez) y se
    persiste el cursor en el _id de la última doc. Con cursor → páginas
    ascendentes de ``_id > cursor`` (el cursor se persiste tras cada
    página escrita). Si una página falla, el cursor queda en la última
    página committeada y la siguiente ejecución la reprocesa; el
    ``ON CONFLICT DO NOTHING`` de las write_fn hace el reproceso
    idempotente. Devuelve el total de filas escritas.
    """
    cursor = get_checkpoint_value(CHECKPOINT_COLLECTION, cursor_key)
    total = 0

    if cursor is None:
        docs = list(mdb[collection].find({}, fields))
        logger.info("  %s: %d documentos (sync completo)", collection, len(docs))
        if docs:
            total += write_fn(docs)
            set_checkpoint_value(CHECKPOINT_COLLECTION, cursor_key, docs[-1]["_id"])
        return total

    logger.info("  %s: sync incremental desde cursor %s", collection, cursor)
    while True:
        docs = list(
            mdb[collection]
            .find({"_id": {"$gt": cursor}}, fields)
            .sort("_id", 1)
            .limit(page_size)
        )
        if not docs:
            break
        n = write_fn(docs)
        total += n
        cursor = docs[-1]["_id"]
        set_checkpoint_value(CHECKPOINT_COLLECTION, cursor_key, cursor)
        logger.info("  Gold %s: %d escritos (cursor %s)", cursor_key, n, cursor)

    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync entity tables: MongoDB → Gold (PostgreSQL)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostrar stats sin insertar nada",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Forzar re-sync aunque haya checkpoint",
    )
    args = parser.parse_args(argv)

    logger.info("=" * 60)
    logger.info(
        "Script 4/5: Entity sync: MongoDB → Gold (flights, aircraft, weather, "
        "aena_infovuelos, metar, holidays, eurocontrol_pru, notam, airports, runways)"
    )
    logger.info("=" * 60)

    # -- Conexión --
    logger.info("Conectando a MongoDB...")
    mongo = MongoClient(get_mongo_uri())
    mdb = mongo.get_database()

    if args.dry_run:
        stats = _stats()
        logger.info("Stats actuales:")
        logger.info(
            "  flights:  MongoDB=%d  Gold=%d",
            stats["mongo_flights"], stats["gold_flights"],
        )
        logger.info(
            "  aircraft: MongoDB=%d  Gold=%d",
            stats["mongo_aircraft"], stats["gold_aircraft"],
        )
        logger.info(
            "  weather:  MongoDB=%d  Gold=%d",
            stats["mongo_weather"], stats["gold_weather"],
        )
        logger.info(
            "  aena:     MongoDB=%d  Gold=%d",
            stats["mongo_aena_infovuelos"], stats["gold_aena_infovuelos"],
        )

        pending_flights = stats["mongo_flights"] - stats["gold_flights"]
        pending_aircraft = stats["mongo_aircraft"] - stats["gold_aircraft"]
        pending_weather = stats["mongo_weather"] - stats["gold_weather"]
        pending_aena = stats["mongo_aena_infovuelos"] - stats["gold_aena_infovuelos"]

        all_pending = (
            pending_flights <= 0
            and pending_aircraft <= 0
            and pending_weather <= 0
            and pending_aena <= 0
        )
        if all_pending:
            logger.info("Sin entidades pendientes. Todo al día.")
        else:
            logger.info(
                "Pendientes de sincronizar: %d flights, %d aircraft, %d weather, %d aena",
                pending_flights, pending_aircraft, pending_weather, pending_aena,
            )

        logger.info("=" * 60)
        logger.info("DRY RUN: no se insertó nada")
        logger.info("=" * 60)
        mongo.close()
        return 0

    # Limpiar checkpoints si --force (mismo path prefijado `checkpoints_`
    # que usan los helpers; antes borraba la colección sin prefijo, no-op).
    if args.force:
        logger.info("Force mode: eliminando checkpoints previos...")
        clear_checkpoints(CHECKPOINT_COLLECTION)

    # -- Sync entities --
    logger.info("Sincronizando entidades...")

    _sync_entity(mdb, "flights", FLIGHTS_FIELDS, write_flights_gold_raw, "flights")
    _sync_entity(mdb, "aircraft", AIRCRAFT_FIELDS, write_aircraft_gold, "aircraft")
    _sync_entity(mdb, "weather", WEATHER_FIELDS, write_weather_gold, "weather")
    _sync_entity_incremental(
        mdb,
        "aena_infovuelos",
        AENA_FIELDS,
        write_aena_infovuelos_gold,
        "aena_infovuelos_cursor",
    )
    _sync_entity(mdb, "metar", METAR_FIELDS, write_metar_gold, "metar")
    _sync_entity(mdb, "holidays", HOLIDAYS_FIELDS, write_holidays_gold, "holidays")
    _sync_entity(
        mdb,
        "eurocontrol_pru",
        EUROCONTROL_FIELDS,
        write_eurocontrol_pru_gold,
        "eurocontrol_pru",
    )
    _sync_entity(mdb, "notam", NOTAM_FIELDS, write_notam_gold, "notam")
    _sync_entity(mdb, "airports", AIRPORTS_FIELDS, write_airports_gold, "airports")
    _sync_entity(mdb, "runways", RUNWAYS_FIELDS, write_runways_gold, "runways")

    mongo.close()

    # Stats finales
    stats = _stats()
    logger.info("=" * 60)
    logger.info("SINCRONIZACIÓN COMPLETADA")
    logger.info("  flights:  MongoDB=%d  Gold=%d", stats["mongo_flights"], stats["gold_flights"])
    logger.info("  aircraft: MongoDB=%d  Gold=%d", stats["mongo_aircraft"], stats["gold_aircraft"])
    logger.info("  weather:  MongoDB=%d  Gold=%d", stats["mongo_weather"], stats["gold_weather"])
    logger.info(
        "  aena:     MongoDB=%d  Gold=%d",
        stats["mongo_aena_infovuelos"], stats["gold_aena_infovuelos"],
    )
    logger.info(
        "  metar:    MongoDB=%d  Gold=%d",
        stats["mongo_metar"], stats["gold_metar"],
    )
    logger.info(
        "  holidays: MongoDB=%d  Gold=%d",
        stats["mongo_holidays"], stats["gold_holidays"],
    )
    logger.info(
        "  eurocontrol_pru: MongoDB=%d  Gold=%d",
        stats["mongo_eurocontrol_pru"], stats["gold_eurocontrol_pru"],
    )
    logger.info(
        "  notam:    MongoDB=%d  Gold=%d",
        stats["mongo_notam"], stats["gold_notam"],
    )
    logger.info(
        "  airports: MongoDB=%d  Gold=%d",
        stats["mongo_airports"], stats["gold_airports"],
    )
    logger.info(
        "  runways:  MongoDB=%d  Gold=%d",
        stats["mongo_runways"], stats["gold_runways"],
    )
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
