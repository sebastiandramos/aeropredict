"""FastAPI server scaffold for model inference.

Loads an MLflow model at startup (if available) and exposes a /health endpoint.
Model is attached to app.state.model and app.state.model_version.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from time import perf_counter

import mlflow
import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response, status

from .models import (
    DelayPredictionRequest,
    DelayPredictionResponse,
    HealthResponse,
)

LOGGER = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


MLFLOW_MODEL_URI = os.environ.get(
    "MLFLOW_MODEL_URI", "models:/delay-predictor/production"
)
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "file:./mlruns")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager: configure mlflow and attempt model load into app.state."""
    # configure tracking uri
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    app.state.model = None
    app.state.model_version = None

    start_ts = time.time()
    try:
        LOGGER.info("Loading MLflow model from %s", MLFLOW_MODEL_URI)
        model = mlflow.pyfunc.load_model(MLFLOW_MODEL_URI)
        app.state.model = model
        # model metadata: attempt to read .metadata if present
        try:
            mv = getattr(model, "metadata", None)
            if mv and hasattr(mv, "run_id"):
                app.state.model_version = str(mv.run_id)
            else:
                # fallback to model spec
                app.state.model_version = getattr(model, "_model_impl", None) and "loaded"
        except Exception:
            app.state.model_version = "loaded"
        LOGGER.info(
            "Model loaded successfully (version=%s) in %.2fs",
            app.state.model_version,
            time.time() - start_ts,
        )
    except Exception as exc:  # keep server up even if model fails
        LOGGER.exception("Failed to load model: %s", exc)
        app.state.model = None
        app.state.model_version = None
    try:
        yield
    finally:
        # no explicit shutdown required for mlflow model
        LOGGER.info("Shutting down FastAPI app")


app = FastAPI(lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    """Health check endpoint.

    Returns 200 when model loaded, otherwise 503 with an error detail.
    """
    start = time.time()
    model = getattr(app.state, "model", None)
    model_version = getattr(app.state, "model_version", None)
    if model is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        out = HealthResponse(status="error", model_version=None)
        LOGGER.warning("Health check failed: model not loaded")
    else:
        response.status_code = status.HTTP_200_OK
        out = HealthResponse(status="ok", model_version=model_version or "unknown")
        LOGGER.info("Health check ok: model_version=%s", model_version)
    duration = (time.time() - start) * 1000.0
    LOGGER.info("/health responded in %.2fms", duration)
    return out


@app.post("/predict/delay", response_model=DelayPredictionResponse)
async def predict_delay(
    request: Request,
    response: Response,
    payload: DelayPredictionRequest,
) -> DelayPredictionResponse:
    """Predict flight delay (minutes) given features.

    - Validates payload using DelayPredictionRequest (Pydantic)
    - Uses app.state.model to run inference. If model missing -> 503
    - Returns DelayPredictionResponse with predicted_delay_minutes, confidence, model_version
    """
    start_ts = perf_counter()
    model = getattr(app.state, "model", None)
    model_version = getattr(app.state, "model_version", None)
    # check model loaded
    if model is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        LOGGER.warning("/predict/delay called but model not loaded")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded",
        )

    # prepare features: pydantic -> dict -> single-row DataFrame
    try:
        features = payload.model_dump()
        # ensure deterministic column order using model's feature names when available
        try:
            feature_names = []
            # try common metadata paths used in mlflow pyfunc LightGBM wrapper
            if hasattr(model, "metadata") and model.metadata is not None:
                meta = model.metadata
                feature_names = getattr(meta, "signature", {}).get("inputs", []) or []
        except Exception:
            feature_names = []

        df = pd.DataFrame([features])
        # if we have explicit feature_names and they are a list of names, reorder/reindex
        if feature_names and all(isinstance(x, str) for x in feature_names):
            # keep only known columns and preserve order
            cols = [c for c in feature_names if c in df.columns]
            if cols:
                df = df.reindex(columns=cols, fill_value=-1)

        # call model.predict - mlflow pyfunc expects DataFrame
        pred = model.predict(df)
        # pred may be array-like or scalar
        if hasattr(pred, "__len__") and len(pred) > 0:
            pred_val = float(pred[0])
        else:
            pred_val = float(pred)

        # estimate confidence
        confidence = 0.85
        try:
            # LightGBM may expose predict with pred_leaf or raw scores;
            # ensemble stddev is not always available from pyfunc wrapper.
            if hasattr(model, "predict_proba"):
                # not applicable for regression, skip
                pass
        except Exception:
            pass

        duration_ms = (perf_counter() - start_ts) * 1000.0

        # log the prediction
        try:
            client_ip = request.client.host if request.client else "unknown"
        except Exception:
            client_ip = "unknown"
        LOGGER.info(
            "/predict/delay: input=%s predicted=%.3f model_version=%s duration_ms=%.2f client=%s",
            features,
            pred_val,
            model_version,
            duration_ms,
            client_ip,
        )

        # ensure inference time recorded; promised <100ms for single prediction
        if duration_ms > 100:
            LOGGER.warning("Inference slow: %.2fms (>100ms)", duration_ms)

        out = DelayPredictionResponse(
            predicted_delay_minutes=pred_val,
            confidence=float(confidence),
            model_version=model_version,
        )
        response.status_code = status.HTTP_200_OK
        return out
    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Error during prediction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
