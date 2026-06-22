"""Register best LightGBM model to MLflow Model Registry.

This script locates the best HPO trial (by CV RMSE) from a local MLflow tracking
folder (./mlruns) or falls back to artifacts under models/ and registers the model
as `delay-predictor`. It tags the registered model and model version with useful
metadata and transitions the version to the requested stage.

Usage (dry-run prints actions):

python scripts/register_model.py \
  --mlruns-dir ./mlruns \
  --model-name delay-predictor \
  --version 1.0.0 \
  --stage Production \
  --dry-run

The script is defensive: if MLflow tracking data is not available it will still
attempt to register the local model artifact under models/best_model.txt.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("register_model")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_feature_list_from_model_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("feature_names="):
            # line like: feature_names=a b c
            rhs = line.split("=", 1)[1]
            # features are space-separated in LightGBM text dump
            return [f.strip() for f in rhs.split() if f.strip()]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mlruns-dir", type=Path, default=Path("./mlruns"))
    parser.add_argument("--model-name", type=str, default="delay-predictor")
    parser.add_argument("--version", type=str, default="1.0.0")
    parser.add_argument("--stage", type=str, default="Production")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    mlruns_dir: Path = args.mlruns_dir
    model_name = args.model_name
    version_tag = args.version
    stage = args.stage
    dry_run = args.dry_run

    # Helpful local artifacts
    models_dir = Path("models")
    best_params = _read_json(models_dir / "best_params.json") or {}
    hpo_results = _read_json(models_dir / "hpo_results.json") or []
    baseline = _read_json(models_dir / "baseline_metrics.json") or {}
    best_model_file = models_dir / "best_model.txt"
    features = _parse_feature_list_from_model_file(best_model_file)

    # MLflow lazy import
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.error("mlflow is required to run this script: %s", exc)
        return 2

    # Ensure file store env set for local registry usage
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri(f"file:{mlruns_dir}")
    client = MlflowClient(tracking_uri=mlflow.get_tracking_uri())

    # Find best run from mlruns (prefer tracked runs). We'll search experiments.
    best_run_id: Optional[str] = None
    best_cv_rmse = float("inf")
    try:
        # search across all experiments
        runs = client.search_runs(experiment_ids=None, filter_string="", run_view_type=1)
        for r in runs:
            metrics = r.data.metrics
            if "cv_rmse_mean" in metrics:
                val = float(metrics["cv_rmse_mean"])
                if val < best_cv_rmse:
                    best_cv_rmse = val
                    best_run_id = r.info.run_id
    except Exception:
        # search_runs may fail if no mlruns present
        best_run_id = None

    # Fallback: use models/hpo_results.json
    if best_run_id is None and hpo_results:
        # hpo_results is list of dicts sorted? pick min cv_rmse_mean
        try:
            best_entry = min(hpo_results, key=lambda x: float(x.get("cv_rmse_mean", float("inf"))))
            best_cv_rmse = float(best_entry.get("cv_rmse_mean", float("nan")))
            # no run id available from file; we'll register from local artifact
            best_run_id = None
        except Exception:
            best_entry = None
    else:
        best_entry = None

    # Prepare tags
    training_date = datetime.utcnow().isoformat()
    features_count = len(features)

    # Prepare evidence log
    evidence_dir = Path(".omo/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []

    if dry_run:
        logger.info("DRY-RUN: Would register model '%s' version_tag=%s stage=%s", model_name, version_tag, stage)
        logger.info("Found best cv_rmse=%.4f (run_id=%s)", best_cv_rmse, best_run_id)
        logger.info("Features_count=%d, best_params=%s", features_count, best_params)
        (evidence_dir / "task-18-mlflow-registry.log").write_text("DRY-RUN: registration skipped\n")
        # also write model card copy later
        pass
    else:
        try:
            # Ensure registered model exists
            try:
                client.get_registered_model(model_name)
                log_lines.append(f"Registered model '{model_name}' already exists")
            except Exception:
                client.create_registered_model(model_name)
                log_lines.append(f"Created registered model '{model_name}'")

            # Determine source for creating a model version
            if best_run_id:
                source = f"runs:/{best_run_id}/model"
                mv = client.create_model_version(name=model_name, source=source, run_id=best_run_id)
                created_version = mv.version
                log_lines.append(f"Created model version {created_version} from run {best_run_id}")
            else:
                # Fallback: use mlflow.lightgbm to log model from local file
                if best_model_file.exists():
                    import mlflow.lightgbm as lgb_mlflow
                    import lightgbm as lgb

                    booster = lgb.Booster(model_file=str(best_model_file.resolve()))
                    with mlflow.start_run():
                        lgb_mlflow.log_model(booster, artifact_path="model")
                        run_id = mlflow.active_run().info.run_id
                    source = f"runs:/{run_id}/model"
                    mv = client.create_model_version(name=model_name, source=source, run_id=run_id)
                    created_version = mv.version
                    log_lines.append(f"Created model version {created_version} from local file {best_model_file}")
                else:
                    raise RuntimeError("No run found and models/best_model.txt not present; cannot register model")

            # Set registered model tags
            client.set_registered_model_tag(model_name, "algorithm", "lightgbm")
            client.set_registered_model_tag(model_name, "target", "delay_target")
            client.set_registered_model_tag(model_name, "training_date", training_date)
            client.set_registered_model_tag(model_name, "features_count", str(features_count))
            client.set_registered_model_tag(model_name, "version_tag", version_tag)
            log_lines.append(f"Set registered model tags for {model_name}")

            # Set model version tags
            client.set_model_version_tag(model_name, created_version, "stage", stage)
            if not (best_cv_rmse is None or best_cv_rmse == float("inf")):
                client.set_model_version_tag(model_name, created_version, "cv_rmse", f"{best_cv_rmse:.4f}")
                log_lines.append(f"Tagged version {created_version} cv_rmse={best_cv_rmse:.4f}")
            # If baseline/test metrics available, tag them
            try:
                test_rmse = baseline.get("metrics", {}).get("test", {}).get("rmse")
                if test_rmse is not None:
                    client.set_model_version_tag(model_name, created_version, "test_rmse", f"{float(test_rmse):.4f}")
                    log_lines.append(f"Tagged version {created_version} test_rmse={test_rmse}")
            except Exception:
                pass

            # Transition stage to Production
            client.transition_model_version_stage(name=model_name, version=created_version, stage=stage, archive_existing_versions=False)
            log_lines.append(f"Transitioned model {model_name} version {created_version} to stage {stage}")

            # Try to load the model via mlflow.pyfunc.load_model for verification
            try:
                loaded = mlflow.pyfunc.load_model(f"models:/{model_name}/{created_version}")
                log_lines.append(f"Successfully loaded models:/{model_name}/{created_version}")
            except Exception as exc:
                log_lines.append(f"Warning: failed to load models:/{model_name}/{created_version}: {exc}")

            # Also load by stage name
            try:
                loaded = mlflow.pyfunc.load_model(f"models:/{model_name}/{stage}")
                log_lines.append(f"Successfully loaded models:/{model_name}/{stage}")
            except Exception as exc:
                log_lines.append(f"Warning: failed to load models:/{model_name}/{stage}: {exc}")

        except Exception as exc:  # pragma: no cover - operational
            log_lines.append(f"ERROR during registration: {exc}")

        (evidence_dir / "task-18-mlflow-registry.log").write_text("\n".join(log_lines), encoding="utf-8")

    # Write a minimal model card copy into evidence for traceability
    model_card_path = Path("docs") / "model_card_delay_predictor.md"
    if model_card_path.exists():
        (evidence_dir / "task-18-model-card.md").write_text(model_card_path.read_text(encoding="utf-8"), encoding="utf-8")

    # Append to learnings notepad
    notepad = Path(".omo/notepads/aeropredict-gap-closure/learnings.md")
    notepad.parent.mkdir(parents=True, exist_ok=True)
    with notepad.open("a", encoding="utf-8") as fh:
        fh.write(f"[{datetime.utcnow().isoformat()}] register_model: attempted registration of {model_name} version_tag={version_tag} stage={stage}\n")

    logger.info("Wrote evidence to %s", evidence_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
