"""Tests del collector EUROCONTROL PRU (todo 5, data-source-collectors).

Patrón importlib + monkeypatch de ``tests/test_collect_metar.py``: sin red,
sin DB, sin Delta real. La capa de descarga se mockea en
``aeropredict.sources.eurocontrol.download_to_file`` y las escrituras en el
módulo del script (``write_raw_csv`` / ``write_raw_snapshot``).
"""

import bz2
import importlib.util
import logging
from pathlib import Path

import requests

from aeropredict.sources import eurocontrol

# Fixtures CSV inline pequeños (UTF-8, BOM y CRLF para probar que el texto
# se conserva exacto, sin json.dumps).
AIRPORT_TRAFFIC_CSV = (
    "\ufeffYear,Month,APT_ICAO,APT_NAME,Flights\r\n"
    "2026,1,LEMD,Madrid Barajas,1200\r\n"
    "2026,1,LEBL,Barcelona El Prat,900\r\n"
)
APT_DLY_CSV = (
    "Year,Month,APT_ICAO,APT_NAME,TotalDelay\r\n"
    "2026,1,LEMD,Madrid Barajas,5400\r\n"
)
PRE_DEP_CSV = (
    "Year,Month,APT_ICAO,APT_NAME,PreDepDelay\r\n"
    "2026,1,LEMD,Madrid Barajas,1800\r\n"
)


