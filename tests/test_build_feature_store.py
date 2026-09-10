"""Tests for scripts/build_feature_store.py — pure helpers & integration fakes.

Covers:
    - compute_retraso (delay derivation)
    - compute_retraso_10_min (binary target mapping)
    - select_cut_time_metar (data-leakage-safe METAR selection)
    - scheduled_to_cut_epoch (local→UTC conversion)

No network, no real DB, no real PostgreSQL.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_feature_store as bfs

# ------------------------------------------------------------------
# compute_retraso
# ------------------------------------------------------------------


class TestComputeRetraso:
    """Unit tests for compute_retraso."""

    def test_positive_delay(self) -> None:
        """Given: scheduled 12:00, estimated 12:35. When: compute. Then: 35.0."""
        result = bfs.compute_retraso("2026-08-03T12:00:00", "2026-08-03T12:35:00")
        assert result == 35.0

    def test_zero_delay(self) -> None:
        """Given: same scheduled and estimated. When: compute. Then: 0.0."""
        result = bfs.compute_retraso("2026-08-03T12:00:00", "2026-08-03T12:00:00")
        assert result == 0.0

    def test_negative_delay(self) -> None:
        """Given: estimated before scheduled. When: compute. Then: negative."""
        result = bfs.compute_retraso("2026-08-03T12:00:00", "2026-08-03T11:50:00")
        assert result == -10.0

    def test_none_estimated(self) -> None:
        """Given: no estimated_local. When: compute. Then: None."""
        result = bfs.compute_retraso("2026-08-03T12:00:00", None)
        assert result is None

    def test_empty_estimated(self) -> None:
        """Given: empty estimated_local. When: compute. Then: None."""
        result = bfs.compute_retraso("2026-08-03T12:00:00", "")
        assert result is None

    def test_unparseable_estimated(self) -> None:
        """Given: bad estimated_local. When: compute. Then: None."""
        result = bfs.compute_retraso("2026-08-03T12:00:00", "not-a-date")
        assert result is None

    def test_unparseable_scheduled(self) -> None:
        """Given: bad scheduled_local. When: compute. Then: None."""
        result = bfs.compute_retraso("bad", "2026-08-03T12:00:00")
        assert result is None

    def test_rounding(self) -> None:
        """Given: 35 min 22 sec delay. When: compute. Then: 35.4 (rounded)."""
        result = bfs.compute_retraso("2026-08-03T12:00:00", "2026-08-03T12:35:22")
        assert result == 35.4

    def test_none_scheduled(self) -> None:
        """Given: None scheduled. When: compute. Then: None (TypeError caught)."""
        result = bfs.compute_retraso(None, "2026-08-03T12:00:00")  # type: ignore[arg-type]
        assert result is None


# ------------------------------------------------------------------
# compute_retraso_10_min
# ------------------------------------------------------------------


class TestComputeRetraso10Min:
    """Unit tests for compute_retraso_10_min binary target mapping."""

    def test_no_retraso_below_threshold(self) -> None:
        """Given: 9.9 min delay. When: map. Then: 'NO_RETRASO'."""
        assert bfs.compute_retraso_10_min(9.9) == "NO_RETRASO"

    def test_retraso_at_threshold(self) -> None:
        """Given: 10.0 min delay. When: map. Then: 'RETRASO'."""
        assert bfs.compute_retraso_10_min(10.0) == "RETRASO"

    def test_retraso_above_threshold(self) -> None:
        """Given: 35.0 min delay. When: map. Then: 'RETRASO'."""
        assert bfs.compute_retraso_10_min(35.0) == "RETRASO"

    def test_no_retraso_zero(self) -> None:
        """Given: 0.0 min delay. When: map. Then: 'NO_RETRASO'."""
        assert bfs.compute_retraso_10_min(0.0) == "NO_RETRASO"

    def test_no_retraso_negative(self) -> None:
        """Given: -5.0 min delay (early). When: map. Then: 'NO_RETRASO'."""
        assert bfs.compute_retraso_10_min(-5.0) == "NO_RETRASO"

    def test_none_delay(self) -> None:
        """Given: None delay. When: map. Then: None."""
        assert bfs.compute_retraso_10_min(None) is None

    def test_boundary_9_99(self) -> None:
        """Given: 9.99 min delay. When: map. Then: 'NO_RETRASO'."""
        assert bfs.compute_retraso_10_min(9.99) == "NO_RETRASO"

    def test_boundary_10_01(self) -> None:
        """Given: 10.01 min delay. When: map. Then: 'RETRASO'."""
        assert bfs.compute_retraso_10_min(10.01) == "RETRASO"


# ------------------------------------------------------------------
# select_cut_time_metar
# ------------------------------------------------------------------


def _metar_row(obs_time: int, temp: float = 25.0) -> dict:
    """Helper to create a minimal METAR row dict."""
    return {"obs_time": obs_time, "temp": temp, "dewp": 10.0, "relh": 40.0}


class TestSelectCutTimeMetar:
    """Unit tests for select_cut_time_metar (data-leakage-safe join)."""

    def test_returns_latest_before_cut(self) -> None:
        """Given: rows at t=100,200,300 and cut=250. When: select. Then: row at t=200."""
        rows = [_metar_row(100), _metar_row(200), _metar_row(300)]
        result = bfs.select_cut_time_metar(rows, 250)
        assert result is not None
        assert result["obs_time"] == 200

    def test_exact_cut_matches(self) -> None:
        """Given: row at t=200 and cut=200. When: select. Then: row at t=200."""
        rows = [_metar_row(100), _metar_row(200)]
        result = bfs.select_cut_time_metar(rows, 200)
        assert result is not None
        assert result["obs_time"] == 200

    def test_no_observation_before_cut(self) -> None:
        """Given: rows at t=300,400 and cut=200. When: select. Then: None."""
        rows = [_metar_row(300), _metar_row(400)]
        result = bfs.select_cut_time_metar(rows, 200)
        assert result is None

    def test_empty_list(self) -> None:
        """Given: empty metar list. When: select. Then: None."""
        result = bfs.select_cut_time_metar([], 1000)
        assert result is None

    def test_never_selects_after_cut(self) -> None:
        """Given: rows at t=100,200,300 and cut=150. When: select. Then: t=100, NOT t=200."""
        rows = [_metar_row(100), _metar_row(200), _metar_row(300)]
        result = bfs.select_cut_time_metar(rows, 150)
        assert result is not None
        assert result["obs_time"] == 100
        # Verify we never selected the row with obs_time > cut
        assert result["obs_time"] <= 150

    def test_single_row_before_cut(self) -> None:
        """Given: one row at t=100 and cut=200. When: select. Then: row at t=100."""
        rows = [_metar_row(100)]
        result = bfs.select_cut_time_metar(rows, 200)
        assert result is not None
        assert result["obs_time"] == 100

    def test_skips_none_obs_time(self) -> None:
        """Given: rows with obs_time=None interspersed. When: select. Then: correct row."""
        rows = [{"obs_time": None}, _metar_row(100), {"obs_time": None}, _metar_row(200)]
        result = bfs.select_cut_time_metar(rows, 150)
        assert result is not None
        assert result["obs_time"] == 100

    def test_all_none_obs_time(self) -> None:
        """Given: all rows have obs_time=None. When: select. Then: None."""
        rows = [{"obs_time": None}, {"obs_time": None}]
        result = bfs.select_cut_time_metar(rows, 1000)
        assert result is None

    def test_preserves_row_data(self) -> None:
        """Given: matching row with temp=27.3. When: select. Then: row preserved."""
        rows = [_metar_row(100, temp=27.3), _metar_row(200, temp=18.0)]
        result = bfs.select_cut_time_metar(rows, 150)
        assert result is not None
        assert result["temp"] == 27.3
        assert result["dewp"] == 10.0
        assert result["relh"] == 40.0


# ------------------------------------------------------------------
# scheduled_to_cut_epoch
# ------------------------------------------------------------------


class TestScheduledToCutEpoch:
    """Unit tests for scheduled_to_cut_epoch (local→UTC conversion)."""

    def test_midday_madrid_in_summer(self) -> None:
        """Given: 2026-08-03T12:00:00 (CEST = UTC+2). When: convert. Then: 10:00 UTC."""
        epoch = bfs.scheduled_to_cut_epoch("2026-08-03T12:00:00")
        # Verify: the UTC hour should be 10 (CEST offset = +2 in summer)
        utc_dt = datetime.fromtimestamp(epoch, tz=UTC)
        assert utc_dt.hour == 10
        assert utc_dt.minute == 0

    def test_midday_madrid_in_winter(self) -> None:
        """Given: 2026-01-15T12:00:00 (CET = UTC+1). When: convert. Then: 11:00 UTC."""
        epoch = bfs.scheduled_to_cut_epoch("2026-01-15T12:00:00")
        utc_dt = datetime.fromtimestamp(epoch, tz=UTC)
        assert utc_dt.hour == 11
        assert utc_dt.minute == 0

    def test_midnight(self) -> None:
        """Given: 2026-08-03T00:00:00 (CEST). When: convert. Then: previous day 22:00 UTC."""
        epoch = bfs.scheduled_to_cut_epoch("2026-08-03T00:00:00")
        utc_dt = datetime.fromtimestamp(epoch, tz=UTC)
        assert utc_dt.day == 2
        assert utc_dt.hour == 22

    def test_returns_float(self) -> None:
        """Given: valid local time. When: convert. Then: float."""
        epoch = bfs.scheduled_to_cut_epoch("2026-08-03T12:00:00")
        assert isinstance(epoch, float)


# ------------------------------------------------------------------
# Integration: build_feature_store with fake DB
# ------------------------------------------------------------------


class FakeCursor:
    """Minimal cursor stub for integration tests."""

    def __init__(self, results: list[tuple] | None = None):
        self._results = results or []
        self.description: list[tuple] = []
        self.executed: list[tuple] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql, params))
        if self._results:
            self.description = [("col",)] * len(self._results[0])

    def fetchall(self) -> list[tuple]:
        return list(self._results)

    def fetchone(self) -> tuple | None:
        return self._results[0] if self._results else None

    def close(self) -> None:
        pass


class FakeConn:
    """Fake PostgreSQL connection for integration tests."""

    def __init__(self, departures: list[tuple] | None = None):
        self.departures = departures or []
        self.insert_calls: list[tuple] = []
        self.commits = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        # If first query is the deduped departures query, return those
        if self.departures and not self.insert_calls:
            cur = FakeCursor(self.departures)
            cur.description = [
                ("flight_number",), ("aena_airport_iata",), ("flight_type",),
                ("scheduled_local",), ("estimated_local",), ("airline_iata",),
                ("other_airport_iata",),
            ]
            return cur
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1


def _make_departure_row(
    flight_number: str = "IB1234",
    airport_iata: str = "MAD",
    scheduled_local: str = "2026-08-03T12:00:00",
    estimated_local: str | None = "2026-08-03T12:35:00",
    airline_iata: str = "IB",
    other_airport_iata: str = "BCN",
) -> dict:
    """Create a fake departure row dict matching _fetch_deduped_departures output."""
    return {
        "flight_number": flight_number,
        "aena_airport_iata": airport_iata,
        "flight_type": "departures",
        "scheduled_local": scheduled_local,
        "estimated_local": estimated_local,
        "airline_iata": airline_iata,
        "other_airport_iata": other_airport_iata,
    }


class TestBuildFeatureStoreIntegration:
    """Integration tests using fake connections and monkeypatched METAR/ICAO."""

    def test_dry_run_counts_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given: 2 departures, dry_run=True. When: build. Then: returns 0, no insert."""
        departures = [_make_departure_row(), _make_departure_row("VY5678", "BCN")]
        conn = FakeConn(departures)
        monkeypatch.setattr(bfs, "_get_conn", lambda: conn)
        monkeypatch.setattr(
            bfs, "_fetch_deduped_departures", lambda c: [_make_departure_row()],
        )

        # Patch checkpoint to return empty set
        monkeypatch.setattr(bfs, "get_checkpoint_set", lambda c: set())

        # Patch ICAO resolution
        monkeypatch.setattr(bfs, "_resolve_icao", lambda iata, c: "LEMD")

        # Patch METAR fetch
        monkeypatch.setattr(
            bfs, "_fetch_metar_for_airport",
            lambda c, icao, epoch: [
                {"obs_time": int(epoch) - 3600, "temp": 25.0, "dewp": 10.0, "relh": 40.0},
            ],
        )

        n = bfs.build_feature_store(dry_run=True)
        assert n == 0
        assert conn.insert_calls == []

    def test_checkpoint_blocks_rebuild(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given: checkpoint 'done' exists. When: build without --force. Then: skip."""
        monkeypatch.setattr(bfs, "_get_conn", lambda: FakeConn())
        monkeypatch.setattr(bfs, "get_checkpoint_set", lambda c: {"done"})

        n = bfs.build_feature_store()
        assert n == 0

    def test_inserts_correct_columns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given: one departure with known values. When: build. Then: row has 13 columns."""
        departure = _make_departure_row()
        monkeypatch.setattr(bfs, "_get_conn", lambda: FakeConn())
        monkeypatch.setattr(
            bfs, "_fetch_deduped_departures", lambda c: [departure],
        )
        monkeypatch.setattr(bfs, "get_checkpoint_set", lambda c: set())
        monkeypatch.setattr(bfs, "_resolve_icao", lambda iata, c: "LEMD")
        monkeypatch.setattr(
            bfs, "_fetch_metar_for_airport",
            lambda c, icao, epoch: [
                {"obs_time": int(epoch) - 600, "temp": 22.5, "dewp": 8.0, "relh": 35.0},
            ],
        )

        # Capture inserted rows
        captured_rows: list = []

        def fake_execute_values(cur, sql, argslist, **kwargs):
            captured_rows.extend(argslist)

        monkeypatch.setattr(bfs, "execute_values", fake_execute_values)

        n = bfs.build_feature_store()
        assert n == 1
        assert len(captured_rows) == 1
        row = captured_rows[0]
        assert row[0] == "MAD"  # aena_airport_iata
        assert row[1] == "IB1234"  # flight_number
        assert row[2] == "departures"  # flight_type
        assert row[3] == "2026-08-03T12:00:00"  # scheduled_local
        assert row[4] == 12  # hora_vuelo
        assert row[5] == 1  # dia_semana (Mon=1)
        assert row[6] == "IB"  # airline_iata
        assert row[7] == "BCN"  # other_airport_iata
        assert row[8] == 22.5  # temperatura_metar
        assert row[9] == 8.0  # punto_rocio_metar
        assert row[10] == 35.0  # relh
        assert row[11] == 35.0  # retraso_minutos (35 min)
        assert row[12] == "RETRASO"  # retraso_10_min

    def test_no_estimated_local_sets_none_targets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given: departure without estimated_local. When: build. Then: targets are None."""
        departure = _make_departure_row(estimated_local=None)
        monkeypatch.setattr(bfs, "_get_conn", lambda: FakeConn())
        monkeypatch.setattr(
            bfs, "_fetch_deduped_departures", lambda c: [departure],
        )
        monkeypatch.setattr(bfs, "get_checkpoint_set", lambda c: set())
        monkeypatch.setattr(bfs, "_resolve_icao", lambda iata, c: "LEMD")
        monkeypatch.setattr(bfs, "_fetch_metar_for_airport", lambda c, i, e: [])

        captured_rows: list = []
        monkeypatch.setattr(
            bfs, "execute_values",
            lambda cur, sql, al, **kw: captured_rows.extend(al),
        )

        n = bfs.build_feature_store()
        assert n == 1
        row = captured_rows[0]
        assert row[11] is None  # retraso_minutos
        assert row[12] is None  # retraso_10_min

    def test_no_metar_sets_null_weather(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given: no METAR for airport. When: build. Then: weather cols are None."""
        monkeypatch.setattr(bfs, "_get_conn", lambda: FakeConn())
        monkeypatch.setattr(
            bfs, "_fetch_deduped_departures",
            lambda c: [_make_departure_row()],
        )
        monkeypatch.setattr(bfs, "get_checkpoint_set", lambda c: set())
        monkeypatch.setattr(bfs, "_resolve_icao", lambda iata, c: "LEMD")
        monkeypatch.setattr(bfs, "_fetch_metar_for_airport", lambda c, i, e: [])

        captured_rows: list = []
        monkeypatch.setattr(
            bfs, "execute_values",
            lambda cur, sql, al, **kw: captured_rows.extend(al),
        )

        n = bfs.build_feature_store()
        assert n == 1
        row = captured_rows[0]
        assert row[8] is None  # temperatura_metar
        assert row[9] is None  # punto_rocio_metar
        assert row[10] is None  # relh

    def test_unresolvable_icao_skips_flight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given: airport not in ICAO mapping. When: build. Then: flight skipped."""
        monkeypatch.setattr(bfs, "_get_conn", lambda: FakeConn())
        monkeypatch.setattr(
            bfs, "_fetch_deduped_departures",
            lambda c: [_make_departure_row(airport_iata="ZZZ")],
        )
        monkeypatch.setattr(bfs, "get_checkpoint_set", lambda c: set())
        monkeypatch.setattr(bfs, "_resolve_icao", lambda iata, c: None)

        n = bfs.build_feature_store()
        assert n == 0

    def test_metar_leakage_prevention(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given: METAR obs AFTER cut time. When: build. Then: weather is None (no leak)."""
        monkeypatch.setattr(bfs, "_get_conn", lambda: FakeConn())
        monkeypatch.setattr(
            bfs, "_fetch_deduped_departures",
            lambda c: [_make_departure_row()],
        )
        monkeypatch.setattr(bfs, "get_checkpoint_set", lambda c: set())
        monkeypatch.setattr(bfs, "_resolve_icao", lambda iata, c: "LEMD")
        # METAR obs_time is AFTER the cut (future observation = leakage)
        monkeypatch.setattr(
            bfs, "_fetch_metar_for_airport",
            lambda c, i, e: [{"obs_time": int(e) + 3600, "temp": 99.0, "dewp": 0.0, "relh": 0.0}],
        )

        captured_rows: list = []
        monkeypatch.setattr(
            bfs, "execute_values",
            lambda cur, sql, al, **kw: captured_rows.extend(al),
        )

        n = bfs.build_feature_store()
        assert n == 1
        row = captured_rows[0]
        assert row[8] is None  # temperatura_metar — no leakage
        assert row[9] is None  # punto_rocio_metar
        assert row[10] is None  # relh
