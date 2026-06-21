"""Tests for ``scripts/extract_to_bronze.py`` — Bronze layer extraction.

Covers: API call orchestration, checkpoint logic, Delta Lake writes,
idempotency, and rate-limit handling.  Mock OpenSky API at the
``fetch_arrivals_raw`` / ``fetch_departures_raw`` seam so the test
never reaches real HTTP.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from deltalake import DeltaTable

# Allow import from scripts/ (no __init__.py there)
_scripts_root = str(Path(__file__).resolve().parent.parent)
if _scripts_root not in sys.path:
    sys.path.insert(0, _scripts_root)

from aeropredict.opensky.storage import RAW_SCHEMA, write_raw  # noqa: E402
from scripts.extract_to_bronze import (  # noqa: E402
    SPANISH_AIRPORT_CODES,
    _count_bronze_rows,
    _extract_day,
    _get_bronze_dates,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_flight(**overrides: Any) -> dict[str, Any]:
    """A single sample flight dict matching real mock data structure."""
    base = {
        "icao24": "344fed",
        "firstSeen": 1781240817,
        "lastSeen": 1781245353,
        "estDepartureAirport": "LEVC",
        "estArrivalAirport": "LEMD",
        "callsign": "ANE4597",
    }
    base.update(overrides)
    return base


def _flights_list(n: int = 3) -> list[dict[str, Any]]:
    """Build a list of *n* distinct sample flight dicts."""
    return [
        _sample_flight(icao24=f"aaaa{i:02d}", callsign=f"FLT{i:04d}")
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_all_deps(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Patch *all* external dependencies so no real IO or waits occur.

    ``time.sleep`` is also patched to avoid the 5 s delay between
    airport iterations.

    Individual tests that need a real subsystem (e.g. real Delta Lake
    writes) can call this fixture and then re-patch the specific seam.
    """
    deps: dict[str, MagicMock] = {}

    # Eliminate the 5 s sleep between airport iterations
    monkeypatch.setattr("scripts.extract_to_bronze.time.sleep", MagicMock())

    targets: dict[str, Any] = {
        "fetch_arrivals_raw": MagicMock(
            return_value=(
                "/flights/arrival", {"airport": "LEMD"}, _flights_list(3)
            ),
        ),
        "fetch_departures_raw": MagicMock(
            return_value=(
                "/flights/departure", {"airport": "LEMD"}, _flights_list(2)
            ),
        ),
        "write_raw": MagicMock(return_value=1),
        "is_airport_empty": MagicMock(return_value=False),
        "cache_empty_airport": MagicMock(),
        "get_checkpoint_dict": MagicMock(return_value={}),
        "save_checkpoint_dict_entry": MagicMock(),
        "get_delta_root": MagicMock(return_value="/tmp/test_delta_extract"),
    }
    for name, mock_obj in targets.items():
        monkeypatch.setattr(f"scripts.extract_to_bronze.{name}", mock_obj)
        deps[name] = mock_obj
    return deps


# ═══════════════════════════════════════════════════════════════════════
# 1. TestExtractApiCalls
# ═══════════════════════════════════════════════════════════════════════


