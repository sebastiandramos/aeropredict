"""Script: check_alerts — revisa suscripciones y genera alertas de retraso.

Lee las suscripciones de vuelo de MongoDB (``user_flight_subscriptions``),
predice el retraso de cada vuelo reutilizando el modelo MLflow de la API
(``server.py``) y, si supera el umbral de la suscripción, escribe una alerta
en ``gold.alerts`` (SIEMPRE) y — solo si el notifier está habilitado (flag +
secreto, ver ``notifications/email.py``) — envía un email. Idempotente:
re-ejecutar no duplica alertas del mismo vuelo en la ventana de dedup (60
min); ``--force`` la ignora. ``--dry-run`` no escribe ni envía nada.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from aeropredict.app import persistence
from aeropredict.notifications.email import send_alert_email
from aeropredict.sources.airport_coords import AIRPORT_COORDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Mismo modelo que server.py (carga MLflow pyfunc).
MLFLOW_MODEL_URI = os.environ.get("MLFLOW_MODEL_URI", "models:/delay-predictor/production")
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "file:./mlruns")

# Ventana de idempotencia: no re-alertar el mismo vuelo dentro de N minutos.
DEDUP_WINDOW_MINUTES = 60

# Umbrales de severidad — mismos que ResultPanel.tsx (severityFor).
SEVERITY_ONTIME_MIN = 15
SEVERITY_MODERATE_MAX = 60

_EARTH_RADIUS_KM = 6371.0
_AIRLINE_RE = re.compile(r"^([A-Za-z]{2,3})")


def _load_model() -> Any:
    """Carga el modelo MLflow pyfunc (mismo URI/tracking que server.py)."""
    import mlflow  # lazy: extra ``ml``

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    return mlflow.pyfunc.load_model(MLFLOW_MODEL_URI)


def _list_all_subscriptions() -> list[dict[str, Any]]:
    """Todas las suscripciones de todos los usuarios (vía persistencia)."""
    return list(persistence._get_subscriptions_collection().find({}))


def _parse_iso(value: Any) -> datetime | None:
    """Parsea un datetime o ISO string a datetime UTC; ``None`` si no es parseable."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _airline_from_flight_number(flight_number: str) -> str:
    """Código de aerolínea desde el número de vuelo (p. ej. ``IB1234`` → ``IB``)."""
    match = _AIRLINE_RE.match(flight_number.strip())
    return match.group(1).upper() if match else flight_number.strip().upper()


def _route_distance_km(from_airport: str, to_airport: str) -> float:
    """Distancia ortodrómica (haversine) entre dos aeropuertos en km."""
    try:
        lat1, lon1 = AIRPORT_COORDS[from_airport.upper()]
        lat2, lon2 = AIRPORT_COORDS[to_airport.upper()]
    except KeyError:
        logger.warning("Sin coordenadas para %s→%s; distancia 0", from_airport, to_airport)
        return 0.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _build_features(subscription: dict[str, Any]) -> dict[str, Any]:
    """Features del modelo desde una suscripción (orden fijo, igual que server.py)."""
    schedule = _parse_iso(subscription.get("schedule_local", ""))
    return {
        "hour_of_day": schedule.hour if schedule else 0,
        "day_of_week": schedule.weekday() if schedule else 0,
        "airline": _airline_from_flight_number(subscription.get("flight_number", "")),
        "route_distance": _route_distance_km(
            subscription.get("from_airport", ""),
            subscription.get("to_airport", ""),
        ),
        "aircraft_type": None,
        "aircraft_manufacturer": None,
        "aircraft_operator": None,
        "weather_temperature_2m": None,
        "weather_precipitation": None,
    }


def predict_delay(model: Any, features: dict[str, Any]) -> float:
    """Predice minutos de retraso con el modelo (misma lógica que server.py)."""
    pred = model.predict(pd.DataFrame([features]))
    if hasattr(pred, "__len__") and len(pred) > 0:
        return float(pred[0])
    return float(pred)


def _severity_for(delay_minutes: float) -> str:
    """Severidad de la alerta — mismos umbrales que ResultPanel.tsx."""
    if delay_minutes < SEVERITY_ONTIME_MIN:
        return "ontime"
    if delay_minutes <= SEVERITY_MODERATE_MAX:
        return "moderate"
    return "severe"


def _has_recent_alert(
    alerts: list[dict[str, Any]],
    flight_key: str,
    window_minutes: int = DEDUP_WINDOW_MINUTES,
    now: datetime | None = None,
) -> bool:
    """True si ya existe una alerta del vuelo dentro de la ventana."""
    reference = now or datetime.now(UTC)
    cutoff = reference - timedelta(minutes=window_minutes)
    for alert in alerts:
        if alert.get("flight_key") != flight_key:
            continue
        created = _parse_iso(alert.get("created_at"))
        if created is not None and created >= cutoff:
            return True
    return False


