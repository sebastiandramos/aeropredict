"""Tests de los endpoints de auth (todo 5, mis-vuelos-alertas).

Patrón TestClient + monkeypatch: sin red, sin DB real, sin Doppler. Se
monkeypatchean ``aeropredict.app.persistence.create_user`` y
``get_user_by_email`` con fakes en memoria, y se fija un ``JWT_SECRET``
determinista para poder verificar los tokens emitidos.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeropredict.api.server import app
from aeropredict.auth import verify_token

# ===================================================================
# Fakes en memoria
# ===================================================================


class _FakeUsers:
    """Almacén de usuarios en memoria con hash bcrypt real."""

    def __init__(self) -> None:
        self._users: dict[str, dict[str, Any]] = {}
        self._by_email: dict[str, str] = {}

    def create_user(
        self,
        email: str,
        password_hash: str,
        auth_method: str = "email",
    ) -> dict[str, Any] | None:
        if email in self._by_email:
            return None
        user = {
            "user_id": f"user-{len(self._users) + 1}",
            "email": email,
            "password_hash": password_hash,
            "auth_method": auth_method,
        }
        self._users[user["user_id"]] = user
        self._by_email[email] = user["user_id"]
        return dict(user)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        user_id = self._by_email.get(email)
        if user_id is None:
            return None
        return dict(self._users[user_id])


@pytest.fixture
def fake_users(monkeypatch: pytest.MonkeyPatch) -> _FakeUsers:
    """Monkeypatchea la persistencia con un almacén en memoria."""
    store = _FakeUsers()
    monkeypatch.setattr(
        "aeropredict.app.persistence.create_user", store.create_user
    )
    monkeypatch.setattr(
        "aeropredict.app.persistence.get_user_by_email", store.get_user_by_email
    )
    return store


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija un secreto determinista para todos los tests."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-aeropredict-tests")


@pytest.fixture
def client() -> TestClient:
    """TestClient sin lifespan (no carga el modelo MLflow)."""
    return TestClient(app)


# ===================================================================
# Registro
# ===================================================================


def test_register_returns_201_with_valid_jwt(client: TestClient, fake_users: _FakeUsers) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "password123"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "ana@example.com"
    assert body["user_id"] == "user-1"
    assert body["expires_in"] == 1440
    # El token es un JWT válido cuyo sub coincide con el user_id devuelto.
    assert verify_token(body["token"]) == body["user_id"]


def test_register_duplicate_email_returns_409(client: TestClient, fake_users: _FakeUsers) -> None:
    client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "password123"},
    )

    resp = client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "otrapassword"},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Email already registered"


def test_register_short_password_returns_422(client: TestClient, fake_users: _FakeUsers) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "short"},
    )

    assert resp.status_code == 422


def test_register_invalid_email_returns_422(client: TestClient, fake_users: _FakeUsers) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": "no-es-un-email", "password": "password123"},
    )

    assert resp.status_code == 422


def test_register_stores_bcrypt_hash_not_plaintext(
    client: TestClient, fake_users: _FakeUsers
) -> None:
    client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "password123"},
    )

    stored = fake_users.get_user_by_email("ana@example.com")
    assert stored is not None
    assert stored["password_hash"] != "password123"
    assert stored["password_hash"].startswith("$2")


# ===================================================================
# Login
# ===================================================================


def test_login_ok_returns_200_with_token(client: TestClient, fake_users: _FakeUsers) -> None:
    client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "password123"},
    )

    resp = client.post(
        "/auth/login",
        json={"email": "ana@example.com", "password": "password123"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "ana@example.com"
    assert body["user_id"] == "user-1"
    assert body["expires_in"] == 1440
    assert verify_token(body["token"]) == body["user_id"]


def test_login_wrong_password_returns_401(client: TestClient, fake_users: _FakeUsers) -> None:
    client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "password123"},
    )

    resp = client.post(
        "/auth/login",
        json={"email": "ana@example.com", "password": "wrong-password"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


def test_login_unknown_email_returns_401(client: TestClient, fake_users: _FakeUsers) -> None:
    resp = client.post(
        "/auth/login",
        json={"email": "nadie@example.com", "password": "password123"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


def test_login_unknown_email_indistinguishable_from_wrong_password(
    client: TestClient, fake_users: _FakeUsers
) -> None:
    """El mensaje de error es idéntico para ambos casos (anti-enumeración)."""
    client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "password123"},
    )

    wrong_pw = client.post(
        "/auth/login",
        json={"email": "ana@example.com", "password": "wrong-password"},
    )
    unknown = client.post(
        "/auth/login",
        json={"email": "nadie@example.com", "password": "password123"},
    )

    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


# ===================================================================
# Higiene de respuestas
# ===================================================================


def test_auth_responses_never_contain_password_or_hash(
    client: TestClient, fake_users: _FakeUsers
) -> None:
    register_resp = client.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": "password123"},
    )
    login_resp = client.post(
        "/auth/login",
        json={"email": "ana@example.com", "password": "password123"},
    )

    for resp in (register_resp, login_resp):
        assert resp.status_code in (200, 201)
        raw = resp.text
        assert "password" not in raw.lower()
        assert "password123" not in raw
        assert "$2" not in raw  # sin hash bcrypt en la respuesta
