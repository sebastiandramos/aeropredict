"""Módulo de extracción de datos de OpenSky Network.

Submódulos:
  - auth:       TokenManager OAuth2
  - client:     Cliente HTTP base
  - config:     Configuración (bbox, aeropuertos, env vars)
  - models:     Dataclasses (StateVector, Flight, Track)
  - extract_flights:  Extracción de vuelos
  - extract_states:   Extracción de state vectors
  - extract_tracks:   Extracción de trayectorias
  - storage:    Escritura a Delta Lake
  - cli:        CLI con Click
"""

from .auth import TokenManager
from .client import OpenSkyClient
from .config import (
    AEROPUERTOS,
    BBOX_ESPANA,
    BBOX_EUROPA_OESTE,
    BoundingBox,
    get_airport_icao_codes,
    get_bbox,
    get_client_id,
    get_client_secret,
    get_delta_root,
)
from .extract_flights import (
    fetch_arrivals,
    fetch_arrivals_raw,
    fetch_departures,
    fetch_departures_raw,
    fetch_flights_by_aircraft,
    fetch_flights_by_aircraft_raw,
    fetch_flights_in_interval,
    fetch_flights_in_interval_raw,
    parse_flight_list,
)
from .extract_states import fetch_states, fetch_states_raw, parse_states_response
from .extract_tracks import fetch_track, fetch_track_raw, parse_track_response
from .models import Flight, StateVector, Track, TrackWaypoint
from .storage import (
    write_flights_silver,
    write_raw,
    write_state_vectors_silver,
    write_tracks_silver,
)

__all__ = [
    "AEROPUERTOS",
    "BBOX_ESPANA",
    "BBOX_EUROPA_OESTE",
    "BoundingBox",
    "Flight",
    "OpenSkyClient",
    "StateVector",
    "TokenManager",
    "Track",
    "TrackWaypoint",
    "fetch_arrivals",
    "fetch_arrivals_raw",
    "fetch_departures",
    "fetch_departures_raw",
    "fetch_flights_by_aircraft",
    "fetch_flights_by_aircraft_raw",
    "fetch_flights_in_interval",
    "fetch_flights_in_interval_raw",
    "fetch_states",
    "fetch_states_raw",
    "fetch_track",
    "fetch_track_raw",
    "get_airport_icao_codes",
    "get_bbox",
    "get_client_id",
    "get_client_secret",
    "get_delta_root",
    "parse_flight_list",
    "parse_states_response",
    "parse_track_response",
    "write_flights_silver",
    "write_raw",
    "write_state_vectors_silver",
    "write_tracks_silver",
]
