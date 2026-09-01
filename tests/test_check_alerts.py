"""Tests del scheduler check_alerts (todo 7, mis-vuelos-alertas).

Patrón importlib + monkeypatch de ``tests/test_collect_metar.py``: sin red,
sin DB real, sin Doppler. Se monkeypatchean las funciones de persistencia
(``aeropredict.app.persistence.*``) y las de datos (``_load_model`` /
``predict_delay`` / ``send_alert_email`` del módulo) con fakes en memoria.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aeropredict.app import persistence


def _load_check_alerts_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "check_alerts.py"
    spec = importlib.util.spec_from_file_location("check_alerts_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ===================================================================
# Fakes
# ===================================================================


class _FakeModel:
    """Modelo MLflow fake: ``predict`` devuelve un retraso fijo."""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    def predict(self, df: Any) -> list[float]:
        return [self._delay]


class _FakeCollection:
    """Colección Mongo fake con ``find``."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def find(self, filtro: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self._docs)


def _subscription(**overrides: Any) -> dict[str, Any]:
    """Suscripción fixture con valores por defecto."""
    doc = {
        "user_id": "u1",
        "flight_key": "IB1234-2026-09-01",
        "flight_number": "IB1234",
        "from_airport": "LEMD",
        "to_airport": "LEBL",
        "schedule_local": "2026-09-01T10:00:00",
        "threshold_minutes": 30,
        "email": "ana@example.com",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    doc.update(overrides)
    return doc


def _recent_alert(flight_key: str = "IB1234-2026-09-01") -> dict[str, Any]:
    """Alerta reciente (dentro de la ventana de dedup)."""
    return {
        "id": 1,
        "user_id": "u1",
        "flight_key": flight_key,
        "severity": "moderate",
        "delay_minutes_predicted": 45.0,
        "factor_jsonb": {},
        "email_sent": False,
        "read": False,
        "created_at": datetime.now(UTC),
    }


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def module():
    return _load_check_alerts_module()


@pytest.fixture
def fake_persistence(monkeypatch):
    """Monkeypatchea la persistencia con fakes en memoria."""
    inserted: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    def fake_insert_alert(**kwargs: Any) -> int:
        inserted.append(kwargs)
        return len(inserted)

    monkeypatch.setattr(persistence, "insert_alert", fake_insert_alert)
    monkeypatch.setattr(persistence, "list_alerts", lambda user_id, read=None: list(alerts))
    return {"inserted": inserted, "alerts": alerts}


def _set_subscriptions(monkeypatch, subs: list[dict[str, Any]]) -> None:
    """Fija las suscripciones que devuelve la capa de persistencia."""
    monkeypatch.setattr(
        persistence, "_get_subscriptions_collection", lambda: _FakeCollection(subs)
    )


# ===================================================================
# Happy path
# ===================================================================


def test_happy_path_inserts_alert_and_emails(module, fake_persistence, monkeypatch):
    """Suscripción sobre umbral → alerta insertada + email intentado."""
    _set_subscriptions(monkeypatch, [_subscription()])
    email_calls: list[tuple[str, str, str]] = []

    def fake_email(to, subject, body):
        email_calls.append((to, subject, body))
        return True

    monkeypatch.setattr(module, "send_alert_email", fake_email)

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats["subscriptions"] == 1
    assert stats["alerts_written"] == 1
    assert stats["errors"] == 0
    assert stats["emails_sent"] == 1
    assert len(fake_persistence["inserted"]) == 1
    alert = fake_persistence["inserted"][0]
    assert alert["user_id"] == "u1"
    assert alert["flight_key"] == "IB1234-2026-09-01"
    assert alert["severity"] == "moderate"
    assert alert["delay_minutes_predicted"] == 45.0
    assert alert["email_sent"] is True
    assert alert["factor_jsonb"]["flight_number"] == "IB1234"
    assert len(email_calls) == 1
    assert email_calls[0][0] == "ana@example.com"
    assert "IB1234-2026-09-01" in email_calls[0][1]
    assert "45" in email_calls[0][2]


def test_severe_delay_uses_severe_severity(module, fake_persistence, monkeypatch):
    """Retraso > 60 min → severidad ``severe``."""
    _set_subscriptions(monkeypatch, [_subscription()])
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    stats = module.check_alerts(model=_FakeModel(90.0))

    assert stats["alerts_written"] == 1
    assert fake_persistence["inserted"][0]["severity"] == "severe"


# ===================================================================
# No-op y umbral
# ===================================================================


def test_no_subscriptions_is_noop(module, fake_persistence, monkeypatch):
    """Sin suscripciones → no-op (no escribe, no envía)."""
    _set_subscriptions(monkeypatch, [])
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats == {
        "subscriptions": 0,
        "alerts_written": 0,
        "alerts_skipped": 0,
        "below_threshold": 0,
        "errors": 0,
        "emails_sent": 0,
    }
    assert fake_persistence["inserted"] == []


def test_below_threshold_no_alert(module, fake_persistence, monkeypatch):
    """Retraso bajo el umbral → sin alerta."""
    _set_subscriptions(monkeypatch, [_subscription(threshold_minutes=60)])
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats["alerts_written"] == 0
    assert stats["below_threshold"] == 1
    assert fake_persistence["inserted"] == []


# ===================================================================
# Aislamiento de fallos por vuelo
# ===================================================================


def test_per_flight_failure_isolation(module, fake_persistence, monkeypatch):
    """Un vuelo que falla no rompe el run; el resto se procesa."""
    subs = [
        _subscription(flight_key="FK-OK", flight_number="IB1111"),
        _subscription(flight_key="FK-BAD", flight_number="VY2222"),
    ]
    _set_subscriptions(monkeypatch, subs)
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    def flaky_predict(model, features):
        if features["airline"] == "VY":
            raise RuntimeError("boom")
        return 45.0

    monkeypatch.setattr(module, "predict_delay", flaky_predict)

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats["errors"] == 1
    assert stats["alerts_written"] == 1
    assert len(fake_persistence["inserted"]) == 1
    assert fake_persistence["inserted"][0]["flight_key"] == "FK-OK"


# ===================================================================
# Idempotencia
# ===================================================================


def test_idempotent_skips_recent_alert(module, fake_persistence, monkeypatch):
    """Alerta reciente del mismo vuelo → skip (sin duplicar)."""
    _set_subscriptions(monkeypatch, [_subscription()])
    fake_persistence["alerts"].append(_recent_alert())
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats["alerts_skipped"] == 1
    assert stats["alerts_written"] == 0
    assert fake_persistence["inserted"] == []


def test_old_alert_does_not_skip(module, fake_persistence, monkeypatch):
    """Alerta antigua (fuera de la ventana) → se alerta de nuevo."""
    _set_subscriptions(monkeypatch, [_subscription()])
    old = _recent_alert()
    old["created_at"] = datetime.now(UTC) - timedelta(minutes=120)
    fake_persistence["alerts"].append(old)
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats["alerts_skipped"] == 0
    assert stats["alerts_written"] == 1


def test_force_bypasses_idempotency(module, fake_persistence, monkeypatch):
    """``--force`` ignora la ventana de idempotencia."""
    _set_subscriptions(monkeypatch, [_subscription()])
    fake_persistence["alerts"].append(_recent_alert())
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    stats = module.check_alerts(model=_FakeModel(45.0), force=True)

    assert stats["alerts_skipped"] == 0
    assert stats["alerts_written"] == 1
    assert len(fake_persistence["inserted"]) == 1


# ===================================================================
# Dry-run
# ===================================================================


def test_dry_run_writes_nothing(module, fake_persistence, monkeypatch):
    """``--dry-run`` no escribe alertas ni envía emails."""
    _set_subscriptions(monkeypatch, [_subscription()])
    email_calls: list[Any] = []
    monkeypatch.setattr(
        module, "send_alert_email", lambda *a, **k: email_calls.append(a) or True
    )

    stats = module.check_alerts(model=_FakeModel(45.0), dry_run=True)

    assert stats["alerts_written"] == 0
    assert fake_persistence["inserted"] == []
    assert email_calls == []


# ===================================================================
# Email default-off (integración con T3)
# ===================================================================


def test_email_disabled_by_default_still_persists(module, fake_persistence, monkeypatch):
    """Email apagado por defecto → alerta persistida con ``email_sent=False``."""
    _set_subscriptions(monkeypatch, [_subscription()])
    monkeypatch.delenv("NOTIFICATIONS_ENABLED", raising=False)

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats["alerts_written"] == 1
    assert stats["emails_sent"] == 0
    assert fake_persistence["inserted"][0]["email_sent"] is False


def test_subscription_without_email_persists_without_email(module, fake_persistence, monkeypatch):
    """Suscripción sin email → alerta persistida, email no intentado."""
    _set_subscriptions(monkeypatch, [_subscription(email=None)])
    email_calls: list[Any] = []
    monkeypatch.setattr(
        module, "send_alert_email", lambda *a, **k: email_calls.append(a) or True
    )

    stats = module.check_alerts(model=_FakeModel(45.0))

    assert stats["alerts_written"] == 1
    assert stats["emails_sent"] == 0
    assert fake_persistence["inserted"][0]["email_sent"] is False
    assert email_calls == []


# ===================================================================
# CLI (main)
# ===================================================================


def test_main_dry_run_exit_zero(module, monkeypatch):
    """``main(["--dry-run"])`` → exit 0 sin efectos."""
    _set_subscriptions(monkeypatch, [_subscription()])
    monkeypatch.setattr(module, "_load_model", lambda: _FakeModel(45.0))
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    assert module.main(["--dry-run"]) == 0


def test_main_force_flag_accepted(module, fake_persistence, monkeypatch):
    """``main(["--force"])`` → exit 0."""
    _set_subscriptions(monkeypatch, [_subscription()])
    monkeypatch.setattr(module, "_load_model", lambda: _FakeModel(45.0))
    monkeypatch.setattr(module, "send_alert_email", lambda *a, **k: True)

    assert module.main(["--force"]) == 0
    assert len(fake_persistence["inserted"]) == 1


def test_main_all_failed_exit_one(module, monkeypatch):
    """Todas las suscripciones fallan → exit 1 (hard failure)."""
    _set_subscriptions(monkeypatch, [_subscription()])
    monkeypatch.setattr(module, "_load_model", lambda: _FakeModel(45.0))

    def boom(model, features):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "predict_delay", boom)

    assert module.main([]) == 1


