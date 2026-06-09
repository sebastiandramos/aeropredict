"""TokenManager para autenticación OAuth2 con OpenSky Network."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
TOKEN_REFRESH_MARGIN = 30  # segundos antes de expiración para renovar


class TokenManager:
    """Gestiona el token OAuth2 con renovación automática.

    Uso:
        tokens = TokenManager(client_id="...", client_secret="...")
        response = requests.get("https://opensky-network.org/api/states/all",
                                headers=tokens.headers())
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def get_token(self) -> str:
        """Devuelve un token válido, renovándolo si es necesario."""
        if self._token and self._expires_at and datetime.now(UTC) < self._expires_at:
            return self._token
        return self._refresh()

    def headers(self) -> dict[str, str]:
        """Cabeceras HTTP con Bearer token para requests."""
        return {"Authorization": f"Bearer {self.get_token()}"}

    def _refresh(self) -> str:
        """Obtiene un nuevo token del servidor de autenticación."""
        logger.info("Renovando token OAuth2 de OpenSky...")
        r = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        expires_in = data.get("expires_in", 1800)
        self._expires_at = datetime.now(UTC) + timedelta(
            seconds=expires_in - TOKEN_REFRESH_MARGIN
        )
        logger.info("Token renovado (expira en %ds)", expires_in)
        return self._token
