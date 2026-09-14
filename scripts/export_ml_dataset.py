"""Export ML dataset from gold.feature_store to parquet/csv with metadata.

Usage (local dev with mock):
    python scripts/export_ml_dataset.py --mock --mock-rows 500

By default writes:
 - data/processed/feature_store.parquet
 - data/processed/feature_store.csv
 - data/processed/feature_store_metadata.json

The script may connect to PostgreSQL to read gold.feature_store unless
--mock is used. Controlled with --db-url or POSTGRES_URI env var.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from aeropredict.opensky.logging_config import setup_daily_logger

EVIDENCE_LOG = Path(".omo/evidence/task-12-export.log")
NOTEPAD = Path(".omo/notepads/aeropredict-gap-closure/learnings.md")


def _setup_logging() -> logging.Logger:
    logger = setup_daily_logger(name="export_ml_dataset", log_dir=".omo/evidence", log_file="task-12-export.log")
    return logger


def _connect_db(db_url: str | None) -> psycopg2.extensions.connection:
    url = db_url or os.environ.get("POSTGRES_URI")
    if not url:
        raise RuntimeError("No database URL provided. Set POSTGRES_URI or use --db-url or run with --mock")
    conn = psycopg2.connect(url)
    return conn


def _query_feature_store(conn: psycopg2.extensions.connection, date_start: str | None, date_end: str | None) -> pd.DataFrame:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    sql = "SELECT * FROM gold.feature_store"
    params: list[Any] = []
    if date_start or date_end:
        where_clauses: list[str] = []
        if date_start:
            where_clauses.append("scheduled_local >= %s")
            params.append(date_start)
        if date_end:
            where_clauses.append("scheduled_local <= %s")
            params.append(date_end)
        sql += " WHERE " + " AND ".join(where_clauses)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df


def _mock_row(airport_choices: list[str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    dep = random.choice(airport_choices)
    arr = random.choice([a for a in airport_choices if a != dep])
    hour = random.randint(0, 23)
    scheduled_local = (
        now.replace(hour=hour, minute=0, second=0, microsecond=0)
        - timedelta(days=random.randint(0, 30))
    ).replace(tzinfo=None)
    retraso = random.gauss(5, 15)

    return {
        "aena_airport_iata": dep,
        "flight_number": f"IB{random.randint(1000, 9999)}",
        "flight_type": "departures",
        "scheduled_local": scheduled_local.isoformat(),
        "hora_vuelo": hour,
        "dia_semana": scheduled_local.isoweekday(),
        "airline_iata": random.choice(["IB", "VY", "RYR"]),
        "other_airport_iata": arr,
        "temperatura_metar": round(random.uniform(-5, 35), 1),
        "punto_rocio_metar": round(random.uniform(-10, 20), 1),
        "relh": round(random.uniform(20, 100), 1),
        "retraso_minutos": float(round(retraso, 1)),
        "retraso_10_min": "RETRASO" if retraso >= 10 else "NO_RETRASO",
    }


def _generate_mock_dataframe(n: int) -> pd.DataFrame:
    airports = ["LEMD", "LEBL", "LEAL", "LECO", "LEMG"]
    rows = [_mock_row(airports) for _ in range(n)]
    df = pd.DataFrame(rows)
    return df


def _metadata_for_df(df: pd.DataFrame, date_start: str | None, date_end: str | None) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    meta["row_count"] = len(df)
    meta["feature_count"] = len(df.columns)
    meta["features"] = list(df.columns.astype(str))
    meta["date_range"] = {"start": date_start, "end": date_end}
    nulls = {}
    for c in df.columns:
        pct = float(df[c].isna().mean() * 100) if len(df) > 0 else 0.0
        nulls[str(c)] = round(pct, 3)
    meta["null_percentages"] = nulls
    return meta


def _write_outputs(df: pd.DataFrame, parquet_path: Path, csv_path: Path, metadata_path: Path, logger: logging.Logger) -> None:
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    df.to_parquet(parquet_path, index=False)
    logger.info("Wrote parquet: %s", parquet_path)
    df.to_csv(csv_path, index=False)
    logger.info("Wrote csv: %s", csv_path)

    meta = _metadata_for_df(df, None, None)
    metadata_path.write_text(json.dumps(meta, indent=2, sort_keys=True))
    logger.info("Wrote metadata: %s", metadata_path)


def _append_notepad(msg: str) -> None:
    NOTEPAD.parent.mkdir(parents=True, exist_ok=True)
    with NOTEPAD.open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def main(argv: list[str] | None = None) -> int:
    logger = _setup_logging()

    parser = argparse.ArgumentParser(description="Export ML dataset from gold.feature_store")
    parser.add_argument("--output", default="data/processed/feature_store.parquet", help="Parquet output path")
    parser.add_argument("--csv-output", default="data/processed/feature_store.csv", help="CSV output path")
    parser.add_argument("--metadata-output", default="data/processed/feature_store_metadata.json", help="Metadata JSON path")
    parser.add_argument("--date-start", help="Filter scheduled_local >= YYYY-MM-DD")
    parser.add_argument("--date-end", help="Filter scheduled_local <= YYYY-MM-DD")
    parser.add_argument("--mock", action="store_true", help="Generate synthetic mock data instead of querying DB")
    parser.add_argument("--mock-rows", type=int, default=500, help="Number of mock rows to generate")
    parser.add_argument("--db-url", help="Override POSTGRES_URI env var for DB connection")
    parser.add_argument("--dry-run", action="store_true", help="Only report counts and schema, do not write files")
    args = parser.parse_args(argv)

    try:
        if args.mock:
            logger.info("Generating mock data: %d rows", args.mock_rows)
            df = _generate_mock_dataframe(args.mock_rows)
        else:
            conn = _connect_db(args.db_url)
            df = _query_feature_store(conn, args.date_start, args.date_end)
            conn.close()

        logger.info("Dataset rows: %d columns: %d", len(df), len(df.columns))

        if args.dry_run:
            logger.info("Dry-run: schema=%s", list(df.columns))
            return 0

        parquet_path = Path(args.output)
        csv_path = Path(args.csv_output)
        metadata_path = Path(args.metadata_output)

        meta = _metadata_for_df(df, args.date_start, args.date_end)
        metadata_path.write_text(json.dumps(meta, indent=2, sort_keys=True))
        logger.info("Wrote metadata: %s", metadata_path)

        df.to_parquet(parquet_path, index=False)
        logger.info("Wrote parquet: %s", parquet_path)

        df.to_csv(csv_path, index=False)
        logger.info("Wrote csv: %s", csv_path)

        _append_notepad(f"[{datetime.now(tz=UTC).isoformat()}] export_ml_dataset wrote {len(df)} rows to {parquet_path}")

    except Exception as exc:  # Log and write to evidence
        logger.exception("Export failed: %s", exc)
        EVIDENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EVIDENCE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(UTC).isoformat()} - ERROR - {exc}\n")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
