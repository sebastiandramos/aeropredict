"""OpenSky aircraft database handler.

Descarga el CSV público de la base de datos de aeronaves de OpenSky
y lo importa a MongoDB.

URL de descarga:
  https://opensky-network.org/datasets/metadata/
  aircraft-database-comprehensive-2025-09.csv

Documentación:
  https://openskynetwork.github.io/opensky-api/rest.html#aircraft-database
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Última URL conocida (complete-2024-06 funciona, comprehensive-2025-09 404)
# Revisar en https://opensky-network.org/datasets/metadata/
DEFAULT_DOWNLOAD_URL = (
    "https://opensky-network.org/datasets/metadata/"
    "aircraft-database-complete-2024-06.csv"
)

# Tamaño máximo de chunk para descarga (1 MB)
CHUNK_SIZE = 1024 * 1024


def download_aircraft_csv(
    url: str = DEFAULT_DOWNLOAD_URL,
    dest_path: str = "data/aircraft_db.csv",
) -> str:
    """Descarga el CSV de aeronaves de OpenSky.

    Args:
        url: URL de descarga del CSV.
        dest_path: Ruta de destino.

    Returns:
        Ruta absoluta al archivo descargado.

    Raises:
        requests.RequestException: Si falla la descarga.
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    logger.info("Descargando aircraft DB desde %s ...", url)
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


def parse_aircraft_csv(path: str) -> list[dict[str, Any]]:
    """Parsea el CSV de aeronaves y devuelve una lista de dicts normalizados.

    El CSV de OpenSky tiene encoding latin1 y BOM.

    Args:
        path: Ruta al archivo CSV.

    Returns:
        Lista de dicts con campos limpios por aeronave.
    """
    records: list[dict[str, Any]] = []

    with open(path, encoding="latin1") as f:
        # Saltar posibles líneas de cabecera antes del CSV real
        reader = csv.DictReader(f)
        # OpenSky cambia entre camelCase (complete-2024) y lowercase (comprehensive-2025)
        # El CSV v2024 usa comillas simples: "'campo'" → limpiamos de ambas
        # Normalizamos: lowercase keys + strip quotes de keys y values
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            cleaned: dict[str, str] = {}
            for k, v in raw_row.items():
                key = k.strip().strip("'").strip('"').lower() if k else ""
                # csv.DictReader con restkey/restval puede devolver list
                val_str = str(v) if v is not None else ""
                val = val_str.strip().strip("'").strip('"')
                cleaned[key] = val
            rows.append(cleaned)

        for row in rows:
            icao24 = _clean(row.get("icao24", ""))
            if not icao24:
                continue

            record = {
                "icao24": icao24.lower(),
                "registration": _clean(row.get("registration", "")),
                "manufacturer": _clean(row.get("manufacturername", "")),
                "model": _clean(row.get("model", "")),
                "typecode": _clean(row.get("typecode", "")),
                "serial_number": _clean(row.get("serialnumber", "")),
                "line_number": _clean(row.get("linenumber", "")),
                "icao_aircraft_type": _clean(row.get("icaoaircraftclass", "")),
                "operator": _clean(row.get("operator", "")),
                "operator_callsign": _clean(row.get("operatorcallsign", "")),
                "operator_icao": _clean(row.get("operatoricao", "")),
                "operator_iata": _clean(row.get("operatoriata", "")),
                "first_flight_date": _clean(row.get("firstflightdate", "")),
            }
            records.append(record)

    logger.info("CSV parseado: %d registros", len(records))
    return records


def import_aircraft_to_mongodb(path: str) -> int:
    """Parsea CSV e importa a MongoDB (colección ``aircraft``).

    Args:
        path: Ruta al archivo CSV.

    Returns:
        Número de registros importados.
    """
    from aeropredict.opensky.storage_silver import write_aircraft

    records = parse_aircraft_csv(path)
    if not records:
        logger.warning("CSV vacío: %s", path)
        return 0

    return write_aircraft(records)


def _clean(value: str | None) -> str:
    """Limpia un valor CSV: elimina espacios y convierte None → \"\"."""
    if value is None:
        return ""
    return value.strip().strip('"')
