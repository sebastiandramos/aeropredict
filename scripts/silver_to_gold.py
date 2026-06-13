#!/usr/bin/env python3
"""Script 3/3: Silver (MongoDB) → Gold (PostgreSQL).

Lee vuelos desde MongoDB, los convierte a objetos Flight y actualiza las
tablas Gold en PostgreSQL (daily_airport_traffic, route_density, hourly_distribution).

Uso:
    python scripts/silver_to_gold.py [--date YYYY-MM-DD] [--dry-run]

Sin --date: detecta automáticamente fechas en Silver no procesadas en Gold.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, date as date_type
from typing import Any

import pymongo

from aeropredict.opensky.config import get_mongo_uri, get_postgres_uri
from aeropredict.opensky.logging_config import setup_daily_logger
from aeropredict.opensky.models import Flight
from aeropredict.opensky.storage_gold import (
    write_flights_gold,
    close as close_gold,
    _get_conn as get_gold_conn,
)

logger = logging.getLogger("silver_to_gold")

# Campos MongoDB → constructor Flight
_FIELD_MAP: dict[str, str] = {
    "icao24": "icao24",
    "callsign": "callsign",
    "first_seen": "first_seen",
    "last_seen": "last_seen",
    "est_departure_airport": "est_departure_airport",
    "est_arrival_airport": "est_arrival_airport",
    "departure_airport_horiz_distance": "est_departure_airport_horiz_distance",
    "departure_airport_vert_distance": "est_departure_airport_vert_distance",
    "arrival_airport_horiz_distance": "est_arrival_airport_horiz_distance",
    "arrival_airport_vert_distance": "est_arrival_airport_vert_distance",
    "departure_airport_candidates_count": "departure_airport_candidates_count",
    "arrival_airport_candidates_count": "arrival_airport_candidates_count",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Script 3/3: Procesa Silver (MongoDB) → Gold (PostgreSQL)",
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="Fecha concreta YYYY-MM-DD (default: autodetecta fechas pendientes)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra lo que procesaría")
    return parser.parse_args(argv)


def _mongo_doc_to_flight(doc: dict[str, Any]) -> Flight | None:
    """Convierte un documento de MongoDB a objeto Flight."""
    first_seen = doc.get("first_seen")
    last_seen = doc.get("last_seen")

    if not first_seen or not last_seen:
        return None

    return Flight(
        icao24=str(doc.get("icao24", "")),
        first_seen=first_seen if isinstance(first_seen, datetime) else None,
        last_seen=last_seen if isinstance(last_seen, datetime) else None,
        est_departure_airport=doc.get("est_departure_airport"),
        est_arrival_airport=doc.get("est_arrival_airport"),
        callsign=doc.get("callsign"),
        est_departure_airport_horiz_distance=doc.get("departure_airport_horiz_distance"),
        est_departure_airport_vert_distance=doc.get("departure_airport_vert_distance"),
        est_arrival_airport_horiz_distance=doc.get("arrival_airport_horiz_distance"),
        est_arrival_airport_vert_distance=doc.get("arrival_airport_vert_distance"),
        departure_airport_candidates_count=doc.get("departure_airport_candidates_count"),
        arrival_airport_candidates_count=doc.get("arrival_airport_candidates_count"),
    )


def _get_gold_dates() -> set[date_type]:
    """Obtiene fechas ya procesadas en Gold."""
    try:
        conn = get_gold_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT flight_date FROM gold.daily_airport_traffic")
            return {row[0] for row in cur.fetchall()}
    except Exception as exc:
        logger.warning("No se pudo consultar Gold (quizás tabla vacía): %s", exc)
        return set()


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)

    logger.info("=" * 60)
    logger.info("Script 3/3: Silver → Gold")
    logger.info("=" * 60)

    # Early exit: --dry-run + --date no requiere conexión a BD
    if args.dry_run and args.date:
        target_date = date_type.fromisoformat(args.date)
        logger.info("DRY RUN: procesaría fecha %s desde Silver → Gold", target_date)
        logger.info("  (sin conexión MongoDB — usa `--dry-run` sin `--date` para conteo real)")
        logger.info("=" * 60)
        logger.info("GOLD DRY RUN: 1 fecha")
        logger.info("=" * 60)
        return 0

    uri = get_mongo_uri()
    logger.info("Conectando a MongoDB: %s", uri)

    try:
        mongo_client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command("ping")
    except Exception as e:
        logger.error("No se pudo conectar a MongoDB: %s", e)
        return 1

    db = mongo_client.get_database()
    flights_col = db["flights"]

    # Determinar fechas a procesar
    if args.date:
        target_date = date_type.fromisoformat(args.date)
        pending_dates: list[date_type] = [target_date]
        logger.info("Fecha específica: %s", target_date)
    else:
        gold_dates = _get_gold_dates()
        # MongoDB almacena flight_date como datetime con tz; PG como DATE.
        # Normalizamos ambas a date para comparación correcta.
        all_silver_dates = {
            d.date() if isinstance(d, datetime) else d
            for d in flights_col.distinct("flight_date")
            if d is not None
        }
        pending_dates = sorted(all_silver_dates - gold_dates)
        logger.info(
            "Silver: %d fechas | Gold: %d fechas | Pendientes: %d",
            len(all_silver_dates), len(gold_dates), len(pending_dates),
        )

    if not pending_dates:
        logger.info("No hay fechas pendientes en Silver para procesar")
        mongo_client.close()
        return 0

    total_flights = 0
    total_dates = 0

    for target_date in pending_dates:
        # Leer vuelos de MongoDB para esta fecha
        # MongoDB almacena flight_date sin timezone (tzinfo=None)
        target_dt = datetime(target_date.year, target_date.month, target_date.day)
        cursor = flights_col.find({"flight_date": target_dt})
        docs = list(cursor)
        logger.info("--- Fecha %s: %d docs en Silver ---", target_date, len(docs))

        if args.dry_run:
            logger.info("  DRY RUN: %d vuelos listos para Gold", len(docs))
            total_flights += len(docs)
            total_dates += 1
            continue

        if not docs:
            continue

        # Convertir a Flight objects
        flights: list[Flight] = []
        skipped = 0
        for doc in docs:
            f = _mongo_doc_to_flight(doc)
            if f is not None:
                flights.append(f)
            else:
                skipped += 1

        if skipped:
            logger.debug("  %d docs saltados (sin first_seen/last_seen)", skipped)

        # Escribir a Gold
        try:
            counts = write_flights_gold(flights)
            logger.info("  Gold: %s", counts)
        except Exception as e:
            logger.error("  Error escribiendo Gold para %s: %s", target_date, e)
            continue

        total_flights += len(flights)
        total_dates += 1

    mongo_client.close()
    close_gold()

    logger.info("=" * 60)
    if args.dry_run:
        logger.info("GOLD DRY RUN: %d fechas, %d vuelos", total_dates, total_flights)
    else:
        logger.info(
            "SILVER→GOLD COMPLETADO: %d fechas, %d vuelos",
            total_dates, total_flights,
        )
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
