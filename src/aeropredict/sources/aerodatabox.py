"""AeroDataBox API adapter for flight schedules.

Documentación: https://aerodatabox.com/
Acceso via API.Market (https://api.market/store/aedbx/aerodatabox).
Free trial: 600 API units.
Autenticación: header ``x-api-market-key``.
"""

from __future__ import annotations

import logging
from typing import Any

from aeropredict.opensky.config import get_aerodatabox_key
from aeropredict.sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://prod.api.market/api/v1/aedbx/aerodatabox"


class AeroDataBoxAdapter(BaseAdapter):
    """Adaptador para la API de AeroDataBox via API.Market.

    Key se pasa como header ``x-api-market-key``.
    Endpoint usado: ``/flights/number/{callsign}/{dateLocal}`` (TIER 2).
    """

    def __init__(self) -> None:
        super().__init__()
        self._api_key = get_aerodatabox_key()

    # -- Método principal ---------------------------------------------------

    def get_schedule(self, callsign: str, flight_date: str) -> dict[str, Any] | None:
        """Busca el schedule de un vuelo por callsign + fecha.

        Args:
            callsign: Código ICAO del vuelo (ej. ``IBE1234``).
            flight_date: Fecha ISO (``YYYY-MM-DD``).

        Returns:
            Dict normalizado del schedule, o ``None`` si no hay datos o no configurado.
        """
        if not self._api_key:
            logger.warning("AERODATABOX_API_KEY no configurada")
            return None

        date_part = flight_date[:10]
        # Usar searchBy=callsign porque OpenSky devuelve ICAO callsigns (EJU37PQ)
        endpoint = f"{BASE_URL}/flights/callsign/{callsign}/{date_part}"
        try:
            data = self._http_get(endpoint)
        except Exception as e:
            err = str(e)
            if "404" in err or "204" in err or "No Content" in err or "no content" in err.lower():
                logger.info("AeroDataBox: sin datos para %s %s", callsign, flight_date)
                return None
            raise

        return self._normalize(data, flight_date)

    def _get_headers(self) -> dict[str, str]:
        return {
            "x-api-market-key": self._api_key,
        }

    @staticmethod
    def _normalize(
        data: dict[str, Any] | list[Any], flight_date: str = ""
    ) -> dict[str, Any] | None:
        """Normaliza la respuesta de AeroDataBox a formato común.

        El endpoint ``/flights/callsign/{callsign}/{dateLocal}`` devuelve
        una lista (JSON array) de objetos flight. Cada flight tiene
        ``callSign``, ``number``, ``departure``, ``arrival``, ``airline``,
        ``aircraft``, etc.
        """
        flights = data if isinstance(data, list) else data.get("flights", [])
        if not flights:
            return None

        flight = flights[0]
        dep = flight.get("departure", {}) or {}
        arr = flight.get("arrival", {}) or {}
        airline = flight.get("airline", {}) or {}
        ac = flight.get("aircraft", {}) or {}

        # Extraer códigos ICAO de los objetos airport
        dep_airport = dep.get("airport", {}) or {}
        arr_airport = arr.get("airport", {}) or {}

        # actualTime puede no existir; fallback a revisedTime o runwayTime
        dep_actual = (
            dep.get("actualTime", {}) or
            dep.get("revisedTime", {}) or
            dep.get("runwayTime", {}) or {}
        )
        arr_actual = (
            arr.get("actualTime", {}) or
            arr.get("revisedTime", {}) or
            arr.get("runwayTime", {}) or {}
        )

        # Terminal/gate pueden estar en un objeto anidado o ser string directo
        dep_terminal = dep.get("terminal", None)
        if isinstance(dep_terminal, dict):
            dep_terminal = dep_terminal.get("local", "")
        dep_gate = dep.get("gate", None)
        if isinstance(dep_gate, dict):
            dep_gate = dep_gate.get("local", "")
        arr_terminal = arr.get("terminal", None)
        if isinstance(arr_terminal, dict):
            arr_terminal = arr_terminal.get("local", "")
        arr_gate = arr.get("gate", None)
        if isinstance(arr_gate, dict):
            arr_gate = arr_gate.get("local", "")

        return {
            "source": "aerodatabox",
            "callsign": flight.get("callSign", "") or flight.get("number", ""),
            "flight_date": flight.get("date", flight_date),
            "flight_status": flight.get("status", ""),
            "departure_airport": dep_airport.get("icao", ""),
            "departure_scheduled": dep.get("scheduledTime", {}).get("local"),
            "departure_actual": dep_actual.get("local"),
            "departure_terminal": dep_terminal,
            "departure_gate": dep_gate,
            "arrival_airport": arr_airport.get("icao", ""),
            "arrival_scheduled": arr.get("scheduledTime", {}).get("local"),
            "arrival_actual": arr_actual.get("local"),
            "arrival_terminal": arr_terminal,
            "arrival_gate": arr_gate,
            "airline_name": airline.get("name", ""),
            "airline_icao": airline.get("icao", ""),
            "aircraft_type": ac.get("model", "") or ac.get("type", ""),
            "aircraft_reg": ac.get("reg", ""),
            "raw": data,
        }
