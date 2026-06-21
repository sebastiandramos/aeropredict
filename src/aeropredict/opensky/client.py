"""Cliente HTTP base para la API REST de OpenSky Network.

Maneja autenticación, reintentos, rate limiting y errores HTTP.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .auth import TokenManager

logger = logging.getLogger(__name__)

BASE_URL = "https://opensky-network.org/api"
MAX_RETRIES = 3
RETRY_DELAY = 2.0  # segundos entre reintentos


class OpenSkyClient:
    """Cliente HTTP para la API REST de OpenSky.

    Args:
        client_id: Credencial OAuth2 (opcional, modo anónimo si se omite).
        client_secret: Credencial OAuth2 (opcional).
        timeout: Timeout en segundos para cada petición.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: int = 60,
    ) -> None:
        self._session = requests.Session()
        self._timeout = timeout
        self._token_manager: TokenManager | None = None

        if client_id and client_secret:
            self._token_manager = TokenManager(client_id, client_secret)

    def _get_headers(self) -> dict[str, str]:
        """Cabeceras para la petición (con token si autenticado)."""
        if self._token_manager:
            return self._token_manager.headers()
        return {}

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Petición GET con reintentos y control de rate limiting.

        Args:
            path: Ruta relativa (ej. '/states/all').
            params: Parámetros de query string.

        Returns:
            Respuesta JSON como dict.

        Raises:
            requests.HTTPError: Si el error persiste tras reintentos.
        """
        url = f"{BASE_URL}{path}"
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                    timeout=self._timeout,
                )

                # Rate limiting - propagar inmediatamente para que ClientPool rote
                if resp.status_code == 429:
                    logger.warning(
                        "Rate limited (429) en cuenta. ClientePool rotará.",
                    )
                    resp.raise_for_status()

                # 401 - token expirado, forzar refresh en siguiente intento
                if resp.status_code == 401 and self._token_manager:
                    logger.info("Token expirado (401), renovando...")
                    self._token_manager._refresh()
                    if attempt < MAX_RETRIES:
                        continue
                    resp.raise_for_status()

                resp.raise_for_status()
                return resp.json()

            except requests.Timeout as e:
                last_error = e
                logger.warning(
                    "Timeout en %s (intento %d/%d)", path, attempt, MAX_RETRIES
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    raise

            except requests.ConnectionError as e:
                last_error = e
                logger.warning(
                    "Error de conexión en %s (intento %d/%d)", path, attempt, MAX_RETRIES
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    raise

            except requests.HTTPError as e:
                last_error = e
                if (
                    attempt < MAX_RETRIES
                    and e.response is not None
                    and e.response.status_code >= 500
                ):
                    logger.warning(
                        "Error servidor %d en %s (intento %d/%d)",
                        e.response.status_code,
                        path,
                        attempt,
                        MAX_RETRIES,
                    )
                    time.sleep(RETRY_DELAY)
                else:
                    raise

        msg = f"Fallo tras {MAX_RETRIES} intentos en {path}"
        raise RuntimeError(msg) from last_error



