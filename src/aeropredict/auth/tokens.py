"""Emisión y verificación de tokens JWT (capa intercambiable, OAuth-ready).

Esta capa define un contrato fijo de tokens: un JWT firmado con HS256 que
lleva los claims estándar ``sub`` (user_id), ``iat``, ``exp`` e ``iss``.

Es **agnóstica al emisor**: no conoce ni le importa cómo se autenticó el
usuario (email/contraseña u OAuth de terceros). Un futuro proveedor OAuth
puede emitir o validar el MISMO JWT sin reescribir la lógica de
suscripciones/alertas/scope, porque la verificación solo valida un token,
no un método de login.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt

# Issuer por defecto para los tokens emitidos por esta aplicación.
DEFAULT_ISSUER = "aeropredict"
# Algoritmo de firma. HS256 (HMAC-SHA256) con el secreto compartido.
SIGNING_ALGORITHM = "HS256"
# Variable de entorno que debe contener el secreto de firma.
JWT_SECRET_ENV = "JWT_SECRET"
# Entorno en el que se permite un fallback de desarrollo (sin secreto real).
DEV_ENV = "dev"


def get_jwt_secret() -> str:
    """Devuelve el secreto JWT desde ``JWT_SECRET``.

    Lanza ``RuntimeError`` nombrando la variable ausente si no está definida,
    salvo en entorno de desarrollo (``AEROPREDICT_ENV=dev``), donde usa un
    fallback documentado para facilitar el desarrollo local.
    """
    secret = os.environ.get(JWT_SECRET_ENV)
    if secret:
        return secret
    if os.environ.get("AEROPREDICT_ENV") == DEV_ENV:
        # Fallback SOLO para desarrollo local. Nunca usar en producción.
        return "dev-only-insecure-secret-do-not-use-in-prod"
    raise RuntimeError(
        f"Falta la variable de entorno '{JWT_SECRET_ENV}'. "
        "Defínela antes de emitir o verificar tokens."
    )


def create_token(
    user_id: str,
    *,
    issuer: str = DEFAULT_ISSUER,
    expires_in_minutes: int = 1440,
    now: datetime | None = None,
) -> str:
    """Emite un JWT firmado con los claims ``sub``/``iat``/``exp``/``iss``.

    Args:
        user_id: Identificador del usuario (claim ``sub``).
        issuer: Emisor del token (claim ``iss``).
        expires_in_minutes: Validez del token en minutos (claim ``exp``).
        now: Instante de emisión (claim ``iat``). Por defecto, ahora UTC.

    Returns:
        El token JWT firmado (str).
    """
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=expires_in_minutes)
    payload = {
        "sub": user_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": issuer,
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=SIGNING_ALGORITHM)


def verify_token(token: str, *, now: datetime | None = None) -> str | None:
    """Verifica firma, expiración e issuer; devuelve el ``sub`` o None.

    Nunca lanza sobre entrada inválida: cualquier fallo (token corrupto,
    expirado, issuer incorrecto, firma inválida) devuelve ``None``.

    Args:
        token: El JWT a verificar.
        now: Instante de referencia para la expiración. Por defecto, ahora UTC.

    Returns:
        El ``sub`` (user_id) si el token es válido, o ``None`` en cualquier
        otro caso.
    """
    try:
        payload = jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=[SIGNING_ALGORITHM],
            issuer=DEFAULT_ISSUER,
            options={"verify_iss": True},
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


def decode_claims(token: str) -> dict | None:
    """Devuelve los claims crudos del token sin verificar firma ni expiración.

    Pensado para que un futuro proveedor OAuth inspeccione el contenido del
    token. Devuelve ``None`` si el token no es un JWT decodificable.

    Args:
        token: El JWT a inspeccionar.

    Returns:
        Un dict con los claims, o ``None`` si no se puede decodificar.
    """
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None


__all__ = [
    "DEFAULT_ISSUER",
    "SIGNING_ALGORITHM",
    "create_token",
    "decode_claims",
    "get_jwt_secret",
    "verify_token",
]
