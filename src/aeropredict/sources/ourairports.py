"""OurAirports — datos públicos de aeropuertos y pistas (CSV nocturno).

Fuente: https://davidmegginson.github.io/ourairports-data/ — dos CSVs
públicos (sin API key, licencia Unlicense):
  - ``airports.csv`` (~12,7 MB): todos los aeropuertos del mundo.
  - ``runways.csv`` (~4 MB): todas las pistas.

No usa BaseAdapter: las descargas siguen el patrón stream de
``download_aircraft_csv`` (requests ``stream=True``, ``timeout=300``,
chunks de 1 MB). El parseo usa ``csv.DictReader`` (UTF-8 con cabecera).

Gap conocido (ver plan): OurAirports no publica variación magnética; los
headings de pista (``le_heading_degT``/``he_heading_degT``) ya vienen en
grados verdaderos y se conservan tal cual. El CSV tampoco tiene columna
``icao_code``: el código ICAO se toma de ``gps_code`` (que es el ICAO para
la práctica totalidad de aeropuertos; vacío en los que no lo tienen).
"""

from __future__ import annotations

import csv
import io
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

OURAIRPORTS_BASE_URL = "https://davidmegginson.github.io/ourairports-data"

# Nombre lógico -> nombre de archivo en OurAirports. El orden define el par
# de datasets que el collector descarga y normaliza.
OURAIRPORTS_FILES: dict[str, str] = {
    "airports": "airports.csv",
    "runways": "runways.csv",
}

# Campos normalizados del plan (todo 6). El resto de columnas del CSV
# (id, continent, keywords, ...) se descartan en la normalización.
AIRPORT_FIELDS: list[str] = [
    "ident",
    "type",
    "name",
    "latitude_deg",
    "longitude_deg",
    "elevation_ft",
    "iso_country",
    "iso_region",
    "municipality",
    "iata_code",
    "icao_code",
]

RUNWAY_FIELDS: list[str] = [
    "airport_ident",
    "length_ft",
    "width_ft",
    "surface",
    "le_ident",
    "he_ident",
    "le_heading_degT",
    "he_heading_degT",
]

# Tamaño máximo de chunk para descarga (1 MB), igual que aircraft_db.
CHUNK_SIZE = 1024 * 1024


def ourairports_url(filename: str) -> str:
    """URL del CSV de OurAirports para un nombre lógico."""
    return f"{OURAIRPORTS_BASE_URL}/{OURAIRPORTS_FILES[filename]}"


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


def download_ourairports(
    dest_dir: str, files: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Descarga los CSVs de OurAirports solicitados.

    Args:
        dest_dir: Directorio de descarga.
        files: Nombres lógicos a descargar (default: los 2 de
            ``OURAIRPORTS_FILES``).

    Returns:
        Lista con un dict por archivo. Éxito: ``{"filename", "url", "path"}``
        con la ruta del CSV. Fallo: ``{"filename", "url", "error",
        "status_code"}``. Un archivo que falle no aborta el resto.
    """
    entries: list[dict[str, Any]] = []
    for filename in files or list(OURAIRPORTS_FILES):
        url = ourairports_url(filename)
        dest_path = os.path.join(dest_dir, OURAIRPORTS_FILES[filename])
        try:
            path = download_to_file(url, dest_path)
            entries.append({"filename": filename, "url": url, "path": path})
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            logger.warning("OurAirports %s: HTTP %s (%s)", filename, status, url)
            entries.append({
                "filename": filename, "url": url,
                "error": str(exc), "status_code": status,
            })
        except Exception as exc:
            logger.warning("OurAirports %s: error (%s)", filename, exc)
            entries.append({"filename": filename, "url": url, "error": str(exc)})
    return entries


def _clean(value: str | None) -> str:
    """Limpia un valor CSV: elimina espacios y convierte None → \"\"."""
    if value is None:
        return ""
    return value.strip().strip('"')


def _reader(text: str) -> csv.DictReader[str]:
    """DictReader UTF-8 sobre el texto, tolerante a BOM inicial."""
    return csv.DictReader(io.StringIO(text.lstrip("\ufeff")))


def parse_airports_csv(text: str) -> list[dict[str, Any]]:
    """Parsea el CSV de aeropuertos y devuelve dicts normalizados.

    Normaliza SOLO los 11 campos del plan (``AIRPORT_FIELDS``); el resto de
    columnas del CSV se descarta. La columna ``icao_code`` no existe en
    OurAirports: se rellena con ``gps_code`` (el código ICAO real para la
    mayoría de aeropuertos; ``""`` cuando no lo tienen). Las filas sin
    ``ident`` se omiten.

    Args:
        text: Contenido CSV crudo (UTF-8 con cabecera).

    Returns:
        Lista de dicts normalizados por aeropuerto.

    Raises:
        ValueError: Si el CSV no tiene cabecera con la columna ``ident``.
    """
    reader = _reader(text)
    if reader.fieldnames is None or "ident" not in reader.fieldnames:
        raise ValueError(
            "airports.csv sin cabecera válida: falta la columna 'ident'"
        )

    records: list[dict[str, Any]] = []
    for row in reader:
        ident = _clean(row.get("ident", ""))
        if not ident:
            continue
        records.append({
            "ident": ident,
            "type": _clean(row.get("type", "")),
            "name": _clean(row.get("name", "")),
            "latitude_deg": _clean(row.get("latitude_deg", "")),
            "longitude_deg": _clean(row.get("longitude_deg", "")),
            "elevation_ft": _clean(row.get("elevation_ft", "")),
            "iso_country": _clean(row.get("iso_country", "")),
            "iso_region": _clean(row.get("iso_region", "")),
            "municipality": _clean(row.get("municipality", "")),
            "iata_code": _clean(row.get("iata_code", "")),
            "icao_code": _clean(row.get("gps_code", "")),
        })
    logger.info("airports.csv parseado: %d registros", len(records))
    return records


def parse_runways_csv(text: str) -> list[dict[str, Any]]:
    """Parsea el CSV de pistas y devuelve dicts normalizados.

    Normaliza SOLO los 8 campos del plan (``RUNWAY_FIELDS``). Las filas sin
    ``airport_ident`` se omiten.

    Args:
        text: Contenido CSV crudo (UTF-8 con cabecera).

    Returns:
        Lista de dicts normalizados por pista.

    Raises:
        ValueError: Si el CSV no tiene cabecera con la columna
            ``airport_ident``.
    """
    reader = _reader(text)
    if reader.fieldnames is None or "airport_ident" not in reader.fieldnames:
        raise ValueError(
            "runways.csv sin cabecera válida: falta la columna 'airport_ident'"
        )

    records: list[dict[str, Any]] = []
    for row in reader:
        airport_ident = _clean(row.get("airport_ident", ""))
        if not airport_ident:
            continue
        records.append({
            "airport_ident": airport_ident,
            "length_ft": _clean(row.get("length_ft", "")),
            "width_ft": _clean(row.get("width_ft", "")),
            "surface": _clean(row.get("surface", "")),
            "le_ident": _clean(row.get("le_ident", "")),
            "he_ident": _clean(row.get("he_ident", "")),
            "le_heading_degT": _clean(row.get("le_heading_degT", "")),
            "he_heading_degT": _clean(row.get("he_heading_degT", "")),
        })
    logger.info("runways.csv parseado: %d registros", len(records))
    return records
