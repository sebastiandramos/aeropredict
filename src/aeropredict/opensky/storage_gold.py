"""Capa gold — PostgreSQL para agregaciones analíticas.

Las tablas se crean automáticamente bajo el esquema ``gold``.

Tablas entidad (raw desde MongoDB):
    ``flights``
        Vuelos raw. Se sincroniza desde la colección ``flights`` de MongoDB.

    ``aircraft``
        Aeronaves con metadatos (fabricante, operador, tipo, antigüedad).
        Se sincroniza desde la colección ``aircraft`` de MongoDB.

    ``weather``
        Datos meteorológicos horarios por aeropuerto.
        Se sincroniza desde la colección ``weather`` de MongoDB.

    ``aena_infovuelos``
        Vuelos infovuelos de AENA por aeropuerto y snapshot.
        Se sincroniza desde la colección ``aena_infovuelos`` de MongoDB.

    ``metar``
        Informes METAR (NOAA AWC) por aeropuerto.
        Se sincroniza desde la colección ``metar`` de MongoDB.

    ``holidays``
        Festivos de España (Nager.Date + python-holidays).
        Se sincroniza desde la colección ``holidays`` de MongoDB.

    ``eurocontrol_pru``
        Filas CSV de EUROCONTROL PRU (cada fila como JSONB).
        Se sincroniza desde la colección ``eurocontrol_pru`` de MongoDB.

    ``notam``
        Features NOTAM de ENAIRE servAIS (GeoJSON como JSONB).
        Se sincroniza desde la colección ``notam`` de MongoDB.

    ``airports``
        Aeropuertos de OurAirports (maestra).
        Se sincroniza desde la colección ``airports`` de MongoDB.

    ``runways``
        Pistas de OurAirports (maestra).
        Se sincroniza desde la colección ``runways`` de MongoDB.

Tablas agregadas (desde flights):
    ``daily_airport_traffic``
        Vuelos por aeropuerto y día (arrivals / departures).
        Útil para identificar días punta, estacionalidad, etc.

    ``route_density``
        Pares origen-destino con frecuencia acumulada.
        Útil para análisis de rutas y predicción de demanda.

    ``hourly_distribution``
        Vuelos por aeropuerto, día y hora.
        Útil para patrones horarios y ventanas de slot.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

from .config import get_postgres_uri
from .models import Flight

logger = logging.getLogger(__name__)

# Conexión perezosa
_conn: Any = None

SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_flights_icao24_date ON gold.flights (icao24, flight_date);
CREATE INDEX IF NOT EXISTS idx_flights_date ON gold.flights (flight_date);

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

-- Migración: ampliar columnas en tablas existentes
ALTER TABLE gold.aircraft ALTER COLUMN icao24 TYPE VARCHAR(12);
ALTER TABLE gold.aircraft ALTER COLUMN icao_aircraft_type TYPE VARCHAR(20);

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

CREATE INDEX IF NOT EXISTS idx_weather_airport_date ON gold.weather (airport_code, flight_date);

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

CREATE TABLE IF NOT EXISTS gold.aena_infovuelos (
    id                  SERIAL PRIMARY KEY,
    snapshot_at_utc     TIMESTAMPTZ NOT NULL,
    flight_number       VARCHAR(20),
    aena_airport_iata   VARCHAR(4) NOT NULL,
    flight_type         VARCHAR(20) NOT NULL,
    source              VARCHAR(30),
    query_airport_iata  VARCHAR(4),
    query_flight_type   VARCHAR(20),
    raw_flight_number   VARCHAR(20),
    airline_iata        VARCHAR(4),
    airline_icao        VARCHAR(4),
    airline_name        VARCHAR(100),
    icao24_airport      VARCHAR(10),
    other_airport_iata  VARCHAR(4),
    other_city          VARCHAR(100),
    scheduled_date      VARCHAR(20),
    scheduled_time      VARCHAR(20),
    scheduled_local     VARCHAR(30),
    estimated_date      VARCHAR(20),
    estimated_time      VARCHAR(20),
    estimated_local     VARCHAR(30),
    status              VARCHAR(50),
    terminal            VARCHAR(10),
    gate_first          VARCHAR(20),
    gate_second         VARCHAR(20),
    checkin_from        VARCHAR(20),
    checkin_to          VARCHAR(20),
    aircraft_type       VARCHAR(50),
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_at_utc, flight_number, aena_airport_iata, flight_type, scheduled_local)
);

CREATE INDEX IF NOT EXISTS idx_aena_infovuelos_airport_date
    ON gold.aena_infovuelos (aena_airport_iata, snapshot_at_utc);

CREATE TABLE IF NOT EXISTS gold.metar (
    icao_id        VARCHAR(8) NOT NULL,
    raw_ob         TEXT,
    receipt_time   TIMESTAMPTZ,
    obs_time       BIGINT NOT NULL,
    temp           FLOAT,
    dewp           FLOAT,
    wdir           INTEGER,
    wspd           INTEGER,
    wgst           INTEGER,
    visib          VARCHAR(16),
    altim          FLOAT,
    flt_cat        VARCHAR(8),
    clouds_base    INTEGER,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (icao_id, obs_time)
);

CREATE INDEX IF NOT EXISTS idx_metar_icao_obs ON gold.metar (icao_id, obs_time);

CREATE TABLE IF NOT EXISTS gold.holidays (
    id            SERIAL PRIMARY KEY,
    date          DATE NOT NULL,
    name          VARCHAR(200) NOT NULL,
    local_name    VARCHAR(200) NOT NULL DEFAULT '',
    country_code  VARCHAR(8) NOT NULL,
    is_global     BOOLEAN,
    counties      TEXT[],
    types         TEXT[],
    source        VARCHAR(32) NOT NULL,
    subdivision   VARCHAR(16) NOT NULL DEFAULT '',
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, name, country_code, source, subdivision)
);

CREATE INDEX IF NOT EXISTS idx_holidays_date_source ON gold.holidays (date, source);

CREATE TABLE IF NOT EXISTS gold.eurocontrol_pru (
    id            SERIAL PRIMARY KEY,
    source_file   VARCHAR(64) NOT NULL,
    year          INTEGER NOT NULL,
    row_json      JSONB NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_file, year, row_json)
);

CREATE INDEX IF NOT EXISTS idx_eurocontrol_file_year
    ON gold.eurocontrol_pru (source_file, year);

CREATE TABLE IF NOT EXISTS gold.notam (
    id            SERIAL PRIMARY KEY,
    layer         INTEGER NOT NULL,
    snapshot_at   TIMESTAMPTZ,
    feature_json  JSONB NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (layer, feature_json)
);

CREATE INDEX IF NOT EXISTS idx_notam_snapshot_layer ON gold.notam (snapshot_at, layer);

CREATE TABLE IF NOT EXISTS gold.airports (
    ident          VARCHAR(8) PRIMARY KEY,
    type           VARCHAR(32),
    name           VARCHAR(200),
    latitude_deg   FLOAT,
    longitude_deg  FLOAT,
    elevation_ft   FLOAT,
    iso_country    VARCHAR(8),
    iso_region     VARCHAR(16),
    municipality   VARCHAR(100),
    iata_code      VARCHAR(8),
    icao_code      VARCHAR(8),
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_airports_iso_country ON gold.airports (iso_country);

CREATE TABLE IF NOT EXISTS gold.runways (
    airport_ident  VARCHAR(8) NOT NULL,
    length_ft      FLOAT,
    width_ft       FLOAT,
    surface        VARCHAR(32),
    le_ident       VARCHAR(8) NOT NULL,
    he_ident       VARCHAR(8) NOT NULL,
    le_heading_degT FLOAT,
    he_heading_degT FLOAT,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (airport_ident, le_ident, he_ident)
);

CREATE INDEX IF NOT EXISTS idx_runways_surface ON gold.runways (surface);
"""


