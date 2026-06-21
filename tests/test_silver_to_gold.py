"""Tests for silver_to_gold aggregation logic.

Tests ``write_flights_gold()`` which aggregates ``Flight`` objects into
``gold.daily_airport_traffic``, ``gold.route_density``, and
``gold.hourly_distribution`` with ON CONFLICT merge semantics.
"""

from __future__ import annotations

from datetime import date as date_type, datetime, timezone

import pytest

from aeropredict.opensky.models import Flight
from aeropredict.opensky.storage_gold import close, write_flights_gold


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _flight(
    icao24: str = "abc123",
    dep: str | None = "LEMD",
    arr: str | None = "LEBL",
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    callsign: str | None = "IBE1234",
) -> Flight:
    """Return a Flight with sensible defaults for test brevity."""
    ts = first_seen or datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
    return Flight(
        icao24=icao24,
        first_seen=ts,
        last_seen=last_seen or ts,
        est_departure_airport=dep,
        est_arrival_airport=arr,
        callsign=callsign,
        est_departure_airport_horiz_distance=None,
        est_departure_airport_vert_distance=None,
        est_arrival_airport_horiz_distance=None,
        est_arrival_airport_vert_distance=None,
        departure_airport_candidates_count=None,
        arrival_airport_candidates_count=None,
    )


