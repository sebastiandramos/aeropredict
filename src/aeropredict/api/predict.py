"""Two-stage delay inference, faithful to ``AeroPredict_TFM_Modelado_Final_Git.ipynb``.

The notebook trains, in R:

1. Stage 1 — binomial logistic regression on ``retraso_15_min``
   (``retraso_minutos >= 15``). Features come from ``gold.feature_store``:
   ``hora_vuelo``, ``dia_semana``, ``airline_iata``, ``other_airport_iata``,
   ``temperatura_metar``, ``punto_rocio_metar`` y ``relh``.
2. Stage 2 — Gaussian GBM on ``log1p(retraso_minutos)`` for flights classified
   as delayed, with predictions floored at 15 minutes.

This module reproduces every preprocessing step of the notebook:

* ``airline_iata`` / ``other_airport_iata``: categories with fewer than
  ``frecuencia_minima`` occurrences **in the training split** are collapsed to
  ``OTRA``. The category universe and its order are frozen in the artifact.
* ``hora_vuelo`` / ``dia_semana``: fixed domains (0-23 and 1-7). Values outside
  the domain are a hard error, exactly like the notebook's ``stop()``.
* ``model.matrix()`` treatment contrasts: the first factor level is the
  reference and the remaining levels become ``<var><level>`` dummy columns.
* Constant columns are dropped, then the design matrix is rank-reduced with a
  pivoted QR, mirroring ``qr()$pivot[seq_len(qr()$rank)]``.
* ``plogis(X %*% beta)`` for stage 1, ``pmax(15, expm1(pred_log))`` for stage 2.

Everything learned from data (category rules, column layout, coefficients,
imputation medians) lives in a single ``.pkl`` artifact plus a human-readable
JSON mirror, so serving never re-learns anything from request data.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

# --- Notebook constants (CELL 7 / CELL 11 / CELL 21 / CELL 26) ----------------
PREDICTOR_VARIABLES: tuple[str, ...] = (
    "hora_vuelo",
    "dia_semana",
    "airline_iata",
    "other_airport_iata",
    "temperatura_metar",
    "punto_rocio_metar",
    "relh",
)
CATEGORICAL_VARIABLES: tuple[str, ...] = (
    "hora_vuelo",
    "dia_semana",
    "airline_iata",
    "other_airport_iata",
)
GROUPED_VARIABLES: tuple[str, ...] = ("airline_iata", "other_airport_iata")
TEMPORAL_DOMAINS: dict[str, list[str]] = {
    "hora_vuelo": [str(hour) for hour in range(24)],
    "dia_semana": [str(day) for day in range(1, 8)],
}
MIN_CATEGORY_FREQUENCY = 2
RARE_CATEGORY = "OTRA"
TARGET_COLUMN = "retraso_minutos"
DELAY_CLASSIFICATION_MINUTES = 15
PREDICTION_FLOOR_MINUTES = 15
N_TREES = 300
GBM_SEED = 2026

DEFAULT_ARTIFACT_PATH = "models/aeropredict_modelos_finales_v1.pkl"
DEFAULT_RULES_PATH = "models/preprocessor_rules.json"
ARTIFACT_VERSION = "1.0.0"

# Factor identifier -> human readable label (used for the ``factores`` payload).
FACTOR_LABELS: dict[str, str] = {
    "hora_vuelo": "Hora de salida",
    "dia_semana": "Día de la semana",
    "airline_iata": "Aerolínea",
    "other_airport_iata": "Aeropuerto contraparte",
    "temperatura_metar": "Temperatura METAR",
    "punto_rocio_metar": "Punto de rocío METAR",
    "relh": "Humedad relativa",
}

# Spanish weekday names (model coding: 0 = Monday … 6 = Sunday).
_WEEKDAY_NAMES = (
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
)

# Magnitude below which a contribution is considered noise and omitted from
# the explanation instead of being reported as a spurious effect.
MIN_FACTOR_CONTRIBUTION = 1e-3

# Magnus-Tetens coefficients (used only to derive a missing METAR field from
# the other two, never to alter a provided value).
_MAGNUS_A = 17.625
_MAGNUS_B = 243.04


def format_factor_value(variable: str, value: Any) -> str:
    """Format the observed value of a predictor for the explanation payload."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "sin dato"
    if variable == "dia_semana":
        index = int(float(value))
        return _WEEKDAY_NAMES[index] if 0 <= index < len(_WEEKDAY_NAMES) else str(value)
    if variable == "hora_vuelo":
        return f"{int(float(value)):02d}:00"
    if variable in {"temperatura_metar", "punto_rocio_metar"}:
        return f"{float(value):.1f} °C"
    if variable == "relh":
        return f"{float(value):.0f} %"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


