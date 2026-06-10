"""Script de extracción diaria de vuelos OpenSky.

Uso:
    python -m aeropredict.opensky.daily_extract [--dry-run] [--days N]

Ejecuta una extracción incremental de vuelos históricos del día anterior,
verificando créditos disponibles y saltando aeropuertos ya cargados en Delta Lake.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa

from .client_pool import ClientPool
from .config import AEROPUERTOS, get_all_credentials, get_delta_root
from .credit_checker import can_extract
from .extract_flights import fetch_arrivals_raw, fetch_departures_raw, parse_flight_list
from .logging_config import setup_daily_logger
from .storage import write_flights_silver

# Retry delay entre pares arrival+departure (evitar rate limiting por minuto)
REQUEST_DELAY = 5.0

logger = logging.getLogger("daily_extract")

# Códigos de aeropuertos españoles (país "España" en AEROPUERTOS)
SPANISH_AIRPORT_CODES: list[str] = [
    code for code, _name, _city, country in AEROPUERTOS if country == "España"
]

# Umbral mínimo de créditos — 0 para consumir hasta el último crédito
MIN_CREDITS = 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Extracción diaria de vuelos históricos OpenSky",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula la extracción sin hacer llamadas a la API",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Número de días hacia atrás a extraer (default: 1)",
    )
    return parser.parse_args(argv)


def _get_existing_airports_for_day(
    delta_root: str, target_date: datetime.date,
) -> set[str]:
    """Obtiene aeropuertos ya cargados en Delta para una fecha concreta.

    Args:
        delta_root: Ruta base de tablas Delta.
        target_date: Fecha a consultar.

    Returns:
        Set de códigos ICAO de aeropuertos que ya tienen datos.
    """
    try:
        from deltalake import DeltaTable

        table_uri = f"{delta_root}/silver/flights"
        dt = DeltaTable(table_uri)
        table = dt.to_pyarrow_table()

        # Filtrar por flight_date
        date_scalar = pa.scalar(target_date, type=pa.date32())
        mask = pa.compute.equal(table.column("flight_date"), date_scalar)
        day_table = table.filter(mask)

        seen: set[str] = set()
        for col in ("est_arrival_airport", "est_departure_airport"):
            col_array = day_table.column(col)
            for i in range(len(col_array)):
                val = col_array[i].as_py()
                if val is not None:
                    seen.add(val)

        return seen

    except Exception as exc:
        logger.debug("No se pudo consultar Delta (quizás tabla no existe): %s", exc)
        return set()


def _extract_day(
    client: Any,
    target_date: datetime.date,
    dry_run: bool,
    delta_root: str,
) -> dict[str, Any]:
    """Extrae vuelos para un día concreto.

    Args:
        client: Cliente OpenSky autenticado.
        target_date: Fecha a extraer.
        dry_run: Si es True, no hace llamadas a la API.
        delta_root: Ruta base de tablas Delta.

    Returns:
        Dict con stats de la extracción.
    """
    day_start = datetime(
        target_date.year, target_date.month, target_date.day, tzinfo=UTC,
    )
    day_end = datetime(
        target_date.year, target_date.month, target_date.day, 23, 59, 59,
        tzinfo=UTC,
    )

    # Aeropuertos ya cargados para esta fecha
    existing = _get_existing_airports_for_day(delta_root, target_date)
    missing = [a for a in SPANISH_AIRPORT_CODES if a not in existing]
    skipped = len(SPANISH_AIRPORT_CODES) - len(missing)

    total_flights = 0
    total_airports = 0
    errors = []

    logger.info(
        "Fecha: %s | Aeropuertos: %d total, %d ya cargados, %d pendientes",
        target_date, len(SPANISH_AIRPORT_CODES), skipped, len(missing),
    )

    if dry_run:
        logger.info(
            "DRY RUN: extraería %d aeropuertos para %s (saltando %d ya cargados)",
            len(missing), target_date, skipped,
        )
        return {
            "date": str(target_date),
            "missing": len(missing),
            "skipped": skipped,
            "flights": 0,
            "airports_ok": 0,
            "errors": errors,
            "dry_run": True,
        }

    for i, apt in enumerate(missing):
        if i > 0:
            time.sleep(REQUEST_DELAY)

        try:
            # Verificar créditos antes de cada par de peticiones (opcional)
            _, _, raw_a = fetch_arrivals_raw(client, apt, day_start, day_end)
            _, _, raw_d = fetch_departures_raw(client, apt, day_start, day_end)
            flights = parse_flight_list(raw_a) + parse_flight_list(raw_d)
            if flights:
                write_flights_silver(flights, delta_root)
            total_flights += len(flights)
            total_airports += 1
            logger.info(
                "  %s (%d/%d): %d arrivals + %d departures = %d vuelos",
                apt, i + 1, len(missing),
                len(parse_flight_list(raw_a)) if raw_a else 0,
                len(parse_flight_list(raw_d)) if raw_d else 0,
                len(flights),
            )
        except Exception as e:
            err_msg = str(e)
            # 404 = sin datos en el rango (no es error real)
            if "404" in err_msg:
                logger.info("  %s: sin datos en el rango", apt)
                total_airports += 1  # contar como procesado
            elif "429" in err_msg or "rate limited" in err_msg:
                logger.warning(
                    "  %s: créditos agotados (%s). Deteniendo extracción.",
                    apt,
                    "429" if "429" in err_msg else "pool exhausto",
                )
                errors.append({"airport": apt, "error": err_msg})
                break  # salir del bucle, créditos agotados
            else:
                logger.warning("  %s: error: %s", apt, err_msg)
                errors.append({"airport": apt, "error": err_msg})

    return {
        "date": str(target_date),
        "missing": len(missing),
        "skipped": skipped,
        "flights": total_flights,
        "airports_ok": total_airports,
        "errors": errors,
        "dry_run": False,
    }


def _check_credits_and_exit() -> bool:
    """Verifica créditos y loggea info. Nunca bloquea la extracción."""
    _, info = can_extract(min_required=MIN_CREDITS)
    remaining = info.get("remaining", "?")
    retry = info.get("retry_after")
    reset = info.get("reset_at")
    logger.info(
        "Créditos OpenSky: %s remaining. Ventana: %s (retry after: %ss)",
        remaining,
        reset.isoformat() if reset else "?",
        retry or "?",
    )
    return True


def _fill_gaps(client: Any, delta_root: str, max_lookback: int = 30) -> int:
    """Rellena huecos históricos con créditos sobrantes tras la extracción diaria.

    Escanea desde D-3 hacia atrás buscando fechas con aeropuertos españoles
    pendientes de cargar. Procesa lo que falta hasta agotar créditos (429)
    o cubrir todo el histórico.

    Returns:
        Número total de vuelos rellenados.
    """
    now_utc = datetime.now(UTC)
    filled = 0
    days_checked = 0

    for offset in range(3, max_lookback + 1):
        target_date = (now_utc - timedelta(days=offset)).date()

        ok, info = can_extract(min_required=60)
        if not ok:
            logger.info(
                "Relleno de huecos: créditos insuficientes (%s). Parando.",
                info.get("remaining", "?"),
            )
            break

        existing = _get_existing_airports_for_day(delta_root, target_date)
        missing = [a for a in SPANISH_AIRPORT_CODES if a not in existing]
        if not missing:
            continue

        days_checked += 1
        logger.info(
            "--- Rellenando hueco: %s (D-%d) — %d aeropuertos pendientes ---",
            target_date, offset, len(missing),
        )

        result = _extract_day(client, target_date, dry_run=False, delta_root=delta_root)
        filled += result["flights"]

        if result["errors"]:
            for err in result["errors"]:
                err_text = err.get("error", "")
                if "429" in err_text or "rate limited" in err_text:
                    logger.info(
                        "Créditos agotados durante relleno de huecos. "
                        "Detenido en %s después de %d días y %d vuelos.",
                        target_date, days_checked, filled,
                    )
                    return filled

        time.sleep(2)

    if days_checked:
        logger.info(
            "Relleno de huecos completado: %d días escaneados, %d vuelos añadidos",
            days_checked, filled,
        )
    return filled


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada principal.

    Args:
        argv: Argumentos de línea de comandos (None = sys.argv[1:]).

    Returns:
        Código de salida (0 = éxito, 1 = error).
    """
    setup_daily_logger()
    args = _parse_args(argv)
    delta_root = get_delta_root()

    logger.info("=" * 60)
    logger.info("Iniciando extracción diaria OpenSky")
    logger.info("Dry-run: %s | Days: %d", args.dry_run, args.days)
    logger.info("Delta root: %s", delta_root)
    logger.info("=" * 60)

    # Crear pool de clientes (solo si no es dry-run)
    client: ClientPool | None = None
    if not args.dry_run:
        _check_credits_and_exit()
        creds = get_all_credentials()
        if not creds:
            logger.error(
                "No hay credenciales OpenSky configuradas. "
                "Usa `doppler run` o define OPENSKY_CLIENT_ID* en .env",
            )
            return 1
        client = ClientPool(creds)
        logger.info(
            "Pool creado con %d cuentas: %s",
            client.account_count,
            [client.current_name],  # nombre de la activa inicial
        )
        # Log de todas las cuentas disponibles
        for cred in creds:
            logger.info("  Cuenta disponible: %s (%s)", cred.get("name", "?"), cred["id"])

    start_time = time.time()
    total_summary: dict[str, Any] = {
        "total_flights": 0,
        "total_airports": 0,
        "days_done": 0,
        "errors": [],
    }

    # Extraer desde D-1 hacia atrás
    now_utc = datetime.now(UTC)
    for offset in range(1, args.days + 1):
        target_date = (now_utc - timedelta(days=offset)).date()
        logger.info("--- Día %s (D-%d) ---", target_date, offset)

        result = _extract_day(
            client=client,
            target_date=target_date,
            dry_run=args.dry_run,
            delta_root=delta_root,
        )

        if result["dry_run"]:
            continue

        total_summary["total_flights"] += result["flights"]
        total_summary["total_airports"] += result["airports_ok"]
        total_summary["days_done"] += 1
        total_summary["errors"].extend(result["errors"])

    # Rellenar huecos históricos con créditos sobrantes
    gap_filled = 0
    if not args.dry_run and total_summary["days_done"] > 0:
        gap_filled = _fill_gaps(client, delta_root)

    elapsed = time.time() - start_time

    # Resumen final
    logger.info("=" * 60)
    if args.dry_run:
        logger.info(
            "DRY RUN COMPLETADO: %d días simulados",
            args.days,
        )
    else:
        parts = [
            f"{total_summary['days_done']} días, "
            f"{total_summary['total_flights']} vuelos de "
            f"{total_summary['total_airports']} aeropuertos",
        ]
        if gap_filled:
            parts.append(f"({gap_filled} de relleno histórico)")
        parts.append(f"{elapsed:.1f}s")
        logger.info("EXTRACCIÓN COMPLETADA: %s", " | ".join(parts))
        if total_summary["errors"]:
            logger.warning(
                "Errores: %d aeropuertos con fallos",
                len(total_summary["errors"]),
            )
            for err in total_summary["errors"]:
                logger.warning("  - %s: %s", err["airport"], err["error"])
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
