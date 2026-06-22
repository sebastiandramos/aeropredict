#!/usr/bin/env python3
"""Evaluate archived predictions against actual arrivals.

Joins gold.predictions with gold.flights to compute MAE, RMSE, and R²
metrics.  Writes the evaluation report to data/reports/evaluation_latest.json.

Usage:
    python scripts/evaluate_predictions.py
    python scripts/evaluate_predictions.py --days 7
    POSTGRES_URI=postgresql://... python scripts/evaluate_predictions.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psycopg2

POSTGRES_URI = os.environ.get(
    "POSTGRES_URI",
    "postgresql://aeropredict:aeropredict@localhost:5432/aeropredict",
)

REPORT_DIR = Path("data/reports")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)


def _ensure_report_dir() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def evaluate(days: int = 30) -> dict:
    """Query predictions joined with actuals, compute metrics, return report dict."""
    conn = psycopg2.connect(POSTGRES_URI)
    try:
        with conn.cursor() as cur:
            # Join predictions with actual flight data
            cur.execute(
                """
                SELECT
                    p.request_id,
                    p.model_version,
                    p.predicted_delay_minutes,
                    p.predicted_eta,
                    p.timestamp AS prediction_timestamp,
                    f.actual_arrival,
                    p.flight_features
                FROM gold.predictions p
                LEFT JOIN gold.flights f
                    ON p.flight_features->>'icao24' = f.icao24
                    AND (p.flight_features->>'flight_date' = f.flight_date::text
                         OR p.flight_features->>'flight_date' IS NULL)
                WHERE p.timestamp >= NOW() - INTERVAL '%s days'
                  AND f.actual_arrival IS NOT NULL
                ORDER BY p.timestamp DESC
                """,
                (days,),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

        if not rows:
            LOGGER.warning(
                "No predictions with actual arrivals found in the last %d days", days
            )
            return {
                "evaluation_timestamp": datetime.now(UTC).isoformat(),
                "days_lookback": days,
                "sample_count": 0,
                "mae_minutes": None,
                "rmse_minutes": None,
                "r_squared": None,
                "status": "no_data",
                "sample_predictions": [],
            }

        # Compute residuals: predicted_delay vs (actual_arrival - prediction_time approximated)
        predicted_delays = []
        actual_delays = []
        samples = []

        for row in rows:
            row_dict = dict(zip(columns, row, strict=False))
            pred_delay = row_dict["predicted_delay_minutes"]
            actual_arrival = row_dict["actual_arrival"]
            row_dict["prediction_timestamp"]

            if pred_delay is None or actual_arrival is None:
                continue

            # Approximate actual delay: actual_arrival - (predicted_eta - predicted_delay)
            # Since we don't have scheduled_arrival directly, we approximate:
            # scheduled ≈ actual_arrival - actual_delay
            # We use predicted_eta - predicted_delay as approximate scheduled time
            predicted_eta = row_dict.get("predicted_eta")
            if predicted_eta and pred_delay:
                # scheduled ≈ predicted_eta - predicted_delay
                from datetime import timedelta as td

                if isinstance(predicted_eta, str):
                    from dateutil.parser import isoparse

                    predicted_eta = isoparse(predicted_eta)
                approx_scheduled = predicted_eta - td(minutes=pred_delay)
                actual_delay_min = (actual_arrival - approx_scheduled).total_seconds() / 60.0
            else:
                # Fallback: we can't compute actual delay without scheduled_arrival
                continue

            predicted_delays.append(pred_delay)
            actual_delays.append(actual_delay_min)

            if len(samples) < 10:
                samples.append(
                    {
                        "request_id": str(row_dict["request_id"]),
                        "predicted_delay_minutes": round(pred_delay, 2),
                        "actual_delay_minutes": round(actual_delay_min, 2),
                        "error_minutes": round(pred_delay - actual_delay_min, 2),
                        "prediction_timestamp": str(row_dict["prediction_timestamp"]),
                    }
                )

        if not predicted_delays:
            LOGGER.warning("No valid prediction-actual pairs found")
            return {
                "evaluation_timestamp": datetime.now(UTC).isoformat(),
                "days_lookback": days,
                "sample_count": 0,
                "mae_minutes": None,
                "rmse_minutes": None,
                "r_squared": None,
                "status": "no_valid_pairs",
                "sample_predictions": [],
            }

        pred_arr = np.array(predicted_delays)
        actual_arr = np.array(actual_delays)
        errors = pred_arr - actual_arr

        mae = float(np.mean(np.abs(errors)))
        rmse = float(np.sqrt(np.mean(errors**2)))

        # R² (coefficient of determination)
        ss_res = float(np.sum(errors**2))
        ss_tot = float(np.sum((actual_arr - np.mean(actual_arr)) ** 2))
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        report = {
            "evaluation_timestamp": datetime.now(UTC).isoformat(),
            "days_lookback": days,
            "sample_count": len(predicted_delays),
            "mae_minutes": round(mae, 4),
            "rmse_minutes": round(rmse, 4),
            "r_squared": round(r_squared, 4),
            "status": "ok",
            "mean_predicted_delay": round(float(np.mean(pred_arr)), 4),
            "mean_actual_delay": round(float(np.mean(actual_arr)), 4),
            "sample_predictions": samples,
        }

        LOGGER.info(
            "Evaluation complete: %d samples, MAE=%.2f min, RMSE=%.2f min, R²=%.4f",
            len(predicted_delays),
            mae,
            rmse,
            r_squared,
        )
        return report
    finally:
        conn.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate prediction accuracy")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback window in days (default: 30)",
    )
    args = parser.parse_args()

    _ensure_report_dir()
    report = evaluate(days=args.days)

    # Write latest report
    report_path = REPORT_DIR / "evaluation_latest.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    LOGGER.info("Report written to %s", report_path)

    # Also write timestamped copy
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    dated_path = REPORT_DIR / f"evaluation_{ts}.json"
    with open(dated_path, "w") as f:
        json.dump(report, f, indent=2)
    LOGGER.info("Timestamped report written to %s", dated_path)

    # Print summary
    print(f"\n{'='*60}")
    print("Prediction Evaluation Report")
    print(f"{'='*60}")
    print(f"Timestamp:       {report['evaluation_timestamp']}")
    print(f"Lookback:        {report['days_lookback']} days")
    print(f"Samples:         {report['sample_count']}")
    print(f"MAE:             {report['mae_minutes']} minutes")
    print(f"RMSE:            {report['rmse_minutes']} minutes")
    print(f"R²:              {report['r_squared']}")
    print(f"Status:          {report['status']}")
    print(f"{'='*60}\n")

    if report["status"] != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