# ---------------------------------------------------------------------------
# Preprocessing (CELL 11 equivalent)
# ---------------------------------------------------------------------------
def _as_string_series(series: pd.Series) -> pd.Series:
    """Cast to string dtype keeping missing values distinguishable."""
    return series.astype("string")


def learn_category_rules(
    train: pd.DataFrame,
    *,
    categorical_variables: Sequence[str] = CATEGORICAL_VARIABLES,
    grouped_variables: Sequence[str] = GROUPED_VARIABLES,
    min_frequency: int = MIN_CATEGORY_FREQUENCY,
) -> dict[str, dict[str, Any]]:
    """Learn the notebook's category rules **on the training split only**.

    Mirrors ``preparar_categorias_temporal()``: high-cardinality variables are
    grouped by frequency (``table()`` over non-NA training values), temporal
    variables get the fixed notebook domains.
    """
    rules: dict[str, dict[str, Any]] = {}

    for variable in grouped_variables:
        if variable not in categorical_variables:
            continue
        values = _as_string_series(train[variable])
        valid = values[values.notna() & (values != "")]
        counts = valid.value_counts()
        # R's table() returns alphabetically sorted names.
        frequent = sorted(counts[counts >= min_frequency].index.tolist())
        rules[variable] = {
            "tipo": "frecuencia",
            "niveles_frecuentes": frequent,
            "niveles_finales": [*frequent, RARE_CATEGORY],
            "frecuencia_minima": min_frequency,
        }

    for variable in categorical_variables:
        if variable in grouped_variables:
            continue
        levels = TEMPORAL_DOMAINS.get(variable)
        if levels is None:
            observed = _as_string_series(train[variable]).dropna().unique().tolist()
            levels = sorted(observed)
        rules[variable] = {"tipo": "dominio", "niveles_finales": list(levels)}

    return rules


