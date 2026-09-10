"""Build the gold.feature_store table.

Joins AENA flight info (departures) with cut-time METAR weather to create a
flat feature table ready for model training.  Only departure flights are
included (model predicts departure delay).

Data sources (all PostgreSQL gold schema):
    1. gold.aena_infovuelos  — flight schedules & estimated times (snapshot table;
       latest snapshot per flight deduped).
    2. gold.metar            — METAR weather observations (obs_time, temp, dewp, relh).
    3. gold.airports         — fallback IATA→ICAO mapping when airport_codes.py
       doesn't cover the airport.

Derivations:
    hora_vuelo      — hour of scheduled_local (0-23).
    dia_semana      — ISO weekday (1=Mon..7=Sun).
    retraso_minutos — (estimated - scheduled) in minutes, rounded to 1 decimal.
    retraso_10_min  — 'RETRASO' if >= 10 else 'NO_RETRASO'.
    METAR join      — most recent obs_time <= cut_epoch (scheduled departure
                      converted to UTC).  No data leakage.

Skips if checkpoint exists (use --force to rebuild).

CLI:
    --reset     Drops and recreates the table
    --dry-run   Shows how many rows would be inserted
    --force     Ignore checkpoint and process all history
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import execute_values

from aeropredict.opensky.checkpoint_mongo import (
    add_to_checkpoint_set,
    get_checkpoint_set,
)
from aeropredict.opensky.storage_gold import _get_conn
from aeropredict.sources.airport_codes import get_icao_for_iata

CHECKPOINT_COLLECTION = "build_feature_store"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MADRID_TZ = ZoneInfo("Europe/Madrid")

# ---------------------------------------------------------------------------
# DDL — created by this script; other agents own storage_gold.py
# ---------------------------------------------------------------------------

FEATURE_STORE_DDL = """
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS gold.feature_store (
    aena_airport_iata   VARCHAR(4) NOT NULL,
    flight_number       VARCHAR(20) NOT NULL,
    flight_type         VARCHAR(20) NOT NULL,
    scheduled_local     VARCHAR(30) NOT NULL,
    hora_vuelo          INTEGER NOT NULL,
    dia_semana          INTEGER NOT NULL,
    airline_iata        VARCHAR(4),
    other_airport_iata  VARCHAR(4),
    temperatura_metar   FLOAT,
    punto_rocio_metar   FLOAT,
    relh                FLOAT,
    retraso_minutos     FLOAT,
    retraso_10_min      VARCHAR(20),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (aena_airport_iata, flight_number, flight_type, scheduled_local)
);
"""

INFERENCE_VIEW_SQL = """
CREATE OR REPLACE VIEW gold.feature_store_inference AS
SELECT
    hora_vuelo,
    dia_semana,
    airline_iata,
    other_airport_iata,
    temperatura_metar,
    punto_rocio_metar,
    relh
