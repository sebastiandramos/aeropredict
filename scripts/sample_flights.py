"""Sampleo estratificado de ~400 vuelos desde MongoDB.

Cubre todas las rutas únicas, aerolíneas y bloques horarios.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict

from pymongo import MongoClient

from aeropredict.opensky.config import get_mongo_uri

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

SAMPLE_TARGET = 400
HOUR_BLOCKS = [(0, 6), (6, 12), (12, 18), (18, 24)]


def _get_hour_block(hour: int) -> str:
    for lo, hi in HOUR_BLOCKS:
        if lo <= hour < hi:
            return f"{lo:02d}-{hi:02d}"
    return "00-06"


def sample_flights(target: int = SAMPLE_TARGET) -> list[dict]:
    """Sampleo estratificado: cubre rutas, aerolineas y bloques horarios.

    Returns:
        Lista de dicts con campos seleccionados.
    """
    uri = get_mongo_uri()
    client = MongoClient(uri)
    db = client.get_database()
    col = db["flights"]

    total = col.count_documents({})
    logger.info("Total vuelos en MongoDB: %d", total)

    if total == 0:
        logger.warning("No hay vuelos — devolviendo lista vacía")
        return []

    # Agrupar vuelos por ruta + aerolínea + bloque horario
    buckets: dict[str, list[dict]] = defaultdict(list)

    for doc in col.find(
        {"callsign": {"$ne": None}},
        {
            "_id": 1,
            "icao24": 1,
            "callsign": 1,
            "est_departure_airport": 1,
            "est_arrival_airport": 1,
            "first_seen": 1,
            "last_seen": 1,
            "flight_date": 1,
        },
    ).limit(100_000):
        dep = doc.get("est_departure_airport") or "UNKNOWN"
        arr = doc.get("est_arrival_airport") or "UNKNOWN"
        callsign = doc.get("callsign") or "UNKNOWN"
        airline = callsign[:3] if len(callsign) >= 3 else callsign

        first_seen = doc.get("first_seen")
        hour = first_seen.hour if first_seen else 0
        block = _get_hour_block(hour)

        key = f"{dep}→{arr}|{airline}|{block}"
        doc["flight_date_str"] = str(doc.get("flight_date", ""))
        doc["first_seen_str"] = str(first_seen) if first_seen else ""
        doc["last_seen_str"] = str(doc.get("last_seen", ""))
        doc["_id"] = str(doc["_id"])
        buckets[key].append(doc)

    # Seleccionar 1-2 vuelos por bucket hasta alcanzar target
    samples: list[dict] = []
    bucket_list = list(buckets.items())
    bucket_list.sort(key=lambda x: len(x[1]), reverse=True)

    for _key, docs in bucket_list:
        if len(samples) >= target:
            break
        # 1 vuelo por bucket, o 2 si hay muchos vuelos en ese bucket
        take = 2 if len(docs) >= 10 else 1
        take = min(take, target - len(samples))
        samples.extend(docs[:take])

    client.close()

    logger.info(
        "Sample: %d vuelos de %d buckets (target: %d)",
        len(samples), len(bucket_list), target,
    )

    routes = {(s["est_departure_airport"], s["est_arrival_airport"]) for s in samples}
    airlines = {(s.get("callsign") or "")[:3] for s in samples if s.get("callsign")}
    logger.info("Rutas únicas: %d | Aerolíneas: %d", len(routes), len(airlines))

    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Sampleo estratificado de vuelos")
    parser.add_argument(
        "--target", type=int, default=SAMPLE_TARGET,
        help="Número objetivo de muestras (default: 400)",
    )
    parser.add_argument(
        "--output", default="data/sample_flights.json",
        help="Ruta de salida JSON (default: data/sample_flights.json)",
    )
    args = parser.parse_args()

    samples = sample_flights(args.target)

    with open(args.output, "w") as f:
        json.dump(samples, f, indent=2, default=str)

    logger.info("Sample guardado en %s (%d vuelos)", args.output, len(samples))


if __name__ == "__main__":
    main()
