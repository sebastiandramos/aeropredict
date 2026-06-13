"""Capa silver — MongoDB para datos estructurados.

Reemplaza la escritura en Delta Lake por MongoDB, manteniendo la misma
interfaz que las funciones ``write_*_silver`` existentes en ``storage.py``.

Colecciones:
  - ``flights``:          vuelos parseados (1 doc por vuelo)
  - ``state_vectors``:    snapshots de estado (1 doc por state vector)
  - ``track_waypoints``:  waypoints de trayectorias (1 doc por waypoint)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pymongo
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

from .config import get_mongo_uri
from .models import Flight, StateVector, Track

logger = logging.getLogger(__name__)

# Conexión perezosa (se conecta en el primer uso)
_client: pymongo.MongoClient[Any] | None = None
_indexes_ensure = False
_schedule_indexes_ensure = False
_aircraft_indexes_ensure = False
_weather_indexes_ensure = False


def _connect() -> None:
    """Conecta a MongoDB si no hay conexión activa."""
    global _client
    if _client is None:
        uri = get_mongo_uri()
        logger.info("Conectando a MongoDB: %s", uri)
        _client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        _client.admin.command("ping")


def _get_db() -> pymongo.database.Database[Any]:
    """Devuelve la base de datos, asegurando conexión e índices."""
    global _indexes_ensure
    _connect()
    if not _indexes_ensure:
        _ensure_indexes()
        _indexes_ensure = True
    assert _client is not None
    return _client.get_database()


def _ensure_indexes() -> None:
    """Crea índices para consultas frecuentes si no existen."""
    assert _client is not None
    db = _client.get_database()

    flights = db["flights"]
    flights.create_index("flight_date")
    flights.create_index([
        ("est_departure_airport", pymongo.ASCENDING),
        ("flight_date", pymongo.ASCENDING),
    ])
    flights.create_index([
        ("est_arrival_airport", pymongo.ASCENDING),
        ("flight_date", pymongo.ASCENDING),
    ])

    sv = db["state_vectors"]
    sv.create_index("snapshot_date")
    sv.create_index("icao24")

    tw = db["track_waypoints"]
    tw.create_index([("icao24", pymongo.ASCENDING), ("waypoint_time", pymongo.ASCENDING)])
    tw.create_index("track_date")


def _get_collection(name: str) -> Collection[Any]:
    """Devuelve una colección conectándose si es necesario.

    Los índices se crean una sola vez en el primer acceso.
    """
    global _indexes_ensure
    _connect()
    assert _client is not None
    db = _client.get_database()
    if not _indexes_ensure:
        _ensure_indexes()
        _indexes_ensure = True
    return db[name]


def close() -> None:
    """Cierra la conexión a MongoDB."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ===================================================================
# FLIGHTS
# ===================================================================


def _flight_to_doc(f: Flight) -> dict[str, Any]:
    flight_date = f.first_seen.date() if f.first_seen else None
    return {
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
        "flight_date": datetime(flight_date.year, flight_date.month, flight_date.day, tzinfo=UTC) if flight_date else None,  # noqa: E501
        "ingested_at": datetime.now(UTC),
    }


def write_flights_silver(flights: list[Flight]) -> int:
    """Inserta vuelos en MongoDB (colección ``flights``).

    Args:
        flights: Lista de objetos Flight.

    Returns:
        Número de documentos insertados.
    """
    if not flights:
        return 0

    col = _get_collection("flights")
    docs = [_flight_to_doc(f) for f in flights]

    try:
        result = col.insert_many(docs, ordered=False)
        n = len(result.inserted_ids)
    except BulkWriteError as e:
        # ordered=False inserta los que puede; los duplicados se saltan
        n = len(e.details.get("insertedIds", [])) if e.details else 0

    logger.info("Silver (MongoDB): %d vuelos insertados", n)
    return n


# ===================================================================
# STATE VECTORS
# ===================================================================


def _state_vector_to_doc(
    s: StateVector,
    snapshot_date: datetime.date | None = None,
) -> dict[str, Any]:
    return {
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
        "snapshot_date": snapshot_date or datetime.now(UTC).date(),
        "ingested_at": datetime.now(UTC),
    }


def write_state_vectors_silver(states: list[StateVector]) -> int:
    """Inserta state vectors en MongoDB (colección ``state_vectors``)."""
    if not states:
        return 0

    col = _get_collection("state_vectors")
    docs = [_state_vector_to_doc(s) for s in states]
    now = datetime.now(UTC).date()
    for d in docs:
        d["snapshot_date"] = now

    try:
        result = col.insert_many(docs, ordered=False)
        n = len(result.inserted_ids)
    except BulkWriteError as e:
        n = len(e.details.get("insertedIds", [])) if e.details else 0

    logger.info("Silver (MongoDB): %d state vectors insertados", n)
    return n


# ===================================================================
# TRACKS (waypoints)
# ===================================================================