def _get_conn():
    """Conecta a PostgreSQL y crea tablas si no existen."""
    global _conn
    if _conn is None or _conn.closed:
        uri = get_postgres_uri()
        logger.debug("Conectando a PostgreSQL: %s", uri)
        _conn = psycopg2.connect(uri)
        _conn.autocommit = True
        with _conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    return _conn


def close() -> None:
    """Cierra la conexión a PostgreSQL."""
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
    _conn = None


# ===================================================================
# Gold — actualizaciones desde lista de vuelos
# ===================================================================


def write_flights_gold(flights: list[Flight]) -> dict[str, int]:
    """Actualiza las tablas gold a partir de una lista de vuelos.

    Args:
        flights: Lista de objetos Flight (recién extraídos).

    Returns:
        Dict con filas afectadas por tabla.
    """
    if not flights:
        return {"daily_airport_traffic": 0, "route_density": 0, "hourly_distribution": 0}

    conn = _get_conn()

    # Agregar antes de insertar para evitar duplicados en ON CONFLICT
    daily_agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    route_agg: dict[tuple[str, str], list[int | str | None]] = {}
    hourly_agg: dict[tuple[str, str, int], list[int]] = defaultdict(lambda: [0, 0])

    for f in flights:
        flight_date = f.first_seen.date() if f.first_seen else None
        if flight_date is None:
            continue
        fd_str = flight_date.isoformat()
        dep = f.est_departure_airport
        arr = f.est_arrival_airport
        hour = f.first_seen.hour if f.first_seen else 0

        # daily_airport_traffic
        if dep:
            v = daily_agg[(dep, fd_str)]
            v[1] += 1  # departures_count
        if arr:
            v = daily_agg[(arr, fd_str)]
            v[0] += 1  # arrivals_count

        # route_density
        if dep and arr:
            key = (dep, arr)
            if key in route_agg:
                r = route_agg[key]
                r[0] = int(r[0]) + 1  # type: ignore[arg-type]
                if flight_date < r[1]:
                    r[1] = flight_date
                if flight_date > r[2]:
                    r[2] = flight_date
            else:
                route_agg[key] = [1, flight_date, flight_date]

        # hourly_distribution
        if dep:
            v = hourly_agg[(dep, fd_str, hour)]
            v[1] += 1  # departures_count
        if arr:
            v = hourly_agg[(arr, fd_str, hour)]
            v[0] += 1  # arrivals_count

    # Aplanar agregaciones
    daily_rows: list[tuple[str, str, int, int]] = [
        (k[0], k[1], v[0], v[1]) for k, v in daily_agg.items()
    ]
    route_rows: list[tuple[str, str, int, str, str]] = [
        (k[0], k[1], v[0], v[1].isoformat(), v[2].isoformat())
        for k, v in route_agg.items()
    ]
    hourly_rows: list[tuple[str, str, int, int, int]] = [
        (k[0], k[1], k[2], v[0], v[1]) for k, v in hourly_agg.items()
    ]

    counts: dict[str, int] = {}

    if daily_rows:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO gold.daily_airport_traffic
                (airport_code, flight_date, arrivals_count, departures_count)
                VALUES %s
                ON CONFLICT (airport_code, flight_date) DO UPDATE SET
                    arrivals_count = gold.daily_airport_traffic.arrivals_count
                        + EXCLUDED.arrivals_count,
                    departures_count = gold.daily_airport_traffic.departures_count
                        + EXCLUDED.departures_count,
                    updated_at = NOW()
                """,
                daily_rows,
                template="(%s, %s::date, %s, %s)",
            )
            counts["daily_airport_traffic"] = len(daily_rows)

    if route_rows:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO gold.route_density
                (departure_airport, arrival_airport, flight_count, first_seen, last_seen)
                VALUES %s
                ON CONFLICT (departure_airport, arrival_airport) DO UPDATE SET
                    flight_count = gold.route_density.flight_count + EXCLUDED.flight_count,
                    first_seen = LEAST(gold.route_density.first_seen, EXCLUDED.first_seen),
                    last_seen = GREATEST(gold.route_density.last_seen, EXCLUDED.last_seen),
                    updated_at = NOW()
                """,
                route_rows,
                template="(%s, %s, %s, %s::date, %s::date)",
            )
            counts["route_density"] = len(route_rows)

    if hourly_rows:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO gold.hourly_distribution
                (airport_code, flight_date, hour, arrivals_count, departures_count)
                VALUES %s
                ON CONFLICT (airport_code, flight_date, hour) DO UPDATE SET
                    arrivals_count = gold.hourly_distribution.arrivals_count
                        + EXCLUDED.arrivals_count,
                    departures_count = gold.hourly_distribution.departures_count
                        + EXCLUDED.departures_count,
                    updated_at = NOW()
                """,
                hourly_rows,
                template="(%s, %s::date, %s, %s, %s)",
            )
            counts["hourly_distribution"] = len(hourly_rows)

    logger.info("Gold: %s", counts)
    return counts


# ===================================================================
# Gold — entidades (sync desde MongoDB)
# ===================================================================


def write_flights_gold_raw(flight_docs: list[dict[str, Any]]) -> int:
    """Inserta vuelos raw en gold.flights.

    Lee documentos tal cual desde la colección ``flights`` de MongoDB
    y los escribe en la tabla tabular ``gold.flights``.
    Omite duplicados basándose en (icao24, flight_date, first_seen, last_seen).

    Args:
        flight_docs: Lista de documentos de MongoDB (colección flights).

    Returns:
        Número de filas insertadas.
    """
    if not flight_docs:
        return 0

    rows: list[tuple[Any, ...]] = []
    for doc in flight_docs:
        fd = doc.get("flight_date")
        if fd and hasattr(fd, "strftime"):
            flight_date = fd.strftime("%Y-%m-%d")
        elif fd:
            flight_date = str(fd)[:10]
        else:
            continue

        rows.append((
            doc.get("icao24", ""),
            doc.get("callsign"),
            doc.get("first_seen"),
            doc.get("last_seen"),
            flight_date,
            doc.get("est_departure_airport"),
            doc.get("est_arrival_airport"),
            doc.get("departure_airport_horiz_distance"),
            doc.get("departure_airport_vert_distance"),
            doc.get("arrival_airport_horiz_distance"),
            doc.get("arrival_airport_vert_distance"),
            doc.get("departure_airport_candidates_count"),
            doc.get("arrival_airport_candidates_count"),
        ))

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.flights
            (icao24, callsign, first_seen, last_seen, flight_date,
             est_departure_airport, est_arrival_airport,
             departure_airport_horiz_distance, departure_airport_vert_distance,
             arrival_airport_horiz_distance, arrival_airport_vert_distance,
             departure_airport_candidates_count, arrival_airport_candidates_count)
            VALUES %s
            ON CONFLICT (icao24, flight_date, first_seen) DO NOTHING
            """,
            rows,
            template=(
                "(%s, %s, %s::timestamptz, %s::timestamptz, %s::date,"
                " %s, %s, %s, %s, %s, %s, %s, %s)"
            ),
            page_size=500,
        )
    conn.commit()
    logger.info("Gold flights raw: %d filas insertadas", len(rows))
    return len(rows)


