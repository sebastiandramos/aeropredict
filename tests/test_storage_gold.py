"""Tests for the PostgreSQL gold storage layer."""

from __future__ import annotations

from aeropredict.opensky.models import Flight
from aeropredict.opensky.storage_gold import close, write_flights_gold
from aeropredict.opensky.storage_gold import write_flights_gold_raw as write_flights_gold_raw_func


class TestGoldStorage:
    """Gold storage operations (PostgreSQL)."""

    def test_write_empty_flights(self) -> None:
        """Writing an empty flight list returns zero counts."""
        result = write_flights_gold([])
        assert result == {"daily_airport_traffic": 0, "route_density": 0, "hourly_distribution": 0}

    def test_write_empty_raw_flights(self) -> None:
        """Writing an empty raw flight doc list returns 0."""
        count = write_flights_gold_raw_func([])
        assert count == 0

    def test_close_connection(self) -> None:
        """Closing the PostgreSQL connection does not raise."""
        close()
