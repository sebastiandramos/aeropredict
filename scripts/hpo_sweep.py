"""Hyperparameter sweep for LightGBM delay prediction with Optuna + MLflow.

Creates artifacts in models/ and logs runs to a local mlflow tracking folder (./mlruns).

Usage:
    python scripts/hpo_sweep.py --data-dir data/processed --output-dir models --trials 40 --seed 42

Known constraints:
- Uses 3-fold CV on the training set to evaluate trials (RMSE).
- After the study the best trial is retrained on train+val and evaluated on test.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
import sys
from typing import Any, Dict, List

import numpy as np
import pandas as pd


# allow importing sibling modules in scripts/
_scripts_root = str(Path(__file__).resolve().parent)
if _scripts_root not in sys.path:
    sys.path.insert(0, _scripts_root)
from train_baseline_helpers import _safe_read_parquet, _prepare_features, _metrics  # type: ignore


DEFAULT_DATA_DIR = Path("data/processed")
DEFAULT_OUTPUT_DIR = Path("models")
EVIDENCE_DIR = Path(".omo/evidence")
NOTEPAD = Path(".omo/notepads/aeropredict-gap-closure/learnings.md")


def _load_splits(data_dir: Path, dry_run: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train_path = data_dir / "train.parquet"
    val_path = data_dir / "val.parquet"
    test_path = data_dir / "test.parquet"
    try:
        train_df = _safe_read_parquet(train_path)
        val_df = _safe_read_parquet(val_path)
        test_df = _safe_read_parquet(test_path)
    except FileNotFoundError as exc:
        if dry_run:
            print(f"Dry-run: missing dataset file: {exc}. Exiting dry-run.")
            raise
        raise
    return train_df, val_df, test_df


def _prepare_Xy_for_model(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, List[str]]:
    X, feature_names = _prepare_features(df)
    y = df["delay_target"].astype(float).to_numpy()
    return X, y, feature_names


def _objective_factory(X_train: pd.DataFrame, y_train: np.ndarray, seed: int):
    import lightgbm as lgb  # local import for dry-run safety
    from sklearn.model_selection import KFold

    def objective(trial: optuna.Trial) -> float:
        # Search space per requirements
        params = {
            "n_estimators": int(trial.suggest_categorical("n_estimators", [50, 100, 200, 500])),
            "max_depth": int(trial.suggest_categorical("max_depth", [5, 7, 10, 15])),
            "learning_rate": float(trial.suggest_categorical("learning_rate", [0.01, 0.05, 0.1, 0.2])),
            "num_leaves": int(trial.suggest_categorical("num_leaves", [15, 31, 63])),
            "subsample": float(trial.suggest_categorical("subsample", [0.7, 0.8, 1.0])),
            "colsample_bytree": float(trial.suggest_categorical("colsample_bytree", [0.7, 0.8, 1.0])),
            "random_state": seed,
            "objective": "regression",
            "metric": "rmse",
        }

        kf = KFold(n_splits=3, shuffle=True, random_state=seed)
        rmses: List[float] = []
        maes: List[float] = []
        r2s: List[float] = []

        for train_idx, valid_idx in kf.split(X_train):
            X_tr = X_train.iloc[train_idx]
            y_tr = y_train[train_idx]
            X_val = X_train.iloc[valid_idx]
            y_val = y_train[valid_idx]

            model = lgb.LGBMRegressor(**params)
            # LightGBM sklearn API changed verbose arg handling across versions
            try:
                model.fit(
                    X_tr,
                    y_tr,
                    eval_set=[(X_val, y_val)],
                    callbacks=[lgb.early_stopping(50)],
                    verbose=False,
                )
            except TypeError:
                model.fit(
                    X_tr,
                    y_tr,
                    eval_set=[(X_val, y_val)],
                    callbacks=[lgb.early_stopping(50)],
                )

            y_pred = model.predict(X_val)
            m = _metrics(y_val, y_pred)
            rmses.append(m["rmse"])
            maes.append(m["mae"])
            r2s.append(m["r2"])

        mean_rmse = float(np.mean(rmses))
        # report intermediate value to Optuna
        trial.set_user_attr("cv_rmse_std", float(np.std(rmses)))
        trial.set_user_attr("cv_mae_mean", float(np.mean(maes)))
        trial.set_user_attr("cv_r2_mean", float(np.mean(r2s)))
        return mean_rmse

    return objective


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_dir: Path = args.data_dir
    out_dir: Path = args.output_dir
    trials = int(args.trials)
    seed = int(args.seed)

    print("Loading train/val/test from:", data_dir)
    try:
        train_df, val_df, test_df = _load_splits(data_dir, dry_run=args.dry_run)
    except FileNotFoundError:
        if args.dry_run:
            print("Dry-run exit: dataset files missing")
            return 0
        raise

    print(f"Shapes -> train: {train_df.shape}, val: {val_df.shape}, test: {test_df.shape}")

    if args.dry_run:
        # show candidate feature list after preparation
        sample_X, feature_names = _prepare_features(train_df)
        print("Dry-run: prepared feature matrix shapes:")
        print(" train X:", sample_X.shape)
        print(" feature names:", feature_names)
        return 0

    X_train, y_train, feature_names = _prepare_Xy_for_model(train_df)
    X_val, y_val, _ = _prepare_Xy_for_model(val_df)
    X_test, y_test, _ = _prepare_Xy_for_model(test_df)

    # Align columns
    common_cols = [c for c in feature_names if c in X_train.columns]
    X_train = X_train[common_cols]
    X_val = X_val.reindex(columns=common_cols, fill_value=-1)
    X_test = X_test.reindex(columns=common_cols, fill_value=-1)

    # MLflow local tracking and Optuna study are imported lazily so --dry-run works
    try:
        import optuna
    except Exception:
        optuna = None  # type: ignore

    try:
        import mlflow
    except Exception:
        mlflow = None  # type: ignore

    if args.dry_run:
        # dry-run early-exit already handled above; this is a safety net
        print("Dry-run: skipping Optuna/MLflow setup")
        return 0

    if optuna is None:
        raise RuntimeError("optuna is required to run HPO; install optuna in your environment")
    if mlflow is None:
        raise RuntimeError("mlflow is required to run HPO; install mlflow in your environment")

    # MLflow tracking
    mlflow.set_tracking_uri("file:./mlruns")
    exp_name = "hpo_delay_predictor"
    mlflow.set_experiment(exp_name)

    # Optuna study
    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="minimize", sampler=sampler)
    objective = _objective_factory(X_train, y_train, seed)

    print(f"Starting Optuna study for {trials} trials (seed={seed})")
    start_time = time.time()
    trial_results: List[Dict[str, Any]] = []

    for t in range(trials):
        trial = study.ask()
        # run objective and log with MLflow inside each trial
        with mlflow.start_run(nested=False):
            # suggest params (we must call objective via study.tell later to keep study consistent)
            try:
                value = objective(trial)
                study.tell(trial, value)
            except Exception as exc:  # safety: mark failed trial
                study.tell(trial, float("inf"))
                raise

            # collect trial metadata
            tr = study.trials[-1]
            tparams = tr.params
            user_attrs = tr.user_attrs
            # log params and metrics
            mlflow.log_params({k: (int(v) if isinstance(v, (int, np.integer)) else float(v) if isinstance(v, (float, np.floating)) else v) for k, v in tparams.items()})
            mlflow.log_metric("cv_rmse_mean", float(tr.value))
            if "cv_rmse_std" in user_attrs:
                mlflow.log_metric("cv_rmse_std", float(user_attrs["cv_rmse_std"]))
            if "cv_mae_mean" in user_attrs:
                mlflow.log_metric("cv_mae_mean", float(user_attrs["cv_mae_mean"]))
            if "cv_r2_mean" in user_attrs:
                mlflow.log_metric("cv_r2_mean", float(user_attrs["cv_r2_mean"]))

            trial_results.append({
                "trial_number": tr.number,
                "params": tparams,
                "cv_rmse_mean": float(tr.value),
                "cv_rmse_std": float(user_attrs.get("cv_rmse_std", "nan")),
                "cv_mae_mean": float(user_attrs.get("cv_mae_mean", "nan")),
                "cv_r2_mean": float(user_attrs.get("cv_r2_mean", "nan")),
            })

    elapsed = time.time() - start_time
    print(f"Optuna study completed in {elapsed:.1f}s. Best value (RMSE)={study.best_value:.4f}")

    # Best trial params
    best_params = study.best_trial.params

    # Retrain best model on train+val and evaluate on test
    import lightgbm as lgb  # type: ignore

    X_trainval = pd.concat([X_train, X_val], axis=0).reset_index(drop=True)
    y_trainval = np.concatenate([y_train, y_val], axis=0)

    print("Retraining best model on train+val with params:", best_params)
    model = lgb.LGBMRegressor(
        objective="regression",
        metric="rmse",
        random_state=seed,
        **{k: int(v) if k in ("n_estimators", "max_depth", "num_leaves") else float(v) if k in ("learning_rate", "subsample", "colsample_bytree") else v for k, v in best_params.items()},
    )

    try:
        model.fit(X_trainval, y_trainval, verbose=False)
    except TypeError:
        model.fit(X_trainval, y_trainval)

    y_test_pred = model.predict(X_test)
    test_metrics = _metrics(y_test, y_test_pred)

    out_dir.mkdir(parents=True, exist_ok=True)
    best_model_path = out_dir / "best_model.txt"
    try:
        booster = model.booster_
        booster.save_model(str(best_model_path))
    except Exception:
        model.booster_.save_model(str(best_model_path))

    # Save JSON artifacts
    (out_dir / "best_params.json").write_text(json.dumps(best_params, indent=2))
    (out_dir / "hpo_results.json").write_text(json.dumps(sorted(trial_results, key=lambda x: x["cv_rmse_mean"]), indent=2))

    # MLflow: log final test metrics and register model
    with mlflow.start_run(run_name="hpo_best_model"):
        mlflow.log_params({f"best_{k}": v for k, v in best_params.items()})
        mlflow.log_metrics({f"test_{k}": float(v) for k, v in test_metrics.items()})
        # try to log feature count and top importances
        try:
            imp = list(model.feature_importances_)
            names = list(X_trainval.columns)
            if len(imp) == len(names):
                idx = int(np.argsort(imp)[-1])
                mlflow.log_metric("feature_count", len(names))
                # log top 10 as params
                topk = min(10, len(names))
                top_idx = np.argsort(imp)[::-1][:topk]
                for i, ix in enumerate(top_idx, start=1):
                    mlflow.log_param(f"top_feature_{i}", names[int(ix)])
        except Exception:
            pass

        # Log model artifact and register
        try:
            # prefer logging sklearn model wrapper
            mlflow.lightgbm.log_model(model.booster_, artifact_path="model", registered_model_name="delay-predictor")
        except Exception:
            try:
                mlflow.sklearn.log_model(model, artifact_path="model", registered_model_name="delay-predictor")
            except Exception:
                print("Warning: model registration failed; continuing")

    # Evidence files
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "task-17-mlflow-runs.json").write_text(json.dumps(trial_results, indent=2))
    (EVIDENCE_DIR / "task-17-best-params.json").write_text(json.dumps(best_params, indent=2))

    # Documentation summary
    docs_dir = Path("docs")
    docs_dir.mkdir(parents=True, exist_ok=True)
    md = docs_dir / "ml_hpo_results.md"
    md.write_text(
        """
# HPO Results - LightGBM delay predictor

This document summarizes the hyperparameter optimization run performed with Optuna and logged using MLflow.

Best hyperparameters (summary):

```
%s
```

Test set performance:

```
%s
```

Top features (logged in MLflow) and feature count available in the run artifacts.

Training duration: %0.1fs

Limitations: tuned on small dataset; final model trained on train+val and evaluated on test only. Do not use test to tune.
"""
        % (json.dumps(best_params, indent=2), json.dumps({"test": test_metrics}, indent=2), elapsed),
        encoding="utf-8",
    )

    # Append short note to notepad
    NOTEPAD.parent.mkdir(parents=True, exist_ok=True)
    with NOTEPAD.open("a", encoding="utf-8") as fh:
        fh.write(
            f"[{datetime.utcnow().isoformat()}] hpo_sweep completed. best_rmse={study.best_value:.4f}, test_rmse={test_metrics['rmse']:.4f}\n"
        )

    print("Wrote best model to:", best_model_path)
    print("Wrote artifacts:", out_dir / "best_params.json", out_dir / "hpo_results.json")
    print("Wrote evidence:", EVIDENCE_DIR / "task-17-mlflow-runs.json", EVIDENCE_DIR / "task-17-best-params.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
