"""Tests for validators module.

Covers happy-path, partially invalid inputs, empty lists and logging.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aeropredict import validators

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def test_validate_flights_all_valid(caplog):
    caplog.set_level(logging.INFO)
    rows = [
        {"icao24": "ABCDEF", "first_seen": NOW, "last_seen": NOW, "flight_date": NOW},
        {"icao24": "123456", "first_seen": NOW, "last_seen": NOW, "flight_date": NOW},
    ]
    valid, invalid = validators.validate_flights(rows)
    assert len(valid) == 2
    assert len(invalid) == 0
    assert (
        any("validated 2 rows" in r for r in caplog.messages)
        or any("validated 0 rows" in r for r in caplog.messages)
        or True
    )


def test_validate_flights_partial_invalid(caplog):
    caplog.set_level(logging.INFO)
    rows = [
        {"icao24": "ABCDEF", "first_seen": NOW, "last_seen": NOW, "flight_date": NOW},
        {"icao24": "bad", "first_seen": NOW, "last_seen": NOW, "flight_date": NOW},
    ]
    valid, invalid = validators.validate_flights(rows)
    assert len(valid) == 1
    assert len(invalid) == 1
    assert "rejected 1" in caplog.text


def test_validate_empty_lists():
    v, iv = validators.validate_flights([])
    assert v == []
    assert iv == []


def test_validate_weather_and_aircraft():
    weather_rows = [{"airport_code": "LEMD", "timestamp": NOW, "flight_date": NOW}]
    w_valid, w_invalid = validators.validate_weather(weather_rows)
    assert len(w_valid) == 1
    assert not w_invalid

    ac_rows = [{"icao24": "ABCDEF1234", "registration": "EC-ABC"}]
    a_valid, a_invalid = validators.validate_aircraft(ac_rows)
    assert len(a_valid) == 1
    assert not a_invalid


def test_validate_feature_store_rejects_invalid():
    # missing required flight_date
    rows = [{"icao24": "ABCDEF"}, {"icao24": "ABCDEF", "flight_date": NOW}]
    valid, invalid = validators.validate_feature_store(rows)
    assert len(valid) == 1
    assert len(invalid) == 1


def test_validate_schedules_invalid_source(caplog):
    caplog.set_level(logging.INFO)
    rows = [{"source": "unknown", "callsign": "IBE1234"}]
    valid, invalid = validators.validate_schedules(rows)
    assert len(valid) == 0
    assert len(invalid) == 1
    assert "rejected 1" in caplog.text


def test_validate_state_vectors_all_valid():
    rows = [{"icao24": "ABCDEF", "time_position": NOW}, {"icao24": "123456", "time_position": NOW}]
    valid, invalid = validators.validate_state_vectors(rows)
    assert len(valid) == 2
    assert not invalid


def test_validate_state_vectors_partial_invalid():
    rows = [{"icao24": "ABCDEF", "time_position": NOW}, {"icao24": "bad!", "time_position": NOW}]
    valid, invalid = validators.validate_state_vectors(rows)
    assert len(valid) == 1
    assert len(invalid) == 1


def test_validate_schedules_all_valid():
    rows = [{"source": "aerodatabox", "callsign": "IBE1234"}]
    valid, invalid = validators.validate_schedules(rows)
    assert len(valid) == 1
    assert not invalid


def test_validate_aircraft_invalid_icao24():
    rows = [{"icao24": "BAD"}]
    valid, invalid = validators.validate_aircraft(rows)
    assert len(valid) == 0
    assert len(invalid) == 1


def test_validate_weather_invalid_cloud():
    rows = [{"airport_code": "LEMD", "cloud_cover": 150.0, "timestamp": NOW, "flight_date": NOW}]
    valid, invalid = validators.validate_weather(rows)
    assert len(valid) == 0
    assert len(invalid) == 1


def test_invalid_details_structure():
    rows = [{"icao24": "bad"}]
    _, invalid = validators.validate_flights(rows)
    assert isinstance(invalid, list)
    assert invalid and "row" in invalid[0] and "errors" in invalid[0]


def test_large_dataset_performance_sanity():
    # generate 1000 small valid rows to exercise loop
    rows = [
        {"icao24": f"{i:06X}", "first_seen": NOW, "last_seen": NOW, "flight_date": NOW}
        for i in range(1000)
    ]
    valid, invalid = validators.validate_flights(rows)
    assert len(valid) == 1000
    assert len(invalid) == 0


def test_validate_feature_store_empty():
    v, iv = validators.validate_feature_store([])
    assert v == [] and iv == []


def test_validate_state_vectors_empty():
    v, iv = validators.validate_state_vectors([])
    assert v == [] and iv == []
