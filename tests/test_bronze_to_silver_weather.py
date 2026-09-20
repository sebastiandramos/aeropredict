import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pytest


def _load_bronze_to_silver_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "bronze_to_silver.py"
    spec = importlib.util.spec_from_file_location("bronze_to_silver_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_weather_docs_from_bronze_payload():
    module = _load_bronze_to_silver_module()

    payload = {
        "airport_code": "LEAL",
        "start_date": "2026-06-01",
        "end_date": "2026-06-02",
        "hourly": {
            "time": ["2026-06-01T00:00:00"],
            "temperature_2m": [20.5],
            "precipitation": [0.0],
            "wind_speed_10m": [5.0],
            "wind_gusts_10m": [7.0],
            "visibility": [10000.0],
            "cloud_cover": [10.0],
            "relative_humidity_2m": [60.0],
        },
    }

    docs = module._build_weather_docs(payload)

    assert len(docs) == 1
    assert docs[0]["airport_code"] == "LEAL"
    assert docs[0]["flight_date"] == "2026-06-01"
    assert docs[0]["temperature_2m"] == 20.5
    assert docs[0]["precipitation"] == 0.0


def test_read_weather_pushes_down_day_range_filter(monkeypatch: pytest.MonkeyPatch):
    """_read_bronze_weather filters the Delta read to the target calendar day."""
    module = _load_bronze_to_silver_module()

    captured: list = []

    class _MockDeltaTable:
        def __init__(self, table_uri: str, storage_options=None) -> None:
            self._empty = pa.table(
                {
                    "fetched_at": pa.array([], type=pa.timestamp("us", tz="UTC")),
                    "response": pa.array([], type=pa.string()),
                }
            )

        def to_pyarrow_table(self, filters=None) -> pa.Table:
            captured.append(filters)
            return self._empty

    monkeypatch.setattr("deltalake.DeltaTable", _MockDeltaTable)

    docs = module._read_bronze_weather("/tmp/fake", target_date=date(2026, 9, 18))

    assert docs == []
    # Day range is [target_date 00:00, next day 00:00)
    assert captured == [
        [
            ("fetched_at", ">=", datetime(2026, 9, 18, 0, 0, 0, tzinfo=UTC)),
            ("fetched_at", "<", datetime(2026, 9, 19, 0, 0, 0, tzinfo=UTC)),
        ]
    ]
