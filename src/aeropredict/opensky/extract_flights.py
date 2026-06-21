"""Extracción de vuelos desde la API REST de OpenSky.

Endpoints:
  - /flights/all         Vuelos en un intervalo de tiempo (máx 2h)
  - /flights/aircraft    Vuelos de una aeronave (máx 2 días)
  - /flights/arrival     Llegadas a un aeropuerto (máx 2 días)
  - /flights/departure   Salidas desde un aeropuerto (máx 2 días)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .client import OpenSkyClient
from .models import Flight

logger = logging.getLogger(__name__)


# ===================================================================
# Funciones raw: devuelven (endpoint, params, respuesta_json_cruda)
# ===================================================================


def fetch_flights_in_interval_raw(
    client: OpenSkyClient,
    begin: datetime,
    end: datetime,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Vuelos en un intervalo de tiempo (máx 2 horas) - JSON crudo."""
    endpoint = "/flights/all"
    params: dict[str, Any] = {
        "begin": str(int(begin.timestamp())),
        "end": str(int(end.timestamp())),
    }
    return endpoint, params, client.get(endpoint, params=params)


def fetch_flights_by_aircraft_raw(
    client: OpenSkyClient,
    icao24: str,
    begin: datetime,
    end: datetime,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Vuelos de una aeronave - JSON crudo."""
    endpoint = "/flights/aircraft"
    params: dict[str, Any] = {
        "icao24": icao24.lower(),
        "begin": str(int(begin.timestamp())),
        "end": str(int(end.timestamp())),
    }
    return endpoint, params, client.get(endpoint, params=params)


def fetch_arrivals_raw(
    client: OpenSkyClient,
    airport: str,
    begin: datetime,
    end: datetime,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Llegadas a un aeropuerto - JSON crudo."""
    endpoint = "/flights/arrival"
    params: dict[str, Any] = {
        "airport": airport.upper(),
        "begin": str(int(begin.timestamp())),
        "end": str(int(end.timestamp())),
    }
    return endpoint, params, client.get(endpoint, params=params)


def fetch_departures_raw(
    client: OpenSkyClient,
    airport: str,
    begin: datetime,
    end: datetime,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Salidas desde un aeropuerto - JSON crudo."""
    endpoint = "/flights/departure"
    params: dict[str, Any] = {
        "airport": airport.upper(),
        "begin": str(int(begin.timestamp())),
        "end": str(int(end.timestamp())),
    }
    return endpoint, params, client.get(endpoint, params=params)


# ===================================================================
# Parseo: JSON crudo -> objetos de dominio
# ===================================================================


def parse_flight_list(data: Any) -> list[Flight]:
    """Convierte la respuesta JSON cruda (lista) en objetos Flight."""
    if not isinstance(data, list):
        logger.warning("Respuesta inesperada (no es lista): %s", type(data))
        return []
    return [Flight.from_dict(item) for item in data]


# ===================================================================
# Funciones de conveniencia (un solo paso)
# ===================================================================


def fetch_flights_in_interval(
    client: OpenSkyClient,
    begin: datetime,
    end: datetime,
) -> list[Flight]:
    _, _, data = fetch_flights_in_interval_raw(client, begin, end)
    return parse_flight_list(data)


def fetch_flights_by_aircraft(
    client: OpenSkyClient,
    icao24: str,
    begin: datetime,
    end: datetime,
) -> list[Flight]:
    _, _, data = fetch_flights_by_aircraft_raw(client, icao24, begin, end)
    return parse_flight_list(data)


def fetch_arrivals(
    client: OpenSkyClient,
    airport: str,
    begin: datetime,
    end: datetime,
) -> list[Flight]:
    _, _, data = fetch_arrivals_raw(client, airport, begin, end)
    return parse_flight_list(data)


def fetch_departures(
    client: OpenSkyClient,
    airport: str,
    begin: datetime,
    end: datetime,
) -> list[Flight]:
    _, _, data = fetch_departures_raw(client, airport, begin, end)
    return parse_flight_list(data)
