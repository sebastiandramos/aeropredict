"""Tests del collector OurAirports (todo 6, data-source-collectors).

Patrón importlib + monkeypatch de ``tests/test_collect_eurocontrol.py``:
sin red, sin DB, sin Delta real. La capa de descarga se mockea en
``aeropredict.sources.ourairports.download_to_file`` y las escrituras en el
módulo del script (``write_raw_csv`` / ``write_raw_snapshot`` / Mongo).
"""

import csv
import importlib.util
import io
import logging
from pathlib import Path

import pytest
import requests

from aeropredict.sources import ourairports

# Cabecera real de OurAirports (18 columnas) para las fixtures.
_AIRPORT_HEADER = [
    "id", "ident", "type", "name", "latitude_deg", "longitude_deg",
    "elevation_ft", "continent", "iso_country", "iso_region", "municipality",
    "scheduled_service", "gps_code", "iata_code", "local_code", "home_link",
    "wikipedia_link", "keywords",
]

_AIRPORT_ROWS = [
    {
        "id": "4019", "ident": "LEMD", "type": "large_airport",
        "name": "Adolfo Suarez Madrid-Barajas Airport",
        "latitude_deg": "40.4722", "longitude_deg": "-3.5609",
        "elevation_ft": "1998", "continent": "EU", "iso_country": "ES",
        "iso_region": "ES-MD", "municipality": "Madrid",
        "scheduled_service": "yes", "gps_code": "LEMD", "iata_code": "MAD",
    },
    {
        "id": "20850", "ident": "LEBL", "type": "large_airport",
        "name": "Barcelona International Airport",
        "latitude_deg": "41.2971", "longitude_deg": "2.0785",
        "elevation_ft": "14", "continent": "EU", "iso_country": "ES",
        "iso_region": "ES-CT", "municipality": "Barcelona",
        "scheduled_service": "yes", "gps_code": "LEBL", "iata_code": "BCN",
    },
    {
        "id": "20864", "ident": "LEAL", "type": "medium_airport",
        "name": "Alicante-Elche Miguel Hernandez Airport",
        "latitude_deg": "38.2822", "longitude_deg": "-0.5582",
        "elevation_ft": "142", "continent": "EU", "iso_country": "ES",
        "iso_region": "ES-VC", "municipality": "Alicante",
        "scheduled_service": "yes", "gps_code": "LEAL", "iata_code": "ALC",
    },
    {
        "id": "26", "ident": "KJFK", "type": "large_airport",
        "name": "John F Kennedy International Airport",
        "latitude_deg": "40.6398", "longitude_deg": "-73.7789",
        "elevation_ft": "13", "continent": "NA", "iso_country": "US",
        "iso_region": "US-NY", "municipality": "New York",
        "scheduled_service": "yes", "gps_code": "KJFK", "iata_code": "JFK",
    },
    {
        # Aeropuerto sin código IATA ni ICAO (gps_code vacío): los campos
        # opcionales se normalizan a "".
        "id": "99999", "ident": "ZZ-SEA", "type": "seaplane_base",
        "name": "Remote Seaplane Base",
        "latitude_deg": "-54.0", "longitude_deg": "-68.5",
        "elevation_ft": "", "continent": "SA", "iso_country": "AR",
        "iso_region": "AR-U", "municipality": "Ushuaia",
        "scheduled_service": "no", "gps_code": "", "iata_code": "",
    },
]

_RUNWAY_HEADER = [
    "id", "airport_ref", "airport_ident", "length_ft", "width_ft", "surface",
    "lighted", "closed", "le_ident", "le_latitude_deg", "le_longitude_deg",
    "le_elevation_ft", "le_heading_degT", "le_displaced_threshold_ft",
    "he_ident", "he_latitude_deg", "he_longitude_deg", "he_elevation_ft",
    "he_heading_degT", "he_displaced_threshold_ft",
]