def _email_message(
    subscription: dict[str, Any], flight_key: str, delay_minutes: float, severity: str
) -> tuple[str, str]:
    """Asunto y cuerpo del email de alerta."""
    subject = f"[AeroPredict] Alerta de retraso: {flight_key} ({delay_minutes:.0f} min)"
    body = (
        f"Tu vuelo {subscription.get('flight_number', '')} "
        f"({subscription.get('from_airport', '')} → {subscription.get('to_airport', '')}) "
        f"tiene un retraso estimado de {delay_minutes:.0f} minutos "
        f"(severidad: {severity}).\n"
        f"Salida programada: {subscription.get('schedule_local', '')}."
    )
    return subject, body


def _factor_jsonb(
    subscription: dict[str, Any], features: dict[str, Any], delay_minutes: float
) -> dict[str, Any]:
    """Factores explicativos de la alerta (JSONB)."""
    return {
        "flight_number": subscription.get("flight_number"),
        "from_airport": subscription.get("from_airport"),
        "to_airport": subscription.get("to_airport"),
        "schedule_local": subscription.get("schedule_local"),
        "threshold_minutes": subscription.get("threshold_minutes"),
        "delay_minutes_predicted": delay_minutes,
        "features": features,
    }


def check_alerts(
    dry_run: bool = False,
    force: bool = False,
    model: Any | None = None,
) -> dict[str, int]:
    """Revisa suscripciones y genera alertas de retraso.

    Args:
        dry_run: No escribe alertas ni envía emails; imprime qué haría.
        force: Ignora la ventana de idempotencia (re-alerta).
        model: Modelo ya cargado (None = cargar con ``_load_model``).

    Returns:
        Stats: subscriptions, alerts_written, alerts_skipped, below_threshold,
        errors, emails_sent.
    """
    if model is None:
        model = _load_model()

    subscriptions = _list_all_subscriptions()
    stats = {
        "subscriptions": len(subscriptions),
        "alerts_written": 0,
        "alerts_skipped": 0,
        "below_threshold": 0,
        "errors": 0,
        "emails_sent": 0,
    }
    if not subscriptions:
        logger.info("Sin suscripciones de vuelo que revisar")
        return stats

    now = datetime.now(UTC)
    alerts_cache: dict[str, list[dict[str, Any]]] = {}

    for sub in subscriptions:
        flight_key = sub.get("flight_key", "?")
        user_id = sub.get("user_id", "?")
        try:
            features = _build_features(sub)
            delay = predict_delay(model, features)
        except Exception as exc:
            logger.error("Fallo al predecir %s (user=%s): %s", flight_key, user_id, exc)
            stats["errors"] += 1
            continue  # aislamiento por vuelo: el run continúa

        threshold = int(sub.get("threshold_minutes") or 60)
        if delay < threshold:
            logger.info(
                "Vuelo %s: retraso %.1f min < umbral %d min (sin alerta)",
                flight_key, delay, threshold,
            )
            stats["below_threshold"] += 1
            continue

        severity = _severity_for(delay)
        if dry_run:
            logger.info(
                "[dry-run] Insertaría alerta %s user=%s delay=%.1f min severity=%s",
                flight_key, user_id, delay, severity,
            )
            continue

        if not force:
            if user_id not in alerts_cache:
                alerts_cache[user_id] = persistence.list_alerts(user_id)
            if _has_recent_alert(alerts_cache[user_id], flight_key, now=now):
                logger.info(
                    "Vuelo %s: alerta reciente ya existe (skip; --force para forzar)",
                    flight_key,
                )
                stats["alerts_skipped"] += 1
                continue

        email_sent = False
        email = sub.get("email")
        if email:
            subject, body = _email_message(sub, flight_key, delay, severity)
            email_sent = send_alert_email(email, subject, body)
            if email_sent:
                stats["emails_sent"] += 1

        alert_id = persistence.insert_alert(
            user_id=user_id,
            flight_key=flight_key,
            severity=severity,
            delay_minutes_predicted=delay,
            factor_jsonb=_factor_jsonb(sub, features, delay),
            email_sent=email_sent,
        )
        logger.info(
            "Alerta %d insertada: %s user=%s delay=%.1f min severity=%s email_sent=%s",
            alert_id, flight_key, user_id, delay, severity, email_sent,
        )
        stats["alerts_written"] += 1

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revisa suscripciones de vuelo y genera alertas de retraso",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="No escribe alertas ni envía emails; imprime qué haría",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignora la ventana de idempotencia y re-alerta",
    )
    args = parser.parse_args(argv)

    try:
        stats = check_alerts(dry_run=args.dry_run, force=args.force)
    except Exception as exc:
        logger.error("check_alerts falló: %s", exc)
        return 1

    logger.info("--- Resultados ---")
    logger.info(
        "Suscripciones: %d | Alertas escritas: %d | Saltadas (ya alertado): %d | "
        "Bajo umbral: %d | Errores: %d | Emails enviados: %d",
        stats["subscriptions"], stats["alerts_written"], stats["alerts_skipped"],
        stats["below_threshold"], stats["errors"], stats["emails_sent"],
    )

    if stats["errors"] and stats["errors"] >= stats["subscriptions"]:
        logger.error("Todas las suscripciones fallaron: check_alerts run failed")
        return 1
    if stats["errors"]:
        logger.warning(
            "Fallo parcial: %d/%d — el run continúa", stats["errors"], stats["subscriptions"]
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
