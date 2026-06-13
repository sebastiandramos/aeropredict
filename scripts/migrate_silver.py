#!/usr/bin/env python3
"""Migración de la capa silver de Delta Lake a MongoDB + PostgreSQL.

Lee los datos existentes de ``data/raw/silver/flights/`` en Delta Lake,
los transforma a objetos Flight y los escribe en MongoDB (silver)
y PostgreSQL (gold).

Uso:
    python scripts/migrate_silver.py [--delta-root DATA_RAW]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# Añadir src/ al path para imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aeropredict.opensky.models import Flight
from aeropredict.opensky.storage_gold import write_flights_gold
from aeropredict.opensky.storage_silver import close as close_mongo
from aeropredict.opensky.storage_silver import write_flights_silver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("migrate_silver")


def _parse_ts(val: object) -> datetime | None:
    """Convierte un valor PyArrow/pylist a datetime UTC."""
    if val is None:
        return None
    if isinstance(val, datetime):
        # PyArrow devuelve datetime nativo; aseguramos UTC
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC)
        return val
    if hasattr(val, "to_pydatetime"):  # PyArrow scalar
        return val.to_pydatetime().replace(tzinfo=UTC)
    return None


def _to_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val: object) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def delta_row_to_flight(row: dict[str, object]) -> Flight | None:
    """Convierte una fila de Delta Lake (pylist) a Flight.

    Los nombres de columna de Delta usan snake_case sin prefijo ``est_``,
    mientras que Flight usa ``est_departure_airport_horiz_distance``, etc.
    """
    first_seen = _parse_ts(row.get("first_seen"))
    last_seen = _parse_ts(row.get("last_seen"))

    if first_seen is None or last_seen is None:
        return None

    return Flight(
        icao24=str(row.get("icao24", "")),
        callsign=str(row.get("callsign", "")) or None,
        first_seen=first_seen,
        last_seen=last_seen,
        est_departure_airport=(
            str(row["est_departure_airport"]) if row.get("est_departure_airport") else None
        ),
        est_arrival_airport=(
            str(row["est_arrival_airport"]) if row.get("est_arrival_airport") else None
        ),
        est_departure_airport_horiz_distance=_to_float(row.get("departure_airport_horiz_distance")),
        est_departure_airport_vert_distance=_to_float(row.get("departure_airport_vert_distance")),
        est_arrival_airport_horiz_distance=_to_float(row.get("arrival_airport_horiz_distance")),
        est_arrival_airport_vert_distance=_to_float(row.get("arrival_airport_vert_distance")),
        departure_airport_candidates_count=_to_int(row.get("departure_airport_candidates_count")),
        arrival_airport_candidates_count=_to_int(row.get("arrival_airport_candidates_count")),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrar silver de Delta Lake a MongoDB + PostgreSQL"
    )
    parser.add_argument(
        "--delta-root",
        default="data/raw",
        help="Ruta base de Delta Lake (default: data/raw)",
    )
    args = parser.parse_args()

    delta_root = Path(args.delta_root)
    silver_path = delta_root / "silver" / "flights"

    if not silver_path.exists():
        logger.error("No existe la tabla Delta en %s", silver_path)
        sys.exit(1)

    from deltalake import DeltaTable

    logger.info("Leyendo datos desde %s ...", silver_path)

    dt = DeltaTable(str(silver_path))
    table = dt.to_pyarrow_table()
    rows = table.to_pylist()

    logger.info("Total filas en Delta: %d", len(rows))

    flights: list[Flight] = []
    for row in rows:
        flight = delta_row_to_flight(row)
        if flight is not None:
            flights.append(flight)

    logger.info("Total vuelos leídos: %d", len(flights))

    if not flights:
        logger.info("No hay vuelos para migrar.")
        return

    # Escribir en MongoDB (silver)
    logger.info("Escribiendo en MongoDB (silver)...")
    n_silver = write_flights_silver(flights)
    logger.info("Silver: %d documentos insertados en MongoDB", n_silver)

    # Escribir en PostgreSQL (gold)
    logger.info("Escribiendo en PostgreSQL (gold)...")
    counts = write_flights_gold(flights)
    logger.info("Gold: %s", counts)

    close_mongo()
    logger.info("Migración completada.")


if __name__ == "__main__":
    main()
