#!/usr/bin/env python3
"""Export the notebook's two-stage delay model to a Python artifact.

The notebook (``AeroPredict_TFM_Modelado_Final_Git.ipynb``) stores its result in
``aeropredict_modelos_finales_v1.rds``. That file is not versioned in this
repository and R is not part of the serving stack, so this script offers two
routes to a Python-native artifact:

``--from-db`` (default)
    Re-runs the notebook's exact pipeline (same SQL, cutoff, 60/20/20
    chronological split, category grouping, ``model.matrix`` contrasts, QR
    rank reduction, threshold search, ``log1p``/``expm1`` and the 15-minute
    floor) over ``gold.feature_store`` and fits a scikit-learn equivalent of
    each stage. Requires ``POSTGRES_URI`` (``doppler run -- ...``).

``--rds PATH``
    Reads the exported ``.rds`` through ``Rscript`` to recover the stage-1
    coefficients, column layout and category rules verbatim. R's ``gbm``
    object cannot be deserialised by scikit-learn, so stage 2 still needs
    ``--from-db`` to refit the equivalent gradient booster on the same
    development rows. Pass both flags to combine them.

Outputs (default ``models/``):

* ``aeropredict_modelos_finales_v1.pkl`` — sklearn models + full metadata.
* ``preprocessor_rules.json`` — human-readable rules (categories, columns,
  threshold, imputation medians).
* ``aeropredict_metricas_*.csv`` and ``aeropredict_predicciones_*_test.csv`` —
  the notebook's evidence files, used by ``tests/test_two_stage_predict.py``.

No credentials are written anywhere; the DSN is read from the environment.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from aeropredict.api.predict import (
    ARTIFACT_VERSION,
    DEFAULT_ARTIFACT_PATH,
    DEFAULT_RULES_PATH,
    DELAY_CLASSIFICATION_MINUTES,
    MIN_CATEGORY_FREQUENCY,
    N_TREES,
    PREDICTION_FLOOR_MINUTES,
    TARGET_COLUMN,
    TEMPORAL_DOMAINS,
    TwoStageDelayModel,
    apply_category_rules,
    build_model_matrix,
    fit_gbm,
    fit_logistic,
    learn_category_rules,
    rank_reduce_design,
    select_threshold,
)

LOGGER = logging.getLogger("export_model")

NOTEBOOK_SQL = """
SELECT aena_airport_iata, flight_number, flight_type, scheduled_local,
       hora_vuelo, dia_semana, airline_iata, other_airport_iata,
       temperatura_metar, punto_rocio_metar, relh, retraso_minutos
FROM gold.feature_store
WHERE DATE(scheduled_local) <= DATE %(cutoff)s
  AND retraso_minutos IS NOT NULL
