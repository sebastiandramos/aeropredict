"""Sync PostgreSQL gold schema to DuckDB Platinum layer.

Platinum is the accelerated DuckDB cache used for fast training / exploratory
analytics. It mirrors a subset of gold.* tables in a local DuckDB file so
training jobs can run without hitting Postgres.

Usage:
    python scripts/sync_gold_to_platinum_duckdb.py --table gold.feature_store
    python scripts/sync_gold_to_platinum_duckdb.py --all --force
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import duckdb
import psycopg2
from psycopg2.extras import execute_values

from aeropredict.opensky.storage_gold import _get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PLATINUM_ROOT = Path("data/platinum")
PLATINUM_DB = PLATINUM_ROOT / "platinum.duckdb"

def ensure_platinum_db() -> duckdb.DuckDBPyConnection:
    PLATINUM_ROOT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(PLATINUM_DB))
    con.execute("PRAGMA memory_limit='4GB'")
    return con

def table_exists_pg(pg_conn, schema_table: str) -> bool:
    schema, table = schema_table.split(".", 1)
    cur = pg_conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema, table),
    )
    exists = cur.fetchone() is not None
    cur.close()
    return exists

def get_pk_columns(pg_conn, schema, table):
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE i.indisprimary
          AND n.nspname = %s
          AND c.relname = %s
        ORDER BY array_position(i.indkey, a.attnum)
    """, (schema, table))
    cols = [r[0] for r in cur.fetchall()]
    cur.close()
    return cols

def sync_table(pg_conn, duck_con, schema_table: str, dry_run: bool = False) -> int:
    schema, table = schema_table.split(".", 1)
    duck_table = f"{schema}_{table}"
    logger.info("Syncing %s -> platinum.%s", schema_table, duck_table)

    if not table_exists_pg(pg_conn, schema_table):
        logger.warning("Postgres table %s does not exist, skipping", schema_table)
        return 0

    # Count rows for logging
    cur = pg_conn.cursor()
    cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
    count = cur.fetchone()[0]

    if dry_run:
        logger.info("Dry-run: %d rows would be synced to %s", count, duck_table)
        # Show sample
        cur.execute(f'SELECT * FROM "{schema}"."{table}" LIMIT 5')
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        logger.info("Sample rows:")
        logger.info("Columns: %s", cols)
        for r in rows:
            logger.info(r)
        cur.close()
        return 0
    cur.close()

    # Load columns
    cur = pg_conn.cursor()
    cur.execute(f'SELECT * FROM "{schema}"."{table}" LIMIT 1')
    cols = [d[0] for d in cur.description]
    cur.close()

    col_defs = ", ".join([f'"{c}" TYPE' for c in cols])  # DuckDB will infer on create
    # Create table if not exists with dynamic schema using first batch
    # Simpler: create empty table with same columns using DuckDB's type inference
    duck_con.execute(f'DROP TABLE IF EXISTS "{duck_table}"')
    duck_con.execute(f'CREATE TABLE "{duck_table}" AS SELECT * FROM "{schema}"."{table}" WHERE 1=0')
    # Actually we need data from Postgres, so stream via cursor
    cur = pg_conn.cursor(name="pg_copy_cursor")
    cur.itersize = 10000
    cur.execute(f'SELECT * FROM "{schema}"."{table}"')
    placeholders = ", ".join(["?" for _ in cols])
    batch = []
    batch_size = 10000
    total = 0
    for row in cur:
        batch.append(row)
        if len(batch) >= batch_size:
            duck_con.executemany(f'INSERT INTO "{duck_table}" VALUES ({placeholders})', batch)
            total += len(batch)
            batch.clear()
    if batch:
        duck_con.executemany(f'INSERT INTO "{duck_table}" VALUES ({placeholders})', batch)
        total += len(batch)
    cur.close()

    # Idempotent upsert: if PK exists, do MERGE to replace
    pk_cols = get_pk_columns(pg_conn, schema, table)
    if pk_cols:
        # Create temp table with new data and merge
        temp_table = f"{duck_table}_tmp"
        duck_con.execute(f'DROP TABLE IF EXISTS "{temp_table}"')
        # We already inserted directly; for true idempotency we could have used MERGE.
        # For simplicity and performance, we keep the recreate approach when force is True,
        # otherwise we assume table is empty. To make default idempotent, we recreate table each run.
        # Recreate is safe for feature_store training use case.
        pass

    logger.info("Synced %d rows to %s", total, duck_table)
    return total

def sync_all(pg_conn, duck_con, tables, dry_run=False):
    total = 0
    for t in tables:
        total += sync_table(pg_conn, duck_con, t, dry_run=dry_run)
    return total

def main():
    parser = argparse.ArgumentParser(description="Sync gold.feature_store -> platinum DuckDB")
    parser.add_argument("--dry-run", action="store_true", help="Count only")
    args = parser.parse_args()

    tables = ["gold.feature_store"]

    pg_conn = _get_conn()
    duck_con = ensure_platinum_db()

    try:
        n = sync_all(pg_conn, duck_con, tables, dry_run=args.dry_run)
        logger.info("Total rows synced: %d", n)
    finally:
        pg_conn.close()
        duck_con.close()

if __name__ == "__main__":
    main()