_RUNWAY_ROWS = [
    {
        "id": "256151", "airport_ref": "4078", "airport_ident": "LEMD",
        "length_ft": "13450", "width_ft": "197", "surface": "asphalt",
        "lighted": "1", "closed": "0", "le_ident": "14R",
        "le_latitude_deg": "40.46", "le_longitude_deg": "-3.57",
        "le_elevation_ft": "1996", "le_heading_degT": "141",
        "le_displaced_threshold_ft": "0", "he_ident": "32L",
        "he_latitude_deg": "40.48", "he_longitude_deg": "-3.54",
        "he_elevation_ft": "1996", "he_heading_degT": "321",
        "he_displaced_threshold_ft": "0",
    },
    {
        "id": "256152", "airport_ref": "4078", "airport_ident": "LEMD",
        "length_ft": "11483", "width_ft": "197", "surface": "asphalt",
        "lighted": "1", "closed": "0", "le_ident": "18L",
        "le_latitude_deg": "40.46", "le_longitude_deg": "-3.55",
        "le_elevation_ft": "1990", "le_heading_degT": "181",
        "le_displaced_threshold_ft": "0", "he_ident": "36R",
        "he_latitude_deg": "40.49", "he_longitude_deg": "-3.53",
        "he_elevation_ft": "1985", "he_heading_degT": "1",
        "he_displaced_threshold_ft": "0",
    },
    {
        # Pista sin superficie declarada: se normaliza a "".
        "id": "256153", "airport_ref": "4078", "airport_ident": "LEMD",
        "length_ft": "11079", "width_ft": "148", "surface": "",
        "lighted": "1", "closed": "0", "le_ident": "18R",
        "le_latitude_deg": "40.46", "le_longitude_deg": "-3.56",
        "le_elevation_ft": "1990", "le_heading_degT": "181",
        "le_displaced_threshold_ft": "0", "he_ident": "36L",
        "he_latitude_deg": "40.49", "he_longitude_deg": "-3.53",
        "he_elevation_ft": "1990", "he_heading_degT": "1",
        "he_displaced_threshold_ft": "0",
    },
]


