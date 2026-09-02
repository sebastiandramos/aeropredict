"""Tests for the AENA Infovuelos promotion path in scripts/bronze_to_silver.py.

Coverage:
  - Hour discovery from bronze/aena_infovuelos (window + date override)
  - Normalization of raw AENA flights via AenaInfovuelosAdapter + ICAO enrichment
  - Per-hour atomic checkpointing in _process_aena_hours (write → checkpoint)
  - main() integration: pending-hour filtering, dry-run, exit codes
  - write_aena_infovuelos MongoDB write path

The AENA route was merged from main without test coverage (MAJOR #4).
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

# ---------------------------------------------------------------------------
# Load the script module (scripts/ is not a package)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path("scripts/bronze_to_silver.py")
_spec = importlib.util.spec_from_file_location("bronze_to_silver_aena", _SCRIPT_PATH)
bronze_to_silver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bronze_to_silver)  # type: ignore[union-attr]

_get_aena_bronze_hours = bronze_to_silver._get_aena_bronze_hours
_read_bronze_aena_infovuelos = bronze_to_silver._read_bronze_aena_infovuelos
_process_aena_hours = bronze_to_silver._process_aena_hours
main = bronze_to_silver.main
CHECKPOINT_COLLECTION = bronze_to_silver.CHECKPOINT_COLLECTION
CHECKPOINT_COLLECTION_AENA = bronze_to_silver.CHECKPOINT_COLLECTION_AENA
CHECKPOINT_COLLECTION_METAR = bronze_to_silver.CHECKPOINT_COLLECTION_METAR
CHECKPOINT_COLLECTION_HOLIDAYS = bronze_to_silver.CHECKPOINT_COLLECTION_HOLIDAYS
CHECKPOINT_COLLECTION_EUROCONTROL = bronze_to_silver.CHECKPOINT_COLLECTION_EUROCONTROL
CHECKPOINT_COLLECTION_NOTAM = bronze_to_silver.CHECKPOINT_COLLECTION_NOTAM

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

HOUR_10 = datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC)
HOUR_11 = datetime(2026, 6, 15, 11, 0, 0, tzinfo=UTC)
HOUR_12 = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)

AENA_PARAMS = {"airport": "MAD", "flightType": "S"}


def _raw_aena_flight(**overrides: Any) -> dict[str, Any]:
    """Minimal raw AENA flight row as returned by the Satellite endpoint."""
    raw: dict[str, Any] = {
        "tipoVuelo": "S",
        "iataCompania": "IB",
        "compania": "IBERIA",
        "oaciCompania": "IBE",
        "nombreCompania": "IBERIA",
        "numVuelo": "1234",
        "iataAena": "MAD",
        "iataOtro": "BCN",
        "ciudadIataOtro": "BARCELONA",
        "fecha": "15/06/2026",
        "horaProgramada": "10:30:00",
        "fechaEstimada": "15/06/2026",
        "horaEstimada": "10:35:00",
        "estado": "Aterrizado",
        "terminal": "4",
        "puertaPrimera": "B12",
        "puertaSegunda": None,
        "mostradorDesde": None,
        "mostradorHasta": None,
        "tipoAeronave": "A320",
    }
    raw.update(overrides)
    return raw


def _aena_row(
    hour: datetime,
    params: dict[str, Any] | None = AENA_PARAMS,
    raw_flights: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One bronze/aena_infovuelos row (fetched_at, params, response)."""
    return {
        "fetched_at": hour,
        "params": json.dumps(params) if params is not None else "",
        "response": json.dumps(raw_flights if raw_flights is not None else []),
    }


