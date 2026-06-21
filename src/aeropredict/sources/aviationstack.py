"""AviationStack API adapter for flight schedules.

Documentación: https://aviationstack.com/documentation
Free tier: 100 requests/mes.
"""

from __future__ import annotations

import logging
from typing import Any

from aeropredict.opensky.config import get_aviationstack_api_key
from aeropredict.opensky.pool import Pool
from aeropredict.sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.aviationstack.com/v1/flights"


class AviationStackAdapter(BaseAdapter):
    """Adaptador para la API de AviationStack.

    Soporta Pool rotation para múltiples API keys.
    """

    def __init__(self, pool: Pool[str] | None = None) -> None:
        super().__init__(pool)
        self._api_key: str = ""

    # -- Método principal ---------------------------------------------------

    def get_schedule(self, callsign: str, flight_date: str) -> dict[str, Any] | None:
        """Busca el schedule de un vuelo por callsign + fecha.

        Args:
            callsign: Código ICAO del vuelo (ej. ``IBE1234``).
            flight_date: Fecha ISO (``YYYY-MM-DD``).

        Returns:
            Dict con datos normalizados del schedule, o ``None`` si no hay datos.
        """
        params: dict[str, Any] = {
            "flight_icao": callsign,
            "flight_date": flight_date,
        }

        if self.pool:
            # Usar Pool para rotar API keys — pasar key activa al lambda
            return self.pool.execute(
                lambda key: self._query_with_key({**params, "access_key": key}),
            )

        # Sin pool — usar API key directa
        self._api_key = get_aviationstack_api_key()
        if not self._api_key:
            logger.warning("AVIATIONSTACK_API_KEY no configurada")
            return None
        return self._query_with_key(params)

    # -- Métodos internos ---------------------------------------------------

    def _query_with_key(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """Ejecuta query con la API key activa."""
        params["access_key"] = self._api_key
        data = self._http_get(BASE_URL, params=params)
        return self._normalize(data)

    def _get_headers(self) -> dict[str, str]:
        return {}  # API key va en query params

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any] | None:
        """Normaliza la respuesta de AviationStack a un formato común.

        Returns:
            Dict normalizado o ``None`` si no hay datos.
        """
        results = data.get("data", [])
        if not results:
            return None

        flight = results[0]  # Usar el primer resultado
        dep = flight.get("departure", {}) or {}
        arr = flight.get("arrival", {}) or {}
        airline = flight.get("airline", {}) or {}
        ac = flight.get("aircraft", {}) or {}

        return {
            "source": "aviationstack",
            "callsign": flight.get("flight", {}).get("icao", ""),
            "flight_date": flight.get("flight_date", ""),
            "flight_status": flight.get("flight_status", ""),
            "departure_airport": dep.get("icao", ""),
            "departure_scheduled": dep.get("scheduled"),
            "departure_actual": dep.get("actual"),
            "departure_estimated": dep.get("estimated"),
            "departure_terminal": dep.get("terminal"),
            "departure_gate": dep.get("gate"),
            "arrival_airport": arr.get("icao", ""),
            "arrival_scheduled": arr.get("scheduled"),
            "arrival_actual": arr.get("actual"),
            "arrival_estimated": arr.get("estimated"),
            "arrival_terminal": arr.get("terminal"),
            "arrival_gate": arr.get("gate"),
            "airline_name": airline.get("name", ""),
            "airline_icao": airline.get("icao", ""),
            "aircraft_type": ac.get("registration", ""),
            "raw": data,
        }