# ---------------------------------------------------------------------------
# fixture – clean aggregation tables before each test
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_agg(postgres_client):
    """Truncate gold aggregation tables so each test starts empty.

    Also resets the ``storage_gold`` module-level connection singleton
    so that ``_get_conn()`` creates a fresh connection.
    """
    close()
    with postgres_client.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE "
            "gold.daily_airport_traffic, "
            "gold.route_density, "
            "gold.hourly_distribution",
        )
    yield
    with postgres_client.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE "
            "gold.daily_airport_traffic, "
            "gold.route_density, "
            "gold.hourly_distribution",
        )
    close()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestDailyAirportTraffic:
    """Tests for gold.daily_airport_traffic aggregation."""

    def test_empty_flight_list_returns_zeros(self, clean_agg):
        """Empty flight list → all counts zero."""
        result = write_flights_gold([])
        assert result == {
            "daily_airport_traffic": 0,
            "route_density": 0,
            "hourly_distribution": 0,
        }

    def test_single_arrival_increments_arrivals(self, clean_agg, postgres_client):
        """1 arrival at LEMD → arrivals_count=1, departures_count=0."""
        write_flights_gold([_flight(dep=None, arr="LEMD")])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT airport_code, arrivals_count, departures_count "
                "FROM gold.daily_airport_traffic",
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0] == ("LEMD", 1, 0)

    def test_single_departure_increments_departures(self, clean_agg, postgres_client):
        """1 departure from LEMD → departures_count=1, arrivals_count=0."""
        write_flights_gold([_flight(dep="LEMD", arr=None)])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT airport_code, arrivals_count, departures_count "
                "FROM gold.daily_airport_traffic",
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0] == ("LEMD", 0, 1)

    def test_20_flights_at_lemd_total_20(self, clean_agg, postgres_client):
        """20 flights departing LEMD → LEMD row shows departures_count=20."""
        flights = [
            _flight(icao24=f"abc{i:03d}", dep="LEMD", arr="LEBL")
            for i in range(20)
        ]
        write_flights_gold(flights)

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT airport_code, arrivals_count + departures_count AS total "
                "FROM gold.daily_airport_traffic ORDER BY airport_code",
            )
            totals = dict(cur.fetchall())
        assert totals["LEMD"] == 20
        assert totals["LEBL"] == 20

    def test_daily_traffic_date_matches_first_seen(self, clean_agg, postgres_client):
        """flight_date in daily_airport_traffic matches first_seen date."""
        write_flights_gold([
            _flight(
                icao24="abc001",
                dep="LEMD",
                arr=None,
                first_seen=datetime(2025, 7, 4, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT flight_date FROM gold.daily_airport_traffic "
                "WHERE airport_code='LEMD'",
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == date_type(2025, 7, 4)

    def test_on_conflict_traffic_merges(self, clean_agg, postgres_client):
        """Same airport/date inserted twice → counts are additive."""
        batch1 = [_flight(icao24="abc001", dep="LEMD", arr=None)]
        batch2 = [_flight(icao24="abc002", dep="LEMD", arr=None)]

        write_flights_gold(batch1)
        write_flights_gold(batch2)

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT departures_count FROM gold.daily_airport_traffic "
                "WHERE airport_code='LEMD'",
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == 2


class TestRouteDensity:
    """Tests for gold.route_density aggregation."""

    def test_single_route_accumulates(self, clean_agg, postgres_client):
        """10 flights LEMD→LEBL → 1 row with flight_count=10."""
        flights = [
            _flight(icao24=f"abc{i:03d}", dep="LEMD", arr="LEBL")
            for i in range(10)
        ]
        write_flights_gold(flights)

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT departure_airport, arrival_airport, flight_count "
                "FROM gold.route_density",
            )
            row = cur.fetchone()
        assert row is not None
        assert row == ("LEMD", "LEBL", 10)

    def test_multiple_routes_separate_rows(self, clean_agg, postgres_client):
        """Different route pairs produce independent rows."""
        flights = [
            _flight(icao24="abc001", dep="LEMD", arr="LEBL"),
            _flight(icao24="abc002", dep="LEMD", arr="LEBL"),
            _flight(icao24="abc003", dep="LEBL", arr="LEMD"),
            _flight(icao24="abc004", dep="LEMD", arr="LEPA"),
        ]
        write_flights_gold(flights)

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT departure_airport, arrival_airport, flight_count "
                "FROM gold.route_density ORDER BY flight_count DESC",
            )
            rows = cur.fetchall()
        assert len(rows) == 3
        counts = {(r[0], r[1]): r[2] for r in rows}
        assert counts[("LEMD", "LEBL")] == 2
        assert counts[("LEBL", "LEMD")] == 1
        assert counts[("LEMD", "LEPA")] == 1

    def test_on_conflict_route_merges(self, clean_agg, postgres_client):
        """Same route inserted twice → flight_count adds up."""
        write_flights_gold([_flight(icao24="abc001", dep="LEMD", arr="LEBL")])
        write_flights_gold([_flight(icao24="abc002", dep="LEMD", arr="LEBL")])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT flight_count FROM gold.route_density "
                "WHERE departure_airport='LEMD' AND arrival_airport='LEBL'",
            )
            count = cur.fetchone()[0]
        assert count == 2

    def test_route_density_tracks_date_range(self, clean_agg, postgres_client):
        """first_seen/last_seen reflect earliest and latest flight dates."""
        write_flights_gold([
            _flight(
                icao24="abc001", dep="LEMD", arr="LEBL",
                first_seen=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            ),
            _flight(
                icao24="abc002", dep="LEMD", arr="LEBL",
                first_seen=datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
            ),
        ])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT first_seen, last_seen FROM gold.route_density "
                "WHERE departure_airport='LEMD' AND arrival_airport='LEBL'",
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == date_type(2025, 1, 1)
        assert row[1] == date_type(2025, 6, 15)


class TestHourlyDistribution:
    """Tests for gold.hourly_distribution aggregation."""

    def test_hourly_tracks_arrivals_and_departures(self, clean_agg, postgres_client):
        """Flights at different hours → separate hourly rows with correct counts."""
        flights = [
            _flight(
                icao24="abc001", dep="LEMD", arr="LEBL",
                first_seen=datetime(2025, 6, 15, 8, 0, 0, tzinfo=timezone.utc),
            ),
            _flight(
                icao24="abc002", dep="LEMD", arr="LEBL",
                first_seen=datetime(2025, 6, 15, 8, 30, 0, tzinfo=timezone.utc),
            ),
            _flight(
                icao24="abc003", dep="LEMD", arr="LEPA",
                first_seen=datetime(2025, 6, 15, 14, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        write_flights_gold(flights)

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT airport_code, hour, arrivals_count, departures_count "
                "FROM gold.hourly_distribution ORDER BY airport_code, hour",
            )
            rows = cur.fetchall()
        assert len(rows) == 4
        by_key = {(r[0], r[1]): (r[2], r[3]) for r in rows}
        assert by_key[("LEBL", 8)] == (2, 0)   # 2 arrivals at 08
        assert by_key[("LEMD", 8)] == (0, 2)   # 2 departures at 08
        assert by_key[("LEMD", 14)] == (0, 1)  # 1 departure at 14
        assert by_key[("LEPA", 14)] == (1, 0)  # 1 arrival at 14

    def test_on_conflict_hourly_merges(self, clean_agg, postgres_client):
        """Same airport/date/hour inserted twice → counts are additive."""
        write_flights_gold([
            _flight(
                icao24="abc001", dep="LEMD", arr=None,
                first_seen=datetime(2025, 6, 15, 8, 0, 0, tzinfo=timezone.utc),
            ),
        ])
        write_flights_gold([
            _flight(
                icao24="abc002", dep="LEMD", arr=None,
                first_seen=datetime(2025, 6, 15, 8, 0, 0, tzinfo=timezone.utc),
            ),
        ])

        with postgres_client.cursor() as cur:
            cur.execute(
                "SELECT departures_count FROM gold.hourly_distribution "
                "WHERE airport_code='LEMD' AND hour=8",
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == 2


class TestEdgeCases:
    """Edge cases for write_flights_gold."""

    def test_flight_without_first_seen_skipped(self, clean_agg):
        """Flight with first_seen=None contributes nothing to aggregations."""
        flights = [
            Flight(
                icao24="abc123",
                first_seen=None,  # type: ignore[arg-type]
                last_seen=datetime(2025, 6, 15, 11, 0, 0, tzinfo=timezone.utc),
                est_departure_airport="LEMD",
                est_arrival_airport="LEBL",
                callsign="IBE1234",
                est_departure_airport_horiz_distance=None,
                est_departure_airport_vert_distance=None,
                est_arrival_airport_horiz_distance=None,
                est_arrival_airport_vert_distance=None,
                departure_airport_candidates_count=None,
                arrival_airport_candidates_count=None,
            ),
        ]
        result = write_flights_gold(flights)
        # All flights are skipped → no aggregate rows are produced
        assert result == {}

    def test_flight_without_airport_skipped(self, clean_agg):
        """Flight with neither dep nor arr airport still contributes nothing."""
        flights = [_flight(dep=None, arr=None)]
        result = write_flights_gold(flights)
        # Flight has first_seen but no airports → no agg rows
        assert result == {}