def _load_collect_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "collect_eurocontrol.py"
    spec = importlib.util.spec_from_file_location("collect_eurocontrol_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture_path(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _fake_downloader(files: dict[str, str]):
    """Downloader fake: escribe el fixture (o lo comprime a .bz2) en dest_path."""
    def fake_download_to_file(url, dest_path):
        text = files[next(k for k in files if k in url)]
        if url.endswith(".bz2"):
            Path(dest_path).write_bytes(bz2.compress(text.encode("utf-8")))
        else:
            Path(dest_path).write_text(text, encoding="utf-8")
        return str(Path(dest_path).resolve())
    return fake_download_to_file


# ---------------------------------------------------------------------------
# Fuente: descargas y descompresión bz2
# ---------------------------------------------------------------------------


def test_download_pru_csvs_decompresses_bz2_utf8(tmp_path, monkeypatch):
    """apt_dly: el .bz2 se descomprime como UTF-8 y el binario no se persiste."""
    text = "Year,Month,APT_ICAO,APT_NAME,TotalDelay\r\n2026,1,LEMD,Málaga,5400\r\n"
    monkeypatch.setattr(eurocontrol, "download_to_file", _fake_downloader({"apt_dly": text}))

    entries = eurocontrol.download_pru_csvs(2026, str(tmp_path), ["apt_dly"])

    assert len(entries) == 1
    entry = entries[0]
    assert "error" not in entry
    assert entry["filename"] == "apt_dly"
    assert entry["url"].endswith("apt_dly_2026.csv.bz2")
    assert entry["path"].endswith(".csv")  # descomprimido, no .bz2
    assert not list(tmp_path.glob("*.bz2"))  # el .bz2 no se persiste
    decoded = Path(entry["path"]).read_text(encoding="utf-8")
    assert "Málaga" in decoded  # UTF-8, no cp1252


def test_download_pru_csvs_downloads_all_three_with_year(tmp_path, monkeypatch):
    """Las 3 URLs usan el año parametrizado (--year)."""
    files = {
        "airport_traffic": AIRPORT_TRAFFIC_CSV,
        "apt_dly": APT_DLY_CSV,
        "all_pre_departure_delays": PRE_DEP_CSV,
    }
    monkeypatch.setattr(eurocontrol, "download_to_file", _fake_downloader(files))

    entries = eurocontrol.download_pru_csvs(2025, str(tmp_path))

    assert [e["filename"] for e in entries] == [
        "airport_traffic", "apt_dly", "all_pre_departure_delays",
    ]
    assert all("error" not in e for e in entries)
    urls = [e["url"] for e in entries]
    assert any(u.endswith("airport_traffic_2025.csv") for u in urls)
    assert any(u.endswith("apt_dly_2025.csv.bz2") for u in urls)
    assert any(u.endswith("all_pre_departure_delays_2025.csv") for u in urls)


def test_download_pru_csvs_tolerates_corrupt_utf8_bytes(tmp_path, monkeypatch):
    """Un .bz2 con bytes no UTF-8 no aborta: se reemplazan con U+FFFD.

    Verificado en vivo 2026-08-03: ``apt_dly_2025.csv.bz2`` es UTF-8 con
    bytes latinos sueltos (0xfc, 0x8d) que rompen la decodificación estricta.
    """
    good = "Year,Month,APT_ICAO,APT_NAME,TotalDelay\r\n2026,1,LEMD,Madrid,5400\r\n"
    raw_bytes = good.encode("utf-8").replace(b"Madrid", b"Madr\xfcd")  # 0xfc latino

    def fake_download_to_file(url, dest_path):
        Path(dest_path).write_bytes(bz2.compress(raw_bytes))
        return str(Path(dest_path).resolve())

    monkeypatch.setattr(eurocontrol, "download_to_file", fake_download_to_file)

    entries = eurocontrol.download_pru_csvs(2026, str(tmp_path), ["apt_dly"])

    assert "error" not in entries[0]
    decoded = Path(entries[0]["path"]).read_text(encoding="utf-8")
    assert "\ufffd" in decoded  # byte corrupto → U+FFFD, sin abortar


def test_decompress_bz2_streams_with_copyfileobj_64kb(tmp_path, monkeypatch):
    """_decompress_bz2 copia en streaming: copyfileobj con chunks de 64 KB."""
    raw_bytes = (
        b"Year,Month,APT_ICAO,APT_NAME,TotalDelay\r\n"
        b"2026,1,LEMD,Madrid,5400\r\n"
    )
    bz2_path = tmp_path / "apt_dly.csv.bz2"
    bz2_path.write_bytes(bz2.compress(raw_bytes))

    captured: dict = {}

    def fake_copyfileobj(src, dst, length=16 * 1024):
        captured["length"] = length
        while True:
            buf = src.read(length)
            if not buf:
                break
            dst.write(buf)

    monkeypatch.setattr(eurocontrol.shutil, "copyfileobj", fake_copyfileobj)

    dest = eurocontrol._decompress_bz2(str(bz2_path))

    assert captured["length"] == 64 * 1024
    # bz2 en modo texto aplica newlines universales (CRLF → LF), igual que la
    # implementación original; el contenido se conserva exacto.
    expected = raw_bytes.decode("utf-8").replace("\r\n", "\n")
    with open(dest, encoding="utf-8", newline="") as f:
        assert f.read() == expected
    assert not Path(bz2_path).exists()  # el .bz2 no se persiste


def test_decompress_bz2_warns_on_utf8_replacement(tmp_path, monkeypatch, caplog):
    """Bytes no UTF-8 → U+FFFD en la salida + warning con el conteo."""
    raw_bytes = (
        b"Year,Month,APT_ICAO,APT_NAME,TotalDelay\r\n"
        b"2026,1,LEMD,Madr\xfcd,5400\r\n"  # 0xfc latino inválido en UTF-8
    )
    bz2_path = tmp_path / "apt_dly.csv.bz2"
    bz2_path.write_bytes(bz2.compress(raw_bytes))

    with caplog.at_level(logging.WARNING):
        dest = eurocontrol._decompress_bz2(str(bz2_path))

    decoded = Path(dest).read_text(encoding="utf-8")
    assert "\ufffd" in decoded  # byte corrupto → U+FFFD, sin abortar
    assert any("U+FFFD" in record.message for record in caplog.records)


def test_download_pru_csvs_404_marks_error_and_continues(tmp_path, monkeypatch):
    """Un 404 (año futuro / URL drift) marca error por archivo sin abortar."""
    def fake_download_to_file(url, dest_path):
        resp = requests.Response()
        resp.status_code = 404
        err = requests.HTTPError(f"404 Client Error for {url}")
        err.response = resp
        raise err

    monkeypatch.setattr(eurocontrol, "download_to_file", fake_download_to_file)

    entries = eurocontrol.download_pru_csvs(2030, str(tmp_path))

    assert len(entries) == 3
    assert all(e["error"] for e in entries)
    assert all(e["status_code"] == 404 for e in entries)


# ---------------------------------------------------------------------------
# Collector: escritura Bronze
# ---------------------------------------------------------------------------


def test_collect_eurocontrol_writes_bronze(tmp_path, monkeypatch):
    """Los 2 primeros CSVs → write_raw_csv; el último anual → write_raw_snapshot."""
    module = _load_collect_module()
    fixtures = {
        "airport_traffic": AIRPORT_TRAFFIC_CSV,
        "apt_dly": APT_DLY_CSV,
        "all_pre_departure_delays": PRE_DEP_CSV,
    }

    def fake_download_pru_csvs(year, dest_dir, files=None):
        assert year == 2026
        return [
            {
                "filename": name,
                "url": eurocontrol.pru_url(name, year),
                "path": _fixture_path(tmp_path, f"{name}.csv", text),
            }
            for name, text in fixtures.items()
        ]

    monkeypatch.setattr(module, "download_pru_csvs", fake_download_pru_csvs)

    csv_calls: list[tuple] = []
    snapshot_calls: list[tuple] = []
    write_order: list[str] = []

    def fake_write_raw_csv(*a, **k):
        csv_calls.append(a)
        write_order.append("csv")
        return 1

    def fake_write_raw_snapshot(*a, **k):
        snapshot_calls.append(a)
        write_order.append("snapshot")
        return 1

    monkeypatch.setattr(module, "write_raw_csv", fake_write_raw_csv)
    monkeypatch.setattr(module, "write_raw_snapshot", fake_write_raw_snapshot)

    stats = module.collect_eurocontrol(2026, dry_run=False, delta_root="data/raw")

    assert stats == {"total": 3, "written": 3, "skipped": 0, "errors": 0}

    # El snapshot (overwrite) se escribe PRIMERO y los appends después; si el
    # orden se invirtiera, el overwrite final borraría las filas de los appends.
    assert write_order == ["snapshot", "csv", "csv"]

    # airport_traffic y apt_dly → write_raw_csv (response = csv_text, sin json.dumps)
    assert len(csv_calls) == 2
    source, _endpoint, params, csv_text, delta_root = csv_calls[0]
    assert source == "eurocontrol_pru"
    assert params == {"filename": "airport_traffic", "year": 2026}
    assert delta_root == "data/raw"
    assert csv_text == AIRPORT_TRAFFIC_CSV
    assert csv_calls[1][2] == {"filename": "apt_dly", "year": 2026}

    # último CSV anual → write_raw_snapshot (overwrite, tabla = último juego anual)
    assert len(snapshot_calls) == 1
    snap_source, _snap_endpoint, snap_params, snap_text, _snap_root = snapshot_calls[0]
    assert snap_source == "eurocontrol_pru"
    assert snap_params == {"filename": "all_pre_departure_delays", "year": 2026}
    assert snap_text == PRE_DEP_CSV


def test_collect_eurocontrol_skips_snapshot_on_failure(tmp_path, monkeypatch):
    """Si el último CSV anual falla (404), NO se escribe nada: snapshot intacto.

    El snapshot (overwrite) es la pieza que se destruiría con un juego anual
    incompleto; si falla, la guardia aborta el run completo antes de cualquier
    escritura (ni los appends de los 2 archivos buenos).
    """
    module = _load_collect_module()
    monkeypatch.setattr(module, "download_pru_csvs", lambda *a, **k: [
        {
            "filename": "airport_traffic",
            "url": eurocontrol.pru_url("airport_traffic", 2026),
            "path": _fixture_path(tmp_path, "airport_traffic.csv", AIRPORT_TRAFFIC_CSV),
        },
        {
            "filename": "apt_dly",
            "url": eurocontrol.pru_url("apt_dly", 2026),
            "path": _fixture_path(tmp_path, "apt_dly.csv", APT_DLY_CSV),
        },
        {
            "filename": "all_pre_departure_delays",
            "url": eurocontrol.pru_url("all_pre_departure_delays", 2026),
            "error": "404 Client Error",
            "status_code": 404,
        },
    ])
    csv_calls: list[tuple] = []
    snapshot_calls: list[tuple] = []
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: csv_calls.append(a) or 1)
    monkeypatch.setattr(
        module, "write_raw_snapshot", lambda *a, **k: snapshot_calls.append(a) or 1,
    )

    stats = module.collect_eurocontrol(2026, delta_root="data/raw")

    assert stats["written"] == 0
    assert stats["skipped"] == 2  # los 2 archivos buenos quedan sin escribir
    assert stats["errors"] == 1
    assert csv_calls == []
    assert snapshot_calls == []


