"""Construct target variable dataset for delay prediction.

Loads a mock feature-store export produced by scripts/export_ml_dataset.py
and computes delay_target = actual_arrival - scheduled_arrival (minutes).

Writes:
 - data/processed/target_dataset.parquet
 - reports/figures/delay_distribution.png
 - reports/figures/delay_by_hour.png
 - .omo/evidence/task-14-target-stats.json
 - .omo/evidence/task-14-target-dist.png (copy of histogram)

Also appends a short note to .omo/notepads/aeropredict-gap-closure/learnings.md
for traceability.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns


EVIDENCE_DIR = Path(".omo/evidence")
FIG_DIR = Path("reports/figures")
NOTEPAD = Path(".omo/notepads/aeropredict-gap-closure/learnings.md")


def _run_export_mock(rows: int) -> Path:
    # Run the existing export script in mock mode and write to a temp path
    out_parquet = Path("data/processed/feature_store_for_target.parquet")
    cmd = [sys.executable, "scripts/export_ml_dataset.py", "--mock", "--mock-rows", str(rows),
           "--output", str(out_parquet), "--csv-output", "data/processed/feature_store_for_target.csv",
           "--metadata-output", "data/processed/feature_store_for_target_metadata.json"]
    print("Running export mock: ", " ".join(cmd))
    subprocess.check_call(cmd)
    return out_parquet


def _load_df(parquet_path: Path) -> pd.DataFrame:
    df = pd.read_parquet(parquet_path)
    return df


def _ensure_datetime(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col, errors="coerce")


def construct_target(df: pd.DataFrame) -> pd.DataFrame:
    # Work on a copy
    df = df.copy()

    # Normalize possible column names
    if "scheduled_arrival" not in df.columns:
        # try common alternatives
        for alt in ("scheduledArrival", "scheduled_arrival_utc"):
            if alt in df.columns:
                df["scheduled_arrival"] = df[alt]
                break

    # actual arrival may be present under several names; reconstruct if missing and target_delay exists
    actual_cols = [c for c in ("actual_arrival", "actual_arr", "arrival_time") if c in df.columns]
    if actual_cols:
        df["actual_arrival"] = df[actual_cols[0]]
    elif "target_delay" in df.columns:
        # reconstruct actual arrival from scheduled + target_delay
        df["scheduled_arrival_dt"] = _ensure_datetime(df["scheduled_arrival"])
        df["actual_arrival"] = df["scheduled_arrival_dt"] + pd.to_timedelta(df["target_delay"], unit="m")
        df.drop(columns=["scheduled_arrival_dt"], inplace=True)

    # Parse datetimes
    df["scheduled_arrival_dt"] = _ensure_datetime(df.get("scheduled_arrival"))
    df["actual_arrival_dt"] = _ensure_datetime(df.get("actual_arrival"))

    # Flag schedule missing when scheduled_arrival is NaT or schedule_source missing
    schedule_col = df.get("schedule_source")
    if schedule_col is not None:
        df["schedule_missing"] = df["scheduled_arrival_dt"].isna() | schedule_col.isna()
    else:
        df["schedule_missing"] = df["scheduled_arrival_dt"].isna()

    # Remove rows that lack actual_arrival (we cannot compute delay)
    before = len(df)
    df = df[~df["actual_arrival_dt"].isna()].copy()
    removed = before - len(df)

    # Compute delay in minutes (can be negative)
    df["delay_target"] = (df["actual_arrival_dt"] - df["scheduled_arrival_dt"]).dt.total_seconds() / 60.0

    # If scheduled_arrival was NaT, delay_target will be NaN; keep rows but they are flagged by schedule_missing
    # For these, we keep delay_target as NaN

    return df


def summarize_and_save(df: pd.DataFrame, out_parquet: Path) -> dict:
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_parquet, index=False)

    stats = {}
    delays = df["delay_target"].dropna()
    if len(delays) > 0:
        stats["count"] = int(len(delays))
        stats["mean"] = float(np.mean(delays))
        stats["std"] = float(np.std(delays, ddof=1))
        stats["min"] = float(np.min(delays))
        stats["max"] = float(np.max(delays))
        stats["percentiles"] = {
            "5": float(np.percentile(delays, 5)),
            "25": float(np.percentile(delays, 25)),
            "50": float(np.percentile(delays, 50)),
            "75": float(np.percentile(delays, 75)),
            "95": float(np.percentile(delays, 95)),
        }
    else:
        stats["count"] = 0

    return stats


def plot_distribution(df: pd.DataFrame) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    delays = df["delay_target"].dropna()
    plt.figure(figsize=(8, 5))
    sns.histplot(delays, bins=60, kde=True)
    plt.xlabel("Delay (minutes)")
    plt.title("Delay distribution")
    hist_path = FIG_DIR / "delay_distribution.png"
    plt.tight_layout()
    plt.savefig(hist_path)
    plt.close()

    # Copy to evidence as well
    (EVIDENCE_DIR / "task-14-target-dist.png").parent.mkdir(parents=True, exist_ok=True)
    plt_image = hist_path
    # save copy
    import shutil

    shutil.copy2(str(plt_image), str(EVIDENCE_DIR / "task-14-target-dist.png"))

    # Boxplot by hour_of_day if available
    hour_col = None
    for candidate in ("departure_hour", "scheduled_departure_hour", "hour_of_day"):
        if candidate in df.columns:
            hour_col = candidate
            break

    if hour_col is not None:
        plt.figure(figsize=(10, 6))
        sns.boxplot(x=hour_col, y="delay_target", data=df, showfliers=False)
        plt.xlabel("Hour of day")
        plt.ylabel("Delay (minutes)")
        plt.title("Delay by hour of day (boxplot)")
        bp_path = FIG_DIR / "delay_by_hour.png"
        plt.tight_layout()
        plt.savefig(bp_path)
        plt.close()


def append_notepad(stats: dict, removed: int) -> None:
    NOTEPAD.parent.mkdir(parents=True, exist_ok=True)
    with NOTEPAD.open("a", encoding="utf-8") as fh:
        fh.write(f"[{datetime.utcnow().isoformat()}] constructed target dataset. rows_removed_for_missing_actual={removed}. stats={json.dumps(stats)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-rows", type=int, default=1000)
    parser.add_argument("--output", default="data/processed/target_dataset.parquet")
    args = parser.parse_args(argv)

    try:
        parquet_src = _run_export_mock(args.mock_rows)
        df = _load_df(parquet_src)
        constructed = construct_target(df)
        out_parquet = Path(args.output)
        stats = summarize_and_save(constructed, out_parquet)

        # Save stats to evidence
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stats_path = EVIDENCE_DIR / "task-14-target-stats.json"
        stats_path.write_text(json.dumps(stats, indent=2))

        # Plot
        plot_distribution(constructed)

        append_notepad(stats, 0)

        print("Wrote target dataset:", out_parquet)
        print("Wrote stats:", stats_path)
        return 0
    except Exception as exc:
        print("Error constructing target:", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
