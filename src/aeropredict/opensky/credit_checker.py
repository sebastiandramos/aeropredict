"""Verificación de créditos disponibles en la API de OpenSky.

Cada bucket (/states/*, /tracks/*, /flights/*) tiene su propio límite
diario independiente. Este módulo consulta el estado actual leyendo
las cabeceras HTTP X-Rate-Limit-* de una petición ligera.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from .auth import TokenManager
from .config import get_client_id, get_client_secret

logger = logging.getLogger(__name__)

# Cabeceras de rate limiting que devuelve OpenSky
HEADER_REMAINING = "X-Rate-Limit-Remaining"
HEADER_RETRY_AFTER = "X-Rate-Limit-Retry-After-Seconds"

# Endpoint más barato para probar créditos de flights (1h de rango = 4 créditos)
PROBE_ENDPOINT = "/flights/arrival"
PROBE_PARAMS = {"airport": "LEMD"}


def check_credits(endpoint_bucket: str = "flights") -> dict[str, Any]:
    """Consulta créditos disponibles para un bucket de la API.

    Hace una petición GET ligera a un endpoint del bucket y lee las
    cabeceras de rate limiting.  La petición puede fallar con 404 si
    no hay datos en el rango, pero igualmente devuelve las cabeceras
    de créditos.

    Args:
        endpoint_bucket: Bucket a consultar ('flights', 'states', 'tracks').

    Returns:
        Diccionario con:
            - remaining: créditos restantes (int, -1 si no se pudo obtener).
            - retry_after: segundos hasta reset (int | None).
            - reset_at: datetime estimado de reset (datetime | None).
            - success: True si se obtuvo respuesta válida.
            - error: mensaje de error si success=False.
    """
    tm = TokenManager(get_client_id(), get_client_secret())
    url = f"https://opensky-network.org/api{PROBE_ENDPOINT}"

    # Rango de 1h hacia atrás (mínimo coste: 4 créditos para flights)
    now = datetime.now(UTC)
    begin = int((now - timedelta(hours=2)).timestamp())
    end = int((now - timedelta(hours=1)).timestamp())
    params = {**PROBE_PARAMS, "begin": begin, "end": end}

    try:
        resp = requests.get(
            url,
            params=params,
            headers=tm.headers(),
            timeout=15,
        )

        remaining_str = resp.headers.get(HEADER_REMAINING)
        remaining = int(remaining_str) if remaining_str is not None else -1

        retry_after_str = resp.headers.get(HEADER_RETRY_AFTER)
        retry_after = None
        reset_at = None
        if retry_after_str is not None:
            retry_after = int(retry_after_str)
            reset_at = datetime.now(UTC) + timedelta(seconds=retry_after)

        return {
            "remaining": remaining,
            "retry_after": retry_after,
            "reset_at": reset_at,
            "success": True,
            "error": None,
        }

    except requests.RequestException as e:
        logger.warning("Error consultando créditos: %s", e)
        return {
            "remaining": -1,
            "retry_after": None,
            "reset_at": None,
            "success": False,
            "error": str(e),
        }


def can_extract(min_required: int = 2000) -> tuple[bool, dict[str, Any]]:
    """Verifica si hay créditos suficientes para extraer.

    Args:
        min_required: Número mínimo de créditos necesario.

    Returns:
        Tupla (ok, info) donde:
            - ok: True si remaining >= min_required.
            - info: diccionario completo de check_credits().
    """
    info = check_credits()
    if not info["success"]:
        logger.error(
            "No se pudieron verificar créditos: %s", info.get("error")
        )
        return False, info

    if info["remaining"] < min_required:
        retry = info.get("retry_after")
        reset = info.get("reset_at")
        logger.warning(
            "Créditos insuficientes: %d remaining (mínimo %d). "
            "Retry after: %ss. Reset estimado: %s",
            info["remaining"],
            min_required,
            retry or "?",
            reset.isoformat() if reset else "?",
        )
        return False, info

    logger.info(
        "Créditos suficientes: %d remaining (mínimo %d)",
        info["remaining"],
        min_required,
    )
    return True, info
