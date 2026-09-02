"""Tests de la capa de tokens JWT (todo 4, mis-vuelos-alertas).

Cubre el contrato fijo de la capa intercambiable: emisión, verificación y
decodificación de claims. La capa es agnóstica al emisor (email vs OAuth),
por lo que los tests solo ejercitan el formato del token, no un método de
login.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aeropredict.auth import create_token, decode_claims, get_jwt_secret, verify_token


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija un secreto determinista para todos los tests."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-aeropredict-tests")


def test_roundtrip_returns_user_id() -> None:
    token = create_token("u1")
    assert verify_token(token) == "u1"


def test_expired_token_returns_none() -> None:
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    token = create_token("u1", now=issued_at, expires_in_minutes=1)
    # Verificar en un instante posterior a la expiración.
    later = issued_at + timedelta(minutes=2)
    assert verify_token(token, now=later) is None


def test_tampered_token_returns_none() -> None:
    token = create_token("u1")
    # Corrompe un carácter del SEGMENTO payload (no el último de la firma):
    # la firma HMAC cubre los bytes codificados `header.payload`, así que
    # cualquier cambio en el payload codificado invalida la firma de forma
    # determinista. (Un swap del último carácter de la firma puede dejar los
    # bytes decodificados intactos cuando ese carácter está en {A,B,C,D}.)
    header, payload, signature = token.split(".")
    middle = len(payload) // 2
    flipped = "A" if payload[middle] != "A" else "B"
    tampered = f"{header}.{payload[:middle]}{flipped}{payload[middle + 1:]}.{signature}"
    assert verify_token(tampered) is None
    # El token intacto sigue siendo válido.
    assert verify_token(token) == "u1"


def test_wrong_issuer_returns_none() -> None:
    token = create_token("u1", issuer="evil")
    # La verificación usa el issuer por defecto ("aeropredict").
    assert verify_token(token) is None


def test_create_token_has_expected_claims() -> None:
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    token = create_token("u1", now=issued_at, expires_in_minutes=60)
    claims = decode_claims(token)
    assert claims is not None
    assert claims["sub"] == "u1"
    assert claims["iss"] == "aeropredict"
    assert claims["iat"] == int(issued_at.timestamp())
    assert claims["exp"] == int((issued_at + timedelta(minutes=60)).timestamp())


def test_decode_claims_returns_none_on_garbage() -> None:
    assert decode_claims("not-a-jwt") is None


def test_get_jwt_secret_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AEROPREDICT_ENV", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        get_jwt_secret()


def test_get_jwt_secret_dev_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET")
    monkeypatch.setenv("AEROPREDICT_ENV", "dev")
    assert get_jwt_secret()  # dev fallback no vacío
