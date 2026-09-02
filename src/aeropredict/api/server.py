"""FastAPI server scaffold for model inference.

Loads an MLflow model at startup (if available) and exposes a /health endpoint.
Model is attached to app.state.model and app.state.model_version.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from time import perf_counter

import mlflow
import pandas as pd
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.security import APIKeyHeader

from .models import (
    AlertResponse,
    AuthResponse,
    DelayPredictionRequest,
    DelayPredictionResponse,
    ETAPredictionRequest,
    ETAPredictionResponse,
    HealthResponse,
    LoginRequest,
    RegisterRequest,
    SubscriptionCreateRequest,
    SubscriptionResponse,
)

# PostgreSQL connection for prediction archival
POSTGRES_URI = os.environ.get(
    "POSTGRES_URI",
    "postgresql://aeropredict:aeropredict@localhost:5432/aeropredict",
)


def _log_prediction_to_db(
    *,
    request_id: str,
    model_version: str | None,
    flight_features: dict,
    predicted_delay_minutes: float | None,
    predicted_eta: str | None,
) -> None:
    """Write a single prediction row to gold.predictions (fire-and-forget).

    This function is designed to be called via FastAPI BackgroundTasks so it
    never blocks the API response.  Errors are logged but never propagated.
    """
    try:
        import psycopg2  # lazy import to avoid hard dep at module level

        conn = psycopg2.connect(POSTGRES_URI)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO gold.predictions
                        (request_id, model_version, flight_features,
                         predicted_delay_minutes, predicted_eta, timestamp)
                    VALUES (%s, %s, %s::jsonb, %s, %s, NOW())
                    ON CONFLICT (request_id) DO NOTHING
                    """,
                    (
                        request_id,
                        model_version,
                        json.dumps(flight_features),
                        predicted_delay_minutes,
                        predicted_eta,
                    ),
                )
            conn.commit()
            LOGGER.info("Prediction %s archived to gold.predictions", request_id)
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("Failed to archive prediction %s", request_id)


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

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Validate API key on prediction endpoints if API_KEY env var is set."""
    api_key_required = os.environ.get("API_KEY")
    if api_key_required and request.url.path.startswith("/predict"):
        provided_key = request.headers.get("X-API-Key")
        if not provided_key or provided_key != api_key_required:
            return Response(
                content='{"detail":"Invalid or missing API key"}',
                status_code=401,
                media_type="application/json",
            )
    response = await call_next(request)
    return response


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
    background_tasks: BackgroundTasks,
) -> DelayPredictionResponse:
    """Predict flight delay (minutes) given features.

    - Validates payload using DelayPredictionRequest (Pydantic)
    - Uses app.state.model to run inference. If model missing -> 503
    - Returns DelayPredictionResponse with predicted_delay_minutes, confidence, model_version
    - Archives prediction to gold.predictions in background
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
            if hasattr(model, "metadata") and model.metadata is not None:
                sig = getattr(model.metadata, "signature", None)
                if sig is not None and hasattr(sig, "inputs"):
                    feature_names = [
                        inp.name for inp in sig.inputs.inputs() if hasattr(inp, "name")
                    ]
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

        # Confidence is a placeholder — LightGBM regression does not expose predict_proba.
        confidence = 0.85

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

        # Archive prediction to PostgreSQL in background (non-blocking)
        request_id = str(uuid.uuid4())
        background_tasks.add_task(
            _log_prediction_to_db,
            request_id=request_id,
            model_version=model_version,
            flight_features=features,
            predicted_delay_minutes=pred_val,
            predicted_eta=None,
        )

        return out
    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Error during prediction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


