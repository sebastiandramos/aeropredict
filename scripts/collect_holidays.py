"""Collector de festivos públicos de España → Bronze (Delta Lake).

Dos fuentes complementarias:
  - Nager.Date (https://date.nager.at): fechas NOMINALES oficiales por año.
  - Paquete ``holidays`` (offline): fechas EFECTIVAS por subdivisión.

Tablas Bronze:
  - ``bronze/holidays_nager_date`` — CSV crudo vía ``write_raw_csv``
    (append + dedup por endpoint/params).
  - ``bronze/holidays_python`` — snapshot JSON vía ``write_raw_snapshot``
    (overwrite: la tabla contiene solo el último estado).
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
from datetime import date
from typing import Any

from aeropredict.opensky.config import get_delta_root
from aeropredict.opensky.storage import table_row_exists, write_raw_csv, write_raw_snapshot
from aeropredict.sources.nager import NagerAdapter
from aeropredict.sources.python_holidays import PythonHolidaysAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

NAGER_SOURCE = "holidays_nager_date"
PYTHON_SOURCE = "holidays_python"
NAGER_BASE_URL = "https://date.nager.at/api/v4/Holidays"
COUNTRY = "ES"

CSV_HEADER = ["date", "localName", "name", "countryCode", "global", "counties", "types"]


def _holidays_to_csv(raw: list[dict[str, Any]]) -> str:
    """Serializa la lista de festivos de Nager.Date a texto CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for holiday in raw:
        writer.writerow([
            holiday.get("date", ""),
            holiday.get("localName", ""),
            holiday.get("name", ""),
            holiday.get("countryCode", ""),
            "true" if holiday.get("global") else "false",
            "|".join(holiday.get("counties") or []),
            "|".join(holiday.get("types") or []),
        ])
    return buffer.getvalue()


def collect_holidays(
    year: int | None = None,
    days: int = 1,
    dry_run: bool = False,
    delta_root: str = "data/raw",
    force: bool = False,
) -> dict[str, int]:
    """Recolecta festivos de España (Nager.Date + python-holidays) a Bronze.

    Args:
        year: Año final del rango (default: año actual).
        days: Años hacia atrás desde ``year`` (inclusive, default 1).
        dry_run: Solo loguear lo que haría, sin llamadas ni escrituras.
        delta_root: Ruta base de datos Delta.
        force: Re-escribir Nager aunque la fila (endpoint, params) ya exista.

    Returns:
        Stats: total, nager_written, nager_skipped, python_written, errors.
    """
    end_year = year or date.today().year
    years = list(range(end_year - days + 1, end_year + 1))

    stats = {
        "total": len(years) * 2,
        "nager_written": 0,
        "nager_skipped": 0,
        "python_written": 0,
        "errors": 0,
    }

    nager = NagerAdapter()
    python_holidays = PythonHolidaysAdapter()

    for current_year in years:
        # --- Nager.Date (fechas nominales, append + dedup) ---
        nager_endpoint = f"{NAGER_BASE_URL}/{COUNTRY}/{current_year}"
        nager_params = {"country": COUNTRY, "year": current_year}

        if dry_run:
            logger.info("  Nager: consultaría %s", nager_endpoint)
        elif not force and table_row_exists(delta_root, NAGER_SOURCE, nager_endpoint, nager_params):
            logger.info("  Nager %s: ya existe en Bronze (skip)", current_year)
            stats["nager_skipped"] += 1
        else:
            data = nager.get_holidays(country=COUNTRY, year=current_year)
            if data is None or not data["raw"]:
                logger.warning("  Nager %s: respuesta vacía, saltando", current_year)
                stats["errors"] += 1
            else:
                write_raw_csv(
                    NAGER_SOURCE,
                    nager_endpoint,
                    nager_params,
                    _holidays_to_csv(data["raw"]),
                    delta_root,
                )
                logger.info(
                    "  Nager %s: %d festivos guardados en Bronze",
                    current_year, data["count"],
                )
                stats["nager_written"] += 1

        # --- python-holidays (fechas efectivas, snapshot overwrite) ---
        python_endpoint = f"python_holidays://{COUNTRY}/{current_year}"
        python_params = {"country": COUNTRY, "year": current_year, "subdivisions": "all"}

        if dry_run:
            logger.info("  python-holidays: consultaría %s", python_endpoint)
        else:
            data = python_holidays.get_holidays(country=COUNTRY, year=current_year)
            if data is None or data["count"] == 0:
                logger.warning("  python-holidays %s: respuesta vacía, saltando", current_year)
                stats["errors"] += 1
            else:
                write_raw_snapshot(PYTHON_SOURCE, python_endpoint, python_params, data, delta_root)
                logger.info(
                    "  python-holidays %s: %d festivos (snapshot)",
                    current_year, data["count"],
                )
                stats["python_written"] += 1

    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Argumentos de línea de comandos del collector de festivos."""
    parser = argparse.ArgumentParser(description="Colección de festivos públicos de España")
    parser.add_argument("--year", type=int, default=None,
                        help="Año final del rango (default: año actual)")
    parser.add_argument("--days", type=int, default=1,
                        help="Años hacia atrás desde --year, inclusive (default: 1)")
    parser.add_argument("--force", action="store_true",
                        help="Re-escribir Nager aunque la fila ya exista en Bronze")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar lo que haría, sin red ni escrituras")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: exit 0 si todo ok o fallo parcial, 1 si fallan todas."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)
    stats = collect_holidays(
        year=args.year,
        days=args.days,
        dry_run=args.dry_run,
        delta_root=get_delta_root(),
        force=args.force,
    )
    logger.info(
        "Festivos: total=%d | Nager escritos=%d | Nager saltados=%d | "
        "python escritos=%d | errores=%d",
        stats["total"], stats["nager_written"], stats["nager_skipped"],
        stats["python_written"], stats["errors"],
    )
    if args.dry_run:
        logger.info("Dry run completo — no se hicieron llamadas ni escrituras")

    failed = stats["errors"]
    total = stats["total"]
    if failed and failed >= total:
        logger.error("Fallaron todas las %d fuentes — run fallido", total)
        return 1
    if failed:
        logger.warning("Fallo parcial: %d/%d — run continúa", failed, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
