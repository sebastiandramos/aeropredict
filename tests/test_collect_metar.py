"""Tests del collector METAR de la NOAA AWC (todo 4, data-source-collectors).

Patrón importlib + monkeypatch de ``tests/test_collect_weather.py``:
sin red, sin DB, sin Delta real. La capa HTTP se mockea en
``aeropredict.sources.metar.http_get_with_retry``.
"""

import importlib.util
from pathlib import Path

import requests

from aeropredict.sources import metar


def _load_collect_metar_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "collect_metar.py"
    spec = importlib.util.spec_from_file_location("collect_metar_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _two_station_fixture():
    """Respuesta JSON array real de la AWC (verificada en vivo 2026-08-03)."""
    return [
        {
            "icaoId": "LEBL",
            "receiptTime": "2026-08-03T06:34:24.469Z",
            "obsTime": 1785738600,
            "temp": 29,
            "dewp": 26,
            "wdir": 70,
            "wspd": 10,
            "visib": "6+",
            "altim": 1011,
            "rawOb": "METAR LEBL 030630Z 07010KT 9999 SCT016 29/26 Q1011 NOSIG",
            "clouds": [{"cover": "SCT", "base": 1600}],
            "fltCat": "VFR",
        },
        {
            "icaoId": "LEMD",
            "receiptTime": "2026-08-03T06:34:24.728Z",
            "obsTime": 1785738600,
            "temp": 24,
            "dewp": 10,
            "wdir": 180,
            "wspd": 6,
            "visib": "6+",
            "altim": 1013,
            "rawOb": "METAR LEMD 030630Z 18006KT CAVOK 24/10 Q1013 NOSIG",
            "clouds": [],
            "fltCat": "VFR",
        },
    ]


def test_metar_adapter_normalizes_reports(monkeypatch):
    calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        calls.append(params)
        return _two_station_fixture()

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)

    result = metar.MetarAWCAdapter().get_metars(["LEMD", "LEBL"], hours=2)

    assert result is not None
    assert result["count"] == 2
    assert result["airport_codes"] == ["LEMD", "LEBL"]
    assert len(calls) == 1
    assert calls[0]["ids"] == "LEMD,LEBL"
    assert calls[0]["format"] == "json"
    assert calls[0]["taf"] == "false"
    assert calls[0]["hours"] == 2

    rows = result["raw"]
    assert rows[0]["icaoId"] == "LEBL"
    assert rows[0]["fltCat"] == "VFR"
    assert rows[0]["visib"] == "6+"
    assert rows[0]["clouds_base"] == 1600
    assert rows[0]["altim"] == 1011
    assert rows[1]["clouds_base"] is None
    assert all(set(row) == set(metar.CSV_FIELDS) for row in rows)


def test_metar_adapter_batches_over_54_codes(monkeypatch):
    calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        calls.append(params)
        return [{"icaoId": params["ids"].split(",")[0], "rawOb": "METAR X"}]

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)

    codes = [f"LE{i:02d}" for i in range(62)]
    result = metar.MetarAWCAdapter().get_metars(codes, hours=48)

    assert result is not None
    assert len(calls) == 2
    batches = [params["ids"].split(",") for params in calls]
    assert all(len(batch) <= metar.MAX_ICAOS_PER_REQUEST for batch in batches)
    assert len(batches[0]) + len(batches[1]) == 62
    assert result["count"] == 2


def test_metar_adapter_handles_empty_204(monkeypatch):
    # HTTP 204 No Content -> http_get_with_retry devuelve {} (no list)
    monkeypatch.setattr(metar, "http_get_with_retry", lambda *a, **k: {})

    result = metar.MetarAWCAdapter().get_metars(["LEMD"], hours=2)

    assert result is not None
    assert result["count"] == 0
    assert result["raw"] == []


def test_metar_adapter_returns_none_on_api_failure(monkeypatch):
    def fake_http_get(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)

    assert metar.MetarAWCAdapter().get_metars(["LEMD"], hours=2) is None


def test_collect_metar_writes_bronze(monkeypatch):
    module = _load_collect_metar_module()

    monkeypatch.setattr(
        metar, "http_get_with_retry",
        lambda url, headers=None, params=None, timeout=30: _two_station_fixture(),
    )
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: False)

    raw_calls = []

    def fake_write_raw_csv(*args, **kwargs):
        raw_calls.append((args, kwargs))
        return 1

    monkeypatch.setattr(module, "write_raw_csv", fake_write_raw_csv)

    stats = module.collect_metar(
        airport="LEMD", days_back=2,
        dry_run=False, delta_root="data/raw",
    )

    assert stats["total"] == 1
    assert stats["metar_written"] == 1
    assert stats["errors"] == 0
    assert len(raw_calls) == 1
    (source, endpoint, params, csv_text, delta_root), _ = raw_calls[0]
    assert source == "metar_awc"
    assert endpoint == module.METAR_URL
    assert params["ids"] == "LEMD"
    assert params["hours"] == 48
    assert "ingestion_ts" in params
    assert delta_root == "data/raw"
    lines = csv_text.strip().splitlines()
    assert lines[0] == (
        "icaoId,rawOb,receiptTime,obsTime,temp,dewp,wdir,wspd,wgst,visib,"
        "altim,fltCat,clouds_base"
    )
    assert len(lines) == 3  # header + 2 informes


