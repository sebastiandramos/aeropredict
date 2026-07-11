import importlib.util
from pathlib import Path


def _load_collect_weather_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "collect_weather.py"
    spec = importlib.util.spec_from_file_location("collect_weather_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collect_weather_only_writes_bronze(monkeypatch):
    module = _load_collect_weather_module()

    monkeypatch.setattr(module, "_get_airport_date_ranges", lambda airport=None: [("LEAL", "2026-06-01", "2026-06-02")])
    monkeypatch.setattr(module, "_has_weather", lambda airport, date: False)

    class DummyAdapter:
        def get_weather_batch(self, icao, start_date, end_date):
            return {
                "latitude": 40.0,
                "longitude": -3.0,
                "hourly": {
                    "time": ["2026-06-01T00:00:00"],
                    "temperature_2m": [20.0],
                    "precipitation": [0.0],
                    "wind_speed_10m": [5.0],
                    "wind_gusts_10m": [7.0],
                    "visibility": [10000.0],
                    "cloud_cover": [20.0],
                    "relative_humidity_2m": [60.0],
                },
                "raw": {"ok": True},
            }

    monkeypatch.setattr(module, "OpenMeteoAdapter", DummyAdapter)

    raw_calls = []

    def fake_write_raw_json(*args, **kwargs):
        raw_calls.append((args, kwargs))
        return 1

    monkeypatch.setattr(module, "write_raw_json", fake_write_raw_json)

    stats = module.collect_weather(airport="LEAL", days_back=2, dry_run=False, delta_root="data/raw")

    assert stats["weather_written"] == 1
    assert raw_calls
