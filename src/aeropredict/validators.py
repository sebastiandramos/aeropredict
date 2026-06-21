"""Validation wrappers for pipeline scripts.

These functions validate lists of raw dicts against pydantic models
defined in :mod:`aeropredict.schemas` using pydantic v2's
``model_validate``. Validation is non-blocking: invalid rows are
collected and returned alongside parsed model instances. All functions
log a concise summary: validated N rows, rejected M (X%).

The functions return a tuple: (valid_models, invalid_details)
where ``invalid_details`` is a list of dicts with keys ``row`` and
``errors`` (stringified validation error).
"""

from __future__ import annotations

import logging
from typing import Any, List, Tuple, Type

from pydantic import ValidationError

from aeropredict import schemas

logger = logging.getLogger(__name__)


def _validate_generic(
    rows: List[dict[str, Any]], model: Type[Any], label: str
) -> Tuple[List[Any], List[dict[str, Any]]]:
    """Validate a list of dicts against a pydantic model.

    Returns (valid_models, invalid_details).
    """
    valid: List[Any] = []
    invalid: List[dict[str, Any]] = []

    total = len(rows)
    if total == 0:
        logger.info("%s validation: 0 rows (0 rejected)", label)
        return valid, invalid

    for r in rows:
        try:
            m = model.model_validate(r)
            valid.append(m)
        except ValidationError as e:
            invalid.append({"row": r, "errors": e.errors() if hasattr(e, "errors") else str(e)})

    rejected = len(invalid)
    pct = (rejected / total * 100) if total else 0.0
    logger.info(
        "%s validation: validated %d rows, rejected %d (%.1f%%)",
        label,
        total - rejected,
        rejected,
        pct,
    )
    return valid, invalid


def validate_flights(flights: List[dict[str, Any]]) -> Tuple[List[schemas.FlightDocument], List[dict[str, Any]]]:
    """Validate flight documents (Silver flight schema).

    Non-blocking: returns parsed FlightDocument instances and list of
    invalid rows with error info.
    """
    return _validate_generic(flights, schemas.FlightDocument, "flights")


def validate_state_vectors(vectors: List[dict[str, Any]]) -> Tuple[List[schemas.StateVectorDocument], List[dict[str, Any]]]:
    """Validate state vectors (Silver schema)."""
    return _validate_generic(vectors, schemas.StateVectorDocument, "state_vectors")


def validate_weather(weather: List[dict[str, Any]]) -> Tuple[List[schemas.WeatherDocument], List[dict[str, Any]]]:
    """Validate weather documents (Silver schema)."""
    return _validate_generic(weather, schemas.WeatherDocument, "weather")


def validate_aircraft(aircraft: List[dict[str, Any]]) -> Tuple[List[schemas.AircraftDocument], List[dict[str, Any]]]:
    """Validate aircraft registry documents (Silver schema)."""
    return _validate_generic(aircraft, schemas.AircraftDocument, "aircraft")


def validate_schedules(schedules: List[dict[str, Any]]) -> Tuple[List[schemas.ScheduleDocument], List[dict[str, Any]]]:
    """Validate schedules (Silver schema)."""
    return _validate_generic(schedules, schemas.ScheduleDocument, "schedules")


def validate_feature_store(features: List[dict[str, Any]]) -> Tuple[List[schemas.FeatureStoreRow], List[dict[str, Any]]]:
    """Validate feature store rows (Gold feature store schema)."""
    return _validate_generic(features, schemas.FeatureStoreRow, "feature_store")


__all__ = [
    "validate_flights",
    "validate_state_vectors",
    "validate_weather",
    "validate_aircraft",
    "validate_schedules",
    "validate_feature_store",
]