def test_collect_eurocontrol_logs_url_drift(tmp_path, monkeypatch, caplog):
    """404 → log claro de 'URL drift' + error en stats."""
    module = _load_collect_module()
    monkeypatch.setattr(module, "download_pru_csvs", lambda *a, **k: [
        {
            "filename": name,
            "url": eurocontrol.pru_url(name, 2030),
            "error": "404 Client Error",
            "status_code": 404,
        }
        for name in ("airport_traffic", "apt_dly", "all_pre_departure_delays")
    ])
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: 1)
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: 1)

    with caplog.at_level(logging.ERROR):
        stats = module.collect_eurocontrol(2030, delta_root="data/raw")

    assert stats["errors"] == 3
    assert any("URL drift" in record.message for record in caplog.records)


def test_collect_eurocontrol_skips_empty_files(tmp_path, monkeypatch):
    """Contenido vacío → skip (no se escribe fila vacía, no se toca el snapshot)."""
    module = _load_collect_module()
    monkeypatch.setattr(module, "download_pru_csvs", lambda *a, **k: [
        {
            "filename": "airport_traffic",
            "url": eurocontrol.pru_url("airport_traffic", 2026),
            "path": _fixture_path(tmp_path, "airport_traffic.csv", AIRPORT_TRAFFIC_CSV),
        },
        {
            "filename": "apt_dly",
            "url": eurocontrol.pru_url("apt_dly", 2026),
            "path": _fixture_path(tmp_path, "apt_dly.csv", ""),
        },
        {
            "filename": "all_pre_departure_delays",
            "url": eurocontrol.pru_url("all_pre_departure_delays", 2026),
            "path": _fixture_path(tmp_path, "all_pre_departure_delays.csv", PRE_DEP_CSV),
        },
    ])
    csv_calls: list[tuple] = []
    snapshot_calls: list[tuple] = []
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: csv_calls.append(a) or 1)
    monkeypatch.setattr(
        module, "write_raw_snapshot", lambda *a, **k: snapshot_calls.append(a) or 1,
    )

    stats = module.collect_eurocontrol(2026, delta_root="data/raw")

    assert stats["written"] == 2
    assert stats["skipped"] == 1
    assert stats["errors"] == 0
    assert len(csv_calls) == 1  # apt_dly vacío no se escribe
    assert len(snapshot_calls) == 1


