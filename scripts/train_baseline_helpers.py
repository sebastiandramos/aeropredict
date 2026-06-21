"""Helpers for train_baseline.py extracted to keep file size small.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import pandas.api.types as ptypes


DROP_COLS = [
    "delay_target",
    "schedule_missing",
    "icao24",
    "callsign",
    "est_departure_airport",
    "est_arrival_airport",
    "schedule_source",
    "created_at",
    "actual_arrival_dt",
    "scheduled_arrival_dt",
    "scheduled_departure_dt",
    "first_seen",
    "last_seen",
    "actual_arrival",
    "scheduled_departure",
    "scheduled_arrival",
    "departure_airport",
    "arrival_airport",
    "aircraft_icao24",
    "__temporal_dt",
    "flight_date",
]


# include categorical features (explicit list)
CAT_FEATURES = ["airline", "aircraft_type", "aircraft_manufacturer", "aircraft_operator"]


def _safe_read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    return pd.read_parquet(path)


def _prepare_features(df: pd.DataFrame) -> (pd.DataFrame, List[str]):
    # Drop listed non-feature columns if present
    cols = [c for c in df.columns if c not in DROP_COLS]

    X = df[cols].copy()

    # Fill NaNs: numeric -> -1, categorical -> 'UNKNOWN'
    for c in X.columns:
        if X[c].dtype == object or c in CAT_FEATURES:
            X[c] = X[c].fillna("UNKNOWN").astype(str)
        else:
            # numeric - use -1 as sentinel
            try:
                X[c] = X[c].astype(float).fillna(-1)
            except Exception:
                # if conversion fails, treat as categorical
                X[c] = X[c].fillna("UNKNOWN").astype(str)

    # Encode categorical features (all string/object-like columns) to integers (stable mapping via factorize)
    feature_names: List[str] = list(X.columns)
    obj_cols = [
        c
        for c in X.columns
        if ptypes.is_object_dtype(X[c].dtype) or ptypes.is_string_dtype(X[c].dtype)
    ]
    for c in obj_cols:
        codes, uniques = pd.factorize(X[c], sort=True)
        X[c] = codes.astype(int)

    return X, feature_names


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    err = y_true - y_pred
    mse = float(np.mean(err ** 2))
    rmse = float(np.sqrt(mse))
    mae = float(np.mean(np.abs(err)))
    var = float(np.mean((y_true - float(np.mean(y_true))) ** 2))
    r2 = 1.0 - (mse / var) if var > 0 else float("nan")
    return {"rmse": rmse, "mae": mae, "r2": r2}