def test_main_model_load_failure_exit_one(module, monkeypatch):
    """Fallo al cargar el modelo → exit 1 (hard failure)."""

    def failing_load():
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(module, "_load_model", failing_load)

    assert module.main([]) == 1


def test_main_unknown_flag_raises_system_exit(module):
    """Flag desconocido → SystemExit (argparse)."""
    with pytest.raises(SystemExit):
        module.main(["--unknown-flag"])


# ===================================================================
# Helpers
# ===================================================================


def test_severity_for(module):
    assert module._severity_for(10.0) == "ontime"
    assert module._severity_for(15.0) == "moderate"
    assert module._severity_for(60.0) == "moderate"
    assert module._severity_for(61.0) == "severe"


def test_build_features(module):
    features = module._build_features(_subscription())

    schedule = datetime.fromisoformat("2026-09-01T10:00:00")
    assert features["hour_of_day"] == 10
    assert features["day_of_week"] == schedule.weekday()
    assert features["airline"] == "IB"
    assert 400 < features["route_distance"] < 600  # LEMD→LEBL ~505 km
    assert features["aircraft_type"] is None
    assert features["weather_temperature_2m"] is None


def test_build_features_unknown_airport_distance_zero(module):
    features = module._build_features(
        _subscription(from_airport="XXXX", to_airport="YYYY")
    )

    assert features["route_distance"] == 0.0


def test_has_recent_alert(module):
    now = datetime.now(UTC)
    old = {"flight_key": "FK1", "created_at": now - timedelta(minutes=120)}
    recent = {"flight_key": "FK1", "created_at": now - timedelta(minutes=5)}
    other = {"flight_key": "FK2", "created_at": now}

    assert module._has_recent_alert([old], "FK1", now=now) is False
    assert module._has_recent_alert([recent], "FK1", now=now) is True
    assert module._has_recent_alert([other], "FK1", now=now) is False
    # created_at como string ISO también funciona
    assert (
        module._has_recent_alert(
            [{"flight_key": "FK1", "created_at": now.isoformat()}], "FK1", now=now
        )
        is True
    )