def _to_csv(header: list[str], rows: list[dict[str, str]]) -> str:
    """Construye el texto CSV con el header y las filas dadas (UTF-8)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


AIRPORTS_CSV = _to_csv(_AIRPORT_HEADER, _AIRPORT_ROWS)
RUNWAYS_CSV = _to_csv(_RUNWAY_HEADER, _RUNWAY_ROWS)


def _load_collect_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "collect_ourairports.py"
    spec = importlib.util.spec_from_file_location("collect_ourairports_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture_path(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _stub_bronze_writes(monkeypatch, module):
    """Monkeypatchea las 4 escrituras y devuelve la lista de llamadas."""
    calls: list[tuple[str, tuple]] = []

    def fake_snapshot(*a, **k):
        calls.append(("snapshot", a))
        return 1

    def fake_csv(*a, **k):
        calls.append(("csv", a))
        return 1

    monkeypatch.setattr(module, "write_raw_snapshot", fake_snapshot)
    monkeypatch.setattr(module, "write_raw_csv", fake_csv)
    return calls


# ---------------------------------------------------------------------------
# Fuente: descarga stream y parseo
# ---------------------------------------------------------------------------


def test_download_to_file_streams_with_timeout(monkeypatch, tmp_path):
    """Descarga tipo aircraft_db: stream=True, timeout=300, chunks de 1 MB."""

    class FakeResp:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            assert chunk_size == ourairports.CHUNK_SIZE
            yield b"a" * 1024
            yield b"b" * 10

    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResp()

    monkeypatch.setattr(ourairports.requests, "get", fake_get)

    path = ourairports.download_to_file(
        "https://example.com/x.csv", str(tmp_path / "x.csv"),
    )

    assert captured["url"] == "https://example.com/x.csv"
    assert captured["stream"] is True
    assert captured["timeout"] == 300
    assert Path(path).read_bytes() == b"a" * 1024 + b"b" * 10


def test_download_ourairports_downloads_both_files(monkeypatch, tmp_path):
    """Las 2 URLs de OurAirports se descargan y devuelven entradas sin error."""
    downloaded: list[str] = []

    def fake_download_to_file(url, dest_path):
        downloaded.append(url)
        Path(dest_path).write_text("x", encoding="utf-8")
        return str(Path(dest_path).resolve())

    monkeypatch.setattr(ourairports, "download_to_file", fake_download_to_file)

    entries = ourairports.download_ourairports(str(tmp_path))

    assert [e["filename"] for e in entries] == ["airports", "runways"]
    assert all("error" not in e for e in entries)
    urls = [e["url"] for e in entries]
    assert any(u.endswith("airports.csv") for u in urls)
    assert any(u.endswith("runways.csv") for u in urls)
    assert all(u.startswith(ourairports.OURAIRPORTS_BASE_URL) for u in urls)


def test_download_ourairports_404_marks_error_and_continues(monkeypatch, tmp_path):
    """Un 404 marca error por archivo sin abortar la descarga del resto."""
    def fake_download_to_file(url, dest_path):
        resp = requests.Response()
        resp.status_code = 404
        err = requests.HTTPError(f"404 Client Error for {url}")
        err.response = resp
        raise err

    monkeypatch.setattr(ourairports, "download_to_file", fake_download_to_file)

    entries = ourairports.download_ourairports(str(tmp_path))

    assert len(entries) == 2
    assert all(e["error"] for e in entries)
    assert all(e["status_code"] == 404 for e in entries)


def test_parse_airports_csv_normalizes_fields():
    """Los 11 campos del plan se normalizan; gps_code → icao_code."""
    records = ourairports.parse_airports_csv(AIRPORTS_CSV)

    assert len(records) == 5
    assert records[0] == {
        "ident": "LEMD",
        "type": "large_airport",
        "name": "Adolfo Suarez Madrid-Barajas Airport",
        "latitude_deg": "40.4722",
        "longitude_deg": "-3.5609",
        "elevation_ft": "1998",
        "iso_country": "ES",
        "iso_region": "ES-MD",
        "municipality": "Madrid",
        "iata_code": "MAD",
        "icao_code": "LEMD",  # OurAirports no tiene icao_code: es gps_code
    }
    # Columnas extra (id, continent, ...) NO aparecen en el documento.
    assert "id" not in records[0]
    assert "continent" not in records[0]
    # Sin IATA ni ICAO → "" en lugar de None.
    assert records[4]["iata_code"] == ""
    assert records[4]["icao_code"] == ""
    assert records[4]["elevation_ft"] == ""
    assert all(set(r) == set(ourairports.AIRPORT_FIELDS) for r in records)


def test_parse_runways_csv_normalizes_fields():
    """Los 8 campos de pista del plan se normalizan."""
    records = ourairports.parse_runways_csv(RUNWAYS_CSV)

    assert len(records) == 3
    assert records[0] == {
        "airport_ident": "LEMD",
        "length_ft": "13450",
        "width_ft": "197",
        "surface": "asphalt",
        "le_ident": "14R",
        "he_ident": "32L",
        "le_heading_degT": "141",
        "he_heading_degT": "321",
    }
    assert records[2]["surface"] == ""  # superficie vacía → ""
    assert all(set(r) == set(ourairports.RUNWAY_FIELDS) for r in records)


def test_parse_airports_csv_missing_header_raises():
    """CSV sin cabecera válida → ValueError con mensaje claro."""
    with pytest.raises(ValueError, match="ident"):
        ourairports.parse_airports_csv("LEMD,large_airport,Madrid\nLEBL,large_airport,Barcelona\n")


def test_parse_runways_csv_missing_header_raises():
    with pytest.raises(ValueError, match="airport_ident"):
        ourairports.parse_runways_csv("LEMD,13450,asphalt\n")


def test_parse_skips_rows_without_ident():
    """Filas sin ``ident``/``airport_ident`` se omiten (como aircraft_db)."""
    header = ",".join(ourairports.AIRPORT_FIELDS)
    text = (
        f"{header}\n"
        ",large_airport,No ident airport,,,,,,,,\n"
        "LEMD,large_airport,Madrid Barajas,40.47,-3.56,1998,ES,ES-MD,Madrid,MAD,LEMD\n"
    )
    records = ourairports.parse_airports_csv(text)
    assert len(records) == 1
    assert records[0]["ident"] == "LEMD"


# ---------------------------------------------------------------------------
# Collector: escritura Bronze y Mongo
# ---------------------------------------------------------------------------


def test_collect_ourairports_writes_bronze(tmp_path, monkeypatch):
    """Cada dataset → su tabla: snapshot (overwrite) PRIMERO, csv (append) después."""
    module = _load_collect_module()
    fixtures = {"airports": AIRPORTS_CSV, "runways": RUNWAYS_CSV}

    def fake_download_ourairports(dest_dir, files=None):
        names = files or list(ourairports.OURAIRPORTS_FILES)
        return [
            {
                "filename": name,
                "url": ourairports.ourairports_url(name),
                "path": _fixture_path(tmp_path, f"{name}.csv", fixtures[name]),
            }
            for name in names
        ]

    monkeypatch.setattr(module, "download_ourairports", fake_download_ourairports)
    calls = _stub_bronze_writes(monkeypatch, module)
    monkeypatch.setattr(module, "write_airports", lambda recs: len(recs))
    monkeypatch.setattr(module, "write_runways", lambda recs: len(recs))

    stats = module.collect_ourairports(dry_run=False, delta_root="data/raw")

    assert stats == {
        "total": 2, "written": 2, "skipped": 0, "errors": 0,
        "airports": 5, "runways": 3, "mongo_written": 8,
    }

    # El snapshot (overwrite) se escribe PRIMERO y el csv (append) después:
    # si se invirtiera, el overwrite final borraría la fila del append.
    assert [(name, args[0]) for name, args in calls] == [
        ("snapshot", "ourairports_airports"),
        ("csv", "ourairports_airports"),
        ("snapshot", "ourairports_runways"),
        ("csv", "ourairports_runways"),
    ]

    # params {"dataset": ...} y texto CSV exacto (sin json.dumps, BOM intacto)
    snap_source, snap_endpoint, snap_params, snap_text, snap_root = calls[0][1]
    assert snap_source == "ourairports_airports"
    assert snap_endpoint == ourairports.ourairports_url("airports")
    assert snap_params == {"dataset": "airports"}
    assert snap_text == AIRPORTS_CSV
    assert snap_root == "data/raw"

    csv_source, _csv_endpoint, csv_params, csv_text, _csv_root = calls[1][1]
    assert csv_source == "ourairports_airports"
    assert csv_params == {"dataset": "airports"}
    assert csv_text == AIRPORTS_CSV

    # Tabla de runways usa su propio source_name.
    assert calls[2][1][0] == "ourairports_runways"
    assert calls[3][1][0] == "ourairports_runways"
    assert calls[2][1][2] == {"dataset": "runways"}


def test_collect_ourairports_writes_mongo_normalized(tmp_path, monkeypatch):
    """write_airports/write_runways reciben los docs normalizados (no el CSV)."""
    module = _load_collect_module()
    fixtures = {"airports": AIRPORTS_CSV, "runways": RUNWAYS_CSV}

    def fake_download_ourairports(dest_dir, files=None):
        names = files or list(ourairports.OURAIRPORTS_FILES)
        return [
            {
                "filename": name,
                "url": ourairports.ourairports_url(name),
                "path": _fixture_path(tmp_path, f"{name}.csv", fixtures[name]),
            }
            for name in names
        ]

    monkeypatch.setattr(module, "download_ourairports", fake_download_ourairports)
    _stub_bronze_writes(monkeypatch, module)

    captured: dict[str, list] = {}

    def fake_write_airports(records):
        captured["airports"] = records
        return len(records)

    def fake_write_runways(records):
        captured["runways"] = records
        return len(records)

    monkeypatch.setattr(module, "write_airports", fake_write_airports)
    monkeypatch.setattr(module, "write_runways", fake_write_runways)

    stats = module.collect_ourairports(delta_root="data/raw")

    assert stats["mongo_written"] == 8
    assert len(captured["airports"]) == 5
    assert len(captured["runways"]) == 3
    assert captured["airports"][0]["icao_code"] == "LEMD"
    assert captured["airports"][0]["ident"] == "LEMD"
    assert captured["runways"][0]["airport_ident"] == "LEMD"
    # El _id compuesto lo añade write_runways (storage_silver); el collector
    # pasa solo los campos normalizados.
    assert "_id" not in captured["runways"][0]


def test_collect_ourairports_mongo_unavailable_does_not_fail_run(
    tmp_path, monkeypatch, caplog,
):
    """Mongo caído → warning + Bronze escrito; NO cuenta como error ni aborta."""
    module = _load_collect_module()
    fixtures = {"airports": AIRPORTS_CSV, "runways": RUNWAYS_CSV}

    def fake_download_ourairports(dest_dir, files=None):
        names = files or list(ourairports.OURAIRPORTS_FILES)
        return [
            {
                "filename": name,
                "url": ourairports.ourairports_url(name),
                "path": _fixture_path(tmp_path, f"{name}.csv", fixtures[name]),
            }
            for name in names
        ]

    monkeypatch.setattr(module, "download_ourairports", fake_download_ourairports)
    calls = _stub_bronze_writes(monkeypatch, module)

    def fail_write(*a, **k):
        raise RuntimeError("connection refused: localhost:27017")

    monkeypatch.setattr(module, "write_airports", fail_write)
    monkeypatch.setattr(module, "write_runways", fail_write)

    with caplog.at_level(logging.WARNING):
        stats = module.collect_ourairports(delta_root="data/raw")

    assert stats["written"] == 2  # Bronze escrito igualmente
    assert stats["errors"] == 0  # Mongo no cuenta como error del collector
    assert stats["mongo_written"] == 0
    assert len(calls) == 4  # snapshot + csv por dataset
    assert any("Mongo" in record.message for record in caplog.records)


def test_collect_ourairports_skips_empty_dataset(tmp_path, monkeypatch):
    """Dataset descargado vacío → skip (sin fila vacía, sin tocar el otro)."""
    module = _load_collect_module()
    fixtures = {"airports": AIRPORTS_CSV, "runways": ""}

    def fake_download_ourairports(dest_dir, files=None):
        names = files or list(ourairports.OURAIRPORTS_FILES)
        return [
            {
                "filename": name,
                "url": ourairports.ourairports_url(name),
                "path": _fixture_path(tmp_path, f"{name}.csv", fixtures[name]),
            }
            for name in names
        ]

    monkeypatch.setattr(module, "download_ourairports", fake_download_ourairports)
    calls = _stub_bronze_writes(monkeypatch, module)
    monkeypatch.setattr(module, "write_airports", lambda recs: len(recs))
    monkeypatch.setattr(module, "write_runways", lambda recs: len(recs))

    stats = module.collect_ourairports(delta_root="data/raw")

    assert stats["written"] == 1
    assert stats["skipped"] == 1
    assert stats["errors"] == 0
    assert stats["airports"] == 5
    assert stats["runways"] == 0
    assert len(calls) == 2  # solo airports: snapshot + csv
    assert all(args[0] == "ourairports_airports" for _, args in calls)


def test_collect_ourairports_csv_without_header_logs_error(tmp_path, monkeypatch, caplog):
    """CSV sin cabecera → error claro; ese dataset no se escribe en Bronze."""
    module = _load_collect_module()
    fixtures = {"airports": "LEMD,large_airport,Madrid\n", "runways": RUNWAYS_CSV}

    def fake_download_ourairports(dest_dir, files=None):
        names = files or list(ourairports.OURAIRPORTS_FILES)
        return [
            {
                "filename": name,
                "url": ourairports.ourairports_url(name),
                "path": _fixture_path(tmp_path, f"{name}.csv", fixtures[name]),
            }
            for name in names
        ]

    monkeypatch.setattr(module, "download_ourairports", fake_download_ourairports)
    calls = _stub_bronze_writes(monkeypatch, module)
    monkeypatch.setattr(module, "write_airports", lambda recs: len(recs))
    monkeypatch.setattr(module, "write_runways", lambda recs: len(recs))

    with caplog.at_level(logging.ERROR):
        stats = module.collect_ourairports(delta_root="data/raw")

    assert stats["written"] == 1  # solo runways
    assert stats["errors"] == 1
    assert stats["airports"] == 0
    assert len(calls) == 2
    assert all(args[0] == "ourairports_runways" for _, args in calls)
    assert any("cabecera" in record.message for record in caplog.records)


def test_collect_ourairports_dry_run_does_not_fetch(monkeypatch):
    module = _load_collect_module()
    download_calls: list[tuple] = []
    monkeypatch.setattr(
        module, "download_ourairports",
        lambda *a, **k: download_calls.append(a) or [],
    )
    _stub_bronze_writes(monkeypatch, module)
    monkeypatch.setattr(module, "write_airports", lambda recs: len(recs))
    monkeypatch.setattr(module, "write_runways", lambda recs: len(recs))

    stats = module.collect_ourairports(dry_run=True, delta_root="data/raw")

    assert stats == {
        "total": 2, "written": 0, "skipped": 0, "errors": 0,
        "airports": 0, "runways": 0, "mongo_written": 0,
    }
    assert download_calls == []


def test_main_exit_codes(monkeypatch):
    module = _load_collect_module()
    monkeypatch.setattr(module, "get_delta_root", lambda: "data/raw")

    # exit 0: dry-run sin errores
    monkeypatch.setattr(
        module, "collect_ourairports",
        lambda **kwargs: {
            "total": 2, "written": 0, "skipped": 0, "errors": 0,
            "airports": 0, "runways": 0, "mongo_written": 0,
        },
    )
    assert module.main(["--dry-run"]) == 0

    # exit 1: ambos datasets fallaron
    monkeypatch.setattr(
        module, "collect_ourairports",
        lambda **kwargs: {
            "total": 2, "written": 0, "skipped": 0, "errors": 2,
            "airports": 0, "runways": 0, "mongo_written": 0,
        },
    )
    assert module.main([]) == 1

    # exit 0: fallo parcial (el run continúa)
    monkeypatch.setattr(
        module, "collect_ourairports",
        lambda **kwargs: {
            "total": 2, "written": 1, "skipped": 0, "errors": 1,
            "airports": 5, "runways": 0, "mongo_written": 5,
        },
    )
    assert module.main([]) == 0
