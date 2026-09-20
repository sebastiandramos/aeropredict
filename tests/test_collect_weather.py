import importlib.util
from pathlib import Path


def _load_collect_weather_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "collect_weather.py"
    spec = importlib.util.spec_from_file_location("collect_weather_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DummyAdapter:
    """Réplica del contrato real de OpenMeteoAdapter.get_weather_batch:

    Devuelve el dict enriquecido con ``airport_code`` (que es lo que consume
    ``bronze_to_silver._build_weather_docs``) y el payload crudo en ``raw``.
    """

    def get_weather_batch(self, icao, start_date, end_date):
        return {
            "airport_code": icao,
            "latitude": 40.0,
            "longitude": -3.0,
            "start_date": start_date,
            "end_date": end_date,
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


def test_collect_weather_only_writes_bronze(monkeypatch):
    module = _load_collect_weather_module()

    monkeypatch.setattr(
        module, "_get_airport_date_ranges",
        lambda airport=None: [
            ("LEAL", "2026-06-01", "2026-06-02")
        ],
    )
    monkeypatch.setattr(module, "_has_weather", lambda airport, date: False)

    monkeypatch.setattr(module, "OpenMeteoAdapter", DummyAdapter)

    raw_calls = []

    def fake_write_raw_json(*args, **kwargs):
        raw_calls.append((args, kwargs))
        return 1

    monkeypatch.setattr(module, "write_raw_json", fake_write_raw_json)

    stats = module.collect_weather(
        airport="LEAL", days_back=2,
        dry_run=False, delta_root="data/raw",
    )

    assert stats["weather_written"] == 1
    assert raw_calls


def test_collect_weather_persists_airport_code_in_bronze_payload(monkeypatch):
    """El payload guardado en Bronze debe conservar ``airport_code``.

    Regression: se escribía ``data.get("raw", data)`` (payload crudo de
    Open-Meteo sin ``airport_code``), por lo que
    ``bronze_to_silver._build_weather_docs`` devolvía siempre [] y la
    colección Silver ``weather`` nunca se poblaba (observado en Neon:
    MONGO_weather: 0).
    """
    module = _load_collect_weather_module()

    monkeypatch.setattr(
        module, "_get_airport_date_ranges",
        lambda airport=None: [
            ("LEAL", "2026-06-01", "2026-06-02")
        ],
    )
    monkeypatch.setattr(module, "_has_weather", lambda airport, date: False)
    monkeypatch.setattr(module, "OpenMeteoAdapter", DummyAdapter)

    written = []

    def fake_write_raw_json(*args, **kwargs):
        written.append(args)
        return 1

    monkeypatch.setattr(module, "write_raw_json", fake_write_raw_json)

    module.collect_weather(
        airport="LEAL", days_back=2,
        dry_run=False, delta_root="data/raw",
    )

    assert written, "write_raw_json debe haberse llamado"
    # write_raw_json(source_name, path, params, data, delta_root)
    stored_payload = written[0][3]
    assert stored_payload.get("airport_code") == "LEAL", (
        "el payload Bronze debe conservar airport_code para que "
        "bronze_to_silver._build_weather_docs genere documentos"
    )