def _track_waypoint_to_docs(t: Track) -> list[dict[str, Any]]:
    track_date = t.start_time.date() if t.start_time else None
    docs = []
    for wp in t.path:
        docs.append({
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
            "ingested_at": datetime.now(UTC),
        })
    return docs


def write_tracks_silver(tracks: list[Track]) -> int:
    """Inserta waypoints de tracks en MongoDB (colección ``track_waypoints``)."""
    docs: list[dict[str, Any]] = []
    for t in tracks:
        docs.extend(_track_waypoint_to_docs(t))

    if not docs:
        logger.warning("Tracks sin waypoints, ignorados")
        return 0

    col = _get_collection("track_waypoints")
    try:
        result = col.insert_many(docs, ordered=False)
        n = len(result.inserted_ids)
    except BulkWriteError as e:
        n = len(e.details.get("insertedIds", [])) if e.details else 0

    logger.info("Silver (MongoDB): %d waypoints insertados", n)
    return n


# ===================================================================
# SCHEDULES (from AviationStack, AeroDataBox, etc.)
# ===================================================================


def _get_schedule_collection() -> Collection[Any]:
    """Devuelve colección ``schedules`` con índices."""
    global _schedule_indexes_ensure
    _connect()
    assert _client is not None
    db = _client.get_database()
    if not _schedule_indexes_ensure:
        col = db["schedules"]
        col.create_index([
            ("callsign", pymongo.ASCENDING),
            ("flight_date", pymongo.ASCENDING),
        ])
        col.create_index([
            ("source", pymongo.ASCENDING),
            ("fetched_at", pymongo.ASCENDING),
        ])
        _schedule_indexes_ensure = True
    return db["schedules"]


def write_schedules(schedules: list[dict[str, Any]]) -> int:
    """Inserta schedules en MongoDB (colección ``schedules``).

    Args:
        schedules: Lista de dicts normalizados.

    Returns:
        Número de documentos insertados.
    """
    if not schedules:
        return 0
    col = _get_schedule_collection()
    now = datetime.now(UTC)
    for doc in schedules:
        doc.setdefault("ingested_at", now)
    try:
        result = col.insert_many(schedules, ordered=False)
        n = len(result.inserted_ids)
    except BulkWriteError as e:
        n = len(e.details.get("insertedIds", [])) if e.details else 0
    logger.info("Silver (MongoDB): %d schedules insertados", n)
    return n


# ===================================================================
# AIRCRAFT (OpenSky aircraft DB)
# ===================================================================


def _get_aircraft_collection() -> Collection[Any]:
    """Devuelve colección ``aircraft`` con índice único por icao24."""
    global _aircraft_indexes_ensure
    _connect()
    assert _client is not None
    db = _client.get_database()
    if not _aircraft_indexes_ensure:
        col = db["aircraft"]
        col.create_index("icao24", unique=True)
        _aircraft_indexes_ensure = True
    return db["aircraft"]


def write_aircraft(aircraft_list: list[dict[str, Any]]) -> int:
    """Upsert de aeronaves en MongoDB (colección ``aircraft``).

    Cada documento se identifica por ``icao24``. Si ya existe, se actualiza.

    Args:
        aircraft_list: Lista de dicts con al menos campo ``icao24``.

    Returns:
        Número de documentos insertados o actualizados.
    """
    if not aircraft_list:
        return 0
    col = _get_aircraft_collection()
    now = datetime.now(UTC)
    n = 0
    for doc in aircraft_list:
        doc.setdefault("ingested_at", now)
        result = col.replace_one({"icao24": doc["icao24"]}, doc, upsert=True)
        if result.upserted_id is not None or result.modified_count > 0:
            n += 1
    logger.info("Silver (MongoDB): %d aircraft upsertados", n)
    return n


# ===================================================================
# WEATHER (Open-Meteo)
# ===================================================================


def _get_weather_collection() -> Collection[Any]:
    """Devuelve colección ``weather`` con índices."""
    global _weather_indexes_ensure
    _connect()
    assert _client is not None
    db = _client.get_database()
    if not _weather_indexes_ensure:
        col = db["weather"]
        col.create_index([
            ("airport_code", pymongo.ASCENDING),
            ("flight_date", pymongo.ASCENDING),
        ])
        col.create_index("fetched_at")
        _weather_indexes_ensure = True
    return db["weather"]


def write_weather(weather_list: list[dict[str, Any]]) -> int:
    """Inserta datos meteorológicos en MongoDB (colección ``weather``).

    Args:
        weather_list: Lista de dicts con hourly weather data.

    Returns:
        Número de documentos insertados.
    """
    if not weather_list:
        return 0
    col = _get_weather_collection()
    now = datetime.now(UTC)
    for doc in weather_list:
        doc.setdefault("ingested_at", now)
    try:
        result = col.insert_many(weather_list, ordered=False)
        n = len(result.inserted_ids)
    except BulkWriteError as e:
        n = len(e.details.get("insertedIds", [])) if e.details else 0
    logger.info("Silver (MongoDB): %d weather docs insertados", n)
    return n
