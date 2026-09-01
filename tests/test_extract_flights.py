"""Tests for flight extraction logic from OpenSky Network API."""

from __future__ import annotations

from aeropredict.opensky.extract_flights import parse_flight_list
from aeropredict.opensky.models import Flight


class TestParseFlightList:
    """Parsing raw API flight list responses."""

    def test_parse_single_flight(self) -> None:
        """A single flight dict is correctly parsed into a Flight object."""
        raw: list[dict] = [
            {
                "icao24": "abc123",
                "firstSeen": 1700000000,
                "lastSeen": 1700003600,
                "estDepartureAirport": "LEMD",
                "estArrivalAirport": "LEBL",
                "callsign": "ABC123",
            },
        ]
        flights = parse_flight_list(raw)
        assert len(flights) == 1
        assert isinstance(flights[0], Flight)
        assert flights[0].icao24 == "abc123"

    def test_parse_empty_list(self) -> None:
        """An empty list returns an empty list of flights."""
        flights = parse_flight_list([])
        assert flights == []
