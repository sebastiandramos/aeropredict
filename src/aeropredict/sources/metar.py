"""NOAA Aviation Weather Center (AWC) METAR adapter.

Fuente: https://aviationweather.gov/api/data/metar — gratuita, sin API key.
La API devuelve un JSON **array** de informes METAR dentro de la ventana
``hours``; si no hay datos responde HTTP 204 con cuerpo vacío (que
``http_get_with_retry`` normaliza a ``{}``). Los informes se aplanan a
columnas CSV tipadas con los helpers ``_safe_*`` de ``BaseAdapter``.

Verificado en vivo 2026-08-03: ``ids=LEMD,LEBL&format=json&taf=false&hours=48``
→ HTTP 200 con array; ``ids=XXXX`` → HTTP 204. La API no limita ``hours``
más allá de los 15 días de retención del servicio.
"""

from __future__ import annotations

import logging
from typing import Any

from aeropredict.sources.base import BaseAdapter, http_get_with_retry

logger = logging.getLogger(__name__)

METAR_URL = "https://aviationweather.gov/api/data/metar"

# Máximo de códigos ICAO por petición (62 en AEROPUERTOS → 2 peticiones).
MAX_ICAOS_PER_REQUEST = 54

# Columnas del CSV aplanado en Bronze (una fila por informe METAR).
CSV_FIELDS = [
    "icaoId",
    "rawOb",
    "receiptTime",
    "obsTime",
    "temp",
    "dewp",
    "wdir",
    "wspd",
    "wgst",
    "visib",
    "altim",
    "fltCat",
    "clouds_base",
]


def chunk_codes(codes: list[str], size: int = MAX_ICAOS_PER_REQUEST) -> list[list[str]]:
    """Divide códigos ICAO en lotes de como máximo ``size`` códigos."""
    return [codes[i : i + size] for i in range(0, len(codes), size)]


class MetarAWCAdapter(BaseAdapter):
    """Adaptador METAR de la NOAA AWC. Sin API key ni Pool.

    ``get_metars`` agrupa los códigos en lotes de ≤54 y hace una petición
    por lote; el resultado es la unión de todos los informes normalizados.
    """

    def get_metars(
        self, icao_codes: list[str], hours: int = 2,
    ) -> dict[str, Any] | None:
        """Obtiene los METAR de una lista de aeropuertos.

        Args:
            icao_codes: Códigos ICAO a consultar (se agrupan en lotes).
            hours: Ventana temporal hacia atrás en horas.

        Returns:
            ``{"raw": [...], "count": n, "airport_codes": ids}`` con los
            informes aplanados, o ``None`` si la API falla tras los
            reintentos de ``http_get_with_retry``.
        """
        if not icao_codes:
            return None

        reports: list[dict[str, Any]] = []
        for batch in chunk_codes(icao_codes):
            params: dict[str, Any] = {
                "ids": ",".join(batch),
                "format": "json",
                "taf": "false",
                "hours": hours,
            }
            try:
                data = http_get_with_retry(METAR_URL, params=params)
            except Exception as e:
                logger.warning("AWC METAR error para %s: %s", ",".join(batch), e)
                return None
            reports.extend(self._normalize_reports(data))

        return {
            "raw": reports,
            "count": len(reports),
            "airport_codes": icao_codes,
        }

    # -- Normalización -------------------------------------------------------

    def _normalize_reports(self, data: Any) -> list[dict[str, Any]]:
        """Aplana la respuesta JSON de la AWC a filas CSV.

        La API devuelve un array; HTTP 204 (sin datos) llega como ``{}`` y
        también se acepta un dict defensivo con clave ``data``. Los informes
        vacíos (sin ``icaoId`` ni ``rawOb``) se omiten.
        """
        if isinstance(data, dict):
            data = data.get("data", [])
        if not isinstance(data, list):
            return []

        rows: list[dict[str, Any]] = []
        for report in data:
            if not isinstance(report, dict):
                continue
            row = self._normalize_report(report)
            if row["icaoId"] is None and row["rawOb"] is None:
                continue
            rows.append(row)
        return rows

    def _normalize_report(self, report: dict[str, Any]) -> dict[str, Any]:
        """Aplana un informe METAR a las columnas de ``CSV_FIELDS``."""
        clouds = self._safe_get(report, "clouds")
        clouds_base = (
            self._safe_int(clouds[0].get("base"))
            if isinstance(clouds, list) and clouds and isinstance(clouds[0], dict)
            else None
        )
        return {
            "icaoId": self._safe_str(self._safe_get(report, "icaoId")),
            "rawOb": self._safe_str(self._safe_get(report, "rawOb")),
            "receiptTime": self._safe_str(self._safe_get(report, "receiptTime")),
            "obsTime": self._safe_int(self._safe_get(report, "obsTime")),
            "temp": self._safe_float(self._safe_get(report, "temp")),
            "dewp": self._safe_float(self._safe_get(report, "dewp")),
            "wdir": self._safe_int(self._safe_get(report, "wdir")),
            "wspd": self._safe_int(self._safe_get(report, "wspd")),
            "wgst": self._safe_int(self._safe_get(report, "wgst")),
            "visib": self._safe_str(self._safe_get(report, "visib")),
            "altim": self._safe_float(self._safe_get(report, "altim")),
            "fltCat": self._safe_str(self._safe_get(report, "fltCat")),
            "clouds_base": clouds_base,
        }
