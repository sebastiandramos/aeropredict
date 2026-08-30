"""Tests de los writers Gold para las fuentes nuevas (Task 11).

Cubre ``write_metar_gold``, ``write_holidays_gold``,
``write_eurocontrol_pru_gold``, ``write_notam_gold``, ``write_airports_gold``
y ``write_runways_gold``, más el soporte ``fields=None`` de
``_sync_entity``. Todo con mocks puros: sin PostgreSQL, sin red.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from aeropredict.opensky import storage_gold

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import silver_to_gold_entities as entities


class FakeCursor:
    """Cursor no-op que registra en la conexión fake."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    """Conexión fake: registra execute_values y commit no-op."""

    def __init__(self):
        self.calls: list[tuple] = []  # (sql, rows, template, page_size)
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def _record_execute_values(cur, sql, argslist, template=None, page_size=None, **kwargs):
    cur.conn.calls.append((sql, argslist, template, page_size))


def _patch_conn(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(storage_gold, "_get_conn", lambda: conn)
    monkeypatch.setattr(storage_gold, "execute_values", _record_execute_values)
    return conn


# ------------------------------------------------------------------
# Documentos de ejemplo (contrato Mongo, Sección 1)
# ------------------------------------------------------------------


def _metar_doc(icao_id, obs_time, **overrides):
    doc = {
        "icao_id": icao_id,
        "raw_ob": "LEMD 010800Z 24012KT 9999 FEW040 25/08 Q1020",
        "receipt_time": "2026-08-03T08:00:00Z",
        "obs_time": obs_time,
        "temp": 25.0,
        "dewp": 8.0,
        "wdir": 240,
        "wspd": 12,
        "wgst": 20,
        "visib": "9999",
        "altim": 1020.0,
        "flt_cat": "VFR",
        "clouds_base": 4000,
    }
    doc.update(overrides)
    return doc


def _holiday_doc(date, name, **overrides):
    doc = {
        "date": date,
        "name": name,
        "local_name": name,
        "country_code": "ES",
        "is_global": True,
        "counties": ["28", "29"],
        "types": ["Public"],
        "source": "nager_date",
        "subdivision": "",
    }
    doc.update(overrides)
    return doc


def _notam_doc(layer, **overrides):
    doc = {
        "feature": {
            "type": "Feature",
            "properties": {"id": f"ES-2026-{layer:04d}"},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]},
        },
        "layer": layer,
        "snapshot_at": "2026-08-03T10:00:00Z",
    }
    doc.update(overrides)
    return doc


def _airport_doc(ident, **overrides):
    doc = {
        "ident": ident,
        "type": "large_airport",
        "name": f"Airport {ident}",
        "latitude_deg": "40.4719",
        "longitude_deg": "-3.5626",
        "elevation_ft": "1998",
        "iso_country": "ES",
        "iso_region": "ES-MD",
        "municipality": "Madrid",
        "iata_code": "MAD",
        "icao_code": ident,
    }
    doc.update(overrides)
    return doc


def _runway_doc(airport_ident, le_ident, he_ident, **overrides):
    doc = {
        "airport_ident": airport_ident,
        "length_ft": "13450",
        "width_ft": "197",
        "surface": "asphalt",
        "le_ident": le_ident,
        "he_ident": he_ident,
        "le_heading_degT": "143",
        "he_heading_degT": "323",
    }
    doc.update(overrides)
    return doc


# ------------------------------------------------------------------
# write_metar_gold
# ------------------------------------------------------------------


def test_write_metar_gold_inserts_rows(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_metar_doc("LEMD", 1722657600), _metar_doc("LEBL", 1722657600)]

    n = storage_gold.write_metar_gold(docs)

    assert n == 2
    assert conn.commits == 1
    sql, rows, template, page_size = conn.calls[0]
    assert "INSERT INTO gold.metar" in sql
    assert "ON CONFLICT (icao_id, obs_time) DO NOTHING" in sql
    assert page_size == 500
    assert template == (
        "(%s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    row = rows[0]
    assert row[0] == "LEMD"
    assert isinstance(row[1], str) and row[1].startswith("LEMD")
    assert isinstance(row[2], datetime)  # receipt_time parseada
    assert row[3] == 1722657600
    assert row[4] == 25.0
    assert row[9] == "9999"
    assert row[11] == "VFR"
    assert row[12] == 4000


def test_write_metar_gold_defensive_on_bad_values(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [
        _metar_doc("LEMD", 1, receipt_time="no-iso", temp="XX", wdir="abc", visib=9999),
    ]

    n = storage_gold.write_metar_gold(docs)

    assert n == 1
    _, rows, _, _ = conn.calls[0]
    assert rows[0][2] is None  # receipt_time no parseable
    assert rows[0][4] is None  # temp no numérico
    assert rows[0][6] is None  # wdir no numérico
    assert rows[0][9] == "9999"  # visib numérico str → truncado


def test_write_metar_gold_skips_missing_icao_or_obs_time(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [
        _metar_doc("LEMD", 1),
        _metar_doc(None, 2),
        _metar_doc("LEBL", None),
        _metar_doc("LEAL", "bogus"),
    ]

    n = storage_gold.write_metar_gold(docs)

    assert n == 1
    _, rows, _, _ = conn.calls[0]
    assert len(rows) == 1
    assert rows[0][0] == "LEMD"


# ------------------------------------------------------------------
# write_holidays_gold
# ------------------------------------------------------------------


def test_write_holidays_gold_inserts_rows(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_holiday_doc("2026-01-01", "Año Nuevo"), _holiday_doc("2026-12-25", "Navidad")]

    n = storage_gold.write_holidays_gold(docs)

    assert n == 2
    assert conn.commits == 1
    sql, rows, template, page_size = conn.calls[0]
    assert "INSERT INTO gold.holidays" in sql
    assert "ON CONFLICT (date, name, country_code, source, subdivision) DO NOTHING" in sql
    assert page_size == 500
    assert "%s::date" in template
    assert "%s::text[]" in template
    row = rows[0]
    assert row[0] == "2026-01-01"
    assert row[1] == "Año Nuevo"
    assert row[3] == "ES"
    assert row[4] is True
    assert row[5] == ["28", "29"]
    assert row[6] == ["Public"]
    assert row[7] == "nager_date"
    assert row[8] == ""


def test_write_holidays_gold_defaults_empty_arrays(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_holiday_doc("2026-01-01", "Año Nuevo", counties=None, types=None)]

    storage_gold.write_holidays_gold(docs)

    _, rows, _, _ = conn.calls[0]
    assert rows[0][5] == []
    assert rows[0][6] == []


def test_write_holidays_gold_skips_missing_required_fields(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [
        _holiday_doc(None, "Sin fecha"),
        _holiday_doc("2026-01-01", None),
        _holiday_doc("2026-01-01", "OK"),
    ]

    n = storage_gold.write_holidays_gold(docs)

    assert n == 1
    _, rows, _, _ = conn.calls[0]
    assert len(rows) == 1
    assert rows[0][1] == "OK"


# ------------------------------------------------------------------
# write_eurocontrol_pru_gold
# ------------------------------------------------------------------


def test_write_eurocontrol_pru_gold_inserts_rows(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [
        {
            "_id": "abc",
            "source_file": "PRU_ALL_2025_2025_01.csv",
            "year": 2025,
            "Flight": "IB1234",
            "Dep": "LEMD",
            "Arr": "LEBL",
            "ingested_at": "2026-01-01T00:00:00Z",
        },
    ]

    n = storage_gold.write_eurocontrol_pru_gold(docs)

    assert n == 1
    assert conn.commits == 1
    sql, rows, template, page_size = conn.calls[0]
    assert "INSERT INTO gold.eurocontrol_pru" in sql
    assert "ON CONFLICT (source_file, year, row_json) DO NOTHING" in sql
    assert "%s::jsonb" in template
    assert page_size == 500
    source_file, year, row_json = rows[0]
    assert source_file == "PRU_ALL_2025_2025_01.csv"
    assert year == 2025
    parsed = json.loads(row_json)
    assert set(parsed) == {"Flight", "Dep", "Arr"}
    assert parsed == {"Arr": "LEBL", "Dep": "LEMD", "Flight": "IB1234"}


def test_write_eurocontrol_pru_gold_skips_missing_keys(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [
        {"year": 2025, "col": "x"},
        {"source_file": "f.csv", "col": "x"},
        {"source_file": "f.csv", "year": 2025, "col": "x"},
    ]

    n = storage_gold.write_eurocontrol_pru_gold(docs)

    assert n == 1
    _, rows, _, _ = conn.calls[0]
    assert len(rows) == 1
    assert rows[0][1] == 2025


# ------------------------------------------------------------------
# write_notam_gold
# ------------------------------------------------------------------


def test_write_notam_gold_inserts_rows(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_notam_doc(0), _notam_doc(1)]

    n = storage_gold.write_notam_gold(docs)

    assert n == 2
    assert conn.commits == 1
    sql, rows, template, page_size = conn.calls[0]
    assert "INSERT INTO gold.notam" in sql
    assert "ON CONFLICT (layer, feature_json) DO NOTHING" in sql
    assert "%s::jsonb" in template
    assert "%s::timestamptz" in template
    assert page_size == 500
    layer, snapshot_at, feature_json = rows[0]
    assert layer == 0
    assert isinstance(snapshot_at, datetime)
    assert json.loads(feature_json)["properties"]["id"] == "ES-2026-0000"


def test_write_notam_gold_unparseable_snapshot_is_none(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_notam_doc(0, snapshot_at="bogus")]

    storage_gold.write_notam_gold(docs)

    _, rows, _, _ = conn.calls[0]
    assert rows[0][1] is None


def test_write_notam_gold_skips_missing_feature_or_layer(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [
        {"layer": 0},
        {"feature": {"type": "Feature"}},
        _notam_doc(0),
    ]

    n = storage_gold.write_notam_gold(docs)

    assert n == 1
    _, rows, _, _ = conn.calls[0]
    assert len(rows) == 1


# ------------------------------------------------------------------
# write_airports_gold
# ------------------------------------------------------------------


def test_write_airports_gold_upserts(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_airport_doc("LEMD"), _airport_doc("LEAL")]

    n = storage_gold.write_airports_gold(docs)

    assert n == 2
    assert conn.commits == 1
    sql, rows, _template, page_size = conn.calls[0]
    assert "INSERT INTO gold.airports" in sql
    assert "ON CONFLICT (ident) DO UPDATE SET" in sql
    assert "ingested_at    = NOW()" in sql
    assert page_size == 500
    row = rows[0]
    assert row[0] == "LEMD"
    assert row[1] == "large_airport"
    assert row[2] == "Airport LEMD"
    assert row[3] == 40.4719  # str → float
    assert row[4] == -3.5626
    assert row[5] == 1998.0
    assert row[6] == "ES"
    assert row[7] == "ES-MD"
    assert row[9] == "MAD"


def test_write_airports_gold_defensive_on_bad_float(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_airport_doc("LEMD", latitude_deg="n/a", elevation_ft=None)]

    storage_gold.write_airports_gold(docs)

    _, rows, _, _ = conn.calls[0]
    assert rows[0][3] is None
    assert rows[0][5] is None


def test_write_airports_gold_skips_missing_ident(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_airport_doc("LEMD"), {"type": "heliport"}]

    n = storage_gold.write_airports_gold(docs)

    assert n == 1
    _, rows, _, _ = conn.calls[0]
    assert len(rows) == 1
    assert rows[0][0] == "LEMD"


# ------------------------------------------------------------------
# write_runways_gold
# ------------------------------------------------------------------


def test_write_runways_gold_upserts(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [_runway_doc("LEMD", "14R", "32L"), _runway_doc("LEMD", "18L", "36R")]

    n = storage_gold.write_runways_gold(docs)

    assert n == 2
    assert conn.commits == 1
    sql, rows, _template, page_size = conn.calls[0]
    assert "INSERT INTO gold.runways" in sql
    assert "ON CONFLICT (airport_ident, le_ident, he_ident) DO UPDATE SET" in sql
    assert "length_ft      = EXCLUDED.length_ft" in sql
    assert page_size == 500
    row = rows[0]
    assert row[0] == "LEMD"
    assert row[1] == 13450.0  # str → float
    assert row[2] == 197.0
    assert row[3] == "asphalt"
    assert row[4] == "14R"
    assert row[5] == "32L"
    assert row[6] == 143.0
    assert row[7] == 323.0


def test_write_runways_gold_skips_missing_key_columns(monkeypatch):
    conn = _patch_conn(monkeypatch)
    docs = [
        _runway_doc("LEMD", "14R", "32L"),
        {"airport_ident": "LEAL", "le_ident": "10"},
        {"airport_ident": "LEAL", "he_ident": "28"},
        {"le_ident": "10", "he_ident": "28"},
    ]

    n = storage_gold.write_runways_gold(docs)

    assert n == 1
    _, rows, _, _ = conn.calls[0]
    assert len(rows) == 1
    assert rows[0][0] == "LEMD"


# ------------------------------------------------------------------
# Guardas de lista vacía (todos los writers)
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "writer",
    [
        storage_gold.write_metar_gold,
        storage_gold.write_holidays_gold,
        storage_gold.write_eurocontrol_pru_gold,
        storage_gold.write_notam_gold,
        storage_gold.write_airports_gold,
        storage_gold.write_runways_gold,
    ],
)
def test_writers_empty_list_return_zero_without_conn(monkeypatch, writer):
    def fail_if_called(*a, **k):
        raise AssertionError("con lista vacía no debe conectar a PostgreSQL")

    monkeypatch.setattr(storage_gold, "_get_conn", fail_if_called)

    assert writer([]) == 0


# ------------------------------------------------------------------
# _sync_entity con fields=None (esquema dinámico)
# ------------------------------------------------------------------


class FakeFindCollection:
    """Colección fake que registra las llamadas a find."""

    def __init__(self, docs):
        self.docs = docs
        self.find_calls = []

    def find(self, filter_, fields=None):
        self.find_calls.append((filter_, fields))
        return self.docs


class FakeWrite:
    """write_fn fake que registra los docs recibidos."""

    def __init__(self):
        self.calls = []

    def __call__(self, docs):
        self.calls.append(docs)
        return len(docs)


def test_sync_entity_fields_none_reads_all_fields(monkeypatch):
    collection = FakeFindCollection([{"source_file": "f.csv", "year": 2025, "col": "x"}])
    mdb = {"eurocontrol_pru": collection}
    write_fn = FakeWrite()
    monkeypatch.setattr(entities, "add_to_checkpoint_set", lambda *a, **k: None)

    n = entities._sync_entity(mdb, "eurocontrol_pru", None, write_fn, "eurocontrol_pru")

    assert n == 1
    assert collection.find_calls == [({}, None)]
    assert write_fn.calls == [[{"source_file": "f.csv", "year": 2025, "col": "x"}]]


def test_sync_entity_fields_projection_passed_to_find(monkeypatch):
    fields = {"source_file": 1, "_id": 0}
    collection = FakeFindCollection([{"source_file": "f.csv"}])
    mdb = {"eurocontrol_pru": collection}
    write_fn = FakeWrite()
    monkeypatch.setattr(entities, "add_to_checkpoint_set", lambda *a, **k: None)

    entities._sync_entity(mdb, "eurocontrol_pru", fields, write_fn, "eurocontrol_pru")

    assert collection.find_calls == [({}, fields)]


def test_sync_entity_empty_collection_returns_zero(monkeypatch):
    collection = FakeFindCollection([])
    mdb = {"metar": collection}
    write_fn = FakeWrite()
    monkeypatch.setattr(entities, "add_to_checkpoint_set", lambda *a, **k: None)

    n = entities._sync_entity(mdb, "metar", entities.METAR_FIELDS, write_fn, "metar")

    assert n == 0
    assert write_fn.calls == []