def test_collect_eurocontrol_dry_run_does_not_fetch(tmp_path, monkeypatch):
    module = _load_collect_module()
    download_calls: list[tuple] = []
    monkeypatch.setattr(
        module, "download_pru_csvs",
        lambda *a, **k: download_calls.append(a) or [],
    )
    monkeypatch.setattr(module, "write_raw_csv", lambda *a, **k: 1)
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: 1)

    stats = module.collect_eurocontrol(2026, dry_run=True, delta_root="data/raw")

    assert stats == {"total": 3, "written": 0, "skipped": 0, "errors": 0}
    assert download_calls == []


def test_main_exit_codes(monkeypatch):
    module = _load_collect_module()
    monkeypatch.setattr(module, "get_delta_root", lambda: "data/raw")

    monkeypatch.setattr(
        module, "collect_eurocontrol",
        lambda **kwargs: {"total": 3, "written": 0, "skipped": 0, "errors": 0},
    )
    assert module.main(["--year", "2026", "--dry-run"]) == 0

    monkeypatch.setattr(
        module, "collect_eurocontrol",
        lambda **kwargs: {"total": 3, "written": 0, "skipped": 0, "errors": 3},
    )
    assert module.main(["--year", "2030"]) == 1

    monkeypatch.setattr(
        module, "collect_eurocontrol",
        lambda **kwargs: {"total": 3, "written": 2, "skipped": 0, "errors": 1},
    )
    assert module.main(["--year", "2026"]) == 0
