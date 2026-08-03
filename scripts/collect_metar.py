"""Script: NOAA AWC METAR → Bronze (Delta Lake).

Recolecta los informes METAR de los aeropuertos configurados (NOAA Aviation
Weather Center, sin API key) y los persiste como CSV aplanado en la tabla
``bronze/metar_awc`` (append + dedup por (endpoint, params)).
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from io import StringIO
from typing import Any

from aeropredict.opensky.config import get_airport_icao_codes, get_delta_root
from aeropredict.opensky.storage import table_row_exists, write_raw_csv
from aeropredict.sources.metar import CSV_FIELDS, METAR_URL, MetarAWCAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TABLE_NAME = "metar_awc"
DEFAULT_DAYS = 2


def _select_codes(airport: str | None) -> list[str]:
    """Códigos ICAO a consultar: todos los AEROPUERTOS o uno específico."""
    if airport:
        return [airport.upper()]
    return get_airport_icao_codes()


def _reports_to_csv(reports: list[dict[str, Any]]) -> str:
    """Convierte informes aplanados en texto CSV (header + una fila por informe)."""
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for report in reports:
        writer.writerow({
            field: (report[field] if report[field] is not None else "")
            for field in CSV_FIELDS
        })
    return buffer.getvalue()


def collect_metar(
    airport: str | None = None,
    days_back: int = DEFAULT_DAYS,
    dry_run: bool = False,
    delta_root: str = "data/raw",
    force: bool = False,
) -> dict[str, int]:
    """Recolecta METAR de la NOAA AWC y los persiste en ``bronze/metar_awc``.

    Un run = una fila Delta con params ``{"ids": ..., "hours": ...}``
    (la ventana ``hours`` = días x 24). Idempotente: si la fila (endpoint,
    params) ya existe se salta; ``force`` la reescribe. Respuestas vacías
    (HTTP 204 -> ``{}``) se registran como skip sin escribir.

    Args:
        airport: Código ICAO específico (None = todos los AEROPUERTOS).
        days_back: Ventana hacia atrás en días.
        dry_run: Solo mostrar lo que haría.
        delta_root: Ruta base Delta.
        force: Ignorar el dedup por (endpoint, params).

    Returns:
        Stats: total, metar_written, skipped, errors.
    """
    codes = _select_codes(airport)
    if not codes:
        logger.warning("Sin códigos ICAO que consultar")
        return {"total": 0, "metar_written": 0, "skipped": 0, "errors": 0}

    hours = max(1, days_back * 24)
    params: dict[str, Any] = {"ids": ",".join(codes), "hours": hours}
    total = 1

    if dry_run:
        logger.info(
            "Consultaría METAR de %d aeropuertos (%s, hours=%d)",
            len(codes), params["ids"], hours,
        )
        return {"total": total, "metar_written": 0, "skipped": 0, "errors": 0}

    if not force and table_row_exists(delta_root, TABLE_NAME, METAR_URL, params):
        logger.info("METAR ya existe para %s (skip)", params["ids"])
        return {"total": total, "metar_written": 0, "skipped": 1, "errors": 0}

    try:
        data = MetarAWCAdapter().get_metars(codes, hours=hours)
    except Exception as e:
        logger.warning("METAR error: %s", e)
        return {"total": total, "metar_written": 0, "skipped": 0, "errors": 1}

    if data is None or data.get("count", 0) == 0:
        logger.info("Sin METAR disponibles para %s (skip)", params["ids"])
        return {"total": total, "metar_written": 0, "skipped": 1, "errors": 0}

    csv_text = _reports_to_csv(data["raw"])
    write_raw_csv(TABLE_NAME, METAR_URL, params, csv_text, delta_root)
    logger.info("METAR guardado en Bronze: %d informes", data["count"])

    return {"total": total, "metar_written": 1, "skipped": 0, "errors": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Colección de METAR (NOAA Aviation Weather Center)",
    )
    parser.add_argument("--airport", default=None, help="Código ICAO específico")
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help="Ventana hacia atrás en días (-> hours = días x 24)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force", action="store_true",
        help="Reescribir aunque (endpoint, params) ya exista en Bronze",
    )
    args = parser.parse_args(argv)

    stats = collect_metar(
        airport=args.airport,
        days_back=args.days,
        dry_run=args.dry_run,
        delta_root=get_delta_root(),
        force=args.force,
    )

    logger.info("--- Resultados ---")
    logger.info(
        "Total: %d | METAR escrito: %d | Saltados: %d | Errores: %d",
        stats["total"], stats["metar_written"], stats["skipped"], stats["errors"],
    )

    failed = stats["errors"]
    total = stats["total"]
    if failed and failed >= total:
        logger.error("Todos los lotes fallaron: collector run failed")
        return 1
    if failed:
        logger.warning("Fallo parcial: %d/%d — el run continúa", failed, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
