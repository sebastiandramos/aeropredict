"""Flight-to-schedule matching logic for delay computation.

El matching es inexacto por naturaleza:
- OpenSky estima aeropuertos de salida/llegada basándose en proximidad
- first_seen/last_seen son detecciones de transpondedor, no wheels-off/wheels-on
- Los schedules vienen de APIs externas con formatos variados
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.database import Database

logger = logging.getLogger(__name__)

# Ventanas de tolerancia para matching temporal (horas)
MAX_DEPARTURE_DELTA = timedelta(hours=3)
MAX_ARRIVAL_DELTA = timedelta(hours=3)


class FlightScheduleMatcher:
    """Empareja vuelos OpenSky con schedules de fuentes externas.

    Args:
        mongo_db: Base de datos MongoDB (con colecciones flights y schedules).
    """

    def __init__(self, mongo_db: Database[Any]) -> None:
        self.db = mongo_db

    # -- Métodos públicos ---------------------------------------------------

    def match_by_callsign_date(
        self, callsign: str, flight_date: str,
    ) -> list[dict[str, Any]]:
        """Busca schedules para un callsign + fecha.

        Args:
            callsign: Código ICAO del vuelo.
            flight_date: Fecha ISO.

        Returns:
            Lista de schedules candidatos.
        """
        cursor = self.db["schedules"].find({
            "callsign": callsign,
            "flight_date": flight_date,
        })
        return list(cursor)

    def match_flight_to_schedule(
        self, flight: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Encuentra el mejor schedule para un vuelo.

        Estrategia de matching (por orden de precisión):
        1. Callsign exacto + fecha + aeropuerto salida + aeropuerto llegada
        2. Callsign exacto + fecha + aeropuerto salida
        3. Callsign exacto + fecha + aeropuerto llegada
        4. Callsign exacto + fecha (menos preciso)

        Args:
            flight: Documento de vuelo de MongoDB.

        Returns:
            Dict del schedule matched, o ``None``.
        """
        callsign = flight.get("callsign")
        flight_date = str(flight.get("flight_date", ""))[:10]
        if not callsign or not flight_date:
            return None

        candidates = self.match_by_callsign_date(callsign, flight_date)
        if not candidates:
            return None

        dep = flight.get("est_departure_airport")
        arr = flight.get("est_arrival_airport")

        # 1. Coincidencia exacta de aeropuertos
        for s in candidates:
            s_dep = s.get("departure_airport", "")
            s_arr = s.get("arrival_airport", "")
            if dep and arr and s_dep == dep and s_arr == arr:
                logger.debug("Match nivel 1 (exacto): %s %s→%s", callsign, dep, arr)
                return s

        # 2. Coincidencia solo salida
        if dep:
            for s in candidates:
                if s.get("departure_airport", "") == dep:
                    logger.debug("Match nivel 2 (solo dep): %s %s", callsign, dep)
                    return s

        # 3. Coincidencia solo llegada
        if arr:
            for s in candidates:
                if s.get("arrival_airport", "") == arr:
                    logger.debug("Match nivel 3 (solo arr): %s %s", callsign, arr)
                    return s

        # 4. Cualquier schedule con este callsign+fecha
        logger.debug("Match nivel 4 (solo callsign): %s %s", callsign, flight_date)
        return candidates[0]

    def compute_delay(
        self,
        flight: dict[str, Any],
        schedule: dict[str, Any],
    ) -> float | None:
        """Calcula el retraso en minutos.

        delay = actual_arrival - scheduled_arrival

        Donde:
        - actual_arrival ≈ flight.last_seen (transpondedor, no wheels-on)
        - scheduled_arrival ≈ schedule.arrival_scheduled

        Args:
            flight: Documento de vuelo de MongoDB.
            schedule: Dict de schedule normalizado.

        Returns:
            Minutos de retraso (positivo = tarde, negativo = temprano)
            o ``None`` si no se puede calcular.
        """
        # Hora real: last_seen del vuelo OpenSky
        actual = flight.get("last_seen")
        if isinstance(actual, str):
            actual = datetime.fromisoformat(actual)
        if not isinstance(actual, datetime):
            return None

        # Asegurar timezone-aware
        if actual.tzinfo is None:
            actual = actual.replace(tzinfo=UTC)

        # Hora programada: del schedule
        scheduled_str = schedule.get("arrival_scheduled")
        if not scheduled_str:
            return None

        try:
            scheduled = datetime.fromisoformat(scheduled_str)
        except (ValueError, TypeError):
            return None

        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=UTC)

        delay = (actual - scheduled).total_seconds() / 60.0
        return round(delay, 1)
