#!/usr/bin/env python3
"""Script 2/3: Bronze (Delta Lake) → Silver (MongoDB).

Lee los JSON crudos de la capa Bronze, parsea los vuelos y los inserta
en MongoDB (colección ``flights``).

Uso:
    python scripts/bronze_to_silver.py [--date YYYY-MM-DD] [--dry-run]

Flujo:
    Lee bronze/opensky DeltaTable → parse_flight_list() → write_flights_silver()
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime, date as date_type
from typing import Any

from aeropredict.opensky.checkpoint_mongo import (
    add_to_checkpoint_set,
    get_checkpoint_set,
)
from aeropredict.opensky.config import get_delta_root, get_storage_options
from aeropredict.opensky.extract_flights import parse_flight_list
from aeropredict.opensky.logging_config import setup_daily_logger
from aeropredict.opensky.models import Flight
from aeropredict.opensky.storage_silver import write_flights_silver, close as close_silver

CHECKPOINT_COLLECTION = "bronze_to_silver"
logger = logging.getLogger("bronze_to_silver")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Script 2/3: Procesa Bronze (Delta Lake) → Silver (MongoDB)",
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
    """Obtiene todas las fechas con datos en bronze/opensky.

    La tabla está particionada por ``ingestion_date`` (date32).
    """
    from deltalake import DeltaTable

    try:
        table_uri = f"{delta_root}/bronze/opensky"
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

    table_uri = f"{delta_root}/bronze/opensky"
    logger.info("Leyendo Bronze: %s", table_uri)

    dt = DeltaTable(table_uri, storage_options=get_storage_options())

    # Filtrar por fecha si se especifica
    if target_date:
        import pyarrow.compute as pc
        import pyarrow as pa

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

    for i in range(table.num_rows):
        row = table.slice(i, 1).to_pydict()
        response_str = row["response"][0]
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


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)
    delta_root = args.delta_root or get_delta_root()

    logger.info("=" * 60)
    logger.info("Script 2/3: Bronze → Silver")
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

    # Leer y parsear
    flights = _read_bronze_flights(delta_root, target_date, dry_run=args.dry_run)

    if args.dry_run:
        logger.info(
            "DRY RUN: %d vuelos listos para Silver (MongoDB)",
            len(flights),
        )
        return 0

    if not flights:
        logger.info("No hay vuelos nuevos para insertar en Silver")
        return 0

    # Escribir a Silver (MongoDB)
    try:
        n = write_flights_silver(flights)
        logger.info("Silver (MongoDB): %d vuelos insertados", n)
        add_to_checkpoint_set(CHECKPOINT_COLLECTION, str(target_date))
    except Exception as e:
        logger.error("Error escribiendo a Silver: %s", e)
        close_silver()
        return 1
    finally:
        close_silver()

    logger.info("=" * 60)
    logger.info("BRONZE→SILVER COMPLETADO: %d vuelos", len(flights))
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
