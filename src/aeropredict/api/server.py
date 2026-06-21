"""FastAPI server scaffold for model inference.

Loads an MLflow model at startup (if available) and exposes a /health endpoint.
Model is attached to app.state.model and app.state.model_version.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

import mlflow
from fastapi import FastAPI, Response, status

from .models import HealthResponse

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
