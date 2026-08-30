"""EUROCONTROL PRU (Performance Review Unit) — CSVs anuales de tráfico y retrasos.

Fuente: https://www.eurocontrol.int/performance/data/download/csv/ — CSVs
anuales públicos (sin API key). Las descargas delegan en el helper compartido
``base.download_stream_to_file`` (requests ``stream=True``, ``timeout=300``,
chunks de 1 MB, reintentos en 429/5xx). El CSV ``apt_dly`` se distribuye
comprimido con bzip2; se descomprime con el stdlib ``bz2`` en UTF-8 (nunca
cp1252) y el binario ``.bz2`` no se persiste.
"""

from __future__ import annotations

import bz2
import logging
import os
import shutil
from typing import Any

import requests

from aeropredict.sources.base import download_stream_to_file

logger = logging.getLogger(__name__)

PRU_BASE_URL = "https://www.eurocontrol.int/performance/data/download/csv"

# Nombre lógico -> plantilla de URL anual. El orden define el juego anual:
# los dos primeros van a ``write_raw_csv`` (append) y el último (snapshot)
# a ``write_raw_snapshot`` (overwrite).
PRU_FILE_TEMPLATES: dict[str, str] = {
    "airport_traffic": "airport_traffic_{year}.csv",
    "apt_dly": "apt_dly_{year}.csv.bz2",
    "all_pre_departure_delays": "all_pre_departure_delays_{year}.csv",
}


def pru_url(filename: str, year: int) -> str:
    """URL del CSV anual PRU para un nombre lógico y un año."""
    template = PRU_FILE_TEMPLATES[filename]
    return f"{PRU_BASE_URL}/{template.format(year=year)}"


def download_to_file(url: str, dest_path: str) -> str:
    """Descarga ``url`` a ``dest_path`` (delegado a ``base.download_stream_to_file``).

    Args:
        url: URL de descarga.
        dest_path: Ruta de destino.

    Returns:
        Ruta absoluta al archivo descargado.

    Raises:
        requests.HTTPError: Si el servidor responde 4xx (404/401 incluidos)
            o si se agotan los reintentos en 5xx.
        requests.RequestException: Si fallan todos los reintentos (429
            persistente, timeout o error de conexión).
    """
    return download_stream_to_file(url, dest_path)


class _ReplacementCountingReader:
    """Reader de texto que cuenta los U+FFFD (bytes no UTF-8) al leer.

    Envuelve el stream de ``bz2.open(..., errors="replace")`` para que
    ``shutil.copyfileobj`` copie en streaming (64 KB) mientras se cuenta
    cuántos bytes inválidos se reemplazaron por U+FFFD.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self.replacement_count = 0

    def read(self, size: int = -1) -> str:
        data = self._stream.read(size)
        self.replacement_count += data.count("\ufffd")
        return data


def _decompress_bz2(path: str) -> str:
    """Descomprime un CSV ``.bz2`` a texto UTF-8 (stdlib ``bz2``).

    Escribe el CSV plano junto al binario y elimina el ``.bz2`` (no se
    persiste). Decodifica como UTF-8 con ``errors="replace"``: la fuente real
    (verificado en vivo 2026-08-03, ``apt_dly_2025.csv.bz2``) es UTF-8 con
    bytes latinos sueltos que rompen la decodificación estricta; los bytes
    inválidos se reemplazan por U+FFFD y se registran como warning. La copia
    es en streaming (``shutil.copyfileobj``, chunks de 64 KB): nunca se carga
    el archivo descomprimido entero en RAM.

    Args:
        path: Ruta al archivo ``.bz2``.

    Returns:
        Ruta del CSV plano equivalente.

    Raises:
        OSError: Si el archivo bz2 está corrupto.
    """
    dest_path = path.removesuffix(".bz2")
    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as src, \
            open(dest_path, "w", encoding="utf-8", newline="") as dst:
        reader = _ReplacementCountingReader(src)
        shutil.copyfileobj(reader, dst, length=64 * 1024)
    if reader.replacement_count:
        logger.warning(
            "PRU apt_dly: %d byte(s) no UTF-8 reemplazados por U+FFFD "
            "(calidad de datos de la fuente)", reader.replacement_count,
        )
    os.remove(path)
    return dest_path


def download_pru_csvs(
    year: int, dest_dir: str, files: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Descarga los CSVs anuales PRU solicitados.

    Args:
        year: Año de los CSVs (parametrizable con ``--year``).
        dest_dir: Directorio temporal de descarga.
        files: Nombres lógicos a descargar (default: los 3 de
            ``PRU_FILE_TEMPLATES``).

    Returns:
        Lista con un dict por archivo. Éxito: ``{"filename", "url", "path"}``
        con la ruta del CSV plano (el ``.bz2`` de ``apt_dly`` queda
        descomprimido). Fallo: ``{"filename", "url", "error", "status_code"}``.
        Un archivo que falle no aborta el resto.
    """
    entries: list[dict[str, Any]] = []
    for filename in files or list(PRU_FILE_TEMPLATES):
        url = pru_url(filename, year)
        try:
            if filename == "apt_dly":
                bz2_path = download_to_file(
                    url, os.path.join(dest_dir, f"apt_dly_{year}.csv.bz2"),
                )
                path = _decompress_bz2(bz2_path)
            else:
                path = download_to_file(
                    url, os.path.join(dest_dir, f"{filename}_{year}.csv"),
                )
            entries.append({"filename": filename, "url": url, "path": path})
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            logger.warning("PRU %s: HTTP %s (%s)", filename, status, url)
            entries.append({
                "filename": filename, "url": url,
                "error": str(exc), "status_code": status,
            })
        except Exception as exc:
            logger.warning("PRU %s: error (%s)", filename, exc)
            entries.append({"filename": filename, "url": url, "error": str(exc)})
    return entries
