"""Tests for silver_to_gold_entities entity sync logic.

Tests ``write_flights_gold_raw()``, ``write_aircraft_gold()``, and
``write_weather_gold()`` with upsert semantics:
- ``gold.flights`` … ON CONFLICT DO NOTHING on (icao24, flight_date, first_seen)
- ``gold.aircraft`` … ON CONFLICT DO UPDATE on icao24
- ``gold.weather``   … ON CONFLICT DO NOTHING on (airport_code, timestamp)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aeropredict.opensky.storage_gold import (
    close,
    write_aircraft_gold,
    write_flights_gold_raw,
    write_weather_gold,
)

# ---------------------------------------------------------------------------
# helpers — produce dicts that look like MongoDB documents
# ---------------------------------------------------------------------------


def _flight_doc(
    icao24: str = "abc123",
    callsign: str | None = "IBE1234",
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    flight_date: datetime | None = None,
    dep: str | None = "LEMD",
    arr: str | None = "LEBL",
) -> dict:
    """Return a MongoDB-style flight document dict."""
    ts = first_seen or datetime(2025, 6, 15, 10, 30, 0, tzinfo=UTC)
    return {
        "icao24": icao24,
        "callsign": callsign,
        "first_seen": ts,
        "last_seen": last_seen or ts,
        "flight_date": flight_date or datetime(2025, 6, 15),
        "est_departure_airport": dep,
        "est_arrival_airport": arr,
        "departure_airport_horiz_distance": None,
        "departure_airport_vert_distance": None,
        "arrival_airport_horiz_distance": None,
        "arrival_airport_vert_distance": None,
        "departure_airport_candidates_count": None,
        "arrival_airport_candidates_count": None,
    }


def _aircraft_doc(
    icao24: str = "abc123",
    typecode: str | None = "A320",
    manufacturer: str | None = "Airbus",
    operator: str | None = "Iberia",
    first_flight_date: str | None = "2015-06-01",
    icao_aircraft_type: str | None = "L2J",
    registration: str | None = "EC-ABC",
    serial_number: str | None = "SN12345",
) -> dict:
    """Return a MongoDB-style aircraft document dict."""
    return {
        "icao24": icao24,
        "typecode": typecode,
        "manufacturer": manufacturer,
        "operator": operator,
        "first_flight_date": first_flight_date,
        "icao_aircraft_type": icao_aircraft_type,
        "registration": registration,
        "serial_number": serial_number,
    }


def _weather_doc(
    airport_code: str = "LEMD",
    timestamp: datetime | None = None,
    flight_date: datetime | None = None,
    temperature_2m: float | None = 25.0,
    precipitation: float | None = 0.0,
    wind_speed_10m: float | None = 10.0,
    wind_gusts_10m: float | None = 15.0,
    visibility: float | None = 10000.0,
    cloud_cover: float | None = 50.0,
    relative_humidity_2m: float | None = 60.0,
) -> dict:
    """Return a MongoDB-style weather document dict."""
    return {
        "airport_code": airport_code,
        "timestamp": timestamp
        or datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
        "flight_date": flight_date or datetime(2025, 6, 15),
        "temperature_2m": temperature_2m,
        "precipitation": precipitation,
        "wind_speed_10m": wind_speed_10m,
        "wind_gusts_10m": wind_gusts_10m,
        "visibility": visibility,
        "cloud_cover": cloud_cover,
        "relative_humidity_2m": relative_humidity_2m,
    }


# ---------------------------------------------------------------------------
# fixture - clean entity tables before each test
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_entities(postgres_client):
    """Truncate gold entity tables and reset serial sequences.

    Also closes the ``storage_gold`` module-level connection so that
    ``_get_conn()`` starts fresh each test.
    """
    close()
    with postgres_client.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE "
            "gold.flights, "
            "gold.aircraft, "
            "gold.weather "
            "RESTART IDENTITY",
        )
    yield
    with postgres_client.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE "
            "gold.flights, "
            "gold.aircraft, "
            "gold.weather "
            "RESTART IDENTITY",
        )
    close()


# ---------------------------------------------------------------------------
# tests — write_flights_gold_raw
# ---------------------------------------------------------------------------


class TestFlightsRaw:
    """Tests for gold.flights raw insert (ON CONFLICT DO NOTHING)."""

    def test_empty_list_returns_zero(self, clean_entities):
        """Empty flight doc list → returns 0."""
        assert write_flights_gold_raw([]) == 0

    def test_insert_two_flights(self, clean_entities, postgres_client):
        """Insert 2 flight docs → 2 rows in gold.flights."""
        n = write_flights_gold_raw([
            _flight_doc(icao24="abc001"),
            _flight_doc(icao24="abc002"),
        ])
        assert n == 2

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.flights")
            assert cur.fetchone()[0] == 2

    def test_on_conflict_does_nothing(self, clean_entities, postgres_client):
        """Same (icao24, flight_date, first_seen) → no duplicate row."""
        ts = datetime(2025, 6, 15, 10, 30, 0, tzinfo=UTC)
        doc = _flight_doc(icao24="abc001", first_seen=ts)

        n1 = write_flights_gold_raw([doc])
        n2 = write_flights_gold_raw([doc])
        assert n1 == 1
        assert n2 == 1  # counts attempted rows, not actually inserted

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.flights")
            assert cur.fetchone()[0] == 1

    def test_different_first_seen_allows_duplicate_icao24(self, clean_entities, postgres_client):
        """Same icao24+flight_date but different first_seen → allowed."""
        write_flights_gold_raw([
            _flight_doc(
                icao24="abc001",
                first_seen=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        ])
        write_flights_gold_raw([
            _flight_doc(
                icao24="abc001",
                first_seen=datetime(2025, 6, 15, 14, 0, 0, tzinfo=UTC),
            ),
        ])

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.flights")
            assert cur.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# tests — write_aircraft_gold
# ---------------------------------------------------------------------------


class TestAircraft:
    """Tests for gold.aircraft upsert (ON CONFLICT DO UPDATE)."""

    def test_empty_list_returns_zero(self, clean_entities):
        """Empty aircraft list → returns 0."""
        assert write_aircraft_gold([]) == 0

    def test_insert_new_aircraft(self, clean_entities, postgres_client):
        """Insert 1 aircraft → 1 row in gold.aircraft."""
        n = write_aircraft_gold([_aircraft_doc(icao24="abc123")])
        assert n == 1

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.aircraft")
            assert cur.fetchone()[0] == 1

    def test_upsert_idempotent_same_icao24(self, clean_entities, postgres_client):
        """Same icao24 inserted twice → exactly 1 row (no duplicates)."""
        doc = _aircraft_doc(icao24="abc123")
        write_aircraft_gold([doc])
        write_aircraft_gold([doc])

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.aircraft")
            assert cur.fetchone()[0] == 1

    def test_upsert_updates_existing_row(self, clean_entities, postgres_client):
        """Same icao24 with different typecode → row updated to new value."""
        write_aircraft_gold([_aircraft_doc(icao24="abc123", typecode="A320")])
        write_aircraft_gold([_aircraft_doc(icao24="abc123", typecode="B738")])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT typecode FROM gold.aircraft WHERE icao24='abc123'",
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "B738"

    def test_upsert_multiple_aircraft(self, clean_entities, postgres_client):
        """Insert 3 different aircraft → 3 rows."""
        docs = [
            _aircraft_doc(icao24="abc001", typecode="A320"),
            _aircraft_doc(icao24="abc002", typecode="B738"),
            _aircraft_doc(icao24="abc003", typecode="A333"),
        ]
        write_aircraft_gold(docs)

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.aircraft")
            assert cur.fetchone()[0] == 3

    def test_upsert_updates_multiple_fields(self, clean_entities, postgres_client):
        """Updating an aircraft changes multiple tracked fields."""
        write_aircraft_gold([
            _aircraft_doc(
                icao24="abc123",
                typecode="A320",
                manufacturer="Airbus",
                operator="Iberia",
            ),
        ])
        write_aircraft_gold([
            _aircraft_doc(
                icao24="abc123",
                typecode="A321",
                manufacturer="Airbus",
                operator="Vueling",
            ),
        ])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT typecode, operator FROM gold.aircraft WHERE icao24='abc123'",
            )
            row = cur.fetchone()
        assert row is not None
        assert row == ("A321", "Vueling")


# ---------------------------------------------------------------------------
# tests — write_weather_gold
# ---------------------------------------------------------------------------


class TestWeather:
    """Tests for gold.weather insert (ON CONFLICT DO NOTHING)."""

    def test_empty_list_returns_zero(self, clean_entities):
        """Empty weather list → returns 0."""
        assert write_weather_gold([]) == 0

    def test_insert_two_weather_records(self, clean_entities, postgres_client):
        """Insert 2 weather docs → 2 rows in gold.weather."""
        n = write_weather_gold([
            _weather_doc(airport_code="LEMD"),
            _weather_doc(airport_code="LEBL"),
        ])
        assert n == 2

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.weather")
            assert cur.fetchone()[0] == 2

    def test_on_conflict_does_nothing(self, clean_entities, postgres_client):
        """Same (airport_code, timestamp) → no duplicate row."""
        ts = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        doc = _weather_doc(airport_code="LEMD", timestamp=ts)

        write_weather_gold([doc])
        write_weather_gold([doc])

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.weather")
            assert cur.fetchone()[0] == 1

    def test_multi_airport_weather(self, clean_entities, postgres_client):
        """Weather for 3 airports at distinct timestamps → 3 rows."""
        docs = [
            _weather_doc(
                airport_code="LEMD",
                timestamp=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
            _weather_doc(
                airport_code="LEBL",
                timestamp=datetime(2025, 6, 15, 11, 0, 0, tzinfo=UTC),
            ),
            _weather_doc(
                airport_code="LEPA",
                timestamp=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
            ),
        ]
        write_weather_gold(docs)

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.weather")
            assert cur.fetchone()[0] == 3

    def test_same_airport_different_timestamps(self, clean_entities, postgres_client):
        """Same airport, different timestamps → multiple rows allowed."""
        write_weather_gold([
            _weather_doc(
                airport_code="LEMD",
                timestamp=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
            _weather_doc(
                airport_code="LEMD",
                timestamp=datetime(2025, 6, 15, 11, 0, 0, tzinfo=UTC),
            ),
        ])

        with postgres_client.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gold.weather WHERE airport_code='LEMD'")
            assert cur.fetchone()[0] == 2
