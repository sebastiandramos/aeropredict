"""Capa de autenticación — tokens JWT intercambiables.

Expone la emisión y verificación de tokens JWT con un contrato fijo
(claims ``sub``/``iat``/``exp``/``iss``). La capa es agnóstica al emisor:
no sabe ni le importa cómo se autenticó el usuario (email/contraseña u
OAuth de terceros); solo emite y valida tokens con el mismo formato.
"""
from __future__ import annotations

from .tokens import create_token, decode_claims, get_jwt_secret, verify_token

__all__ = [
    "create_token",
    "decode_claims",
    "get_jwt_secret",
    "verify_token",
]
