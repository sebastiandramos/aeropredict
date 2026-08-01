#!/usr/bin/env python3
"""Script 2/5: promote datos de Bronze a Silver.

Este script procesa los registros crudos almacenados en Bronze (Delta Lake) y
los convierte en documentos listos para MongoDB. Actualmente maneja tres tipos
de ingestión:

- vuelos OpenSky (`bronze/opensky`) → `flights` en MongoDB
- datos meteorológicos Open-Meteo (`bronze/weather_openmeteo`) → `weather` en MongoDB
- datos AENA Infovuelos (`bronze/aena_infovuelos`) → `aena_infovuelos` en MongoDB

AENA se promociona por hora UTC con checkpoints independientes de OpenSky
(colección ``bronze_to_silver_aena``): cada hora pendiente se procesa y solo se
marca como completada si toda su escritura termina correctamente.

Uso:
    python scripts/bronze_to_silver.py [--date YYYY-MM-DD] [--delta-root PATH]
                                         [--aena-window-days N] [--dry-run]

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
from datetime import UTC, datetime, timedelta
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
    write_aena_infovuelos,
    write_flights_silver,
    write_weather,
)
from aeropredict.sources.aena_infovuelos import AenaInfovuelosAdapter
from aeropredict.sources.airport_codes import get_icao_for_iata

CHECKPOINT_COLLECTION = "bronze_to_silver"
CHECKPOINT_COLLECTION_AENA = "bronze_to_silver_aena"
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
    parser.add_argument(
        "--aena-window-days", type=int, default=3,
        help="Ventana en días para descubrir horas AENA pendientes (default: 3)",
    )
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


def _get_aena_bronze_hours(
    delta_root: str,
    window_days: int = 3,
    date_override: date_type | str | None = None,
) -> list[datetime]:
    """Descubre las horas UTC disponibles en Bronze AENA infovuelos.

    Lee la tabla no particionada ``bronze/aena_infovuelos`` y agrupa las filas
    por ``floor(fetched_at, unit="hour")``. Si ``date_override`` se indica,
    devuelve las horas de esa fecha concreta (backfill); en caso contrario,
    las horas dentro de la ventana ``[now - window_days, now]``. Devuelve las
    horas ordenadas de forma ascendente.
    """
    if isinstance(date_override, str):
        override = date_type.fromisoformat(date_override)
    else:
        override = date_override

    from deltalake import DeltaTable

    table_uri = _build_table_uri(delta_root, "bronze", "aena_infovuelos")
    logger.info("Leyendo Bronze AENA infovuelos: %s", table_uri)

    try:
        dt = DeltaTable(table_uri, storage_options=get_storage_options())
    except Exception as exc:
        logger.warning("No se pudo leer bronze/aena_infovuelos: %s", exc)
        return []

    table = dt.to_pyarrow_table()
    if table.num_rows == 0:
        return []

    import pyarrow.compute as pc

    hours = pc.unique(
        pc.floor_temporal(table.column("fetched_at"), unit="hour"),
    )
    hour_list = sorted(hours.to_pylist())

    if override is not None:
        return [h for h in hour_list if h.date() == override]

    now = datetime.now(UTC)
    window_start = now - timedelta(days=window_days)
    return [h for h in hour_list if window_start <= h <= now]


def _read_bronze_aena_infovuelos(
    delta_root: str,
    hour: datetime,
) -> list[dict[str, Any]]:
    """Lee la tabla Bronze de AENA Infovuelos y devuelve documentos normalizados.

    Cada fila de Bronze contiene ``params`` (JSON con airport y flightType)
    y ``response`` (JSON con la lista de vuelos crudos de AENA). Esta función
    deserializa ambos campos, normaliza cada vuelo con ``AenaInfovuelosAdapter``
    y enriquece con el código ICAO del aeropuerto. Solo se procesan las filas
    cuya ``fetched_at`` cae en la hora UTC indicada.
    """
    from deltalake import DeltaTable

    table_uri = _build_table_uri(delta_root, "bronze", "aena_infovuelos")
    logger.info("Leyendo Bronze AENA infovuelos: %s", table_uri)

    try:
        dt = DeltaTable(table_uri, storage_options=get_storage_options())
    except Exception as exc:
        logger.warning("No se pudo leer bronze/aena_infovuelos: %s", exc)
        return []

    table = dt.to_pyarrow_table()
    import pyarrow as pa
    import pyarrow.compute as pc

    hour_scalar = pa.scalar(hour, type=table.schema.field("fetched_at").type)
    mask = pc.equal(
        pc.floor_temporal(table.column("fetched_at"), unit="hour"),
        hour_scalar,
    )
    table = table.filter(mask)

    rows = table.to_pylist()
    aena_docs: list[dict[str, Any]] = []
    parse_errors = 0

    for row in rows:
        raw_params = row.get("params")
        raw_response = row.get("response")
        snapshot_at = row.get("fetched_at")
        if not raw_params or not raw_response:
            continue
        try:
            params = json.loads(raw_params)
            flights_list = json.loads(raw_response)
        except (TypeError, json.JSONDecodeError):
            parse_errors += 1
            continue

        airport_iata = params.get("airport", "")
        flight_type_code = params.get("flightType", "")

        if not isinstance(flights_list, list):
            continue

        for raw_flight in flights_list:
            try:
                doc = AenaInfovuelosAdapter.normalize_flight(
                    raw_flight, airport_iata, flight_type_code, snapshot_at,
                )
            except (KeyError, TypeError, ValueError):
                parse_errors += 1
                continue
            doc["icao24_airport"] = get_icao_for_iata(
                doc.get("aena_airport_iata", ""),
            )
            aena_docs.append(doc)

    if parse_errors:
        logger.warning("AENA infovuelos: %d errores de parseo", parse_errors)
    logger.info(
        "Bronze AENA: %d filas → %d docs infovuelos",
        len(rows), len(aena_docs),
    )
    return aena_docs


def _process_aena_hours(
    delta_root: str,
    pending_hours: list[datetime],
    dry_run: bool,
) -> int:
    """Procesa las horas AENA pendientes (de más antigua a más reciente).

    Cada hora se lee de Bronze y se escribe a Silver (MongoDB) de forma
    atómica: el checkpoint por hora solo se marca tras una escritura
    correcta. Si una hora falla, no se checkpointea y se reintenta en la
    siguiente ejecución. Devuelve el número de horas fallidas.
    """
    failures = 0
    for hour in pending_hours:
        hour_key = hour.strftime("%Y-%m-%dT%H:00")
        try:
            docs = _read_bronze_aena_infovuelos(delta_root, hour)
            if dry_run:
                logger.info("hour=%s rows=%d (dry-run)", hour_key, len(docs))
                continue
            write_aena_infovuelos(docs)
            add_to_checkpoint_set(CHECKPOINT_COLLECTION_AENA, hour_key)
        except Exception as exc:
            failures += 1
            logger.error("Error procesando hora AENA %s: %s", hour_key, exc)
    return failures


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)
    delta_root = args.delta_root or get_delta_root()

    logger.info("=" * 60)
    logger.info("Script 2/5: Bronze → Silver")
    logger.info("Delta root: %s", delta_root)
    logger.info("=" * 60)

    exit_code = 0

    # Determinar fechas a procesar (OpenSky)
    target_date: date_type | None = None
    if args.date:
        target_date = date_type.fromisoformat(args.date)
        logger.info("Fecha específica: %s", target_date)
    else:
        dates = _get_bronze_dates(delta_root)
        if not dates:
            logger.warning("No hay datos en Bronze para procesar")
        else:
            logger.info("Fechas disponibles en Bronze: %s", dates)
            # Si no se especifica fecha, procesar la más reciente
            target_date = dates[-1]
            logger.info("Procesando la más reciente: %s", target_date)

    # Ruta OpenSky + weather (sin cambios de comportamiento)
    if target_date is not None:
        # Checkpoint: saltar si la fecha ya fue procesada a Silver
        processed_dates = get_checkpoint_set(CHECKPOINT_COLLECTION)
        if str(target_date) in processed_dates:
            logger.info("%s ya procesado a Silver (checkpoint), saltando", target_date)
        else:
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
            else:
                # Escribir a Silver (MongoDB)
                try:
                    if flights:
                        n_flights = write_flights_silver(flights)
                        logger.info("Silver (MongoDB): %d vuelos insertados", n_flights)
                    if weather_docs:
                        n_weather = write_weather(weather_docs)
                        logger.info("Silver (MongoDB): %d weather docs insertados", n_weather)
                    add_to_checkpoint_set(CHECKPOINT_COLLECTION, str(target_date))

                    logger.info("=" * 60)
                    logger.info(
                        "BRONZE→SILVER COMPLETADO: %d vuelos y %d weather docs",
                        len(flights), len(weather_docs),
                    )
                    logger.info("=" * 60)
                except Exception as e:
                    logger.error("Error escribiendo a Silver: %s", e)
                    exit_code = 1

    # Ruta AENA: independiente del checkpoint OpenSky, por horas UTC
    pending_hours = _get_aena_bronze_hours(
        delta_root,
        window_days=args.aena_window_days,
        date_override=args.date,
    )
    checkpointed_hours = get_checkpoint_set(CHECKPOINT_COLLECTION_AENA)
    pending = [
        h for h in pending_hours
        if h.strftime("%Y-%m-%dT%H:00") not in checkpointed_hours
    ]
    if not pending:
        logger.info("AENA: 0 pending")
    else:
        logger.info("AENA: %d pending hours", len(pending))
        aena_failures = _process_aena_hours(delta_root, pending, args.dry_run)
        if aena_failures and not args.dry_run:
            exit_code = 1

    close_silver()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