"""
NUMERIC_PREDICTORS = ("temperatura_metar", "punto_rocio_metar", "relh")


# ---------------------------------------------------------------------------
# Data loading and the notebook's transformations (CELL 7 / CELL 9)
# ---------------------------------------------------------------------------
def load_frame(*, cutoff: str, csv_path: str | None) -> pd.DataFrame:
    if csv_path:
        frame = pd.read_csv(csv_path)
    else:
        import os

        import psycopg2

        dsn = os.environ.get("POSTGRES_URI")
        if not dsn:
            raise SystemExit(
                "POSTGRES_URI is not set. Run with `doppler run -- python "
                "scripts/export_r_model_to_python.py --from-db` or pass --input-csv."
            )
        with psycopg2.connect(dsn, connect_timeout=30) as connection:
            frame = pd.read_sql(NOTEBOOK_SQL, connection, params={"cutoff": cutoff})

    required = {
        "aena_airport_iata",
        "flight_number",
        "scheduled_local",
        "hora_vuelo",
        "dia_semana",
        "airline_iata",
        "other_airport_iata",
        TARGET_COLUMN,
    }
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(f"Input data is missing columns: {sorted(missing)}")

    frame = frame.copy()
    frame["fecha_vuelo"] = frame["scheduled_local"].astype(str).str.slice(0, 10)
    frame["hora_vuelo"] = pd.to_numeric(frame["hora_vuelo"], errors="coerce")
    frame["dia_semana"] = pd.to_numeric(frame["dia_semana"], errors="coerce")
    for column in NUMERIC_PREDICTORS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame[TARGET_COLUMN] = pd.to_numeric(frame[TARGET_COLUMN], errors="coerce")
    for column in ("airline_iata", "other_airport_iata"):
        frame[column] = frame[column].astype("string")
    frame["retraso_15_min"] = (frame[TARGET_COLUMN] >= DELAY_CLASSIFICATION_MINUTES).astype(int)

    predictor_columns = [
        "hora_vuelo",
        "dia_semana",
        "airline_iata",
        "other_airport_iata",
        *NUMERIC_PREDICTORS,
    ]
    # R: filter(complete.cases(...)) over predictors + target.
    frame = frame.dropna(subset=[*predictor_columns, TARGET_COLUMN])
    frame = frame.sort_values("fecha_vuelo").reset_index(drop=True)
    LOGGER.info(
        "Loaded %d complete rows (%s → %s)",
        len(frame),
        frame["fecha_vuelo"].min(),
        frame["fecha_vuelo"].max(),
    )
    return frame


def chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reproduce CELL 9: 60% train, 20% validation, 20% test by flight date."""
    dates = sorted(frame["fecha_vuelo"].unique().tolist())
    if len(dates) < 3:
        raise SystemExit("Not enough distinct flight dates for a 60/20/20 chronological split")
    validation_cut = dates[int(np.floor(len(dates) * 0.60))]
    test_cut = dates[int(np.floor(len(dates) * 0.80))]
    train = frame[frame["fecha_vuelo"] < validation_cut]
    validation = frame[(frame["fecha_vuelo"] >= validation_cut) & (frame["fecha_vuelo"] < test_cut)]
    test = frame[frame["fecha_vuelo"] >= test_cut]
    LOGGER.info(
        "Split by date: train=%d (%s..%s), val=%d (%s..%s), test=%d (%s..%s)",
        len(train),
        train["fecha_vuelo"].min(),
        train["fecha_vuelo"].max(),
        len(validation),
        validation["fecha_vuelo"].min(),
        validation["fecha_vuelo"].max(),
        len(test),
        test["fecha_vuelo"].min(),
        test["fecha_vuelo"].max(),
    )
    if min(len(train), len(validation), len(test)) == 0:
        raise SystemExit("Chronological split produced an empty partition")
    return train, validation, test


# ---------------------------------------------------------------------------
# Stage 1 (CELL 13 / 17 / 19)
# ---------------------------------------------------------------------------
def _matrix_from_rules(frame: pd.DataFrame, rules: dict) -> tuple[np.ndarray, list[str]]:
    prepared = apply_category_rules(frame, rules)
    return build_model_matrix(prepared, rules)


