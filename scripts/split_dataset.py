"""Split feature-store dataset into train/val/test with temporal stratification.

Reads data/processed/target_dataset.parquet by default and writes
train.parquet, val.parquet and test.parquet to the --output-dir.

Temporal stratification: try to find a datetime column (several
common candidates). If found, sort ascending and split contiguous
blocks: first train, then val, then test. If no suitable datetime
column exists, falls back to random split using --seed.

Also writes dataset_splits.json summarising counts, ratios, temporal
column used, date ranges per split and target stats (mean/std).

If target file is missing and --mock is set, calls
scripts/export_ml_dataset.py --mock --mock-rows N to generate a
mock input before proceeding.

Supports --dry-run to log split stats without writing files.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("data/processed/target_dataset.parquet")
DEFAULT_OUTPUT_DIR = Path("data/processed")
EVIDENCE_DIR = Path(".omo/evidence")
NOTEPAD = Path(".omo/notepads/aeropredict-gap-closure/learnings.md")


def _run_export_mock(out_path: Path, rows: int) -> Path:
    cmd = [
        sys.executable,
        "scripts/export_ml_dataset.py",
        "--mock",
        "--mock-rows",
        str(rows),
        "--output",
        str(out_path),
    ]
    print("Running export mock:", " ".join(cmd))
    subprocess.check_call(cmd)
    return out_path


def _find_datetime_column(df: pd.DataFrame) -> Optional[str]:
    candidates = [
        # precise engineered columns first
        "scheduled_arrival_dt",
        "actual_arrival_dt",
        "scheduled_departure_dt",
        # common raw names
        "scheduled_arrival",
        "actual_arrival",
        "scheduled_departure",
        "departure_time",
        "arrival_time",
        "first_seen",
        "last_seen",
        "timestamp",
    ]

    for c in candidates:
        if c in df.columns:
            # attempt parse and check non-null proportion
            try:
                parsed = pd.to_datetime(df[c], errors="coerce")
            except Exception:
                continue
            non_null = parsed.notna().sum()
            if non_null >= 1:
                # prefer columns with at least one non-null value
                # replace column in df with parsed when used by caller
                return c
    # fallback: search any column with datetime-like dtype
    for c in df.columns:
        if np.issubdtype(df[c].dtype, np.datetime64):
            return c
    return None


def _compute_target_stats(series: pd.Series) -> dict:
    s = series.dropna()
    if len(s) == 0:
        return {"count": 0}
    return {
        "count": int(s.size),
        "mean": float(np.mean(s)),
        "std": float(np.std(s, ddof=1)) if s.size > 1 else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Generate mock input if missing")
    parser.add_argument("--mock-rows", type=int, default=1000)
    args = parser.parse_args(argv)

    input_path: Path = args.input
    out_dir: Path = args.output_dir
    val_ratio = args.val_ratio
    test_ratio = args.test_ratio
    seed = int(args.seed)

    print(f"Loading input: {input_path}")

    if not input_path.exists():
        if args.mock:
            print("Input not found; generating mock dataset...")
            input_path.parent.mkdir(parents=True, exist_ok=True)
            _run_export_mock(input_path, args.mock_rows)
        else:
            print(f"Input file {input_path} not found and --mock not set. Aborting.")
            return 2

    df = pd.read_parquet(input_path)
    print(f"Read {len(df)} rows from {input_path}")

    # Drop rows where target is NaN
    if "delay_target" not in df.columns:
        print("Expected target column 'delay_target' not present in input. Aborting.")
        return 2

    before = len(df)
    df = df[~df["delay_target"].isna()].copy()
    dropped = before - len(df)
    print(f"Dropped {dropped} rows with NaN delay_target. Remaining {len(df)}")

    if len(df) == 0:
        print("No rows left after dropping NaN target. Aborting.")
        return 2

    # find datetime column
    dt_col = _find_datetime_column(df)
    used_temporal = None

    n = len(df)
    train_ratio = 1.0 - val_ratio - test_ratio
    if train_ratio <= 0:
        print("Invalid ratios: train ratio <= 0. Adjust --val-ratio and --test-ratio.")
        return 2

    if dt_col is not None:
        print(f"Using temporal column candidate: {dt_col}")
        # parse it into a new column name for safety
        parsed = pd.to_datetime(df[dt_col], errors="coerce")
        if parsed.notna().sum() >= 1:
            # store parsed under a working name
            df["__temporal_dt"] = parsed
            # rows with NaT will be placed at the end
            df = df.sort_values("__temporal_dt", na_position="last").reset_index(drop=True)
            used_temporal = dt_col
            print(f"Temporal column parsed. Non-null temporal rows: {df['__temporal_dt'].notna().sum()}")
        else:
            print(f"Temporal candidate {dt_col} parsed to all NaT; falling back to random split.")
            dt_col = None

    if dt_col is None:
        # random split
        print("No usable temporal column found; performing random split with seed", seed)
        rng = np.random.RandomState(seed)
        perm = rng.permutation(n)
        df = df.reset_index(drop=True).iloc[perm].reset_index(drop=True)

    # compute counts
    n_total = len(df)
    n_train = int(n_total * train_ratio)
    n_val = int(n_total * val_ratio)
    n_test = n_total - n_train - n_val

    # Assign splits
    train_df = df.iloc[:n_train].copy()
    val_df = df.iloc[n_train : n_train + n_val].copy()
    test_df = df.iloc[n_train + n_val :].copy()

    # Prepare split summary
    def date_range_for(subdf: pd.DataFrame) -> Optional[tuple[str, str]]:
        if used_temporal is None and "__temporal_dt" not in subdf.columns:
            return None
        col = "__temporal_dt"
        if col not in subdf.columns:
            return None
        s = subdf[col].dropna()
        if s.empty:
            return None
        return (s.min().isoformat(), s.max().isoformat())

    splits = {
        "counts": {"train": len(train_df), "val": len(val_df), "test": len(test_df)},
        "ratios": {"train": train_ratio, "val": val_ratio, "test": test_ratio},
        "temporal_column": used_temporal,
        "date_ranges": {
            "train": date_range_for(train_df),
            "val": date_range_for(val_df),
            "test": date_range_for(test_df),
        },
        "target_stats": {
            "train": _compute_target_stats(train_df["delay_target"]),
            "val": _compute_target_stats(val_df["delay_target"]),
            "test": _compute_target_stats(test_df["delay_target"]),
        },
    }

    # Print concise summary
    overall_mean = float(np.mean(df["delay_target"]))
    print(
        f"Train: {len(train_df)} rows, Val: {len(val_df)} rows, Test: {len(test_df)} rows | Target mean: {overall_mean:.3f} min"
    )

    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "dataset_splits.json"
    evidence_path = EVIDENCE_DIR / "task-15-splits.json"
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print("Dry-run: not writing parquet files. Writing summary JSON only.")
        json_path.write_text(json.dumps(splits, indent=2, default=str))
        evidence_path.write_text(json.dumps({"dry_run": True, "summary": splits}, indent=2, default=str))
    else:
        # write parquet files
        train_df.to_parquet(out_dir / "train.parquet", index=False)
        val_df.to_parquet(out_dir / "val.parquet", index=False)
        test_df.to_parquet(out_dir / "test.parquet", index=False)
        json_path.write_text(json.dumps(splits, indent=2, default=str))
        evidence_path.write_text(json.dumps({"created_at": datetime.utcnow().isoformat(), "summary": splits}, indent=2, default=str))
        print("Wrote:", out_dir / "train.parquet", out_dir / "val.parquet", out_dir / "test.parquet")
        print("Wrote summary:", json_path)

    # Append short note to notepad for traceability
    NOTEPAD.parent.mkdir(parents=True, exist_ok=True)
    with NOTEPAD.open("a", encoding="utf-8") as fh:
        fh.write(
            f"[{datetime.utcnow().isoformat()}] split_dataset created splits: train={len(train_df)}, val={len(val_df)}, test={len(test_df)}, temporal_column={used_temporal}\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
