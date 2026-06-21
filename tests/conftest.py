"""Reusable pytest fixtures for the Aeropredict test suite.

Fixtures:
    mongo_client: Session-scoped MongoDB client (Docker local).
    postgres_client: Session-scoped PostgreSQL connection (Docker local).
    mock_opensky_data: Function-scoped mock flight data from JSON files.
    delta_lake_manager: Function-scoped temp directory for Delta Lake writes.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import shutil
from collections.abc import Generator
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extensions
import pymongo
import pytest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOCK_DIR = Path("data/mock/opensky")
MONGO_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
PG_URI = os.environ.get(
    "POSTGRES_URI",
    "postgresql://aeropredict:aeropredict@localhost:5432/aeropredict",
)

# Gold schema DDL used by the project — replicated here for test isolation.
GOLD_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS gold.daily_airport_traffic (
    airport_code    VARCHAR(4) NOT NULL,
    flight_date     DATE NOT NULL,
    arrivals_count  INTEGER NOT NULL DEFAULT 0,
    departures_count INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (airport_code, flight_date)
);

CREATE TABLE IF NOT EXISTS gold.route_density (
    departure_airport VARCHAR(4) NOT NULL,
    arrival_airport   VARCHAR(4) NOT NULL,
    flight_count      INTEGER NOT NULL DEFAULT 0,
    first_seen        DATE,
    last_seen         DATE,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (departure_airport, arrival_airport)
);

CREATE TABLE IF NOT EXISTS gold.hourly_distribution (
    airport_code    VARCHAR(4) NOT NULL,
    flight_date     DATE NOT NULL,
    hour            SMALLINT NOT NULL,
    arrivals_count  INTEGER NOT NULL DEFAULT 0,
    departures_count INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (airport_code, flight_date, hour)
);

CREATE TABLE IF NOT EXISTS gold.flights (
    id                  SERIAL PRIMARY KEY,
    icao24              VARCHAR(6) NOT NULL,
    callsign            VARCHAR(10),
    first_seen          TIMESTAMPTZ,
    last_seen           TIMESTAMPTZ,
    flight_date         DATE NOT NULL,
    est_departure_airport        VARCHAR(4),
    est_arrival_airport          VARCHAR(4),
    departure_airport_horiz_distance FLOAT,
    departure_airport_vert_distance  FLOAT,
    arrival_airport_horiz_distance   FLOAT,
    arrival_airport_vert_distance    FLOAT,
    departure_airport_candidates_count INTEGER,
    arrival_airport_candidates_count   INTEGER,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (icao24, flight_date, first_seen)
);

CREATE TABLE IF NOT EXISTS gold.aircraft (
    icao24              VARCHAR(12) NOT NULL PRIMARY KEY,
    typecode            VARCHAR(30),
    manufacturer        VARCHAR(150),
    operator            VARCHAR(100),
    first_flight_date   DATE,
    icao_aircraft_type  VARCHAR(20),
    registration        VARCHAR(20),
    serial_number       VARCHAR(50),
    tracked             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gold.weather (
    id                  SERIAL PRIMARY KEY,
    airport_code        VARCHAR(4) NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    flight_date         DATE NOT NULL,
    temperature_2m      FLOAT,
    precipitation       FLOAT,
    wind_speed_10m      FLOAT,
    wind_gusts_10m      FLOAT,
    visibility          FLOAT,
    cloud_cover         FLOAT,
    relative_humidity_2m FLOAT,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (airport_code, timestamp)
);

CREATE TABLE IF NOT EXISTS gold.feature_store (
    icao24                      VARCHAR(6) NOT NULL,
    flight_date                 DATE NOT NULL,
    callsign                    VARCHAR(10),
    departure_airport           VARCHAR(4),
    arrival_airport             VARCHAR(4),
    delay_minutes               FLOAT,
    airborne_minutes            FLOAT,
    departure_hour              INTEGER,
    day_of_week                 INTEGER,
    month                       INTEGER,
    aircraft_type               VARCHAR(30),
    aircraft_manufacturer       VARCHAR(150),
    aircraft_operator           VARCHAR(100),
    aircraft_age_years          FLOAT,
    route_daily_traffic         INTEGER,
    route_total_density         INTEGER,
    departure_airport_hourly_traffic INTEGER,
    arrival_airport_hourly_traffic   INTEGER,
    dep_temperature             FLOAT,
    dep_precipitation           FLOAT,
    dep_wind_speed              FLOAT,
    dep_visibility              FLOAT,
    arr_temperature             FLOAT,
    arr_precipitation           FLOAT,
    arr_wind_speed              FLOAT,
    arr_visibility              FLOAT,
    schedule_source             VARCHAR(20),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (icao24, flight_date)
);
"""


