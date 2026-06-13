"""Build the gold.feature_store table.

Joins flights + schedules + aircraft + weather + gold aggregations
into a flat feature table ready for model training.

CLI:
    --reset     Drops and recreates the table
    --dry-run   Shows how many rows would be inserted
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient

from aeropredict.opensky.config import get_mongo_uri
from aeropredict.opensky.storage_gold import _get_conn
from aeropredict.sources.matcher import FlightScheduleMatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FEATURE_STORE_SQL = """
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


def _load_aircraft_lookup(mongo_db: Any) -> dict[str, dict[str, Any]]:
    """Carga todas las aeronaves en un dict {icao24: doc}."""
    lookup: dict[str, dict[str, Any]] = {}
    for doc in mongo_db["aircraft"].find({}, {
        "icao24": 1, "typecode": 1, "manufacturer": 1,
        "operator": 1, "first_flight_date": 1,
    }):
        lookup[doc["icao24"]] = doc
    logger.info("Aircraft lookup: %d registros", len(lookup))
    return lookup


def _load_weather_lookup(mongo_db: Any) -> dict[tuple[str, str], list[dict]]:
    """Carga weather en dict {(airport, date): [hourly_docs]}."""
    lookup: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for doc in mongo_db["weather"].find({}, {
        "airport_code": 1, "flight_date": 1, "timestamp": 1,
        "temperature_2m": 1, "precipitation": 1, "wind_speed_10m": 1,
        "visibility": 1,
    }):
        key = (doc["airport_code"], str(doc.get("flight_date", ""))[:10])
        lookup[key].append(doc)
    logger.info("Weather lookup: %d airport-dates", len(lookup))
    return dict(lookup)


def _load_gold_lookup(pg_conn: Any) -> dict[str, Any]:
    """Carga agregaciones gold en lookups."""
    cur = pg_conn.cursor()
    result: dict[str, Any] = {}

    # route_density
    route_counts: dict[tuple[str, str], int] = {}
    cur.execute("SELECT departure_airport, arrival_airport, flight_count FROM gold.route_density")
    for dep, arr, cnt in cur.fetchall():
        route_counts[(dep, arr)] = cnt
    result["route_density"] = route_counts

    # daily_airport_traffic
    daily_traffic: dict[tuple[str, str], int] = {}
    cur.execute(
        "SELECT airport_code, flight_date, arrivals_count + departures_count "
        "FROM gold.daily_airport_traffic"
    )
    for ap, dt, cnt in cur.fetchall():
        daily_traffic[(ap, str(dt))] = cnt
    result["daily_traffic"] = daily_traffic

    # hourly_distribution
    hourly_traffic: dict[tuple[str, str, int], int] = {}
    cur.execute(
        "SELECT airport_code, flight_date, hour, arrivals_count + departures_count "
        "FROM gold.hourly_distribution"
    )
    for ap, dt, hr, cnt in cur.fetchall():
        hourly_traffic[(ap, str(dt), hr)] = cnt
    result["hourly_traffic"] = hourly_traffic

    cur.close()
    return result


def _find_weather_hour(
    weather_docs: list[dict], hour: int,
) -> dict[str, Any] | None:
    """Encuentra el doc de weather más cercano a una hora específica."""
    if not weather_docs:
        return None
    # Buscar el que tenga timestamp más cercano a la hora
    for doc in weather_docs:
        ts = doc.get("timestamp", "")
        if isinstance(ts, str) and len(ts) >= 13:
            try:
                doc_hour = int(ts[11:13])
                if doc_hour == hour:
                    return doc
            except (ValueError, IndexError):
                pass
    # Fallback: primer doc
    return weather_docs[0]


