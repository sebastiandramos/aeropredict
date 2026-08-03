"""Tests para el collector de festivos (Todo 3, data-source-collectors).

Patrón importlib + monkeypatch de ``tests/test_collect_weather.py``:
sin red, sin DB. El test de datos reales usa la librería ``holidays``
(offline) sobre el año 2026.
"""

import importlib.util
from pathlib import Path

import holidays

from aeropredict.sources.nager import NagerAdapter
from aeropredict.sources.python_holidays import PythonHolidaysAdapter

NAGER_SAMPLE = [
    {
        "date": "2026-01-01",
        "localName": "Año Nuevo",
        "name": "New Year's Day",
        "countryCode": "ES",
        "fixed": True,
        "global": True,
        "counties": None,
        "launchYear": None,
        "types": ["Public"],
    },
    {
        "date": "2026-12-25",
        "localName": "Navidad",
        "name": "Christmas Day",
        "countryCode": "ES",
        "fixed": True,
        "global": True,
        "counties": None,
        "launchYear": None,
        "types": ["Public"],
    },
]

PYTHON_SAMPLE = {
    "raw": {
        "AN": {
            "2026-01-01": "Año Nuevo",
            "2026-02-28": "Día de Andalucía",
        },
        "ES": {"2026-01-01": "Año Nuevo"},
    },
    "count": 3,
    "subdivisions": "all",
}


def _load_collect_holidays_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "collect_holidays.py"
    spec = importlib.util.spec_from_file_location("collect_holidays_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _patch_adapters(module, monkeypatch, nager_cls, python_cls):
    monkeypatch.setattr(module, "NagerAdapter", nager_cls)
    monkeypatch.setattr(module, "PythonHolidaysAdapter", python_cls)


class _OkNager:
    def get_holidays(self, country="ES", year=...):
        return {
            "raw": NAGER_SAMPLE,
            "count": len(NAGER_SAMPLE),
            "country": country,
            "year": year,
        }


class _OkPython:
    def get_holidays(self, country="ES", year=...):
        return PYTHON_SAMPLE


def test_nager_writes_csv_via_collect_holidays(monkeypatch):
    module = _load_collect_holidays_module()
    _patch_adapters(module, monkeypatch, _OkNager, _OkPython)
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: False)

    csv_calls = []

    def fake_write_raw_csv(
        source_name, endpoint, params, csv_text, delta_root, storage_options=None,
    ):
        csv_calls.append((source_name, endpoint, params, csv_text, delta_root))
        return 1

    monkeypatch.setattr(module, "write_raw_csv", fake_write_raw_csv)
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: 1)

    stats = module.collect_holidays(year=2026, days=1, dry_run=False, delta_root="data/raw")

    assert stats == {
        "total": 2, "nager_written": 1, "nager_skipped": 0,
        "python_written": 1, "errors": 0,
    }
    assert len(csv_calls) == 1
    source_name, endpoint, params, csv_text, _delta_root = csv_calls[0]
    assert source_name == "holidays_nager_date"
    assert endpoint == "https://date.nager.at/api/v4/Holidays/ES/2026"
    assert params == {"country": "ES", "year": 2026}
    assert csv_text.splitlines()[0] == "date,localName,name,countryCode,global,counties,types"
    assert "2026-01-01" in csv_text


def test_nager_dedup_skips_existing_row(monkeypatch):
    module = _load_collect_holidays_module()

    class NeverCalledNager:
        def get_holidays(self, country="ES", year=...):
            raise AssertionError("No debe llamarse si la fila ya existe")

    _patch_adapters(module, monkeypatch, NeverCalledNager, _OkPython)
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: True)

    csv_calls = []
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: csv_calls.append(a) or 1)
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: 1)

    stats = module.collect_holidays(year=2026, days=1, dry_run=False, delta_root="data/raw")

    assert stats["nager_skipped"] == 1
    assert stats["nager_written"] == 0
    assert csv_calls == []


