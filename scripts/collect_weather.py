"""Colección de datos meteorológicos para aeropuertos.

Usa Open-Meteo historical archive para obtener datos horarios
de temperatura, precipitación, viento, visibilidad, etc.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from typing import Any

from pymongo import MongoClient

from aeropredict.opensky.config import get_delta_root, get_mongo_uri
from aeropredict.opensky.storage import write_raw_json
from aeropredict.sources.airport_coords import AIRPORT_COORDS
from aeropredict.sources.openmeteo import OpenMeteoAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _get_airport_date_ranges(
    airport: str | None = None,
) -> list[tuple[str, str, str]]:
    """Obtiene (icao, start_date, end_date) desde los vuelos en MongoDB.

    Args:
        airport: Filtrar por aeropuerto específico (opcional).

    Returns:
        Lista de tuplas (icao, start_date, end_date).
    """
    client = MongoClient(get_mongo_uri())
    db = client.get_database()
    flights = db["flights"]

    pipeline: list[dict[str, Any]] = [
        {"$group": {
            "_id": "$est_departure_airport",
            "min_date": {"$min": "$flight_date"},
            "max_date": {"$max": "$flight_date"},
        }},
        {"$match": {"_id": {"$ne": None}}},
    ]

    if airport:
        pipeline[1]["$match"]["_id"] = airport  # type: ignore[index]

    results = list(flights.aggregate(pipeline))

    # También arrivals
    pipeline2: list[dict[str, Any]] = [
        {"$group": {
            "_id": "$est_arrival_airport",
            "min_date": {"$min": "$flight_date"},
            "max_date": {"$max": "$flight_date"},
        }},
        {"$match": {"_id": {"$ne": None}}},
    ]
    if airport:
        pipeline2[1]["$match"]["_id"] = airport  # type: ignore[index]

    results2 = list(flights.aggregate(pipeline2))

    client.close()

    # Combinar ambos grupos por aeropuerto
    ranges: dict[str, tuple[str, str]] = {}
    for r in results + results2:
        icao = r["_id"]
        if icao not in AIRPORT_COORDS:
            continue
        mn = str(r["min_date"])[:10] if r["min_date"] else ""
        mx = str(r["max_date"])[:10] if r["max_date"] else ""
        if icao in ranges:
            old_min, old_max = ranges[icao]
            mn = min(mn, old_min) if mn and old_min else mn or old_min
            mx = max(mx, old_max) if mx and old_max else mx or old_max
        ranges[icao] = (mn, mx)

    result_list = [(icao, mn, mx) for icao, (mn, mx) in ranges.items() if mn and mx]
    result_list.sort()
    return result_list


def _has_weather(airport: str, date: str) -> bool:
    """Verifica si ya hay weather para aeropuerto+fecha."""
    client = MongoClient(get_mongo_uri())
    db = client.get_database()
    exists = db["weather"].find_one({
        "airport_code": airport,
        "start_date": date,
    }) is not None
    client.close()
    return exists


def collect_weather(
    airport: str | None = None,
    date_range: tuple[str, str] | None = None,
    days_back: int | None = None,
    dry_run: bool = False,
    delta_root: str = "data/raw",
) -> dict[str, int]:
    """Recolecta datos meteorológicos para aeropuertos.

    Args:
        airport: Aeropuerto específico (None = todos).
        date_range: Tupla (start, end) en formato ISO.
        days_back: Limitar la extracción a los últimos N días desde la fecha final.
        dry_run: Solo mostrar lo que haría.
        delta_root: Ruta base Delta.

    Returns:
        Stats de la colección.
    """
    adapter = OpenMeteoAdapter()
    ranges = _get_airport_date_ranges(airport)
    logger.info("Aeropuertos con datos: %d", len(ranges))

    if date_range:
        # Solapar con el rango especificado
        filter_start, filter_end = date_range
        ranges = [
            (icao, max(s, filter_start), min(e, filter_end))
            for icao, s, e in ranges
            if s <= filter_end and e >= filter_start
        ]

    if days_back is not None and days_back > 0:
        clipped_ranges: list[tuple[str, str, str]] = []
        for icao, start, end in ranges:
            start_dt = date.fromisoformat(start)
            end_dt = date.fromisoformat(end)
            clipped_start = max(start_dt, end_dt - timedelta(days=days_back - 1)).isoformat()
            clipped_ranges.append((icao, clipped_start, end_dt.isoformat()))
        ranges = clipped_ranges

    total = 0
    weather_written = 0
    skipped = 0
    errors = 0

    for icao, start, end in ranges:
        if dry_run:
            logger.info("  %s: consultaría %s → %s", icao, start, end)
            total += 1
            continue

        # Saltar si ya tenemos datos
        if _has_weather(icao, start):
            logger.info("  %s %s: ya existe (skip)", icao, start)
            skipped += 1
            total += 1
            continue

        try:
            data = adapter.get_weather_batch(icao, start, end)
            if data is None or not data.get("hourly", {}).get("time"):
                logger.info("  %s: sin datos meteorológicos", icao)
                errors += 1
                total += 1
                continue

            # Bronze: guardar el payload crudo tal cual llegó de la API
            write_raw_json(
                "weather_openmeteo",
                "/v1/archive",
                {"latitude": data["latitude"], "longitude": data["longitude"]},
                data.get("raw", data),
                delta_root,
            )

            logger.info("  %s %s→%s: datos guardados en Bronze", icao, start, end)
            weather_written += 1
        except Exception as e:
            logger.warning("  %s: error: %s", icao, e)
            errors += 1

        total += 1

    return {
        "total": total,
        "weather_written": weather_written,
        "skipped": skipped,
        "errors": errors,
    }


def _safe(arr: list[Any], idx: int) -> Any:
    """Acceso seguro a lista por índice."""
    try:
        return arr[idx]
    except (IndexError, TypeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Colección de datos meteorológicos")
    parser.add_argument("--airport", default=None, help="Código ICAO específico")
    parser.add_argument("--date-range", nargs=2, default=None,
                        help="Rango de fechas YYYY-MM-DD YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=None,
                        help="Limitar la extracción a los últimos N días desde la fecha final")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = collect_weather(
        airport=args.airport,
        date_range=tuple(args.date_range) if args.date_range else None,
        days_back=args.days,
        dry_run=args.dry_run,
        delta_root=get_delta_root(),
    )

    logger.info("--- Resultados ---")
    logger.info(
        "Total: %d | Weather escrito: %d | Saltados: %d | Errores: %d",
        stats["total"], stats["weather_written"], stats["skipped"], stats["errors"],
    )


if __name__ == "__main__":
    main()
