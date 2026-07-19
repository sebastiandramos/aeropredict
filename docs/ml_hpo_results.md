
# HPO Results - LightGBM delay predictor

This document summarizes the hyperparameter optimization run performed with Optuna and logged using MLflow.

Best hyperparameters (summary):

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

Test set performance:

```
{
  "test": {
    "rmse": 1.0306273498845666,
    "mae": 0.30483526927434224,
    "r2": 0.994769592743941
  }
}
```

Top features (logged in MLflow) and feature count available in the run artifacts.

Training duration: 1.2s

Limitations: tuned on small dataset; final model trained on train+val and evaluated on test only. Do not use test to tune.