def test_collect_metar_skips_empty_response(monkeypatch):
    module = _load_collect_metar_module()

    monkeypatch.setattr(metar, "http_get_with_retry", lambda *a, **k: {})
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: False)

    raw_calls = []

    def fake_write_raw_csv(*args, **kwargs):
        raw_calls.append(args)
        return 1

    monkeypatch.setattr(module, "write_raw_csv", fake_write_raw_csv)

    stats = module.collect_metar(airport="LEMD", delta_root="data/raw")

    assert stats["metar_written"] == 0
    assert stats["skipped"] == 1
    assert raw_calls == []


def test_collect_metar_dedupes_existing_row(monkeypatch):
    module = _load_collect_metar_module()

    http_calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        http_calls.append(params)
        return []

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: True)
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: 1)

    stats = module.collect_metar(airport="LEMD", delta_root="data/raw")

    assert stats["skipped"] == 1
    assert stats["metar_written"] == 0
    assert http_calls == []


def test_collect_metar_dry_run_does_not_fetch(monkeypatch):
    module = _load_collect_metar_module()

    http_calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        http_calls.append(params)
        return []

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)

    stats = module.collect_metar(airport="LEMD", dry_run=True, delta_root="data/raw")

    assert stats["total"] == 1
    assert stats["metar_written"] == 0
    assert http_calls == []


def test_main_exit_codes(monkeypatch):
    module = _load_collect_metar_module()
    monkeypatch.setattr(module, "get_delta_root", lambda: "data/raw")

    # exit 0: dry-run sin errores
    monkeypatch.setattr(
        module, "collect_metar",
        lambda **kwargs: {"total": 1, "metar_written": 0, "skipped": 1, "errors": 0},
    )
    assert module.main(["--airport", "LEMD", "--dry-run"]) == 0

    # exit 1: todos los lotes fallaron
    monkeypatch.setattr(
        module, "collect_metar",
        lambda **kwargs: {"total": 2, "metar_written": 0, "skipped": 0, "errors": 2},
    )
    assert module.main(["--airport", "LEMD"]) == 1

    # exit 0: fallo parcial (el run continúa)
    monkeypatch.setattr(
        module, "collect_metar",
        lambda **kwargs: {"total": 2, "metar_written": 1, "skipped": 0, "errors": 1},
    )
    assert module.main(["--airport", "LEMD"]) == 0


def test_metar_adapter_partial_batch_failure_returns_partial(monkeypatch):
    calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        calls.append(params)
        if len(calls) == 1:
            return [{"icaoId": "LE01", "rawOb": "METAR LE01"}]
        raise requests.RequestException("boom on second batch")

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)

    codes = [f"LE{i:02d}" for i in range(62)]
    result = metar.MetarAWCAdapter().get_metars(codes, hours=48)

    assert result is not None
    assert result["count"] == 1
    assert result["errors"] == 1
    assert len(calls) == 2


def test_metar_adapter_all_batches_fail_returns_none(monkeypatch):
    def fake_http_get(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)

    codes = [f"LE{i:02d}" for i in range(62)]
    assert metar.MetarAWCAdapter().get_metars(codes, hours=48) is None


def test_metar_adapter_unexpected_exception_is_caught_per_batch(monkeypatch):
    calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        calls.append(params)
        if len(calls) == 1:
            return [{"icaoId": "LE01", "rawOb": "METAR LE01"}]
        # schema drift / programming error — not a requests error
        raise ValueError("unexpected schema drift")

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)

    codes = [f"LE{i:02d}" for i in range(62)]
    result = metar.MetarAWCAdapter().get_metars(codes, hours=48)

    assert result is not None
    assert result["count"] == 1
    assert result["errors"] == 1


def test_collect_metar_two_runs_both_write(monkeypatch):
    module = _load_collect_metar_module()

    monkeypatch.setattr(
        metar, "http_get_with_retry",
        lambda url, headers=None, params=None, timeout=30: _two_station_fixture(),
    )
    # distinct ingestion keys per run → dedup no-op, both runs write fresh
    keys = iter(["2026-08-03T06:30:00+00:00", "2026-08-03T19:30:00+00:00"])
    monkeypatch.setattr(module, "_ingestion_key", lambda: next(keys))
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: False)

    raw_calls = []

    def fake_write_raw_csv(*args, **kwargs):
        raw_calls.append(args)
        return 1

    monkeypatch.setattr(module, "write_raw_csv", fake_write_raw_csv)

    stats1 = module.collect_metar(airport="LEMD", delta_root="data/raw")
    stats2 = module.collect_metar(airport="LEMD", delta_root="data/raw")

    assert stats1["metar_written"] == 1
    assert stats2["metar_written"] == 1
    assert len(raw_calls) == 2
    # distinct ingestion keys → distinct dedup keys per run
    assert raw_calls[0][2]["ingestion_ts"] != raw_calls[1][2]["ingestion_ts"]


def test_collect_metar_all_batches_fail_returns_errors(monkeypatch):
    module = _load_collect_metar_module()

    def fake_http_get(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(metar, "http_get_with_retry", fake_http_get)
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: False)
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: 1)

    stats = module.collect_metar(airport="LEMD", delta_root="data/raw")

    assert stats["errors"] == stats["total"]
    assert stats["metar_written"] == 0
