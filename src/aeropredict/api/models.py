"""Pydantic v2 request/response models for the inference API.

Uses Pydantic v2 ConfigDict style per repository conventions.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str
    model_version: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DelayPredictionRequest(BaseModel):
    # feature set used by the baseline model (see scripts/train_baseline_helpers.py)
    hour_of_day: int
    day_of_week: int
    airline: str
    route_distance: float
    aircraft_type: str | None = None
    aircraft_manufacturer: str | None = None
    aircraft_operator: str | None = None
    # weather fields (optional)
    weather_temperature_2m: float | None = None
    weather_precipitation: float | None = None

    model_config = ConfigDict(from_attributes=True, frozen=True)


class DelayPredictionResponse(BaseModel):
    predicted_delay_minutes: float
    confidence: float
    model_version: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ETAPredictionRequest(BaseModel):
    scheduled_arrival: datetime
    features: DelayPredictionRequest

    model_config = ConfigDict(from_attributes=True)


class ETAPredictionResponse(BaseModel):
    estimated_arrival_time: datetime
    confidence: float
    delay_component: float
    disruption_likely: bool = False

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "DelayPredictionRequest",
    "DelayPredictionResponse",
    "ETAPredictionRequest",
    "ETAPredictionResponse",
    "HealthResponse",
]
