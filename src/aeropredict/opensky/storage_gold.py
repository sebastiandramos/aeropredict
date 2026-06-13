"""Capa gold — PostgreSQL para agregaciones analíticas.

Las tablas se crean automáticamente bajo el esquema ``gold``.

Tablas:
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