def fit_classification_stage(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    development: pd.DataFrame,
    test: pd.DataFrame,
) -> dict:
    """Threshold search on validation, then the final model on development data."""
    rules_validation = learn_category_rules(train)
    train_matrix, columns = _matrix_from_rules(train, rules_validation)
    validation_matrix, _ = build_model_matrix(
        apply_category_rules(validation, rules_validation), rules_validation, columns=columns
    )

    # CELL 17: drop constant columns, add intercept, QR rank reduction.
    train_logistic, logistic_columns = _drop_constant(train_matrix, columns)
    validation_logistic, _ = _drop_constant(validation_matrix, columns, keep=logistic_columns)
    design_train, design_columns = rank_reduce_design(train_logistic, logistic_columns)
    design_validation = _select_design(validation_logistic, logistic_columns, design_columns)

    coefficients = fit_logistic(design_train, train["retraso_15_min"].to_numpy())
    validation_probabilities = 1.0 / (
        1.0 + np.exp(-np.clip(design_validation @ coefficients, -60.0, 60.0))
    )
    threshold, threshold_metrics = select_threshold(
        validation["retraso_15_min"].to_numpy(), validation_probabilities
    )
    LOGGER.info(
        "Threshold selected on validation: %.3f (F1=%.4f, precision=%.4f, recall=%.4f)",
        threshold,
        threshold_metrics["f1"],
        threshold_metrics["precision"],
        threshold_metrics["recall"],
    )

    # CELL 19: re-learn the rules on the full development set and refit.
    rules_final = learn_category_rules(development)
    development_matrix, columns_final = _matrix_from_rules(development, rules_final)
    test_matrix, _ = build_model_matrix(
        apply_category_rules(test, rules_final), rules_final, columns=columns_final
    )
    development_logistic, logistic_columns_final = _drop_constant(development_matrix, columns_final)
    test_logistic, _ = _drop_constant(test_matrix, columns_final, keep=logistic_columns_final)
    design_development, design_columns_final = rank_reduce_design(
        development_logistic, logistic_columns_final
    )
    design_test = _select_design(test_logistic, logistic_columns_final, design_columns_final)
    final_coefficients = fit_logistic(design_development, development["retraso_15_min"].to_numpy())

    return {
        "rules": rules_final,
        "columns_matrix": columns_final,
        "columns_design": design_columns_final,
        "coefficients": final_coefficients,
        "threshold": threshold,
        "threshold_metrics": threshold_metrics,
        "design_test": design_test,
        "test_probabilities": 1.0
        / (1.0 + np.exp(-np.clip(design_test @ final_coefficients, -60.0, 60.0))),
        "matrix_test": test_matrix,
        "columns_matrix_test": columns_final,
    }


