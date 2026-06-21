"""Tests for scripts/bronze_to_silver.py — Bronze (Delta Lake) → Silver (MongoDB).

Coverage:
  - MongoDB writes (empty, single, batch, schema compliance)
  - Date-range filtering via ingestion_date
  - Deduplication by (icao24, first_seen, callsign)
  - Null handling (null icao24, null first_seen, null callsign)
  - Checkpoint logic (skip, advance, re-entry)
  - Dry-run mode
  - Argument parsing
"""

from __future__ import annotations

import importlib.util
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from aeropredict.opensky.models import Flight

# ---------------------------------------------------------------------------
# Load the script module (scripts/ is not a package)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path("scripts/bronze_to_silver.py")
_spec = importlib.util.spec_from_file_location("bronze_to_silver", _SCRIPT_PATH)
bronze_to_silver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bronze_to_silver)  # type: ignore[union-attr]

# Module-level references we use across tests
_read_bronze_flights = bronze_to_silver._read_bronze_flights
_get_bronze_dates = bronze_to_silver._get_bronze_dates
_parse_args = bronze_to_silver._parse_args
main = bronze_to_silver.main
CHECKPOINT_COLLECTION = bronze_to_silver.CHECKPOINT_COLLECTION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


def _make_flight(
    icao24: str = "abc123",
    callsign: str | None = "ABC123",
    first_seen: datetime | None = NOW,
    last_seen: datetime | None = None,
    est_departure_airport: str | None = "LEMD",
    est_arrival_airport: str | None = "LEBL",
) -> Flight:
    """Create a Flight with sensible defaults for testing."""
    return Flight(
        icao24=icao24,
        callsign=callsign,
        first_seen=first_seen or NOW,
        last_seen=last_seen or (NOW + timedelta(hours=2)),
        est_departure_airport=est_departure_airport,
        est_arrival_airport=est_arrival_airport,
        est_departure_airport_horiz_distance=100.0,
        est_departure_airport_vert_distance=50.0,
        est_arrival_airport_horiz_distance=200.0,
        est_arrival_airport_vert_distance=75.0,
        departure_airport_candidates_count=3,
        arrival_airport_candidates_count=5,
    )


def _flight_to_response_json(flight: Flight) -> str:
    """Convert a Flight back to the JSON response string stored in Bronze.

    OpenSky API returns a JSON **list** of flight dicts, so we wrap in a list.
    """
    return json.dumps([{
        "icao24": flight.icao24,
        "firstSeen": int(flight.first_seen.timestamp()),
        "lastSeen": int(flight.last_seen.timestamp()),
        "estDepartureAirport": flight.est_departure_airport,
        "estArrivalAirport": flight.est_arrival_airport,
        "callsign": flight.callsign,
        "estDepartureAirportHorizDistance": flight.est_departure_airport_horiz_distance,
        "estDepartureAirportVertDistance": flight.est_departure_airport_vert_distance,
        "estArrivalAirportHorizDistance": flight.est_arrival_airport_horiz_distance,
        "estArrivalAirportVertDistance": flight.est_arrival_airport_vert_distance,
        "departureAirportCandidatesCount": flight.departure_airport_candidates_count,
        "arrivalAirportCandidatesCount": flight.arrival_airport_candidates_count,
    }])


def _build_mock_delta_table(
    flights: list[Flight],
    ingestion_dates: list[str | date] | None = None,
) -> pa.Table:
    """Build a PyArrow table simulating a Delta Lake bronze/opensky partition.

    ``ingestion_dates`` may be ISO strings or ``date`` objects.
    """
    if ingestion_dates is None:
        ingestion_dates = [date(2026, 6, 15)] * len(flights)

    parsed: list[date] = []
    for d in ingestion_dates:
        if isinstance(d, str):
            parsed.append(date.fromisoformat(d))
        else:
            parsed.append(d)

    return pa.table({
        "response": pa.array(
            [_flight_to_response_json(f) for f in flights],
            type=pa.string(),
        ),
        "ingestion_date": pa.array(parsed, type=pa.date32()),
    })


