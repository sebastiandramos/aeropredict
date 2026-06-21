"""Pool de clientes OpenSky con rotación automática entre cuentas.

Cuando una cuenta recibe rate limiting (HTTP 429), el pool rota
automáticamente a la siguiente cuenta disponible.

Este módulo extiende el :class:`Pool` genérico con la lógica específica
de OpenSky: rotación en 429 y clientes autenticados.

Uso::

    pool = ClientPool(get_all_credentials())
    data = pool.get("/flights/arrival", params={...})
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from .client import OpenSkyClient
from .pool import Pool

logger = logging.getLogger(__name__)


class ClientPool:
    """Pool de cuentas OpenSky con rotación automática en rate limiting.

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

        self._names: list[str] = [cred.get("name", "?") for cred in credentials]

        self._pool: Pool[OpenSkyClient] = Pool(
            items=[
                OpenSkyClient(
                    client_id=cred["id"],
                    client_secret=cred["secret"],
                    timeout=timeout,
                )
                for cred in credentials
            ],
            should_rotate=_is_rate_limited,
            labels=self._names,
        )

    # ------------------------------------------------------------------
    # Propiedades (API pública para credit_checker y otros)
    # ------------------------------------------------------------------

    @property
    def clients(self) -> list[OpenSkyClient]:
        """Lista de clientes OpenSky."""
        return self._pool.items

    @property
    def names(self) -> list[str]:
        """Nombres de cada cuenta."""
        return self._names

    @property
    def current_index(self) -> int:
        """Índice de la cuenta activa actualmente."""
        return self._pool.current_index

    @current_index.setter
    def current_index(self, idx: int) -> None:
        self._pool.current_index = idx

    @property
    def current_name(self) -> str:
        """Nombre de la cuenta activa actualmente."""
        return self._names[self._pool.current_index]

    @property
    def account_count(self) -> int:
        """Número total de cuentas configuradas."""
        return self._pool.item_count

    # ------------------------------------------------------------------
    # Método público principal
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Petición GET con rotación automática en 429.

        Args:
            path: Ruta relativa de la API (ej. ``/flights/arrival``).
            params: Parámetros de query string.

        Returns:
            Respuesta JSON de la API.

        Raises:
            RuntimeError: Si todas las cuentas están rate limited.
            requests.HTTPError: Para errores HTTP que no sean 429.
        """
        return self._pool.execute(
            lambda client: client.get(path, params),
            context=path,
        )


# ------------------------------------------------------------------
# Helper de rotación
# ------------------------------------------------------------------


def _is_rate_limited(e: Exception) -> bool:
    """True si la excepción es un 429 HTTP (rate limited)."""
    return (
        isinstance(e, requests.HTTPError)
        and e.response is not None
        and e.response.status_code == 429
    )
