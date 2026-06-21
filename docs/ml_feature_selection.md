# ML Feature Selection — Delay Prediction

This document describes the methodology and the top features selected for the delay prediction task.

## Methodology

- Generate mock dataset using scripts/export_ml_dataset.py --mock --mock-rows 1000
- Compute absolute Pearson correlation between numeric features and the target (`target_delay`).
- Train a quick LightGBM regressor (n_estimators=50) to obtain tree-based feature importances.
- Combine normalized correlation and importance scores (weighted: 0.6 corr, 0.4 importance) to rank features.
- Apply domain knowledge to avoid leaking features (e.g., target-derived fields) and ensure operational availability.

Figures (produced by scripts/feature_analysis.py):

- reports/figures/task-13-correlation.png — correlation heatmap (top 20 numeric)
- reports/figures/task-13-importance.png — LightGBM importance bar chart

## Top 18 selected features (one-line justification each)

1. previous_flight_delay — recent delay likely to propagate
2. departure_airport_hourly_traffic — congestion at departure time
3. arrival_airport_hourly_traffic — congestion on arrival
4. route_daily_traffic — route-level congestion signal
5. route_total_density — historical popularity of route
6. airborne_minutes — longer flights may have different delay patterns
7. departure_hour — hour-of-day effect
8. day_of_week — weekday/weekend patterns
9. month — seasonal effects
10. dep_wind_speed — weather impact on departures
11. arr_wind_speed — weather impact on arrivals
12. dep_precipitation — precipitation can cause delays
13. arr_precipitation — precipitation on arrival can cause delays
14. dep_visibility — low visibility affects operations
15. arr_visibility — arrival visibility
16. aircraft_age_years — older aircraft may have reliability differences
17. aircraft_operator — operator-level operational performance
18. schedule_source — source may encode schedule quality / reliability

## Excluded features (examples and reasons)

- delay_minutes / target_delay (the prediction target) — would leak the label.
- created_at, scheduled_departure, scheduled_arrival — contain timestamps that can leak target if used naively; prefer engineered features instead.
- callsign, icao24 — high-cardinality identifiers; use operator or aggregated features instead.

## Notes

- The selection is reproducible via scripts/feature_analysis.py which produces `data/processed/selected_features.json`.
- The combined ranking uses a simple weighted linear combination; for production selection, cross-validated selection and stability analysis should be added.
