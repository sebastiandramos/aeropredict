"""AENA Infovuelos adapter.

This adapter uses the same public JSON endpoint loaded by
https://www.aena.es/es/infovuelos.html. The service exposes a short window
around the query time, so it is useful for periodic snapshots, not historical
bulk downloads.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

from .base import BaseAdapter

logger = logging.getLogger(__name__)

AENA_BASE_URL = "https://www.aena.es"
AENA_INFOVUELOS_PAGE = f"{AENA_BASE_URL}/es/infovuelos.html"
AENA_FLIGHTS_ENDPOINT = f"{AENA_BASE_URL}/sites/Satellite"

FLIGHT_TYPES = {
    "departures": "S",
    "arrivals": "L",
}

FLIGHT_TYPE_LABELS = {
    "S": "departures",
    "L": "arrivals",
}


class AenaInfovuelosAdapter(BaseAdapter):
    """Client for AENA's Infovuelos JSON endpoint."""

    def __init__(self, timeout: int = 30) -> None:
        super().__init__()
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": AENA_INFOVUELOS_PAGE,
        }

    def warmup(self) -> None:
        """Load the Infovuelos page once for browser context cookies."""
        try:
            response = requests.get(
                AENA_INFOVUELOS_PAGE,
                headers=self._get_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("AENA warmup failed (non-fatal): %s", exc)

    def get_flights(
        self,
        airport_iata: str,
        flight_type: str,
        dos_dias: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch flights for one AENA airport.

        Args:
            airport_iata: AENA airport IATA code, e.g. ``BCN`` or ``MAD``.
            flight_type: ``departures``/``S`` or ``arrivals``/``L``.
            dos_dias: Match the website's short-window query mode.

        Returns:
            A list of raw AENA flight dictionaries.
        """
        flight_type_code = FLIGHT_TYPES.get(flight_type)
        if flight_type_code is None:
            allowed = ", ".join(sorted(FLIGHT_TYPES))
            raise ValueError(f"Invalid flight_type={flight_type!r}. Expected one of: {allowed}")

        params: dict[str, str] = {
            "pagename": "AENA_ConsultarVuelos",
            "airport": airport_iata.upper(),
            "flightType": flight_type_code,
        }
        if dos_dias:
            params["dosDias"] = "si"

        data = self._http_post(
            AENA_FLIGHTS_ENDPOINT,
            params=params,
        )
        if not isinstance(data, list):
            raise ValueError(f"Unexpected AENA response type: {type(data).__name__}")
        return data

    @staticmethod
    def normalize_flight(
        raw: dict[str, Any],
        query_airport_iata: str,
        query_flight_type: str,
        snapshot_at_utc: datetime,
    ) -> dict[str, Any]:
        """Normalize one AENA raw flight row for CSV output."""
        flight_type = AenaInfovuelosAdapter._clean(raw.get("tipoVuelo")) or query_flight_type
        airline_iata = AenaInfovuelosAdapter._clean(
            raw.get("iataCompania")
        ) or AenaInfovuelosAdapter._clean(raw.get("compania"))
        raw_number = AenaInfovuelosAdapter._clean(raw.get("numVuelo"))

        return {
            "snapshot_at_utc": snapshot_at_utc.isoformat(timespec="seconds"),
            "source": "aena_infovuelos",
            "query_airport_iata": query_airport_iata.upper(),
            "query_flight_type": FLIGHT_TYPE_LABELS.get(query_flight_type, query_flight_type),
            "flight_type": FLIGHT_TYPE_LABELS.get(str(flight_type), str(flight_type)),
            "flight_number": AenaInfovuelosAdapter._join_flight_number(airline_iata, raw_number),
            "raw_flight_number": raw_number,
            "airline_iata": airline_iata,
            "airline_icao": AenaInfovuelosAdapter._clean(raw.get("oaciCompania")),
            "airline_name": AenaInfovuelosAdapter._clean(raw.get("nombreCompania")),
            "aena_airport_iata": AenaInfovuelosAdapter._clean(raw.get("iataAena")),
            "other_airport_iata": AenaInfovuelosAdapter._clean(raw.get("iataOtro")),
            "other_city": AenaInfovuelosAdapter._clean(raw.get("ciudadIataOtro")),
            "scheduled_date": AenaInfovuelosAdapter._clean(raw.get("fecha")),
            "scheduled_time": AenaInfovuelosAdapter._clean(raw.get("horaProgramada")),
            "scheduled_local": AenaInfovuelosAdapter._local_datetime(
                raw.get("fecha"),
                raw.get("horaProgramada"),
            ),
            "estimated_date": AenaInfovuelosAdapter._clean(raw.get("fechaEstimada")),
            "estimated_time": AenaInfovuelosAdapter._clean(raw.get("horaEstimada")),
            "estimated_local": AenaInfovuelosAdapter._local_datetime(
                raw.get("fechaEstimada"),
                raw.get("horaEstimada"),
            ),
            "status": AenaInfovuelosAdapter._clean(raw.get("estado")),
            "terminal": AenaInfovuelosAdapter._clean(raw.get("terminal")),
            "gate_first": AenaInfovuelosAdapter._clean(raw.get("puertaPrimera")),
            "gate_second": AenaInfovuelosAdapter._clean(raw.get("puertaSegunda")),
            "checkin_from": AenaInfovuelosAdapter._clean(raw.get("mostradorDesde")),
            "checkin_to": AenaInfovuelosAdapter._clean(raw.get("mostradorHasta")),
            "aircraft_type": AenaInfovuelosAdapter._clean(raw.get("tipoAeronave")),
        }

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() == "null":
            return None
        return text

    @staticmethod
    def _join_flight_number(airline_iata: str | None, raw_number: str | None) -> str | None:
        if not raw_number:
            return None
        if airline_iata:
            return f"{airline_iata}{raw_number}"
        return raw_number

    @staticmethod
    def _local_datetime(date_value: Any, time_value: Any) -> str | None:
        date_text = AenaInfovuelosAdapter._clean(date_value)
        time_text = AenaInfovuelosAdapter._clean(time_value)
        if not date_text or not time_text:
            return None
        try:
            parsed = datetime.strptime(f"{date_text} {time_text}", "%d/%m/%Y %H:%M:%S")
        except ValueError:
            return None
        return parsed.isoformat(timespec="seconds")

