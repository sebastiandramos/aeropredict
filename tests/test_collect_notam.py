"""Tests del collector de NOTAM de ENAIRE (todo 7, data-source-collectors).

Patrón importlib + monkeypatch de ``tests/test_collect_metar.py``: sin red,
sin DB, sin Delta real. La capa HTTP se mockea en
``aeropredict.sources.notam_enaire.http_get_with_retry`` y la escritura en
``scripts.collect_notam.write_raw_snapshot``.
"""

import importlib.util
import logging
from pathlib import Path

import requests

from aeropredict.sources import notam_enaire


def _load_collect_notam_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "collect_notam.py"
    spec = importlib.util.spec_from_file_location("collect_notam_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _feature_collection_fixture(layer_suffix: str = "") -> dict:
    """GeoJSON FeatureCollection inline (2 features), forma real del servicio."""
    return {
        "type": "FeatureCollection",
        "name": "NOTAM_APP_V3",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25830"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"IDENT": f"A0001/26{layer_suffix}", "ICAO": "LEMD", "CODE": "A"},
                "geometry": {"type": "Point", "coordinates": [-3.567, 40.472]},
            },
            {
                "type": "Feature",
                "properties": {"IDENT": f"A0002/26{layer_suffix}", "ICAO": "LEBL", "CODE": "A"},
                "geometry": {"type": "Point", "coordinates": [2.078, 41.297]},
            },
        ],
    }


def _layer_payload(layer: int) -> dict:
    """Payload normalizado que devuelve el adapter para una capa."""
    return {"raw": _feature_collection_fixture(str(layer)), "count": 2, "layer": layer}


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=resp)


def _make_fake_adapter(getter):
    """Clase fake que reemplaza a ``NotamEnaireAdapter`` en el módulo del script."""

    class FakeNotamAdapter:
        def __init__(self) -> None:
            pass

        def get_notam_layer(self, layer: int):
            return getter(layer)

    return FakeNotamAdapter


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def test_adapter_queries_layer_0_with_expected_url_and_params(monkeypatch):
    calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        calls.append((url, params))
        return _feature_collection_fixture()

    monkeypatch.setattr(notam_enaire, "http_get_with_retry", fake_http_get)

    result = notam_enaire.NotamEnaireAdapter().get_notam_layer(0)

    assert len(calls) == 1
    url, params = calls[0]
    assert url.endswith("/NOTAM/NOTAM_APP_V3/FeatureServer/0/query")
    assert params == {"where": "1=1", "f": "geojson", "outFields": "*"}

    assert result is not None
    assert result["layer"] == 0
    assert result["count"] == 2
    assert result["raw"] == _feature_collection_fixture()


def test_adapter_queries_layer_1_url(monkeypatch):
    calls = []

    def fake_http_get(url, headers=None, params=None, timeout=30):
        calls.append(url)
        return _feature_collection_fixture()

    monkeypatch.setattr(notam_enaire, "http_get_with_retry", fake_http_get)

    result = notam_enaire.NotamEnaireAdapter().get_notam_layer(1)

    assert result is not None
    assert result["layer"] == 1
    assert len(calls) == 1
    assert calls[0].endswith("/FeatureServer/1/query")


def test_adapter_warns_on_exceeded_transfer_limit(monkeypatch, caplog):
    fc = _feature_collection_fixture()
    fc["exceededTransferLimit"] = True
    monkeypatch.setattr(
        notam_enaire, "http_get_with_retry",
        lambda url, headers=None, params=None, timeout=30: fc,
    )

    with caplog.at_level(logging.WARNING, logger="aeropredict.sources.notam_enaire"):
        result = notam_enaire.NotamEnaireAdapter().get_notam_layer(0)

    assert result is not None
    assert result["count"] == 2  # la respuesta se sigue normalizando
    assert "exceededTransferLimit" in caplog.text


def test_adapter_returns_none_for_non_geojson(monkeypatch):
    monkeypatch.setattr(
        notam_enaire, "http_get_with_retry",
        lambda url, headers=None, params=None, timeout=30: {"foo": "bar"},
    )

    assert notam_enaire.NotamEnaireAdapter().get_notam_layer(0) is None


def test_adapter_propagates_http_errors(monkeypatch):
    # 401/403 (auth-gate) y 5xx deben PROPAGARSE para que el collector
    # distinga fallo graceful (auth) de fallo real (5xx).
    for status in (401, 403, 500, 503):
        def fake_http_get(url, headers=None, params=None, timeout=30, _status=status):
            raise _http_error(_status)

        monkeypatch.setattr(notam_enaire, "http_get_with_retry", fake_http_get)
        try:
            notam_enaire.NotamEnaireAdapter().get_notam_layer(0)
        except requests.HTTPError as exc:
            assert exc.response is not None and exc.response.status_code == status
        else:
            raise AssertionError(f"HTTP {status} debería propagarse")


