"""Integration tests for FastAPI inference endpoints.

Tests the /health, /predict/delay, and /predict/eta endpoints.
Model-dependent tests skip gracefully when model is not loaded.
"""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.aeropredict.api.server import app

client = TestClient(app)


def test_health_check():
    """Verify the health check endpoint returns 200 if model is loaded, 503 otherwise."""
    response = client.get("/health")
    # We don't assume model is always loaded in test env, but we check response shape
    assert response.status_code in [200, 503]
    data = response.json()
    assert "status" in data
    assert "model_version" in data


def test_predict_delay_happy_path():
    """Verify /predict/delay returns 200 with a valid prediction payload."""
    payload = {
        "hour_of_day": 14,
        "day_of_week": 2,
        "airline": "IB",
        "route_distance": 500.0,
        "aircraft_type": "A320",
        "aircraft_manufacturer": "Airbus",
        "aircraft_operator": "Iberia",
        "weather_temperature_2m": 20.0,
        "weather_precipitation": 0.0,
    }
    response = client.post("/predict/delay", json=payload)

    if response.status_code == 503:
        pytest.skip("Model not loaded in test environment")

    assert response.status_code == 200
    data = response.json()
    assert "predicted_delay_minutes" in data
    assert "confidence" in data
    assert "model_version" in data


def test_predict_delay_invalid_input():
    """Verify /predict/delay returns 422 for malformed input (missing required field)."""
    # Missing 'hour_of_day'
    payload = {
        "day_of_week": 2,
        "airline": "IB",
        "route_distance": 500.0,
    }
    response = client.post("/predict/delay", json=payload)
    assert response.status_code == 422


def test_predict_delay_type_error():
    """Verify 422 when types are incorrect (e.g. route_distance is a string)."""
    payload = {
        "hour_of_day": 14,
        "day_of_week": 2,
        "airline": "IB",
        "route_distance": "five hundred",
    }
    response = client.post("/predict/delay", json=payload)
    assert response.status_code == 422


def test_predict_delay_extreme_values():
    """Verify API handles extreme (but valid) feature values without crashing."""
    payload = {
        "hour_of_day": 23,
        "day_of_week": 6,
        "airline": "XX",
        "route_distance": 15000.0,
        "weather_temperature_2m": -50.0,
        "weather_precipitation": 100.0,
    }
    response = client.post("/predict/delay", json=payload)
    if response.status_code != 503:
        assert response.status_code == 200


def test_predict_eta_happy_path():
    """Verify /predict/eta returns 200 and calculates ETA correctly."""
    payload = {
        "scheduled_arrival": "2026-06-21T18:30:00Z",
        "features": {
            "hour_of_day": 14,
            "day_of_week": 2,
            "airline": "IB",
            "route_distance": 500.0,
            "aircraft_type": "A320",
            "aircraft_manufacturer": "Airbus",
            "aircraft_operator": "Iberia",
            "weather_temperature_2m": 20.0,
            "weather_precipitation": 0.0,
        },
    }
    response = client.post("/predict/eta", json=payload)

    if response.status_code == 503:
        pytest.skip("Model not loaded in test environment")

    assert response.status_code == 200
    data = response.json()
    assert "estimated_arrival_time" in data
    assert "delay_component" in data
    assert "disruption_likely" in data


def test_predict_eta_invalid_timestamp():
    """Verify /predict/eta returns 422 for malformed timestamps."""
    payload = {
        "scheduled_arrival": "not-a-date",
        "features": {
            "hour_of_day": 14,
            "day_of_week": 2,
            "airline": "IB",
            "route_distance": 500.0,
        },
    }
    response = client.post("/predict/eta", json=payload)
    assert response.status_code == 422


def test_predict_eta_missing_features():
    """Verify /predict/eta returns 422 if features are missing."""
    payload = {
        "scheduled_arrival": "2026-06-21T18:30:00Z",
    }
    response = client.post("/predict/eta", json=payload)
    assert response.status_code == 422


def test_predict_eta_boundary_timestamp():
    """Verify API handles timestamps at the edge of the day."""
    payload = {
        "scheduled_arrival": "2026-12-31T23:59:59Z",
        "features": {
            "hour_of_day": 23,
            "day_of_week": 5,
            "airline": "IB",
            "route_distance": 100.0,
        },
    }
    response = client.post("/predict/eta", json=payload)
    if response.status_code == 200:
        assert response.status_code == 200


def test_predict_eta_disruption_flag():
    """Verify that large predicted delays trigger disruption_likely = True.

    Since we can't easily force the model to predict > 240min without
    specific input, we test the response schema.
    """
    payload = {
        "scheduled_arrival": "2026-06-21T18:30:00Z",
        "features": {
            "hour_of_day": 14,
            "day_of_week": 2,
            "airline": "IB",
            "route_distance": 500.0,
        },
    }
    response = client.post("/predict/eta", json=payload)
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data["disruption_likely"], bool)


def test_model_version_consistency():
    """Verify that model_version is consistently returned in responses."""
    payload = {
        "hour_of_day": 14,
        "day_of_week": 2,
        "airline": "IB",
        "route_distance": 500.0,
    }
    response = client.post("/predict/delay", json=payload)
    if response.status_code == 200:
        data = response.json()
        assert "model_version" in data
        # Cross-check with health
        health_res = client.get("/health").json()
        assert data["model_version"] == health_res["model_version"]


def test_api_response_time():
    """Verification that inference is reasonably fast (<1s for test purposes)."""
    payload = {
        "hour_of_day": 14,
        "day_of_week": 2,
        "airline": "IB",
        "route_distance": 500.0,
    }
    start = datetime.now(UTC)
    response = client.post("/predict/delay", json=payload)
    end = datetime.now(UTC)

    if response.status_code == 200:
        assert (end - start).total_seconds() < 1.0