def _parse_aircraft_date(raw: Any) -> str | None:
    """Valida que un valor sea una fecha ISO (YYYY-MM-DD) o None."""
    if not raw or not isinstance(raw, str):
        return None
    stripped = raw.strip()[:10]
    try:
        datetime.strptime(stripped, "%Y-%m-%d")
        return stripped
    except (ValueError, IndexError):
        return None


def _trunc(val: Any, maxlen: int) -> str | None:
    """Trunca un valor string a maxlen caracteres, o None si es vacío."""
    if not val:
        return None
    s = str(val).strip()
    return s[:maxlen] if s else None


def _safe_float(val: Any) -> float | None:
    """Convierte a float, o None si el valor no es convertible."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> int | None:
    """Convierte a int, o None si el valor no es convertible."""
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_iso_timestamp(raw: Any) -> datetime | None:
    """Parsea un string ISO 8601 a datetime, o None si no es válido."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def write_aircraft_gold(aircraft_list: list[dict[str, Any]]) -> int:
    """Upsert de aeronaves en gold.aircraft (batch via execute_values).

    Cada documento se identifica por ``icao24``.
    Si ya existe, se actualizan los metadatos.

    Args:
        aircraft_list: Lista de dicts con al menos ``icao24``.

    Returns:
        Número de filas insertadas/actualizadas.
    """
    if not aircraft_list:
        return 0

    rows: list[tuple[Any, ...]] = []
    for doc in aircraft_list:
        rows.append((
            _trunc(doc.get("icao24"), 12) or "",
            _trunc(doc.get("typecode"), 30),
            _trunc(doc.get("manufacturer"), 150),
            _trunc(doc.get("operator"), 100),
            _parse_aircraft_date(doc.get("first_flight_date")),
            _trunc(doc.get("icao_aircraft_type"), 20),
            _trunc(doc.get("registration"), 20),
            _trunc(doc.get("serial_number"), 50),
        ))

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.aircraft
            (icao24, typecode, manufacturer, operator,
             first_flight_date, icao_aircraft_type, registration, serial_number)
            VALUES %s
            ON CONFLICT (icao24) DO UPDATE SET
                typecode           = EXCLUDED.typecode,
                manufacturer       = EXCLUDED.manufacturer,
                operator           = EXCLUDED.operator,
                first_flight_date  = EXCLUDED.first_flight_date,
                icao_aircraft_type = EXCLUDED.icao_aircraft_type,
                registration       = EXCLUDED.registration,
                serial_number      = EXCLUDED.serial_number,
                tracked            = NOW()
            """,
            rows,
            template="(%s, %s, %s, %s, %s::date, %s, %s, %s)",
            page_size=500,
        )
    conn.commit()
    logger.info("Gold aircraft: %d upsertados", len(rows))
    return len(rows)


def write_weather_gold(weather_list: list[dict[str, Any]]) -> int:
    """Inserta datos meteorológicos en gold.weather.

    Args:
        weather_list: Lista de dicts con datos horarios.

    Returns:
        Número de filas insertadas.
    """
    if not weather_list:
        return 0

    rows: list[tuple[Any, ...]] = []
    for doc in weather_list:
        rows.append((
            doc.get("airport_code"),
            doc.get("timestamp"),
            doc.get("flight_date"),
            doc.get("temperature_2m"),
            doc.get("precipitation"),
            doc.get("wind_speed_10m"),
            doc.get("wind_gusts_10m"),
            doc.get("visibility"),
            doc.get("cloud_cover"),
            doc.get("relative_humidity_2m"),
        ))

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.weather
            (airport_code, timestamp, flight_date,
             temperature_2m, precipitation, wind_speed_10m,
             wind_gusts_10m, visibility, cloud_cover, relative_humidity_2m)
            VALUES %s
            ON CONFLICT (airport_code, timestamp) DO NOTHING
            """,
            rows,
            template="(%s, %s::timestamptz, %s::date, %s, %s, %s, %s, %s, %s, %s)",
            page_size=500,
        )
    conn.commit()
    logger.info("Gold weather: %d filas insertadas", len(rows))
    return len(rows)


