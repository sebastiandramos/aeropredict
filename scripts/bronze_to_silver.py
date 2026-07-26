#!/usr/bin/env python3
"""Script 2/5: promote datos de Bronze a Silver.

Este script procesa los registros crudos almacenados en Bronze (Delta Lake) y
los convierte en documentos listos para MongoDB. Actualmente maneja dos tipos
de ingestión:

- vuelos OpenSky (`bronze/opensky`) → `flights` en MongoDB
- datos meteorológicos Open-Meteo (`bronze/weather_openmeteo`) → `weather` en MongoDB

Uso:
    python scripts/bronze_to_silver.py [--date YYYY-MM-DD] [--delta-root PATH] [--dry-run]

Flujo:
    1. lee la tabla Delta Bronze
    2. parsea los payloads crudos
    3. escribe los documentos transformados en MongoDB
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date as date_type
from typing import Any

from aeropredict.opensky.checkpoint_mongo import (
    add_to_checkpoint_set,
    get_checkpoint_set,
)
from aeropredict.opensky.config import get_delta_root, get_storage_options
from aeropredict.opensky.extract_flights import parse_flight_list
from aeropredict.opensky.logging_config import setup_daily_logger
from aeropredict.opensky.models import Flight
from aeropredict.opensky.storage import _build_table_uri
from aeropredict.opensky.storage_silver import (
    close as close_silver,
)
from aeropredict.opensky.storage_silver import (
    write_flights_silver,
    write_weather,
)

CHECKPOINT_COLLECTION = "bronze_to_silver"
logger = logging.getLogger("bronze_to_silver")


def _build_weather_docs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Construye documentos meteorológicos para MongoDB a partir de un payload Bronze.

    El payload debe incluir claves de hora en la sección ``hourly`` y un
    ``airport_code`` válido. Cada fila horaria se transforma en un documento
    independiente con los valores meteorológicos correspondientes.
    """
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return []

    airport_code = payload.get("airport_code")
    if not airport_code:
        return []

    docs: list[dict[str, Any]] = []
    for i, timestamp in enumerate(times):
        docs.append({
            "airport_code": airport_code,
            "timestamp": timestamp,
            "flight_date": timestamp[:10],
            "temperature_2m": _safe(hourly.get("temperature_2m", []), i),
            "precipitation": _safe(hourly.get("precipitation", []), i),
            "wind_speed_10m": _safe(hourly.get("wind_speed_10m", []), i),
            "wind_gusts_10m": _safe(hourly.get("wind_gusts_10m", []), i),
            "visibility": _safe(hourly.get("visibility", []), i),
            "cloud_cover": _safe(hourly.get("cloud_cover", []), i),
            "relative_humidity_2m": _safe(hourly.get("relative_humidity_2m", []), i),
        })
    return docs


def _safe(arr: list[Any], idx: int) -> Any:
    """Devuelve el valor en el índice indicado o None si no existe.

    Esto permite procesar arrays horarias incompletos sin lanzar excepciones.
    """
    try:
        return arr[idx]
    except (IndexError, TypeError):
        return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Script 2/5: Procesa Bronze (Delta Lake) → Silver (MongoDB)",
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="Fecha concreta YYYY-MM-DD (default: todas las disponibles en Bronze)",
    )
    parser.add_argument(
        "--delta-root", type=str, default=None,
        help="Override de delta_root (útil para leer desde local: data/raw)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra lo que procesaría")
    return parser.parse_args(argv)


def _get_bronze_dates(delta_root: str) -> list[date_type]:
    """Lista las fechas disponibles en la tabla Bronze de vuelos.

    Lee las particiones de ``bronze/opensky`` y devuelve todas las fechas de
    ``ingestion_date`` encontradas. Si ocurre un error, devuelve una lista vacía.
    """
    from deltalake import DeltaTable

    try:
        table_uri = _build_table_uri(delta_root, "bronze", "opensky")
        dt = DeltaTable(table_uri, storage_options=get_storage_options())
        partitions = dt.partitions()
        # Cada partición: {ingestion_date: YYYY-MM-DD}
        seen: set[date_type] = set()
        for p in partitions:
            raw = p.get("ingestion_date")
            if raw:
                try:
                    d = date_type.fromisoformat(str(raw))
                    seen.add(d)
                except (ValueError, TypeError):
                    continue
        return sorted(seen)
    except Exception as exc:
        logger.warning("No se pudo leer bronze/opensky: %s", exc)
        return []


