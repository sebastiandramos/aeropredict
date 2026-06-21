"""Extracción de state vectors desde la API REST de OpenSky.

Endpoints:
  - /states/all     State vectors actuales (opcionalmente con bbox)
  - /states/own     State vectors de sensores propios (requiere auth)
"""

from __future__ import annotations

import logging
from typing import Any

from .client import OpenSkyClient
from .config import BoundingBox
from .models import StateVector

logger = logging.getLogger(__name__)


def fetch_states_raw(
    client: OpenSkyClient,
    bbox: BoundingBox | None = None,
    icao24_filter: list[str] | None = None,
    own: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Obtiene state vectors de la API y devuelve el JSON crudo.

    Returns:
        (endpoint, params_usados, respuesta_json_cruda)
    """
    params: dict[str, Any] = {}

    if bbox:
        params["lamin"] = bbox.lamin
        params["lamax"] = bbox.lamax
        params["lomin"] = bbox.lomin
        params["lomax"] = bbox.lomax

    if icao24_filter:
        params["icao24"] = icao24_filter if len(icao24_filter) > 1 else icao24_filter[0]

    endpoint = "/states/own" if own else "/states/all"
    data = client.get(endpoint, params=params)
    return endpoint, params, data


def parse_states_response(data: dict[str, Any]) -> list[StateVector]:
    """Convierte la respuesta JSON cruda en objetos StateVector."""
    time_ref: int = data.get("time", 0)
    rows: list[list[Any]] = data.get("states", [])
    return [StateVector.from_row(row, time_ref) for row in rows]


def fetch_states(
    client: OpenSkyClient,
    bbox: BoundingBox | None = None,
    icao24_filter: list[str] | None = None,
    own: bool = False,
) -> list[StateVector]:
    """Obtiene state vectors actuales (función convienencia)."""
    _, _, data = fetch_states_raw(client, bbox, icao24_filter, own)
    return parse_states_response(data)
