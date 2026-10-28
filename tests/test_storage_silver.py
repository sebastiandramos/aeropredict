"""Tests for the MongoDB silver storage layer."""

from __future__ import annotations

from datetime import UTC, datetime

import pymongo

from aeropredict.opensky.models import Flight
from aeropredict.opensky.storage_silver import close, write_aena_infovuelos, write_metar
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


def _make_flight(icao24="abc123", first_seen=None):
    if first_seen is None:
        first_seen = datetime(2025, 1, 1, tzinfo=UTC)
    return Flight(
        icao24=icao24,
        first_seen=first_seen,
        last_seen=first_seen,
        est_departure_airport="LEMD",
        est_arrival_airport="LEBL",
        callsign="TEST",
        est_departure_airport_horiz_distance=None,
        est_departure_airport_vert_distance=None,
        est_arrival_airport_horiz_distance=None,
        est_arrival_airport_vert_distance=None,
        departure_airport_candidates_count=None,
        arrival_airport_candidates_count=None,
    )


def test_write_flights_silver_idempotent(mongo_client, monkeypatch):
    from aeropredict.opensky import storage_silver

    db = mongo_client.get_database("aeropredict_test")
    col = db["flights"]
    col.drop()

    # Monkeypatch collection to test DB
    monkeypatch.setattr(storage_silver, "_get_collection", lambda name: col)
    # Also avoid index creation side effects
    monkeypatch.setattr(storage_silver, "_indexes_ensure", True)

    f = _make_flight()
    n1 = write_flights_silver_mongo([f])
    write_flights_silver_mongo([f])
    assert n1 == 1
    # Document count stays 1; ingested_at may refresh on upsert
    assert col.count_documents({}) == 1


def test_write_metar_idempotent(mongo_client, monkeypatch):
    from aeropredict.opensky import storage_silver

    db = mongo_client.get_database("aeropredict_test")
    col = db["metar"]
    col.drop()

    monkeypatch.setattr(storage_silver, "_get_metar_collection", lambda: col)
    monkeypatch.setattr(storage_silver, "_metar_indexes_ensure", True)

    doc = {"icao_id": "LEMD", "obs_time": "2025-01-01T00:00:00Z", "raw": "test"}
    n1 = write_metar([doc])
    write_metar([doc])
    assert n1 == 1
    # Document count stays 1; ingested_at may refresh on upsert
    assert col.count_documents({}) == 1


def test_write_aena_infovuelos_idempotent(mongo_client, monkeypatch):
    from aeropredict.opensky import storage_silver

    db = mongo_client.get_database("aeropredict_test")
    col = db["aena_infovuelos"]
    col.drop()

    monkeypatch.setattr(storage_silver, "_get_aena_collection", lambda: col)
    # avoid index creation side effects
    monkeypatch.setattr(storage_silver, "_aena_indexes_ensure", True)

    doc = {
        "snapshot_at_utc": "2025-01-01T00:00:00Z",
        "flight_number": "IB123",
        "aena_airport_iata": "MAD",
        "flight_type": "I",
        "scheduled_local": "2025-01-01 10:00",
        "other": "x",
    }
    n1 = write_aena_infovuelos([doc])
    write_aena_infovuelos([doc])
    assert n1 == 1
    # Document count stays 1; ingested_at may refresh
    assert col.count_documents({}) == 1


def test_migrate_silver_dedupe_dry_run(mongo_client):
    from scripts.migrate_silver_dedupe import _dedupe_collection

    db = mongo_client.get_database("aeropredict_test")
    col = db["flights"]
    col.drop()
    # Insert 3 duplicates with different _id
    now = datetime.now(UTC)
    for i in range(3):
        col.insert_one(
            {
                "icao24": "abc",
                "first_seen": now,
                "callsign": f"CS{i}",
            }
        )
    before = col.count_documents({})
    assert before == 3
    before_cnt, after_cnt, deleted = _dedupe_collection(col, ["icao24", "first_seen"], dry_run=True)
    assert before_cnt == 3
    assert after_cnt == 3
    assert deleted == 0
    assert col.count_documents({}) == 3


def test_migrate_silver_dedupe_apply(mongo_client):
    from scripts.migrate_silver_dedupe import _dedupe_collection, _ensure_unique_index

    db = mongo_client.get_database("aeropredict_test")
    col = db["flights"]
    col.drop()
    now = datetime.now(UTC)
    # Insert duplicates
    for i in range(3):
        col.insert_one(
            {
                "icao24": "abc",
                "first_seen": now,
                "callsign": f"CS{i}",
            }
        )
    assert col.count_documents({}) == 3
    before_cnt, after_cnt, deleted = _dedupe_collection(
        col, ["icao24", "first_seen"], dry_run=False
    )
    assert before_cnt == 3
    assert after_cnt == 1
    assert deleted == 2
    # Unique index should be creatable now
    _ensure_unique_index(col, ["icao24", "first_seen"])
    # Verify index exists
    indexes = col.index_information()
    found = any(
        idx.get("key", []) == [("icao24", pymongo.ASCENDING), ("first_seen", pymongo.ASCENDING)]
        for idx in indexes.values()
    )
    assert found
