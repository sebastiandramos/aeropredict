"""Tests del helper compartido de descarga streaming (base.download_stream_to_file).

Patrón monkeypatch de ``requests.get`` (sin red, sin DB, sin Delta real):
se simulan respuestas 503/429/404 y se verifica el reintento, el cierre de la
respuesta (también en excepciones a mitad de stream) y el comportamiento
documentado ante status no-2xx.
"""

from pathlib import Path

import pytest
import requests

from aeropredict.sources import base


class _FakeResponse:
    """Respuesta requests fake con soporte de context manager (cierre SIEMPRE)."""

    def __init__(self, status_code=200, chunks=(), exc=None, headers=None):
        self.status_code = status_code
        self._chunks = chunks
        self._exc = exc
        self.headers = headers or {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def iter_content(self, chunk_size):
        yield from self._chunks
        if self._exc is not None:
            raise self._exc


def _patch_get(monkeypatch, responses):
    """Monkeypatchea ``requests.get`` devolviendo respuestas en secuencia."""
    calls: list[tuple] = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(base.requests, "get", fake_get)
    return calls


def test_download_stream_to_file_writes_stream(tmp_path, monkeypatch):
    """Descarga OK: stream=True, timeout=300, chunks de 1 MB, ruta absoluta."""
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse(chunks=[b"a" * 1024, b"b" * 10])

    monkeypatch.setattr(base.requests, "get", fake_get)

    path = base.download_stream_to_file(
        "https://example.com/x.csv", str(tmp_path / "x.csv"),
    )

    assert captured["url"] == "https://example.com/x.csv"
    assert captured["stream"] is True
    assert captured["timeout"] == 300
    assert Path(path).is_absolute()
    assert Path(path).read_bytes() == b"a" * 1024 + b"b" * 10


def test_download_stream_to_file_retries_503_then_succeeds(tmp_path, monkeypatch):
    """503 transitorio → reintento con backoff; el segundo GET (200) completa."""
    calls = _patch_get(monkeypatch, [
        _FakeResponse(status_code=503),
        _FakeResponse(chunks=[b"ok"]),
    ])
    sleeps: list[float] = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))

    path = base.download_stream_to_file(
        "https://example.com/x.csv", str(tmp_path / "x.csv"),
    )

    assert len(calls) == 2
    assert Path(path).read_bytes() == b"ok"
    assert len(sleeps) == 1  # backoff entre reintentos


def test_download_stream_to_file_persistent_5xx_fails(tmp_path, monkeypatch):
    """5xx persistente → HTTPError tras agotar los reintentos (MAX_RETRIES)."""
    calls = _patch_get(monkeypatch, [
        _FakeResponse(status_code=503) for _ in range(base.MAX_RETRIES)
    ])
    monkeypatch.setattr(base.time, "sleep", lambda s: None)

    with pytest.raises(requests.HTTPError) as excinfo:
        base.download_stream_to_file(
            "https://example.com/x.csv", str(tmp_path / "x.csv"),
        )

    assert excinfo.value.response.status_code == 503
    assert len(calls) == base.MAX_RETRIES


def test_download_stream_to_file_404_raises_immediately(tmp_path, monkeypatch):
    """404 (URL drift / año futuro) → HTTPError sin reintentos."""
    calls = _patch_get(monkeypatch, [_FakeResponse(status_code=404)])

    with pytest.raises(requests.HTTPError) as excinfo:
        base.download_stream_to_file(
            "https://example.com/x.csv", str(tmp_path / "x.csv"),
        )

    assert excinfo.value.response.status_code == 404
    assert len(calls) == 1  # 4xx no se reintenta


def test_download_stream_to_file_retries_429_with_retry_after(tmp_path, monkeypatch):
    """429 → reintento respetando Retry-After; el segundo GET completa."""
    calls = _patch_get(monkeypatch, [
        _FakeResponse(status_code=429, headers={"Retry-After": "2"}),
        _FakeResponse(chunks=[b"ok"]),
    ])
    sleeps: list[float] = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))

    path = base.download_stream_to_file(
        "https://example.com/x.csv", str(tmp_path / "x.csv"),
    )

    assert len(calls) == 2
    assert Path(path).read_bytes() == b"ok"
    assert sleeps == [2.0]  # Retry-After respetado


def test_download_stream_to_file_closes_response_on_mid_stream_error(
    tmp_path, monkeypatch,
):
    """Excepción a mitad de stream → la respuesta se cierra SIEMPRE (sin retry)."""
    resp = _FakeResponse(
        chunks=[b"partial"], exc=requests.ConnectionError("mid-stream"),
    )
    monkeypatch.setattr(base.requests, "get", lambda url, **kwargs: resp)

    with pytest.raises(requests.ConnectionError):
        base.download_stream_to_file(
            "https://example.com/x.csv", str(tmp_path / "x.csv"),
        )

    assert resp.closed is True
    # El archivo parcial queda escrito; el fallo a mitad de stream NO se reintenta.
    assert Path(tmp_path / "x.csv").read_bytes() == b"partial"
