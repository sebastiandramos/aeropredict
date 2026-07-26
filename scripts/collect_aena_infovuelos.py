#!/usr/bin/env python3
"""Collect AENA Infovuelos snapshots into CSV.

Example:
    python scripts/collect_aena_infovuelos.py --airports BCN MAD --types both
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aeropredict.opensky.config import get_delta_root
from aeropredict.opensky.storage import write_raw_json
from aeropredict.sources.aena_infovuelos import (
    FLIGHT_TYPE_LABELS,
    AenaInfovuelosAdapter,
)

logger = logging.getLogger("collect_aena_infovuelos")

CSV_COLUMNS = [
    "snapshot_at_utc",
    "source",
    "query_airport_iata",
    "query_flight_type",
    "flight_type",
    "flight_number",
    "raw_flight_number",
    "airline_iata",
    "airline_icao",
    "airline_name",
    "aena_airport_iata",
    "other_airport_iata",
    "other_city",
    "scheduled_date",
    "scheduled_time",
    "scheduled_local",
    "estimated_date",
    "estimated_time",
    "estimated_local",
    "status",
    "terminal",
    "gate_first",
    "gate_second",
    "checkin_from",
    "checkin_to",
    "aircraft_type",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect AENA Infovuelos snapshots")
    parser.add_argument(
        "--airports",
        nargs="+",
        default=["BCN"],
        help="AENA airport IATA codes, e.g. BCN MAD PMI",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        default=["both"],
        choices=["departures", "arrivals", "both", "S", "L"],
        help="Flight type to collect",
    )
    parser.add_argument(
        "--output-dir",
        default="data/raw/aena_infovuelos",
        help="Directory for CSV and raw JSON outputs",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max rows per airport/type")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between requests")
    parser.add_argument("--no-json", action="store_true", help="Do not write raw JSON")
    parser.add_argument("--no-csv", action="store_true", help="Do not write CSV output")
    parser.add_argument("--no-warmup", action="store_true", help="Skip initial page warmup")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be collected without fetching",
    )
    return parser.parse_args(argv)


def expand_types(values: list[str]) -> list[str]:
    codes: list[str] = []
    for value in values:
        if value == "both":
            codes.extend(["S", "L"])
        elif value == "departures":
            codes.append("S")
        elif value == "arrivals":
            codes.append("L")
        elif value in FLIGHT_TYPE_LABELS:
            codes.append(value)
        else:
            allowed = "arrivals, both, departures, L, S"
            raise ValueError(f"Invalid flight type {value!r}. Expected one of: {allowed}")
    seen: set[str] = set()
    unique: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def collect(
    airports: list[str],
    flight_types: list[str],
    output_dir: Path,
    limit: int | None = None,
    sleep_seconds: float = 1.0,
    write_csv: bool = True,
    write_json: bool = True,
    warmup: bool = True,
    dry_run: bool = False,
    delta_root: str = "data/raw",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = AenaInfovuelosAdapter()
    if warmup:
        adapter.warmup()

    if dry_run:
        for airport in [a.upper() for a in airports]:
            for flight_type in flight_types:
                label = FLIGHT_TYPE_LABELS.get(flight_type, flight_type)
                logger.info("  [dry-run] Would fetch AENA %s %s", airport, label)
        return {"csv_path": None, "json_path": None, "rows": 0, "errors": 0}

    snapshot_at = datetime.now(UTC).replace(microsecond=0)
    stamp = snapshot_at.strftime("%Y%m%dT%H%M%SZ")
    csv_path = output_dir / f"aena_infovuelos_{stamp}.csv" if write_csv else None
    json_path = output_dir / f"aena_infovuelos_{stamp}.json" if write_json else None

    rows: list[dict[str, Any]] = []
    raw_batches: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    requests_made = 0
    for airport in [airport.upper() for airport in airports]:
        for flight_type in flight_types:
            if requests_made > 0 and sleep_seconds > 0:
                time.sleep(sleep_seconds)

            label = FLIGHT_TYPE_LABELS.get(flight_type, flight_type)
            logger.info("Fetching AENA %s %s", airport, label)
            try:
                raw_flights = adapter.get_flights(airport, label)
            except Exception as exc:
                logger.warning("AENA error %s %s: %s", airport, label, exc)
                errors.append(
                    {"airport": airport, "flight_type": label, "error": str(exc)}
                )
                continue
            finally:
                requests_made += 1

            if limit is not None:
                raw_flights = raw_flights[:limit]

            raw_batches.append(
                {
                    "airport": airport,
                    "flight_type": label,
                    "snapshot_at_utc": snapshot_at.isoformat(),
                    "count": len(raw_flights),
                    "flights": raw_flights,
                }
            )
            rows.extend(
                AenaInfovuelosAdapter.normalize_flight(raw, airport, flight_type, snapshot_at)
                for raw in raw_flights
            )

    if csv_path:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    if json_path:
        payload = {
            "source": "aena_infovuelos",
            "snapshot_at_utc": snapshot_at.isoformat(),
            "rows": len(rows),
            "errors": errors,
            "batches": raw_batches,
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write raw batches to Bronze (Delta Lake)
    for batch in raw_batches:
        write_raw_json(
            "aena_infovuelos",
            "/sites/Satellite",
            {"airport": batch["airport"], "flightType": batch["flight_type"]},
            batch["flights"],
            delta_root,
        )

    return {
        "csv_path": str(csv_path) if csv_path else None,
        "json_path": str(json_path) if json_path else None,
        "rows": len(rows),
        "errors": len(errors),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)
    result = collect(
        airports=args.airports,
        flight_types=expand_types(args.types),
        output_dir=Path(args.output_dir),
        limit=args.limit,
        sleep_seconds=args.sleep,
        write_csv=not args.no_csv,
        write_json=not args.no_json,
        warmup=not args.no_warmup,
        dry_run=args.dry_run,
        delta_root=get_delta_root(),
    )
    if args.dry_run:
        logger.info("Dry run complete — no data fetched or written")
    else:
        logger.info(
            "AENA Infovuelos complete: %d rows, %d errors",
            result["rows"],
            result["errors"],
        )
        if result["csv_path"]:
            logger.info("CSV: %s", result["csv_path"])
        if result["json_path"]:
            logger.info("JSON: %s", result["json_path"])
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
