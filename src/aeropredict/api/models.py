"""Pydantic v2 request/response models for the inference API.

Uses Pydantic v2 ConfigDict style per repository conventions.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    model_version: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DelayPredictionRequest(BaseModel):
    # Feature set used by the baseline model (see scripts/train_baseline_helpers.py)
    # plus the notebook's two-stage predictors. Every field added for the
    # two-stage model is optional so existing clients keep working unchanged.
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
    # --- two-stage notebook predictors (optional) -------------------------
    # Route endpoints in ICAO or IATA. The notebook's ``other_airport_iata``
    # is derived from these (the airport that is not the AENA one).
    origin_iata: str | None = None
    destination_iata: str | None = None
    # Explicit override for the notebook predictor (IATA), if the caller
    # already knows which airport is the "other" one.
    other_airport_iata: str | None = None
    # METAR predictors. Missing values are imputed with development medians;
    # dew point / humidity are derived from each other when possible.
    dew_point_c: float | None = None
    relative_humidity: float | None = None

    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")

    @field_validator("hour_of_day")
    @classmethod
    def _hour_in_range(cls, value: int) -> int:
        if not 0 <= value <= 23:
            raise ValueError("hour_of_day must be between 0 and 23")
        return value

    @field_validator("day_of_week")
    @classmethod
    def _day_in_range(cls, value: int) -> int:
        # Frontend convention: 0 = Monday ... 6 = Sunday.
        if not 0 <= value <= 6:
            raise ValueError("day_of_week must be between 0 (Monday) and 6 (Sunday)")
        return value

    @field_validator("route_distance")
    @classmethod
    def _distance_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("route_distance must be non-negative")
        return value

    @field_validator("relative_humidity")
    @classmethod
    def _humidity_in_range(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 100:
            raise ValueError("relative_humidity must be between 0 and 100")
        return value


class DelayFactor(BaseModel):
    """Local explanation of the stage-1 (classification) delay probability.

    ``contribution`` is measured in log-odds, computed for the concrete
    observation on the same design matrix used by the model. Positive values
    push the delay probability up (``direction="increase"``), negative values
    push it down. It is neither a percentage nor a number of minutes.
    """

    id: str
    title: str
    description: str | None = None
    value: str | None = None
    contribution: float | None = None
    direction: Literal["increase", "decrease"] | None = None
    impact: str

    model_config = ConfigDict(from_attributes=True)


class DelayPredictionResponse(BaseModel):
    # Legacy contract (kept so existing clients and tests keep working).
    predicted_delay_minutes: float
    confidence: float
    model_version: str | None = None
    # Two-stage contract returned by the notebook model.
    prob_retraso: float | None = None
    prediccion_retraso_15min: bool | None = None
    minutos_retraso_estimados: float | None = None
    umbral_aplicado: float | None = None
    factores: list[DelayFactor] = Field(default_factory=list)
    imputaciones: list[str] = Field(default_factory=list)
    model_source: str | None = None

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
    prob_retraso: float | None = None
    umbral_aplicado: float | None = None
    model_source: str | None = None

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
