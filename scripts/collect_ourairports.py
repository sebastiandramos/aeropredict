"""Script: OurAirports (airports.csv + runways.csv) → Bronze + MongoDB.

Descarga los dos CSVs públicos de OurAirports y los persiste en Bronze
(Delta Lake) con la convención de metadatos de storage.py:

- ``bronze/ourairports_airports``: snapshot (overwrite) + append, CSV crudo.
- ``bronze/ourairports_runways``: snapshot (overwrite) + append, CSV crudo.

Además normaliza los registros y los upserta en MongoDB (colecciones
``airports`` / ``runways``) vía ``write_airports`` / ``write_runways``.
Si MongoDB no está disponible, se registra un warning y el run continúa:
el Bronze (destino primario) ya se ha escrito.

Skip-si-vacío obligatorio: si una descarga falla o viene vacía, no se
escribe nada para ese dataset y el snapshot bueno previo queda intacto.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from typing import Any

from aeropredict.opensky.config import get_delta_root
from aeropredict.opensky.storage import write_raw_csv, write_raw_snapshot
from aeropredict.opensky.storage_silver import write_airports, write_runways
from aeropredict.sources.ourairports import (
    download_ourairports,
    ourairports_url,
    parse_airports_csv,
    parse_runways_csv,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TABLE_AIRPORTS = "ourairports_airports"
TABLE_RUNWAYS = "ourairports_runways"

# Nombre lógico -> (tabla Delta, nombre del parser, nombre del writer Mongo).
# Los nombres se resuelven en runtime (_resolve) para que los tests puedan
# monkeypatchar parser y writer, y para que el fallo graceful de Mongo
# funcione sobre la función realmente importada.
_DATASETS: dict[str, tuple[str, str, str]] = {
    "airports": (TABLE_AIRPORTS, "parse_airports_csv", "write_airports"),
    "runways": (TABLE_RUNWAYS, "parse_runways_csv", "write_runways"),
}


def _resolve(name: str) -> Any:
    """Resuelve parser/writer en runtime (monkeypatch-friendly).

    Referencia los nombres importados directamente (evita F401) pero la
    resolución ocurre en cada llamada: un monkeypatch sobre el módulo
    (``module.write_airports``) sí tiene efecto.
    """
    if name == "parse_airports_csv":
        return parse_airports_csv
    if name == "parse_runways_csv":
        return parse_runways_csv
    if name == "write_airports":
        return write_airports
    if name == "write_runways":
        return write_runways
    raise ValueError(f"Nombre no resoluble: {name}")


def _log_download_error(entry: dict[str, Any]) -> None:
    """Registra el error de descarga; 404 se reporta como posible URL drift."""
    if entry.get("status_code") == 404:
        logger.error(
            "URL drift: OurAirports %s devuelve HTTP 404 (%s). "
            "Revisar las URLs en aeropredict.sources.ourairports",
            entry["filename"], entry["url"],
        )
    else:
        logger.warning("OurAirports %s: %s", entry["filename"], entry["error"])


def collect_ourairports(
    dry_run: bool = False, delta_root: str = "data/raw",
) -> dict[str, int]:
    """Recolecta airports.csv + runways.csv → Bronze y MongoDB.

    Un run descarga los 2 CSVs y escribe cada dataset en su propia tabla:
    ``write_raw_snapshot`` (overwrite) PRIMERO y ``write_raw_csv`` (append)
    después — el overwrite inicial limpia la tabla y el append la reconstruye,
    por lo que el nº de filas es estable entre runs (idempotente). El CSV
    crudo viaja tal cual (BOM/CRLF intactos, sin json.dumps); la
    normalización solo se usa para MongoDB.

    Si una descarga falla o viene vacía, ese dataset se salta (skip) y el
    snapshot bueno previo queda intacto. Si MongoDB no está disponible, se
    registra un warning y el run continúa (el Bronze ya está escrito).

    Args:
        dry_run: Solo mostrar lo que haría (sin descargas ni escrituras).
        delta_root: Ruta base Delta.

    Returns:
        Stats: total, written, skipped, errors, airports, runways,
        mongo_written.
    """
    stats = {
        "total": len(_DATASETS),
        "written": 0,
        "skipped": 0,
        "errors": 0,
        "airports": 0,
        "runways": 0,
        "mongo_written": 0,
    }

    if dry_run:
        for filename in _DATASETS:
            logger.info(
                "Descargaría %s → write para %s (%s)",
                ourairports_url(filename), filename, _DATASETS[filename][0],
            )
        return stats

    dest_dir = tempfile.mkdtemp(prefix="ourairports_")
    try:
        entries = download_ourairports(dest_dir)

        # Pass 1: descargas → texto + registros normalizados (None si error o
        # vacío). El parseo falla el dataset con error claro (CSV sin cabecera)
        # sin abortar el run.
        texts: dict[str, str | None] = {}
        parsed: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            filename = entry["filename"]
            table, parser_name, _writer_name = _DATASETS[filename]
            parser = _resolve(parser_name)
            if "error" in entry:
                _log_download_error(entry)
                stats["errors"] += 1
                texts[filename] = None
                continue
            # newline="" para conservar CRLF exactos (el texto llega a
            # write_raw_csv tal cual, BOM incluido).
            with open(entry["path"], encoding="utf-8", newline="") as f:
                text = f.read()
            if not text.strip():
                logger.info("OurAirports %s: contenido vacío (skip)", filename)
                stats["skipped"] += 1
                texts[filename] = None
                continue
            try:
                records = parser(text)
            except ValueError as exc:
                logger.error("OurAirports %s: %s (no se escribe en Bronze)", filename, exc)
                stats["errors"] += 1
                texts[filename] = None
                continue
            texts[filename] = text
            parsed[filename] = records
            stats[filename] = len(records)
            logger.info(
                "OurAirports %s: %d registros (%d bytes) → %s",
                filename, len(records), len(text.encode("utf-8")), entry["url"],
            )

        # Pass 2: snapshot (overwrite) PRIMERO y csv (append) después, por
        # tabla; así el run es idempotente (ver docstring).
        for filename, text in texts.items():
            if text is None:
                continue
            table = _DATASETS[filename][0]
            params: dict[str, Any] = {"dataset": filename}
            write_raw_snapshot(table, ourairports_url(filename), params, text, delta_root)
            write_raw_csv(table, ourairports_url(filename), params, text, delta_root)
            stats["written"] += 1

        # Pass 3: MongoDB (best-effort). Un Mongo caído/inalcanzable registra
        # un warning y NO falla el run: el Bronze ya está escrito.
        mongo_total = 0
        for filename, records in parsed.items():
            if not records:
                continue
            mongo_writer = _resolve(_DATASETS[filename][2])
            try:
                mongo_total += mongo_writer(records)
            except Exception as exc:
                logger.warning(
                    "Mongo %s no disponible (%s) — Bronze ya escrito, run continúa",
                    filename, exc,
                )
        stats["mongo_written"] = mongo_total
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Colección de OurAirports (airports.csv + runways.csv)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostrar qué se descargaría sin descargar ni escribir",
    )
    args = parser.parse_args(argv)

    stats = collect_ourairports(
        dry_run=args.dry_run, delta_root=get_delta_root(),
    )

    logger.info("--- Resultados ---")
    logger.info(
        "Total: %d | Escritos: %d | Saltados: %d | Errores: %d | "
        "Aeropuertos: %d | Pistas: %d | Mongo: %d",
        stats["total"], stats["written"], stats["skipped"], stats["errors"],
        stats["airports"], stats["runways"], stats["mongo_written"],
    )

    failed = stats["errors"]
    total = stats["total"]
    if failed and failed >= total:
        logger.error("Todos los datasets fallaron: collector run failed")
        return 1
    if failed:
        logger.warning("Fallo parcial: %d/%d — el run continúa", failed, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
