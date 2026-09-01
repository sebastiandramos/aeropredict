"""Tests de los endpoints de suscripciones y alertas (todo 6, mis-vuelos-alertas).

Patrón TestClient + monkeypatch: sin red, sin DB real, sin Doppler. Se
monkeypatchean las funciones de persistencia de suscripciones/alertas con un
almacén en memoria, y se fija un ``JWT_SECRET`` determinista para poder emitir
tokens válidos con ``create_token``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeropredict.api.server import app
from aeropredict.auth import create_token

# ===================================================================
# Fake en memoria de la capa de persistencia
# ===================================================================


class _FakeStore:
    """Almacén en memoria de suscripciones y alertas (fakes de persistencia)."""

    def __init__(self) -> None:
        self._subs: list[dict[str, Any]] = []
        self._alerts: list[dict[str, Any]] = []
        self._next_alert_id = 1

    # --- suscripciones ---

    def create_subscription(
        self,
        user_id: str,
        flight_key: str,
        flight_number: str,
        from_airport: str,
        to_airport: str,
        schedule_local: str,
        threshold_minutes: int = 60,
        email: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        doc = {
            "user_id": user_id,
            "flight_key": flight_key,
            "flight_number": flight_number,
            "from_airport": from_airport,
            "to_airport": to_airport,
            "schedule_local": schedule_local,
            "threshold_minutes": threshold_minutes,
            "email": email,
            "created_at": now,
            "updated_at": now,
        }
        # Upsert por (user_id, flight_key): conserva created_at original.
        for i, existing in enumerate(self._subs):
            if (
                existing["user_id"] == user_id
                and existing["flight_key"] == flight_key
            ):
                doc["created_at"] = existing["created_at"]
                self._subs[i] = doc
                return dict(doc)
        self._subs.append(doc)
        return dict(doc)

    def list_subscriptions(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(s) for s in self._subs if s["user_id"] == user_id]

    def delete_subscription(self, user_id: str, flight_key: str) -> bool:
        for i, sub in enumerate(self._subs):
            if sub["user_id"] == user_id and sub["flight_key"] == flight_key:
                self._subs.pop(i)
                return True
        return False

    # --- alertas ---

    def insert_alert(
        self,
        user_id: str,
        flight_key: str,
        severity: str,
        delay_minutes_predicted: float,
        factor_jsonb: dict[str, Any],
        email_sent: bool = False,
    ) -> int:
        alert = {
            "id": self._next_alert_id,
            "user_id": user_id,
            "flight_key": flight_key,
            "severity": severity,
            "delay_minutes_predicted": delay_minutes_predicted,
            "factor_jsonb": factor_jsonb,
            "email_sent": email_sent,
            "read": False,
            "created_at": datetime.now(UTC),
        }
        self._next_alert_id += 1
        self._alerts.append(alert)
        return alert["id"]

    def list_alerts(
        self,
        user_id: str,
        read: bool | None = None,
    ) -> list[dict[str, Any]]:
        alerts = [dict(a) for a in self._alerts if a["user_id"] == user_id]
        if read is not None:
            alerts = [a for a in alerts if a["read"] == read]
        return alerts

    def mark_alert_read(self, alert_id: int) -> bool:
        for alert in self._alerts:
            if alert["id"] == alert_id:
                alert["read"] = True
                return True
        return False


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    """Monkeypatchea la persistencia de suscripciones/alertas con un fake."""
    fake = _FakeStore()
    monkeypatch.setattr(
        "aeropredict.app.persistence.create_subscription",
        fake.create_subscription,
    )
    monkeypatch.setattr(
        "aeropredict.app.persistence.list_subscriptions",
        fake.list_subscriptions,
    )
    monkeypatch.setattr(
        "aeropredict.app.persistence.delete_subscription",
        fake.delete_subscription,
    )
    monkeypatch.setattr(
        "aeropredict.app.persistence.insert_alert",
        fake.insert_alert,
    )
    monkeypatch.setattr(
        "aeropredict.app.persistence.list_alerts",
        fake.list_alerts,
    )
    monkeypatch.setattr(
        "aeropredict.app.persistence.mark_alert_read",
        fake.mark_alert_read,
    )
    return fake


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija un secreto determinista para todos los tests."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-aeropredict-tests")


@pytest.fixture
def client() -> TestClient:
    """TestClient sin lifespan (no carga el modelo MLflow)."""
    return TestClient(app)


def _auth(user_id: str) -> dict[str, str]:
    """Header Authorization con un Bearer token válido para ``user_id``."""
    return {"Authorization": f"Bearer {create_token(user_id)}"}


_SUB_BODY = {
    "flight_key": "IB1234-2026-09-01",
    "flight_number": "IB1234",
    "from_airport": "LEMD",
    "to_airport": "LEBL",
    "schedule_local": "2026-09-01T10:00:00",
    "threshold_minutes": 60,
}


# ===================================================================
# Auth requerida (401)
# ===================================================================


def test_subscriptions_require_token(client: TestClient, store: _FakeStore) -> None:
    assert (
        client.post("/alerts/subscriptions", json=_SUB_BODY).status_code == 401
    )
    assert client.get("/alerts/subscriptions").status_code == 401
    assert client.delete("/alerts/subscriptions/IB1234-2026-09-01").status_code == 401
    assert client.get("/alerts").status_code == 401
    assert client.patch("/alerts/1/read").status_code == 401


def test_subscriptions_invalid_token_returns_401(
    client: TestClient, store: _FakeStore
) -> None:
    headers = {"Authorization": "Bearer not-a-valid-jwt"}
    assert (
        client.post(
            "/alerts/subscriptions", json=_SUB_BODY, headers=headers
        ).status_code
        == 401
    )
    assert client.get("/alerts/subscriptions", headers=headers).status_code == 401
    assert client.get("/alerts", headers=headers).status_code == 401


def test_subscriptions_wrong_scheme_returns_401(
    client: TestClient, store: _FakeStore
) -> None:
    headers = {"Authorization": "Basic dXNlcjpwYXNz"}
    assert client.get("/alerts/subscriptions", headers=headers).status_code == 401


# ===================================================================
# Suscripciones — CRUD
# ===================================================================


def test_create_subscription_returns_201(client: TestClient, store: _FakeStore) -> None:
    resp = client.post(
        "/alerts/subscriptions", json=_SUB_BODY, headers=_auth("user-1")
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == "user-1"
    assert body["flight_key"] == "IB1234-2026-09-01"
    assert body["flight_number"] == "IB1234"
    assert body["threshold_minutes"] == 60
    assert body["email"] is None
    assert body["created_at"]
    assert body["updated_at"]


def test_create_subscription_with_email_and_threshold(
    client: TestClient, store: _FakeStore
) -> None:
    body = {**_SUB_BODY, "threshold_minutes": 30, "email": "ana@example.com"}
    resp = client.post(
        "/alerts/subscriptions", json=body, headers=_auth("user-1")
    )

    assert resp.status_code == 201
    assert resp.json()["threshold_minutes"] == 30
    assert resp.json()["email"] == "ana@example.com"


def test_create_subscription_upsert_updates_not_duplicates(
    client: TestClient, store: _FakeStore
) -> None:
    client.post("/alerts/subscriptions", json=_SUB_BODY, headers=_auth("user-1"))
    updated = {**_SUB_BODY, "threshold_minutes": 15}
    resp = client.post(
        "/alerts/subscriptions", json=updated, headers=_auth("user-1")
    )

    assert resp.status_code == 201
    assert resp.json()["threshold_minutes"] == 15
    assert len(store.list_subscriptions("user-1")) == 1


def test_create_subscription_validation_422(
    client: TestClient, store: _FakeStore
) -> None:
    # Falta flight_number
    missing = {k: v for k, v in _SUB_BODY.items() if k != "flight_number"}
    assert (
        client.post(
            "/alerts/subscriptions", json=missing, headers=_auth("user-1")
        ).status_code
        == 422
    )
    # threshold_minutes inválido
    bad_threshold = {**_SUB_BODY, "threshold_minutes": 0}
    assert (
        client.post(
            "/alerts/subscriptions", json=bad_threshold, headers=_auth("user-1")
        ).status_code
        == 422
    )
    # email mal formado
    bad_email = {**_SUB_BODY, "email": "no-es-un-email"}
    assert (
        client.post(
            "/alerts/subscriptions", json=bad_email, headers=_auth("user-1")
        ).status_code
        == 422
    )
    # Campo extra no permitido
    extra = {**_SUB_BODY, "admin": True}
    assert (
        client.post(
            "/alerts/subscriptions", json=extra, headers=_auth("user-1")
        ).status_code
        == 422
    )


def test_list_subscriptions_scoped_per_user(
    client: TestClient, store: _FakeStore
) -> None:
    client.post("/alerts/subscriptions", json=_SUB_BODY, headers=_auth("user-1"))

    resp_a = client.get("/alerts/subscriptions", headers=_auth("user-1"))
    resp_b = client.get("/alerts/subscriptions", headers=_auth("user-2"))

    assert resp_a.status_code == 200
    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["user_id"] == "user-1"
    assert resp_b.status_code == 200
    assert resp_b.json() == []


def test_delete_subscription_returns_204(client: TestClient, store: _FakeStore) -> None:
    client.post("/alerts/subscriptions", json=_SUB_BODY, headers=_auth("user-1"))

    resp = client.delete(
        "/alerts/subscriptions/IB1234-2026-09-01", headers=_auth("user-1")
    )

    assert resp.status_code == 204
    assert store.list_subscriptions("user-1") == []


def test_delete_subscription_other_user_returns_404(
    client: TestClient, store: _FakeStore
) -> None:
    client.post("/alerts/subscriptions", json=_SUB_BODY, headers=_auth("user-1"))

    resp = client.delete(
        "/alerts/subscriptions/IB1234-2026-09-01", headers=_auth("user-2")
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Subscription not found"
    # La suscripción de user-1 sigue intacta
    assert len(store.list_subscriptions("user-1")) == 1


def test_delete_subscription_missing_returns_404(
    client: TestClient, store: _FakeStore
) -> None:
    resp = client.delete(
        "/alerts/subscriptions/no-existe", headers=_auth("user-1")
    )

    assert resp.status_code == 404


# ===================================================================
# Alertas — listado y estado de lectura
# ===================================================================


def test_list_alerts_returns_200(client: TestClient, store: _FakeStore) -> None:
    store.insert_alert("user-1", "IB1234-2026-09-01", "alta", 45.0, {"weather": True})

    resp = client.get("/alerts", headers=_auth("user-1"))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["user_id"] == "user-1"
    assert body[0]["severity"] == "alta"
    assert body[0]["delay_minutes_predicted"] == 45.0
    assert body[0]["read"] is False


def test_list_alerts_scoped_per_user(client: TestClient, store: _FakeStore) -> None:
    store.insert_alert("user-1", "fk1", "alta", 45.0, {"weather": True})
    store.insert_alert("user-2", "fk2", "moderada", 20.0, {})

    resp_a = client.get("/alerts", headers=_auth("user-1"))
    resp_b = client.get("/alerts", headers=_auth("user-2"))

    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["flight_key"] == "fk1"
    assert len(resp_b.json()) == 1
    assert resp_b.json()[0]["flight_key"] == "fk2"


def test_list_alerts_filter_by_read(client: TestClient, store: _FakeStore) -> None:
    store.insert_alert("user-1", "fk1", "alta", 45.0, {"weather": True})
    store.insert_alert("user-1", "fk2", "moderada", 20.0, {})
    store.mark_alert_read(2)

    unread = client.get("/alerts", params={"read": "false"}, headers=_auth("user-1"))
    read = client.get("/alerts", params={"read": "true"}, headers=_auth("user-1"))

    assert unread.status_code == 200
    assert [a["id"] for a in unread.json()] == [1]
    assert [a["id"] for a in read.json()] == [2]


def test_mark_alert_read_returns_200(client: TestClient, store: _FakeStore) -> None:
    alert_id = store.insert_alert(
        "user-1", "fk1", "alta", 45.0, {"weather": True}
    )

    resp = client.patch(f"/alerts/{alert_id}/read", headers=_auth("user-1"))

    assert resp.status_code == 200
    assert resp.json()["read"] is True
    assert store.list_alerts("user-1")[0]["read"] is True


def test_mark_alert_read_other_user_returns_404(
    client: TestClient, store: _FakeStore
) -> None:
    alert_id = store.insert_alert(
        "user-1", "fk1", "alta", 45.0, {"weather": True}
    )

    resp = client.patch(f"/alerts/{alert_id}/read", headers=_auth("user-2"))

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Alert not found"
    # La alerta de user-1 sigue sin leer
    assert store.list_alerts("user-1")[0]["read"] is False


def test_mark_alert_read_missing_returns_404(
    client: TestClient, store: _FakeStore
) -> None:
    resp = client.patch("/alerts/999/read", headers=_auth("user-1"))

    assert resp.status_code == 404
