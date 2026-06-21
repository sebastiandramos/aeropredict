#!/usr/bin/env python3
"""Script 1/3: OpenSky API → Bronze (Delta Lake).

Extrae vuelos históricos de la API OpenSky y escribe el JSON crudo
en la capa Bronze (Delta Lake), con dual-write a R2 + local.

Uso:
    python scripts/extract_to_bronze.py [--dry-run] [--days N]

Flujo:
    Verifica créditos → itera aeropuertos → fetch arrivals/departures
    → write_raw() → checkpoint
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta

from aeropredict.opensky.client_pool import ClientPool
from aeropredict.opensky.config import AEROPUERTOS, get_all_credentials, get_delta_root
from aeropredict.opensky.credit_checker import can_extract
from aeropredict.opensky.extract_flights import fetch_arrivals_raw, fetch_departures_raw
from aeropredict.opensky.logging_config import setup_daily_logger
from aeropredict.opensky.checkpoint_mongo import (
    get_checkpoint_dict,
    save_checkpoint_dict_entry,
)
from aeropredict.opensky.storage import (
    cache_empty_airport,
    is_airport_empty,
    write_raw,
)

CHECKPOINT_COLLECTION = "bronze_extract"

REQUEST_DELAY = 5.0
logger = logging.getLogger("extract_to_bronze")

SPANISH_AIRPORT_CODES: list[str] = [
    code for code, _name, _city, country in AEROPUERTOS if country == "España"
]

MIN_CREDITS = 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Script 1/3: Extrae vuelos OpenSky y escribe en Bronze (Delta Lake)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simula sin llamar a la API")
    parser.add_argument(
        "--days", type=int, default=1,
        help="Días hacia atrás a extraer (default: 1)",
    )
    parser.add_argument(
        "--backfill", action="store_true",
        help="Re-extrae días con datos incompletos en Bronze (ignora checkpoint y cache)",
    )
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="Fecha inicio para backfill (YYYY-MM-DD). Si se omite, usa la fecha más antigua en Bronze.",
    )
    return parser.parse_args(argv)


def _extract_day(
    client: ClientPool,
    target_date: datetime.date,
    dry_run: bool,
    force: bool = False,
) -> dict:
    """Extrae arrivals + departures para un día y escribe en Bronze.

    Returns:
        Dict con stats de extracción.
    """
    day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=UTC)
    day_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=UTC)

    total_airports = 0
    airports_done: list[str] = []
    errors: list[dict] = []
    delta_root = get_delta_root()

    # Cargar checkpoint para saltar aeropuertos ya extraídos
    cp = get_checkpoint_dict(CHECKPOINT_COLLECTION)
    date_str = str(target_date)
    already_extracted = set(cp.get(date_str, [])) if not force else set()

    for i, apt in enumerate(SPANISH_AIRPORT_CODES):
        if apt in already_extracted:
            logger.info("  %s (%d/%d): ya extraído (checkpoint), saltando", apt, i + 1, len(SPANISH_AIRPORT_CODES))
            total_airports += 1
            airports_done.append(apt)
            continue

        if i > 0:
            time.sleep(REQUEST_DELAY)

        if not dry_run:
            # --- Arrivals ---
            if not force and is_airport_empty(delta_root, apt, target_date, "arrivals"):
                logger.info("  %s: arrivals vacío (cache), saltando", apt)
            else:
                try:
                    ep_a, params_a, raw_a = fetch_arrivals_raw(client, apt, day_start, day_end)
                    write_raw(ep_a, params_a, raw_a, delta_root)
                    if not raw_a:
                        cache_empty_airport(delta_root, apt, target_date, "arrivals")
                except Exception as e:
                    err = str(e)
                    if "429" in err:
                        logger.warning("  %s: arrivals rate limited. Deteniendo.", apt)
                        errors.append({"airport": apt, "error": f"arrivals: {err}"})
                        break
                    logger.warning("  %s: arrivals error: %s", apt, err)
                    errors.append({"airport": apt, "error": f"arrivals: {err}"})

            # --- Departures ---
            if not force and is_airport_empty(delta_root, apt, target_date, "departures"):
                logger.info("  %s: departures vacío (cache), saltando", apt)
            else:
                try:
                    ep_d, params_d, raw_d = fetch_departures_raw(client, apt, day_start, day_end)
                    write_raw(ep_d, params_d, raw_d, delta_root)
                    if not raw_d:
                        cache_empty_airport(delta_root, apt, target_date, "departures")
                except Exception as e:
                    err = str(e)
                    if "429" in err:
                        logger.warning("  %s: departures rate limited. Deteniendo.", apt)
                        errors.append({"airport": apt, "error": f"departures: {err}"})
                        break
                    logger.warning("  %s: departures error: %s", apt, err)
                    errors.append({"airport": apt, "error": f"departures: {err}"})

        total_airports += 1
        airports_done.append(apt)
        logger.info(
            "  %s (%d/%d) → Bronze",
            apt, i + 1, len(SPANISH_AIRPORT_CODES),
        )

    return {
        "date": str(target_date),
        "airports": total_airports,
        "airports_done": airports_done,
        "errors": errors,
    }


def _get_bronze_dates(delta_root: str) -> list[str]:
    """Get sorted list of dates with data in Bronze."""
    from deltalake import DeltaTable

    table_uri = f"{delta_root}/bronze/opensky"
    try:
        dt = DeltaTable(table_uri)
    except Exception:
        return []
    table = dt.to_pyarrow_table(columns=["ingestion_date"])
    dates = sorted(set(str(d) for d in table.column("ingestion_date").to_pylist()))
    return dates


def _count_bronze_rows(delta_root: str, date_str: str) -> int:
    """Count rows in Bronze for a specific date."""
    from deltalake import DeltaTable
    import pyarrow.compute as pc

    table_uri = f"{delta_root}/bronze/opensky"
    try:
        dt = DeltaTable(table_uri)
        table = dt.to_pyarrow_table(columns=["ingestion_date", "endpoint"])
    except Exception:
        return 0
    from datetime import date as date_type
    y, m, d = map(int, date_str.split("-"))
    target = date_type(y, m, d)
    mask = pc.equal(table.column("ingestion_date"), target)
    filtered = table.filter(mask)
    return len(filtered)


def main(argv: list[str] | None = None) -> int:
    setup_daily_logger()
    args = _parse_args(argv)
    delta_root = get_delta_root()

    logger.info("=" * 60)
    logger.info("Script 1/3: Extract → Bronze")
    logger.info("Dry-run: %s | Days: %d | Delta root: %s", args.dry_run, args.days, delta_root)
    if args.backfill:
        logger.info("Mode: BACKFill (re-extract incomplete dates + fill missing dates, ignore checkpoint)")
    logger.info("=" * 60)

    client: ClientPool | None = None
    if not args.dry_run:
        creds = get_all_credentials()
        if not creds:
            logger.error("No hay credenciales OpenSky. Usa `doppler run`.")
            return 1
        client = ClientPool(creds)
        ok, info = can_extract(min_required=MIN_CREDITS, pool=client)
        logger.info("Créditos OpenSky: %s remaining", info.get("remaining", "?"))
        if not ok:
            retry = info.get("retry_after", "?")
            logger.warning("Créditos insuficientes. Retry after: %ss", retry)
            logger.info("Continuando de todas formas (MIN_CREDITS=%s)...", MIN_CREDITS)

    start = time.time()
    total_airports = 0
    all_errors: list[dict] = []
    dates_done: list[str] = []

    now_utc = datetime.now(UTC)

    if args.backfill:
        # Backfill mode: re-extract incomplete dates AND missing dates
        bronze_dates = set(_get_bronze_dates(delta_root))
        # Checkpoint: which airports are done per date
        cp = get_checkpoint_dict(CHECKPOINT_COLLECTION)

        # Determine date range
        if args.start_date:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        elif bronze_dates:
            start_date = min(datetime.strptime(d, "%Y-%m-%d").date() for d in bronze_dates)
        else:
            start_date = (now_utc - timedelta(days=30)).date()

        end_date = (now_utc - timedelta(days=1)).date()

        # Generate all dates in range
        all_dates = []
        current = start_date
        while current <= end_date:
            all_dates.append(current)
            current += timedelta(days=1)

        logger.info("Rango backfill: %s a %s (%d días)", start_date, end_date, len(all_dates))
        logger.info("Bronze tiene %d fechas, faltan %d fechas nuevas",
                     len(bronze_dates), len([d for d in all_dates if str(d) not in bronze_dates]))

        # Process dates newest → oldest
        for target_date in reversed(all_dates):
            date_str = str(target_date)

            if date_str not in bronze_dates:
                # Missing date: extract all 32 airports
                logger.info("  %s: AUSENTE en Bronze — extrayendo todos los aeropuertos", date_str)
                missing = set(SPANISH_AIRPORT_CODES)
            else:
                # Existing date: check completeness
                row_count = _count_bronze_rows(delta_root, date_str)
                cp_airports = set(cp.get(date_str, []))
                missing = set(SPANISH_AIRPORT_CODES) - cp_airports

                # Skip only if: enough rows AND checkpoint complete
                if len(missing) == 0 and row_count >= 1000:
                    logger.info("  %s: %d rows, %d/%d airports — completo, saltando",
                                date_str, row_count, len(cp_airports), len(SPANISH_AIRPORT_CODES))
                    continue

                # If checkpoint says complete but too few rows → force all 32 airports
                if len(missing) == 0 and row_count < 1000:
                    logger.info("  %s: %d rows pero checkpoint dice completo — forzando re-extracción",
                                date_str, row_count)
                    missing = set(SPANISH_AIRPORT_CODES)
                else:
                    logger.info("  %s: %d rows, checkpoint=%d/%d airports, faltan=%d — REEXTRAER",
                                date_str, row_count, len(cp_airports), len(SPANISH_AIRPORT_CODES), len(missing))

            if args.dry_run:
                logger.info("  DRY RUN: extraería %d aeropuertos para %s", len(missing), date_str)
                dates_done.append(date_str)
                total_airports += len(missing)
                continue

            result = _extract_day(client, target_date, dry_run=False, force=True)
            dates_done.append(result["date"])
            total_airports += result["airports"]
            all_errors.extend(result["errors"])

            if result["airports_done"]:
                save_checkpoint_dict_entry(CHECKPOINT_COLLECTION, result["date"], result["airports_done"])
    else:
        # Normal mode: extract last N days
        for offset in range(1, args.days + 1):
            target_date = (now_utc - timedelta(days=offset)).date()
            logger.info("--- Día %s (D-%d) ---", target_date, offset)

            if args.dry_run:
                logger.info(
                    "DRY RUN: extraería %d aeropuertos para %s",
                    len(SPANISH_AIRPORT_CODES), target_date,
                )
                dates_done.append(str(target_date))
                total_airports += len(SPANISH_AIRPORT_CODES)
                continue

            result = _extract_day(client, target_date, dry_run=False)
            dates_done.append(result["date"])
            total_airports += result["airports"]
            all_errors.extend(result["errors"])

            if result["airports_done"]:
                save_checkpoint_dict_entry(CHECKPOINT_COLLECTION, result["date"], result["airports_done"])

    elapsed = time.time() - start

    logger.info("=" * 60)
    if args.dry_run:
        logger.info("BRONZE DRY RUN: %d días simulados", args.days)
    else:
        logger.info(
            "BRONZE COMPLETADO: %d días, %d aeropuertos | %.1fs",
            len(dates_done), total_airports, elapsed,
        )
        if all_errors:
            logger.warning("Errores: %d aeropuertos", len(all_errors))
            for e in all_errors:
                logger.warning("  - %s: %s", e["airport"], e["error"])
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
