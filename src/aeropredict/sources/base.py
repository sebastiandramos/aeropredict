"""Base adapter class with retry/backoff and Pool integration."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

from aeropredict.opensky.pool import Pool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 30  # segundos
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # segundos


# ---------------------------------------------------------------------------
# Utility: low-level HTTP GET con reintentos
# ---------------------------------------------------------------------------


def http_get_with_retry(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """HTTP GET con backoff exponencial y jitter.

    Reintenta en 429, 5xx, timeout y errores de conexión.
    Respeta header ``Retry-After`` si está presente.

    Returns:
        Dict con la respuesta JSON.

    Raises:
        requests.RequestException: si fallan todos los reintentos.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp)
                wait = retry_after if retry_after else _backoff_delay(attempt)
                logger.warning(
                    "HTTP 429: %s (attempt %d/%d) → waiting %.1fs",
                    url, attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except requests.Timeout:
            logger.warning("Timeout: %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            if attempt < MAX_RETRIES:
                time.sleep(_backoff_delay(attempt))
            last_exc = requests.Timeout(f"Timeout after {timeout}s: {url}")
        except requests.ConnectionError as e:
            logger.warning("Connection error: %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            if attempt < MAX_RETRIES:
                time.sleep(_backoff_delay(attempt))
            last_exc = e
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status >= 500 and attempt < MAX_RETRIES:
                logger.warning("HTTP %d: %s (attempt %d/%d)", status, url, attempt, MAX_RETRIES)
                time.sleep(_backoff_delay(attempt))
                last_exc = e
            else:
                raise
        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                time.sleep(_backoff_delay(attempt))
            last_exc = e

    raise requests.RequestException(f"All {MAX_RETRIES} retries failed for {url}") from last_exc


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _backoff_delay(attempt: int) -> float:
    """Backoff exponencial con jitter: base * 2^(attempt-1) + random(0, 0.5)."""
    return BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5)


def _parse_retry_after(resp: requests.Response) -> float | None:
    """Lee header ``Retry-After`` (segundos o fecha HTTP)."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        # Fecha HTTP — complexity no vale la pena, ignoramos
        return None


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------


class BaseAdapter:
    """Adaptador base para fuentes de datos externas.

    Args:
        pool: Pool opcional para rotación de credenciales.
    """

    def __init__(self, pool: Pool[str] | None = None) -> None:
        self.pool = pool

    # -- Métodos públicos ---------------------------------------------------

    def fetch(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ejecuta una petición GET con retry/backoff y rotación de pool.

        Si el adaptador tiene un ``Pool`` configurado, delega la ejecución
        a ``pool.execute()`` que rota automáticamente en caso de 429.

        Args:
            endpoint: URL completa del endpoint.
            params: Parámetros de query string.

        Returns:
            Dict con la respuesta JSON.
        """
        if self.pool is not None:
            return self.pool.execute(
                lambda: self._http_get(endpoint, params),
                context=endpoint,
            )
        return self._http_get(endpoint, params)

    # -- Métodos internos ---------------------------------------------------

    def _http_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """HTTP GET con los headers específicos de la fuente."""
        return http_get_with_retry(endpoint, headers=self._get_headers(), params=params)

    def _get_headers(self) -> dict[str, str]:
        """Headers HTTP específicos de cada fuente (sobrescribir si necesario)."""
        return {}

    # -- Helpers de extracción segura ---------------------------------------

    @staticmethod
    def _safe_get(data: dict[str, Any] | None, *keys: str) -> Any:
        """Acceso anidado seguro a un dict.

        Ejemplo: ``self._safe_get(data, "departure", "scheduled")``
        """
        if data is None:
            return None
        current: Any = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return current

    @staticmethod
    def _log_error(context: str, error: Exception) -> None:
        """Log estructurado de errores."""
        logger.error("[%s] %s: %s", type(error).__name__, context, error)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        """Convierte a int de forma segura."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Convierte a float de forma segura."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_str(value: Any) -> str | None:
        """Convierte a str de forma segura."""
        if value is None:
            return None
        try:
            s = str(value).strip()
            return s if s else None
        except (ValueError, TypeError):
            return None
