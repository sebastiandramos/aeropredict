"""Modelos de datos para las respuestas de la API de OpenSky."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# State vector (estado instantáneo de una aeronave)
# ---------------------------------------------------------------------------


@dataclass
class StateVector:
    """Estado instantáneo de una aeronave.

    Documentación: https://openskynetwork.github.io/opensky-api/rest.html#state-vectors
    """

    icao24: str
    callsign: str | None
    origin_country: str
    time_position: datetime | None
    last_contact: datetime
    longitude: float | None
    latitude: float | None
    baro_altitude: float | None
    on_ground: bool
    velocity: float | None
    true_track: float | None
    vertical_rate: float | None
    sensors: list[int] | None
    geo_altitude: float | None
    squawk: str | None
    spi: bool
    position_source: int
    category: int | None

    @staticmethod
    def from_row(row: list[Any], time_ref: int) -> StateVector:
        """Construye un StateVector desde un array de la API.

        Args:
            row: Array de 18+ elementos de la respuesta JSON.
            time_ref: Unix timestamp (seconds) asociado a estos estados.

        Returns:
            StateVector poblado.
        """
        return StateVector(
            icao24=str(row[0]),
            callsign=str(row[1]).strip() if row[1] is not None else None,
            origin_country=str(row[2]) if row[2] is not None else "",
            time_position=_parse_ts(row[3]),
            last_contact=_parse_ts(row[4]),
            longitude=_to_float(row[5]),
            latitude=_to_float(row[6]),
            baro_altitude=_to_float(row[7]),
            on_ground=bool(row[8]),
            velocity=_to_float(row[9]),
            true_track=_to_float(row[10]),
            vertical_rate=_to_float(row[11]),
            sensors=row[12] if isinstance(row[12], list) else None,
            geo_altitude=_to_float(row[13]),
            squawk=str(row[14]) if row[14] is not None else None,
            spi=bool(row[15]),
            position_source=int(row[16]) if row[16] is not None else 0,
            category=int(row[17]) if len(row) > 17 and row[17] is not None else None,
        )


# ---------------------------------------------------------------------------
# Vuelo (flight)
# ---------------------------------------------------------------------------


@dataclass
class Flight:
    """Vuelo devuelto por los endpoints /flights/*."""

    icao24: str
    first_seen: datetime
    est_departure_airport: str | None
    last_seen: datetime
    est_arrival_airport: str | None
    callsign: str | None
    est_departure_airport_horiz_distance: float | None
    est_departure_airport_vert_distance: float | None
    est_arrival_airport_horiz_distance: float | None
    est_arrival_airport_vert_distance: float | None
    departure_airport_candidates_count: int | None
    arrival_airport_candidates_count: int | None

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Flight:
        """Construye un Flight desde un dict de la API JSON."""
        return Flight(
            icao24=str(data["icao24"]),
            first_seen=_parse_ts(data.get("firstSeen")),
            est_departure_airport=data.get("estDepartureAirport"),
            last_seen=_parse_ts(data.get("lastSeen")),
            est_arrival_airport=data.get("estArrivalAirport"),
            callsign=str(data.get("callsign", "")).strip() or None,
            est_departure_airport_horiz_distance=_to_float(
                data.get("estDepartureAirportHorizDistance")
            ),
            est_departure_airport_vert_distance=_to_float(
                data.get("estDepartureAirportVertDistance")
            ),
            est_arrival_airport_horiz_distance=_to_float(
                data.get("estArrivalAirportHorizDistance")
            ),
            est_arrival_airport_vert_distance=_to_float(
                data.get("estArrivalAirportVertDistance")
            ),
            departure_airport_candidates_count=_to_int(
                data.get("departureAirportCandidatesCount")
            ),
            arrival_airport_candidates_count=_to_int(
                data.get("arrivalAirportCandidatesCount")
            ),
        )


# ---------------------------------------------------------------------------
# Track / trayectoria
# ---------------------------------------------------------------------------


@dataclass
class TrackWaypoint:
    """Punto individual dentro de una trayectoria."""

    time: datetime
    latitude: float | None
    longitude: float | None
    baro_altitude: float | None
    true_track: float | None
    on_ground: bool


@dataclass
class Track:
    """Trayectoria completa de un vuelo.

    Documentación: https://openskynetwork.github.io/opensky-api/rest.html#track-by-aircraft
    """

    icao24: str
    start_time: datetime
    end_time: datetime
    callsign: str | None
    path: list[TrackWaypoint]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Track:
        """Construye un Track desde la respuesta JSON de la API."""
        icao24 = str(data["icao24"])
        start_time = _parse_ts(data.get("startTime"))
        end_time = _parse_ts(data.get("endTime"))
        callsign = str(data.get("callsign", "")).strip() or None

        path: list[TrackWaypoint] = []
        for wp in data.get("path", []):
            if isinstance(wp, list) and len(wp) >= 6:
                path.append(
                    TrackWaypoint(
                        time=_parse_ts(wp[0]),
                        latitude=_to_float(wp[1]),
                        longitude=_to_float(wp[2]),
                        baro_altitude=_to_float(wp[3]),
                        true_track=_to_float(wp[4]),
                        on_ground=bool(wp[5]),
                    )
                )
        return Track(
            icao24=icao24,
            start_time=start_time,
            end_time=end_time,
            callsign=callsign,
            path=path,
        )


# ---------------------------------------------------------------------------
# Utilidades de conversión
# ---------------------------------------------------------------------------


def _parse_ts(ts: int | None) -> datetime | None:
    """Convierte Unix timestamp (seconds) a datetime UTC."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