def _drop_constant(
    matrix: np.ndarray,
    columns: list[str],
    *,
    keep: list[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    if keep is not None:
        position = {column: idx for idx, column in enumerate(columns)}
        return matrix[:, [position[column] for column in keep]], list(keep)
    selected = [idx for idx in range(len(columns)) if np.unique(matrix[:, idx]).size > 1]
    return matrix[:, selected], [columns[idx] for idx in selected]


def _select_design(
    matrix: np.ndarray, matrix_columns: list[str], design_columns: list[str]
) -> np.ndarray:
    from aeropredict.api.predict import build_design_matrix

    return build_design_matrix(matrix, matrix_columns, design_columns)


# ---------------------------------------------------------------------------
# Stage 2 (CELL 21)
# ---------------------------------------------------------------------------
def fit_regression_stage(development: pd.DataFrame, test: pd.DataFrame) -> dict:
    delayed_development = development[development["retraso_15_min"] == 1].copy()
    delayed_test = test[test["retraso_15_min"] == 1].copy()
    if delayed_development.empty or delayed_test.empty:
        raise SystemExit("Regression stage needs delayed flights in both development and test")

    rules = learn_category_rules(delayed_development)
    development_matrix, columns = _matrix_from_rules(delayed_development, rules)
    development_matrix, columns = _drop_constant(development_matrix, columns)
    test_matrix, _ = build_model_matrix(
        apply_category_rules(delayed_test, rules), rules, columns=columns
    )

    target_log = np.log1p(delayed_development[TARGET_COLUMN].to_numpy(dtype=float))
    model = fit_gbm(development_matrix, target_log)
    predicted_log = model.predict(test_matrix)
    predicted_minutes = np.maximum(PREDICTION_FLOOR_MINUTES, np.expm1(predicted_log))

    target = delayed_test[TARGET_COLUMN].to_numpy(dtype=float)
    errors = predicted_minutes - target
    metrics = {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "r2": float(1 - np.sum(errors**2) / np.sum((target - target.mean()) ** 2)),
        "n_test": len(target),
    }
    LOGGER.info(
        "Stage 2 on test (delayed only): MAE=%.2f min, RMSE=%.2f min, R2=%.4f (n=%d)",
        metrics["mae"],
        metrics["rmse"],
        metrics["r2"],
        metrics["n_test"],
    )
    return {
        "rules": rules,
        "columns_matrix": columns,
        "gbm": model,
        "test_predictions": predicted_minutes,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Evidence files
# ---------------------------------------------------------------------------
def classification_metrics(target: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict:
    from sklearn.metrics import (
        accuracy_score,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    predicted = (probabilities >= threshold).astype(int)
    return {
        "accuracy": float(accuracy_score(target, predicted)),
        "precision": float(precision_score(target, predicted, zero_division=0)),
        "recall": float(recall_score(target, predicted, zero_division=0)),
        "f1": float(f1_score(target, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(target, probabilities)),
        "n_test": len(target),
    }


def write_evidence(
    *,
    output_dir: Path,
    test: pd.DataFrame,
    classification: dict,
    regression: dict,
    cutoff: str,
    threshold: float,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    probabilities = classification["test_probabilities"]
    predicted_delay = (probabilities >= threshold).astype(int)
    metrics_classification = classification_metrics(
        test["retraso_15_min"].to_numpy(), probabilities, threshold
    )

    pd.DataFrame(
        [
            {"conjunto": "test", **metrics_classification, "umbral": threshold},
        ]
    ).to_csv(output_dir / "aeropredict_metricas_clasificacion_final.csv", index=False)

    pd.DataFrame(
        [
            {"conjunto": "test_retrasados", **regression["metrics"]},
        ]
    ).to_csv(output_dir / "aeropredict_metricas_regresion_final.csv", index=False)

    pd.DataFrame(
        {
            "variable": classification["columns_design"],
            "coeficiente": classification["coefficients"],
        }
    ).to_csv(output_dir / "aeropredict_coeficientes_logistica.csv", index=False)

    classification_predictions = pd.DataFrame(
        {
            "scheduled_local": test["scheduled_local"].to_numpy(),
            "flight_number": test["flight_number"].to_numpy(),
            "aena_airport_iata": test["aena_airport_iata"].to_numpy(),
            "retraso_15_min_real": test["retraso_15_min"].to_numpy(),
            "probabilidad_retraso": probabilities,
            "prediccion_retraso_15_min": predicted_delay,
            "umbral_aplicado": threshold,
        }
    )
    classification_predictions.to_csv(
        output_dir / "aeropredict_predicciones_clasificacion_test.csv", index=False
    )

    delayed_test = test[test["retraso_15_min"] == 1]
    regression_predictions = pd.DataFrame(
        {
            "aena_airport_iata": delayed_test["aena_airport_iata"].to_numpy(),
            "flight_number": delayed_test["flight_number"].to_numpy(),
            "scheduled_local": delayed_test["scheduled_local"].to_numpy(),
            "retraso_estimado_objetivo": delayed_test[TARGET_COLUMN].to_numpy(),
            "retraso_predicho": regression["test_predictions"],
            "error_absoluto_minutos": np.abs(
                regression["test_predictions"] - delayed_test[TARGET_COLUMN].to_numpy()
            ),
        }
    )
    regression_predictions.to_csv(
        output_dir / "aeropredict_predicciones_regresion_test.csv", index=False
    )

    return {"clasificacion": metrics_classification, "regresion": regression["metrics"]}


def imputation_values(development: pd.DataFrame) -> dict[str, float]:
    """Medians learned on development data, used when optional inputs are absent."""
    return {column: float(development[column].median()) for column in NUMERIC_PREDICTORS}


# ---------------------------------------------------------------------------
# R bridge (--rds)
# ---------------------------------------------------------------------------
R_EXTRACT_SCRIPT = r"""
args <- commandArgs(trailingOnly = TRUE)
rds_path <- args[[1]]
out_path <- args[[2]]
if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("Package 'jsonlite' is required to export the RDS artifact")
}
artefacto <- readRDS(rds_path)
extraer_reglas <- function(reglas) {
  lapply(reglas, function(r) {
    if (identical(r$tipo, "frecuencia")) {
      list(tipo = r$tipo,
           niveles_frecuentes = as.character(r$niveles_frecuentes),
           niveles_finales = as.character(r$niveles_finales),
           frecuencia_minima = r$frecuencia_minima)
    } else {
      list(tipo = r$tipo, niveles_finales = as.character(r$niveles_finales))
    }
  })
}
salida <- list(
  version = as.character(artefacto$version),
  fecha_corte_datos = as.character(artefacto$fecha_corte_datos),
  variables_predictoras = as.character(artefacto$variables_predictoras),
  variables_categoricas = as.character(artefacto$variables_categoricas),
  formula_predictoras = as.character(artefacto$formula_predictoras),
  frecuencia_minima_categoria = artefacto$frecuencia_minima_categoria,
  dominios_temporales = artefacto$dominios_temporales,
  clasificacion = list(
    coeficientes = as.list(artefacto$clasificacion$coeficientes),
    columnas_matriz = as.character(artefacto$clasificacion$columnas_matriz),
    columnas_diseno = as.character(artefacto$clasificacion$columnas_diseno),
    umbral = artefacto$clasificacion$umbral,
    reglas_categorias = extraer_reglas(artefacto$clasificacion$reglas_categorias)
  ),
  regresion = list(
    columnas_matriz = as.character(artefacto$regresion$columnas_matriz),
    n_trees = artefacto$regresion$n.trees,
    suelo_prediccion_minutos = artefacto$regresion$suelo_prediccion_minutos,
    reglas_categorias = extraer_reglas(artefacto$regresion$reglas_categorias)
  )
)
jsonlite::write_json(salida, out_path, auto_unbox = TRUE, pretty = TRUE)
cat("Exported stage-1 metadata to", out_path, "\n")
"""


def export_from_rds(rds_path: str, output_dir: Path) -> dict:
    rscript = subprocess.run(
        ["which", "Rscript"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if not rscript:
        raise SystemExit(
            "Rscript was not found. Install R (and the 'jsonlite' package) to use --rds, "
            "or run with --from-db to reproduce the model in Python."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "aeropredict_rds_metadata.json"
    with tempfile.NamedTemporaryFile("w", suffix=".R", delete=False, encoding="utf-8") as handle:
        handle.write(R_EXTRACT_SCRIPT)
        script_path = handle.name
    try:
        subprocess.run(
            [rscript, script_path, rds_path, str(metadata_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    LOGGER.info("Extracted stage-1 metadata from %s", rds_path)
    return metadata


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def build_artifact(
    *,
    cutoff: str,
    output_artifact: Path,
    output_rules: Path,
    csv_path: str | None,
    rds_path: str | None,
) -> dict:
    frame = load_frame(cutoff=cutoff, csv_path=csv_path)
    train, validation, test = chronological_split(frame)
    development = (
        pd.concat([train, validation], ignore_index=True)
        .sort_values("fecha_vuelo")
        .reset_index(drop=True)
    )

    classification = fit_classification_stage(train, validation, development, test)
    regression = fit_regression_stage(development, test)
    imputation = imputation_values(development)

    rds_metadata = None
    if rds_path:
        rds_metadata = export_from_rds(rds_path, output_artifact.parent)
        rds_metadata_path = output_artifact.parent / "aeropredict_rds_metadata.json"
        if not rds_metadata_path.exists():
            LOGGER.warning(
                "RDS metadata was not produced; stage 2 still comes from the Python refit"
            )

    evidence = write_evidence(
        output_dir=output_artifact.parent,
        test=test,
        classification=classification,
        regression=regression,
        cutoff=cutoff,
        threshold=classification["threshold"],
    )

    metadata = {
        "version": ARTIFACT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source": "rds+db" if rds_path else ("csv" if csv_path else "postgresql"),
        "fecha_corte_datos": cutoff,
        "rds_metadata": rds_metadata,
        "n_rows": len(frame),
        "n_train": len(train),
        "n_validation": len(validation),
        "n_test": len(test),
        "n_development": len(development),
        "n_development_delayed": int((development["retraso_15_min"] == 1).sum()),
        "fecha_inicio_test": str(test["fecha_vuelo"].min()),
        "fecha_fin_test": str(test["fecha_vuelo"].max()),
        "target_definition": f"retraso_15_min = retraso_minutos >= {DELAY_CLASSIFICATION_MINUTES}",
        "metrics_test": evidence,
        "equivalence": {
            "stage1": "sklearn LogisticRegression(penalty=None) on the R QR-reduced design",
            "stage2": (
                "sklearn GradientBoostingRegressor(loss=squared_error, learning_rate=0.03, "
                "n_estimators=300, max_depth=3, min_samples_leaf=20, subsample=0.5, "
                "random_state=2026) fitted on log1p(retraso_minutos)"
            ),
            "note": (
                "R's gbm and scikit-learn's gradient boosting share the squared-error "
                "backfitting scheme but differ in split search details, so stage-2 "
                "predictions are statistically equivalent, not bit-identical."
            ),
        },
        "imputation": imputation,
        "n_trees": N_TREES,
        "prediction_floor_minutes": PREDICTION_FLOOR_MINUTES,
        "frecuencia_minima_categoria": MIN_CATEGORY_FREQUENCY,
        "dominios_temporales": TEMPORAL_DOMAINS,
    }

    artifact = {
        "version": ARTIFACT_VERSION,
        "metadata": metadata,
        "rules_classification": classification["rules"],
        "rules_regression": regression["rules"],
        "columns_classification": classification["columns_matrix"],
        "columns_classification_design": classification["columns_design"],
        "coefficients_classification": classification["coefficients"],
        "threshold": classification["threshold"],
        "columns_regression": regression["columns_matrix"],
        "gbm": regression["gbm"],
        "imputation": imputation,
    }

    import joblib

    output_artifact.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_artifact)
    rules_payload = {
        "version": ARTIFACT_VERSION,
        "fecha_corte_datos": cutoff,
        "prediction_floor_minutes": PREDICTION_FLOOR_MINUTES,
        "frecuencia_minima_categoria": MIN_CATEGORY_FREQUENCY,
        "dominios_temporales": TEMPORAL_DOMAINS,
        "imputation": imputation,
        "clasificacion": {
            "columnas_matriz": classification["columns_matrix"],
            "columnas_diseno": classification["columns_design"],
            "coeficientes": {
                name: float(value)
                for name, value in zip(
                    classification["columns_design"], classification["coefficients"], strict=True
                )
            },
            "umbral": classification["threshold"],
            "reglas_categorias": classification["rules"],
        },
        "regresion": {
            "columnas_matriz": regression["columns_matrix"],
            "n_trees": N_TREES,
            "reglas_categorias": regression["rules"],
        },
        "metricas_test": evidence,
    }
    output_rules.write_text(
        json.dumps(rules_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Round-trip check: the artifact must load and predict through the public path.
    model = TwoStageDelayModel.load(output_artifact, output_rules)
    sample = {
        "hour_of_day": 14,
        "day_of_week": 2,
        "airline": "IB",
        "origin_iata": "LEMD",
        "destination_iata": "LEBL",
        "weather_temperature_2m": 20.0,
        "dew_point_c": 10.0,
        "relative_humidity": 55.0,
    }
    LOGGER.info("Smoke prediction: %s", model.predict(sample))

    LOGGER.info("Artifact written to %s", output_artifact)
    LOGGER.info("Rules written to %s", output_rules)
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Reproduce the notebook pipeline from gold.feature_store (default)",
    )
    parser.add_argument(
        "--rds", help="Path to aeropredict_modelos_finales_v1.rds (requires Rscript)"
    )
    parser.add_argument("--input-csv", help="Alternative CSV with the notebook's source columns")
    parser.add_argument(
        "--date-cutoff", default="2026-10-19", help="fecha_corte_datos (default: %(default)s)"
    )
    parser.add_argument("--output", default=DEFAULT_ARTIFACT_PATH, help="Artifact .pkl path")
    parser.add_argument("--rules-output", default=DEFAULT_RULES_PATH, help="Rules .json path")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if not args.from_db and not args.rds and not args.input_csv:
        args.from_db = True
    if args.rds and not (args.from_db or args.input_csv):
        raise SystemExit(
            "Stage 2 (R gbm) cannot be reconstructed from the .rds without R's gbm runtime. "
            "Pass --from-db (or --input-csv) so the equivalent sklearn booster can be refitted "
            "on the same development rows, or use --from-db alone."
        )

    metadata = build_artifact(
        cutoff=args.date_cutoff,
        output_artifact=Path(args.output),
        output_rules=Path(args.rules_output),
        csv_path=args.input_csv,
        rds_path=args.rds,
    )
    print(json.dumps(metadata["metrics_test"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
