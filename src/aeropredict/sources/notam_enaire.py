"""Adaptador del servicio público de NOTAM de ENAIRE (servAIS ArcGIS).

Fuente: https://servais.enaire.es/insignias/rest/services/NOTAM/NOTAM_APP_V3/FeatureServer
Servicio "NOTAM V2 PRE" (pre-producción): expone dos capas — capa 0
"Puntos" y capa 1 "Áreas" — como FeatureCollection GeoJSON vía la API REST
de ArcGIS. Gratuita, sin API key. Verificado en vivo 2026-08-03: capa 0 con
529 features y capa 1 con 873 features (el servicio puede activar
autenticación en el futuro; el collector trata 401/403 como fallo graceful).

No se normaliza el esquema: se persiste el GeoJSON completo tal cual llega;
la promoción a Silver queda como tarea futura.
"""

from __future__ import annotations

import logging
from typing import Any

from aeropredict.sources.base import BaseAdapter, http_get_with_retry

logger = logging.getLogger(__name__)

# URL base del FeatureServer de NOTAM (ArcGIS REST). La capa se añade como
# segmento de path: {NOTAM_FEATURE_SERVER_URL}/{layer}/query
NOTAM_FEATURE_SERVER_URL = (
    "https://servais.enaire.es/insignias/rest/services/NOTAM/NOTAM_APP_V3/FeatureServer"
)


class NotamEnaireAdapter(BaseAdapter):
    """Adaptador de NOTAM de ENAIRE (servAIS ArcGIS). Sin API key ni Pool.

    ``get_notam_layer`` devuelve el FeatureCollection GeoJSON crudo de una
    capa envuelto en metadatos. A diferencia del adapter METAR, los errores
    HTTP (401/403/5xx) y de red NO se tragan: se propagan para que el
    collector distinga fallo graceful (auth-gate) de fallo real (5xx/red).
    """

    def get_notam_layer(self, layer: int) -> dict[str, Any] | None:
        """Obtiene la FeatureCollection GeoJSON de una capa NOTAM.

        Args:
            layer: Número de capa del FeatureServer (0 = "Puntos", 1 = "Áreas").

        Returns:
            ``{"raw": <FeatureCollection>, "count": features, "layer": layer}``,
            o ``None`` si la respuesta no es una FeatureCollection GeoJSON
            válida (sin clave ``features`` de tipo list).
        """
        url = f"{NOTAM_FEATURE_SERVER_URL}/{layer}/query"
        params = {"where": "1=1", "f": "geojson", "outFields": "*"}
        fc = http_get_with_retry(url, params=params)

        # Defensivo: si el servicio superara su transferLimit habría que
        # paginar (resultOffset/resultRecordCount). 529/873 < 3000, sin
        # paginación esperada — solo se avisa.
        if fc.get("exceededTransferLimit"):
            logger.warning(
                "exceededTransferLimit activo en la capa %d — respuesta truncada", layer,
            )

        features = fc.get("features") if isinstance(fc, dict) else None
        if not isinstance(features, list):
            logger.warning("Capa %d: respuesta no es una FeatureCollection GeoJSON válida", layer)
            return None

        return {"raw": fc, "count": len(features), "layer": layer}
