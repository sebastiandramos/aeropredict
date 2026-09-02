"""Pydantic v2 request/response models for the inference API.

Uses Pydantic v2 ConfigDict style per repository conventions.
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


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

    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")


class DelayPredictionResponse(BaseModel):
    predicted_delay_minutes: float
    confidence: float
    model_version: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ETAPredictionRequest(BaseModel):
    scheduled_arrival: datetime
    features: DelayPredictionRequest

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ETAPredictionResponse(BaseModel):
    estimated_arrival_time: datetime
    confidence: float
    delay_component: float
    disruption_likely: bool = False

    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Auth — registro y login (email + contraseña + JWT)
# ===================================================================


class RegisterRequest(BaseModel):
    email: str
    password: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("email")
    @classmethod
    def _email_format(cls, value: str) -> str:
        # Validación ligera de email sin depender de email-validator.
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("password")
    @classmethod
    def _password_min_length(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return value


class LoginRequest(BaseModel):
    email: str
    password: str

    model_config = ConfigDict(extra="forbid")


class AuthResponse(BaseModel):
    token: str
    user_id: str
    email: str
    expires_in: int

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    user_id: str
    email: str
    auth_method: str
    created_at: datetime | str

    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Suscripciones y alertas — "mis vuelos" (auth-scoped)
# ===================================================================


class SubscriptionCreateRequest(BaseModel):
    flight_key: str
    flight_number: str
    from_airport: str
    to_airport: str
    schedule_local: str
    threshold_minutes: int = 60
    email: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("threshold_minutes")
    @classmethod
    def _threshold_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("threshold_minutes must be at least 1")
        return value

    @field_validator("email")
    @classmethod
    def _email_format(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value


class SubscriptionResponse(BaseModel):
    user_id: str
    flight_key: str
    flight_number: str
    from_airport: str
    to_airport: str
    schedule_local: str
    threshold_minutes: int
    email: str | None = None
    created_at: datetime | str
    updated_at: datetime | str

    model_config = ConfigDict(from_attributes=True)


class AlertResponse(BaseModel):
    id: int
    user_id: str
    flight_key: str
    severity: str
    delay_minutes_predicted: float | None = None
    factor_jsonb: dict | None = None
    email_sent: bool = False
    read: bool = False
    created_at: datetime | str

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "AlertResponse",
    "AuthResponse",
    "DelayPredictionRequest",
    "DelayPredictionResponse",
    "ETAPredictionRequest",
    "ETAPredictionResponse",
    "HealthResponse",
    "LoginRequest",
    "RegisterRequest",
    "SubscriptionCreateRequest",
    "SubscriptionResponse",
    "UserResponse",
]
