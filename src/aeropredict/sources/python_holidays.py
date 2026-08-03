"""python-holidays adapter for Spain (offline, effective holiday dates).

Usa el paquete PyPI ``holidays`` (https://python-holidays.readthedocs.io/).
Import LAZY dentro de la función: el import del módulo no falla si el paquete
no está instalado y los tests pueden ejecutarse sin red.

Fechas EFECTIVAS (las que realmente se celebran en cada comunidad autónoma),
frente a las fechas NOMINALES de Nager.Date.
"""

from __future__ import annotations

import logging
from typing import Any

from aeropredict.sources.base import BaseAdapter

logger = logging.getLogger(__name__)

COUNTRY_CODE = "ES"


class PythonHolidaysAdapter(BaseAdapter):
    """Adaptador local de festivos vía el paquete ``holidays``."""

    def get_holidays(self, year: int, country: str = COUNTRY_CODE) -> dict[str, Any] | None:
        """Festivos efectivos de España para un año, por subdivisión.

        Itera ``holidays.ES.subdivisions`` (19 comunidades/ciudades autónomas)
        más el calendario nacional ``holidays.ES()``. Sin red.

        Args:
            year: Año de consulta.
            country: Código ISO 3166-1 alpha-2 (solo ``ES`` soportado).

        Returns:
            Dict normalizado ``{"raw": {subdiv: {fecha: nombre}}, "count",
            "subdivisions"}`` o ``None`` si el país no está soportado.
        """
        # Import lazy: no rompe el import del módulo si falta el paquete.
        import holidays

        if country.upper() != COUNTRY_CODE:
            logger.warning("python-holidays: solo soportado ES, recibido %s", country)
            return None

        raw: dict[str, dict[str, str]] = {}
        count = 0
        for subdivision in holidays.ES.subdivisions:
            sub_calendar = holidays.ES(subdiv=subdivision, years=year)
            raw[subdivision] = {
                holiday_date.isoformat(): name
                for holiday_date, name in sorted(sub_calendar.items())
            }
            count += len(raw[subdivision])

        national = holidays.ES(years=year)
        raw[COUNTRY_CODE] = {
            holiday_date.isoformat(): name
            for holiday_date, name in sorted(national.items())
        }
        count += len(raw[COUNTRY_CODE])

        return {
            "raw": raw,
            "count": count,
            "subdivisions": "all",
        }
