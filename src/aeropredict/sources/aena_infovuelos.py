"""AENA Infovuelos adapter.

This adapter uses the same public JSON endpoint that the AENA website loads
from https://www.aena.es/es/infovuelos.html.

Key behavior:
- Requests are made via POST to a Satellite endpoint used by the public site.
- The API returns a limited time window of flights, not full historical data.
- The `dosDias=si` parameter asks for the site’s two-day window around the
  current query time.
- The adapter keeps a browser-like session context with a Referer header so
  the endpoint sees the request as coming from the Infovuelos page.

This module is intended for periodic snapshots such as hourly polling, and
not for bulk historical ingestion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from aeropredict.sources.base import BaseAdapter


AENA_BASE_URL = "https://www.aena.es"
AENA_INFOVUELOS_PAGE = f"{AENA_BASE_URL}/es/infovuelos.html"
AENA_FLIGHTS_ENDPOINT = f"{AENA_BASE_URL}/sites/Satellite"

FLIGHT_TYPES = {
    "departures": "S",
    "arrivals": "L",
    "S": "S",
    "L": "L",
}

FLIGHT_TYPE_LABELS = {
    "S": "departures",
    "L": "arrivals",
}


class AenaInfovuelosAdapter(BaseAdapter):
    """Client for AENA's Infovuelos JSON endpoint.

    The AENA Infovuelos service is not a standard REST API; it is the same
    backend used by AENA's public flight status page.

    This adapter:
    - maintains a session with browser-like headers and Referer,
    - optionally delegates retries through `BaseAdapter`/`Pool`,
    - falls back to a simple retry/backoff loop for POST requests,
    - and raises explicit errors when AENA returns non-JSON responses.

    Args:
        pool: Optional credential pool for sources that support it.
        timeout: Request timeout in seconds.
    """

    def __init__(self, pool: Any | None = None, timeout: int = 30) -> None:
        super().__init__(pool=pool)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": AENA_INFOVUELOS_PAGE,
            }
        )

    def warmup(self) -> None:
        """Load the Infovuelos page once so the session has normal browser context."""
        response = self.session.get(AENA_INFOVUELOS_PAGE, timeout=self.timeout)
        response.raise_for_status()

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

        # The AENA endpoint returns a short time window of flights around the
        # current query point. This is useful for snapshot polling, but it is
        # not a bulk historical endpoint.

        # Use a POST with simple retry/backoff. If a Pool is configured, prefer
        # using pool.execute to allow credential rotation; fallback to local loop.
        def _do_post() -> requests.Response:
            return self.session.post(
                AENA_FLIGHTS_ENDPOINT,
                params=params,
                timeout=self.timeout,
                allow_redirects=True,
            )

        # Execute with pool if available (pool.execute expects a callable).
        if getattr(self, "pool", None) is not None:
            response = self.pool.execute(lambda: _do_post(), context=AENA_FLIGHTS_ENDPOINT)
        else:
            # Basic retry/backoff loop (mirrors BaseAdapter behavior for GET).
            last_exc: Exception | None = None
            for attempt in range(1, 4):
                try:
                    response = _do_post()
                    if response.status_code == 429:
                        # Respect Retry-After if present
                        retry_after = response.headers.get("Retry-After")
                        try:
                            wait = float(retry_after) if retry_after else 1.0 * (2 ** (attempt - 1))
                        except Exception:
                            wait = 1.0 * (2 ** (attempt - 1))
                        time.sleep(wait)
                        continue
                    response.raise_for_status()
                    break
                except requests.RequestException as exc:
                    last_exc = exc
                    if attempt < 4:
                        time.sleep(1.0 * (2 ** (attempt - 1)))
                    else:
                        raise

        # Safe JSON parse with context for non-JSON 200 responses
        try:
            data = response.json()
        except ValueError as exc:
            raise ValueError(
                f"Non-JSON response from AENA for {airport_iata}: content-type={response.headers.get('content-type', '?')}"
            ) from exc

        if not isinstance(data, list):
            raise ValueError(f"Unexpected AENA response type: {type(data).__name__}")
        return data


def normalize_flight(
    raw: dict[str, Any],
    query_airport_iata: str,
    query_flight_type: str,
    snapshot_at_utc: datetime,
) -> dict[str, Any]:
    """Normalize one AENA raw flight row for CSV output."""
    flight_type = _clean(raw.get("tipoVuelo")) or query_flight_type
    airline_iata = _clean(raw.get("iataCompania")) or _clean(raw.get("compania"))
    raw_number = _clean(raw.get("numVuelo"))

    return {
        "snapshot_at_utc": snapshot_at_utc.isoformat(timespec="seconds"),
        "source": "aena_infovuelos",
        "query_airport_iata": query_airport_iata.upper(),
        "query_flight_type": FLIGHT_TYPE_LABELS.get(query_flight_type, query_flight_type),
        "flight_type": FLIGHT_TYPE_LABELS.get(str(flight_type), str(flight_type)),
        "flight_number": _join_flight_number(airline_iata, raw_number),
        "raw_flight_number": raw_number,
        "airline_iata": airline_iata,
        "airline_icao": _clean(raw.get("oaciCompania")),
        "airline_name": _clean(raw.get("nombreCompania")),
        "aena_airport_iata": _clean(raw.get("iataAena")),
        "other_airport_iata": _clean(raw.get("iataOtro")),
        "other_city": _clean(raw.get("ciudadIataOtro")),
        "scheduled_date": _clean(raw.get("fecha")),
        "scheduled_time": _clean(raw.get("horaProgramada")),
        "scheduled_local": _local_datetime(raw.get("fecha"), raw.get("horaProgramada")),
        "estimated_date": _clean(raw.get("fechaEstimada")),
        "estimated_time": _clean(raw.get("horaEstimada")),
        "estimated_local": _local_datetime(raw.get("fechaEstimada"), raw.get("horaEstimada")),
        "status": _clean(raw.get("estado")),
        "terminal": _clean(raw.get("terminal")),
        "gate_first": _clean(raw.get("puertaPrimera")),
        "gate_second": _clean(raw.get("puertaSegunda")),
        "checkin_from": _clean(raw.get("mostradorDesde")),
        "checkin_to": _clean(raw.get("mostradorHasta")),
        "aircraft_type": _clean(raw.get("tipoAeronave")),
    }


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def _join_flight_number(airline_iata: str | None, raw_number: str | None) -> str | None:
    if not raw_number:
        return None
    if airline_iata:
        return f"{airline_iata}{raw_number}"
    return raw_number


def _local_datetime(date_value: Any, time_value: Any) -> str | None:
    date_text = _clean(date_value)
    time_text = _clean(time_value)
    if not date_text or not time_text:
        return None
    try:
        parsed = datetime.strptime(f"{date_text} {time_text}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None
    return parsed.isoformat(timespec="seconds")