def apply_category_rules(
    frame: pd.DataFrame,
    rules: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    """Apply learned category rules, returning categorical dtypes in order."""
    out = frame.copy()
    for variable, rule in rules.items():
        values = _as_string_series(out[variable])
        if rule["tipo"] == "frecuencia":
            frequent = set(rule["niveles_frecuentes"])
            mapped = values.where(values.isin(frequent), RARE_CATEGORY)
            mapped = mapped.fillna(RARE_CATEGORY)
            out[variable] = pd.Categorical(mapped, categories=rule["niveles_finales"])
        else:
            levels = list(rule["niveles_finales"])
            invalid = values.notna() & (values != "") & ~values.isin(levels)
            if bool(invalid.any()):
                bad = sorted(set(values[invalid].tolist()))
                raise ValueError(f"{variable} contains values outside the notebook domain: {bad}")
            out[variable] = pd.Categorical(values, categories=levels)
    return out


def build_model_matrix(
    frame: pd.DataFrame,
    rules: Mapping[str, Mapping[str, Any]],
    columns: Sequence[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Reproduce ``stats::model.matrix()`` treatment contrasts (no intercept).

    The first level of every factor is the reference; remaining levels become
    ``<variable><level>`` dummy columns. Numeric predictors pass through in
    formula order. When ``columns`` is given, the matrix is aligned to that
    exact column layout (missing columns are zero-filled, matching the
    notebook's ``crear_matriz_predictores``).
    """
    blocks: list[np.ndarray] = []
    names: list[str] = []

    for variable in PREDICTOR_VARIABLES:
        if variable in rules:
            levels = list(rules[variable]["niveles_finales"])
            categorical = pd.Categorical(frame[variable], categories=levels)
            for level in levels[1:]:
                names.append(f"{variable}{level}")
                blocks.append(np.asarray(categorical == level, dtype=float))
        else:
            names.append(variable)
            blocks.append(pd.to_numeric(frame[variable], errors="coerce").to_numpy(dtype=float))

    matrix = np.column_stack(blocks) if blocks else np.empty((len(frame), 0))

    if columns is None:
        if matrix.size == 0:
            return matrix, names
        keep = [idx for idx in range(len(names)) if np.unique(matrix[:, idx]).size > 1]
        return matrix[:, keep], [names[idx] for idx in keep]

    aligned = np.zeros((len(frame), len(columns)), dtype=float)
    position = {name: idx for idx, name in enumerate(names)}
    for target_idx, column in enumerate(columns):
        source_idx = position.get(column)
        if source_idx is None:
            LOGGER.debug("Column %s absent from request matrix; zero-filled", column)
            continue
        aligned[:, target_idx] = matrix[:, source_idx]
    return aligned, list(columns)


def build_design_matrix(
    matrix: np.ndarray,
    matrix_columns: Sequence[str],
    design_columns: Sequence[str],
) -> np.ndarray:
    """Prepend the intercept and select the rank-reduced design columns."""
    with_intercept = np.column_stack([np.ones(matrix.shape[0]), matrix])
    names = ["(Intercept)", *matrix_columns]
    position = {name: idx for idx, name in enumerate(names)}
    missing = [column for column in design_columns if column not in position]
    if missing:
        raise ValueError(f"Design columns not present in matrix: {missing}")
    return with_intercept[:, [position[column] for column in design_columns]]


def rank_reduce_design(
    matrix: np.ndarray,
    matrix_columns: Sequence[str],
) -> tuple[np.ndarray, list[str]]:
    """Drop collinear columns with a pivoted QR (``qr()`` equivalent)."""
    from scipy.linalg import qr

    with_intercept = np.column_stack([np.ones(matrix.shape[0]), matrix])
    names = ["(Intercept)", *matrix_columns]
    if with_intercept.shape[1] == 1:
        return with_intercept, names

    _, r_matrix, pivots = qr(with_intercept, mode="economic", pivoting=True)
    diagonal = np.abs(np.diag(r_matrix))
    if diagonal.size == 0 or diagonal[0] == 0:
        return with_intercept[:, :1], names[:1]
    tolerance = diagonal[0] * max(with_intercept.shape) * np.finfo(float).eps
    rank = int(np.sum(diagonal > tolerance))
    rank = max(rank, 1)
    selected = list(pivots[:rank])
    # Keep pivot order, exactly like R's qr()$pivot[seq_len(rank)].
    return with_intercept[:, selected], [names[idx] for idx in selected]


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------
def fit_logistic(
    design: np.ndarray,
    target: np.ndarray,
) -> np.ndarray:
    """Fit ``glm.fit(family = binomial())`` equivalent, returning coefficients.

    ``newton-cholesky`` mirrors R's IRLS/Fisher-scoring solver: it converges in
    a handful of Newton steps with an unpenalised, unregularised likelihood
    (``C=np.inf``) and handles the ``n_samples >> n_features`` regime of the
    notebook's design matrix efficiently.
    """
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(
        fit_intercept=False,
        C=np.inf,
        solver="newton-cholesky",
        max_iter=100,
        tol=1e-8,
    )
    model.fit(design, target)
    return np.asarray(model.coef_).ravel()


def _expit(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60.0, 60.0)))


def select_threshold(
    target: np.ndarray,
    probabilities: np.ndarray,
) -> tuple[float, dict[str, float]]:
    """Search the notebook's threshold grid, ranked by F1, precision, recall."""
    rows: list[tuple[float, float, float, float]] = []
    for step in range(1, 101):
        threshold = step * 0.005
        predicted = (probabilities >= threshold).astype(int)
        true_positive = int(np.sum((predicted == 1) & (target == 1)))
        false_positive = int(np.sum((predicted == 1) & (target == 0)))
        false_negative = int(np.sum((predicted == 0) & (target == 1)))
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append((threshold, f1, precision, recall))

    # Stable sort preserving the ascending threshold order on ties (R's arrange).
    best = sorted(rows, key=lambda row: (-row[1], -row[2], -row[3]))[0]
    return best[0], {"f1": best[1], "precision": best[2], "recall": best[3]}


def fit_gbm(design: np.ndarray, target_log: np.ndarray) -> Any:
    """Fit the Gaussian GBM equivalent of ``gbm::gbm(distribution="gaussian")``."""
    from sklearn.ensemble import GradientBoostingRegressor

    model = GradientBoostingRegressor(
        loss="squared_error",
        learning_rate=0.03,
        n_estimators=N_TREES,
        max_depth=3,
        min_samples_leaf=20,
        subsample=0.5,
        random_state=GBM_SEED,
    )
    model.fit(design, target_log)
    return model


# ---------------------------------------------------------------------------
# Serving-time payload mapping
# ---------------------------------------------------------------------------
def _magnus_dew_point(temperature_c: float, relative_humidity: float) -> float:
    humidity = min(max(relative_humidity, 1.0), 100.0)
    gamma = math.log(humidity / 100.0) + (_MAGNUS_A * temperature_c) / (_MAGNUS_B + temperature_c)
    return (_MAGNUS_B * gamma) / (_MAGNUS_A - gamma)


def _magnus_relative_humidity(temperature_c: float, dew_point_c: float) -> float:
    exponent = (_MAGNUS_A * dew_point_c) / (_MAGNUS_B + dew_point_c) - (
        _MAGNUS_A * temperature_c
    ) / (_MAGNUS_B + temperature_c)
    return min(max(100.0 * math.exp(exponent), 1.0), 100.0)


def _icao_to_iata(code: str | None) -> str | None:
    if not code:
        return None
    from ..sources.airport_codes import IATA_TO_ICAO

    normalized = code.strip().upper()
    if len(normalized) == 3:
        return normalized
    for iata, icao in IATA_TO_ICAO.items():
        if icao == normalized:
            return iata
    return normalized


def _aena_icao_codes() -> set[str]:
    """Spanish AENA airports (ICAO) from the shared IATA/ICAO table."""
    from ..sources.airport_codes import IATA_TO_ICAO

    return {icao for icao in IATA_TO_ICAO.values() if icao.startswith(("LE", "GC"))}


def map_payload_to_model_features(
    payload: Mapping[str, Any],
    *,
    imputation: Mapping[str, float] | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Translate the public API payload into the notebook's predictor columns.

    Returns ``(features, imputed_fields, resolved)`` where ``resolved`` records
    the derived routes/airports for logging.
    """
    imputation = imputation or {}

    hour = int(payload["hour_of_day"])
    if hour < 0 or hour > 23:
        raise ValueError(f"hora_vuelo outside the notebook domain: {hour}")
    day_of_week = int(payload["day_of_week"])
    if day_of_week < 0 or day_of_week > 6:
        raise ValueError(f"day_of_week must be in 0..6 (0=lunes): {day_of_week}")
    # Frontend convention: 0 = Monday. Notebook convention: 1 = Monday (isodow).
    dia_semana = day_of_week + 1

    origin_code = payload.get("origin_iata")
    destination_code = payload.get("destination_iata")
    origin_iata = _icao_to_iata(origin_code)
    destination_iata = _icao_to_iata(destination_code)

    explicit_other = payload.get("other_airport_iata")
    if explicit_other:
        other_airport = str(explicit_other).strip().upper()
        other_source = "explicit"
    else:
        aena = _aena_icao_codes()
        origin_icao = str(origin_code).strip().upper() if origin_code else None
        destination_icao = str(destination_code).strip().upper() if destination_code else None
        if origin_icao in aena and destination_iata:
            other_airport, other_source = destination_iata, "destination"
        elif destination_icao in aena and origin_iata:
            other_airport, other_source = origin_iata, "origin"
        elif destination_iata:
            other_airport, other_source = destination_iata, "destination"
        elif origin_iata:
            other_airport, other_source = origin_iata, "origin"
        else:
            other_airport, other_source = RARE_CATEGORY, "fallback"

    imputed: list[str] = []

    temperature = payload.get("weather_temperature_2m")
    dew_point = payload.get("dew_point_c")
    humidity = payload.get("relative_humidity")

    if temperature is None:
        temperature = imputation.get("temperatura_metar")
        imputed.append("temperatura_metar")
    if dew_point is None:
        if temperature is not None and humidity is not None:
            dew_point = _magnus_dew_point(float(temperature), float(humidity))
        else:
            dew_point = imputation.get("punto_rocio_metar")
            imputed.append("punto_rocio_metar")
    if humidity is None:
        if temperature is not None and dew_point is not None:
            humidity = _magnus_relative_humidity(float(temperature), float(dew_point))
        else:
            humidity = imputation.get("relh")
            imputed.append("relh")

    for name, value in (
        ("temperatura_metar", temperature),
        ("punto_rocio_metar", dew_point),
        ("relh", humidity),
    ):
        if value is None:
            raise ValueError(f"Missing METAR predictor {name} and no imputation value available")

    features = {
        "hora_vuelo": hour,
        "dia_semana": dia_semana,
        "airline_iata": str(payload["airline"]).strip().upper(),
        "other_airport_iata": other_airport,
        "temperatura_metar": float(temperature),
        "punto_rocio_metar": float(dew_point),
        "relh": float(humidity),
    }
    resolved = {
        "origin_iata": origin_iata,
        "destination_iata": destination_iata,
        "other_airport_iata": other_airport,
        "other_airport_source": other_source,
    }
    return features, imputed, resolved


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
@dataclass
class TwoStageDelayModel:
    """Two-stage predictor loaded from an exported artifact."""

    version: str
    rules_classification: dict[str, dict[str, Any]]
    rules_regression: dict[str, dict[str, Any]]
    columns_classification: list[str]
    columns_classification_design: list[str]
    coefficients_classification: np.ndarray
    threshold: float
    columns_regression: list[str]
    gbm: Any
    imputation: dict[str, float]
    metadata: dict[str, Any]

    # -- construction ------------------------------------------------------
    @classmethod
    def load(
        cls,
        artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
        rules_path: str | Path | None = DEFAULT_RULES_PATH,
    ) -> TwoStageDelayModel:
        import joblib

        artifact_path = Path(artifact_path)
        if not artifact_path.exists():
            raise FileNotFoundError(f"Two-stage artifact not found: {artifact_path}")
        artifact = joblib.load(artifact_path)
        model = cls(
            version=str(artifact.get("version", ARTIFACT_VERSION)),
            rules_classification=artifact["rules_classification"],
            rules_regression=artifact["rules_regression"],
            columns_classification=list(artifact["columns_classification"]),
            columns_classification_design=list(artifact["columns_classification_design"]),
            coefficients_classification=np.asarray(
                artifact["coefficients_classification"], dtype=float
            ),
            threshold=float(artifact["threshold"]),
            columns_regression=list(artifact["columns_regression"]),
            gbm=artifact["gbm"],
            imputation=dict(artifact.get("imputation", {})),
            metadata=dict(artifact.get("metadata", {})),
        )
        if rules_path is not None and Path(rules_path).exists():
            exported = json.loads(Path(rules_path).read_text(encoding="utf-8"))
            if exported.get("version") and exported["version"] != model.version:
                LOGGER.warning(
                    "Rules file version %s differs from artifact version %s",
                    exported["version"],
                    model.version,
                )
        return model

    # -- inference ---------------------------------------------------------
    def _design(
        self,
        frame: pd.DataFrame,
        rules: Mapping[str, Mapping[str, Any]],
        columns: Sequence[str],
        design_columns: Sequence[str] | None = None,
    ) -> np.ndarray:
        prepared = apply_category_rules(frame, rules)
        matrix, matrix_columns = build_model_matrix(prepared, rules, columns=columns)
        if design_columns is None:
            # Regression stage: the GBM consumes the aligned matrix as-is (no
            # intercept, no QR reduction), exactly like the notebook's literal
            # ``x_reg_dev`` / ``x_reg_test``.
            return matrix
        return build_design_matrix(matrix, matrix_columns, design_columns)

    def predict_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Predict for a frame that already contains the model's raw columns."""
        probabilities = _expit(
            self._design(
                frame,
                self.rules_classification,
                self.columns_classification,
                self.columns_classification_design,
            )
            @ self.coefficients_classification
        )
        delayed = probabilities >= self.threshold

        minutes = np.zeros(len(frame), dtype=float)
        if bool(delayed.any()):
            delayed_frame = frame.loc[delayed]
            regression_design = self._design(
                delayed_frame,
                self.rules_regression,
                self.columns_regression,
            )
            predicted_log = self.gbm.predict(regression_design)
            minutes[np.asarray(delayed, dtype=bool)] = np.maximum(
                PREDICTION_FLOOR_MINUTES, np.expm1(predicted_log)
            )

        return pd.DataFrame(
            {
                "prob_retraso": probabilities,
                "prediccion_retraso_15min": delayed.astype(bool),
                "minutos_retraso_estimados": minutes,
            },
            index=frame.index,
        )

    def predict(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Predict a single API payload, returning the public response fields."""
        features, imputed, resolved = map_payload_to_model_features(
            payload, imputation=self.imputation
        )
        frame = pd.DataFrame([features])
        result = self.predict_frame(frame).iloc[0]
        probability = float(result["prob_retraso"])
        is_delayed = bool(result["prediccion_retraso_15min"])
        minutes = float(result["minutos_retraso_estimados"])
        factors = self.explain(features)
        return {
            "prob_retraso": probability,
            "prediccion_retraso_15min": is_delayed,
            "minutos_retraso_estimados": minutes,
            "umbral_aplicado": float(self.threshold),
            "factores": factors,
            "imputaciones": imputed,
            "resolved_features": resolved,
            "model_version": f"two-stage-{self.version}",
        }

    # -- explainability ----------------------------------------------------
    def explain(
        self,
        features: Mapping[str, Any],
        *,
        max_per_direction: int = 3,
        min_abs_contribution: float = MIN_FACTOR_CONTRIBUTION,
    ) -> list[dict[str, Any]]:
        """Explain the **classification stage** for a single observation.

        Contributions follow the logistic-regression decomposition
        ``logit(p) = intercept + sum(coef_j * x_j)`` computed on the very same
        design matrix used to predict, so:

        * positive values push the estimated delay probability up
          (``direction="increase"``),
        * negative values push it down (``direction="decrease"``),
        * near-zero values are dropped instead of reported as noise.

        The numbers are log-odds contributions — **not** percentage points nor
        minutes — and are a local explanation of this prediction, not a global
        feature importance. The regression stage (minutes) is not explained
        here.
        """
        _, contributions = self._logit_contributions(features)
        ranked = sorted(
            (
                (variable, contribution)
                for variable, contribution in contributions.items()
                if abs(contribution) >= min_abs_contribution
            ),
            key=lambda item: abs(item[1]),
            reverse=True,
        )
        increasing = [item for item in ranked if item[1] > 0][:max_per_direction]
        decreasing = [item for item in ranked if item[1] < 0][:max_per_direction]
        return [
            self._factor_payload(variable, contribution, features)
            for variable, contribution in increasing + decreasing
        ]

    def _logit_contributions(self, features: Mapping[str, Any]) -> tuple[float, dict[str, float]]:
        """Return ``(intercept, contributions)`` of the stage-1 log-odds.

        ``intercept + sum(contributions.values())`` reconstructs the predicted
        logit for the observation (verified in the test suite).
        """
        frame = pd.DataFrame([features])
        prepared = apply_category_rules(frame, self.rules_classification)
        matrix, matrix_columns = build_model_matrix(
            prepared, self.rules_classification, columns=self.columns_classification
        )
        design = build_design_matrix(matrix, matrix_columns, self.columns_classification_design)[0]

        intercept = 0.0
        aggregated: dict[str, float] = dict.fromkeys(PREDICTOR_VARIABLES, 0.0)
        for name, coef, value in zip(
            self.columns_classification_design,
            self.coefficients_classification,
            design,
            strict=True,
        ):
            contribution = float(coef * value)
            if name == "(Intercept)":
                intercept = contribution
                continue
            # One-hot columns are aggregated per source variable: the
            # contribution of the reference category is exactly 0 (all of its
            # dummies are 0), so it never shows up as a fake universal effect.
            for variable in PREDICTOR_VARIABLES:
                if name.startswith(variable):
                    aggregated[variable] += contribution
                    break
        return intercept, aggregated

    @staticmethod
    def _factor_payload(
        variable: str, contribution: float, features: Mapping[str, Any]
    ) -> dict[str, Any]:
        direction = "increase" if contribution > 0 else "decrease"
        return {
            "id": variable,
            "title": FACTOR_LABELS.get(variable, variable),
            "value": format_factor_value(variable, features.get(variable)),
            "contribution": round(contribution, 4),
            "direction": direction,
            # ``impact`` keeps the original wire semantics: positive means the
            # variable increases the delay risk.
            "impact": "positive" if direction == "increase" else "negative",
        }


__all__ = [
    "ARTIFACT_VERSION",
    "CATEGORICAL_VARIABLES",
    "DEFAULT_ARTIFACT_PATH",
    "DEFAULT_RULES_PATH",
    "DELAY_CLASSIFICATION_MINUTES",
    "GROUPED_VARIABLES",
    "MIN_CATEGORY_FREQUENCY",
    "MIN_FACTOR_CONTRIBUTION",
    "N_TREES",
    "PREDICTION_FLOOR_MINUTES",
    "PREDICTOR_VARIABLES",
    "RARE_CATEGORY",
    "TARGET_COLUMN",
    "TEMPORAL_DOMAINS",
    "TwoStageDelayModel",
    "apply_category_rules",
    "build_design_matrix",
    "build_model_matrix",
    "fit_gbm",
    "fit_logistic",
    "format_factor_value",
    "learn_category_rules",
    "map_payload_to_model_features",
    "rank_reduce_design",
    "select_threshold",
]