@app.post("/predict/eta", response_model=ETAPredictionResponse)
async def predict_eta(
    request: Request,
    response: Response,
    payload: ETAPredictionRequest,
    background_tasks: BackgroundTasks,
) -> ETAPredictionResponse:
    """Predict ETA given a scheduled arrival and flight features.

    Computes ETA = scheduled_arrival + predicted_delay.
    If delay > 240 minutes, marks ``disruption_likely = True``.
    Archives prediction to gold.predictions in background.
    """
    start_ts = perf_counter()
    model = getattr(app.state, "model", None)
    # check model loaded
    if model is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        LOGGER.warning("/predict/eta called but model not loaded")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model not loaded",
        )

    # prepare features: payload.features -> dict -> single-row DataFrame
    try:
        features_dict = payload.features.model_dump()
        df = pd.DataFrame([features_dict])

        # call model.predict - mlflow pyfunc expects DataFrame
        pred = model.predict(df)
        if hasattr(pred, "__len__") and len(pred) > 0:
            pred_val = float(pred[0])
        else:
            pred_val = float(pred)

        predicted_delay = pred_val
        delay_timedelta = timedelta(minutes=predicted_delay)
        eta = payload.scheduled_arrival + delay_timedelta
        disruption_likely = predicted_delay > 240

        duration_ms = (perf_counter() - start_ts) * 1000.0

        # log the prediction
        try:
            client_ip = request.client.host if request.client else "unknown"
        except Exception:
            client_ip = "unknown"
        LOGGER.info(
            "/predict/eta: scheduled=%s features=%s "
            "predicted_delay=%.3f eta=%s "
            "disruption=%s duration_ms=%.2f client=%s",
            payload.scheduled_arrival,
            features_dict,
            predicted_delay,
            eta,
            disruption_likely,
            duration_ms,
            client_ip,
        )

        # ensure inference time recorded; promised <100ms for single prediction
        if duration_ms > 100:
            LOGGER.warning("Inference slow: %.2fms (>100ms)", duration_ms)

        out = ETAPredictionResponse(
            estimated_arrival_time=eta,
            confidence=0.85,
            delay_component=predicted_delay,
            disruption_likely=disruption_likely,
        )
        response.status_code = status.HTTP_200_OK

        # Archive prediction to PostgreSQL in background (non-blocking)
        request_id = str(uuid.uuid4())
        background_tasks.add_task(
            _log_prediction_to_db,
            request_id=request_id,
            model_version=getattr(app.state, "model_version", None),
            flight_features=features_dict,
            predicted_delay_minutes=predicted_delay,
            predicted_eta=eta.isoformat(),
        )

        return out
    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Error during ETA prediction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# ===================================================================
# Auth — registro y login (email + contraseña + JWT)
# ===================================================================
#
# Los imports de bcrypt, persistencia y tokens son perezosos (dentro de las
# funciones) para que el paquete API siga siendo importable sin los extras
# ``auth`` instalados, igual que el patrón de psycopg2 en _log_prediction_to_db.
#
# TTL del token (minutos) — debe coincidir con el ``expires_in`` devuelto.
_TOKEN_TTL_MINUTES = 1440


def _hash_password(password: str) -> str:
    """Devuelve el hash bcrypt de una contraseña en claro."""
    import bcrypt  # lazy import (extra ``auth``)

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, stored_hash: str) -> bool:
    """Comprueba una contraseña en claro contra su hash bcrypt almacenado."""
    import bcrypt  # lazy import (extra ``auth``)

    return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))


