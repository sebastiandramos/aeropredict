"""Script: NOTAM de ENAIRE (servAIS ArcGIS) → Bronze (Delta Lake).

Recolecta las NOTAM del FeatureServer público de ENAIRE (capa 0 "Puntos" y
capa 1 "Áreas") y persiste un snapshot diario en la tabla
``bronze/notam_enaire`` vía ``write_raw_snapshot`` (mode="overwrite": la
tabla contiene solo el último estado del servicio; skip-si-vacío integrado).

Auth-gate: si el servicio activa autenticación (HTTP 401/403), el run
termina con exit 0 (fallo graceful, decisión del usuario — NO se envía
email a ENAIRE) sin tocar el snapshot bueno previo. 5xx / errores de red
terminan con exit 1.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import requests

from aeropredict.opensky.config import get_delta_root
from aeropredict.opensky.storage import write_raw_snapshot
from aeropredict.sources.notam_enaire import NOTAM_FEATURE_SERVER_URL, NotamEnaireAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TABLE_NAME = "notam_enaire"

# Capas del FeatureServer NOTAM_APP_V3: 0 = "Puntos", 1 = "Áreas".
LAYERS = (0, 1)

# Params de la escritura en Bronze (identifican el snapshot del run).
SNAPSHOT_PARAMS = {"layers": "0,1"}


def collect_notam(dry_run: bool = False, delta_root: str = "data/raw") -> dict[str, int]:
    """Recolecta las NOTAM de ambas capas y persiste un snapshot en Bronze.

    Consulta las dos capas con el mismo ``snapshot_at`` y escribe UNA fila
    vía ``write_raw_snapshot`` (mode="overwrite") con ambas capas embebidas:
    dos llamadas por capa se borrarían entre sí (lección del todo 5). Si
    ninguna capa devuelve datos no se escribe nada y el snapshot previo
    queda intacto. HTTP 401/403 se registran como auth-gate (fallo graceful);
    el resto de errores HTTP/de red como errores reales.

    Args:
        dry_run: Solo mostrar lo que haría, sin consultar ni escribir.
        delta_root: Ruta base Delta.

    Returns:
        Stats: total, notam_written, skipped, errors, auth_gated.
    """
    total = len(LAYERS)
    if dry_run:
        for layer in LAYERS:
            logger.info("  [dry-run] Consultaría la capa %d del FeatureServer NOTAM", layer)
        return {
            "total": total, "notam_written": 0, "skipped": 0, "errors": 0, "auth_gated": 0,
        }

    snapshot_at = datetime.now(UTC).replace(microsecond=0)
    adapter = NotamEnaireAdapter()
    layers_data: list[dict[str, Any]] = []
    skipped = 0
    errors = 0
    auth_gated = 0

    for layer in LAYERS:
        try:
            payload = adapter.get_notam_layer(layer)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (401, 403):
                logger.warning(
                    "Auth gate (HTTP %d) en la capa %d — fallo graceful (decisión del usuario)",
                    status, layer,
                )
                auth_gated += 1
            else:
                logger.error("HTTP %d al consultar la capa %d de NOTAM", status, layer)
            errors += 1
        except requests.RequestException as exc:
            logger.error("Error de red consultando la capa %d de NOTAM: %s", layer, exc)
            errors += 1
        else:
            if payload is None or payload.get("count", 0) == 0:
                logger.info("Capa %d de NOTAM vacía (skip)", layer)
                skipped += 1
            else:
                layers_data.append(payload)

    if not layers_data:
        logger.info(
            "Sin datos de NOTAM en ninguna capa (errors=%d, skipped=%d) — "
            "snapshot previo intacto",
            errors, skipped,
        )
        return {
            "total": total, "notam_written": 0,
            "skipped": skipped, "errors": errors, "auth_gated": auth_gated,
        }

    snapshot = {
        "source": TABLE_NAME,
        "snapshot_at": snapshot_at.isoformat(),
        "layers": layers_data,
    }
    write_raw_snapshot(TABLE_NAME, NOTAM_FEATURE_SERVER_URL, SNAPSHOT_PARAMS, snapshot, delta_root)
    total_features = sum(payload["count"] for payload in layers_data)
    logger.info(
        "NOTAM guardado en Bronze: %d capas, %d features en total",
        len(layers_data), total_features,
    )

    return {
        "total": total, "notam_written": len(layers_data),
        "skipped": skipped, "errors": errors, "auth_gated": auth_gated,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Colección de NOTAM (ENAIRE servAIS ArcGIS)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostrar lo que haría sin consultar el servicio",
    )
    args = parser.parse_args(argv)

    stats = collect_notam(dry_run=args.dry_run, delta_root=get_delta_root())

    logger.info("--- Resultados ---")
    logger.info(
        "Total: %d | NOTAM escrito: %d capas | Saltadas: %d | Errores: %d | Auth-gate: %d",
        stats["total"], stats["notam_written"], stats["skipped"],
        stats["errors"], stats["auth_gated"],
    )

    failed = stats["errors"]
    total = stats["total"]
    auth_gated = stats["auth_gated"]
    if failed and failed >= total and failed == auth_gated:
        logger.warning(
            "Auth gate (401/403) en todas las capas — fallo graceful, run completo (exit 0)",
        )
        return 0
    if failed and failed >= total:
        logger.error("Todas las capas fallaron — collector run failed")
        return 1
    if failed:
        logger.warning("Fallo parcial: %d/%d — el run continúa", failed, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
