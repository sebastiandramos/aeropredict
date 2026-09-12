"""Tests for the MongoDB silver storage layer."""

from __future__ import annotations

from aeropredict.opensky.storage_silver import close
from aeropredict.opensky.storage_silver import write_flights_silver as write_flights_silver_mongo


class TestSilverStorage:
    """Silver storage operations (MongoDB)."""

    def test_write_empty_flights(self) -> None:
        """Writing an empty list returns 0."""
        count = write_flights_silver_mongo([])
        assert count == 0

    def test_close_connection(self) -> None:
        """Closing the MongoDB connection does not raise."""
        # This is safe to call even without a real connection
        close()
