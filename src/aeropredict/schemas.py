"""Pydantic v2 validation schemas for all data layers.

Layer architecture:
    Bronze — raw data from OpenSky API, Delta Lake partitioned by ingestion_date.
    Silver — structured documents in MongoDB (flights, aircraft, weather, schedules).
    Gold   — tabular data in PostgreSQL (entities + aggregations + feature store).

Each model is frozen (immutable) and forbids extra fields to catch data drift early.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
_ICAO24_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_ICAO24_12_RE = re.compile(r"^[0-9a-fA-F]{6,12}$")
_AIRPORT_CODE_RE = re.compile(r"^[A-Z]{4}$")
_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,10}$")
_SQUAWK_RE = re.compile(r"^\d{4}$")


def _check_icao24(v: str) -> str:
    """Validate and normalize a 6-character hex ICAO 24-bit code."""
    upper = v.strip().upper()
    if not _ICAO24_RE.match(upper):
        raise ValueError(f"Invalid icao24: '{v}' - must be 6 hex digits")
    return upper


def _check_icao24_12(v: str) -> str:
    """Validate and normalize a 6-12 character hex ICAO code (aircraft registry)."""
    upper = v.strip().upper()
    if not _ICAO24_12_RE.match(upper):
        raise ValueError(f"Invalid icao24: '{v}' - must be 6-12 hex digits")
    return upper


def _check_airport_code(v: str | None) -> str | None:
    """Validate a 4-letter ICAO airport code."""
    if v is None:
        return None
    upper = v.strip().upper()
    if not _AIRPORT_CODE_RE.match(upper):
        raise ValueError(f"Invalid airport code: '{v}' - must be 4 uppercase letters")
    return upper


def _check_callsign(v: str | None) -> str | None:
    """Validate callsign (1-10 alphanumeric, uppercase)."""
    if v is None:
        return None
    upper = v.strip().upper()
    if not _CALLSIGN_RE.match(upper):
        raise ValueError(
            f"Invalid callsign: '{v}' - must be 1-10 uppercase alphanumeric"
        )
    return upper


def _ensure_utc(v: datetime | None) -> datetime | None:
    """Ensure datetime is timezone-aware and UTC."""
    if v is None:
        return None
    if v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v.astimezone(UTC)


def _non_negative(v: float | None) -> float | None:
    if v is not None and v < 0:
        raise ValueError(f"Value must be non-negative, got {v}")
    return v


def _percentage(v: float | None) -> float | None:
    if v is not None and not (0 <= v <= 100):
        raise ValueError(f"Percentage must be between 0 and 100, got {v}")
    return v


def _check_lat(v: float | None) -> float | None:
    if v is not None and not (-90.0 <= v <= 90.0):
        raise ValueError(f"Latitude must be between -90 and 90, got {v}")
    return v


def _check_lon(v: float | None) -> float | None:
    if v is not None and not (-180.0 <= v <= 180.0):
        raise ValueError(f"Longitude must be between -180 and 180, got {v}")
    return v


def _check_squawk(v: str | None) -> str | None:
    if v is None:
        return None
    stripped = v.strip()
    if not _SQUAWK_RE.match(stripped):
        raise ValueError(f"Invalid squawk: '{v}' - must be 4 digits")
    return stripped


# ===================================================================
# BRONZE LAYER — raw API responses
# ===================================================================


class OpenSkyFlight(BaseModel):
    """Bronze: raw flight from OpenSky /flights/* endpoint.

    Mirrors the ``Flight`` dataclass in ``opensky.models`` with added validation.
    """

    icao24: str
    first_seen: datetime | None = None
    est_departure_airport: str | None = None
    last_seen: datetime | None = None
    est_arrival_airport: str | None = None
    callsign: str | None = None
    est_departure_airport_horiz_distance: float | None = None
    est_departure_airport_vert_distance: float | None = None
    est_arrival_airport_horiz_distance: float | None = None
    est_arrival_airport_vert_distance: float | None = None
    departure_airport_candidates_count: int | None = None
    arrival_airport_candidates_count: int | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _dep_airport = field_validator("est_departure_airport", "est_arrival_airport")(
        _check_airport_code
    )
    _first_seen = field_validator("first_seen", "last_seen")(_ensure_utc)
    _dep_horiz = field_validator(
        "est_departure_airport_horiz_distance",
        "est_departure_airport_vert_distance",
        "est_arrival_airport_horiz_distance",
        "est_arrival_airport_vert_distance",
    )(_non_negative)
    _candidates = field_validator(
        "departure_airport_candidates_count",
        "arrival_airport_candidates_count",
    )(_non_negative)


class StateVector(BaseModel):
    """Bronze: raw state vector from OpenSky state vectors endpoint.

    Mirrors ``opensky.models.StateVector``.
    """

    icao24: str
    callsign: str | None = None
    origin_country: str = ""
    time_position: datetime | None = None
    last_contact: datetime | None = None
    longitude: float | None = None
    latitude: float | None = None
    baro_altitude: float | None = None
    on_ground: bool = False
    velocity: float | None = None
    true_track: float | None = None
    vertical_rate: float | None = None
    sensors: list[int] | None = None
    geo_altitude: float | None = None
    squawk: str | None = None
    spi: bool = False
    position_source: int = 0
    category: int | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _squawk = field_validator("squawk")(_check_squawk)
    _time_position = field_validator("time_position", "last_contact")(_ensure_utc)
    _lat = field_validator("latitude")(_check_lat)
    _lon = field_validator("longitude")(_check_lon)


class TrackWaypoint(BaseModel):
    """Bronze: a single waypoint within a flight track.

    Mirrors ``opensky.models.TrackWaypoint``.
    """

    time: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    baro_altitude: float | None = None
    true_track: float | None = None
    on_ground: bool = False

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _time = field_validator("time")(_ensure_utc)
    _lat = field_validator("latitude")(_check_lat)
    _lon = field_validator("longitude")(_check_lon)


class Track(BaseModel):
    """Bronze: complete flight track with waypoints.

    Mirrors ``opensky.models.Track``.
    """

    icao24: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    callsign: str | None = None
    path: list[TrackWaypoint] = Field(default_factory=list)

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _start_time = field_validator("start_time", "end_time")(_ensure_utc)


# ===================================================================
# SILVER LAYER — MongoDB documents
# ===================================================================


class FlightDocument(BaseModel):
    """Silver: flight document stored in MongoDB ``flights`` collection.

    Built from ``OpenSkyFlight`` by ``bronze_to_silver.py`` with added
    ``flight_date`` and ``ingested_at`` fields.
    """

    icao24: str
    callsign: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    est_departure_airport: str | None = None
    est_arrival_airport: str | None = None
    departure_airport_horiz_distance: float | None = None
    departure_airport_vert_distance: float | None = None
    arrival_airport_horiz_distance: float | None = None
    arrival_airport_vert_distance: float | None = None
    departure_airport_candidates_count: int | None = None
    arrival_airport_candidates_count: int | None = None
    flight_date: datetime | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _dep_airport = field_validator("est_departure_airport", "est_arrival_airport")(
        _check_airport_code
    )
    _seens = field_validator("first_seen", "last_seen", "flight_date")(_ensure_utc)
    _ingested = field_validator("ingested_at")(_ensure_utc)
    _horiz = field_validator(
        "departure_airport_horiz_distance",
        "departure_airport_vert_distance",
        "arrival_airport_horiz_distance",
        "arrival_airport_vert_distance",
    )(_non_negative)
    _candidates = field_validator(
        "departure_airport_candidates_count",
        "arrival_airport_candidates_count",
    )(_non_negative)


class StateVectorDocument(BaseModel):
    """Silver: state vector document in MongoDB ``state_vectors`` collection.

    Extends the bronze ``StateVector`` with ``snapshot_date`` and ``ingested_at``.
    """

    icao24: str
    callsign: str | None = None
    origin_country: str = ""
    time_position: datetime | None = None
    last_contact: datetime | None = None
    longitude: float | None = None
    latitude: float | None = None
    baro_altitude: float | None = None
    on_ground: bool = False
    velocity: float | None = None
    true_track: float | None = None
    vertical_rate: float | None = None
    geo_altitude: float | None = None
    squawk: str | None = None
    spi: bool = False
    position_source: int = 0
    category: int | None = None
    snapshot_date: datetime | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _squawk = field_validator("squawk")(_check_squawk)
    _times = field_validator("time_position", "last_contact", "snapshot_date")(
        _ensure_utc
    )
    _ingested = field_validator("ingested_at")(_ensure_utc)
    _lat = field_validator("latitude")(_check_lat)
    _lon = field_validator("longitude")(_check_lon)


class TrackWaypointDocument(BaseModel):
    """Silver: individual track waypoint in MongoDB ``track_waypoints`` collection.

    Extends bronze ``TrackWaypoint`` with track-level metadata for denormalized storage.
    """

    icao24: str
    callsign: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    waypoint_time: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    baro_altitude: float | None = None
    true_track: float | None = None
    on_ground: bool = False
    track_date: datetime | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _times = field_validator(
        "start_time", "end_time", "waypoint_time", "track_date"
    )(_ensure_utc)
    _ingested = field_validator("ingested_at")(_ensure_utc)
    _lat = field_validator("latitude")(_check_lat)
    _lon = field_validator("longitude")(_check_lon)


class WeatherDocument(BaseModel):
    """Silver: weather data document in MongoDB ``weather`` collection.

    Hourly meteorological observations per airport, sourced from Open-Meteo.
    """

    airport_code: str
    timestamp: datetime | None = None
    flight_date: datetime | None = None
    temperature_2m: float | None = None
    precipitation: float | None = None
    wind_speed_10m: float | None = None
    wind_gusts_10m: float | None = None
    visibility: float | None = None
    cloud_cover: float | None = None
    relative_humidity_2m: float | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _airport = field_validator("airport_code")(_check_airport_code)
    _times = field_validator("timestamp", "flight_date")(_ensure_utc)
    _ingested = field_validator("ingested_at")(_ensure_utc)
    _precip = field_validator("precipitation")(_non_negative)
    _cloud = field_validator("cloud_cover")(_percentage)
    _humidity = field_validator("relative_humidity_2m")(_percentage)


class ScheduleDocument(BaseModel):
    """Silver: flight schedule document in MongoDB ``schedules`` collection.

    Sourced from AeroDataBox and/or AviationStack APIs.
    Stores scheduled vs actual times for delay computation.
    """

    source: str = ""
    callsign: str | None = None
    flight_date: str | None = None
    flight_status: str | None = None
    departure_airport: str | None = None
    departure_scheduled: str | None = None
    departure_actual: str | None = None
    departure_terminal: str | None = None
    departure_gate: str | None = None
    arrival_airport: str | None = None
    arrival_scheduled: str | None = None
    arrival_actual: str | None = None
    arrival_terminal: str | None = None
    arrival_gate: str | None = None
    airline_name: str | None = None
    airline_icao: str | None = None
    aircraft_type: str | None = None
    aircraft_reg: str | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _dep_airport = field_validator("departure_airport", "arrival_airport")(
        _check_airport_code
    )
    _callsign = field_validator("callsign")(_check_callsign)
    _ingested = field_validator("ingested_at")(_ensure_utc)

    @field_validator("source")
    @classmethod
    def _check_source(cls, v: str) -> str:
        allowed = {"aerodatabox", "aviationstack", ""}
        if v.lower() not in allowed:
            raise ValueError(f"Unknown schedule source: '{v}'")
        return v.lower()


class AircraftDocument(BaseModel):
    """Silver: aircraft registry document in MongoDB ``aircraft`` collection.

    Aircraft metadata from OpenSky aircraft database (icao24 → registration,
    manufacturer, model, operator, etc.).
    """

    icao24: str
    registration: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    typecode: str | None = None
    serial_number: str | None = None
    line_number: str | None = None
    icao_aircraft_type: str | None = None
    operator: str | None = None
    operator_callsign: str | None = None
    operator_icao: str | None = None
    operator_iata: str | None = None
    first_flight_date: str | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24_12)
    _ingested = field_validator("ingested_at")(_ensure_utc)

    @field_validator("first_flight_date")
    @classmethod
    def _check_first_flight(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            datetime.strptime(v.strip()[:10], "%Y-%m-%d")
        except (ValueError, IndexError):
            raise ValueError(
                f"Invalid first_flight_date: '{v}' - must be YYYY-MM-DD"
            ) from None
        return v.strip()[:10]


# ===================================================================
# GOLD LAYER — PostgreSQL tables (entities)
# ===================================================================


class GoldFlight(BaseModel):
    """Gold: flight entity in PostgreSQL ``gold.flights`` table.

    One row per flight, upserted from MongoDB ``flights`` collection.
    """

    icao24: str
    callsign: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    flight_date: datetime | None = None
    est_departure_airport: str | None = None
    est_arrival_airport: str | None = None
    departure_airport_horiz_distance: float | None = None
    departure_airport_vert_distance: float | None = None
    arrival_airport_horiz_distance: float | None = None
    arrival_airport_vert_distance: float | None = None
    departure_airport_candidates_count: int | None = None
    arrival_airport_candidates_count: int | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _dep_airport = field_validator("est_departure_airport", "est_arrival_airport")(
        _check_airport_code
    )
    _seens = field_validator("first_seen", "last_seen", "flight_date")(_ensure_utc)
    _ingested = field_validator("ingested_at")(_ensure_utc)
    _horiz = field_validator(
        "departure_airport_horiz_distance",
        "departure_airport_vert_distance",
        "arrival_airport_horiz_distance",
        "arrival_airport_vert_distance",
    )(_non_negative)
    _candidates = field_validator(
        "departure_airport_candidates_count",
        "arrival_airport_candidates_count",
    )(_non_negative)


class GoldAircraft(BaseModel):
    """Gold: aircraft master table in PostgreSQL ``gold.aircraft``.

    One row per aircraft, keyed by ``icao24`` (PK).
    """

    icao24: str
    typecode: str | None = None
    manufacturer: str | None = None
    operator: str | None = None
    first_flight_date: str | None = None
    icao_aircraft_type: str | None = None
    registration: str | None = None
    serial_number: str | None = None
    tracked: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24_12)
    _tracked = field_validator("tracked")(_ensure_utc)

    @field_validator("first_flight_date")
    @classmethod
    def _check_first_flight(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            datetime.strptime(v.strip()[:10], "%Y-%m-%d")
        except (ValueError, IndexError):
            raise ValueError(
                f"Invalid first_flight_date: '{v}' - must be YYYY-MM-DD"
            ) from None
        return v.strip()[:10]


class GoldWeather(BaseModel):
    """Gold: weather table in PostgreSQL ``gold.weather``.

    Hourly weather observations per airport, unique on (airport_code, timestamp).
    """

    airport_code: str
    timestamp: datetime
    flight_date: datetime | None = None
    temperature_2m: float | None = None
    precipitation: float | None = None
    wind_speed_10m: float | None = None
    wind_gusts_10m: float | None = None
    visibility: float | None = None
    cloud_cover: float | None = None
    relative_humidity_2m: float | None = None
    ingested_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _airport = field_validator("airport_code")(_check_airport_code)
    _timestamps = field_validator("timestamp", "flight_date")(_ensure_utc)
    _ingested = field_validator("ingested_at")(_ensure_utc)
    _precip = field_validator("precipitation")(_non_negative)
    _cloud = field_validator("cloud_cover")(_percentage)
    _humidity = field_validator("relative_humidity_2m")(_percentage)


# ===================================================================
# GOLD LAYER — PostgreSQL tables (aggregations)
# ===================================================================


class DailyAirportTraffic(BaseModel):
    """Gold: daily traffic aggregation in PostgreSQL ``gold.daily_airport_traffic``.

    Counts arrivals and departures per airport per day.
    """

    airport_code: str
    flight_date: datetime
    arrivals_count: int = 0
    departures_count: int = 0
    updated_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _airport = field_validator("airport_code")(_check_airport_code)
    _flight_date = field_validator("flight_date")(_ensure_utc)
    _updated = field_validator("updated_at")(_ensure_utc)
    _arrivals = field_validator("arrivals_count", "departures_count")(_non_negative)


class RouteDensity(BaseModel):
    """Gold: route density aggregation in PostgreSQL ``gold.route_density``.

    Counts flights per origin-destination pair with first/last seen dates.
    """

    departure_airport: str
    arrival_airport: str
    flight_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    updated_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _dep_airport = field_validator("departure_airport", "arrival_airport")(
        _check_airport_code
    )
    _seens = field_validator("first_seen", "last_seen")(_ensure_utc)
    _updated = field_validator("updated_at")(_ensure_utc)
    _flight_count = field_validator("flight_count")(_non_negative)


class HourlyDistribution(BaseModel):
    """Gold: hourly distribution in PostgreSQL ``gold.hourly_distribution``.

    Arrivals/departures per airport, day, and hour.
    """

    airport_code: str
    flight_date: datetime
    hour: int
    arrivals_count: int = 0
    departures_count: int = 0
    updated_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _airport = field_validator("airport_code")(_check_airport_code)
    _flight_date = field_validator("flight_date")(_ensure_utc)
    _updated = field_validator("updated_at")(_ensure_utc)
    _counts = field_validator("arrivals_count", "departures_count")(_non_negative)

    @field_validator("hour")
    @classmethod
    def _check_hour(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError(f"Hour must be 0-23, got {v}")
        return v


# ===================================================================
# FEATURE STORE
# ===================================================================


class FeatureStoreRow(BaseModel):
    """Gold: feature store row in PostgreSQL ``gold.feature_store``.

    Flat feature vector ready for ML model training.
    Primary key: (icao24, flight_date).
    """

    icao24: str
    flight_date: datetime
    callsign: str | None = None
    departure_airport: str | None = None
    arrival_airport: str | None = None
    delay_minutes: float | None = None
    airborne_minutes: float | None = None
    departure_hour: int | None = None
    day_of_week: int | None = None
    month: int | None = None
    aircraft_type: str | None = None
    aircraft_manufacturer: str | None = None
    aircraft_operator: str | None = None
    aircraft_age_years: float | None = None
    route_daily_traffic: int | None = None
    route_total_density: int | None = None
    departure_airport_hourly_traffic: int | None = None
    arrival_airport_hourly_traffic: int | None = None
    dep_temperature: float | None = None
    dep_precipitation: float | None = None
    dep_wind_speed: float | None = None
    dep_visibility: float | None = None
    arr_temperature: float | None = None
    arr_precipitation: float | None = None
    arr_wind_speed: float | None = None
    arr_visibility: float | None = None
    schedule_source: str | None = None
    created_at: datetime | None = None

    model_config: dict[str, Any] = {"frozen": True, "extra": "forbid"}

    _icao24 = field_validator("icao24")(_check_icao24)
    _callsign = field_validator("callsign")(_check_callsign)
    _dep_airport = field_validator("departure_airport", "arrival_airport")(
        _check_airport_code
    )
    _flight_date = field_validator("flight_date")(_ensure_utc)
    _created = field_validator("created_at")(_ensure_utc)
    _age = field_validator("aircraft_age_years")(_non_negative)
    _delay = field_validator("delay_minutes")(_non_negative)
    _airborne = field_validator("airborne_minutes")(_non_negative)

    @field_validator("departure_hour")
    @classmethod
    def _check_hour(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 23):
            raise ValueError(f"departure_hour must be 0-23, got {v}")
        return v

    @field_validator("day_of_week")
    @classmethod
    def _check_dow(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 7):
            raise ValueError(f"day_of_week must be 1-7, got {v}")
        return v

    @field_validator("month")
    @classmethod
    def _check_month(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 12):
            raise ValueError(f"month must be 1-12, got {v}")
        return v

    @field_validator("schedule_source")
    @classmethod
    def _check_source(cls, v: str | None) -> str | None:
        if v is None:
            return None
        allowed = {"aerodatabox", "aviationstack"}
        if v.lower() not in allowed:
            raise ValueError(f"Unknown schedule source: '{v}'")
        return v.lower()


# ===================================================================
# Re-export commonly used names at schema layer
# ===================================================================

# Convenience alias: the most-used flight model at each layer
BronzeFlight = OpenSkyFlight
SilverFlight = FlightDocument
GoldFlightModel = GoldFlight

# Export all public names
__all__ = [
    "AircraftDocument",
    "BronzeFlight",
    "DailyAirportTraffic",
    "FeatureStoreRow",
    "FlightDocument",
    "GoldAircraft",
    "GoldFlight",
    "GoldFlightModel",
    "GoldWeather",
    "HourlyDistribution",
    "OpenSkyFlight",
    "RouteDensity",
    "ScheduleDocument",
    "SilverFlight",
    "StateVector",
    "StateVectorDocument",
    "Track",
    "TrackWaypoint",
    "TrackWaypointDocument",
    "WeatherDocument",
]
