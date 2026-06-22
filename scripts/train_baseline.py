"""Train a baseline LightGBM regressor and evaluate on temporally-split data.

Reads train/val/test.parquet from data/processed/ by default and writes
model + metrics to models/ by default.

Usage:
    python scripts/train_baseline.py --data-dir data/processed --output-dir models --seed 42

"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sys

# allow importing sibling modules in scripts/ even when it's not a package
_scripts_root = str(Path(__file__).resolve().parent)
if _scripts_root not in sys.path:
    sys.path.insert(0, _scripts_root)
from train_baseline_helpers import _safe_read_parquet, _prepare_features, _metrics


# defaults / paths
DEFAULT_DATA_DIR = Path("data/processed")
DEFAULT_OUTPUT_DIR = Path("models")
EVIDENCE_PATH = Path(".omo/evidence/task-16-baseline-metrics.json")
NOTEPAD = Path(".omo/notepads/aeropredict-gap-closure/learnings.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_dir: Path = args.data_dir
    out_dir: Path = args.output_dir
    seed: int = int(args.seed)

    train_path = data_dir / "train.parquet"
    val_path = data_dir / "val.parquet"
    test_path = data_dir / "test.parquet"

    print("Loading datasets from:", data_dir)
    # read datasets but allow dry-run to exit gracefully if files missing
    try:
        train_df = _safe_read_parquet(train_path)
        val_df = _safe_read_parquet(val_path)
        test_df = _safe_read_parquet(test_path)
    except FileNotFoundError as exc:
        if args.dry_run:
            print(f"Dry-run: dataset file missing: {exc}. Exiting dry-run without error.")
            return 0
        raise

    print(f"Shapes -> train: {train_df.shape}, val: {val_df.shape}, test: {test_df.shape}")

    # Ensure target present
    for name, df in (("train", train_df), ("val", val_df), ("test", test_df)):
        if "delay_target" not in df.columns:
            raise KeyError(f"Expected target column 'delay_target' missing from {name} set")

    if args.dry_run:
        # show candidate feature list after drops
        sample_X, feature_names = _prepare_features(train_df)
        print("Dry-run: prepared feature matrix shapes:")
        print(" train X:", sample_X.shape)
        print(" feature names:", feature_names)
        return 0

    # Prepare X/y
    X_train, feature_names = _prepare_features(train_df)
    y_train = train_df["delay_target"].astype(float).to_numpy()
    X_val, _ = _prepare_features(val_df)
    y_val = val_df["delay_target"].astype(float).to_numpy()
    X_test, _ = _prepare_features(test_df)
    y_test = test_df["delay_target"].astype(float).to_numpy()

    # Align columns (in case some splits lack a column)
    # Ensure same column order
    common_cols = [c for c in feature_names if c in X_train.columns]
    X_train = X_train[common_cols]
    X_val = X_val.reindex(columns=common_cols, fill_value=-1)
    X_test = X_test.reindex(columns=common_cols, fill_value=-1)

    # Train LightGBM regressor
    # import LightGBM lazily so --dry-run works even if package not installed
    try:
        import lightgbm as lgb  # type: ignore
    except Exception as exc:
        raise RuntimeError("lightgbm is required for training; install it in your env") from exc

    model = lgb.LGBMRegressor(
        objective="regression",
        metric="rmse",
        n_estimators=500,
        learning_rate=0.1,
        max_depth=6,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=seed,
    )

    print("Training LightGBM model with random_state=", seed)
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(50)],
    )

    # Predictions
    y_train_pred = model.predict(X_train)
    y_val_pred = model.predict(X_val)
    y_test_pred = model.predict(X_test)

    metrics = {
        "train": _metrics(y_train, y_train_pred),
        "val": _metrics(y_val, y_val_pred),
        "test": _metrics(y_test, y_test_pred),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    # Save LightGBM model (text format)
    model_path = out_dir / "baseline_model.txt"
    # sklearn API wrapper exposes booster_
    model.booster_.save_model(str(model_path))

    metrics_path = out_dir / "baseline_metrics.json"
    metrics_with_meta = {
        "created_at": datetime.utcnow().isoformat(),
        "seed": seed,
        "counts": {"train": int(len(y_train)), "val": int(len(y_val)), "test": int(len(y_test))},
        "metrics": metrics,
    }
    metrics_path.write_text(json.dumps(metrics_with_meta, indent=2))

    # Save evidence copy
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(metrics_with_meta, indent=2))

    # Feature importance plot
    fig_dir = Path("reports/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    imp = model.feature_importances_
    # guard length
    if len(imp) == len(common_cols):
        idx = np.argsort(imp)[::-1]
        topk = min(30, len(common_cols))
        names = [common_cols[i] for i in idx[:topk]]
        values = imp[idx[:topk]]
        plt.figure(figsize=(8, max(3, topk * 0.25)))
        plt.barh(range(len(values))[::-1], values[::-1], align="center")
        plt.yticks(range(len(values))[::-1], names[::-1])
        plt.xlabel("importance")
        plt.title("Feature importance — baseline LightGBM")
        fig_path = fig_dir / "feature_importance_baseline.png"
        plt.tight_layout()
        plt.savefig(fig_path, dpi=150)
        plt.close()
    else:
        print("Warning: feature importance length mismatch; skipping plot")

    # Append finding to notepad
    NOTEPAD.parent.mkdir(parents=True, exist_ok=True)
    with NOTEPAD.open("a", encoding="utf-8") as fh:
        fh.write(
            f"[{datetime.utcnow().isoformat()}] baseline_lightgbm trained. test_rmse={metrics['test']['rmse']:.3f}, test_mae={metrics['test']['mae']:.3f}, test_r2={metrics['test']['r2']:.3f}\n"
        )

    # Print concise summary
    test_rmse = metrics["test"]["rmse"]
    test_mae = metrics["test"]["mae"]
    test_r2 = metrics["test"]["r2"]
    print(
        "Baseline LightGBM: RMSE={:.2f} min, MAE={:.2f} min, R²={:.3f} on test ({})".format(
            test_rmse, test_mae, test_r2, len(y_test)
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
