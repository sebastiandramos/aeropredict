"""Tests para los helpers aditivos de storage (Todo 1, data-source-collectors).

Cubre ``write_raw_csv``, ``write_raw_snapshot`` y ``table_row_exists``.
Sin red, sin DB real; deltalake local sobre ``tmp_path`` permitido.
"""

from deltalake import DeltaTable

from aeropredict.opensky import storage


def _read_table(tmp_path, source_name):
    table_uri = str(tmp_path / "bronze" / source_name)
    return DeltaTable(table_uri).to_pyarrow_table()


def _no_cloud(monkeypatch):
    monkeypatch.setattr(storage, "_get_cloud_root", lambda: None)


def test_write_raw_csv_preserves_exact_csv_text(tmp_path, monkeypatch):
    _no_cloud(monkeypatch)
    csv_text = "\ufefficao,flight_date\r\nLEMD,2026-06-01\r\nLEBL,2026-06-02\r\n"

    n = storage.write_raw_csv(
        "test_csv_source",
        "https://example.test/data.csv",
        {"year": 2026},
        csv_text,
        str(tmp_path),
    )

    assert n == 1
    table = _read_table(tmp_path, "test_csv_source")
    assert table.num_rows == 1
    assert table.column("response")[0].as_py() == csv_text
    assert table.column("source")[0].as_py() == "test_csv_source"
    assert table.column("endpoint")[0].as_py() == "https://example.test/data.csv"
    assert table.column("params")[0].as_py() == '{"year": 2026}'


def test_write_raw_snapshot_overwrite_replaces_previous(tmp_path, monkeypatch):
    _no_cloud(monkeypatch)
    first = {"status": "ok", "features": ["a"]}
    second = {"status": "ok", "features": ["b", "c"]}

    n1 = storage.write_raw_snapshot(
        "test_snapshot_source",
        "https://example.test/snap.json",
        {"layer": 0},
        first,
        str(tmp_path),
    )
    n2 = storage.write_raw_snapshot(
        "test_snapshot_source",
        "https://example.test/snap.json",
        {"layer": 0},
        second,
        str(tmp_path),
    )

    assert n1 == 1
    assert n2 == 1
    table = _read_table(tmp_path, "test_snapshot_source")
    assert table.num_rows == 1
    assert table.column("response")[0].as_py() == '{"status": "ok", "features": ["b", "c"]}'


def test_write_raw_snapshot_skip_empty_keeps_previous(tmp_path, monkeypatch):
    _no_cloud(monkeypatch)
    good = {"features": [{"id": 1}]}
    storage.write_raw_snapshot(
        "test_snapshot_source", "https://example.test/snap.json", {"layer": 1},
        good, str(tmp_path),
    )

    for empty in (None, {}, [], ""):
        n = storage.write_raw_snapshot(
            "test_snapshot_source", "https://example.test/snap.json", {"layer": 1},
            empty, str(tmp_path),
        )
        assert n == 0

    table = _read_table(tmp_path, "test_snapshot_source")
    assert table.num_rows == 1
    assert table.column("response")[0].as_py() == '{"features": [{"id": 1}]}'


def test_table_row_exists_matches_endpoint_and_params(tmp_path, monkeypatch):
    _no_cloud(monkeypatch)
    storage.write_raw_csv(
        "test_dedup_source",
        "https://example.test/d.csv",
        {"a": 1, "b": "x"},
        "h1,h2\r\n1,2\r\n",
        str(tmp_path),
    )

    assert storage.table_row_exists(
        str(tmp_path), "test_dedup_source", "https://example.test/d.csv", {"a": 1, "b": "x"}
    ) is True
    assert storage.table_row_exists(
        str(tmp_path), "test_dedup_source", "https://example.test/d.csv", {"a": 1}
    ) is False
    assert storage.table_row_exists(
        str(tmp_path), "test_dedup_source", "https://example.test/other.csv", {"a": 1, "b": "x"}
    ) is False
    assert storage.table_row_exists(
        str(tmp_path), "missing_source", "https://example.test/d.csv", {"a": 1, "b": "x"}
    ) is False
