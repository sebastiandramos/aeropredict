"""Tests for FeatureStoreRow schema/model completeness.

Pure unit tests — no database connections, no fixtures.
Validates the Pydantic model contract at the schema layer.

Test groups:
- TestSchemaCompleteness: expected columns, frozen, extra-forbid
- TestFeatureRanges: valid/invalid values for constrained fields
- TestNullOptionalFields: None acceptance and required-only construction
- TestSerialization: model_dump() and model_dump_json() correctness

# allow: SIZE_OK — test file; 4 test classes each owning a distinct concern
# (schema, ranges, null handling, serialization) over the same model.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aeropredict.schemas import FeatureStoreRow

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"icao24", "flight_date"}

# Fields from the actual FeatureStoreRow model that should be present
EXPECTED_COLUMNS: set[str] = {
    "icao24",
    "flight_date",
    "callsign",
    "departure_airport",
    "arrival_airport",
    "delay_minutes",
    "airborne_minutes",
    "departure_hour",
    "day_of_week",
    "month",
    "aircraft_type",
    "aircraft_manufacturer",
    "aircraft_operator",
    "aircraft_age_years",
    "route_daily_traffic",
    "route_total_density",
    "departure_airport_hourly_traffic",
    "arrival_airport_hourly_traffic",
    "dep_temperature",
    "dep_precipitation",
    "dep_wind_speed",
    "dep_visibility",
    "arr_temperature",
    "arr_precipitation",
    "arr_wind_speed",
    "arr_visibility",
    "schedule_source",
    "created_at",
}

VALID_DATE = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

# ===================================================================
# TestSchemaCompleteness
# ===================================================================


class TestSchemaCompleteness:
    """Verify FeatureStoreRow schema contains all expected columns and configuration."""

    def test_all_expected_columns_present(self) -> None:
        """FeatureStoreRow model fields match the expected column set.

        Given: The FeatureStoreRow Pydantic model
        When: Inspecting its model_fields
        Then: All expected columns are present, with no unexpected fields
        """
        actual = set(FeatureStoreRow.model_fields.keys())
        missing = EXPECTED_COLUMNS - actual
        extra = actual - EXPECTED_COLUMNS
        assert not missing, f"Missing expected columns: {missing}"
        assert not extra, f"Unexpected columns present: {extra}"

    def test_model_fields_count(self) -> None:
        """FeatureStoreRow has exactly 28 fields per schema definition.

        Given: The FeatureStoreRow Pydantic model
        When: Counting model_fields
        Then: 28 fields are present (2 required + 26 optional)
        """
        assert len(FeatureStoreRow.model_fields) == 28

    def test_model_is_frozen(self) -> None:
        """FeatureStoreRow instance is immutable after creation.

        Given: A constructed FeatureStoreRow instance
        When: Attempting to set a field value
        Then: A ValidationError (frozen instance) is raised
        """
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)
        with pytest.raises(ValidationError, match="Instance is frozen"):
            row.icao24 = "def456"

    def test_model_forbids_extra_fields(self) -> None:
        """FeatureStoreRow rejects fields not defined in the schema.

        Given: The FeatureStoreRow model with extra="forbid"
        When: Constructing with an unknown field
        Then: A ValidationError is raised
        """
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE, unknown_field="x")

    def test_required_fields_must_be_provided(self) -> None:
        """Required fields (icao24, flight_date) raise error when missing.

        Given: FeatureStoreRow requires icao24 and flight_date
        When: Constructing without icao24
        Then: A ValidationError is raised
        And: Constructing without flight_date
        Then: A ValidationError is raised
        """
        with pytest.raises(ValidationError, match="Field required"):
            FeatureStoreRow(icao24="abc123")  # missing flight_date

        with pytest.raises(ValidationError, match="Field required"):
            FeatureStoreRow(flight_date=VALID_DATE)  # missing icao24

    def test_icao24_validator_enforces_hex_format(self) -> None:
        """icao24 must be exactly 6 hex digits.

        Given: A FeatureStoreRow with an invalid icao24
        When: Validating
        Then: A ValidationError is raised
        """
        with pytest.raises(ValidationError, match="Invalid icao24"):
            FeatureStoreRow(icao24="nothex", flight_date=VALID_DATE)

        with pytest.raises(ValidationError, match="Invalid icao24"):
            FeatureStoreRow(icao24="abc1237", flight_date=VALID_DATE)  # 7 chars

        # Valid 6-char hex should work
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)
        assert row.icao24 == "ABC123"  # normalized to uppercase

    def test_airport_code_validator_rejects_invalid_format(self) -> None:
        """Airport codes must be 4 uppercase letters when provided.

        Given: A FeatureStoreRow with an invalid airport code
        When: Validating
        Then: A ValidationError is raised
        """
        with pytest.raises(ValidationError, match="Invalid airport code"):
            FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                departure_airport="1234",
            )

        # Valid 4-letter code works
        row = FeatureStoreRow(
            icao24="abc123",
            flight_date=VALID_DATE,
            departure_airport="LEMD",
        )
        assert row.departure_airport == "LEMD"


# ===================================================================
# TestFeatureRanges
# ===================================================================


class TestFeatureRanges:
    """Verify constrained feature fields reject out-of-range values."""

    def test_departure_hour_range(self) -> None:
        """departure_hour must be 0-23 when provided.

        Given: A FeatureStoreRow with departure_hour
        When: Value is outside [0, 23]
        Then: A ValidationError is raised
        And: Values within [0, 23] are accepted
        """
        for valid_hour in (0, 1, 12, 23):
            row = FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                departure_hour=valid_hour,
            )
            assert row.departure_hour == valid_hour

        for invalid_hour in (-1, 24, 100):
            with pytest.raises(ValidationError, match="departure_hour must be 0-23"):
                FeatureStoreRow(
                    icao24="abc123",
                    flight_date=VALID_DATE,
                    departure_hour=invalid_hour,
                )

    def test_day_of_week_range(self) -> None:
        """day_of_week must be 1-7 when provided (Python isoweekday convention).

        Note: The model uses isoweekday() where Monday=1, Sunday=7.
        This differs from some ML conventions (Monday=0) but matches
        Python's standard library.

        Given: A FeatureStoreRow with day_of_week
        When: Value is outside [1, 7]
        Then: A ValidationError is raised
        And: Values within [1, 7] are accepted
        """
        for valid_dow in (1, 2, 3, 4, 5, 6, 7):
            row = FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                day_of_week=valid_dow,
            )
            assert row.day_of_week == valid_dow

        for invalid_dow in (0, 8, -1):
            with pytest.raises(ValidationError, match="day_of_week must be 1-7"):
                FeatureStoreRow(
                    icao24="abc123",
                    flight_date=VALID_DATE,
                    day_of_week=invalid_dow,
                )

    def test_month_range(self) -> None:
        """month must be 1-12 when provided.

        Given: A FeatureStoreRow with month
        When: Value is outside [1, 12]
        Then: A ValidationError is raised
        And: Values within [1, 12] are accepted
        """
        for valid_month in (1, 6, 12):
            row = FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                month=valid_month,
            )
            assert row.month == valid_month

        for invalid_month in (0, 13, 100):
            with pytest.raises(ValidationError, match="month must be 1-12"):
                FeatureStoreRow(
                    icao24="abc123",
                    flight_date=VALID_DATE,
                    month=invalid_month,
                )

    def test_delay_minutes_non_negative(self) -> None:
        """delay_minutes must be >= 0 when provided.

        Given: A FeatureStoreRow with delay_minutes
        When: Value is negative
        Then: A ValidationError is raised
        And: Zero or positive values are accepted (including fractional)
        """
        for valid in (0.0, 0.5, 45.0, 180.0):
            row = FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                delay_minutes=valid,
            )
            assert row.delay_minutes == valid

        for invalid in (-0.1, -1.0, -100.0):
            with pytest.raises(ValidationError, match="Value must be non-negative"):
                FeatureStoreRow(
                    icao24="abc123",
                    flight_date=VALID_DATE,
                    delay_minutes=invalid,
                )

    def test_airborne_minutes_non_negative(self) -> None:
        """airborne_minutes must be >= 0 when provided.

        Given: A FeatureStoreRow with airborne_minutes
        When: Value is negative
        Then: A ValidationError is raised
        And: Zero or positive values are accepted
        """
        for valid in (0.0, 10.5, 135.0, 720.0):
            row = FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                airborne_minutes=valid,
            )
            assert row.airborne_minutes == valid

        for invalid in (-0.1, -5.0):
            with pytest.raises(ValidationError, match="Value must be non-negative"):
                FeatureStoreRow(
                    icao24="abc123",
                    flight_date=VALID_DATE,
                    airborne_minutes=invalid,
                )

    def test_aircraft_age_years_non_negative(self) -> None:
        """aircraft_age_years must be >= 0 when provided.

        Given: A FeatureStoreRow with aircraft_age_years
        When: Value is negative
        Then: A ValidationError is raised
        And: Zero or positive values are accepted
        """
        for valid in (0.0, 2.5, 15.0, 40.0):
            row = FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                aircraft_age_years=valid,
            )
            assert row.aircraft_age_years == valid

        for invalid in (-0.1, -10.0):
            with pytest.raises(ValidationError, match="Value must be non-negative"):
                FeatureStoreRow(
                    icao24="abc123",
                    flight_date=VALID_DATE,
                    aircraft_age_years=invalid,
                )

    def test_schedule_source_restricts_values(self) -> None:
        """schedule_source must be one of the allowed sources when provided.

        Given: A FeatureStoreRow with schedule_source
        When: Value is not in {aerodatabox, aviationstack}
        Then: A ValidationError is raised
        And: Valid sources are accepted (case-insensitive)
        """
        for valid_source in ("aerodatabox", "aviationstack", "AeroDataBox", "AviationStack"):
            row = FeatureStoreRow(
                icao24="abc123",
                flight_date=VALID_DATE,
                schedule_source=valid_source,
            )
            assert row.schedule_source == valid_source.lower()

        for invalid_source in ("opensky", "flightradar", "unknown"):
            with pytest.raises(ValidationError, match="Unknown schedule source"):
                FeatureStoreRow(
                    icao24="abc123",
                    flight_date=VALID_DATE,
                    schedule_source=invalid_source,
                )

    def test_departure_hour_none_when_unknown(self) -> None:
        """departure_hour accepts None to represent unknown departure time.

        Given: A FeatureStoreRow with departure_hour=None (default)
        When: Validating
        Then: No error; the field is None
        """
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)
        assert row.departure_hour is None


# ===================================================================
# TestNullOptionalFields
# ===================================================================


class TestNullOptionalFields:
    """Verify optional fields accept None and required-only construction works."""

    def test_optional_fields_default_to_none(self) -> None:
        """All optional fields default to None when not provided.

        Given: A FeatureStoreRow with only required fields
        When: Inspecting optional field values
        Then: All optional fields are None
        """
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)

        optional_fields = EXPECTED_COLUMNS - REQUIRED_FIELDS
        for field in sorted(optional_fields):  # sorted for deterministic order
            val = getattr(row, field)
            assert val is None, f"Optional field '{field}' should be None, got {val!r}"

    def test_optional_fields_accept_explicit_none(self) -> None:
        """Optional fields accept explicit None assignment.

        Given: A FeatureStoreRow with explicit None for optional fields
        When: Validating
        Then: All optional fields remain None
        """
        row = FeatureStoreRow(
            icao24="abc123",
            flight_date=VALID_DATE,
            callsign=None,
            departure_airport=None,
            arrival_airport=None,
            delay_minutes=None,
            airborne_minutes=None,
            departure_hour=None,
            day_of_week=None,
            month=None,
        )

        assert row.callsign is None
        assert row.departure_airport is None
        assert row.arrival_airport is None
        assert row.delay_minutes is None
        assert row.airborne_minutes is None
        assert row.departure_hour is None
        assert row.day_of_week is None
        assert row.month is None

    def test_optional_string_fields_accept_values(self) -> None:
        """Optional string fields properly store provided values.

        Given: A FeatureStoreRow with values for optional string fields
        When: Inspecting field values
        Then: Values match what was provided
        """
        row = FeatureStoreRow(
            icao24="abc123",
            flight_date=VALID_DATE,
            callsign="IBE1234",
            departure_airport="LEMD",
            arrival_airport="LEBL",
            aircraft_type="A320",
            aircraft_manufacturer="Airbus",
            aircraft_operator="Iberia",
            schedule_source="aerodatabox",
        )

        assert row.callsign == "IBE1234"
        assert row.departure_airport == "LEMD"
        assert row.arrival_airport == "LEBL"
        assert row.aircraft_type == "A320"
        assert row.aircraft_manufacturer == "Airbus"
        assert row.aircraft_operator == "Iberia"
        assert row.schedule_source == "aerodatabox"

    def test_weather_fields_all_optional(self) -> None:
        """All weather-related fields are optional and default to None.

        Given: A FeatureStoreRow without weather data
        When: Inspecting weather field values
        Then: All dep_* and arr_* weather fields are None
        """
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)

        weather_fields = [
            "dep_temperature", "dep_precipitation", "dep_wind_speed", "dep_visibility",
            "arr_temperature", "arr_precipitation", "arr_wind_speed", "arr_visibility",
        ]
        for field in weather_fields:
            assert getattr(row, field) is None, (
                f"Weather field '{field}' should default to None"
            )

    def test_weather_fields_accept_values(self) -> None:
        """Weather fields properly store provided values.

        Given: A FeatureStoreRow with weather data
        When: Inspecting weather field values
        Then: Values match what was provided
        """
        row = FeatureStoreRow(
            icao24="abc123",
            flight_date=VALID_DATE,
            dep_temperature=22.5,
            dep_precipitation=0.0,
            dep_wind_speed=12.3,
            dep_visibility=10000.0,
            arr_temperature=25.0,
            arr_precipitation=0.5,
            arr_wind_speed=8.1,
            arr_visibility=8000.0,
        )

        assert row.dep_temperature == 22.5
        assert row.dep_precipitation == 0.0
        assert row.dep_wind_speed == 12.3
        assert row.dep_visibility == 10000.0
        assert row.arr_temperature == 25.0
        assert row.arr_precipitation == 0.5
        assert row.arr_wind_speed == 8.1
        assert row.arr_visibility == 8000.0

    def test_created_at_default_none(self) -> None:
        """created_at defaults to None (set by DB default NOW()).

        Given: A FeatureStoreRow without created_at
        When: Inspecting created_at
        Then: It is None (the DB fills it with NOW() on insert)
        """
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)
        assert row.created_at is None

    def test_validation_passes_with_only_required_fields(self) -> None:
        """Model construction succeeds with only icao24 and flight_date.

        Given: Only required fields are provided
        When: Constructing a FeatureStoreRow
        Then: Validation succeeds without errors
        """
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)
        assert row.icao24 == "ABC123"  # normalized
        assert row.flight_date == VALID_DATE


# ===================================================================
# TestSerialization
# ===================================================================


class TestSerialization:
    """Verify model serialization methods produce correct output."""

    def test_model_dump_returns_dict_with_all_keys(self) -> None:
        """model_dump() returns a dict containing all expected columns.

        Given: A populated FeatureStoreRow
        When: Calling model_dump()
        Then: The returned dict has all expected keys with correct values
        """
        row = FeatureStoreRow(
            icao24="abc123",
            flight_date=VALID_DATE,
            callsign="IBE1234",
            departure_airport="LEMD",
            arrival_airport="LEBL",
            delay_minutes=45.0,
            airborne_minutes=135.0,
            departure_hour=12,
            day_of_week=1,
            month=6,
        )
        data = row.model_dump()

        assert isinstance(data, dict)
        assert data["icao24"] == "ABC123"
        assert data["callsign"] == "IBE1234"
        assert data["departure_hour"] == 12
        assert data["day_of_week"] == 1
        assert data["month"] == 6
        assert data["delay_minutes"] == 45.0
        assert data["airborne_minutes"] == 135.0

        # Verify all expected keys are in the dict
        for key in EXPECTED_COLUMNS:
            assert key in data, f"Key '{key}' missing from model_dump()"

    def test_model_dump_optional_fields_none(self) -> None:
        """model_dump() includes None values for unset optional fields.

        Given: A FeatureStoreRow with only required fields
        When: Calling model_dump()
        Then: Optional fields are included with None values
        """
        row = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE)
        data = row.model_dump()

        optional_unset = EXPECTED_COLUMNS - REQUIRED_FIELDS
        for field in optional_unset:
            assert field in data, f"Field '{field}' missing from model_dump()"
            assert data[field] is None, f"Field '{field}' should be None, got {data[field]!r}"

    def test_model_dump_json_produces_valid_json(self) -> None:
        """model_dump_json() returns a valid JSON string.

        Given: A populated FeatureStoreRow
        When: Calling model_dump_json()
        Then: The result is parseable JSON with correct values
        """
        row = FeatureStoreRow(
            icao24="abc123",
            flight_date=VALID_DATE,
            callsign="IBE1234",
        )
        json_str = row.model_dump_json()

        parsed = json.loads(json_str)
        assert parsed["icao24"] == "ABC123"
        assert parsed["callsign"] == "IBE1234"
        # flight_date should be serialized as ISO string
        assert "2025-06-15" in parsed["flight_date"]

    def test_model_dump_json_round_trip(self) -> None:
        """model_dump_json() → json.loads() → model_validate() round-trips.

        Given: A FeatureStoreRow with mixed field types
        When: Serializing to JSON and back
        Then: All field values are preserved
        """
        original = FeatureStoreRow(
            icao24="abc123",
            flight_date=VALID_DATE,
            callsign="IBE1234",
            delay_minutes=15.5,
            airborne_minutes=90.0,
            departure_hour=10,
            day_of_week=3,
            month=6,
            aircraft_type="B738",
        )
        json_str = original.model_dump_json()
        parsed = json.loads(json_str)

        # Round-trip back through the model
        restored = FeatureStoreRow.model_validate(parsed)
        assert restored.icao24 == original.icao24
        assert restored.callsign == original.callsign
        assert restored.delay_minutes == original.delay_minutes
        assert restored.airborne_minutes == original.airborne_minutes
        assert restored.departure_hour == original.departure_hour
        assert restored.day_of_week == original.day_of_week
        assert restored.month == original.month
        assert restored.aircraft_type == original.aircraft_type

    def test_model_copy_creates_new_instance(self) -> None:
        """model_copy(update=...) returns a new instance without mutating the original.

        Given: A FeatureStoreRow instance (frozen=True)
        When: Calling model_copy(update={'callsign': 'NEW123'})
        Then: A new instance is returned with the updated field
        And: The original instance is unchanged
        """
        original = FeatureStoreRow(icao24="abc123", flight_date=VALID_DATE, callsign="OLD")
        copied = original.model_copy(update={"callsign": "NEW123"})

        assert copied.callsign == "NEW123"
        assert original.callsign == "OLD"
        assert copied.icao24 == original.icao24
        assert copied.flight_date == original.flight_date