# ===================================================================
# Gold — AENA Infovuelos (sync desde MongoDB)
# ===================================================================


def write_aena_infovuelos_gold(docs: list[dict[str, Any]]) -> int:
    """Inserta documentos infovuelos de AENA en gold.aena_infovuelos.

    Args:
        docs: Lista de dicts normalizados desde MongoDB (colección aena_infovuelos).

    Returns:
        Número de filas insertadas.
    """
    if not docs:
        return 0

    rows: list[tuple[Any, ...]] = []
    n_skipped = 0
    for doc in docs:
        flight_number = _trunc(doc.get("flight_number"), 20)
        aena_airport_iata = _trunc(doc.get("aena_airport_iata"), 4)
        if not flight_number or not aena_airport_iata:
            n_skipped += 1
            continue
        rows.append((
            doc.get("snapshot_at_utc"),
            flight_number,
            aena_airport_iata,
            doc.get("flight_type"),
            doc.get("source"),
            doc.get("query_airport_iata"),
            doc.get("query_flight_type"),
            doc.get("raw_flight_number"),
            doc.get("airline_iata"),
            doc.get("airline_icao"),
            doc.get("airline_name"),
            doc.get("icao24_airport"),
            doc.get("other_airport_iata"),
            doc.get("other_city"),
            doc.get("scheduled_date"),
            doc.get("scheduled_time"),
            doc.get("scheduled_local"),
            doc.get("estimated_date"),
            doc.get("estimated_time"),
            doc.get("estimated_local"),
            doc.get("status"),
            doc.get("terminal"),
            doc.get("gate_first"),
            doc.get("gate_second"),
            doc.get("checkin_from"),
            doc.get("checkin_to"),
            doc.get("aircraft_type"),
        ))

    if n_skipped:
        logger.warning(
            "Skipped %d AENA rows missing flight_number or aena_airport_iata",
            n_skipped,
        )

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.aena_infovuelos
            (snapshot_at_utc, flight_number, aena_airport_iata, flight_type,
             source, query_airport_iata, query_flight_type, raw_flight_number,
             airline_iata, airline_icao, airline_name, icao24_airport,
             other_airport_iata, other_city,
             scheduled_date, scheduled_time, scheduled_local,
             estimated_date, estimated_time, estimated_local,
             status, terminal, gate_first, gate_second,
             checkin_from, checkin_to, aircraft_type)
            VALUES %s
            ON CONFLICT (
                snapshot_at_utc, flight_number, aena_airport_iata,
                flight_type, scheduled_local
            )
            DO NOTHING
            """,
            rows,
            template=(
                "(%s::timestamptz, %s, %s, %s,"
                " %s, %s, %s, %s,"
                " %s, %s, %s, %s,"
                " %s, %s,"
                " %s, %s, %s,"
                " %s, %s, %s,"
                " %s, %s, %s, %s,"
                " %s, %s, %s)"
            ),
            page_size=500,
        )
    conn.commit()
    logger.info("Gold AENA infovuelos: %d filas insertadas", len(rows))
    return len(rows)


# ===================================================================
# Gold — nuevas fuentes (sync desde MongoDB)
# metar, holidays, eurocontrol_pru, notam, airports, runways
# ===================================================================


def write_metar_gold(metar_reports: list[dict[str, Any]]) -> int:
    """Inserta informes METAR en gold.metar.

    Se omite filas sin ``icao_id`` o ``obs_time``.

    Args:
        metar_reports: Lista de dicts desde MongoDB (colección metar).

    Returns:
        Número de filas insertadas.
    """
    if not metar_reports:
        return 0

    rows: list[tuple[Any, ...]] = []
    n_skipped = 0
    for doc in metar_reports:
        icao_id = _trunc(doc.get("icao_id"), 8)
        obs_time = _safe_int(doc.get("obs_time"))
        if not icao_id or obs_time is None:
            n_skipped += 1
            continue
        rows.append((
            icao_id,
            doc.get("raw_ob"),
            _parse_iso_timestamp(doc.get("receipt_time")),
            obs_time,
            _safe_float(doc.get("temp")),
            _safe_float(doc.get("dewp")),
            _safe_int(doc.get("wdir")),
            _safe_int(doc.get("wspd")),
            _safe_int(doc.get("wgst")),
            _trunc(doc.get("visib"), 16),
            _safe_float(doc.get("altim")),
            _trunc(doc.get("flt_cat"), 8),
            _safe_int(doc.get("clouds_base")),
        ))

    if n_skipped:
        logger.warning(
            "Skipped %d METAR rows missing icao_id or obs_time",
            n_skipped,
        )

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.metar
            (icao_id, raw_ob, receipt_time, obs_time,
             temp, dewp, wdir, wspd, wgst, visib, altim, flt_cat, clouds_base)
            VALUES %s
            ON CONFLICT (icao_id, obs_time) DO NOTHING
            """,
            rows,
            template=(
                "(%s, %s, %s::timestamptz, %s,"
                " %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ),
            page_size=500,
        )
    conn.commit()
    logger.info("Gold metar: %d filas insertadas", len(rows))
    return len(rows)