@app.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest) -> AuthResponse:
    """Registra un usuario con email + contraseña y devuelve un JWT.

    - Valida el payload (email válido, contraseña >= 8 caracteres).
    - Hashea la contraseña con bcrypt (nunca se almacena en claro).
    - Si el email ya existe devuelve 409.
    - En éxito devuelve 201 con un token JWT recién emitido.
    """
    from aeropredict.app.persistence import create_user
    from aeropredict.auth.tokens import create_token

    password_hash = _hash_password(payload.password)
    user = create_user(payload.email, password_hash, "email")
    if user is None:
        LOGGER.warning("Registro rechazado: email ya registrado %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    token = create_token(user["user_id"], expires_in_minutes=_TOKEN_TTL_MINUTES)
    LOGGER.info("Usuario registrado: user_id=%s email=%s", user["user_id"], payload.email)
    return AuthResponse(
        token=token,
        user_id=user["user_id"],
        email=payload.email,
        expires_in=_TOKEN_TTL_MINUTES,
    )


@app.post("/auth/login", response_model=AuthResponse)
async def login(payload: LoginRequest) -> AuthResponse:
    """Autentica un usuario con email + contraseña y devuelve un JWT.

    Usa el MISMO mensaje de error (401 "Invalid credentials") tanto para
    usuario inexistente como para contraseña incorrecta, para no permitir
    enumeración de usuarios.
    """
    from aeropredict.app.persistence import get_user_by_email
    from aeropredict.auth.tokens import create_token

    user = get_user_by_email(payload.email)
    stored_hash = user.get("password_hash") if user is not None else None
    if stored_hash is None or not _verify_password(payload.password, stored_hash):
        LOGGER.warning("Login fallido para email=%s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    token = create_token(user["user_id"], expires_in_minutes=_TOKEN_TTL_MINUTES)
    LOGGER.info("Login ok: user_id=%s email=%s", user["user_id"], payload.email)
    return AuthResponse(
        token=token,
        user_id=user["user_id"],
        email=payload.email,
        expires_in=_TOKEN_TTL_MINUTES,
    )


# ===================================================================
# Suscripciones y alertas — "mis vuelos" (auth-scoped)
# ===================================================================
#
# Todos los endpoints exigen un Bearer token válido (dependencia
# ``_get_current_user_id``). La verificación delega en ``tokens.verify_token``
# (T4), que es agnóstica del emisor: un futuro proveedor OAuth que emita el
# MISMO JWT funciona sin cambios. El scope por usuario se aplica SIEMPRE con el
# ``user_id`` del token — nunca con datos del body.
#
# Los imports de persistencia son perezosos (dentro de las funciones), igual
# que en los endpoints de auth.


def _get_current_user_id(
    authorization: str | None = Header(default=None),
) -> str:
    """Extrae y verifica el Bearer token; devuelve el ``user_id`` o 401.

    Cualquier fallo (header ausente, esquema no-Bearer, token inválido o
    caducado) devuelve 401 con el mismo mensaje, sin distinguir el motivo.
    """
    from aeropredict.auth.tokens import verify_token

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    user_id = verify_token(token.strip())
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return user_id


@app.post(
    "/alerts/subscriptions",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription_endpoint(
    payload: SubscriptionCreateRequest,
    user_id: str = Depends(_get_current_user_id),
) -> SubscriptionResponse:
    """Crea (o actualiza) una suscripción de vuelo para el usuario autenticado.

    ``create_subscription`` hace upsert por (user_id, flight_key): re-seguir un
    vuelo actualiza la suscripción existente en lugar de duplicar.
    """
    from aeropredict.app.persistence import create_subscription

    sub = create_subscription(
        user_id=user_id,
        flight_key=payload.flight_key,
        flight_number=payload.flight_number,
        from_airport=payload.from_airport,
        to_airport=payload.to_airport,
        schedule_local=payload.schedule_local,
        threshold_minutes=payload.threshold_minutes,
        email=payload.email,
    )
    LOGGER.info(
        "Suscripción creada/actualizada: user_id=%s flight_key=%s",
        user_id,
        payload.flight_key,
    )
    return SubscriptionResponse(**sub)


@app.get("/alerts/subscriptions", response_model=list[SubscriptionResponse])
async def list_subscriptions_endpoint(
    user_id: str = Depends(_get_current_user_id),
) -> list[SubscriptionResponse]:
    """Lista las suscripciones de vuelo del usuario autenticado."""
    from aeropredict.app.persistence import list_subscriptions

    subs = list_subscriptions(user_id)
    return [SubscriptionResponse(**s) for s in subs]


@app.delete(
    "/alerts/subscriptions/{flight_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_subscription_endpoint(
    flight_key: str,
    user_id: str = Depends(_get_current_user_id),
) -> None:
    """Elimina una suscripción del usuario autenticado (404 si no existe)."""
    from aeropredict.app.persistence import delete_subscription

    if not delete_subscription(user_id, flight_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    LOGGER.info(
        "Suscripción eliminada: user_id=%s flight_key=%s", user_id, flight_key
    )


@app.get("/alerts", response_model=list[AlertResponse])
async def list_alerts_endpoint(
    read: bool | None = None,
    user_id: str = Depends(_get_current_user_id),
) -> list[AlertResponse]:
    """Lista las alertas del usuario autenticado (opcional ``?read=false``)."""
    from aeropredict.app.persistence import list_alerts

    alerts = list_alerts(user_id, read=read)
    return [AlertResponse(**a) for a in alerts]


@app.patch("/alerts/{alert_id}/read", response_model=AlertResponse)
async def mark_alert_read_endpoint(
    alert_id: int,
    user_id: str = Depends(_get_current_user_id),
) -> AlertResponse:
    """Marca una alerta del usuario autenticado como leída (404 si no es suya)."""
    from aeropredict.app.persistence import list_alerts, mark_alert_read

    alert = next(
        (a for a in list_alerts(user_id) if a["id"] == alert_id),
        None,
    )
    if alert is None or not mark_alert_read(alert_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )
    alert["read"] = True
    return AlertResponse(**alert)