def build_feature_store(
    dry_run: bool = False,
    reset: bool = False,
) -> int:
    """Construye gold.feature_store con todas las features.

    Args:
        dry_run: Solo contar filas, no insertar.
        reset: Dropear y recrear la tabla.

    Returns:
        Número de filas insertadas.
    """
    # -- Conexiones --
    mongo_client = MongoClient(get_mongo_uri())
    mongo_db = mongo_client.get_database()
    pg_conn = _get_conn()

    if reset:
        logger.info("Reseteando gold.feature_store...")
        with pg_conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS gold.feature_store CASCADE")
            cur.execute(FEATURE_STORE_SQL)
        pg_conn.commit()
        logger.info("Tabla recreada")

    # -- Cargar lookups --
    matcher = FlightScheduleMatcher(mongo_db)
    aircraft_lookup = _load_aircraft_lookup(mongo_db)
    weather_lookup = _load_weather_lookup(mongo_db)
    gold_lookup = _load_gold_lookup(pg_conn)

    route_density = gold_lookup.get("route_density", {})
    daily_traffic = gold_lookup.get("daily_traffic", {})
    hourly_traffic = gold_lookup.get("hourly_traffic", {})

    # -- Iterar vuelos --
    flights_cursor = mongo_db["flights"].find({}).batch_size(500)

    rows: list[tuple[Any, ...]] = []
    total_processed = 0

    for flight in flights_cursor:
        total_processed += 1
        if total_processed % 5000 == 0:
            logger.info("Procesados %d vuelos...", total_processed)

        icao24 = flight.get("icao24", "")
        callsign = flight.get("callsign")
        flight_date_raw = flight.get("flight_date")
        if not callsign or not flight_date_raw:
            continue

        if hasattr(flight_date_raw, "strftime"):
            flight_date = flight_date_raw.strftime("%Y-%m-%d")
        else:
            flight_date = str(flight_date_raw)[:10]

        dep = flight.get("est_departure_airport") or ""
        arr = flight.get("est_arrival_airport") or ""
        first_seen = flight.get("first_seen")
        last_seen = flight.get("last_seen")

        # airborne minutes
        airborne_minutes = None
        if isinstance(first_seen, datetime) and isinstance(last_seen, datetime):
            delta = last_seen - first_seen
            airborne_minutes = round(delta.total_seconds() / 60.0, 1)

        # Time features
        departure_hour = first_seen.hour if isinstance(first_seen, datetime) else None
        day_of_week = (
            flight_date_raw.isoweekday()
            if hasattr(flight_date_raw, "isoweekday") else None
        )
        month = flight_date_raw.month if hasattr(flight_date_raw, "month") else None

        # -- Match schedule --
        schedule = matcher.match_flight_to_schedule(flight)
        delay_minutes = None
        schedule_source = None
        if schedule:
            delay_minutes = matcher.compute_delay(flight, schedule)
            schedule_source = schedule.get("source")

        # -- Aircraft features --
        ac = aircraft_lookup.get(icao24.lower(), {})
        ac_type = ac.get("typecode") or ""
        ac_manufacturer = ac.get("manufacturer") or ""
        ac_operator = ac.get("operator") or ""
        ac_age = None
        ff_date = ac.get("first_flight_date")
        if ff_date and isinstance(first_seen, datetime):
            try:
                ff = datetime.strptime(str(ff_date)[:10], "%Y-%m-%d")
                ac_age = round((first_seen - ff.replace(tzinfo=UTC)).days / 365.25, 1)
            except (ValueError, TypeError):
                pass

        # -- Gold aggregations --
        route_key = (dep, arr)
        rtd = route_density.get(route_key)
        dep_key = (dep, flight_date)
        arr_key = (arr, flight_date)
        dep_hourly_key = (dep, flight_date, departure_hour) if departure_hour is not None else None
        arr_hourly_key = (arr, flight_date, departure_hour) if departure_hour is not None else None

        route_total_density = rtd if rtd is not None else 0
        route_daily_traffic = daily_traffic.get(dep_key, 0) + daily_traffic.get(arr_key, 0)
        dep_hourly_traffic = hourly_traffic.get(dep_hourly_key, 0) if dep_hourly_key else 0
        arr_hourly_traffic = hourly_traffic.get(arr_hourly_key, 0) if arr_hourly_key else 0

        # -- Weather features --
        dep_weather = _find_weather_hour(
            weather_lookup.get((dep, flight_date), []),
            departure_hour or 0,
        )
        arr_weather = _find_weather_hour(
            weather_lookup.get((arr, flight_date), []),
            departure_hour or 0,
        )

        dep_temp = dep_weather.get("temperature_2m") if dep_weather else None
        dep_precip = dep_weather.get("precipitation") if dep_weather else None
        dep_wind = dep_weather.get("wind_speed_10m") if dep_weather else None
        dep_vis = dep_weather.get("visibility") if dep_weather else None

        arr_temp = arr_weather.get("temperature_2m") if arr_weather else None
        arr_precip = arr_weather.get("precipitation") if arr_weather else None
        arr_wind = arr_weather.get("wind_speed_10m") if arr_weather else None
        arr_vis = arr_weather.get("visibility") if arr_weather else None

        rows.append((
            icao24, flight_date, callsign, dep or None, arr or None,
            delay_minutes, airborne_minutes,
            departure_hour, day_of_week, month,
            ac_type or None, ac_manufacturer or None, ac_operator or None,
            ac_age,
            route_daily_traffic or None, route_total_density or None,
            dep_hourly_traffic or None, arr_hourly_traffic or None,
            dep_temp, dep_precip, dep_wind, dep_vis,
            arr_temp, arr_precip, arr_wind, arr_vis,
            schedule_source,
        ))

    mongo_client.close()

    if dry_run:
        logger.info("Dry-run: %d filas listas para insertar", len(rows))
        return 0

    if not rows:
        logger.warning("No hay filas para insertar")
        return 0

    # -- Insert a PostgreSQL --
    insert_sql = """
        INSERT INTO gold.feature_store (
            icao24, flight_date, callsign, departure_airport, arrival_airport,
            delay_minutes, airborne_minutes,
            departure_hour, day_of_week, month,
            aircraft_type, aircraft_manufacturer, aircraft_operator, aircraft_age_years,
            route_daily_traffic, route_total_density,
            departure_airport_hourly_traffic, arrival_airport_hourly_traffic,
            dep_temperature, dep_precipitation, dep_wind_speed, dep_visibility,
            arr_temperature, arr_precipitation, arr_wind_speed, arr_visibility,
            schedule_source
        ) VALUES %s
        ON CONFLICT (icao24, flight_date) DO NOTHING
    """

    from psycopg2.extras import execute_values
    with pg_conn.cursor() as cur:
        execute_values(cur, insert_sql, rows, page_size=1000)
    pg_conn.commit()

    logger.info("Feature store: %d filas insertadas", len(rows))
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gold.feature_store")
    parser.add_argument("--reset", action="store_true", help="Drop y recreate table")
    parser.add_argument("--dry-run", action="store_true", help="Solo contar filas")
    args = parser.parse_args()

    build_feature_store(dry_run=args.dry_run, reset=args.reset)


if __name__ == "__main__":
    main()