def write_holidays_gold(holidays: list[dict[str, Any]]) -> int:
    """Inserta festivos de España en gold.holidays.

    Se omite filas sin los campos obligatorios (date, name, country_code,
    source) ya que las columnas son NOT NULL.

    Args:
        holidays: Lista de dicts desde MongoDB (colección holidays).

    Returns:
        Número de filas insertadas.
    """
    if not holidays:
        return 0

    rows: list[tuple[Any, ...]] = []
    n_skipped = 0
    for doc in holidays:
        date = str(doc.get("date"))[:10] if doc.get("date") else None
        name = _trunc(doc.get("name"), 200)
        country_code = _trunc(doc.get("country_code"), 8)
        source = _trunc(doc.get("source"), 32)
        if not date or not name or not country_code or not source:
            n_skipped += 1
            continue
        rows.append((
            date,
            name,
            _trunc(doc.get("local_name"), 200) or "",
            country_code,
            doc.get("is_global"),
            doc.get("counties") or [],
            doc.get("types") or [],
            source,
            _trunc(doc.get("subdivision"), 16) or "",
        ))

    if n_skipped:
        logger.warning(
            "Skipped %d holidays rows missing date, name, country_code or source",
            n_skipped,
        )

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.holidays
            (date, name, local_name, country_code, is_global,
             counties, types, source, subdivision)
            VALUES %s
            ON CONFLICT (date, name, country_code, source, subdivision) DO NOTHING
            """,
            rows,
            template="(%s::date, %s, %s, %s, %s, %s::text[], %s::text[], %s, %s)",
            page_size=500,
        )
    conn.commit()
    logger.info("Gold holidays: %d filas insertadas", len(rows))
    return len(rows)


def write_eurocontrol_pru_gold(rows_in: list[dict[str, Any]]) -> int:
    """Inserta filas CSV de EUROCONTROL PRU en gold.eurocontrol_pru.

    Cada documento (de columnas dinámicas) se serializa como JSONB.
    Se omite filas sin ``source_file`` o ``year``.

    Args:
        rows_in: Lista de dicts desde MongoDB (colección eurocontrol_pru).

    Returns:
        Número de filas insertadas.
    """
    if not rows_in:
        return 0

    rows: list[tuple[Any, ...]] = []
    n_skipped = 0
    for doc in rows_in:
        source_file = _trunc(doc.get("source_file"), 64)
        year = _safe_int(doc.get("year"))
        if not source_file or year is None:
            n_skipped += 1
            continue
        row_json = json.dumps(
            {
                k: v
                for k, v in doc.items()
                if k not in ("_id", "source_file", "year", "ingested_at")
            },
            sort_keys=True,
            default=str,
        )
        rows.append((source_file, year, row_json))

    if n_skipped:
        logger.warning(
            "Skipped %d EUROCONTROL PRU rows missing source_file or year",
            n_skipped,
        )

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.eurocontrol_pru
            (source_file, year, row_json)
            VALUES %s
            ON CONFLICT (source_file, year, row_json) DO NOTHING
            """,
            rows,
            template="(%s, %s, %s::jsonb)",
            page_size=500,
        )
    conn.commit()
    logger.info("Gold eurocontrol_pru: %d filas insertadas", len(rows))
    return len(rows)