def test_adapter_propagates_network_errors(monkeypatch):
    def fake_http_get(url, headers=None, params=None, timeout=30):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(notam_enaire, "http_get_with_retry", fake_http_get)

    try:
        notam_enaire.NotamEnaireAdapter().get_notam_layer(0)
    except requests.ConnectionError:
        pass
    else:
        raise AssertionError("Los errores de red deberían propagarse")


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def test_collect_writes_snapshot_with_both_layers(monkeypatch):
    module = _load_collect_notam_module()
    monkeypatch.setattr(
        module, "NotamEnaireAdapter",
        _make_fake_adapter(lambda layer: _layer_payload(layer)),
    )

    writes = []

    def fake_write_raw_snapshot(
        source_name, endpoint, params, response_data, delta_root, storage_options=None,
    ):
        writes.append((source_name, endpoint, params, response_data, delta_root))
        return 1

    monkeypatch.setattr(module, "write_raw_snapshot", fake_write_raw_snapshot)

    stats = module.collect_notam(dry_run=False, delta_root="data/raw")

    assert stats["total"] == 2
    assert stats["notam_written"] == 2
    assert stats["errors"] == 0
    assert stats["auth_gated"] == 0

    # Un solo write por run: write_raw_snapshot es mode="overwrite", dos
    # llamadas por capa se borrarían entre sí (lección del todo 5).
    assert len(writes) == 1
    source_name, endpoint, params, response_data, delta_root = writes[0]
    assert source_name == "notam_enaire"
    assert endpoint.endswith("/NOTAM_APP_V3/FeatureServer")
    assert params == {"layers": "0,1"}
    assert delta_root == "data/raw"

    assert response_data["source"] == "notam_enaire"
    assert isinstance(response_data["snapshot_at"], str) and "T" in response_data["snapshot_at"]
    layers = response_data["layers"]
    assert [layer["layer"] for layer in layers] == [0, 1]
    assert all(layer["count"] == 2 for layer in layers)
    assert layers[0]["raw"]["features"][0]["properties"]["IDENT"] == "A0001/260"


def test_collect_skips_empty_response_without_writing(monkeypatch):
    module = _load_collect_notam_module()
    monkeypatch.setattr(
        module, "NotamEnaireAdapter",
        _make_fake_adapter(lambda layer: None),
    )

    writes = []
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: writes.append(a) or 1)

    stats = module.collect_notam(dry_run=False, delta_root="data/raw")

    assert stats["notam_written"] == 0
    assert stats["skipped"] == 2
    assert stats["errors"] == 0
    assert writes == []


def test_collect_dry_run_does_not_fetch(monkeypatch):
    module = _load_collect_notam_module()
    monkeypatch.setattr(
        module, "NotamEnaireAdapter",
        _make_fake_adapter(lambda layer: _layer_payload(layer)),
    )
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: 1)

    stats = module.collect_notam(dry_run=True, delta_root="data/raw")

    assert stats["total"] == 2
    assert stats["notam_written"] == 0
    assert stats["errors"] == 0


def test_collect_auth_gate_401_is_graceful(monkeypatch):
    module = _load_collect_notam_module()

    def raise_401(layer):
        raise _http_error(401)

    monkeypatch.setattr(module, "NotamEnaireAdapter", _make_fake_adapter(raise_401))

    writes = []
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: writes.append(a) or 1)

    stats = module.collect_notam(dry_run=False, delta_root="data/raw")

    assert stats["errors"] == 2
    assert stats["auth_gated"] == 2
    assert stats["notam_written"] == 0
    assert writes == []  # snapshot bueno previo intacto


def test_collect_500_is_real_failure(monkeypatch):
    module = _load_collect_notam_module()

    def raise_500(layer):
        raise _http_error(500)

    monkeypatch.setattr(module, "NotamEnaireAdapter", _make_fake_adapter(raise_500))

    writes = []
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: writes.append(a) or 1)

    stats = module.collect_notam(dry_run=False, delta_root="data/raw")

    assert stats["errors"] == 2
    assert stats["auth_gated"] == 0
    assert stats["notam_written"] == 0
    assert writes == []


# ---------------------------------------------------------------------------
# CLI / exit codes
# ---------------------------------------------------------------------------


def test_main_exit_codes(monkeypatch):
    module = _load_collect_notam_module()
    monkeypatch.setattr(module, "get_delta_root", lambda: "data/raw")

    # exit 0: dry-run
    monkeypatch.setattr(
        module, "collect_notam",
        lambda **kw: {"total": 2, "notam_written": 0, "skipped": 0, "errors": 0, "auth_gated": 0},
    )
    assert module.main(["--dry-run"]) == 0

    # exit 1: todas las capas fallaron con error real (no auth)
    monkeypatch.setattr(
        module, "collect_notam",
        lambda **kw: {"total": 2, "notam_written": 0, "skipped": 0, "errors": 2, "auth_gated": 0},
    )
    assert module.main([]) == 1

    # exit 0: fallo parcial (el run continúa)
    monkeypatch.setattr(
        module, "collect_notam",
        lambda **kw: {"total": 2, "notam_written": 1, "skipped": 0, "errors": 1, "auth_gated": 1},
    )
    assert module.main([]) == 0


def test_main_401_exits_0(monkeypatch):
    module = _load_collect_notam_module()
    monkeypatch.setattr(module, "get_delta_root", lambda: "data/raw")

    def raise_401(_layer):
        raise _http_error(401)

    monkeypatch.setattr(module, "NotamEnaireAdapter", _make_fake_adapter(raise_401))
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: 1)

    # Auth-gate en ambas capas -> fallo graceful, exit 0 (decisión del usuario)
    assert module.main([]) == 0


def test_main_500_exits_1(monkeypatch):
    module = _load_collect_notam_module()
    monkeypatch.setattr(module, "get_delta_root", lambda: "data/raw")

    def raise_500(_layer):
        raise _http_error(500)

    monkeypatch.setattr(module, "NotamEnaireAdapter", _make_fake_adapter(raise_500))
    monkeypatch.setattr(module, "write_raw_snapshot", lambda *a, **k: 1)

    # 5xx en ambas capas -> fallo real, exit 1
    assert module.main([]) == 1
