"""Fit the first two mobility baselines on four fixed tests and log local MLflow runs."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import pickle
import sys
import tempfile
import time

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modeling.baseline_evaluation import (
    evaluate_predictions, paired_mae_bootstrap, per_road_metrics,
    subgroup_metrics, write_markdown_report, plot_comparison,
)
from modeling.baseline_tracking import log_run
from modeling.mobility_baselines import GlobalProfile, RoadScaledProfile, calibration_folds


SCENARIOS = ["within_road", "unseen_roads", "time", "season_transfer"]
KEYS = ["segment_id", "direction", "road_type", "hour_start_utc"]
N_FOLDS = 3
NOTES = [
    "These are four independent benchmark experiments. No test data were used to choose parameters.",
    "B1 uses road-balanced empirical q10/q50/q90 by weekday/hour, with hour and city fallbacks.",
    "B2 uses log1p volume: borough/lane-group profile shrunk toward B1, a road level, "
    "and an observed-hour shape pooled across weekdays.",
    "Fixed priors: group20 road/direction units, road level2 observed dates, "
    "hourly shape3 observed dates. These values were not tuned against test scores.",
    "B2 q10/q50/q90 use signed log-residual quantiles from three grouped out-of-fold "
    "training fits. Residuals are fitted by hour with shrinkage toward pooled residual quantiles.",
    "Known-road calibration holds out whole segment/date groups while retaining road history; "
    "cold-start calibration withholds whole segments.",
    "Empirical 80% prediction intervals have no formal coverage guarantee. "
    "OOF fits use less data than final fits; calibration may not transfer across years or seasons.",
    "Time-test training ends in2018; test starts in2019 and includes later COVID-era changes.",
    "Season transfer uses17 road/direction/year pairs (15 segments), full autumn versus "
    "same-year full summer days. It is a small retrospective sensitivity test, not a future forecast.",
    "All road_type values are unspecified. The first two baselines do not use source, "
    "holiday or year as predictors; those remain available for the boosted baseline.",
    "Subgroup metrics recompute equal-road weights within each subgroup; do not average subgroup scores.",
]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, content):
    Path(path).write_text(json.dumps(content, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_inputs():
    clean = ROOT / "data/clean"
    model_path, split_path = clean / "model_table_v1.parquet", clean / "model_splits_v1.parquet"
    model_snapshot = clean / "model_table_v1_snapshot.json"
    split_snapshot = clean / "model_splits_v1_snapshot.json"
    m = json.loads(model_snapshot.read_text(encoding="utf-8"))
    s = json.loads(split_snapshot.read_text(encoding="utf-8"))
    require(sha256(model_path) == m["output"]["sha256"] == s["input"]["sha256"], "Model input hash changed.")
    require(sha256(split_path) == s["output"]["sha256"], "Split file hash changed.")
    columns = KEYS + ["volume", "sd", "date", "hour_start", "hour", "dow", "year", "season",
                      "borough", "number_travel_lanes", "source", "covid"]
    data = pd.read_parquet(model_path, columns=columns)
    splits = pd.read_parquet(split_path)
    require(len(data) == len(splits) == m["rows"], "Input row count mismatch.")
    pd.testing.assert_frame_equal(data[KEYS], splits[KEYS])
    require(np.array_equal(splits.model_row, np.arange(len(data))), "Split row offsets changed.")
    return data, splits, {"model": m, "splits": s}, [model_snapshot, split_snapshot]


def fit_scenario(train, scenario, seed):
    folds = calibration_folds(train, scenario, n_folds=N_FOLDS, seed=seed)
    oof = np.full(len(train), np.nan)
    fold_counts = []
    for fold in range(N_FOLDS):
        fit_mask, held_mask = folds != fold, folds == fold
        fitted = RoadScaledProfile.fit(train.loc[fit_mask])
        oof[held_mask] = fitted.point_log(train.loc[held_mask])
        fold_counts.append({"fold": fold, "fit_rows": int(fit_mask.sum()), "held_rows": int(held_mask.sum()),
                            "fit_roads": int(train.loc[fit_mask].sd.nunique()),
                            "held_roads": int(train.loc[held_mask].sd.nunique())})
        print(f"  {scenario}: calibration fold {fold + 1}/{N_FOLDS} complete", flush=True)
    require(np.isfinite(oof).all(), "Incomplete out-of-fold predictions.")
    model = RoadScaledProfile.fit(train)
    model.calibrate(train, oof)
    return model, folds, oof, fold_counts


def run(args):
    destination = args.output.resolve()
    require(not destination.exists(), f"Refusing to overwrite {destination}. Choose a new --output version.")
    require(args.bootstrap >= 2, "At least two bootstrap repetitions are needed.")
    started = time.perf_counter()
    data, splits, parents, parent_paths = load_inputs()
    print(f"Verified frozen inputs: {len(data):,} observations; four fixed evaluation scenarios.", flush=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".baseline-build-", dir=destination.parent) as temp:
        staging = Path(temp)
        metrics, subgroups, comparisons, records = [], [], [], []
        for scenario in SCENARIOS:
            print(f"Fitting {scenario}", flush=True)
            train_mask = splits["split_" + scenario].eq("train").to_numpy()
            test_mask = splits["split_" + scenario].eq("test").to_numpy()
            train, test = data.loc[train_mask].copy(), data.loc[test_mask].copy()
            test_weights = splits.loc[test_mask, "w_" + scenario].to_numpy()
            expected_train, expected_test = parents["splits"]["summaries"][scenario]["train"], parents["splits"]["summaries"][scenario]["test"]
            require(len(train) == expected_train["rows"] and len(test) == expected_test["rows"], "Scenario count drift.")
            train_days = train.groupby("sd", observed=True).date.nunique()
            test["n_training_days"] = test.sd.astype("string").map(train_days).fillna(0).astype("int32")
            scenario_dir = staging / scenario
            scenario_dir.mkdir()
            b2, folds, oof, fold_counts = fit_scenario(train, scenario, args.seed)
            calibration_path = scenario_dir / "training_oof_calibration.parquet"
            pd.DataFrame({"model_row": train.index.to_numpy(), "fold": folds,
                          "observed_volume": train.volume.to_numpy(), "base_log_prediction": oof,
                          "log_residual": np.log1p(train.volume.to_numpy()) - oof}).to_parquet(calibration_path, index=False)
            models = {"B1_global": b2.global_profile, "B2_road_scaled": b2}
            scenario_predictions = {}
            for model_name, model in models.items():
                model_dir = scenario_dir / model_name
                artifacts = model.save(model_dir)
                prediction = model.predict(test)
                # Reopening each saved model confirms these are usable fitted artifacts.
                with (model_dir / "model.pkl").open("rb") as stream:
                    reloaded = pickle.load(stream)
                pd.testing.assert_frame_equal(prediction.iloc[:200].reset_index(drop=True),
                                              reloaded.predict(test.iloc[:200]), check_exact=True)
                score = evaluate_predictions(test, prediction, test_weights)
                metrics.append({"scenario": scenario, "baseline": model_name, **score})
                details = subgroup_metrics(test, prediction, group_columns=(
                    "borough", "source", "road_type", "training_days_bucket", "covid"))
                details.insert(0, "baseline", model_name)
                details.insert(0, "scenario", scenario)
                subgroups.append(details)
                per_road = per_road_metrics(test, prediction, test_weights)
                per_road.to_parquet(model_dir / "per_road_metrics.parquet", index=False)
                details.to_csv(model_dir / "subgroup_metrics.csv", index=False)
                prediction_output = test[KEYS + ["sd", "date", "hour", "dow", "volume", "source", "borough", "n_training_days"]].reset_index(drop=True)
                prediction_output.insert(0, "model_row", test.index.to_numpy())
                for col in prediction.columns:
                    prediction_output[col] = prediction[col].to_numpy()
                prediction_output.to_parquet(model_dir / "test_predictions.parquet", index=False, compression="zstd")
                write_json(model_dir / "metrics.json", score)
                params = {
                    "model": model_name, "scenario": scenario, "seed": args.seed,
                    "train_rows": len(train), "test_rows": len(test),
                    "weighting": "equal_segment_direction_within_partition",
                    "model_table_sha256": parents["model"]["output"]["sha256"],
                    "split_sha256": parents["splits"]["output"]["sha256"],
                    "target_unit": "vehicles_per_hour", "fit_scale": "log1p",
                    "quantiles": [0.1, 0.5, 0.9],
                    "group_prior_roads": 20 if model_name == "B2_road_scaled" else "not_applicable",
                    "level_prior_days": 2 if model_name == "B2_road_scaled" else "not_applicable",
                    "shape_prior_days": 3 if model_name == "B2_road_scaled" else "not_applicable",
                    "interval_method": "weighted_empirical_slot_quantiles" if model_name == "B1_global" else "grouped_oof_log_residual_quantiles",
                    "calibration_folds": N_FOLDS if model_name == "B2_road_scaled" else 0,
                    "calibration_group": "whole_segment" if scenario == "unseen_roads" else "segment_date",
                }
                write_json(model_dir / "run_parameters.json", params)
                artifacts += [model_dir / name for name in ["per_road_metrics.parquet", "subgroup_metrics.csv", "test_predictions.parquet", "metrics.json", "run_parameters.json"]]
                if model_name == "B2_road_scaled":
                    artifacts.append(calibration_path)
                records.append({"scenario": scenario, "model_name": model_name, "params": params,
                                "metrics": score, "artifacts": artifacts, "fold_counts": fold_counts})
                scenario_predictions[model_name] = prediction
                print(f"  {model_name}: MAE={score['mae']:.2f} vehicles/hour; "
                      f"80% interval coverage={score['coverage_80']:.1%}", flush=True)
            comparison = paired_mae_bootstrap(test, scenario_predictions["B1_global"],
                                              scenario_predictions["B2_road_scaled"],
                                              n_bootstrap=args.bootstrap, seed=args.seed, weights=test_weights)
            comparison["scenario"] = scenario
            comparison["mae_reduction_percent"] = 100 * comparison["mae_improvement_b1_minus_b2"] / comparison["mae_b1"]
            comparisons.append(comparison)
        metrics_frame = pd.DataFrame(metrics)
        breakdown_frame = pd.concat(subgroups, ignore_index=True)
        comparison_frame = pd.DataFrame(comparisons)
        metrics_frame.to_csv(staging / "metrics.csv", index=False)
        breakdown_frame.to_csv(staging / "subgroup_metrics.csv", index=False)
        comparison_frame.to_csv(staging / "paired_comparison.csv", index=False)
        write_markdown_report(staging / "baseline_report.md", metrics_frame, breakdown_frame, comparison_frame, notes=NOTES)
        plot_comparison(metrics_frame, staging / "baseline_comparison.png")
        write_json(staging / "methodology.json", {"notes": NOTES, "seed": args.seed,
                    "calibration_folds": N_FOLDS, "bootstrap_repetitions": args.bootstrap})
        tracking = []
        for record in records:
            common_artifacts = parent_paths + [staging / "baseline_report.md", staging / "methodology.json",
                                               staging / "baseline_comparison.png"]
            tracking.append({"scenario": record["scenario"], "baseline": record["model_name"],
                **log_run(ROOT, record["scenario"], record["model_name"], record["params"],
                          record["metrics"], record["artifacts"] + common_artifacts,
                          tags={"stage": "initial_baselines", "output_version": destination.name,
                                "test_data_used_for_tuning": "false"})})
            print(f"Logged MLflow: {record['scenario']} / {record['model_name']}", flush=True)
        write_json(staging / "mlflow_runs.json", tracking)
        manifest = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "output_version": destination.name,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "input_model_sha256": parents["model"]["output"]["sha256"],
            "input_splits_sha256": parents["splits"]["output"]["sha256"],
            "seed": args.seed, "calibration_folds": N_FOLDS, "methodology": NOTES,
            "models": [{"scenario": item["scenario"], "model": item["model_name"],
                        "fold_counts": item["fold_counts"] if item["model_name"] == "B2_road_scaled" else [],
                        "metrics": item["metrics"]} for item in records],
            "mlflow_runs": tracking,
            "source_code_sha256": {name: sha256(ROOT / "modeling" / name) for name in
                ["mobility_baselines.py", "baseline_evaluation.py", "baseline_tracking.py", "train_baselines.py"]},
            "package_versions": {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "pyarrow", "mlflow"]},
            "files": {path.relative_to(staging).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size}
                      for path in sorted(staging.rglob("*")) if path.is_file()},
            "checks": {"frozen_inputs": True, "target_statistics_train_only": True,
                       "grouped_training_calibration": True, "saved_model_prediction_roundtrip": True,
                       "all_eight_mlflow_runs_logged": True},
        }
        write_json(staging / "run_manifest.json", manifest)
        require(not destination.exists(), f"Output appeared during training: {destination}")
        staging.rename(destination)
    print(f"\nCompleted eight evaluations in {time.perf_counter() - started:.1f}s. Saved: {destination}", flush=True)
    print(metrics_frame[["scenario", "baseline", "mae", "log1p_mae", "coverage_80", "mean_interval_width"]].to_string(index=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/baselines_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bootstrap", type=int, default=1000)
    run(parser.parse_args())
