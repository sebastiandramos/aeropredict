"""Nager.Date public-holidays adapter (no API key required).

Documentación: https://date.nager.at/swagger/index.html
API pública gratuita con los festivos oficiales (fechas NOMINALES) por país
y año. Sin key ni pool: plantilla OpenMeteoAdapter.
"""

from __future__ import annotations

import logging
from typing import Any

from aeropredict.sources.base import BaseAdapter

logger = logging.getLogger(__name__)

NAGER_BASE_URL = "https://date.nager.at/api/v4/Holidays"


class NagerAdapter(BaseAdapter):
    """Adaptador para Nager.Date (festivos nominales de un país y año)."""

    def get_holidays(self, year: int, country: str = "ES") -> dict[str, Any] | None:
        """Obtiene los festivos públicos de un país para un año.

        Args:
            year: Año de consulta.
            country: Código ISO 3166-1 alpha-2 (default ``ES``).

        Returns:
            Dict normalizado con la lista cruda de la API, o ``None`` si error.
        """
        url = f"{NAGER_BASE_URL}/{country}/{year}"
        try:
            data = self._http_get(url)
        except Exception as e:
            logger.warning("Nager.Date error para %s %s: %s", country, year, e)
            return None

        if not isinstance(data, list):
            logger.warning(
                "Nager.Date respuesta inesperada para %s %s: %s",
                country, year, type(data).__name__,
            )
            return None

        return {
            "raw": data,
            "count": len(data),
            "country": country,
            "year": year,
        }
