#!/usr/bin/env python3
"""Migrate gold.runways to allow NULL he_ident and primary key (airport_ident, le_ident)."""
from __future__ import annotations

import argparse
import logging

from aeropredict.opensky.storage_gold import get_postgres_uri

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate runways PK and he_ident nullability")
    parser.add_argument("--apply", action="store_true", help="Apply migration")
    args = parser.parse_args(argv)

    if not args.apply:
        logger.info("Dry-run: no changes applied")
        return 0

    import psycopg2

    uri = get_postgres_uri()
    conn = psycopg2.connect(uri)
    conn.autocommit = True
    cur = conn.cursor()

    logger.info("Altering gold.runways...")
    # Drop existing PK first
    cur.execute("ALTER TABLE gold.runways DROP CONSTRAINT IF EXISTS runways_pkey;")
    # Make he_ident nullable
    cur.execute("ALTER TABLE gold.runways ALTER COLUMN he_ident DROP NOT NULL;")
    # Deduplicate: keep newest ingested_at per airport_ident, le_ident
    cur.execute("""
        DELETE FROM gold.runways
        WHERE ctid IN (
            SELECT ctid FROM (
                SELECT ctid,
                       ROW_NUMBER() OVER (
                           PARTITION BY airport_ident, le_ident
                           ORDER BY ingested_at DESC, ctid DESC
                       ) AS rn
                FROM gold.runways
            ) t
            WHERE t.rn > 1
        );
    """)
    # Recreate PK on airport_ident, le_ident
    cur.execute("ALTER TABLE gold.runways ADD PRIMARY KEY (airport_ident, le_ident);")
    logger.info("Migration applied")
    cur.close()
    conn.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
