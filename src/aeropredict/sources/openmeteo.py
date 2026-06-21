"""Open-Meteo historical weather adapter.

Documentación: https://open-meteo.com/en/docs/historical-weather-api
Gratuito, 10 000 requests/día, no requiere API key.
Datos desde 1940.
"""

from __future__ import annotations

import logging
from typing import Any

from aeropredict.sources.airport_coords import get_airport_coords
from aeropredict.sources.base import BaseAdapter

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Variables horarias que nos interesan para delay prediction
HOURLY_VARS = [
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "visibility",
    "cloud_cover",
    "relative_humidity_2m",
]


class OpenMeteoAdapter(BaseAdapter):
    """Adaptador para el histórico meteorológico de Open-Meteo.

    No requiere API key ni Pool (tier gratuito de 10k req/día).
    """

    def get_weather(self, icao: str, date: str) -> dict[str, Any] | None:
        """Obtiene meteorología horaria para un aeropuerto + fecha.

        Args:
            icao: Código ICAO del aeropuerto.
            date: Fecha ISO (``YYYY-MM-DD``).

        Returns:
            Dict con hourly data o ``None`` si error.
        """
        return self.get_weather_batch(icao, date, date)

    def get_weather_batch(
        self, icao: str, start_date: str, end_date: str,
    ) -> dict[str, Any] | None:
        """Obtiene meteorología para un rango de fechas (más eficiente).

        Args:
            icao: Código ICAO del aeropuerto.
            start_date: Fecha inicio ISO.
            end_date: Fecha fin ISO (inclusive).

        Returns:
            Dict con hourly data del rango completo.
        """
        try:
            lat, lon = get_airport_coords(icao)
        except KeyError:
            logger.warning("Coordenadas no encontradas para %s", icao)
            return None

        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "hourly": ",".join(HOURLY_VARS),
            "timezone": "UTC",
        }

        try:
            data = self._http_get(ARCHIVE_URL, params=params)
        except Exception as e:
            logger.warning("Open-Meteo error para %s (%s): %s", icao, start_date, e)
            return None

        return {
            "airport_code": icao,
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "hourly": data.get("hourly", {}),
            "raw": data,
        }
