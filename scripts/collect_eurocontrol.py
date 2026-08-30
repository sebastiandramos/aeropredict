"""Script: EUROCONTROL PRU CSVs → Bronze (Delta Lake).

Recolecta los 3 CSVs anuales del Performance Review Unit (PRU) de
EUROCONTROL (tráfico por aeropuerto, retrasos por aeropuerto y
pre-departure delays) y los persiste en ``bronze/eurocontrol_pru``:

- ``airport_traffic_{year}.csv`` y ``apt_dly_{year}.csv`` (descomprimido)
  → ``write_raw_csv`` (append, params ``{"filename", "year"}``).
- ``all_pre_departure_delays_{year}.csv`` (último CSV del juego anual)
  → ``write_raw_snapshot`` (overwrite): la tabla representa el último juego
  anual; un año nuevo reemplaza al anterior.

Skip-si-vacío obligatorio: si una descarga falla (p. ej. 404 de año futuro,
"URL drift") o viene vacía, no se escribe nada para ese archivo y el
snapshot bueno previo queda intacto.
"""

from __future__ import annotations

import argparse
import csv
import logging
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from aeropredict.opensky.config import get_delta_root
from aeropredict.opensky.storage import write_raw_csv, write_raw_snapshot
from aeropredict.sources.eurocontrol import (
    PRU_FILE_TEMPLATES,
    download_pru_csvs,
    pru_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TABLE_NAME = "eurocontrol_pru"

# Último CSV del juego anual → snapshot (overwrite). La tabla queda con el
# último juego anual: los 2 primeros en append + este en overwrite.
SNAPSHOT_FILENAME = "all_pre_departure_delays"


def _csv_stats(text: str) -> tuple[int, int]:
    """Bytes (UTF-8) y número de filas CSV (header incluido) de un texto."""
    data = text.encode("utf-8")
    rows = sum(1 for _ in csv.reader(StringIO(text)))
    return len(data), rows


def _log_error(entry: dict[str, Any]) -> None:
    """Registra el error de descarga; 404 se reporta como posible URL drift."""
    if entry.get("status_code") == 404:
        logger.error(
            "URL drift: PRU %s devuelve HTTP 404 (%s). "
            "Revisar las URLs en aeropredict.sources.eurocontrol",
            entry["filename"], entry["url"],
        )
    else:
        logger.warning("PRU %s: %s", entry["filename"], entry["error"])


def collect_eurocontrol(
    year: int, dry_run: bool = False, delta_root: str = "data/raw",
) -> dict[str, int]:
    """Recolecta los CSVs anuales PRU de EUROCONTROL en ``bronze/eurocontrol_pru``.

    Un run descarga los 3 CSVs del año y los escribe con la convención de
    metadatos de storage.py (source/endpoint/params/response/fetched_at):
    los 2 primeros vía ``write_raw_csv`` y el último vía ``write_raw_snapshot``
    (overwrite + skip-si-vacío, para que un run fallido no destruya el
    snapshot bueno previo).

    Args:
        year: Año de los CSVs anuales.
        dry_run: Solo mostrar lo que haría (sin descargas ni escrituras).
        delta_root: Ruta base Delta.

    Returns:
        Stats: total, written, skipped, errors.
    """
    files = list(PRU_FILE_TEMPLATES)
    stats = {"total": len(files), "written": 0, "skipped": 0, "errors": 0}

    if dry_run:
        for filename in files:
            logger.info(
                "Descargaría %s → write para %s (%s)",
                pru_url(filename, year), filename, TABLE_NAME,
            )
        return stats

    dest_dir = tempfile.mkdtemp(prefix="eurocontrol_pru_")
    try:
        entries = download_pru_csvs(year, dest_dir, files)

        # Pass 1: descargas → texto (None si error o vacío).
        texts: dict[str, str | None] = {}
        for entry in entries:
            if "error" in entry:
                _log_error(entry)
                stats["errors"] += 1
                texts[entry["filename"]] = None
                continue
            # newline="" para conservar CRLF exactos (el texto llega a
            # write_raw_csv tal cual, BOM incluido).
            with open(entry["path"], encoding="utf-8", newline="") as f:
                text = f.read()
            if not text.strip():
                logger.info("PRU %s: contenido vacío (skip)", entry["filename"])
                stats["skipped"] += 1
                texts[entry["filename"]] = None
                continue
            data_bytes, rows = _csv_stats(text)
            logger.info(
                "PRU %s: %d bytes, %d filas → %s",
                entry["filename"], data_bytes, rows, entry["url"],
            )
            texts[entry["filename"]] = text

        # Guardia: si el snapshot del juego anual falla o viene vacío, no se
        # escribe nada → el snapshot bueno previo queda intacto.
        if texts[SNAPSHOT_FILENAME] is None:
            stats["skipped"] += sum(
                1 for f in files if f != SNAPSHOT_FILENAME and texts[f] is not None
            )
            logger.error(
                "PRU %s: snapshot inválido (error o vacío) — no se escribe nada, "
                "snapshot previo intacto", SNAPSHOT_FILENAME,
            )
            return stats

        # Pass 2: el snapshot (overwrite) PRIMERO y después los appends; así la
        # tabla queda con el último juego anual completo (un año nuevo reemplaza
        # al anterior en lugar de acumularse).
        for filename in [
            SNAPSHOT_FILENAME, *[f for f in files if f != SNAPSHOT_FILENAME],
        ]:
            text = texts[filename]
            if text is None:
                continue
            params: dict[str, Any] = {"filename": filename, "year": year}
            if filename == SNAPSHOT_FILENAME:
                write_raw_snapshot(
                    TABLE_NAME, pru_url(filename, year), params, text, delta_root,
                )
            else:
                write_raw_csv(TABLE_NAME, pru_url(filename, year), params, text, delta_root)
            stats["written"] += 1
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Colección de CSVs EUROCONTROL PRU (tráfico y retrasos anuales)",
    )
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Año de los CSVs anuales (default: año actual)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    stats = collect_eurocontrol(
        year=args.year, dry_run=args.dry_run, delta_root=get_delta_root(),
    )

    logger.info("--- Resultados ---")
    logger.info(
        "Total: %d | Escritos: %d | Saltados: %d | Errores: %d",
        stats["total"], stats["written"], stats["skipped"], stats["errors"],
    )

    failed = stats["errors"]
    total = stats["total"]
    if failed and failed >= total:
        logger.error("Todos los archivos fallaron: collector run failed")
        return 1
    if failed:
        logger.warning("Fallo parcial: %d/%d — el run continúa", failed, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