def _monkeypatch_deltatable(
    monkeypatch: pytest.MonkeyPatch,
    table: pa.Table,
) -> None:
    """Replace ``deltalake.DeltaTable`` with a mock returning *table*."""

    class MockDeltaTable:
        def __init__(self, table_uri: str, storage_options: Any = None) -> None:
            pass

        def to_pyarrow_table(self) -> pa.Table:
            return table

        def partitions(self) -> list[dict[str, str]]:
            # Build unique ingestion_date values from the table
            date_col = table.column("ingestion_date")
            unique_dates = sorted(
                {str(d.as_py()) for d in date_col if d.as_py() is not None}
            )
            return [{"ingestion_date": d} for d in unique_dates]

    monkeypatch.setattr("deltalake.DeltaTable", MockDeltaTable)


# ===================================================================
# TestMongoWrites
# ===================================================================


class TestMongoWrites:
    """Tests for MongoDB write path (write_flights_silver + doc structure)."""

    def test_write_empty_list_returns_zero(self) -> None:
        """Writing an empty flight list returns 0."""
        from aeropredict.opensky.storage_silver import write_flights_silver

        count = write_flights_silver([])
        assert count == 0

    def test_write_single_flight_calls_insert_many(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A single Flight calls insert_many once with correct docs."""
        mock_collection = MagicMock()
        mock_collection.insert_many.return_value = MagicMock(inserted_ids=["id1"])

        monkeypatch.setattr(
            "aeropredict.opensky.storage_silver._get_collection",
            lambda name: mock_collection,
        )

        from aeropredict.opensky.storage_silver import write_flights_silver

        flight = _make_flight()
        n = write_flights_silver([flight])

        assert n == 1
        mock_collection.insert_many.assert_called_once()
        args, _ = mock_collection.insert_many.call_args
        docs = args[0]
        assert len(docs) == 1
        doc = docs[0]
        assert doc["icao24"] == "abc123"
        assert doc["callsign"] == "ABC123"
        assert doc["est_departure_airport"] == "LEMD"
        assert doc["est_arrival_airport"] == "LEBL"
        assert "flight_date" in doc
        assert "ingested_at" in doc

    def test_write_schema_compliance(self) -> None:
        """Inserted documents match the expected MongoDB schema."""
        from aeropredict.opensky.storage_silver import _flight_to_doc

        flight = _make_flight(
            icao24="def456",
            callsign="DEF456",
            first_seen=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
            last_seen=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
            est_departure_airport="LEMD",
            est_arrival_airport="LEBL",
        )

        doc = _flight_to_doc(flight)

        # All expected keys present
        expected_keys = {
            "icao24",
            "callsign",
            "first_seen",
            "last_seen",
            "est_departure_airport",
            "est_arrival_airport",
            "departure_airport_horiz_distance",
            "departure_airport_vert_distance",
            "arrival_airport_horiz_distance",
            "arrival_airport_vert_distance",
            "departure_airport_candidates_count",
            "arrival_airport_candidates_count",
            "flight_date",
            "ingested_at",
        }
        assert set(doc.keys()) == expected_keys

        # Type checks
        assert isinstance(doc["icao24"], str)
        assert isinstance(doc["flight_date"], datetime)
        assert doc["callsign"] == "DEF456"
        assert doc["departure_airport_horiz_distance"] == 100.0
        # flight_date derived from first_seen
        assert doc["flight_date"].date() == flight.first_seen.date()

    def test_write_schema_with_null_optionals(self) -> None:
        """Doc with null optionals produces expected None fields."""
        from aeropredict.opensky.storage_silver import _flight_to_doc

        flight = _make_flight(
            callsign=None,
            est_departure_airport=None,
            est_arrival_airport=None,
        )
        doc = _flight_to_doc(flight)

        assert doc["callsign"] is None
        assert doc["est_departure_airport"] is None
        assert doc["est_arrival_airport"] is None

    def test_write_batch_invoked_with_ordered_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Batch insert uses ordered=False to handle partial failures."""
        mock_collection = MagicMock()
        monkeypatch.setattr(
            "aeropredict.opensky.storage_silver._get_collection",
            lambda name: mock_collection,
        )

        from aeropredict.opensky.storage_silver import write_flights_silver

        flights = [_make_flight(icao24=f"abc{i}") for i in range(5)]
        write_flights_silver(flights)

        mock_collection.insert_many.assert_called_once()
        _kwargs = mock_collection.insert_many.call_args.kwargs
        assert _kwargs.get("ordered") is False


# ===================================================================
# TestDateRangeFiltering
# ===================================================================


class TestDateRangeFiltering:
    """Date filtering via ingestion_date in _read_bronze_flights."""

    def test_filter_by_specific_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only flights matching target_date are returned."""
        flights = [
            _make_flight(icao24="flt001", first_seen=NOW),
            _make_flight(icao24="flt002", first_seen=NOW),
            _make_flight(icao24="flt003", first_seen=NOW),
        ]
        # Two dates: two flights on 2026-06-15, one on 2026-06-16
        table = _build_mock_delta_table(
            flights,
            ingestion_dates=["2026-06-15", "2026-06-15", "2026-06-16"],
        )
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=datetime(2026, 6, 15).date(),
            dry_run=False,
        )
        icao24s = {f.icao24 for f in result}
        assert "flt001" in icao24s
        assert "flt002" in icao24s
        assert "flt003" not in icao24s

    def test_no_date_returns_all_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without target_date, all rows are processed."""
        flights = [
            _make_flight(icao24="a1", first_seen=NOW),
            _make_flight(icao24="a2", first_seen=NOW),
        ]
        table = _build_mock_delta_table(flights, ["2026-06-15", "2026-06-16"])
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert len(result) == 2

    def test_no_matching_date_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no rows match the target date, returns empty list."""
        flights = [_make_flight(icao24="x1", first_seen=NOW)]
        table = _build_mock_delta_table(flights, ["2026-06-15"])
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=datetime(2026, 6, 20).date(),
            dry_run=False,
        )
        assert result == []


# ===================================================================
# TestDeduplication
# ===================================================================


class TestDeduplication:
    """Dedup by (icao24, first_seen, callsign) in _read_bronze_flights."""

    def test_ten_identical_flights_deduplicate_to_one(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """10 identical records → 1 Flight after dedup."""
        flight = _make_flight()
        flights = [flight] * 10
        table = _build_mock_delta_table(flights)
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert len(result) == 1

    def test_different_icao24_all_kept(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Different icao24 values produce separate documents."""
        flights = [
            _make_flight(icao24="aaa001", callsign="FL001"),
            _make_flight(icao24="bbb002", callsign="FL002"),
            _make_flight(icao24="ccc003", callsign="FL003"),
        ]
        table = _build_mock_delta_table(flights)
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert len(result) == 3

    def test_same_icao24_different_first_seen_both_kept(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same icao24 but different first_seen are separate flights."""
        flights = [
            _make_flight(icao24="abc123", callsign="FL001", first_seen=NOW),
            _make_flight(
                icao24="abc123",
                callsign="FL001",
                first_seen=NOW + timedelta(hours=3),
            ),
        ]
        table = _build_mock_delta_table(flights)
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert len(result) == 2

    def test_same_icao24_different_callsign_both_kept(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same icao24 but different callsign are separate flights."""
        flights = [
            _make_flight(icao24="abc123", callsign="FL001", first_seen=NOW),
            _make_flight(icao24="abc123", callsign="FL002", first_seen=NOW),
        ]
        table = _build_mock_delta_table(flights)
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert len(result) == 2


# ===================================================================
# TestNullHandling
# ===================================================================


class TestNullHandling:
    """Edge cases with null / malformed data."""

    def test_null_icao24_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A flight with null-like icao24 is parsed without error (str() wraps it)."""
        raw_response = json.dumps([{
            "icao24": "",
            "firstSeen": 1700000000,
            "lastSeen": 1700003600,
            "callsign": "NOCALL",
        }])
        table = pa.table({
            "response": pa.array([raw_response], type=pa.string()),
            "ingestion_date": pa.array([date(2026, 6, 15)], type=pa.date32()),
        })
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        # Should not crash; may produce a Flight with empty-string icao24
        assert len(result) == 1
        assert result[0].icao24 == ""

    def test_null_first_seen_handled_gracefully(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Flight with null firstSeen is still parsed."""
        raw_response = json.dumps([{
            "icao24": "abc123",
            "firstSeen": None,
            "lastSeen": 1700003600,
            "callsign": "TESTFLT",
        }])
        table = pa.table({
            "response": pa.array([raw_response], type=pa.string()),
            "ingestion_date": pa.array([date(2026, 6, 15)], type=pa.date32()),
        })
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert len(result) == 1
        assert result[0].first_seen is None

    def test_null_callsign_handled_gracefully(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Flight with missing callsign produces None (str(None) → "None").

        ``Flight.from_dict`` does ``str(data.get("callsign", "")).strip() or None``.
        When the key is absent: ``data.get("callsign", "")`` → ``""`` → ``str("")``
        → ``""`` → ``"" or None`` → ``None``.
        """
        raw_response = json.dumps([{
            "icao24": "abc123",
            "firstSeen": 1700000000,
            "lastSeen": 1700003600,
            # callsign key intentionally omitted
        }])
        table = pa.table({
            "response": pa.array([raw_response], type=pa.string()),
            "ingestion_date": pa.array([date(2026, 6, 15)], type=pa.date32()),
        })
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert len(result) == 1
        assert result[0].callsign is None

    def test_empty_response_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty string in response column is skipped gracefully."""
        table = pa.table({
            "response": pa.array(["", "{}"], type=pa.string()),
            "ingestion_date": pa.array([date(2026, 6, 15), date(2026, 6, 15)], type=pa.date32()),
        })
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        # Empty string is skipped; "{}" parses to empty list
        assert result == []

    def test_invalid_json_logged_dropped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Invalid JSON in response column is logged and dropped."""
        table = pa.table({
            "response": pa.array(['{"malformed": missing_quote'], type=pa.string()),
            "ingestion_date": pa.array([date(2026, 6, 15)], type=pa.date32()),
        })
        _monkeypatch_deltatable(monkeypatch, table)

        with caplog.at_level(logging.WARNING):
            result = _read_bronze_flights(
                delta_root="/tmp/fake",
                target_date=None,
                dry_run=False,
            )

        assert result == []
        assert "Error parseando" in caplog.text

    def test_empty_delta_table_returns_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty Delta table (no rows) returns an empty list."""
        table = pa.table({
            "response": pa.array([], type=pa.string()),
            "ingestion_date": pa.array([], type=pa.date32()),
        })
        _monkeypatch_deltatable(monkeypatch, table)

        result = _read_bronze_flights(
            delta_root="/tmp/fake",
            target_date=None,
            dry_run=False,
        )
        assert result == []


# ===================================================================
# TestCheckpoint
# ===================================================================


class TestCheckpoint:
    """Checkpoint logic prevents re-processing and advances correctly."""

    def _mock_main_dependencies(
        self,
        monkeypatch: pytest.MonkeyPatch,
        table: pa.Table | None = None,
        checkpoint_set: set[str] | None = None,
        write_count: int = 5,
    ) -> MagicMock:
        """Set up common monkeypatches for main() tests.

        Patches the module-level references in ``bronze_to_silver`` itself
        (``from X import Y`` creates a local binding — the source module's
        attribute must be patched to affect calls inside ``main()``).

        Returns the mock for write_flights_silver.
        """
        if table is None:
            flights = [_make_flight(icao24=f"flt{i:03d}") for i in range(5)]
            table = _build_mock_delta_table(flights)

        _monkeypatch_deltatable(monkeypatch, table)

        # Mock checkpoints — patch the local reference in bronze_to_silver
        monkeypatch.setattr(
            bronze_to_silver,
            "get_checkpoint_set",
            lambda _col: checkpoint_set or set(),
        )
        mock_add = MagicMock()
        monkeypatch.setattr(bronze_to_silver, "add_to_checkpoint_set", mock_add)

        # Mock DB writes — patch the local reference in bronze_to_silver
        mock_write = MagicMock(return_value=write_count)
        monkeypatch.setattr(bronze_to_silver, "write_flights_silver", mock_write)
        monkeypatch.setattr(bronze_to_silver, "close_silver", MagicMock())

        return mock_write

    def test_main_skips_processed_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a date is in the checkpoint set, main returns 0 without writing."""
        flights = [_make_flight(icao24="flt001")]
        table = _build_mock_delta_table(flights, ["2026-06-15"])

        mock_write = self._mock_main_dependencies(
            monkeypatch,
            table=table,
            checkpoint_set={"2026-06-15"},  # Already processed
        )

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 0
        mock_write.assert_not_called()

    def test_main_advances_checkpoint_on_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After a successful write, the date is added to the checkpoint set."""
        flights = [_make_flight(icao24="flt001")]
        table = _build_mock_delta_table(flights, ["2026-06-15"])

        mock_add = MagicMock()
        monkeypatch.setattr(bronze_to_silver, "add_to_checkpoint_set", mock_add)
        monkeypatch.setattr(
            bronze_to_silver,
            "get_checkpoint_set",
            lambda _col: set(),
        )
        _monkeypatch_deltatable(monkeypatch, table)

        monkeypatch.setattr(
            bronze_to_silver,
            "write_flights_silver",
            MagicMock(return_value=1),
        )
        monkeypatch.setattr(bronze_to_silver, "close_silver", MagicMock())

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 0
        mock_add.assert_called_once_with(CHECKPOINT_COLLECTION, "2026-06-15")

    def test_main_empty_checkpoint_processes_data(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no checkpoint exists, main processes and writes flights."""
        mock_write = self._mock_main_dependencies(
            monkeypatch,
            checkpoint_set=set(),
            write_count=5,
        )

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 0
        mock_write.assert_called_once()
        args, _ = mock_write.call_args
        flights_arg = args[0]
        assert len(flights_arg) == 5

    def test_main_dry_run_does_not_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dry-run mode returns 0 without writing to MongoDB."""
        mock_write = self._mock_main_dependencies(
            monkeypatch,
            checkpoint_set=set(),
        )

        rc = main([
            "--date", "2026-06-15",
            "--delta-root", "/tmp/fake",
            "--dry-run",
        ])

        assert rc == 0
        mock_write.assert_not_called()

    def test_main_no_flights_returns_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When bronze has no flights for the date, main returns 0."""
        empty_table = pa.table({
            "response": pa.array([], type=pa.string()),
            "ingestion_date": pa.array([], type=pa.date32()),
        })
        _monkeypatch_deltatable(monkeypatch, empty_table)

        monkeypatch.setattr(
            bronze_to_silver,
            "get_checkpoint_set",
            lambda _col: set(),
        )
        mock_write = MagicMock()
        monkeypatch.setattr(
            bronze_to_silver,
            "write_flights_silver",
            mock_write,
        )
        monkeypatch.setattr(bronze_to_silver, "close_silver", MagicMock())

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 0
        mock_write.assert_not_called()


# ===================================================================
# TestArgumentParsing
# ===================================================================


class TestArgumentParsing:
    """Argument parsing edge cases in _parse_args."""

    def test_default_date_is_none(self) -> None:
        """Without --date, target date is None."""
        args = _parse_args(["--delta-root", "/tmp/fake"])
        assert args.date is None

    def test_specific_date_parsed(self) -> None:
        """--date YYYY-MM-DD is correctly parsed."""
        args = _parse_args(["--date", "2026-06-15"])
        assert args.date == "2026-06-15"

    def test_dry_run_flag(self) -> None:
        """--dry-run sets the dry_run flag."""
        args = _parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_delta_root_override(self) -> None:
        """--delta-root overrides the default."""
        args = _parse_args(["--delta-root", "/custom/path"])
        assert args.delta_root == "/custom/path"