FROM gold.feature_store;
"""

# ---------------------------------------------------------------------------
# Pure helpers — testable without DB
# ---------------------------------------------------------------------------


def compute_retraso(scheduled_local: str, estimated_local: str | None) -> float | None:
    """Compute departure delay in minutes (estimated - scheduled).

    Both strings are naive ISO timestamps (e.g. "2026-08-03T12:00:00").

    Returns:
        Delay in minutes rounded to 1 decimal, or None if estimated_local
        is missing/unparseable.
    """
    if not estimated_local:
        return None
    try:
        scheduled_dt = datetime.fromisoformat(scheduled_local)
        estimated_dt = datetime.fromisoformat(estimated_local)
    except (ValueError, TypeError):
        return None
    delta = (estimated_dt - scheduled_dt).total_seconds() / 60.0
    return round(delta, 1)


def compute_retraso_10_min(retraso_minutos: float | None) -> str | None:
    """Map delay minutes to binary target (spec §8).

    Returns:
        'RETRASO' if retraso_minutos >= 10, 'NO_RETRASO' otherwise,
        or None if retraso_minutos is None.
    """
    if retraso_minutos is None:
        return None
    return "RETRASO" if retraso_minutos >= 10 else "NO_RETRASO"


def select_cut_time_metar(
    metar_rows: list[dict[str, Any]],
    cut_epoch: float,
) -> dict[str, Any] | None:
    """Select the most recent METAR obs with obs_time <= cut_epoch.

    The *metar_rows* must be pre-sorted by obs_time ascending.
    This implements the data-leakage rule (spec §10): only weather
    information available *before* the scheduled departure is used.

    Args:
        metar_rows: List of dicts with at least 'obs_time' (int/float epoch).
        cut_epoch:  Scheduled departure time converted to UTC epoch seconds.

    Returns:
        The matching METAR dict, or None if no observation exists at/before cut.
    """
    best: dict[str, Any] | None = None
    for row in metar_rows:
        obs_time = row.get("obs_time")
        if obs_time is None:
            continue
        if float(obs_time) <= cut_epoch:
            best = row
        else:
            break  # ascending order — no more candidates after this
    return best


def scheduled_to_cut_epoch(scheduled_local: str) -> float:
    """Convert naive local scheduled time to UTC epoch for METAR cut.

    Uses Europe/Madrid timezone (spec §10).
    """
    local_dt = datetime.fromisoformat(scheduled_local).replace(tzinfo=MADRID_TZ)
    return local_dt.astimezone(UTC).timestamp()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_IATA_ICAO_FALLBACK_SQL = "SELECT icao_code FROM gold.airports WHERE iata_code = %s"


def _resolve_icao(iata: str, pg_conn: Any) -> str | None:
    """Resolve IATA airport code to ICAO, with DB fallback."""
    icao = get_icao_for_iata(iata)
    if icao:
        return icao
    try:
        cur = pg_conn.cursor()
        cur.execute(_IATA_ICAO_FALLBACK_SQL, (iata,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    except Exception:
        logger.warning("Could not resolve IATA→ICAO for %s via DB fallback", iata)
        return None


def _fetch_deduped_departures(pg_conn: Any) -> list[dict[str, Any]]:
    """Fetch latest-snapshot departures from gold.aena_infovuelos.

    Deduplicates by (flight_number, aena_airport_iata, flight_type, scheduled_local),
    keeping the row with max(snapshot_at_utc).
    """
    sql = """
        SELECT DISTINCT ON (flight_number, aena_airport_iata, flight_type, scheduled_local)
            flight_number,
            aena_airport_iata,
            flight_type,
            scheduled_local,
            estimated_local,
            airline_iata,
            other_airport_iata
        FROM gold.aena_infovuelos
        WHERE flight_type = 'departures'
        ORDER BY flight_number, aena_airport_iata, flight_type, scheduled_local,
                 snapshot_at_utc DESC
    """
    cur = pg_conn.cursor()
    cur.execute(sql)
    cols = [desc[0] for desc in cur.description]
    rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    cur.close()
    return rows


def _fetch_metar_for_airport(
    pg_conn: Any, icao_id: str, max_epoch: float,
) -> list[dict[str, Any]]:
    """Fetch METAR rows for an airport with obs_time <= max_epoch.

    Returns rows sorted by obs_time ascending (for efficient cut-time scan).
    Fetches a generous window (7 days before cut) to keep memory bounded.
    """
    min_epoch = int(max_epoch) - 7 * 86400  # 7-day lookback
    sql = """
        SELECT obs_time, temp, dewp, relh
        FROM gold.metar
        WHERE icao_id = %s AND obs_time >= %s AND obs_time <= %s
        ORDER BY obs_time ASC
    """
    cur = pg_conn.cursor()
    cur.execute(sql, (icao_id, min_epoch, int(max_epoch)))
    cols = [desc[0] for desc in cur.description]
    rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

PAGE_SIZE = 5000  # pagination for deduped departure read


def build_feature_store(
    dry_run: bool = False,
    reset: bool = False,
    force: bool = False,
) -> int:
    """Build gold.feature_store from AENA departures + METAR.

    Args:
        dry_run: Only count rows, don't insert.
        reset: Drop and recreate the table.
        force: Ignore checkpoint and process all history.

    Returns:
        Number of rows inserted.
    """
    pg_conn = _get_conn()

    # -- Checkpoint --
    checkpoint = get_checkpoint_set(CHECKPOINT_COLLECTION)
    if "done" in checkpoint and not force and not reset:
        logger.info(
            "Checkpoint exists: feature_store already built. "
            "Use --force to rebuild."
        )
        return 0

    # -- Reset / create DDL --
    if reset:
        logger.info("Resetting gold.feature_store...")
        with pg_conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS gold.feature_store CASCADE")
            cur.execute(FEATURE_STORE_DDL)
        pg_conn.commit()
        logger.info("Table recreated")
    else:
        # Ensure table exists
        with pg_conn.cursor() as cur:
            cur.execute(FEATURE_STORE_DDL)
        pg_conn.commit()

    # -- Ensure inference view --
    with pg_conn.cursor() as cur:
        cur.execute(INFERENCE_VIEW_SQL)
    pg_conn.commit()

    # -- Fetch deduped departures --
    logger.info("Fetching deduped departures from gold.aena_infovuelos...")
    departures = _fetch_deduped_departures(pg_conn)
    logger.info("Deduped departures: %d flights", len(departures))

    if not departures:
        logger.warning("No departure flights found in gold.aena_infovuelos")
        return 0

    # -- METAR cache: {icao_id: [rows]} — populated lazily per airport --
    metar_cache: dict[str, list[dict[str, Any]]] = {}

    # -- Build rows --
    insert_sql = """
        INSERT INTO gold.feature_store (
            aena_airport_iata, flight_number, flight_type, scheduled_local,
            hora_vuelo, dia_semana,
            airline_iata, other_airport_iata,
            temperatura_metar, punto_rocio_metar, relh,
            retraso_minutos, retraso_10_min
        ) VALUES %s
        ON CONFLICT (aena_airport_iata, flight_number, flight_type, scheduled_local)
        DO NOTHING
    """

    rows: list[tuple[Any, ...]] = []
    total_processed = 0
    skipped_no_sched = 0
    skipped_no_airport = 0

    for flight in departures:
        total_processed += 1
        if total_processed % 5000 == 0:
            logger.info("Processed %d flights...", total_processed)

        scheduled_local = flight.get("scheduled_local")
        airport_iata = flight.get("aena_airport_iata")
        flight_number = flight.get("flight_number")
        flight_type = flight.get("flight_type")

        if not scheduled_local or not airport_iata or not flight_number:
            skipped_no_sched += 1
            continue

        # -- Resolve ICAO for METAR join --
        icao = _resolve_icao(airport_iata, pg_conn)
        if not icao:
            skipped_no_airport += 1
            continue

        # -- Time features --
        try:
            sched_dt = datetime.fromisoformat(scheduled_local)
        except (ValueError, TypeError):
            skipped_no_sched += 1
            continue

        hora_vuelo = sched_dt.hour
        dia_semana = sched_dt.isoweekday()

        # -- Delay targets --
        estimated_local = flight.get("estimated_local")
        retraso = compute_retraso(scheduled_local, estimated_local)
        retraso_cat = compute_retraso_10_min(retraso)

        # -- METAR cut-time join --
        cut_epoch = scheduled_to_cut_epoch(scheduled_local)

        if icao not in metar_cache:
            metar_cache[icao] = _fetch_metar_for_airport(pg_conn, icao, cut_epoch)

        metar_rows = metar_cache[icao]
        metar = select_cut_time_metar(metar_rows, cut_epoch)

        temp = metar.get("temp") if metar else None
        dewp = metar.get("dewp") if metar else None
        relh = metar.get("relh") if metar else None

        rows.append((
            airport_iata,
            flight_number,
            flight_type,
            scheduled_local,
            hora_vuelo,
            dia_semana,
            flight.get("airline_iata"),
            flight.get("other_airport_iata"),
            temp,
            dewp,
            relh,
            retraso,
            retraso_cat,
        ))

    logger.info(
        "Processed %d flights (%d skipped: %d no schedule, %d no ICAO)",
        total_processed, skipped_no_sched + skipped_no_airport,
        skipped_no_sched, skipped_no_airport,
    )

    if dry_run:
        logger.info("Dry-run: %d rows ready to insert", len(rows))
        return 0

    if not rows:
        logger.warning("No rows to insert")
        return 0

    # -- Batch insert with retry --
    batch_size = 2000
    total_batches = (len(rows) + batch_size - 1) // batch_size
    inserted = 0
    max_retries = 3

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        batch_num = (i // batch_size) + 1

        for attempt in range(1, max_retries + 1):
            try:
                if pg_conn.closed:
                    pg_conn = _get_conn()
                with pg_conn.cursor() as cur:
                    execute_values(cur, insert_sql, batch, page_size=1000)
                pg_conn.commit()
                inserted += len(batch)
                logger.info(
                    "Inserted batch %d/%d (%d rows)...", batch_num, total_batches, len(batch)
                )
                break
            except psycopg2.OperationalError:
                logger.warning(
                    "Connection lost at batch %d, retry %d/%d",
                    batch_num, attempt, max_retries,
                )
                if attempt < max_retries:
                    pg_conn = _get_conn()
                else:
                    raise

    logger.info("Feature store: %d rows inserted", inserted)
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gold.feature_store")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate table")
    parser.add_argument("--dry-run", action="store_true", help="Only count rows")
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore checkpoint and process all history",
    )
    args = parser.parse_args()

    n = build_feature_store(dry_run=args.dry_run, reset=args.reset, force=args.force)

    if n > 0 and not args.dry_run:
        add_to_checkpoint_set(CHECKPOINT_COLLECTION, "done")
        logger.info("Checkpoint feature_store saved.")


if __name__ == "__main__":
    main()