def test_python_writes_snapshot_via_collect_holidays(monkeypatch):
    module = _load_collect_holidays_module()
    _patch_adapters(module, monkeypatch, _OkNager, _OkPython)
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: False)

    snapshot_calls = []

    def fake_write_raw_snapshot(
        source_name, endpoint, params, response_data, delta_root, storage_options=None,
    ):
        snapshot_calls.append((source_name, endpoint, params, response_data, delta_root))
        return 1

    monkeypatch.setattr(module, "write_raw_snapshot", fake_write_raw_snapshot)
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: 1)

    stats = module.collect_holidays(year=2026, days=1, dry_run=False, delta_root="data/raw")

    assert stats["python_written"] == 1
    assert stats["errors"] == 0
    assert len(snapshot_calls) == 1
    source_name, endpoint, params, response_data, _delta_root = snapshot_calls[0]
    assert source_name == "holidays_python"
    assert endpoint == "python_holidays://ES/2026"
    assert params == {"country": "ES", "year": 2026, "subdivisions": "all"}
    assert response_data["count"] == 3


def test_dry_run_writes_nothing(monkeypatch):
    module = _load_collect_holidays_module()

    class NeverCalledNager:
        def get_holidays(self, country="ES", year=...):
            raise AssertionError("Dry-run no debe llamar a la API")

    class NeverCalledPython:
        def get_holidays(self, country="ES", year=...):
            raise AssertionError("Dry-run no debe llamar al adaptador local")

    _patch_adapters(module, monkeypatch, NeverCalledNager, NeverCalledPython)

    written = []
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: written.append("csv") or 1)
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: written.append("snap") or 1)

    stats = module.collect_holidays(year=2026, days=1, dry_run=True, delta_root="data/raw")

    assert stats["total"] == 2
    assert stats["nager_written"] == 0
    assert stats["python_written"] == 0
    assert stats["errors"] == 0
    assert written == []


def test_main_dry_run_exit_0(monkeypatch):
    module = _load_collect_holidays_module()
    _patch_adapters(module, monkeypatch, _OkNager, _OkPython)

    assert module.main(["--dry-run", "--year", "2026"]) == 0


def test_main_all_failed_exit_1(monkeypatch):
    module = _load_collect_holidays_module()

    class FailingNager:
        def get_holidays(self, country="ES", year=...):
            return None

    class FailingPython:
        def get_holidays(self, country="ES", year=...):
            return None

    _patch_adapters(module, monkeypatch, FailingNager, FailingPython)
    monkeypatch.setattr(module, "table_row_exists", lambda *a, **k: False)

    assert module.main(["--year", "2026"]) == 1


def test_python_holidays_es_2026_count():
    """Datos reales de la librería ``holidays`` (offline): >= 32 festivos en 2026."""
    result = PythonHolidaysAdapter().get_holidays(country="ES", year=2026)

    assert result is not None
    assert result["subdivisions"] == "all"
    assert result["count"] >= 32


def test_python_holidays_iterates_all_subdivisions():
    result = PythonHolidaysAdapter().get_holidays(country="ES", year=2026)

    assert result is not None
    assert set(result["raw"]) == set(holidays.ES.subdivisions) | {"ES"}
    # Cada subdivisión incluye los festivos nacionales → calendario nunca vacío.
    assert all(rows for rows in result["raw"].values())


def test_python_holidays_unknown_country_returns_none():
    assert PythonHolidaysAdapter().get_holidays(country="FR", year=2026) is None


def test_nager_adapter_normalizes_response(monkeypatch):
    monkeypatch.setattr(
        "aeropredict.sources.base.http_get_with_retry", lambda *a, **k: NAGER_SAMPLE
    )

    result = NagerAdapter().get_holidays(country="ES", year=2026)

    assert result == {
        "raw": NAGER_SAMPLE,
        "count": 2,
        "country": "ES",
        "year": 2026,
    }


def test_nager_adapter_non_list_response_returns_none(monkeypatch):
    monkeypatch.setattr(
        "aeropredict.sources.base.http_get_with_retry", lambda *a, **k: {"unexpected": True}
    )

    assert NagerAdapter().get_holidays(country="ES", year=2026) is None
