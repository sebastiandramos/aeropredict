"""Tests for the OpenSky data models (StateVector, Flight, Track)."""

from __future__ import annotations

from datetime import UTC, datetime

from aeropredict.opensky.models import Flight, StateVector, Track, TrackWaypoint


class TestStateVector:
    """StateVector parsing from API row data."""

    def test_from_row_minimal(self) -> None:
        """StateVector built from a minimal 18-element row."""
        row: list = [
            "abc123",       # icao24
            "ABC123",       # callsign
            "Spain",        # origin_country
            1700000000,     # time_position
            1700000100,     # last_contact
            None,           # longitude
            None,           # latitude
            None,           # baro_altitude
            True,           # on_ground
            None,           # velocity
            None,           # true_track
            None,           # vertical_rate
            None,           # sensors
            None,           # geo_altitude
            None,           # squawk
            False,          # spi
            0,              # position_source
            None,           # category
        ]
        sv = StateVector.from_row(row, 1700000000)
        assert sv.icao24 == "abc123"
        assert sv.callsign == "ABC123"
        assert sv.origin_country == "Spain"
        assert sv.on_ground is True
        assert sv.longitude is None


class TestFlight:
    """Flight parsing from API dict."""

    def test_from_dict_full(self) -> None:
        """Flight built from a complete API dict."""
        data: dict = {
            "icao24": "abc123",
            "firstSeen": 1700000000,
            "lastSeen": 1700003600,
            "estDepartureAirport": "LEMD",
            "estArrivalAirport": "LEBL",
            "callsign": "ABC123",
            "estDepartureAirportHorizDistance": 100.0,
            "estDepartureAirportVertDistance": 50.0,
            "estArrivalAirportHorizDistance": 200.0,
            "estArrivalAirportVertDistance": 75.0,
            "departureAirportCandidatesCount": 3,
            "arrivalAirportCandidatesCount": 2,
        }
        flight = Flight.from_dict(data)
        assert flight.icao24 == "abc123"
        assert flight.callsign == "ABC123"
        assert flight.est_departure_airport == "LEMD"
        assert flight.est_arrival_airport == "LEBL"
        assert flight.first_seen == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        assert flight.last_seen == datetime(2023, 11, 14, 23, 13, 20, tzinfo=UTC)


class TestTrack:
    """Track parsing from API dict."""

    def test_from_dict_with_waypoints(self) -> None:
        """Track built from a dict with a path of waypoints."""
        data: dict = {
            "icao24": "abc123",
            "startTime": 1700000000,
            "endTime": 1700003600,
            "callsign": "ABC123",
            "path": [
                [1700000000, 40.0, -3.0, 10000.0, 180.0, False],
                [1700001800, 41.0, -2.0, 11000.0, 185.0, False],
            ],
        }
        track = Track.from_dict(data)
        assert track.icao24 == "abc123"
        assert track.callsign == "ABC123"
        assert len(track.path) == 2
        assert isinstance(track.path[0], TrackWaypoint)
        assert track.path[0].latitude == 40.0
        assert track.path[1].longitude == -2.0
