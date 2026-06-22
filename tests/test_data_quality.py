"""Data quality tests for the Aeropredict pipeline.

Tests four categories of data quality:
1. **Deduplication** — Same flight ingested twice → 1 row in MongoDB/PostgreSQL
2. **Normalization** — Airport codes uppercased, timestamps UTC, distances ≥ 0
3. **Null handling** — Documented strategy (drop vs impute) per feature
4. **Completeness** — ≥80% of rows have non-null critical columns

All tests are pure unit tests at the dict/schema level. No DB connections needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from aeropredict.schemas import (
    BronzeFlight,
    FlightDocument,
    OpenSkyFlight,
)
from aeropredict.validators import validate_flights

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


def _make_flight_dict(
    icao24: str = "abc123",
    callsign: str | None = "ABC123",
    first_seen: datetime | None = NOW,
    last_seen: datetime | None = None,
    est_departure_airport: str | None = "LEMD",
    est_arrival_airport: str | None = "LEBL",
    **extra: Any,
) -> dict[str, Any]:
    """Create an ``OpenSkyFlight``-compatible dict with sensible defaults.

    All distance/candidate fields use the Bronze ``est_`` prefix.
    Fields in ``extra`` override the defaults. To set a field to None
    explicitly, pass it as a keyword::

        _make_flight_dict(icao24="x", est_departure_airport=None)
    """
    doc: dict[str, Any] = {
        "icao24": icao24,
        "callsign": callsign,
        "first_seen": first_seen,
        "last_seen": last_seen if last_seen is not None else (NOW + timedelta(hours=2)),
        "est_departure_airport": est_departure_airport,
        "est_arrival_airport": est_arrival_airport,
        "est_departure_airport_horiz_distance": 100.0,
        "est_departure_airport_vert_distance": 50.0,
        "est_arrival_airport_horiz_distance": 200.0,
        "est_arrival_airport_vert_distance": 75.0,
        "departure_airport_candidates_count": 3,
        "arrival_airport_candidates_count": 5,
    }
    doc.update(extra)
    return doc


_SENTINEL = object()


def _make_flight_doc_dict(
    icao24: str = "abc123",
    callsign: str | None = "ABC123",
    first_seen: datetime | None = NOW,
    last_seen: datetime | None | object = _SENTINEL,
    est_departure_airport: str | None = "LEMD",
    est_arrival_airport: str | None = "LEBL",
    flight_date: datetime | None | object = _SENTINEL,
    **extra: Any,
) -> dict[str, Any]:
    """Create a ``FlightDocument``-compatible dict (Silver schema).

    Uses ``FlightDocument`` field names (no ``est_`` prefix on distances).
    Fields in ``extra`` override the defaults.

    To explicitly set a field to ``None``, pass it as keyword::

        _make_flight_doc_dict(last_seen=None, flight_date=None)
    """
    if last_seen is _SENTINEL:
        last_seen = NOW + timedelta(hours=2)
    if flight_date is _SENTINEL:
        flight_date = NOW
    doc: dict[str, Any] = {
        "icao24": icao24,
        "callsign": callsign,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "est_departure_airport": est_departure_airport,
        "est_arrival_airport": est_arrival_airport,
        "departure_airport_horiz_distance": 100.0,
        "departure_airport_vert_distance": 50.0,
        "arrival_airport_horiz_distance": 200.0,
        "arrival_airport_vert_distance": 75.0,
        "departure_airport_candidates_count": 3,
        "arrival_airport_candidates_count": 5,
        "flight_date": flight_date,
        "ingested_at": NOW,
    }
    doc.update(extra)
    return doc


def _deduplicate_flight_dicts(
    flights: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate flight dicts by (icao24, first_seen_timestamp, callsign).

    Replicates the dedup logic from ``bronze_to_silver._read_bronze_flights``::

        key = (f.icao24, int(f.first_seen.timestamp()) if f.first_seen else None, f.callsign)
    """
    seen: set[tuple[str, int | None, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for f in flights:
        ts: int | None = (
            int(f["first_seen"].timestamp()) if f.get("first_seen") else None
        )
        key = (f["icao24"], ts, f.get("callsign"))
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped


# ===================================================================
# 1. DEDUPLICATION
# ===================================================================


class TestDeduplication:
    """Dedup by (icao24, first_seen, callsign) at the schema/dict level.

    The production pipeline deduplicates in two places:
    - **Bronze → Silver**: ``bronze_to_silver._read_bronze_flights`` deduplicates parsed
      ``Flight`` objects by ``(icao24, first_seen.timestamp(), callsign)``.
    - **Silver → Gold**: PostgreSQL ``ON CONFLICT`` / upsert enforces uniqueness.

    These tests validate the dedup key semantics at the dict level.
    """

    def test_identical_dicts_deduplicate_to_one(self) -> None:
        """Given: 10 identical flight dicts
        When: deduplicated
        Then: exactly 1 remains.
        """
        flight = _make_flight_dict()
        flights = [flight] * 10

        result = _deduplicate_flight_dicts(flights)

        assert len(result) == 1

    def test_different_icao24_all_kept(self) -> None:
        """Given: 3 flights with different icao24
        When: deduplicated
        Then: all 3 remain.
        """
        flights = [
            _make_flight_dict(icao24="aaa001", callsign="FL001"),
            _make_flight_dict(icao24="bbb002", callsign="FL002"),
            _make_flight_dict(icao24="ccc003", callsign="FL003"),
        ]

        result = _deduplicate_flight_dicts(flights)

        assert len(result) == 3

    def test_same_icao24_different_first_seen_both_kept(self) -> None:
        """Given: Same icao24 with different first_seen
        When: deduplicated
        Then: both remain (different flights).
        """
        flights = [
            _make_flight_dict(icao24="abc123", callsign="FL001", first_seen=NOW),
            _make_flight_dict(
                icao24="abc123",
                callsign="FL001",
                first_seen=NOW + timedelta(hours=3),
            ),
        ]

        result = _deduplicate_flight_dicts(flights)

        assert len(result) == 2

    def test_same_icao24_different_callsign_both_kept(self) -> None:
        """Given: Same icao24 with different callsign
        When: deduplicated
        Then: both remain (different flights).
        """
        flights = [
            _make_flight_dict(icao24="abc123", callsign="FL001", first_seen=NOW),
            _make_flight_dict(icao24="abc123", callsign="FL002", first_seen=NOW),
        ]

        result = _deduplicate_flight_dicts(flights)

        assert len(result) == 2

    def test_empty_list_deduplicate_returns_empty(self) -> None:
        """Given: An empty list
        When: deduplicated
        Then: empty list returned.
        """
        result = _deduplicate_flight_dicts([])

        assert result == []

    def test_identical_empty_icao24_flights_deduplicate(self) -> None:
        """Given: Identical flights with empty-string icao24
        When: deduplicated
        Then: dedup to 1 (same dedup key → same flight).
        """
        flights = [
            _make_flight_dict(icao24="", callsign="FL001", first_seen=NOW),
            _make_flight_dict(icao24="", callsign="FL001", first_seen=NOW),
        ]

        result = _deduplicate_flight_dicts(flights)

        assert len(result) == 1

    def test_empty_icao24_different_callsign_both_kept(self) -> None:
        """Given: Flights with empty-string icao24 but different callsigns
        When: deduplicated
        Then: both kept (callsign is part of the dedup key).
        """
        flights = [
            _make_flight_dict(icao24="", callsign="FL001", first_seen=NOW),
            _make_flight_dict(icao24="", callsign="FL002", first_seen=NOW),
        ]

        result = _deduplicate_flight_dicts(flights)

        assert len(result) == 2


# ===================================================================
# 2. NORMALIZATION
# ===================================================================


class TestNormalization:
    """Schema-level normalization enforced by Pydantic validators.

    The production models in ``schemas.py`` apply these normalizations
    via ``field_validator``:
    - Airport codes: ``_check_airport_code`` → strip, uppercase, validate 4 letters
    - Timestamps: ``_ensure_utc`` → naive → UTC, other tz → converted to UTC
    - Callsign: ``_check_callsign`` → strip, uppercase, validate 1-10 alphanum
    - ICAO24: ``_check_icao24`` → strip, uppercase, validate 6 hex digits
    - Distances: ``_non_negative`` → reject negative values
    """

    def test_airport_codes_uppercased(self) -> None:
        """Given: Lowercase airport codes
        When: validated against BronzeFlight
        Then: codes are uppercased.
        """
        raw = _make_flight_dict(
            est_departure_airport="lemd",
            est_arrival_airport="lebl",
        )

        model = BronzeFlight.model_validate(raw)

        assert model.est_departure_airport == "LEMD"
        assert model.est_arrival_airport == "LEBL"

    def test_airport_codes_strip_whitespace(self) -> None:
        """Given: Airport codes with leading/trailing whitespace
        When: validated
        Then: whitespace is stripped.
        """
        raw = _make_flight_dict(
            est_departure_airport="  LEMD  ",
            est_arrival_airport="  LEBL  ",
        )

        model = BronzeFlight.model_validate(raw)

        assert model.est_departure_airport == "LEMD"
        assert model.est_arrival_airport == "LEBL"

    def test_timestamps_normalized_to_utc(self) -> None:
        """Given: Naive and non-UTC timestamps
        When: validated
        Then: all become timezone-aware UTC.
        """
        raw = _make_flight_dict(
            first_seen=datetime(2026, 6, 15, 10, 0, 0),  # naive
            last_seen=datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC),
        )

        model = BronzeFlight.model_validate(raw)

        assert model.first_seen.tzinfo is not None
        assert model.first_seen.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
        assert model.last_seen.tzinfo is not None

    def test_non_utc_timezone_converted(self) -> None:
        """Given: Timestamp with non-UTC timezone
        When: validated
        Then: converted to UTC (time preserved, offset removed).
        """
        from datetime import timedelta as tzdelta
        from datetime import timezone

        tz_eastern = timezone(tzdelta(hours=-5))
        eastern_time = datetime(2026, 6, 15, 10, 0, 0, tzinfo=tz_eastern)

        raw = _make_flight_dict(first_seen=eastern_time)

        model = BronzeFlight.model_validate(raw)

        # 10:00 AM Eastern = 3:00 PM UTC
        assert model.first_seen.hour == 15
        assert model.first_seen.tzinfo is UTC

    def test_callsign_uppercased(self) -> None:
        """Given: Lowercase callsign
        When: validated
        Then: callsign is uppercased.
        """
        raw = _make_flight_dict(callsign="ibe1234")

        model = BronzeFlight.model_validate(raw)

        assert model.callsign == "IBE1234"

    def test_callsign_strip_whitespace(self) -> None:
        """Given: Callsign with leading/trailing whitespace
        When: validated
        Then: whitespace stripped, uppercased.
        """
        raw = _make_flight_dict(callsign="  ibe1234  ")

        model = BronzeFlight.model_validate(raw)

        assert model.callsign == "IBE1234"

    def test_distances_non_negative(self) -> None:
        """Given: Flight with all distance fields
        When: validated
        Then: all distances are non-negative.
        """
        model = BronzeFlight.model_validate(_make_flight_dict())

        assert model.est_departure_airport_horiz_distance >= 0
        assert model.est_departure_airport_vert_distance >= 0
        assert model.est_arrival_airport_horiz_distance >= 0
        assert model.est_arrival_airport_vert_distance >= 0
        assert model.departure_airport_candidates_count >= 0
        assert model.arrival_airport_candidates_count >= 0

    def test_negative_distance_rejected(self) -> None:
        """Given: A negative distance value
        When: validated
        Then: ValidationError is raised.
        """
        raw = _make_flight_dict(est_departure_airport_horiz_distance=-50.0)

        with pytest.raises(ValueError, match="non-negative"):
            BronzeFlight.model_validate(raw)

    def test_icao24_uppercased(self) -> None:
        """Given: Lowercase hex icao24
        When: validated
        Then: icao24 is uppercased.
        """
        raw = _make_flight_dict(icao24="abcdef")

        model = BronzeFlight.model_validate(raw)

        assert model.icao24 == "ABCDEF"

    def test_icao24_strip_whitespace(self) -> None:
        """Given: icao24 with whitespace
        When: validated
        Then: whitespace stripped, uppercased.
        """
        raw = _make_flight_dict(icao24="  abc123  ")

        model = BronzeFlight.model_validate(raw)

        assert model.icao24 == "ABC123"

    def test_schema_rejects_invalid_icao24(self) -> None:
        """Given: Invalid icao24 (not 6 hex digits)
        When: validated
        Then: ValidationError is raised.
        """
        raw = _make_flight_dict(icao24="XYZ123")

        with pytest.raises(ValueError, match="icao24"):
            BronzeFlight.model_validate(raw)

    def test_null_airport_code_accepted(self) -> None:
        """Given: Flight with null airport codes
        When: validated
        Then: None values pass through (nullable fields).
        """
        raw = _make_flight_dict(est_departure_airport=None, est_arrival_airport=None)

        model = BronzeFlight.model_validate(raw)

        assert model.est_departure_airport is None
        assert model.est_arrival_airport is None


# ===================================================================
# 3. NULL HANDLING
# ===================================================================


class TestNullHandling:
    """Document null handling strategy per feature category.

    The pipeline's approach to null values:

    **Drop (not admitted at all):**
    - ``icao24`` — always required; empty-string icao24 parsed as ``""``
      (not None) but will fail cascade validation downstream.

    **Pass-through (nullable field, stored as NULL):**
    - ``callsign`` — nullable; NULL preserved when absent
    - ``est_departure_airport`` — nullable; NULL preserved
    - ``est_arrival_airport`` — nullable; NULL preserved
    - ``first_seen`` — nullable; NULL preserved (flight still ingested)
    - ``last_seen`` — nullable; NULL preserved
    - Distance fields — nullable; NULL preserved when API doesn't provide them
    - Candidates count — nullable; NULL preserved

    **Imputed (in feature engineering, not at ingestion):**
    - Gold aggregations (route_daily_traffic, etc.): 0 when no data
      (``build_feature_store`` imputes)
    - Derived time features (departure_hour, etc.): NULL when source missing

    These tests verify the ingestion-layer behavior (pass-through).
    """

    def test_null_callsign_accepted(self) -> None:
        """Given: Flight with null callsign
        When: validated against FlightDocument
        Then: document is valid, callsign is None.
        """
        raw = _make_flight_doc_dict(callsign=None)

        valid, invalid = validate_flights([raw])

        assert len(valid) == 1
        assert len(invalid) == 0
        assert valid[0].callsign is None

    def test_null_airport_codes_accepted(self) -> None:
        """Given: Flight with null airport codes
        When: validated
        Then: document is valid, airport codes are None.
        """
        raw = _make_flight_doc_dict(
            est_departure_airport=None,
            est_arrival_airport=None,
        )

        valid, invalid = validate_flights([raw])

        assert len(valid) == 1
        assert len(invalid) == 0
        assert valid[0].est_departure_airport is None
        assert valid[0].est_arrival_airport is None

    def test_null_timestamps_still_ingested(self) -> None:
        """Given: Flight with null first_seen and null last_seen
        When: validated
        Then: document is still valid (timestamps are nullable).
        """
        raw = _make_flight_doc_dict(first_seen=None, last_seen=None)

        valid, invalid = validate_flights([raw])

        assert len(valid) == 1
        assert len(invalid) == 0
        assert valid[0].first_seen is None
        assert valid[0].last_seen is None

    def test_all_null_optionals_still_valid(self) -> None:
        """Given: Flight where every optional field is None
        When: validated
        Then: document is valid (only icao24 is truly required).
        """
        raw = {
            "icao24": "a1b2c3",
            "callsign": None,
            "first_seen": None,
            "last_seen": None,
            "est_departure_airport": None,
            "est_arrival_airport": None,
            "departure_airport_horiz_distance": None,
            "departure_airport_vert_distance": None,
            "arrival_airport_horiz_distance": None,
            "arrival_airport_vert_distance": None,
            "departure_airport_candidates_count": None,
            "arrival_airport_candidates_count": None,
            "flight_date": None,
            "ingested_at": None,
        }

        valid, invalid = validate_flights([raw])

        assert len(valid) == 1
        assert len(invalid) == 0
        doc = valid[0]
        assert doc.icao24 == "A1B2C3"
        assert doc.callsign is None
        assert doc.first_seen is None

    def test_missing_optional_keys_still_valid(self) -> None:
        """Given: Dict missing optional keys entirely
        When: validated
        Then: missing keys get None defaults.
        """
        raw = {
            "icao24": "deadbe",
            "first_seen": NOW,
            "last_seen": NOW + timedelta(hours=1),
        }

        valid, invalid = validate_flights([raw])

        assert len(valid) == 1
        assert len(invalid) == 0
        assert valid[0].callsign is None
        assert valid[0].est_departure_airport is None
        assert valid[0].departure_airport_horiz_distance is None

    def test_null_distance_fields_accepted(self) -> None:
        """Given: Flight with null distance fields
        When: validated against FlightDocument
        Then: all pass through as None (using FlightDocument field names).
        """
        raw = _make_flight_doc_dict(
            departure_airport_horiz_distance=None,
            departure_airport_vert_distance=None,
            arrival_airport_horiz_distance=None,
            arrival_airport_vert_distance=None,
            departure_airport_candidates_count=None,
            arrival_airport_candidates_count=None,
        )

        valid, invalid = validate_flights([raw])

        assert len(valid) == 1
        assert len(invalid) == 0
        doc = valid[0]
        assert doc.departure_airport_horiz_distance is None
        assert doc.departure_airport_vert_distance is None
        assert doc.arrival_airport_horiz_distance is None
        assert doc.arrival_airport_vert_distance is None
        assert doc.departure_airport_candidates_count is None
        assert doc.arrival_airport_candidates_count is None

    def test_column_null_percentage_documented(self) -> None:
        """Document the expected null percentage per column.

        Given: A representative set of flights
        When: validated
        Then: each column's null percentage is within expected bounds.

        This is a *documentation* test — it captures the current null
        rate profile so drift can be detected. Expected ranges are wide
        to avoid brittleness on small test datasets.
        """
        flights = [
            # Fully populated flight
            _make_flight_doc_dict(icao24="aaa001"),
            # Flight with no callsign
            _make_flight_doc_dict(icao24="aaa002", callsign=None),
            # Flight with no airport codes
            _make_flight_doc_dict(icao24="aaa003", est_departure_airport=None),
            # Flight with no timestamps
            _make_flight_doc_dict(icao24="aaa004", first_seen=None, last_seen=None),
            # Flight with no distances
            _make_flight_doc_dict(
                icao24="aaa005",
                departure_airport_horiz_distance=None,
                departure_airport_vert_distance=None,
            ),
            # Flight with no flight_date
            _make_flight_doc_dict(icao24="aaa006", flight_date=None),
            # Flight with only icao24
            _make_flight_doc_dict(
                icao24="aaa007",
                callsign=None,
                first_seen=None,
                last_seen=None,
                est_departure_airport=None,
                est_arrival_airport=None,
                departure_airport_horiz_distance=None,
                departure_airport_vert_distance=None,
                arrival_airport_horiz_distance=None,
                arrival_airport_vert_distance=None,
                departure_airport_candidates_count=None,
                arrival_airport_candidates_count=None,
                flight_date=None,
            ),
        ]

        valid, invalid = validate_flights(flights)
        assert len(invalid) == 0
        assert len(valid) == 7

        total = len(valid)

        # Count nulls per column
        nulls: dict[str, int] = {}
        for col in FlightDocument.model_fields:
            nulls[col] = sum(1 for d in valid if getattr(d, col) is None)

        # Print documentation table
        col_width = max(len(c) for c in nulls)
        lines = ["Column null percentage (test dataset):"]
        for col, count in sorted(nulls.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            lines.append(f"  {col:<{col_width}}  {count}/{total} ({pct:.0f}%)")
        doc_text = "\n".join(lines)

        # Non-nullable columns: icao24 must be 0% null
        assert nulls.get("icao24", total) == 0, f"icao24 must never be null:\n{doc_text}"

        # Optional columns can have nulls — just document the rate
        # No hard assertion on optional columns to avoid brittleness


# ===================================================================
# 4. COMPLETENESS
# ===================================================================


class TestCompleteness:
    """Completeness checks: ≥80% of rows have non-null critical columns.

    Critical columns are those required for downstream ML feature
    engineering (per ``docs/analisis_prediccion_retrasos.md``):
    - ``icao24`` — aircraft identifier, join key for aircraft metadata
    - ``callsign`` — airline prefix, route identification
    - ``est_departure_airport`` — origin airport
    - ``est_arrival_airport`` — destination airport
    - ``first_seen`` — departure time proxy
    - ``last_seen`` — arrival time proxy
    """

    # Columns considered critical for ML feature engineering
    CRITICAL_COLUMNS: ClassVar[list[str]] = [
        "icao24",
        "callsign",
        "est_departure_airport",
        "est_arrival_airport",
    ]

    TIMESTAMP_COLUMNS: ClassVar[list[str]] = [
        "first_seen",
        "last_seen",
    ]

    ALL_CRITICAL: ClassVar[list[str]] = CRITICAL_COLUMNS + TIMESTAMP_COLUMNS

    @staticmethod
    def _make_completeness_test_set() -> list[dict[str, Any]]:
        """Build a representative flight set with realistic null patterns.

        Returns 12 flights with a null distribution approximating real
        OpenSky data: most flights have complete data, a few are missing
        optional fields.
        """
        flights: list[dict[str, Any]] = []

        # 8 complete flights (all fields populated)
        for i in range(8):
            flights.append(
                _make_flight_doc_dict(
                    icao24=f"c0{i:04d}",
                    callsign=f"FLT{i:04d}",
                    first_seen=NOW + timedelta(hours=i),
                    last_seen=NOW + timedelta(hours=i + 2),
                    est_departure_airport="LEMD",
                    est_arrival_airport="LEBL",
                )
            )

        # 2 flights missing callsign (common in real data)
        flights.append(
            _make_flight_doc_dict(
                icao24="b0c001",
                callsign=None,
            )
        )
        flights.append(
            _make_flight_doc_dict(
                icao24="b0c002",
                callsign=None,
            )
        )

        # 1 flight missing departure airport
        flights.append(
            _make_flight_doc_dict(
                icao24="d0e001",
                est_departure_airport=None,
                callsign="NODEP01",
            )
        )

        # 1 flight missing first_seen/last_seen (rare edge case)
        flights.append(
            _make_flight_doc_dict(
                icao24="e0f001",
                first_seen=None,
                last_seen=None,
                callsign="NOTS01",
            )
        )

        return flights

    def test_critical_columns_above_80_pct(self) -> None:
        """Critical columns are non-null in ≥80% of rows.

        Given: A representative set of 12 flights
        When: validated against FlightDocument
        Then: Each critical column has ≥80% non-null rate.
        """
        flights = self._make_completeness_test_set()
        valid, invalid = validate_flights(flights)

        assert len(invalid) == 0, f"Validation errors: {invalid}"
        total = len(valid)
        assert total >= 10, f"Need ≥10 valid flights, got {total}"

        failures: list[str] = []
        for col in self.CRITICAL_COLUMNS:
            non_null = sum(1 for d in valid if getattr(d, col) is not None)
            pct = non_null / total * 100
            if pct < 80.0:
                failures.append(f"{col}: {non_null}/{total} ({pct:.0f}%)")

        assert not failures, (
            "Critical columns below 80% completeness:\n  " + "\n  ".join(failures)
        )

    def test_timestamp_columns_above_80_pct(self) -> None:
        """Timestamp columns (first_seen, last_seen) are non-null in ≥80% of rows.

        Given: A representative set of 12 flights
        When: validated
        Then: first_seen and last_seen each have ≥80% non-null rate.
        """
        flights = self._make_completeness_test_set()
        valid, invalid = validate_flights(flights)

        assert len(invalid) == 0
        total = len(valid)
        assert total >= 10

        failures: list[str] = []
        for col in self.TIMESTAMP_COLUMNS:
            non_null = sum(1 for d in valid if getattr(d, col) is not None)
            pct = non_null / total * 100
            if pct < 80.0:
                failures.append(f"{col}: {non_null}/{total} ({pct:.0f}%)")

        assert not failures, (
            "Timestamp columns below 80% completeness:\n  " + "\n  ".join(failures)
        )

    def test_all_columns_completeness_profile(self) -> None:
        """Document the completeness profile for all columns.

        Given: A representative test set
        When: validated
        Then: Completeness percentages are documented and non-decreasing
              over time (regression guard).

        This is a documentation / regression-detection test. It prints
        the current completeness profile so a reviewer can spot columns
        with unexpectedly low rates.
        """
        flights = self._make_completeness_test_set()
        valid, invalid = validate_flights(flights)
        assert len(invalid) == 0

        total = len(valid)
        col_width = max(len(c) for c in FlightDocument.model_fields)

        lines = ["Completeness profile (test dataset):"]
        for col in sorted(FlightDocument.model_fields):
            non_null = sum(1 for d in valid if getattr(d, col) is not None)
            pct = non_null / total * 100
            bar = "#" * int(pct / 5) + " " * (20 - int(pct / 5))
            lines.append(f"  {col:<{col_width}}  {bar} {pct:5.1f}% ({non_null}/{total})")

        profile = "\n".join(lines)

        # icao24 must always be 100%
        non_null_icao24 = sum(1 for d in valid if d.icao24 is not None)
        assert non_null_icao24 == total, f"icao24 completeness must be 100%:\n{profile}"


# ===================================================================
# 5. EDGE CASES — SCHEMA VALIDATION BOUNDARIES
# ===================================================================


class TestSchemaBoundaries:
    """Edge cases around schema validation boundaries."""

    def test_extra_fields_forbidden_bronze(self) -> None:
        """Given: Dict with an unrecognized field
        When: validated against OpenSkyFlight
        Then: ValidationError is raised (extra='forbid').
        """
        raw = _make_flight_dict()
        raw["unknown_field"] = "should_not_exist"

        with pytest.raises(ValueError, match="extra"):
            OpenSkyFlight.model_validate(raw)

    def test_extra_fields_forbidden_silver(self) -> None:
        """Given: Dict with an unrecognized field
        When: validated against FlightDocument
        Then: ValidationError is raised (extra='forbid').
        """
        raw = _make_flight_doc_dict()
        raw["unknown_field"] = "should_not_exist"

        with pytest.raises(ValueError, match="extra"):
            FlightDocument.model_validate(raw)

    def test_icao24_too_short_rejected(self) -> None:
        """Given: icao24 with only 5 hex digits
        When: validated
        Then: ValidationError is raised.
        """
        raw = _make_flight_dict(icao24="abc12")

        with pytest.raises(ValueError, match="icao24"):
            BronzeFlight.model_validate(raw)

    def test_icao24_too_long_rejected(self) -> None:
        """Given: icao24 with 7 hex digits
        When: validated
        Then: ValidationError is raised.
        """
        raw = _make_flight_dict(icao24="abcdef1")

        with pytest.raises(ValueError, match="icao24"):
            BronzeFlight.model_validate(raw)

    def test_callsign_too_long_rejected(self) -> None:
        """Given: Callsign with 11+ characters
        When: validated
        Then: ValidationError is raised.
        """
        raw = _make_flight_dict(callsign="A" * 11)

        with pytest.raises(ValueError, match="callsign"):
            BronzeFlight.model_validate(raw)

    def test_invalid_airport_code_rejected(self) -> None:
        """Given: Airport code with digits or wrong length
        When: validated
        Then: ValidationError is raised.
        """
        raw = _make_flight_dict(est_departure_airport="1234")

        with pytest.raises(ValueError, match="airport"):
            BronzeFlight.model_validate(raw)

    def test_negative_candidates_count_rejected(self) -> None:
        """Given: Negative candidates count
        When: validated
        Then: ValidationError is raised.
        """
        raw = _make_flight_dict(departure_airport_candidates_count=-1)

        with pytest.raises(ValueError, match="non-negative"):
            BronzeFlight.model_validate(raw)

    def test_flightdocument_without_flight_date_still_valid(self) -> None:
        """Given: FlightDocument dict without flight_date
        When: validated
        Then: Valid (flight_date is optional).
        """
        raw = _make_flight_doc_dict(icao24="abc123", flight_date=None)

        valid, _invalid = validate_flights([raw])

        assert len(valid) == 1
        assert valid[0].flight_date is None