def _aena_delta_table(rows: list[dict[str, Any]]) -> pa.Table:
    """Build the PyArrow table simulating bronze/aena_infovuelos."""
    return pa.table(
        {
            "fetched_at": pa.array(
                [r["fetched_at"] for r in rows],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "params": pa.array([r["params"] for r in rows], type=pa.string()),
            "response": pa.array([r["response"] for r in rows], type=pa.string()),
        }
    )


def _empty_flights_table() -> pa.Table:
    return pa.table(
        {
            "response": pa.array([], type=pa.string()),
            "ingestion_date": pa.array([], type=pa.date32()),
        }
    )


def _monkeypatch_deltatable(
    monkeypatch: pytest.MonkeyPatch,
    flights_table: pa.Table,
    aena_rows: list[dict[str, Any]] | None = None,
) -> None:
    """Mock ``deltalake.DeltaTable``: opensky → *flights_table*, AENA →
    *aena_rows* table, other sources → schema-correct empty tables."""

    aena_table = _aena_delta_table(aena_rows or [])

    def _empty_table(*fields: pa.Field) -> pa.Table:
        return pa.table({f.name: pa.array([], type=f.type) for f in fields})

    _source_tables: dict[str, pa.Table] = {
        "weather_openmeteo": _empty_table(
            pa.field("fetched_at", pa.timestamp("us", tz="UTC")),
            pa.field("response", pa.string()),
        ),
        "aena_infovuelos": aena_table,
        "metar_awc": _empty_table(
            pa.field("fetched_at", pa.timestamp("us", tz="UTC")),
            pa.field("response", pa.string()),
        ),
        "holidays_nager_date": _empty_table(
            pa.field("params", pa.string()),
            pa.field("response", pa.string()),
        ),
        "holidays_python": _empty_table(
            pa.field("params", pa.string()),
            pa.field("response", pa.string()),
        ),
        "eurocontrol_pru": _empty_table(
            pa.field("params", pa.string()),
            pa.field("response", pa.string()),
        ),
        "notam_enaire": _empty_table(
            pa.field("params", pa.string()),
            pa.field("response", pa.string()),
        ),
    }

    class MockDeltaTable:
        def __init__(self, table_uri: str, storage_options: Any = None) -> None:
            self._table = flights_table  # default: flights
            for key, tbl in _source_tables.items():
                if key in table_uri:
                    self._table = tbl
                    break

        def to_pyarrow_table(self) -> pa.Table:
            return self._table

        def partitions(self) -> list[dict[str, str]]:
            if "ingestion_date" not in self._table.column_names:
                return []
            date_col = self._table.column("ingestion_date")
            unique_dates = sorted({str(d.as_py()) for d in date_col if d.as_py() is not None})
            return [{"ingestion_date": d} for d in unique_dates]

    monkeypatch.setattr("deltalake.DeltaTable", MockDeltaTable)


class _FakeDatetime(datetime):
    """Subclass with a fixed ``now()`` to control the AENA window filter."""

    fixed_now: datetime = HOUR_12

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return cls.fixed_now


# ===================================================================
# TestAenaHoursDiscovery — _get_aena_bronze_hours
# ===================================================================


class TestAenaHoursDiscovery:
    """Hour discovery from bronze/aena_infovuelos."""

    def test_hours_sorted_ascending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hours are returned sorted, deduplicated, filtered by date override."""
        rows = [
            _aena_row(HOUR_10),
            _aena_row(HOUR_10),
            _aena_row(HOUR_11),
            _aena_row(HOUR_12),
        ]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        hours = _get_aena_bronze_hours("/tmp/fake", date_override="2026-06-15")

        assert hours == [HOUR_10, HOUR_11, HOUR_12]

    def test_date_override_excludes_other_dates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only hours on the override date are returned."""
        other_day = datetime(2026, 6, 16, 8, 0, 0, tzinfo=UTC)
        rows = [_aena_row(HOUR_10), _aena_row(other_day)]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        hours = _get_aena_bronze_hours("/tmp/fake", date_override=date(2026, 6, 15))

        assert hours == [HOUR_10]

    def test_window_excludes_old_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hours outside [now - window, now] are dropped without override."""
        old_hour = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
        rows = [_aena_row(old_hour), _aena_row(HOUR_10)]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)
        monkeypatch.setattr(bronze_to_silver, "datetime", _FakeDatetime)

        hours = _get_aena_bronze_hours("/tmp/fake", window_days=1)

        assert hours == [HOUR_10]

    def test_zero_window_excludes_all_past_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """window_days=0 (window = exactly ``now``) excludes past hours."""
        rows = [_aena_row(HOUR_10)]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)
        monkeypatch.setattr(bronze_to_silver, "datetime", _FakeDatetime)

        hours = _get_aena_bronze_hours("/tmp/fake", window_days=0)

        assert hours == []

    def test_empty_table_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bronze AENA table with no rows yields no hours."""
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), [])

        assert _get_aena_bronze_hours("/tmp/fake") == []

    def test_missing_table_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing bronze table is logged and treated as no hours."""

        class _MissingDeltaTable:
            def __init__(self, table_uri: str, storage_options: Any = None) -> None:
                raise FileNotFoundError(f"Table not found: {table_uri}")

        monkeypatch.setattr("deltalake.DeltaTable", _MissingDeltaTable)

        assert _get_aena_bronze_hours("/tmp/fake") == []


# ===================================================================
# TestAenaRead — _read_bronze_aena_infovuelos
# ===================================================================


class TestAenaRead:
    """Reading + normalization of one hour of AENA Bronze rows."""

    def test_normalizes_valid_flight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A valid raw flight becomes a fully normalized doc + ICAO enrichment."""
        rows = [_aena_row(HOUR_10, raw_flights=[_raw_aena_flight()])]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        docs = _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10)

        assert len(docs) == 1
        doc = docs[0]
        # fetched_at from pyarrow is tz-aware → isoformat includes the offset
        assert doc["snapshot_at_utc"] == "2026-06-15T10:00:00+00:00"
        assert doc["source"] == "aena_infovuelos"
        assert doc["query_airport_iata"] == "MAD"
        assert doc["query_flight_type"] == "departures"
        assert doc["flight_type"] == "departures"
        assert doc["flight_number"] == "IB1234"
        assert doc["raw_flight_number"] == "1234"
        assert doc["airline_iata"] == "IB"
        assert doc["airline_icao"] == "IBE"
        assert doc["airline_name"] == "IBERIA"
        assert doc["aena_airport_iata"] == "MAD"
        assert doc["other_airport_iata"] == "BCN"
        assert doc["other_city"] == "BARCELONA"
        assert doc["scheduled_local"] == "2026-06-15T10:30:00"
        assert doc["estimated_local"] == "2026-06-15T10:35:00"
        assert doc["status"] == "Aterrizado"
        assert doc["terminal"] == "4"
        assert doc["gate_first"] == "B12"
        assert doc["gate_second"] is None
        assert doc["checkin_from"] is None
        assert doc["checkin_to"] is None
        assert doc["aircraft_type"] == "A320"
        # ICAO enrichment from the IATA→ICAO map
        assert doc["icao24_airport"] == "LEMD"

    def test_filters_rows_to_requested_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rows from other hours are excluded."""
        rows = [
            _aena_row(HOUR_10, raw_flights=[_raw_aena_flight(numVuelo="1")]),
            _aena_row(HOUR_11, raw_flights=[_raw_aena_flight(numVuelo="2")]),
        ]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        docs = _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10)

        assert [d["raw_flight_number"] for d in docs] == ["1"]

    def test_unknown_iata_gives_none_icao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An IATA code absent from the map yields icao24_airport=None."""
        rows = [_aena_row(HOUR_10, raw_flights=[_raw_aena_flight(iataAena="XXX")])]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        docs = _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10)

        assert docs[0]["icao24_airport"] is None

    def test_missing_params_or_response_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rows without params/response are silently skipped."""
        rows = [
            _aena_row(HOUR_10, params=None),
            _aena_row(HOUR_10, raw_flights=[_raw_aena_flight()]),
        ]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        docs = _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10)

        assert len(docs) == 1

    def test_invalid_json_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Malformed response JSON is dropped without crashing."""
        rows = [
            {"fetched_at": HOUR_10, "params": json.dumps(AENA_PARAMS), "response": "{bad"},
            _aena_row(HOUR_10, raw_flights=[_raw_aena_flight()]),
        ]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        docs = _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10)

        assert len(docs) == 1

    def test_non_list_response_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A response that is valid JSON but not a list is skipped."""
        rows = [_aena_row(HOUR_10, raw_flights={"not": "a list"})]
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), rows)

        docs = _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10)

        assert docs == []

    def test_empty_table_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty bronze AENA table yields no docs."""
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), [])

        assert _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10) == []

    def test_missing_table_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing bronze table yields no docs."""

        class _MissingDeltaTable:
            def __init__(self, table_uri: str, storage_options: Any = None) -> None:
                raise FileNotFoundError(f"Table not found: {table_uri}")

        monkeypatch.setattr("deltalake.DeltaTable", _MissingDeltaTable)

        assert _read_bronze_aena_infovuelos("/tmp/fake", HOUR_10) == []


# ===================================================================
# TestAenaProcess — _process_aena_hours
# ===================================================================


class TestAenaProcess:
    """Per-hour processing: write + checkpoint, failures isolated per hour."""

    def _mock_hour_dependencies(
        self,
        monkeypatch: pytest.MonkeyPatch,
        docs_per_hour: list[list[dict[str, Any]]],
        raise_on: set[int] | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        """Mock read/write/checkpoint; returns (write mock, add mock)."""

        def fake_read(_delta_root: str, hour: datetime) -> list[dict[str, Any]]:
            idx = [HOUR_10, HOUR_11, HOUR_12].index(hour)
            if raise_on and idx in raise_on:
                raise RuntimeError(f"boom {hour}")
            return docs_per_hour[idx]

        monkeypatch.setattr(bronze_to_silver, "_read_bronze_aena_infovuelos", fake_read)
        mock_write = MagicMock(return_value=3)
        monkeypatch.setattr(bronze_to_silver, "write_aena_infovuelos", mock_write)
        mock_add = MagicMock()
        monkeypatch.setattr(bronze_to_silver, "add_to_checkpoint_set", mock_add)
        return mock_write, mock_add

    def test_writes_and_checkpoints_each_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each hour is written then checkpointed with its hour key."""
        _, mock_add = self._mock_hour_dependencies(
            monkeypatch,
            docs_per_hour=[[], [], []],
        )

        failures = _process_aena_hours("/tmp/fake", [HOUR_10, HOUR_11], dry_run=False)

        assert failures == 0
        assert mock_add.call_args_list == [
            ((CHECKPOINT_COLLECTION_AENA, "2026-06-15T10:00"),),
            ((CHECKPOINT_COLLECTION_AENA, "2026-06-15T11:00"),),
        ]

    def test_writes_receive_hour_docs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The docs read for each hour are passed to the Silver writer."""
        docs = [{"flight_number": "IB1"}, {"flight_number": "IB2"}]
        mock_write, _ = self._mock_hour_dependencies(
            monkeypatch,
            docs_per_hour=[docs, []],
        )

        _process_aena_hours("/tmp/fake", [HOUR_10], dry_run=False)

        mock_write.assert_called_once_with(docs)

    def test_dry_run_reads_but_does_not_write_or_checkpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run reads and logs but never writes nor checkpoints."""
        mock_write, mock_add = self._mock_hour_dependencies(
            monkeypatch,
            docs_per_hour=[[{"flight_number": "IB1"}], []],
        )

        failures = _process_aena_hours("/tmp/fake", [HOUR_10], dry_run=True)

        assert failures == 0
        mock_write.assert_not_called()
        mock_add.assert_not_called()

    def test_failure_isolated_per_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing hour is not checkpointed; later hours still process."""
        mock_write, mock_add = self._mock_hour_dependencies(
            monkeypatch,
            docs_per_hour=[[]],
            raise_on={0},
        )

        # Only HOUR_11 fails; HOUR_12 must still be processed
        def failing_read(_delta_root: str, hour: datetime) -> list[dict[str, Any]]:
            if hour == HOUR_11:
                raise RuntimeError(f"boom {hour}")
            return []

        monkeypatch.setattr(bronze_to_silver, "_read_bronze_aena_infovuelos", failing_read)

        failures = _process_aena_hours("/tmp/fake", [HOUR_11, HOUR_12], dry_run=False)

        assert failures == 1
        mock_write.assert_called_once()  # only HOUR_12 written
        mock_add.assert_called_once_with(CHECKPOINT_COLLECTION_AENA, "2026-06-15T12:00")

    def test_empty_pending_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No pending hours → no writes, zero failures."""
        mock_write, mock_add = self._mock_hour_dependencies(
            monkeypatch,
            docs_per_hour=[],
        )

        assert _process_aena_hours("/tmp/fake", [], dry_run=False) == 0
        mock_write.assert_not_called()
        mock_add.assert_not_called()