def write_notam_gold(features: list[dict[str, Any]]) -> int:
    """Inserta features NOTAM en gold.notam.

    El documento GeoJSON se serializa como JSONB. Se omite filas sin
    ``feature`` o ``layer``.

    Args:
        features: Lista de dicts desde MongoDB (colección notam).

    Returns:
        Número de filas insertadas.
    """
    if not features:
        return 0

    rows: list[tuple[Any, ...]] = []
    n_skipped = 0
    for doc in features:
        feature = doc.get("feature")
        layer = _safe_int(doc.get("layer"))
        if not feature or layer is None:
            n_skipped += 1
            continue
        feature_json = json.dumps(feature, sort_keys=True, default=str)
        rows.append((
            layer,
            _parse_iso_timestamp(doc.get("snapshot_at")),
            feature_json,
        ))

    if n_skipped:
        logger.warning("Skipped %d NOTAM rows missing feature or layer", n_skipped)

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.notam
            (layer, snapshot_at, feature_json)
            VALUES %s
            ON CONFLICT (layer, feature_json) DO NOTHING
            """,
            rows,
            template="(%s, %s::timestamptz, %s::jsonb)",
            page_size=500,
        )
    conn.commit()
    logger.info("Gold notam: %d filas insertadas", len(rows))
    return len(rows)


def write_airports_gold(airports: list[dict[str, Any]]) -> int:
    """Upsert de aeropuertos en gold.airports (batch via execute_values).

    Los valores llegan como string desde el parser de OurAirports, por lo
    que las columnas numéricas se convierten con float() defensivo.
    Se omite filas sin ``ident``.

    Args:
        airports: Lista de dicts desde MongoDB (colección airports).

    Returns:
        Número de filas insertadas/actualizadas.
    """
    if not airports:
        return 0

    rows: list[tuple[Any, ...]] = []
    n_skipped = 0
    for doc in airports:
        ident = _trunc(doc.get("ident"), 8)
        if not ident:
            n_skipped += 1
            continue
        rows.append((
            ident,
            _trunc(doc.get("type"), 32),
            _trunc(doc.get("name"), 200),
            _safe_float(doc.get("latitude_deg")),
            _safe_float(doc.get("longitude_deg")),
            _safe_float(doc.get("elevation_ft")),
            _trunc(doc.get("iso_country"), 8),
            _trunc(doc.get("iso_region"), 16),
            _trunc(doc.get("municipality"), 100),
            _trunc(doc.get("iata_code"), 8),
            _trunc(doc.get("icao_code"), 8),
        ))

    if n_skipped:
        logger.warning("Skipped %d airports rows missing ident", n_skipped)

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.airports
            (ident, type, name, latitude_deg, longitude_deg, elevation_ft,
             iso_country, iso_region, municipality, iata_code, icao_code)
            VALUES %s
            ON CONFLICT (ident) DO UPDATE SET
                type           = EXCLUDED.type,
                name           = EXCLUDED.name,
                latitude_deg   = EXCLUDED.latitude_deg,
                longitude_deg  = EXCLUDED.longitude_deg,
                elevation_ft   = EXCLUDED.elevation_ft,
                iso_country    = EXCLUDED.iso_country,
                iso_region     = EXCLUDED.iso_region,
                municipality   = EXCLUDED.municipality,
                iata_code      = EXCLUDED.iata_code,
                icao_code      = EXCLUDED.icao_code,
                ingested_at    = NOW()
            """,
            rows,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            page_size=500,
        )
    conn.commit()
    logger.info("Gold airports: %d upsertados", len(rows))
    return len(rows)