def _read_bronze_flights(
    delta_root: str,
    target_date: date_type | None = None,
    dry_run: bool = False,
) -> list[Flight]:
    """Lee y parsea vuelos desde Bronze.

    Args:
        delta_root: Ruta base Delta.
        target_date: Si se especifica, filtra por ingestion_date.
        dry_run: Si es True, solo cuenta.

    Returns:
        Lista de Flight objects deduplicados.
    """
    from deltalake import DeltaTable

    table_uri = _build_table_uri(delta_root, "bronze", "opensky")
    logger.info("Leyendo Bronze: %s", table_uri)

    dt = DeltaTable(table_uri, storage_options=get_storage_options())

    # Filtrar por fecha si se especifica
    if target_date:
        import pyarrow as pa
        import pyarrow.compute as pc

        table = dt.to_pyarrow_table()
        date_scalar = pa.scalar(target_date, type=pa.date32())
        mask = pc.equal(table.column("ingestion_date"), date_scalar)
        table = table.filter(mask)
        logger.info("Filtrado por ingestion_date=%s → %d filas", target_date, table.num_rows)
    else:
        table = dt.to_pyarrow_table()
        logger.info("Total filas en bronze/opensky: %d", table.num_rows)

    all_flights: list[Flight] = []
    parse_errors = 0
    start = time.time()

    # Vectorized: convert entire table to dict of lists once (O(n) instead of O(n²))
    columns = table.to_pydict()
    responses = columns["response"]
    num_rows = len(responses)

    for i in range(num_rows):
        response_str = responses[i]
        if not response_str:
            continue

        try:
            data = json.loads(response_str)
            flights = parse_flight_list(data)
            all_flights.extend(flights)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            parse_errors += 1
            if parse_errors <= 3:
                logger.warning("Error parseando fila %d: %s", i, e)

    # Dedup por (icao24, first_seen, callsign)
    seen: set[tuple[str, int | None, str | None]] = set()
    deduped: list[Flight] = []
    for f in all_flights:
        key = (f.icao24, int(f.first_seen.timestamp()) if f.first_seen else None, f.callsign)
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    elapsed = time.time() - start
    logger.info(
        "Bronze: %d filas → %d vuelos (%d únicos, %d errores parseo) | %.1fs",
        table.num_rows, len(all_flights), len(deduped), parse_errors, elapsed,
    )
    return deduped


def _read_bronze_weather(
    delta_root: str, target_date: date_type | None = None
) -> list[dict[str, Any]]:
    """Lee la tabla Bronze de weather y devuelve documentos para MongoDB.

    Cada fila de Bronze contiene un payload crudo de Open-Meteo. Esta función
    deserializa el JSON, extrae la sección ``hourly`` y construye los documentos
    que se insertarán en la colección ``weather``.
    """
    from deltalake import DeltaTable

    table_uri = _build_table_uri(delta_root, "bronze", "weather_openmeteo")
    logger.info("Leyendo Bronze weather: %s", table_uri)

    try:
        dt = DeltaTable(table_uri, storage_options=get_storage_options())
    except Exception as exc:
        logger.warning("No se pudo leer bronze/weather_openmeteo: %s", exc)
        return []

    table = dt.to_pyarrow_table()
    if target_date:
        import pyarrow as pa
        import pyarrow.compute as pc

        # weather_openmeteo has `fetched_at` (timestamp), not `ingestion_date`
        fetched_dates = pc.cast(
            pc.floor_temporal(table.column("fetched_at"), unit="day"),
            pa.date32(),
        )
        mask = pc.equal(fetched_dates, pa.scalar(target_date, type=pa.date32()))
        table = table.filter(mask)

    rows = table.to_pylist()
    weather_docs: list[dict[str, Any]] = []
    for row in rows:
        response = row.get("response")
        if not response:
            continue
        try:
            payload = json.loads(response)
        except (TypeError, json.JSONDecodeError):
            continue
        weather_docs.extend(_build_weather_docs(payload))

    logger.info("Bronze weather: %d filas → %d docs weather", len(rows), len(weather_docs))
    return weather_docs


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)
    delta_root = args.delta_root or get_delta_root()

    logger.info("=" * 60)
    logger.info("Script 2/5: Bronze → Silver")
    logger.info("Delta root: %s", delta_root)
    logger.info("=" * 60)

    # Determinar fechas a procesar
    target_date: date_type | None = None
    if args.date:
        target_date = date_type.fromisoformat(args.date)
        logger.info("Fecha específica: %s", target_date)
    else:
        dates = _get_bronze_dates(delta_root)
        if not dates:
            logger.warning("No hay datos en Bronze para procesar")
            return 0
        logger.info("Fechas disponibles en Bronze: %s", dates)
        # Si no se especifica fecha, procesar la más reciente
        target_date = dates[-1]
        logger.info("Procesando la más reciente: %s", target_date)

    # Checkpoint: saltar si la fecha ya fue procesada a Silver
    processed_dates = get_checkpoint_set(CHECKPOINT_COLLECTION)
    if str(target_date) in processed_dates:
        logger.info("%s ya procesado a Silver (checkpoint), saltando", target_date)
        return 0

    # Leer y parsear vuelos
    flights = _read_bronze_flights(delta_root, target_date, dry_run=args.dry_run)

    # Leer y parsear weather
    weather_docs = _read_bronze_weather(delta_root, target_date)

    if args.dry_run:
        logger.info(
            "DRY RUN: %d vuelos y %d weather docs listos para Silver (MongoDB)",
            len(flights),
            len(weather_docs),
        )
        return 0

    # Escribir a Silver (MongoDB)
    try:
        if flights:
            n_flights = write_flights_silver(flights)
            logger.info("Silver (MongoDB): %d vuelos insertados", n_flights)
        if weather_docs:
            n_weather = write_weather(weather_docs)
            logger.info("Silver (MongoDB): %d weather docs insertados", n_weather)
        add_to_checkpoint_set(CHECKPOINT_COLLECTION, str(target_date))
    except Exception as e:
        logger.error("Error escribiendo a Silver: %s", e)
        close_silver()
        return 1
    finally:
        close_silver()

    logger.info("=" * 60)
    logger.info(
        "BRONZE→SILVER COMPLETADO: %d vuelos, %d weather docs",
        len(flights), len(weather_docs),
    )
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