# ===================================================================
# TestAenaMainIntegration — main() AENA route
# ===================================================================


class TestAenaMainIntegration:
    """main() end-to-end AENA route: pending filtering, exit codes."""

    def _mock_main_dependencies(
        self,
        monkeypatch: pytest.MonkeyPatch,
        aena_hours: list[datetime],
        aena_checkpoints: set[str] | None = None,
        aena_failures: int = 0,
    ) -> MagicMock:
        """Patch main() deps; returns the _process_aena_hours mock."""
        _monkeypatch_deltatable(monkeypatch, _empty_flights_table(), [])

        checkpoints = {
            CHECKPOINT_COLLECTION: set(),
            CHECKPOINT_COLLECTION_AENA: aena_checkpoints or set(),
            CHECKPOINT_COLLECTION_METAR: set(),
            CHECKPOINT_COLLECTION_HOLIDAYS: set(),
            CHECKPOINT_COLLECTION_EUROCONTROL: set(),
            CHECKPOINT_COLLECTION_NOTAM: set(),
        }
        monkeypatch.setattr(bronze_to_silver, "get_checkpoint_set", lambda col: checkpoints[col])
        monkeypatch.setattr(
            bronze_to_silver,
            "_get_aena_bronze_hours",
            lambda *args, **kwargs: aena_hours,
        )
        mock_process = MagicMock(return_value=aena_failures)
        monkeypatch.setattr(bronze_to_silver, "_process_aena_hours", mock_process)
        monkeypatch.setattr(bronze_to_silver, "write_flights_silver", MagicMock(return_value=0))
        monkeypatch.setattr(bronze_to_silver, "add_to_checkpoint_set", MagicMock())
        monkeypatch.setattr(bronze_to_silver, "close_silver", MagicMock())
        return mock_process

    def test_main_processes_pending_aena_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Uncheckpointed hours are processed and main exits 0."""
        mock_process = self._mock_main_dependencies(
            monkeypatch,
            aena_hours=[HOUR_10, HOUR_11],
        )

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 0
        mock_process.assert_called_once()
        args = mock_process.call_args.args
        assert args[1] == [HOUR_10, HOUR_11]  # pending hours
        assert args[2] is False  # dry_run

    def test_main_skips_checkpointed_aena_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hours already checkpointed are excluded from pending."""
        mock_process = self._mock_main_dependencies(
            monkeypatch,
            aena_hours=[HOUR_10, HOUR_11, HOUR_12],
            aena_checkpoints={"2026-06-15T10:00", "2026-06-15T12:00"},
        )

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 0
        pending = mock_process.call_args.args[1]
        assert pending == [HOUR_11]

    def test_main_all_aena_hours_processed_skips_processing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When every hour is checkpointed, _process_aena_hours is not called."""
        mock_process = self._mock_main_dependencies(
            monkeypatch,
            aena_hours=[HOUR_10],
            aena_checkpoints={"2026-06-15T10:00"},
        )

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 0
        mock_process.assert_not_called()

    def test_main_aena_failures_set_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Failed hours make main return non-zero (unless dry-run)."""
        self._mock_main_dependencies(
            monkeypatch,
            aena_hours=[HOUR_10],
            aena_failures=2,
        )

        rc = main(["--date", "2026-06-15", "--delta-root", "/tmp/fake"])

        assert rc == 1

    def test_main_dry_run_ignores_aena_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In dry-run, AENA failures do not affect the exit code."""
        mock_process = self._mock_main_dependencies(
            monkeypatch,
            aena_hours=[HOUR_10],
            aena_failures=2,
        )

        rc = main(
            [
                "--date",
                "2026-06-15",
                "--delta-root",
                "/tmp/fake",
                "--dry-run",
            ]
        )

        assert rc == 0
        assert mock_process.call_args.args[2] is True


# ===================================================================
# TestAenaSilverWrite — write_aena_infovuelos (storage_silver)
# ===================================================================


class TestAenaSilverWrite:
    """MongoDB write path for AENA docs."""

    def test_write_empty_returns_zero(self) -> None:
        """Writing an empty doc list returns 0."""
        from aeropredict.opensky.storage_silver import write_aena_infovuelos

        assert write_aena_infovuelos([]) == 0

    def test_write_inserts_many_ordered_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-empty list inserts with ordered=False and adds ingested_at."""
        mock_collection = MagicMock()
        mock_collection.insert_many.return_value = MagicMock(inserted_ids=["id1", "id2"])
        monkeypatch.setattr(
            "aeropredict.opensky.storage_silver._get_aena_collection",
            lambda: mock_collection,
        )

        from aeropredict.opensky.storage_silver import write_aena_infovuelos

        docs = [{"flight_number": "IB1"}, {"flight_number": "IB2"}]
        n = write_aena_infovuelos(docs)

        assert n == 2
        mock_collection.insert_many.assert_called_once()
        assert mock_collection.insert_many.call_args.kwargs.get("ordered") is False
        for doc in docs:
            assert "ingested_at" in doc
