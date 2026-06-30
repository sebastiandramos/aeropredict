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

from aeropredict.sources.aena_infovuelos import (
    FLIGHT_TYPE_LABELS,
    AenaInfovuelosAdapter,
    normalize_flight,
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
    parser.add_argument("--no-warmup", action="store_true", help="Skip initial page warmup")
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
        else:
            codes.append(value)
    return list(dict.fromkeys(codes))


def collect(
    airports: list[str],
    flight_types: list[str],
    output_dir: Path,
    limit: int | None = None,
    sleep_seconds: float = 1.0,
    write_json: bool = True,
    warmup: bool = True,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = AenaInfovuelosAdapter()
    if warmup:
        adapter.warmup()

    snapshot_at = datetime.now(UTC).replace(microsecond=0)
    stamp = snapshot_at.strftime("%Y%m%dT%H%M%SZ")
    csv_path = output_dir / f"aena_infovuelos_{stamp}.csv"
    json_path = output_dir / f"aena_infovuelos_{stamp}.json"

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
                raw_flights = adapter.get_flights(airport, flight_type)
            except Exception as exc:
                logger.warning("AENA error %s %s: %s", airport, label, exc)
                errors.append(
                    {"airport": airport, "flight_type": label, "error": str(exc)}
                )
                continue

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
                normalize_flight(raw, airport, flight_type, snapshot_at)
                for raw in raw_flights
            )

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    if write_json:
        payload = {
            "source": "aena_infovuelos",
            "snapshot_at_utc": snapshot_at.isoformat(),
            "rows": len(rows),
            "errors": errors,
            "batches": raw_batches,
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        json_path = None

    return {
        "csv_path": str(csv_path),
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
        write_json=not args.no_json,
        warmup=not args.no_warmup,
    )
    logger.info(
        "AENA Infovuelos complete: %d rows, %d errors",
        result["rows"],
        result["errors"],
    )
    logger.info("CSV: %s", result["csv_path"])
    if result["json_path"]:
        logger.info("JSON: %s", result["json_path"])
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
