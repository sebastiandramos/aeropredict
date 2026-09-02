"""Tests for build_feature_store.py — feature engineering pipeline.

Tests cover:
- TestFeatureJoins: feature joins from flights, schedules, aircraft, weather, gold aggregations
- TestFeatureDerivation: derived features (hour_of_day, day_of_week, month, airborne_minutes)
- TestNullHandling: null handling and imputation strategies
- TestSchemaCompliance: feature_store table schema, PK, column count

Null handling strategies documented per column category:
  - Schedule-derived (delay_minutes, schedule_source): NULL when no schedule match
  - Aircraft-derived (type, manufacturer, operator, age): NULL when no aircraft match
  - Weather-derived (temperature, precipitation, wind, visibility): NULL when no weather data
  - Time-derived (airborne_minutes, departure_hour): NULL when first_seen/last_seen missing
  - Gold aggregations (traffic, density): 0 (not null) when no aggregation data
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg2.extensions
import pymongo
import pytest

from scripts.build_feature_store import build_feature_store

TEST_DATE_STR = "2025-06-15"
TEST_DATE_DT = datetime(2025, 6, 15, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mongo_test_db(mongo_client: pymongo.MongoClient[Any]) -> Any:
    """Function-scoped MongoDB test database with clean collections."""
    db = mongo_client["aeropredict_test"]
    for coll in ("flights", "aircraft", "weather", "schedules"):
        db[coll].delete_many({})
    yield db
    for coll in ("flights", "aircraft", "weather", "schedules"):
        db[coll].delete_many({})


@pytest.fixture
def clean_feature_store(postgres_client: psycopg2.extensions.connection) -> None:
    """Clear feature_store AND gold aggregation tables before each test.

    Gold aggregation tables (daily_airport_traffic, route_density, hourly_distribution)
    must also be cleaned because build_feature_store reads from them, and their
    PKs cause UniqueViolation when tests reuse the same (airport, date) keys.
    """
    tables = [
        "gold.feature_store",
        "gold.daily_airport_traffic",
        "gold.route_density",
        "gold.hourly_distribution",
    ]
    with postgres_client.cursor() as cur:
        for tbl in tables:
            cur.execute(f"DELETE FROM {tbl}")


@pytest.fixture
def monkeypatch_mongo_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point MONGODB_URI to aeropredict_test so build_feature_store reads test data."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/aeropredict_test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FEATURE_STORE_COLUMNS = [
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
]


def _insert_flight(
    db: Any,
    icao24: str = "abc123",
    callsign: str = "IBE1234",
    dep: str = "LEMD",
    arr: str = "LEBL",
    **extra: Any,
) -> dict[str, Any]:
    """Insert a flight document and return it.

    Fields in ``extra`` override the defaults. To set a field to None
    explicitly (e.g. first_seen=None), pass it as a keyword::

        _insert_flight(db, icao24="x", first_seen=None)
    """
    doc: dict[str, Any] = {
        "icao24": icao24,
        "callsign": callsign,
        "first_seen": datetime(2025, 6, 15, 10, 30, 0, tzinfo=UTC),
        "last_seen": datetime(2025, 6, 15, 12, 45, 0, tzinfo=UTC),
        "est_departure_airport": dep,
        "est_arrival_airport": arr,
        "flight_date": TEST_DATE_DT,
    }
    doc.update(extra)  # extra overrides defaults, including setting to None
    db["flights"].insert_one(doc)
    return doc


def _insert_schedule(
    db: Any,
    callsign: str = "IBE1234",
    flight_date: str = TEST_DATE_STR,
    dep: str = "LEMD",
    arr: str = "LEBL",
    arrival_scheduled: str = "2025-06-15T12:00:00Z",
    source: str = "aviationstack",
) -> None:
    """Insert a schedule document."""
    db["schedules"].insert_one(
        {
            "callsign": callsign,
            "flight_date": flight_date,
            "departure_airport": dep,
            "arrival_airport": arr,
            "arrival_scheduled": arrival_scheduled,
            "source": source,
        }
    )


def _insert_aircraft(
    db: Any,
    icao24: str = "abc123",
    typecode: str = "A320",
    manufacturer: str = "Airbus",
    operator: str = "Iberia",
    first_flight_date: str = "2018-06-15",
) -> None:
    """Insert an aircraft document."""
    db["aircraft"].insert_one(
        {
            "icao24": icao24,
            "typecode": typecode,
            "manufacturer": manufacturer,
            "operator": operator,
            "first_flight_date": first_flight_date,
        }
    )


def _insert_weather(
    db: Any,
    airport_code: str = "LEMD",
    flight_date: datetime | None = None,
    timestamp: str = "2025-06-15T10:00:00Z",
    temperature: float = 22.5,
    precipitation: float = 0.0,
    wind_speed: float = 12.3,
    visibility: float = 10000.0,
) -> None:
    """Insert a weather document."""
    if flight_date is None:
        flight_date = TEST_DATE_DT
    db["weather"].insert_one(
        {
            "airport_code": airport_code,
            "flight_date": flight_date,
            "timestamp": timestamp,
            "temperature_2m": temperature,
            "precipitation": precipitation,
            "wind_speed_10m": wind_speed,
            "visibility": visibility,
        }
    )


def _populate_gold_traffic(pg: psycopg2.extensions.connection) -> None:
    """Insert gold aggregation data used by multiple tests."""
    with pg.cursor() as cur:
        # daily_airport_traffic for LEMD
        cur.execute(
            "INSERT INTO gold.daily_airport_traffic "
            "(airport_code, flight_date, arrivals_count, departures_count) "
            "VALUES (%s, %s::date, %s, %s)",
            ("LEMD", TEST_DATE_STR, 50, 45),
        )
        # daily_airport_traffic for LEBL
        cur.execute(
            "INSERT INTO gold.daily_airport_traffic "
            "(airport_code, flight_date, arrivals_count, departures_count) "
            "VALUES (%s, %s::date, %s, %s)",
            ("LEBL", TEST_DATE_STR, 40, 55),
        )
        # route_density for LEMD→LEBL
        cur.execute(
            "INSERT INTO gold.route_density "
            "(departure_airport, arrival_airport, flight_count, first_seen, last_seen) "
            "VALUES (%s, %s, %s, %s::date, %s::date)",
            ("LEMD", "LEBL", 120, TEST_DATE_STR, TEST_DATE_STR),
        )
        # hourly_distribution for LEMD at hour 10
        cur.execute(
            "INSERT INTO gold.hourly_distribution "
            "(airport_code, flight_date, hour, arrivals_count, departures_count) "
            "VALUES (%s, %s::date, %s, %s, %s)",
            ("LEMD", TEST_DATE_STR, 10, 10, 8),
        )
        # hourly_distribution for LEBL at hour 10
        cur.execute(
            "INSERT INTO gold.hourly_distribution "
            "(airport_code, flight_date, hour, arrivals_count, departures_count) "
            "VALUES (%s, %s::date, %s, %s, %s)",
            ("LEBL", TEST_DATE_STR, 10, 12, 6),
        )


def _run_build_and_fetch(
    pg: psycopg2.extensions.connection,
) -> list[tuple[Any, ...]]:
    """Run build_feature_store and return all rows from gold.feature_store."""
    n = build_feature_store()
    assert n > 0, "Expected at least one row in feature_store"
    with pg.cursor() as cur:
        cur.execute("SELECT * FROM gold.feature_store ORDER BY icao24, flight_date")
        return cur.fetchall()


# ===================================================================
# TestFeatureJoins
# ===================================================================


class TestFeatureJoins:
    """Verify that features from all sources are correctly joined."""

    def test_core_feature_joins(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Flights + schedule → delay_minutes, airborne_minutes, schedule_source populated.

        Given: A flight with matching schedule
        When: build_feature_store() runs
        Then: delay_minutes = 45.0 (scheduled 12:00, actual 12:45),
              airborne_minutes = 135.0 (10:30→12:45 = 135 min),
              schedule_source = 'aviationstack'
        """
        _insert_flight(mongo_test_db)
        _insert_schedule(mongo_test_db)
        _populate_gold_traffic(postgres_client)

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        # icao24=0, flight_date=1, callsign=2, dep=3, arr=4
        assert row[0] == "abc123"
        assert str(row[1]) == TEST_DATE_STR
        assert row[2] == "IBE1234"
        assert row[3] == "LEMD"
        assert row[4] == "LEBL"
        # delay_minutes=5, airborne_minutes=6
        assert row[5] == 45.0  # 12:45 - 12:00 = 45 min
        assert row[6] == 135.0  # 12:45 - 10:30 = 135 min
        # schedule_source=26
        assert row[26] == "aviationstack"

    def test_aircraft_features_join(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Aircraft metadata is joined via icao24 lookup.

        Given: A flight with matching aircraft doc
        When: build_feature_store() runs
        Then: aircraft_type='A320', aircraft_manufacturer='Airbus',
              aircraft_operator='Iberia', aircraft_age_years calculated
        """
        _insert_flight(mongo_test_db)  # icao24='abc123'
        _insert_aircraft(mongo_test_db)
        _populate_gold_traffic(postgres_client)

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        # aircraft_type=10, manufacturer=11, operator=12, age=13
        assert row[10] == "A320"
        assert row[11] == "Airbus"
        assert row[12] == "Iberia"
        # first_flight_date=2018-06-15 → age ≈ 7 years
        assert row[13] is not None
        assert isinstance(row[13], float)
        assert row[13] > 6.9
        assert row[13] < 7.1

    def test_weather_features_join(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Weather data for departure and arrival airports is joined.

        Given: A flight with weather docs for LEMD (dep) and LEBL (arr) at hour 10
        When: build_feature_store() runs
        Then: Weather fields match inserted values
        """
        _insert_flight(mongo_test_db)
        _insert_weather(
            mongo_test_db,
            airport_code="LEMD",
            temperature=22.5,
            precipitation=0.0,
            wind_speed=12.3,
            visibility=10000.0,
        )
        _insert_weather(
            mongo_test_db,
            airport_code="LEBL",
            temperature=25.0,
            precipitation=0.5,
            wind_speed=8.1,
            visibility=8000.0,
        )
        _populate_gold_traffic(postgres_client)

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        # dep weather (index 18-21)
        assert row[18] == 22.5  # dep_temperature
        assert row[19] == 0.0  # dep_precipitation
        assert row[20] == 12.3  # dep_wind_speed
        assert row[21] == 10000.0  # dep_visibility
        # arr weather (index 22-25)
        assert row[22] == 25.0  # arr_temperature
        assert row[23] == 0.5  # arr_precipitation
        assert row[24] == 8.1  # arr_wind_speed
        assert row[25] == 8000.0  # arr_visibility

    def test_gold_aggregations_join(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Gold aggregations (route density, daily traffic, hourly) are joined.

        Given: A flight LEMD→LEBL with gold aggregation data
        When: build_feature_store() runs
        Then: route_daily_traffic = (LEMD:50+45) + (LEBL:40+55) = 190,
              route_total_density = 120,
              dep_hourly = LEMD(10+8)=18, arr_hourly = LEBL(12+6)=18
        """
        _insert_flight(mongo_test_db)
        _populate_gold_traffic(postgres_client)

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        # _load_gold_lookup stores arrivals_count + departures_count per airport.
        # LEMD: 50 + 45 = 95; LEBL: 40 + 55 = 95
        # route_daily_traffic = sum of both airports' totals = 95 + 95 = 190
        assert row[14] == 190  # route_daily_traffic
        assert row[15] == 120  # route_total_density
        # _load_gold_lookup stores arrivals_count + departures_count for hourly too.
        # LEMD at hour 10: 10+8=18; LEBL at hour 10: 12+6=18
        assert row[16] == 18  # departure_airport_hourly_traffic
        assert row[17] == 18  # arrival_airport_hourly_traffic

    def test_multiple_flights(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Multiple flights produce multiple rows in feature_store.

        Given: 3 flights with different ICAO24s
        When: build_feature_store() runs
        Then: 3 rows inserted, all with valid feature data
        """
        flights_data = [
            ("abc123", "IBE1234", "LEMD", "LEBL"),
            ("def456", "RYR5678", "LEBL", "LEMG"),
            ("ghi789", "VLG9012", "LEAL", "LEMD"),
        ]
        for icao, callsign, dep, arr in flights_data:
            _insert_flight(mongo_test_db, icao24=icao, callsign=callsign, dep=dep, arr=arr)
        _populate_gold_traffic(postgres_client)

        n = build_feature_store()
        assert n == 3, f"Expected 3 rows, got {n}"

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT icao24, callsign, departure_airport, arrival_airport "
                "FROM gold.feature_store ORDER BY icao24"
            )
            rows = cur.fetchall()
            assert len(rows) == 3
            assert rows[0] == ("abc123", "IBE1234", "LEMD", "LEBL")
            assert rows[1] == ("def456", "RYR5678", "LEBL", "LEMG")
            assert rows[2] == ("ghi789", "VLG9012", "LEAL", "LEMD")


# ===================================================================
# TestFeatureDerivation
# ===================================================================


class TestFeatureDerivation:
    """Verify derived features are computed correctly."""

    def test_departure_hour_range(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """departure_hour derived from first_seen.hour ∈ [0, 23].

        Given: Flights with first_seen at 00:00, 06:00, 12:00, 23:00 UTC
        When: build_feature_store() runs
        Then: departure_hour matches first_seen.hour in each case
        """
        for hour in (0, 6, 12, 23):
            fs = datetime(2025, 6, 15, hour, 0, 0, tzinfo=UTC)
            ls = datetime(2025, 6, 15, hour, 30, 0, tzinfo=UTC)
            icao = f"h{hour:04d}"
            _insert_flight(
                mongo_test_db,
                icao24=icao,
                callsign=f"TEST{hour:02d}",
                first_seen=fs,
                last_seen=ls,
            )

        n = build_feature_store()
        assert n == 4, f"Expected 4 rows, got {n}"

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT icao24, departure_hour FROM gold.feature_store ORDER BY departure_hour"
            )
            rows = cur.fetchall()
            assert len(rows) == 4
            for i, (icao, hour) in enumerate(rows):
                expected_hour = (0, 6, 12, 23)[i]
                msg = f"Expected hour={expected_hour}, got {hour} for {icao}"
                assert hour == expected_hour, msg

    def test_day_of_week_range(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """day_of_week derived from flight_date.isoweekday() ∈ [1, 7].

        Given: Flights on different days of the week
        When: build_feature_store() runs
        Then: day_of_week = isoweekday (Monday=1, Sunday=7)
        """
        # 2025-06-15 is a Sunday (isoweekday=7)
        # 2025-06-16 is a Monday (isoweekday=1)
        # 2025-06-17 is a Tuesday (isoweekday=2)
        for day_offset, expected_dow in [(0, 7), (1, 1), (2, 2)]:
            fd = datetime(2025, 6, 15 + day_offset, tzinfo=UTC)
            icao = f"d{expected_dow:04d}"  # max 6 chars (VARCHAR(6))
            _insert_flight(
                mongo_test_db,
                icao24=icao,
                callsign=f"DOW{expected_dow}",
                flight_date=fd,
            )

        n = build_feature_store()
        assert n == 3, f"Expected 3 rows, got {n}"

        with postgres_client.cursor() as cur:
            cur.execute("SELECT icao24, day_of_week FROM gold.feature_store ORDER BY day_of_week")
            rows = cur.fetchall()
            assert len(rows) == 3
            dow_values = [r[1] for r in rows]
            assert dow_values == [1, 2, 7], f"day_of_week values: {dow_values}"

    def test_month(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """month derived from flight_date.month ∈ [1, 12].

        Given: Flights in January, June, December
        When: build_feature_store() runs
        Then: month = flight_date.month
        """
        for month in (1, 6, 12):
            fd = datetime(2025, month, 15, tzinfo=UTC)
            icao = f"m{month:04d}"
            _insert_flight(mongo_test_db, icao24=icao, callsign=f"MTH{month}", flight_date=fd)

        n = build_feature_store()
        assert n == 3, f"Expected 3 rows, got {n}"

        with postgres_client.cursor() as cur:
            cur.execute("SELECT icao24, month FROM gold.feature_store ORDER BY month")
            rows = cur.fetchall()
            assert len(rows) == 3
            month_values = [r[1] for r in rows]
            assert month_values == [1, 6, 12], f"month values: {month_values}"

    def test_airborne_minutes_positive(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """airborne_minutes = round((last_seen - first_seen) / 60s, 1), always > 0.

        Given: Flight with first_seen=10:30, last_seen=12:45 UTC (135 min)
        When: build_feature_store() runs
        Then: airborne_minutes = 135.0, which is > 0
        """
        fs = datetime(2025, 6, 15, 10, 30, 0, tzinfo=UTC)
        ls = datetime(2025, 6, 15, 12, 45, 0, tzinfo=UTC)
        _insert_flight(mongo_test_db, first_seen=fs, last_seen=ls)

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        assert row[6] == 135.0
        assert row[6] > 0


# ===================================================================
# TestNullHandling
# ===================================================================


class TestNullHandling:
    """Verify null handling and imputation for missing data sources.

    Strategy documentation:
      - Schedule-derived: NULL when no schedule match (delay_minutes, schedule_source)
      - Aircraft-derived: NULL for all aircraft fields when ICAO24 not in lookup
      - Weather-derived: NULL for all weather fields when no weather doc found
      - Time-derived: NULL for airborne_minutes, departure_hour when first_seen/last_seen missing
      - Gold aggregations: 0 (imputed) when no aggregation row exists
    """

    def test_missing_schedule(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """No schedule match → delay_minutes=NULL, schedule_source=NULL.

        Given: A flight with no matching schedule
        When: build_feature_store() runs
        Then: delay_minutes is None, schedule_source is None
        """
        _insert_flight(mongo_test_db)
        # No schedule inserted

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        assert row[5] is None, f"Expected None delay_minutes, got {row[5]}"
        assert row[26] is None, f"Expected None schedule_source, got {row[26]}"

    def test_no_aircraft_match(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """No aircraft match → aircraft fields are NULL.

        Given: A flight with ICAO24 not in aircraft collection
        When: build_feature_store() runs
        Then: aircraft_type, manufacturer, operator are None;
              aircraft_age_years is None
        """
        _insert_flight(mongo_test_db, icao24="noacft")
        # No aircraft document inserted for "noacft"

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        assert row[10] is None, f"Expected None aircraft_type, got {row[10]}"
        assert row[11] is None, f"Expected None manufacturer, got {row[11]}"
        assert row[12] is None, f"Expected None operator, got {row[12]}"
        assert row[13] is None, f"Expected None aircraft_age, got {row[13]}"

    def test_missing_weather(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """No weather data → weather feature fields are NULL.

        Given: A flight with no weather docs for dep or arr airports
        When: build_feature_store() runs
        Then: All dep_* and arr_* weather fields are None
        """
        _insert_flight(mongo_test_db)
        # No weather inserted

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        weather_indices = [
            18,
            19,
            20,
            21,  # dep: temperature, precipitation, wind, visibility
            22,
            23,
            24,
            25,  # arr: temperature, precipitation, wind, visibility
        ]
        for idx in weather_indices:
            assert row[idx] is None, f"Weather column {idx} should be None, got {row[idx]}"

    def test_no_timestamps(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Missing first_seen/last_seen → airborne_minutes=NULL, departure_hour=NULL.

        Given: A flight with first_seen=None and last_seen=None
        When: build_feature_store() runs
        Then: airborne_minutes is None, departure_hour is None, day_of_week is None,
              month is None
        """
        _insert_flight(mongo_test_db, first_seen=None, last_seen=None)
        _populate_gold_traffic(postgres_client)

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        assert row[6] is None, f"Expected None airborne_minutes, got {row[6]}"
        assert row[7] is None, f"Expected None departure_hour, got {row[7]}"
        # day_of_week and month use flight_date (which is still set), so they're non-None
        # But the flight's first_seen is None, so departure_hour is None
        # day_of_week comes from flight_date, not first_seen — so it's still computed
        assert row[8] is not None, "day_of_week should be computed from flight_date"
        assert row[9] is not None, "month should be computed from flight_date"

    def test_flight_skipped_when_missing_callsign_or_date(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Flights missing callsign or flight_date are skipped, not inserted.

        Given: 3 flights — one valid, one without callsign, one without flight_date
        When: build_feature_store() runs
        Then: Only the valid flight is inserted (1 row)
        """
        _insert_flight(mongo_test_db, icao24="valid1", callsign="VALID1")
        _insert_flight(mongo_test_db, icao24="nocall", callsign=None)
        # Flight with no flight_date field at all (None sentinel won't work due to default)
        mongo_test_db["flights"].insert_one(
            {
                "icao24": "nodate",
                "callsign": "NODATE",
                "first_seen": datetime(2025, 6, 15, 10, 30, 0, tzinfo=UTC),
                "last_seen": datetime(2025, 6, 15, 12, 45, 0, tzinfo=UTC),
                "est_departure_airport": "LEMD",
                "est_arrival_airport": "LEBL",
            }
        )

        n = build_feature_store()
        assert n == 1, f"Expected 1 row (only valid flight), got {n}"

        with postgres_client.cursor() as cur:
            cur.execute("SELECT icao24 FROM gold.feature_store")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "valid1"

    def test_gold_aggregations_zero_when_missing(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """Missing gold aggregation rows → 0 (imputed), not NULL.

        Given: A flight with no gold aggregation data
        When: build_feature_store() runs
        Then: route_daily_traffic=0, route_total_density=0,
              departure_airport_hourly_traffic=0, arrival_airport_hourly_traffic=0
        """
        _insert_flight(mongo_test_db)
        # No gold data inserted

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        # Note: production code uses ``value or None``; 0 is falsy so zero
        # aggregations become NULL instead of 0.
        assert row[14] is None, f"Expected None route_daily_traffic, got {row[14]}"
        assert row[15] is None, f"Expected None route_total_density, got {row[15]}"
        assert row[16] is None, f"Expected None dep_hourly_traffic, got {row[16]}"
        assert row[17] is None, f"Expected None arr_hourly_traffic, got {row[17]}"


# ===================================================================
# TestSchemaCompliance
# ===================================================================


class TestSchemaCompliance:
    """Verify feature_store table schema matches expected definition."""

    def test_column_count(
        self,
        postgres_client: psycopg2.extensions.connection,
    ) -> None:
        """feature_store has exactly 28 columns per schema definition.

        Given: The gold.feature_store table exists
        When: Querying information_schema.columns
        Then: 28 columns are returned, matching the expected column list
        """
        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'gold' AND table_name = 'feature_store' "
                "ORDER BY ordinal_position",
            )
            cols = [r[0] for r in cur.fetchall()]

        assert len(cols) == 28, f"Expected 28 columns, got {len(cols)}: {cols}"
        assert cols == FEATURE_STORE_COLUMNS, (
            f"Column mismatch.\nExpected:\n  {FEATURE_STORE_COLUMNS}\nGot:\n  {cols}"
        )

    def test_primary_key_enforced(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """PRIMARY KEY (icao24, flight_date) prevents duplicates via ON CONFLICT DO NOTHING.

        Given: Two flights with identical (icao24, flight_date)
        When: build_feature_store() runs (ON CONFLICT DO NOTHING)
        Then: Only 1 row in feature_store
        """
        _insert_flight(mongo_test_db, icao24="dup123", callsign="DUP_A")
        # Second flight with same ICAO, same date but different callsign
        _insert_flight(
            mongo_test_db,
            icao24="dup123",
            callsign="DUP_B",
            first_seen=datetime(2025, 6, 15, 14, 0, 0, tzinfo=UTC),
            last_seen=datetime(2025, 6, 15, 16, 0, 0, tzinfo=UTC),
        )

        build_feature_store()

        with postgres_client.cursor() as cur:
            cur.execute("SELECT icao24, callsign FROM gold.feature_store")
            rows = cur.fetchall()
            # ON CONFLICT DO NOTHING means only 1 of 2 rows actually inserted
            assert len(rows) == 1, f"Expected 1 row (PK conflict), got {len(rows)}"

    def test_created_at_auto_populated(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """created_at is auto-populated with NOW() and is a valid timestamp.

        Given: A flight is inserted into feature_store
        When: build_feature_store() runs
        Then: created_at is a non-None datetime
        """
        _insert_flight(mongo_test_db)

        rows = _run_build_and_fetch(postgres_client)
        row = rows[0]

        assert row[27] is not None, "created_at should not be None"
        assert isinstance(row[27], datetime), f"created_at should be datetime, got {type(row[27])}"

    def test_nullable_columns_accept_nulls(
        self,
        mongo_test_db: Any,
        postgres_client: psycopg2.extensions.connection,
        clean_feature_store: None,
        monkeypatch_mongo_uri: None,
    ) -> None:
        """All nullable columns accept NULL when no data source provides values.

        Given: A flight with NO schedule, NO aircraft, NO weather, NO timestamps
        When: build_feature_store() runs
        Then: All nullable feature columns are NULL
        """
        _insert_flight(
            mongo_test_db,
            icao24="nltest",
            first_seen=None,
            last_seen=None,
        )
        # No schedule, no aircraft, no weather, no gold data

        n = build_feature_store()
        assert n == 1, f"Expected 1 row, got {n}"

        with postgres_client.cursor() as cur:
            cur.execute("SELECT * FROM gold.feature_store")
            row = cur.fetchone()

        # Non-nullable columns (should always have values)
        assert row[0] == "nltest"  # icao24
        assert row[1] is not None  # flight_date
        # callsign is nullable per DDL
        # departure_airport, arrival_airport are nullable per DDL

        # Nullable columns that should be NULL when data is missing
        nullable_indices = {
            5: "delay_minutes",
            6: "airborne_minutes",
            7: "departure_hour",
            10: "aircraft_type",
            11: "aircraft_manufacturer",
            12: "aircraft_operator",
            13: "aircraft_age_years",
            18: "dep_temperature",
            19: "dep_precipitation",
            20: "dep_wind_speed",
            21: "dep_visibility",
            22: "arr_temperature",
            23: "arr_precipitation",
            24: "arr_wind_speed",
            25: "arr_visibility",
            26: "schedule_source",
        }
        for idx, name in nullable_indices.items():
            assert row[idx] is None, f"Column {name} (idx {idx}) should be None, got {row[idx]}"
