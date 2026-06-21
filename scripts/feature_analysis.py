"""Feature analysis for delay prediction.

Standalone script that:
 - generates a mock dataset via scripts/export_ml_dataset.py --mock
 - computes correlation matrix vs target_delay
 - trains a quick LightGBM (n_estimators=50) to get feature importance
 - selects top 18 features by combined score
 - saves figures to reports/figures and evidence to .omo/evidence
 - writes data/processed/selected_features.json

Usage:
  python scripts/feature_analysis.py --mock-rows 1000
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

import lightgbm as lgb


ROOT = Path(".")
REPORT_DIR = ROOT / "reports" / "figures"
EVIDENCE_DIR = ROOT / ".omo" / "evidence"
NOTEPAD = ROOT / ".omo" / "notepads" / "aeropredict-gap-closure" / "learnings.md"
SELECTED_PATH = ROOT / "data" / "processed" / "selected_features.json"


def run_export(mock_rows: int = 1000) -> None:
    cmd = [sys.executable, "scripts/export_ml_dataset.py", "--mock", "--mock-rows", str(mock_rows)]
    subprocess.check_call(cmd)


def load_data() -> pd.DataFrame:
    p = ROOT / "data" / "processed" / "feature_store.parquet"
    if not p.exists():
        raise FileNotFoundError(f"Expected dataset at {p} - run export script first")
    df = pd.read_parquet(p)
    return df


def compute_correlations(df: pd.DataFrame) -> pd.Series:
    numeric = df.select_dtypes(include=["number"]).copy()
    if "target_delay" not in numeric.columns:
        if "target_delay" in numeric.columns:
            pass
    corr = numeric.corrwith(numeric["target_delay"]).drop(labels=["target_delay"])  # type: ignore[arg-type]
    return corr.abs().sort_values(ascending=False)


def lgb_importance(df: pd.DataFrame) -> pd.Series:
    df2 = df.copy()
    y = df2["target_delay"].astype(float)
    X = df2.select_dtypes(include=["number"]).drop(columns=["target_delay"])  # type: ignore[arg-type]
    # Fill NaNs with median
    X = X.fillna(X.median())

    model = lgb.LGBMRegressor(n_estimators=50, random_state=42)
    model.fit(X, y)
    imp = pd.Series(model.feature_importances_, index=X.columns)
    imp = imp.sort_values(ascending=False)
    return imp


def combined_selection(corr: pd.Series, imp: pd.Series, top_k: int = 18) -> List[str]:
    # Normalize and combine
    c1 = corr.reindex(imp.index).fillna(0.0)
    c1 = (c1 - c1.min()) / (c1.max() - c1.min() + 1e-9)
    i1 = (imp - imp.min()) / (imp.max() - imp.min() + 1e-9)
    score = 0.6 * c1 + 0.4 * i1
    score = score.sort_values(ascending=False)
    return list(score.head(top_k).index), score.to_dict()


def save_figures(df: pd.DataFrame, corr: pd.Series, imp: pd.Series) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    # Correlation heatmap of top features
    top_corr = corr.head(20).index.tolist()
    num = df.select_dtypes(include=["number"])
    plt.figure(figsize=(10, 8))
    sns.heatmap(num[top_corr].corr(), annot=False, cmap="vlag")
    p1 = REPORT_DIR / "task-13-correlation.png"
    plt.title("Feature correlation (top 20 numeric)")
    plt.tight_layout()
    plt.savefig(p1)
    plt.close()

    # Importance bar
    plt.figure(figsize=(10, 8))
    imp.head(20).plot(kind="barh")
    plt.gca().invert_yaxis()
    plt.xlabel("Importance")
    plt.title("LightGBM feature importance (n_estimators=50)")
    p2 = REPORT_DIR / "task-13-importance.png"
    plt.tight_layout()
    plt.savefig(p2)
    plt.close()

    # Copy evidence to .omo for CI/tracing
    (EVIDENCE_DIR / "task-13-correlation.png").write_bytes(p1.read_bytes())
    (EVIDENCE_DIR / "task-13-importance.png").write_bytes(p2.read_bytes())


def write_selected(selected: List[str], scores: Dict[str, float]) -> None:
    SELECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = {"selected_features": selected, "scores": scores, "method": "correlation+importance"}
    SELECTED_PATH.write_text(json.dumps(out, indent=2, sort_keys=True))


def append_notepad(msg: str) -> None:
    NOTEPAD.parent.mkdir(parents=True, exist_ok=True)
    with NOTEPAD.open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-rows", type=int, default=1000)
    args = parser.parse_args(argv)

    run_export(args.mock_rows)
    df = load_data()
    # Ensure consistent target naming
    if "target_delay" not in df.columns and "delay_minutes" in df.columns:
        df = df.rename(columns={"delay_minutes": "target_delay"})

    corr = compute_correlations(df)
    imp = lgb_importance(df)
    selected, scores = combined_selection(corr, imp, top_k=18)

    save_figures(df, corr, imp)
    write_selected(selected, scores)

    append_notepad(f"[feature_analysis] selected {len(selected)} features: {selected}")

    print("Selected features:", selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
