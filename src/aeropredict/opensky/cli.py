"""CLI de extraccion de datos OpenSky.

Arquitectura:
  Bronze: data/raw/bronze/opensky/   -> JSON crudo de la API
  Silver: data/raw/silver/flights/   -> Columnas estructuradas
           data/raw/silver/state_vectors/
           data/raw/silver/tracks/
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import click

from .client import OpenSkyClient
from .config import (
    get_airport_icao_codes,
    get_bbox,
    get_client_id,
    get_client_secret,
    get_delta_root,
)
from .extract_flights import fetch_arrivals_raw, fetch_departures_raw, parse_flight_list
from .extract_states import fetch_states_raw, parse_states_response
from .extract_tracks import fetch_track_raw, parse_track_response
from .storage import (
    write_flights_silver,
    write_raw,
    write_state_vectors_silver,
    write_tracks_silver,
)

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_client() -> OpenSkyClient:
    cid = get_client_id()
    csec = get_client_secret()
    if cid and csec:
        return OpenSkyClient(client_id=cid, client_secret=csec)
    click.echo("OPENSKY_CLIENT_ID/SECRET no configurados. Modo anonimo.", err=True)
    return OpenSkyClient()


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """aeropredict: Extraccion de datos de OpenSky Network."""
    _setup_logging(verbose)


@cli.group()
def fetch() -> None:
    """Comandos de extraccion de datos."""


@fetch.command()
@click.option("--own", is_flag=True)
def states(own: bool) -> None:
    """State vectors actuales."""
    client = _build_client()
    bbox = get_bbox()
    click.echo(
        f"Obteniendo state vectors "
        f"(bbox: {bbox.lamin}/{bbox.lamax}, {bbox.lomin}/{bbox.lomax})..."
    )
    endpoint, params, raw = fetch_states_raw(client, bbox=bbox, own=own)
    vectors = parse_states_response(raw)
    click.echo(f"{len(vectors)} state vectors recibidos")
    base = get_delta_root()
    write_raw(endpoint, params, raw, base)
    write_state_vectors_silver(vectors, base)
    click.echo(f"Escrito en {base}/bronze/opensky/ y {base}/silver/state_vectors/")


@fetch.command()
@click.option("--airport", default="LEMD")
@click.option("--days", default=1, type=int)
@click.option("--begin")
@click.option("--end")
def arrivals(airport: str, days: int, begin: str | None, end: str | None) -> None:
    """Llegadas a un aeropuerto."""
    client = _build_client()
    begin_dt, end_dt = _resolve_interval(begin, end, days)
    click.echo(f"Llegadas a {airport.upper()} desde {begin_dt} hasta {end_dt}...")
    endpoint, params, raw = fetch_arrivals_raw(client, airport, begin_dt, end_dt)
    flights = parse_flight_list(raw)
    click.echo(f"{len(flights)} vuelos recibidos")
    base = get_delta_root()
    write_raw(endpoint, params, raw, base)
    write_flights_silver(flights, base)
    click.echo(f"Escrito en {base}/bronze/opensky/ y {base}/silver/flights/")


@fetch.command()
@click.option("--airport", default="LEMD")
@click.option("--days", default=1, type=int)
@click.option("--begin")
@click.option("--end")
def departures(airport: str, days: int, begin: str | None, end: str | None) -> None:
    """Salidas desde un aeropuerto."""
    client = _build_client()
    begin_dt, end_dt = _resolve_interval(begin, end, days)
    click.echo(f"Salidas desde {airport.upper()} desde {begin_dt} hasta {end_dt}...")
    endpoint, params, raw = fetch_departures_raw(client, airport, begin_dt, end_dt)
    flights = parse_flight_list(raw)
    click.echo(f"{len(flights)} vuelos recibidos")
    base = get_delta_root()
    write_raw(endpoint, params, raw, base)
    write_flights_silver(flights, base)
    click.echo(f"Escrito en {base}/bronze/opensky/ y {base}/silver/flights/")


@fetch.command()
@click.option("--icao24", required=True)
@click.option("--time", "time_str")
def tracks(icao24: str, time_str: str | None) -> None:
    """Trayectoria de una aeronave."""
    client = _build_client()
    time_dt = datetime.fromtimestamp(int(time_str), tz=UTC) if time_str else None
    click.echo(f"Obteniendo track para {icao24}...")
    endpoint, params, raw = fetch_track_raw(client, icao24, time=time_dt)
    if raw is None:
        click.echo("Track no encontrado")
        return
    track = parse_track_response(raw)
    click.echo(f"Track: {track.callsign or '?'} | {len(track.path)} waypoints")
    base = get_delta_root()
    write_raw(endpoint, params, raw, base)
    write_tracks_silver([track], base)
    click.echo(f"Escrito en {base}/bronze/opensky/ y {base}/silver/tracks/")


@fetch.command()
@click.option("--days", default=1, type=int)
@click.option("--begin")
@click.option("--end")
def all_airports(days: int, begin: str | None, end: str | None) -> None:
    """Llegadas/salidas de todos los aeropuertos configurados."""
    client = _build_client()
    begin_dt, end_dt = _resolve_interval(begin, end, days)
    airports = get_airport_icao_codes()
    click.echo(f"Extrayendo {len(airports)} aeropuertos...")
    total = 0
    for apt in airports:
        try:
            _, _, raw_arr = fetch_arrivals_raw(client, apt, begin_dt, end_dt)
            _, _, raw_dep = fetch_departures_raw(client, apt, begin_dt, end_dt)
            flights = parse_flight_list(raw_arr) + parse_flight_list(raw_dep)
            base = get_delta_root()
            write_flights_silver(flights, base)
            total += len(flights)
            click.echo(f"  {apt}: {len(flights)} vuelos")
        except Exception as e:
            click.echo(f"  {apt}: {e}", err=True)
    click.echo(f"Total: {total} vuelos")


@cli.command()
def setup() -> None:
    """Ayuda para configurar credenciales."""
    click.echo("""
Configuracion de OpenSky Network API
1. Registrate en: https://opensky-network.org
2. Cuenta -> API Clients -> Crear cliente
3. Copia CLIENT_ID y CLIENT_SECRET a .env
""")


def _resolve_interval(
    begin_str: str | None,
    end_str: str | None,
    days_back: int,
) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    if begin_str and end_str:
        return (
            datetime.fromtimestamp(int(begin_str), tz=UTC),
            datetime.fromtimestamp(int(end_str), tz=UTC),
        )
    return (now - timedelta(days=days_back), now)


if __name__ == "__main__":
    cli()
