"""Extracción de trayectorias (tracks) desde la API REST de OpenSky.

Endpoint:
  - /tracks/all?icao24=...&time=...   Trayectoria de una aeronave
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .client import OpenSkyClient
from .models import Track

logger = logging.getLogger(__name__)


def fetch_track_raw(
    client: OpenSkyClient,
    icao24: str,
    time: datetime | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Trayectoria de una aeronave - JSON crudo.

    Returns:
        (endpoint, params, respuesta_json_cruda | None si 404)
    """
    endpoint = "/tracks/all"
    params: dict[str, Any] = {
        "icao24": icao24.lower(),
        "time": str(int(time.timestamp())) if time else "0",
    }
    try:
        data = client.get(endpoint, params=params)
    except Exception as e:
        if "404" in str(e):
            logger.info("Track no encontrado para icao24=%s", icao24)
            return endpoint, params, None
        raise
    return endpoint, params, data


def parse_track_response(data: dict[str, Any] | None) -> Track | None:
    """Convierte la respuesta JSON cruda en objeto Track."""
    if data is None or "path" not in data:
        return None
    return Track.from_dict(data)


def fetch_track(
    client: OpenSkyClient,
    icao24: str,
    time: datetime | None = None,
) -> Track | None:
    """Trayectoria de una aeronave (función conveniencia)."""
    _, _, data = fetch_track_raw(client, icao24, time)
    return parse_track_response(data)
