"""Pool de clientes OpenSky con rotación automática entre cuentas.

Cuando una cuenta recibe rate limiting (HTTP 429), el pool rota
automáticamente a la siguiente cuenta disponible.

Uso::

    pool = ClientPool(get_all_credentials())
    data = pool.get("/flights/arrival", params={...})
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from .client import OpenSkyClient

logger = logging.getLogger(__name__)


class ClientPool:
    """Administra N cuentas OpenSky rotando cuando una es rate limited.

    Args:
        credentials: Lista de dicts con claves ``name``, ``id``, ``secret``.
        timeout: Timeout en segundos para cada petición HTTP.
    """

    def __init__(
        self,
        credentials: list[dict[str, str]],
        timeout: int = 60,
    ) -> None:
        if not credentials:
            raise ValueError("Se necesita al menos una cuenta OpenSky")

        self._clients: list[OpenSkyClient] = []
        self._names: list[str] = []
        self._current: int = 0

        for cred in credentials:
            self._clients.append(
                OpenSkyClient(
                    client_id=cred["id"],
                    client_secret=cred["secret"],
                    timeout=timeout,
                ),
            )
            self._names.append(cred.get("name", "?"))

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def current_name(self) -> str:
        """Nombre de la cuenta activa actualmente."""
        return self._names[self._current]

    @property
    def account_count(self) -> int:
        """Número total de cuentas configuradas."""
        return len(self._clients)

    # ------------------------------------------------------------------
    # Método público principal
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Petición GET con rotación automática en 429.

        Intenta con la cuenta activa. Si recibe 429, rota a la siguiente
        cuenta. Si todas las cuentas están rate limited, lanza error.

        Args:
            path: Ruta relativa de la API (ej. ``/flights/arrival``).
            params: Parámetros de query string.

        Returns:
            Respuesta JSON de la API.

        Raises:
            RuntimeError: Si todas las cuentas están rate limited.
            requests.HTTPError: Para errores HTTP que no sean 429.
        """
        start_idx = self._current
        n = self.account_count

        for attempt in range(n):
            idx = (start_idx + attempt) % n
            client = self._clients[idx]
            name = self._names[idx]

            logger.info(
                "Pool get(%s) → cuenta=%s (intento %d/%d)",
                path, name, attempt + 1, n,
            )

            try:
                result = client.get(path, params)
                # Actualizar puntero a la cuenta que funcionó
                if idx != self._current:
                    logger.info("Pool rotado a cuenta=%s como activa", name)
                    self._current = idx
                return result

            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    logger.warning(
                        "Pool: cuenta=%s rate limited (429), "
                        "rotando a siguiente...",
                        name,
                    )
                    continue
                # Cualquier otro HTTPError (401, 404, 500...) se propaga
                raise

        # Todas las cuentas agotadas
        msg = (
            f"Todas las {n} cuentas OpenSky rate limited para {path}. "
            "Esperar a que se restablezcan los créditos."
        )
        logger.error(msg)
        raise RuntimeError(msg)