class TestExtractApiCalls:
    """Orchestration of OpenSky API calls (arrivals + departures)."""

    def test_fetch_arrivals_and_departures(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """Both endpoints are called for every Spanish airport."""
        mock_client = MagicMock()
        target_date = datetime.date(2026, 6, 12)
        n_airports = len(SPANISH_AIRPORT_CODES)

        result = _extract_day(mock_client, target_date, dry_run=False)

        # arrivals + departures called for each airport = 2 x n_airports
        assert mock_all_deps["fetch_arrivals_raw"].call_count == n_airports
        assert mock_all_deps["fetch_departures_raw"].call_count == n_airports

        # write_raw called twice per airport (arrivals + departures)
        assert mock_all_deps["write_raw"].call_count == n_airports * 2

        assert result["airports"] == n_airports
        assert result["date"] == "2026-06-12"
        assert len(result["airports_done"]) == n_airports

    def test_empty_api_response_cached(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """When the API returns an empty list the result is cached as empty."""
        mock_all_deps["fetch_arrivals_raw"].return_value = (
            "/flights/arrival", {}, []
        )
        mock_all_deps["fetch_departures_raw"].return_value = (
            "/flights/departure", {}, []
        )

        target_date = datetime.date(2026, 6, 12)
        _extract_day(MagicMock(), target_date, dry_run=False)

        assert mock_all_deps["cache_empty_airport"].called

    def test_dry_run_skips_all_io(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """Dry-run mode never calls API, write, or checkpoint functions."""
        target_date = datetime.date(2026, 6, 12)
        result = _extract_day(MagicMock(), target_date, dry_run=True)

        assert not mock_all_deps["fetch_arrivals_raw"].called
        assert not mock_all_deps["fetch_departures_raw"].called
        assert not mock_all_deps["write_raw"].called
        assert not mock_all_deps["save_checkpoint_dict_entry"].called
        assert result["airports"] == len(SPANISH_AIRPORT_CODES)
        assert len(result["errors"]) == 0

    def test_previously_empty_airport_skipped(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """Airport marked as 'empty' (cache) is not re-fetched."""
        mock_all_deps["is_airport_empty"].return_value = True

        target_date = datetime.date(2026, 6, 12)
        _extract_day(MagicMock(), target_date, dry_run=False)

        assert mock_all_deps["is_airport_empty"].called
        assert not mock_all_deps["fetch_arrivals_raw"].called
        assert not mock_all_deps["fetch_departures_raw"].called


# ═══════════════════════════════════════════════════════════════════════
# 2. TestCheckpointLogic
# ═══════════════════════════════════════════════════════════════════════


class TestCheckpointLogic:
    """Checkpoint advancement and persistence."""

    def test_checkpoint_prevents_re_extraction(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """Airports listed in checkpoint are skipped (no API call)."""
        first_airport = SPANISH_AIRPORT_CODES[0]
        mock_all_deps["get_checkpoint_dict"].return_value = {
            "2026-06-12": [first_airport],
        }

        target_date = datetime.date(2026, 6, 12)
        _extract_day(MagicMock(), target_date, dry_run=False)

        expected_calls = len(SPANISH_AIRPORT_CODES) - 1
        assert mock_all_deps["fetch_arrivals_raw"].call_count == expected_calls
        assert (
            mock_all_deps["fetch_departures_raw"].call_count == expected_calls
        )

    def test_all_airports_checkpointed_skips_all(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """When *every* airport is in checkpoint, zero API calls happen."""
        mock_all_deps["get_checkpoint_dict"].return_value = {
            "2026-06-12": list(SPANISH_AIRPORT_CODES),
        }

        target_date = datetime.date(2026, 6, 12)
        _extract_day(MagicMock(), target_date, dry_run=False)

        assert not mock_all_deps["fetch_arrivals_raw"].called
        assert not mock_all_deps["fetch_departures_raw"].called
        assert not mock_all_deps["write_raw"].called

    def test_returns_airports_done_after_extraction(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """After extraction airports_done list is fully populated.

        ``save_checkpoint_dict_entry`` is called by ``main()`` (not by
        ``_extract_day()``) — this test verifies the return value that
        ``main()`` uses to decide whether to persist the checkpoint.
        """
        target_date = datetime.date(2026, 6, 12)
        result = _extract_day(MagicMock(), target_date, dry_run=False)

        assert len(result["airports_done"]) == len(SPANISH_AIRPORT_CODES)
        assert result["airports_done"] == SPANISH_AIRPORT_CODES

    def test_all_airports_checkpointed_returns_done(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """All-checkpointed extraction still returns airports_done."""
        mock_all_deps["get_checkpoint_dict"].return_value = {
            "2026-06-12": list(SPANISH_AIRPORT_CODES),
        }
        target_date = datetime.date(2026, 6, 12)
        result = _extract_day(MagicMock(), target_date, dry_run=False)

        assert len(result["airports_done"]) == len(SPANISH_AIRPORT_CODES)

    def test_force_flag_ignores_checkpoint(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """force=True causes extraction regardless of checkpoint."""
        monkeypatch.setattr(
            "scripts.extract_to_bronze.time.sleep", MagicMock(),
        )

        mock_arrivals = MagicMock(
            return_value=(
                "/flights/arrival", {"airport": "LEMD"}, _flights_list(1),
            ),
        )
        mock_departures = MagicMock(
            return_value=(
                "/flights/departure", {"airport": "LEMD"}, _flights_list(1),
            ),
        )
        mock_write = MagicMock(return_value=1)

        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_arrivals_raw", mock_arrivals,
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_departures_raw", mock_departures,
        )
        monkeypatch.setattr("scripts.extract_to_bronze.write_raw", mock_write)
        monkeypatch.setattr(
            "scripts.extract_to_bronze.is_airport_empty",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_checkpoint_dict",
            MagicMock(return_value={
                "2026-06-12": list(SPANISH_AIRPORT_CODES),
            }),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_delta_root",
            MagicMock(return_value="/tmp/test"),
        )

        target_date = datetime.date(2026, 6, 12)
        _extract_day(MagicMock(), target_date, dry_run=False, force=True)

        n = len(SPANISH_AIRPORT_CODES)
        assert mock_arrivals.call_count == n
        assert mock_departures.call_count == n
        assert mock_write.call_count == n * 2


# ═══════════════════════════════════════════════════════════════════════
# 3. TestDeltaLakeWrites
# ═══════════════════════════════════════════════════════════════════════


class TestDeltaLakeWrites:
    """Delta Lake table creation, schema, and partitioning."""

    def test_write_raw_creates_table(self, delta_lake_manager: str) -> None:
        """write_raw creates a valid Delta table at the expected path."""
        n = write_raw(
            endpoint="/flights/arrival",
            params={"airport": "LEMD"},
            response_data=_flights_list(5),
            base_path=delta_lake_manager,
        )
        assert n == 1  # one row per call

        table_uri = f"{delta_lake_manager}/bronze/opensky"
        dt = DeltaTable(table_uri)

        assert dt.metadata().partition_columns == ["ingestion_date"]

    def test_write_raw_schema_matches_raw_schema(
        self, delta_lake_manager: str,
    ) -> None:
        """The written Delta table schema matches ``RAW_SCHEMA``."""
        write_raw(
            endpoint="/flights/departure",
            params={"airport": "LEBL"},
            response_data=_flights_list(2),
            base_path=delta_lake_manager,
        )

        table_uri = f"{delta_lake_manager}/bronze/opensky"
        dt = DeltaTable(table_uri)
        arrow_schema = dt.to_pyarrow_table().schema

        for field in RAW_SCHEMA:
            assert field.name in arrow_schema.names, (
                f"Field {field.name} missing from written table"
            )

    def test_write_raw_empty_response_returns_zero(
        self, delta_lake_manager: str,
    ) -> None:
        """An empty response list yields 0 rows written (early return)."""
        n = write_raw(
            endpoint="/flights/arrival",
            params={"airport": "LEMD"},
            response_data=[],
            base_path=delta_lake_manager,
        )
        assert n == 0

        table_uri = f"{delta_lake_manager}/bronze/opensky"
        with pytest.raises(Exception):  # noqa: B017 — DeltaTable doesn't expose public exception types
            DeltaTable(table_uri)

    def test_get_bronze_dates_returns_written_dates(
        self, delta_lake_manager: str,
    ) -> None:
        """``_get_bronze_dates`` discovers dates with data in Bronze."""
        write_raw(
            endpoint="/flights/arrival",
            params={"airport": "LEMD"},
            response_data=_flights_list(1),
            base_path=delta_lake_manager,
        )

        today_str = datetime.date.today().isoformat()
        dates = _get_bronze_dates(delta_lake_manager)
        assert today_str in dates

    def test_count_bronze_rows_after_write(
        self, delta_lake_manager: str,
    ) -> None:
        """Correct row count for a date after multiple writes."""
        for _ in range(3):
            write_raw(
                endpoint="/flights/arrival",
                params={"airport": "LEMD"},
                response_data=_flights_list(1),
                base_path=delta_lake_manager,
            )

        today_str = datetime.date.today().isoformat()
        count = _count_bronze_rows(delta_lake_manager, today_str)
        assert count == 3

    def test_get_bronze_dates_empty_table(self) -> None:
        """Returns empty list when table does not exist."""
        dates = _get_bronze_dates("/nonexistent/path")
        assert dates == []

    def test_count_bronze_rows_empty_table(self) -> None:
        """Returns 0 when table does not exist."""
        count = _count_bronze_rows("/nonexistent/path", "2026-06-12")
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════
# 4. TestIdempotency
# ═══════════════════════════════════════════════════════════════════════


class TestIdempotency:
    """Safety on repeated extraction (same data, same checkpoint)."""

    def test_extract_twice_produces_matching_rows(
        self, delta_lake_manager: str, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calling ``write_raw`` twice with identical input produces
        matching Delta rows."""
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_delta_root",
            MagicMock(return_value=delta_lake_manager),
        )

        data = _flights_list(3)

        for _ in range(2):
            write_raw(
                endpoint="/flights/arrival",
                params={"airport": "LEMD"},
                response_data=data,
                base_path=delta_lake_manager,
            )

        table_uri = f"{delta_lake_manager}/bronze/opensky"
        dt = DeltaTable(table_uri)
        table = dt.to_pyarrow_table()

        assert len(table) == 2

        responses = [
            json.loads(r) for r in table.column("response").to_pylist()
        ]
        assert all(r == data for r in responses)

    def test_checkpoint_then_extract_skips_airports(
        self, mock_all_deps: dict[str, MagicMock],
    ) -> None:
        """Extraction after partial checkpoint only fetches remaining."""
        target_date = datetime.date(2026, 6, 12)

        # First extraction -- no checkpoint
        _extract_day(MagicMock(), target_date, dry_run=False)

        # Second extraction -- first 5 airports now in checkpoint
        first_5 = SPANISH_AIRPORT_CODES[:5]
        mock_all_deps["get_checkpoint_dict"].return_value = {
            "2026-06-12": first_5,
        }
        mock_all_deps["fetch_arrivals_raw"].reset_mock()
        mock_all_deps["fetch_departures_raw"].reset_mock()

        _extract_day(MagicMock(), target_date, dry_run=False)

        expected = len(SPANISH_AIRPORT_CODES) - 5
        assert mock_all_deps["fetch_arrivals_raw"].call_count == expected
        assert mock_all_deps["fetch_departures_raw"].call_count == expected


# ═══════════════════════════════════════════════════════════════════════
# 5. TestRateLimitHandling
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimitHandling:
    """Behaviour when OpenSky returns HTTP 429 (rate limited)."""

    def test_429_on_arrivals_breaks_loop(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 429 on arrivals stops extraction for the entire day."""
        monkeypatch.setattr(
            "scripts.extract_to_bronze.time.sleep", MagicMock(),
        )

        mock_arrivals = MagicMock()
        mock_arrivals.side_effect = RuntimeError("429 Too Many Requests")

        mock_departures = MagicMock(
            return_value=(
                "/flights/departure", {"airport": "LEMD"}, _flights_list(1),
            ),
        )
        mock_write = MagicMock(return_value=1)

        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_arrivals_raw", mock_arrivals,
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_departures_raw", mock_departures,
        )
        monkeypatch.setattr("scripts.extract_to_bronze.write_raw", mock_write)
        monkeypatch.setattr(
            "scripts.extract_to_bronze.is_airport_empty",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_checkpoint_dict",
            MagicMock(return_value={}),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_delta_root",
            MagicMock(return_value="/tmp/test"),
        )

        target_date = datetime.date(2026, 6, 12)
        result = _extract_day(MagicMock(), target_date, dry_run=False)

        assert len(result["errors"]) == 1
        assert "429" in result["errors"][0]["error"]

    def test_429_on_departures_breaks_loop(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 429 on departures stops extraction; arrivals already written."""
        monkeypatch.setattr(
            "scripts.extract_to_bronze.time.sleep", MagicMock(),
        )

        mock_arrivals = MagicMock(
            return_value=(
                "/flights/arrival", {"airport": "LEMD"}, _flights_list(1),
            ),
        )
        mock_departures = MagicMock()
        mock_departures.side_effect = RuntimeError("429 Too Many Requests")

        mock_write = MagicMock(return_value=1)

        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_arrivals_raw", mock_arrivals,
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_departures_raw", mock_departures,
        )
        monkeypatch.setattr("scripts.extract_to_bronze.write_raw", mock_write)
        monkeypatch.setattr(
            "scripts.extract_to_bronze.is_airport_empty",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_checkpoint_dict",
            MagicMock(return_value={}),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_delta_root",
            MagicMock(return_value="/tmp/test"),
        )

        target_date = datetime.date(2026, 6, 12)
        result = _extract_day(MagicMock(), target_date, dry_run=False)

        assert len(result["errors"]) == 1
        assert "429" in result["errors"][0]["error"]
        assert mock_write.called

    def test_non_429_errors_collected_and_continue(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-429 exceptions are collected; extraction does NOT break."""
        monkeypatch.setattr(
            "scripts.extract_to_bronze.time.sleep", MagicMock(),
        )

        mock_client = MagicMock()
        mock_arrivals = MagicMock()
        mock_departures = MagicMock()

        calls: list[int] = [0]

        def _failing_arrivals(
            *args: Any, **kwargs: Any,
        ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("Connection timeout")
            return ("/flights/arrival", {"airport": "LEMD"}, _flights_list(1))

        mock_arrivals.side_effect = _failing_arrivals
        mock_departures.return_value = (
            "/flights/departure", {"airport": "LEMD"}, _flights_list(1),
        )
        mock_write = MagicMock(return_value=1)

        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_arrivals_raw", mock_arrivals,
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.fetch_departures_raw", mock_departures,
        )
        monkeypatch.setattr("scripts.extract_to_bronze.write_raw", mock_write)
        monkeypatch.setattr(
            "scripts.extract_to_bronze.is_airport_empty",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_checkpoint_dict",
            MagicMock(return_value={}),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.save_checkpoint_dict_entry",
            MagicMock(),
        )
        monkeypatch.setattr(
            "scripts.extract_to_bronze.get_delta_root",
            MagicMock(return_value="/tmp/test"),
        )

        target_date = datetime.date(2026, 6, 12)
        result = _extract_day(mock_client, target_date, dry_run=False)

        assert len(result["errors"]) == 1
        assert "Connection timeout" in result["errors"][0]["error"]
        assert result["airports"] == len(SPANISH_AIRPORT_CODES)
