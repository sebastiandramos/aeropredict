"""Tests for aeropredict.schemas — pydantic v2 validation models.

Covers:
    - Happy-path construction for every layer (Bronze, Silver, Gold, Feature Store)
    - Validation errors for each constraint type
    - Serialization round-trip via model_dump_json / model_validate_json
    - Frozen-model immutability
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aeropredict.schemas import (
    AircraftDocument,
    BronzeFlight,
    DailyAirportTraffic,
    FeatureStoreRow,
    FlightDocument,
    GoldAircraft,
    GoldFlight,
    GoldFlightModel,
    GoldWeather,
    HourlyDistribution,
    OpenSkyFlight,
    RouteDensity,
    ScheduleDocument,
    SilverFlight,
    StateVector,
    StateVectorDocument,
    Track,
    TrackWaypoint,
    TrackWaypointDocument,
    WeatherDocument,
)

# ---------------------------------------------------------------------------
# Fixtures: shared valid values
# ---------------------------------------------------------------------------

NOW_UTC = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
TODAY_UTC = datetime(2025, 6, 15, tzinfo=UTC)


# ===========================================================================
# BRONZE LAYER
# ===========================================================================


class TestBronzeLayer:
    """Happy-path construction for Bronze-layer models."""

    def test_opensky_flight_minimal(self) -> None:
        """OpenSkyFlight with only required fields."""
        f = BronzeFlight(icao24="ABCDEF")
        assert f.icao24 == "ABCDEF"
        assert f.callsign is None

    def test_opensky_flight_full(self) -> None:
        """OpenSkyFlight with all fields populated."""
        f = OpenSkyFlight(
            icao24="ABCDEF",
            callsign="IBE1234",
            first_seen=NOW_UTC,
            est_departure_airport="LEMD",
            last_seen=NOW_UTC,
            est_arrival_airport="LEBL",
            est_departure_airport_horiz_distance=1500.0,
            est_departure_airport_vert_distance=50.0,
            est_arrival_airport_horiz_distance=2000.0,
            est_arrival_airport_vert_distance=100.0,
            departure_airport_candidates_count=3,
            arrival_airport_candidates_count=5,
        )
        assert f.icao24 == "ABCDEF"
        assert f.callsign == "IBE1234"

    def test_state_vector_minimal(self) -> None:
        """StateVector with only required fields."""
        s = StateVector(icao24="ABCDEF")
        assert s.icao24 == "ABCDEF"
        assert s.origin_country == ""

    def test_state_vector_full(self) -> None:
        """StateVector with all fields."""
        s = StateVector(
            icao24="ABCDEF",
            callsign="SWA1234",
            origin_country="Spain",
            time_position=NOW_UTC,
            last_contact=NOW_UTC,
            longitude=-3.5,
            latitude=40.5,
            baro_altitude=10000.0,
            on_ground=False,
            velocity=250.0,
            true_track=180.0,
            vertical_rate=0.0,
            geo_altitude=10500.0,
            squawk="1200",
            spi=False,
            position_source=1,
            category=0,
        )
        assert s.latitude == 40.5
        assert s.squawk == "1200"

    def test_track_waypoint(self) -> None:
        """TrackWaypoint construction."""
        wp = TrackWaypoint(
            time=NOW_UTC,
            latitude=40.5,
            longitude=-3.5,
            baro_altitude=10000.0,
            true_track=180.0,
            on_ground=False,
        )
        assert wp.latitude == 40.5

    def test_track(self) -> None:
        """Track with waypoints."""
        wp = TrackWaypoint(time=NOW_UTC, latitude=40.5, longitude=-3.5)
        t = Track(
            icao24="ABCDEF",
            start_time=NOW_UTC,
            end_time=NOW_UTC,
            callsign="IBE1234",
            path=[wp],
        )
        assert len(t.path) == 1
        assert t.path[0].latitude == 40.5


# ===========================================================================
# SILVER LAYER
# ===========================================================================


class TestSilverLayer:
    """Happy-path construction for Silver-layer (MongoDB) models."""

    def test_flight_document(self) -> None:
        """FlightDocument with all fields."""
        d = FlightDocument(
            icao24="ABCDEF",
            callsign="IBE1234",
            first_seen=NOW_UTC,
            last_seen=NOW_UTC,
            est_departure_airport="LEMD",
            est_arrival_airport="LEBL",
            flight_date=TODAY_UTC,
            ingested_at=NOW_UTC,
        )
        assert d.icao24 == "ABCDEF"
        assert d.est_departure_airport == "LEMD"
        # Verify SilverFlight alias is same class
        assert type(d) is SilverFlight

    def test_state_vector_document(self) -> None:
        """StateVectorDocument with all fields."""
        d = StateVectorDocument(
            icao24="ABCDEF",
            callsign="SWA1234",
            snapshot_date=TODAY_UTC,
            ingested_at=NOW_UTC,
        )
        assert d.icao24 == "ABCDEF"

    def test_track_waypoint_document(self) -> None:
        """TrackWaypointDocument construction."""
        d = TrackWaypointDocument(
            icao24="ABCDEF",
            callsign="IBE1234",
            start_time=NOW_UTC,
            end_time=NOW_UTC,
            waypoint_time=NOW_UTC,
            track_date=TODAY_UTC,
        )
        assert d.icao24 == "ABCDEF"

    def test_weather_document(self) -> None:
        """WeatherDocument with typical fields."""
        w = WeatherDocument(
            airport_code="LEMD",
            timestamp=NOW_UTC,
            flight_date=TODAY_UTC,
            temperature_2m=25.0,
            precipitation=0.0,
            wind_speed_10m=15.0,
            wind_gusts_10m=20.0,
            visibility=10000.0,
            cloud_cover=50.0,
            relative_humidity_2m=60.0,
        )
        assert w.airport_code == "LEMD"
        assert w.cloud_cover == 50.0

    def test_schedule_document(self) -> None:
        """ScheduleDocument with all fields."""
        s = ScheduleDocument(
            source="aerodatabox",
            callsign="IBE1234",
            flight_date="2025-06-15",
            departure_airport="LEMD",
            arrival_airport="LEBL",
            airline_name="Iberia",
        )
        assert s.source == "aerodatabox"
        assert s.departure_airport == "LEMD"

    def test_aircraft_document(self) -> None:
        """AircraftDocument with all fields."""
        a = AircraftDocument(
            icao24="ABCDEF1234",
            registration="EC-ABC",
            manufacturer="Airbus",
            model="A320",
            typecode="A320",
            first_flight_date="2020-01-15",
        )
        assert a.icao24 == "ABCDEF1234"
        assert a.first_flight_date == "2020-01-15"


# ===========================================================================
# GOLD LAYER
# ===========================================================================


class TestGoldLayer:
    """Happy-path construction for Gold-layer (PostgreSQL) models."""

    def test_gold_flight(self) -> None:
        """GoldFlight with all fields."""
        f = GoldFlight(
            icao24="ABCDEF",
            callsign="IBE1234",
            first_seen=NOW_UTC,
            last_seen=NOW_UTC,
            flight_date=TODAY_UTC,
            est_departure_airport="LEMD",
            est_arrival_airport="LEBL",
        )
        # Verify GoldFlightModel alias is same class
        assert type(f) is GoldFlightModel
        assert f.icao24 == "ABCDEF"

    def test_gold_aircraft(self) -> None:
        """GoldAircraft construction."""
        a = GoldAircraft(
            icao24="ABCDEF123456",
            typecode="A320",
            manufacturer="Airbus",
            operator="Iberia",
            registration="EC-ABC",
        )
        assert a.icao24 == "ABCDEF123456"

    def test_gold_weather(self) -> None:
        """GoldWeather construction."""
        w = GoldWeather(
            airport_code="LEMD",
            timestamp=NOW_UTC,
            flight_date=TODAY_UTC,
            temperature_2m=25.0,
        )
        assert w.airport_code == "LEMD"
        assert w.temperature_2m == 25.0

    def test_daily_airport_traffic(self) -> None:
        """DailyAirportTraffic construction."""
        d = DailyAirportTraffic(
            airport_code="LEMD",
            flight_date=TODAY_UTC,
            arrivals_count=100,
            departures_count=95,
        )
        assert d.arrivals_count == 100
        assert d.departures_count == 95

    def test_route_density(self) -> None:
        """RouteDensity construction."""
        r = RouteDensity(
            departure_airport="LEMD",
            arrival_airport="LEBL",
            flight_count=42,
            first_seen=TODAY_UTC,
            last_seen=TODAY_UTC,
        )
        assert r.flight_count == 42

    def test_hourly_distribution(self) -> None:
        """HourlyDistribution construction."""
        h = HourlyDistribution(
            airport_code="LEMD",
            flight_date=TODAY_UTC,
            hour=14,
            arrivals_count=10,
            departures_count=12,
        )
        assert h.hour == 14

    def test_feature_store_row(self) -> None:
        """FeatureStoreRow with typical ML features."""
        fs = FeatureStoreRow(
            icao24="ABCDEF",
            flight_date=TODAY_UTC,
            callsign="IBE1234",
            departure_airport="LEMD",
            arrival_airport="LEBL",
            delay_minutes=15.0,
            airborne_minutes=120.0,
            departure_hour=10,
            day_of_week=1,
            month=6,
        )
        assert fs.delay_minutes == 15.0
        assert fs.airborne_minutes == 120.0


# ===========================================================================
# VALIDATION ERRORS
# ===========================================================================


class TestValidationErrors:
    """Every constraint type must raise a clear ValidationError."""

    def test_invalid_icao24(self) -> None:
        """icao24 must be 6 hex digits."""
        with pytest.raises(ValidationError, match="must be 6 hex digits"):
            FlightDocument(icao24="invalid")

    def test_invalid_icao24_short(self) -> None:
        """icao24 shorter than 6 characters."""
        with pytest.raises(ValidationError, match="must be 6 hex digits"):
            FlightDocument(icao24="AB")

    def test_invalid_airport_code(self) -> None:
        """Airport code must be 4 uppercase letters."""
        with pytest.raises(ValidationError, match="Invalid airport code"):
            GoldFlight(icao24="ABCDEF", est_departure_airport="1234")

    def test_negative_distance(self) -> None:
        """Distance fields must be non-negative."""
        with pytest.raises(ValidationError, match="non-negative"):
            GoldFlight(
                icao24="ABCDEF",
                departure_airport_horiz_distance=-1.0,
            )

    def test_invalid_latitude(self) -> None:
        """Latitude must be -90 to 90."""
        with pytest.raises(ValidationError, match="Latitude must be between"):
            StateVector(icao24="ABCDEF", latitude=100.0)

    def test_invalid_longitude(self) -> None:
        """Longitude must be -180 to 180."""
        with pytest.raises(ValidationError, match="Longitude must be between"):
            StateVector(icao24="ABCDEF", longitude=200.0)

    def test_invalid_hour(self) -> None:
        """Hour must be 0-23."""
        with pytest.raises(ValidationError, match="Hour must be 0-23"):
            HourlyDistribution(
                airport_code="LEMD",
                flight_date=TODAY_UTC,
                hour=24,
            )

    def test_cloud_cover_percentage(self) -> None:
        """Cloud cover must be 0-100."""
        with pytest.raises(ValidationError, match="Percentage must be"):
            WeatherDocument(airport_code="LEMD", cloud_cover=150.0)

    def test_humidity_percentage(self) -> None:
        """Relative humidity must be 0-100."""
        with pytest.raises(ValidationError, match="Percentage must be"):
            WeatherDocument(airport_code="LEMD", relative_humidity_2m=-10.0)

    def test_schedule_source(self) -> None:
        """Schedule source must be aerodatabox or aviationstack."""
        with pytest.raises(ValidationError, match="Unknown schedule source"):
            ScheduleDocument(source="unknown_provider")

    def test_feature_store_day_of_week(self) -> None:
        """day_of_week must be 1-7."""
        with pytest.raises(ValidationError, match="day_of_week must be 1-7"):
            FeatureStoreRow(
                icao24="ABCDEF",
                flight_date=TODAY_UTC,
                day_of_week=0,
            )

    def test_feature_store_month(self) -> None:
        """month must be 1-12."""
        with pytest.raises(ValidationError, match="month must be 1-12"):
            FeatureStoreRow(
                icao24="ABCDEF",
                flight_date=TODAY_UTC,
                month=13,
            )


# ===========================================================================
# SERIALIZATION
# ===========================================================================


class TestSerialization:
    """JSON round-trip for every layer."""

    def test_opensky_flight_roundtrip(self) -> None:
        """OpenSkyFlight serializes and deserializes."""
        f = OpenSkyFlight(
            icao24="ABCDEF",
            callsign="IBE1234",
            first_seen=NOW_UTC,
            est_departure_airport="LEMD",
        )
        json_str = f.model_dump_json()
        recovered = OpenSkyFlight.model_validate_json(json_str)
        assert recovered == f

    def test_flight_document_roundtrip(self) -> None:
        """FlightDocument serializes and deserializes."""
        d = FlightDocument(
            icao24="ABCDEF",
            callsign="IBE1234",
            first_seen=NOW_UTC,
            flight_date=TODAY_UTC,
        )
        json_str = d.model_dump_json()
        recovered = FlightDocument.model_validate_json(json_str)
        assert recovered == d

    def test_weather_document_roundtrip(self) -> None:
        """WeatherDocument serializes and deserializes."""
        w = WeatherDocument(
            airport_code="LEMD",
            timestamp=NOW_UTC,
            temperature_2m=22.5,
        )
        json_str = w.model_dump_json()
        recovered = WeatherDocument.model_validate_json(json_str)
        assert recovered == w

    def test_feature_store_roundtrip(self) -> None:
        """FeatureStoreRow serializes and deserializes."""
        fs = FeatureStoreRow(
            icao24="ABCDEF",
            flight_date=TODAY_UTC,
            delay_minutes=15.0,
            airborne_minutes=120.0,
            departure_hour=10,
            day_of_week=1,
            month=6,
        )
        json_str = fs.model_dump_json()
        recovered = FeatureStoreRow.model_validate_json(json_str)
        assert recovered == fs

    def test_json_keys(self) -> None:
        """Serialized JSON has snake_case keys (not camelCase)."""
        f = GoldFlight(icao24="ABCDEF", est_departure_airport="LEMD")
        json_str = f.model_dump_json()
        assert "est_departure_airport" in json_str
        assert "estDepartureAirport" not in json_str


# ===========================================================================
# FROZEN / IMMUTABILITY
# ===========================================================================


class TestFrozen:
    """All models are frozen and reject attribute mutation."""

    def test_cannot_modify_field(self) -> None:
        """Setting a field on a frozen model raises ValidationError (pydantic v2)."""
        f = GoldFlight(icao24="ABCDEF")
        with pytest.raises((TypeError, ValidationError), match="frozen"):
            f.icao24 = "CHANGED"  # type: ignore[misc]

    def test_cannot_add_extra_field(self) -> None:
        """Extra fields are forbidden."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            GoldFlight(icao24="ABCDEF", unknown_field="x")  # type: ignore[call-arg]


# ===========================================================================
# NORMALIZATION
# ===========================================================================


class TestNormalization:
    """Field-level normalization (uppercasing, stripping, etc.)."""

    def test_icao24_uppercased(self) -> None:
        """Lowercase icao24 is normalized to uppercase."""
        f = GoldFlight(icao24="abcdef")
        assert f.icao24 == "ABCDEF"

    def test_airport_code_uppercased(self) -> None:
        """Lowercase airport code is normalized to uppercase."""
        f = GoldFlight(icao24="ABCDEF", est_departure_airport="lemd")
        assert f.est_departure_airport == "LEMD"

    def test_callsign_uppercased(self) -> None:
        """Lowercase callsign is normalized to uppercase."""
        f = FlightDocument(icao24="ABCDEF", callsign="ibe1234")
        assert f.callsign == "IBE1234"

    def test_timezone_awareness(self) -> None:
        """Naive datetime is converted to UTC-aware."""
        naive = datetime(2025, 6, 15, 12, 0, 0)
        f = GoldFlight(icao24="ABCDEF", first_seen=naive)
        assert f.first_seen is not None
        assert f.first_seen.tzinfo is not None
        assert f.first_seen.tzinfo.utcoffset(f.first_seen) is not None
