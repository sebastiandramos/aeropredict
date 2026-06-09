"""Capas de almacenamiento Delta Lake.

Arquitectura medallion (bronce → plata → oro):

  Bronze (raw):        Respuesta JSON cruda de la API, exactamente como llega,
                       más metadatos de ingestión (endpoint, fetched_at, params).
                       Particionado por endpoint + fecha.

  Silver (cleaned):    Datos parseados en columnas tipadas, limpias y listas
                       para análisis.

  Gold (features):     Feature engineering para ML (a implementar).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from .models import Flight, StateVector, Track

logger = logging.getLogger(__name__)

try:
    from deltalake import write_deltalake
except ImportError as exc:
    msg = "deltalake no está instalado. Ejecuta: pip install deltalake"
    raise ImportError(msg) from exc

# ===================================================================
# BRONZE - Raw JSON (datos exactos de la API + metadatos)
# ===================================================================

RAW_SCHEMA = pa.schema([
    pa.field("endpoint", pa.string()),
    pa.field("params", pa.string()),
    pa.field("fetched_at", pa.timestamp("us", tz="UTC")),
    pa.field("response", pa.string()),
    pa.field("ingestion_date", pa.date32()),
])


def write_raw(
    endpoint: str,
    params: dict[str, Any] | None,
    response_data: dict[str, Any] | list[dict[str, Any]],
    base_path: str,
) -> int:
    """Escribe la respuesta JSON cruda de la API en la capa bronze.

    Tabla: {base_path}/bronze/opensky/
    Particionado por: ingestion_date

    Args:
        endpoint: Ruta del endpoint (ej. '/states/all').
        params: Parámetros de la petición.
        response_data: Respuesta JSON tal cual de la API.
        base_path: Ruta base de datos.

    Returns:
        Número de filas escritas (1 por petición).
    """
    table_uri = str(Path(base_path) / "bronze" / "opensky")
    now = datetime.now(UTC)
    ingestion_date = now.date()

    row = {
        "endpoint": endpoint,
        "params": json.dumps(params) if params else None,
        "fetched_at": now,
        "response": json.dumps(response_data, ensure_ascii=False),
        "ingestion_date": ingestion_date,
    }

    table = pa.Table.from_pylist([row], schema=RAW_SCHEMA)
    write_deltalake(
        table_uri,
        table,
        partition_by=["ingestion_date"],
        mode="append",
    )
    logger.info("Raw escrita en %s: %s (params=%s)", table_uri, endpoint, params)
    return 1


# ===================================================================
# SILVER - Datos estructurados
# ===================================================================

FLIGHT_SCHEMA = pa.schema([
    pa.field("icao24", pa.string()),
    pa.field("callsign", pa.string()),
    pa.field("first_seen", pa.timestamp("us", tz="UTC")),
    pa.field("last_seen", pa.timestamp("us", tz="UTC")),
    pa.field("est_departure_airport", pa.string()),
    pa.field("est_arrival_airport", pa.string()),
    pa.field("departure_airport_horiz_distance", pa.float32()),
    pa.field("departure_airport_vert_distance", pa.float32()),
    pa.field("arrival_airport_horiz_distance", pa.float32()),
    pa.field("arrival_airport_vert_distance", pa.float32()),
    pa.field("departure_airport_candidates_count", pa.int32()),
    pa.field("arrival_airport_candidates_count", pa.int32()),
    pa.field("flight_date", pa.date32()),
])

STATE_VECTOR_SCHEMA = pa.schema([
    pa.field("icao24", pa.string()),
    pa.field("callsign", pa.string()),
    pa.field("origin_country", pa.string()),
    pa.field("time_position", pa.timestamp("us", tz="UTC")),
    pa.field("last_contact", pa.timestamp("us", tz="UTC")),
    pa.field("longitude", pa.float32()),
    pa.field("latitude", pa.float32()),
    pa.field("baro_altitude", pa.float32()),
    pa.field("on_ground", pa.bool_()),
    pa.field("velocity", pa.float32()),
    pa.field("true_track", pa.float32()),
    pa.field("vertical_rate", pa.float32()),
    pa.field("geo_altitude", pa.float32()),
    pa.field("squawk", pa.string()),
    pa.field("spi", pa.bool_()),
    pa.field("position_source", pa.int32()),
    pa.field("category", pa.int32()),
    pa.field("snapshot_date", pa.date32()),
])

TRACK_SCHEMA = pa.schema([
    pa.field("icao24", pa.string()),
    pa.field("callsign", pa.string()),
    pa.field("start_time", pa.timestamp("us", tz="UTC")),
    pa.field("end_time", pa.timestamp("us", tz="UTC")),
    pa.field("waypoint_time", pa.timestamp("us", tz="UTC")),
    pa.field("latitude", pa.float32()),
    pa.field("longitude", pa.float32()),
    pa.field("baro_altitude", pa.float32()),
    pa.field("true_track", pa.float32()),
    pa.field("on_ground", pa.bool_()),
    pa.field("track_date", pa.date32()),
])


def _write_silver(
    table_uri: str,
    rows: list[dict[str, Any]],
    schema: pa.Schema,
    partition_cols: list[str],
) -> int:
    """Escribe filas estructuradas en la capa silver."""
    if not rows:
        return 0

    table = pa.Table.from_pylist(rows, schema=schema)
    write_deltalake(
        table_uri,
        table,
        partition_by=partition_cols,
        mode="append",
    )
    return len(rows)


def write_flights_silver(flights: list[Flight], base_path: str) -> int:
    """Escribe vuelos estructurados en capa silver.

    Tabla: {base_path}/silver/flights/
    Particionado por: flight_date
    """
    if not flights:
        return 0

    table_uri = str(Path(base_path) / "silver" / "flights")
    rows = []
    for f in flights:
        flight_date = f.first_seen.date() if f.first_seen else None
        rows.append({
            "icao24": f.icao24,
            "callsign": f.callsign,
            "first_seen": f.first_seen,
            "last_seen": f.last_seen,
            "est_departure_airport": f.est_departure_airport,
            "est_arrival_airport": f.est_arrival_airport,
            "departure_airport_horiz_distance": f.est_departure_airport_horiz_distance,
            "departure_airport_vert_distance": f.est_departure_airport_vert_distance,
            "arrival_airport_horiz_distance": f.est_arrival_airport_horiz_distance,
            "arrival_airport_vert_distance": f.est_arrival_airport_vert_distance,
            "departure_airport_candidates_count": f.departure_airport_candidates_count,
            "arrival_airport_candidates_count": f.arrival_airport_candidates_count,
            "flight_date": flight_date,
        })

    n = _write_silver(table_uri, rows, FLIGHT_SCHEMA, ["flight_date"])
    logger.info("Silver: %d vuelos en %s", n, table_uri)
    return n


def write_state_vectors_silver(states: list[StateVector], base_path: str) -> int:
    """Escribe state vectors estructurados en capa silver.

    Tabla: {base_path}/silver/state_vectors/
    Particionado por: snapshot_date
    """
    if not states:
        return 0

    table_uri = str(Path(base_path) / "silver" / "state_vectors")
    now = datetime.now(UTC).date()
    rows = []
    for s in states:
        rows.append({
            "icao24": s.icao24,
            "callsign": s.callsign,
            "origin_country": s.origin_country,
            "time_position": s.time_position,
            "last_contact": s.last_contact,
            "longitude": s.longitude,
            "latitude": s.latitude,
            "baro_altitude": s.baro_altitude,
            "on_ground": s.on_ground,
            "velocity": s.velocity,
            "true_track": s.true_track,
            "vertical_rate": s.vertical_rate,
            "geo_altitude": s.geo_altitude,
            "squawk": s.squawk,
            "spi": s.spi,
            "position_source": s.position_source,
            "category": s.category,
            "snapshot_date": now,
        })

    n = _write_silver(table_uri, rows, STATE_VECTOR_SCHEMA, ["snapshot_date"])
    logger.info("Silver: %d state vectors en %s", n, table_uri)
    return n


def write_tracks_silver(tracks: list[Track], base_path: str) -> int:
    """Escribe tracks estructurados en capa silver.

    Tabla: {base_path}/silver/tracks/
    Particionado por: track_date
    """
    if not tracks:
        return 0

    table_uri = str(Path(base_path) / "silver" / "tracks")
    rows = []
    for t in tracks:
        track_date = t.start_time.date() if t.start_time else None
        for wp in t.path:
            rows.append({
                "icao24": t.icao24,
                "callsign": t.callsign,
                "start_time": t.start_time,
                "end_time": t.end_time,
                "waypoint_time": wp.time,
                "latitude": wp.latitude,
                "longitude": wp.longitude,
                "baro_altitude": wp.baro_altitude,
                "true_track": wp.true_track,
                "on_ground": wp.on_ground,
                "track_date": track_date,
            })

    if not rows:
        logger.warning("Tracks sin waypoints, ignorados")
        return 0

    n = _write_silver(table_uri, rows, TRACK_SCHEMA, ["track_date"])
    logger.info("Silver: %d waypoints en %s", n, table_uri)
    return n