def write_runways_gold(runways: list[dict[str, Any]]) -> int:
    """Upsert de pistas en gold.runways (batch via execute_values).

    Los valores llegan como string desde el parser de OurAirports, por lo
    que las columnas numéricas se convierten con float() defensivo.
    Se omite filas sin ``airport_ident``, ``le_ident`` o ``he_ident``.

    Args:
        runways: Lista de dicts desde MongoDB (colección runways).

    Returns:
        Número de filas insertadas/actualizadas.
    """
    if not runways:
        return 0

    rows: list[tuple[Any, ...]] = []
    n_skipped = 0
    for doc in runways:
        airport_ident = _trunc(doc.get("airport_ident"), 8)
        le_ident = _trunc(doc.get("le_ident"), 8)
        he_ident = _trunc(doc.get("he_ident"), 8)
        if not airport_ident or not le_ident or not he_ident:
            n_skipped += 1
            continue
        rows.append((
            airport_ident,
            _safe_float(doc.get("length_ft")),
            _safe_float(doc.get("width_ft")),
            _trunc(doc.get("surface"), 32),
            le_ident,
            he_ident,
            _safe_float(doc.get("le_heading_degT")),
            _safe_float(doc.get("he_heading_degT")),
        ))

    if n_skipped:
        logger.warning(
            "Skipped %d runways rows missing airport_ident, le_ident or he_ident",
            n_skipped,
        )

    conn = _get_conn()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO gold.runways
            (airport_ident, length_ft, width_ft, surface,
             le_ident, he_ident, le_heading_degT, he_heading_degT)
            VALUES %s
            ON CONFLICT (airport_ident, le_ident, he_ident) DO UPDATE SET
                length_ft      = EXCLUDED.length_ft,
                width_ft       = EXCLUDED.width_ft,
                surface        = EXCLUDED.surface,
                le_heading_degT = EXCLUDED.le_heading_degT,
                he_heading_degT = EXCLUDED.he_heading_degT,
                ingested_at    = NOW()
            """,
            rows,
            template="(%s, %s, %s, %s, %s, %s, %s, %s)",
            page_size=500,
        )
    conn.commit()
    logger.info("Gold runways: %d upsertados", len(rows))
    return len(rows)


# ===================================================================
# Gold — consultas públicas
# ===================================================================


def get_daily_traffic(
    airport_code: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Consulta tráfico diario agregado.

    Args:
        airport_code: Filtrar por aeropuerto (opcional).
        limit: Máximo de filas.

    Returns:
        Lista de dicts con airport_code, flight_date, arrivals_count, departures_count, total_count.
    """
    conn = _get_conn()
    if airport_code:
        query = """
            SELECT airport_code, flight_date, arrivals_count, departures_count,
                   arrivals_count + departures_count AS total_count
            FROM gold.daily_airport_traffic
            WHERE airport_code = %s
            ORDER BY flight_date DESC
            LIMIT %s
        """
        params = (airport_code, limit)
    else:
        query = """
            SELECT airport_code, flight_date, arrivals_count, departures_count,
                   arrivals_count + departures_count AS total_count
            FROM gold.daily_airport_traffic
            ORDER BY flight_date DESC, total_count DESC
            LIMIT %s
        """
        params = (limit,)

    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def get_top_routes(limit: int = 20) -> list[dict[str, Any]]:
    """Consulta las rutas más frecuentes.

    Args:
        limit: Máximo de rutas.

    Returns:
        Lista de dicts con departure_airport, arrival_airport, flight_count, first_seen, last_seen.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT departure_airport, arrival_airport, flight_count, first_seen, last_seen
            FROM gold.route_density
            ORDER BY flight_count DESC
            LIMIT %s
            """,
            (limit,),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
