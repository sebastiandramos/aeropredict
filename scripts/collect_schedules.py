"""Colección de schedules para el sample de vuelos.

Itera el archivo de sample, consulta AviationStack y AeroDataBox
en orden de prioridad, y almacena resultados en Bronze + Silver.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from aeropredict.opensky.config import (
    get_aerodatabox_key,
    get_aviationstack_api_key,
    get_delta_root,
)
from aeropredict.opensky.storage import write_raw_json
from aeropredict.opensky.storage_silver import write_schedules
from aeropredict.sources.aerodatabox import AeroDataBoxAdapter
from aeropredict.sources.aviationstack import AviationStackAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DELAY_BETWEEN_REQUESTS = 1.0  # segundos


def _load_samples(path: str) -> list[dict[str, Any]]:
    with open(path) as f:
        return json.load(f)


def _has_schedule_in_mongo(callsign: str, flight_date: str) -> bool:
    """Verifica si ya existe un schedule en MongoDB para este vuelo."""
    from pymongo import MongoClient

    from aeropredict.opensky.config import get_mongo_uri
    client = MongoClient(get_mongo_uri())
    db = client.get_database()
    exists = db["schedules"].find_one({
        "callsign": callsign,
        "flight_date": flight_date,
    }) is not None
    client.close()
    return exists


def collect_schedules(
    samples: list[dict[str, Any]],
    limit: int | None = None,
    dry_run: bool = False,
    resume: bool = False,
    delta_root: str = "data/raw",
) -> dict[str, int]:
    """Itera el sample y recolecta schedules.

    Args:
        samples: Lista de vuelos sampleados.
        limit: Limitar a N vuelos (para pruebas).
        dry_run: Si True, solo muestra lo que haría.
        resume: Si True, salta vuelos ya con schedule en MongoDB.
        delta_root: Ruta base Delta.

    Returns:
        Dict con stats de la colección.
    """
    has_aviationstack = bool(get_aviationstack_api_key())
    has_aerodatabox = bool(get_aerodatabox_key())

    if not has_aviationstack and not has_aerodatabox:
        logger.warning("Ninguna API de schedules configurada")
        return {"total": 0, "found": 0, "not_found": 0, "errors": 0, "skipped": 0}

    avstack = AviationStackAdapter() if has_aviationstack else None
    adbox = AeroDataBoxAdapter() if has_aerodatabox else None

    total = 0
    found = 0
    not_found = 0
    errors = 0
    skipped = 0

    to_process = samples[:limit] if limit else samples

    for i, flight in enumerate(to_process):
        callsign = flight.get("callsign")
        flight_date = flight.get("flight_date_str") or str(flight.get("flight_date", ""))[:10]
        dep = flight.get("est_departure_airport") or "?"
        arr = flight.get("est_arrival_airport") or "?"
        route = f"{dep}→{arr}"

        if not callsign:
            logger.info("  [%d/%d] %s: sin callsign, saltando", i + 1, len(to_process), route)
            skipped += 1
            continue

        # -- Resume: saltar si ya existe --
        if resume and _has_schedule_in_mongo(callsign, flight_date):
            logger.info(
                "  [%d/%d] %s %s: ya existe (resume)", i + 1, len(to_process), callsign, route,
            )
            skipped += 1
            continue

        if dry_run:
            logger.info(
                "  [%d/%d] %s %s | %s → consultaría APIs", i + 1, len(to_process),
                callsign, route, flight_date,
            )
            continue

        # -- Consultar APIs en orden de prioridad --
        result: dict[str, Any] | None = None
        source_used = ""

        # 1. AviationStack
        if avstack:
            try:
                result = avstack.get_schedule(callsign, flight_date)
                if result:
                    source_used = "aviationstack"
            except Exception as e:
                logger.warning("AviationStack error %s %s: %s", callsign, flight_date, e)
                errors += 1

        # 2. AeroDataBox (fallback)
        if result is None and adbox:
            try:
                result = adbox.get_schedule(callsign, flight_date)
                if result:
                    source_used = "aerodatabox"
            except Exception as e:
                logger.warning("AeroDataBox error %s %s: %s", callsign, flight_date, e)
                errors += 1

        # -- Almacenar --
        if result:
            found += 1
            logger.info(
                "  [%d/%d] %s %s → %s: scheduled=✓", i + 1, len(to_process),
                callsign, route, source_used,
            )

            # Bronze
            write_raw_json(
                f"schedules_{source_used}",
                f"/flights/{callsign}",
                {"flight_date": flight_date},
                result.get("raw", result),
                delta_root,
            )

            # Silver (sin el campo raw para no duplicar)
            silver_doc = {k: v for k, v in result.items() if k != "raw"}
            silver_doc["flight_date"] = flight_date
            write_schedules([silver_doc])
        else:
            not_found += 1
            logger.info(
                "  [%d/%d] %s %s: sin schedule", i + 1, len(to_process), callsign, route,
            )

        total += 1

        if i < len(to_process) - 1:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    return {
        "total": total,
        "found": found,
        "not_found": not_found,
        "errors": errors,
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Colección de schedules")
    parser.add_argument("--samples", default="data/sample_flights.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    samples_path = args.samples
    if not Path(samples_path).exists():
        logger.error("Archivo de sample no encontrado: %s", samples_path)
        logger.info("Ejecuta primero: python scripts/sample_flights.py")
        return

    samples = _load_samples(samples_path)
    logger.info(
        "Cargados %d samples | dry_run=%s resume=%s limit=%s",
        len(samples), args.dry_run, args.resume, args.limit,
    )

    stats = collect_schedules(
        samples,
        limit=args.limit,
        dry_run=args.dry_run,
        resume=args.resume,
        delta_root=get_delta_root(),
    )

    logger.info("--- Resultados ---")
    logger.info(
        "Procesados: %d | Encontrados: %d | No encontrados: %d | Errores: %d | Omitidos: %d",
        stats["total"], stats["found"], stats["not_found"], stats["errors"], stats["skipped"],
    )


if __name__ == "__main__":
    main()
