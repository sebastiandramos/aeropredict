"""EUROCONTROL PRU (Performance Review Unit) — CSVs anuales de tráfico y retrasos.

Fuente: https://www.eurocontrol.int/performance/data/download/csv/ — CSVs
anuales públicos (sin API key). No usa BaseAdapter: las descargas siguen el
patrón stream de ``download_aircraft_csv`` (requests ``stream=True``,
``timeout=300``, chunks de 1 MB). El CSV ``apt_dly`` se distribuye comprimido
con bzip2; se descomprime con el stdlib ``bz2`` en UTF-8 (nunca cp1252) y el
binario ``.bz2`` no se persiste.
"""

from __future__ import annotations

import bz2
import logging
import os
from typing import Any

import requests

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

# Tamaño máximo de chunk para descarga (1 MB), igual que aircraft_db.
CHUNK_SIZE = 1024 * 1024


def pru_url(filename: str, year: int) -> str:
    """URL del CSV anual PRU para un nombre lógico y un año."""
    template = PRU_FILE_TEMPLATES[filename]
    return f"{PRU_BASE_URL}/{template.format(year=year)}"


def download_to_file(url: str, dest_path: str) -> str:
    """Descarga ``url`` a ``dest_path`` con el patrón stream de 1 MB.

    Args:
        url: URL de descarga.
        dest_path: Ruta de destino.

    Returns:
        Ruta absoluta al archivo descargado.

    Raises:
        requests.RequestException: Si falla la descarga (HTTP 404 incluido).
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    logger.info("Descargando %s ...", url)
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()

    total = 0
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                f.write(chunk)
                total += len(chunk)

    logger.info("Descargados %d MB → %s", total // (1024 * 1024), dest_path)
    return os.path.abspath(dest_path)


def _decompress_bz2(path: str) -> str:
    """Descomprime un CSV ``.bz2`` a texto UTF-8 (stdlib ``bz2``).

    Escribe el CSV plano junto al binario y elimina el ``.bz2`` (no se
    persiste). Decodifica como UTF-8 con ``errors="replace"``: la fuente real
    (verificado en vivo 2026-08-03, ``apt_dly_2025.csv.bz2``) es UTF-8 con
    bytes latinos sueltos que rompen la decodificación estricta; los bytes
    inválidos se reemplazan por U+FFFD y se registran como warning.

    Args:
        path: Ruta al archivo ``.bz2``.

    Returns:
        Ruta del CSV plano equivalente.

    Raises:
        OSError: Si el archivo bz2 está corrupto.
    """
    dest_path = path.removesuffix(".bz2")
    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as src:
        text = src.read()
    if "\ufffd" in text:
        logger.warning(
            "PRU apt_dly: %d byte(s) no UTF-8 reemplazados por U+FFFD "
            "(calidad de datos de la fuente)", text.count("\ufffd"),
        )
    with open(dest_path, "w", encoding="utf-8", newline="") as dst:
        dst.write(text)
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
