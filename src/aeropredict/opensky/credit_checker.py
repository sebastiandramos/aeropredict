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
from .client_pool import ClientPool
from .config import get_all_credentials

logger = logging.getLogger(__name__)

# Cabeceras de rate limiting que devuelve OpenSky
HEADER_REMAINING = "X-Rate-Limit-Remaining"
HEADER_RETRY_AFTER = "X-Rate-Limit-Retry-After-Seconds"

# Endpoint más barato para probar créditos de flights (1h de rango = 4 créditos)
PROBE_ENDPOINT = "/flights/arrival"
PROBE_PARAMS = {"airport": "LEMD"}


def _read_credit_headers(resp: requests.Response) -> dict[str, Any]:
    """Extrae remaining / retry-after de las cabeceras HTTP de OpenSky."""
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


def _probe_via_pool(
    pool: ClientPool,
    url: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Sondea créditos probando cada cuenta del pool hasta encontrar una activa.

    Respeta el puntero ``current_index`` del pool: empieza por la cuenta que
    el pool está usando actualmente y rota si recibe 429.  Si una cuenta
    responde bien, actualiza ``current_index`` para que el pool la use.
    """
    n = pool.account_count
    start = pool.current_index

    for attempt in range(n):
        idx = (start + attempt) % n
        client = pool.clients[idx]
        name = pool.names[idx]

        try:
            resp = requests.get(
                url,
                params=params,
                headers=client._get_headers(),
                timeout=15,
            )

            if resp.status_code == 429:
                logger.warning(
                    "Credit probe: cuenta=%s rate limited, rotando...", name,
                )
                continue

            # Si el probe funcionó, actualizar la cuenta activa del pool
            if idx != pool.current_index:
                logger.info(
                    "Credit probe rotó cuenta activa a %s", name,
                )
                pool.current_index = idx

            return _read_credit_headers(resp)

        except requests.RequestException as e:
            logger.warning(
                "Credit probe error con cuenta=%s: %s", name, e,
            )
            continue

    return {
        "remaining": -1,
        "retry_after": None,
        "reset_at": None,
        "success": False,
        "error": "Todas las cuentas del pool rate limited o con error",
    }


def check_credits(
    endpoint_bucket: str = "flights",
    pool: ClientPool | None = None,
) -> dict[str, Any]:
    """Consulta créditos disponibles para un bucket de la API.

    Hace una petición GET ligera a un endpoint del bucket y lee las
    cabeceras de rate limiting.  La petición puede fallar con 404 si
    no hay datos en el rango, pero igualmente devuelve las cabeceras
    de créditos.

    Args:
        endpoint_bucket: Bucket a consultar ('flights', 'states', 'tracks').
        pool: Si se proporciona un ClientPool, sondea cada cuenta del pool
              rotando en 429.  Si es ``None``, usa solo ``creds[0]``.

    Returns:
        Diccionario con:
            - remaining: créditos restantes (int, -1 si no se pudo obtener).
            - retry_after: segundos hasta reset (int | None).
            - reset_at: datetime estimado de reset (datetime | None).
            - success: True si se obtuvo respuesta válida.
            - error: mensaje de error si success=False.
    """
    url = f"https://opensky-network.org/api{PROBE_ENDPOINT}"

    # Rango de 2h→1h hacia atrás (mínimo coste: 4 créditos para flights)
    now = datetime.now(UTC)
    begin = int((now - timedelta(hours=2)).timestamp())
    end = int((now - timedelta(hours=1)).timestamp())
    params = {**PROBE_PARAMS, "begin": begin, "end": end}

    if pool is not None:
        return _probe_via_pool(pool, url, params)

    # ---- Fallback: una sola cuenta ----
    creds = get_all_credentials()
    if not creds:
        return {
            "remaining": -1,
            "retry_after": None,
            "reset_at": None,
            "success": False,
            "error": (
                "No credentials configured "
                "(no accounts in get_all_credentials())"
            ),
        }

    tm = TokenManager(creds[0]["id"], creds[0]["secret"])
    try:
        resp = requests.get(
            url,
            params=params,
            headers=tm.headers(),
            timeout=15,
        )
        return _read_credit_headers(resp)

    except requests.RequestException as e:
        logger.warning("Error consultando créditos: %s", e)
        return {
            "remaining": -1,
            "retry_after": None,
            "reset_at": None,
            "success": False,
            "error": str(e),
        }


def can_extract(
    min_required: int = 2000,
    pool: ClientPool | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Verifica si hay créditos suficientes para extraer.

    Args:
        min_required: Número mínimo de créditos necesario.
        pool: ClientPool opcional para sondear todas las cuentas.

    Returns:
        Tupla (ok, info) donde:
            - ok: True si remaining >= min_required.
            - info: diccionario completo de check_credits().
    """
    info = check_credits(pool=pool)
    if not info["success"]:
        logger.error(
            "No se pudieron verificar créditos: %s", info.get("error"),
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
