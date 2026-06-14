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
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
"""


def _get_conn():
    """Conecta a PostgreSQL y crea tablas si no existen."""
    global _conn
    if _conn is None or _conn.closed:
        uri = get_postgres_uri()
        logger.info("Conectando a PostgreSQL: %s", uri)
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
            ON CONFLICT (id) DO NOTHING
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
            ON CONFLICT (id) DO NOTHING
            """,
            rows,
            template="(%s, %s::timestamptz, %s::date, %s, %s, %s, %s, %s, %s, %s)",
            page_size=500,
        )
        n = cur.rowcount
    conn.commit()
    logger.info("Gold weather: %d filas insertadas", len(rows))
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
