# Model Card — Delay Predictor (delay-predictor)

English summary
----------------

Model name: delay-predictor
Version: 1.0.0
Algorithm: LightGBM (gradient-boosted decision trees)
Task: Regression — predict flight arrival delay in minutes (continuous)

This model predicts arrival delay (minutes) for flights covering Peninsular Spain airports. It was trained as part of an academic TFM project and uses a LightGBM regressor trained on synthetic/mock data derived from OpenSky samples and engineered features representing schedule, traffic density and basic environmental observations.

Resumen en Español
-------------------

Nombre del modelo: delay-predictor
Versión: 1.0.0
Algoritmo: LightGBM (árboles potenciados por gradiente)
Objetivo: regresión — predecir retraso de llegada en minutos (valor continuo)

1. Model Overview / Resumen del Modelo
-------------------------------------

What it predicts: the model outputs a numerical estimate of arrival delay in minutes for a scheduled flight. The prediction target 'delay_target' is computed as actual_arrival - scheduled_arrival in minutes.

Use case: academic research (TFM) and exploratory analytics; not intended for operational decision making without human-in-the-loop verification. The model helps understand patterns associated with delays and can be used for scenario analysis.

2. Performance / Rendimiento
----------------------------

Reported metrics (best available run):

- Train RMSE: 0.9515
- Val RMSE: 1.2042
- Test RMSE: 1.0989
- Test MAE: 0.4131
- Test R²: 0.9947

Note: HPO produced higher CV RMSEs in the Optuna study (best CV RMSE ~1.6433) but the baseline retrained model achieved test RMSE ~1.10 when trained on train+val as implemented in the pipeline. These results are from synthetic/mock datasets and will likely differ on real operational data.

Best hyperparameters (from models/best_params.json):

```
{
  "n_estimators": 500,
  "max_depth": 5,
  "learning_rate": 0.05,
  "num_leaves": 31,
  "subsample": 1.0,
  "colsample_bytree": 1.0
}
```

3. Features / Características
-----------------------------

The model uses 23 features (examples below; the trained LightGBM text model contains the full list):

- delay_minutes (numeric)
- airborne_minutes (numeric)
- departure_hour (categorical/numeric)
- day_of_week (categorical)
- month (categorical)
- aircraft_type (categorical)
- aircraft_manufacturer (categorical)
- aircraft_operator (categorical)
- aircraft_age_years (numeric)
- route_daily_traffic (numeric)
- route_total_density (numeric)
- departure_airport_hourly_traffic (numeric)
- arrival_airport_hourly_traffic (numeric)
- dep_temperature (numeric)
- dep_precipitation (numeric)
- dep_wind_speed (numeric)
- dep_visibility (numeric)
- arr_temperature (numeric)
- arr_precipitation (numeric)
- arr_wind_speed (numeric)
- arr_visibility (numeric)
- previous_flight_delay (numeric)
- target_delay (numeric)  (note: included in diagnostics, not used as input at inference)

Feature engineering notes / Notas de ingeniería de características:
- Temporal stratification: dataset split is temporal (train/val/test = 60/20/20) by scheduled arrival time to avoid leakage.
- Categorical encoding: LightGBM's native handling of categorical features was used where appropriate; categorical columns were left as integer codes where pipeline permitted.
- Missing values: numerical missing values are filled with sentinel -1 when aligning columns across splits; categorical missing treated as a separate category.

Importance ranking: top features logged in MLflow per-trial; the LightGBM human-readable dump (models/best_model.txt) contains split gains and thresholds. Users should consult that file for exact importance ordering.

4. Data / Datos de Entrenamiento
--------------------------------

- Dataset size: ~964 rows (reported in pipeline logs and notepad)
- Split: temporal 60% train, 20% validation, 20% test (per scripts/split_dataset.py)
- Date range: synthetic/mock snapshots derived from Bronze samples; see pipeline docs for details
- Source: synthetic/mock data based on real OpenSky Bronze samples stored in data/mock/ when running locally

Limitations: The model is trained on synthetic/mocked data with limited coverage of real-world conditions (964 rows). Performance on real operational datasets is likely to be different; treat predictions as exploratory.

5. Limitations / Limitaciones
-----------------------------

- Synthetic data — the model may not generalize to production data.
- Small dataset (964 rows) — risk of overfitting and poor generalization.
- External factors like comprehensive weather modeling, ATC restrictions, strikes, or airport operational incidents are not explicitly modeled.
- Geographic scope: Peninsular Spain airports only.

6. Usage / Uso
---------------

Loading the model via MLflow (Python):

from mlflow import pyfunc
mdl = pyfunc.load_model("models:/delay-predictor/1")
# or by stage
mdl = pyfunc.load_model("models:/delay-predictor/Production")

Input: a feature vector (DataFrame or dict) matching the pipeline-prepared features (32 columns in the full pipeline); columns must be aligned and missing numeric values set to -1 for safety.

Output: a scalar or array of predicted delay in minutes (float) per input row.

7. Ethics / Ética
------------------

This model is intended for academic research (TFM) and analytics. It must not be used for operational decisions affecting passengers (compensation, rerouting) without human verification and rigorous validation on production data. Predictions can be biased by synthetic data and limited feature coverage.

8. Maintenance / Mantenimiento
------------------------------

- Retraining cadence: weekly retrain recommended once real data accumulation reaches sufficient volume.
- CI/CD: include performance monitoring and alerts if test/validation RMSE degrades by >10%.
- Versioning: use MLflow Model Registry for version history and stage transitions.

Contact / Contacto
-------------------
TFM project maintainers — see repository README for authorship and contact details.