# ---------------------------------------------------------------------------
# MongoDB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mongo_client() -> Generator[pymongo.MongoClient[Any], None, None]:
    """Session-scoped MongoDB client connected to local Docker.

    Uses ``aeropredict_test`` database to isolate test data.
    Skips the test if MongoDB is unreachable.
    """
    client: pymongo.MongoClient[Any] | None = None
    try:
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        logger.info("MongoDB connected: %s", MONGO_URI)
    except pymongo.errors.ConnectionFailure:
        pytest.skip(f"MongoDB not available at {MONGO_URI}")

    db_name = "aeropredict_test"
    yield client  # type: ignore[misc]

    if client is not None:
        client.drop_database(db_name)
        client.close()


# ---------------------------------------------------------------------------
# PostgreSQL fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_client() -> Generator[psycopg2.extensions.connection, None, None]:
    """Session-scoped PostgreSQL connection to local Docker.

    Creates the ``gold`` schema and tables on setup, drops them on teardown.
    Skips the test if PostgreSQL is unreachable.
    """
    conn: psycopg2.extensions.connection | None = None
    try:
        conn = psycopg2.connect(PG_URI, connect_timeout=2)
        conn.autocommit = True
        logger.info("PostgreSQL connected: %s", PG_URI)
    except psycopg2.OperationalError:
        pytest.skip(f"PostgreSQL not available at {PG_URI}")

    # Create the gold schema with all tables
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute(GOLD_SCHEMA_SQL)

    yield conn  # type: ignore[misc]

    # Teardown: drop the gold schema
    if conn is not None and not conn.closed:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS gold CASCADE;")
        conn.close()


# ---------------------------------------------------------------------------
# Mock OpenSky data fixture
# ---------------------------------------------------------------------------


def _load_mock_flight_files(mock_dir: Path) -> list[dict[str, Any]]:
    """Walk ``mock_dir`` and load all JSON flight files.

    Returns:
        Combined list of flight dicts from all JSON files.
    """
    all_flights: list[dict[str, Any]] = []
    if not mock_dir.is_dir():
        logger.warning("Mock directory not found: %s", mock_dir)
        return all_flights

    for json_path in sorted(mock_dir.rglob("*.json")):
        try:
            with open(json_path) as f:
                data = json.load(f)
            if isinstance(data, list):
                all_flights.extend(data)
            else:
                logger.debug("Skipping non-list JSON: %s", json_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load %s: %s", json_path, exc)

    return all_flights


@pytest.fixture(scope="function")
def mock_opensky_data() -> list[dict[str, Any]]:
    """Function-scoped fixture yielding mock flight data.

    Loads all ``*.json`` files from ``data/mock/opensky/`` recursively.
    Returns an empty list if no mock data is found.
    """
    return _load_mock_flight_files(MOCK_DIR)


# ---------------------------------------------------------------------------
# Delta Lake temp directory fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def delta_lake_manager() -> Generator[str, None, None]:
    """Function-scoped fixture providing a temporary Delta Lake root.

    The temp directory is cleaned up after each test function completes.
    """
    tmp_dir = tempfile.mkdtemp(prefix="aeropredict_delta_")
    try:
        yield tmp_dir
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
