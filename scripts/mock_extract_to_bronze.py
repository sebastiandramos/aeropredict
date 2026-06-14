#!/usr/bin/env python3
"""Mock de extract_to_bronze: lee JSON locales y escribe en Bronze (Delta Lake).

Útil para:
  - Testear el pipeline sin consumir créditos OpenSky
  - Desarrollo offline
  - Reproducir errores con datos conocidos

Estructura esperada de mock dir::

    {mock_dir}/opensky/{YYYY-MM-DD}/{ICAO}_{arrivals|departures}.json

Cada archivo JSON contiene la lista de vuelos que devolvería la API OpenSky.
Se genera con ``--generate-samples``, que samplea datos reales desde Bronze.

Uso:
    # Samplear datos reales desde Bronze como mock (últimos 2 días)
    python scripts/mock_extract_to_bronze.py --generate-samples --days 2

    # Procesar los datos mock → Bronze local
    python scripts/mock_extract_to_bronze.py

    # Dry-run
    python scripts/mock_extract_to_bronze.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa

from aeropredict.opensky.config import get_delta_root
from aeropredict.opensky.logging_config import setup_daily_logger
from aeropredict.opensky.storage import write_raw

logger = logging.getLogger("mock_extract_to_bronze")

MOCK_DIR_DEFAULT = Path("data/mock")

# ---------------------------------------------------------------------------
# Muestreo desde Bronze real (Delta Lake)
# ---------------------------------------------------------------------------


def generate_samples(mock_dir: Path, days: int = 1) -> None:
    """Extrae una muestra de datos reales desde Bronze (Delta Lake) y la guarda como mock.

    Lee los archivos parquet de ``bronze/opensky``, samplea vuelos por
    (aeropuerto, endpoint) y los escribe como JSON en
    ``mock_dir/opensky/{date}/{ICAO}_{arrivals|departures}.json``.

    Args:
        mock_dir: Directorio raíz para los JSON mock.
        days: Días hacia atrás a muestrear.
    """
    import pyarrow.parquet as pq

    bronze_path = Path(get_delta_root()) / "bronze" / "opensky"
    parquet_dirs = sorted(bronze_path.glob("ingestion_date=*"))

    if not parquet_dirs:
        logger.error("No se encuentra Bronze en %s. ¿Ejecutaste extract_to_bronze.py?", bronze_path)
        return

    now_utc = datetime.now(UTC)
    target_dates = {(now_utc - timedelta(days=d)).date() for d in range(1, days + 1)}
    logger.info("Sampleando desde %d particiones de Bronze...", len(parquet_dirs))

    # Agrupar vuelos por (aeropuerto, endpoint)
    all_flights: dict[tuple[str, str], list[dict]] = {}

    for pd_ in parquet_dirs:
        ing_date_str = pd_.name.split("=")[1]
        try:
            ing_date = datetime.strptime(ing_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if ing_date not in target_dates:
            continue

        parquet_files = list(pd_.glob("*.parquet"))
        for pf in parquet_files:
            table = pq.read_table(str(pf))
            for row in table.to_pylist():
                try:
                    params = json.loads(row["params"]) if row.get("params") else {}
                except (json.JSONDecodeError, TypeError):
                    continue
                airport = params.get("airport")
                endpoint = str(row.get("endpoint", "")).lstrip("/flights/")
                if not airport or endpoint not in ("arrivals", "departures"):
                    continue
                try:
                    flights = json.loads(row["response"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(flights, list):
                    continue
                key = (airport, endpoint)
                all_flights.setdefault(key, []).extend(flights)

    if not all_flights:
        logger.warning("No se extrajeron vuelos de Bronze.")
        return

    # Samplear hasta 200 vuelos por (aeropuerto, endpoint) y agrupar por fecha
    written_any = False
    for (airport, endpoint), flights in sorted(all_flights.items()):
        sampled = random.sample(flights, min(len(flights), 200))

        # Agrupar por flight_date desde firstSeen timestamp
        by_date: dict[str, list[dict]] = {}
        for f in sampled:
            fs = f.get("firstSeen")
            if fs:
                d = datetime.fromtimestamp(fs, tz=UTC).strftime("%Y-%m-%d")
                by_date.setdefault(d, []).append(f)

        for date_str, flist in sorted(by_date.items()):
            date_dir = mock_dir / "opensky" / date_str
            date_dir.mkdir(parents=True, exist_ok=True)
            fpath = date_dir / f"{airport}_{endpoint}.json"
            # Merge si ya existe (varios archivos parquet pueden tener mismo aeropuerto/fecha)
            existing = json.loads(fpath.read_text()) if fpath.exists() else []
            merged = existing + flist
            fpath.write_text(json.dumps(merged, indent=2))
            logger.info("  %s %s %s: %d vuelos → %s", date_str, airport, endpoint, len(merged), fpath)
            written_any = True

    if written_any:
        logger.info("Muestras guardadas en %s/opensky/", mock_dir)
    else:
        logger.warning("No se generó ninguna muestra.")


# ---------------------------------------------------------------------------
# Procesamiento de mock data → Bronze
# ---------------------------------------------------------------------------

def _discover_mock_data(mock_dir: Path) -> list[dict]:
    """Descubre todos los archivos JSON de mock organizados por fecha/aeropuerto.

    Returns:
        Lista de dicts con: date, airport, endpoint, file_path
    """
    opensky_dir = mock_dir / "opensky"
    if not opensky_dir.exists():
        logger.error("No existe %s. Genera datos con --generate-samples", opensky_dir)
        return []

    discovered: list[dict] = []
    for date_dir in sorted(opensky_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        date_str = date_dir.name
        # Validar formato YYYY-MM-DD
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        for json_file in sorted(date_dir.glob("*.json")):
            # Nombre: {ICAO}_{arrivals|departures}.json
            stem = json_file.stem  # sin .json
            parts = stem.rsplit("_", 1)
            if len(parts) != 2:
                logger.warning("  Formato inesperado: %s (se espera ICAO_endpoint.json)", json_file)
                continue
            airport, endpoint = parts
            if endpoint not in ("arrivals", "departures"):
                logger.warning("  Endpoint desconocido: %s (esperado arrivals/departures)", json_file)
                continue

            discovered.append({
                "date": date_str,
                "airport": airport,
                "endpoint": endpoint,
                "file_path": json_file,
            })

    return discovered


def process_mock_data(
    mock_dir: Path,
    delta_root: str,
    dry_run: bool = False,
) -> dict:
    """Lee los archivos mock y escribe cada uno en Bronze.

    Returns:
        Dict con stats del procesamiento.
    """
    files = _discover_mock_data(mock_dir)
    if not files:
        return {"total": 0, "written": 0, "errors": 0, "files": []}

    logger.info("Descubiertos %d archivos mock", len(files))

    # Agrupar por fecha
    dates = sorted(set(f["date"] for f in files))
    total_written = 0
    total_errors = 0
    results: list[dict] = []

    for date_str in dates:
        date_files = [f for f in files if f["date"] == date_str]
        logger.info("--- %s (%d archivos) ---", date_str, len(date_files))

        for i, entry in enumerate(date_files):
            airport = entry["airport"]
            endpoint = entry["endpoint"]
            file_path = entry["file_path"]

            logger.info(
                "  %s/%s (%d/%d): %s",
                airport, endpoint, i + 1, len(date_files), file_path.name,
            )

            if dry_run:
                results.append({
                    "date": date_str,
                    "airport": airport,
                    "endpoint": endpoint,
                    "status": "dry-run",
                    "flights": 0,
                })
                continue

            try:
                raw_data = json.loads(file_path.read_text())
                if not isinstance(raw_data, list):
                    logger.warning("  Formato inesperado (no es lista): %s", file_path)
                    total_errors += 1
                    results.append({
                        "date": date_str, "airport": airport, "endpoint": endpoint,
                        "status": "error", "error": "not a list",
                    })
                    continue

                # Simular parámetros que usaría la API real
                params = {
                    "airport": airport,
                    "begin": f"{date_str} 00:00:00",
                    "end": f"{date_str} 23:59:59",
                }

                write_raw(f"/flights/{endpoint}", params, raw_data, delta_root)
                total_written += 1
                results.append({
                    "date": date_str,
                    "airport": airport,
                    "endpoint": endpoint,
                    "status": "ok",
                    "flights": len(raw_data),
                })
                logger.info("    → %d vuelos escritos en Bronze", len(raw_data))

            except Exception as e:
                logger.error("  Error procesando %s: %s", file_path, e)
                total_errors += 1
                results.append({
                    "date": date_str, "airport": airport, "endpoint": endpoint,
                    "status": "error", "error": str(e),
                })

            if i < len(date_files) - 1:
                time.sleep(0.5)

    return {
        "total": len(files),
        "written": total_written,
        "errors": total_errors,
        "files": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mock de extract_to_bronze: lee JSON locales y escribe en Bronze (Delta Lake)",
    )
    parser.add_argument(
        "--mock-dir", type=Path, default=MOCK_DIR_DEFAULT,
        help="Directorio raíz con datos mock (default: data/mock)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simula sin escribir en Delta Lake",
    )
    parser.add_argument(
        "--days", type=int, default=1,
        help="Días hacia atrás a samplear si se usa --generate-samples",
    )
    parser.add_argument(
        "--generate-samples", action="store_true",
        help="Extrae muestra de datos reales desde Bronze y los guarda como mock",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)
    delta_root = get_delta_root()

    logger.info("=" * 60)
    logger.info("Mock Extract → Bronze")
    logger.info("Mock dir: %s | Dry-run: %s | Delta root: %s", args.mock_dir, args.dry_run, delta_root)

    if args.generate_samples:
        logger.info("Generando datos de muestra...")
        generate_samples(args.mock_dir, days=args.days)
        logger.info("=" * 60)
        return 0

    logger.info("Procesando datos mock → Bronze...")
    logger.info("=" * 60)

    result = process_mock_data(
        mock_dir=args.mock_dir,
        delta_root=delta_root,
        dry_run=args.dry_run,
    )

    logger.info("=" * 60)
    if args.dry_run:
        logger.info(
            "MOCK DRY RUN: %d archivos simulados",
            result["total"],
        )
    else:
        logger.info(
            "MOCK COMPLETADO: %d archivos → Bronze | %d escritos, %d errores",
            result["total"], result["written"], result["errors"],
        )

        # Resumen por aeropuerto
        for r in result["files"]:
            if r["status"] == "ok":
                logger.info(
                    "  ✅ %s/%s: %d vuelos",
                    r["airport"], r["endpoint"], r["flights"],
                )
            elif r["status"] == "error":
                logger.info(
                    "  ❌ %s/%s: %s",
                    r["airport"], r["endpoint"], r.get("error", "unknown"),
                )

    logger.info("=" * 60)
    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
