"""Train the fixed third baseline, compare all three, and log four local MLflow runs."""

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
PROJECT = ROOT.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modeling.baseline_evaluation import evaluate_predictions, paired_mae_bootstrap, per_road_metrics, subgroup_metrics
from modeling.baseline_tracking import log_run
from modeling.boosted_reporting import weighted_r2, plot_three_models, write_report
from modeling.gradient_boosted import BoostedTrafficModel, make_training_history, NUMERIC_FEATURES, CATEGORICAL_FEATURES


KEYS = ["segment_id", "direction", "road_type", "hour_start_utc"]
SCENARIOS = ["within_road", "unseen_roads", "time", "season_transfer"]
MODEL_NAME = "B3_gradient_boosted"
NOTES = [
    "The four scenarios are independent experiments on the frozen model_table_v1 and model_splits_v1.",
    "B3 uses LightGBM quantile regression on log1p(volume), predicting q10/q50/q90. "
    "Fixed settings:300 trees,31 leaves, learning_rate0.05,min_child_samples100,lambda1,max_bin127,4 CPU threads.",
    "There is no parameter search, early stopping or test-based interval calibration. "
    "The three log-quantiles are sorted before expm1 to resolve crossings; raw crossing fractions are recorded.",
    "Each road/direction receives equal training influence. Fit weights are normalized to mean one "
    "to keep regularization scale stable. All test metrics use the original equal-road weights.",
    "History-profile features for training rows come exclusively from saved B2 grouped OOF predictions. "
    "Test history comes from B2 fitted only on that scenario's complete training partition. "
    "Training history-day counts use each OOF fit subset; test counts use complete training only.",
    "Calendar, holiday, borough, lane, width, speed, street direction, routing codes, coordinates "
    "and observation source are available features. Raw IDs, dates, target values, residuals, "
    "and full-snapshot coverage statistics are excluded from the trees.",
    "Road attributes remain missing when unavailable. Category vocabularies are learned on training rows only; "
    "unseen categories map to missing. Native LightGBM missing-value handling is used.",
    "Source is observation provenance and must be supplied or deliberately defined for future serving. "
    "The stored covid flag is a calendar-context indicator, not a causal estimate of pandemic impact.",
    "The time test is chronological in targets, but LION attributes are from the available release, "
    "not point-in-time historical street vintages. It is not a complete historical deployment backtest.",
    "Season transfer uses17 road/direction/year pairs (15 segments). Autumn is later than the "
    "same-year summer test; this is a small retrospective transfer check, not a future forecast.",
    "Coverage is empirical. A nominal80% interval should cover about80%, but there is no guarantee, "
    "especially under changes across years or seasons.",
    "R2 uses the global equal-road weighted test mean and may be negative. R2 is not accuracy. "
    "Subgroup metrics independently rebalance roads inside each subgroup.",
    "Feature importance is median-model training split gain, not causal influence. "
    "Paired MAE confidence intervals resample whole segments with both directions together.",
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
    clean = PROJECT / "data/mobility/clean"
    baseline_dir = PROJECT / "outputs/mobility/baselines_v1"
    paths = {"model": clean / "model_table_v1.parquet", "splits": clean / "model_splits_v1.parquet",
             "model_snapshot": clean / "model_table_v1_snapshot.json",
             "split_snapshot": clean / "model_splits_v1_snapshot.json",
             "baseline_manifest": baseline_dir / "run_manifest.json"}
    model_meta = json.loads(paths["model_snapshot"].read_text())
    split_meta = json.loads(paths["split_snapshot"].read_text())
    baseline_meta = json.loads(paths["baseline_manifest"].read_text())
    model_hash, split_hash = sha256(paths["model"]), sha256(paths["splits"])
    require(model_hash == model_meta["output"]["sha256"] == split_meta["input"]["sha256"] == baseline_meta["input_model_sha256"], "Modelling input hash mismatch.")
    require(split_hash == split_meta["output"]["sha256"] == baseline_meta["input_splits_sha256"], "Split hash mismatch.")
    require(sha256(ROOT / "modeling/mobility_baselines.py") == baseline_meta["source_code_sha256"]["mobility_baselines.py"], "B2 implementation changed since its OOF features were generated.")
    columns = list(dict.fromkeys(KEYS + ["volume", "sd", "date", "hour_start", "season"] + NUMERIC_FEATURES + CATEGORICAL_FEATURES))
    data = pd.read_parquet(paths["model"], columns=columns)
    splits = pd.read_parquet(paths["splits"])
    require(len(data) == len(splits) == model_meta["rows"], "Input count mismatch.")
    pd.testing.assert_frame_equal(data[KEYS], splits[KEYS])
    require(np.array_equal(splits.model_row, np.arange(len(data))), "Split offsets changed.")
    return data, splits, model_meta, split_meta, baseline_meta, paths


def verified_baseline_file(baseline_meta, relative):
    path = PROJECT / "outputs/mobility/baselines_v1" / relative
    require(sha256(path) == baseline_meta["files"][relative]["sha256"], f"Baseline artifact changed: {relative}")
    return path


def run(args):
    destination = args.output.resolve()
    require(not destination.exists(), f"Refusing to overwrite {destination}. Select a new output version.")
    require(args.bootstrap >= 2, "At least two bootstrap repetitions are needed.")
    started = time.perf_counter()
    data, splits, model_meta, split_meta, baseline_meta, paths = load_inputs()
    print(f"Verified {len(data):,} frozen observations and all parent fingerprints.", flush=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".boosted-build-", dir=destination.parent) as temp:
        staging = Path(temp)
        (staging / "baseline_reference_manifest.json").write_bytes(paths["baseline_manifest"].read_bytes())
        metrics, prior_metrics, comparisons, breakdowns, importances, records = [], [], [], [], [], []
        for scenario in SCENARIOS:
            print(f"Fitting LightGBM: {scenario}", flush=True)
            train_mask = splits["split_" + scenario].eq("train").to_numpy()
            test_mask = splits["split_" + scenario].eq("test").to_numpy()
            train, test = data.loc[train_mask].copy(), data.loc[test_mask].copy()
            weights = splits.loc[test_mask, "w_" + scenario].to_numpy()
            require(len(train) == split_meta["summaries"][scenario]["train"]["rows"] and
                    len(test) == split_meta["summaries"][scenario]["test"]["rows"], "Scenario counts changed.")
            oof_path = verified_baseline_file(baseline_meta, f"{scenario}/training_oof_calibration.parquet")
            history_path = verified_baseline_file(baseline_meta, f"{scenario}/B2_road_scaled/model.pkl")
            history = make_training_history(train, pd.read_parquet(oof_path), scenario)
            with history_path.open("rb") as stream:
                b2 = pickle.load(stream)
            model = BoostedTrafficModel.fit(train, history.history_profile_log.to_numpy(),
                        history.history_days.to_numpy(), b2, seed=args.seed)
            test["n_training_days"] = test.sd.astype("string").map(model.training_days).fillna(0).astype("int32")
            raw_log = model.raw_log_predictions(test)
            crossing_fraction = float(np.any(np.diff(raw_log, axis=1) < 0, axis=1).mean())
            prediction = pd.DataFrame(np.expm1(np.maximum(np.sort(raw_log, axis=1), 0)), columns=["p10", "p50", "p90"])
            score = evaluate_predictions(test, prediction, weights)
            score["r2"] = weighted_r2(test, prediction, weights)
            score["raw_quantile_crossing_fraction"] = crossing_fraction
            metrics.append({"scenario": scenario, "baseline": MODEL_NAME, **score})
            scenario_dir = staging / scenario
            artifacts = model.save(scenario_dir)
            with (scenario_dir / "model.pkl").open("rb") as stream:
                reopened = pickle.load(stream)
            pd.testing.assert_frame_equal(prediction.iloc[:200].reset_index(drop=True), reopened.predict(test.iloc[:200]), check_exact=True)
            history.rename_axis("model_row").reset_index().to_parquet(scenario_dir / "training_oof_features.parquet", index=False)
            details = subgroup_metrics(test, prediction, group_columns=("borough", "source", "road_type", "training_days_bucket", "covid"))
            details.insert(0, "baseline", MODEL_NAME)
            details.insert(0, "scenario", scenario)
            details.to_csv(scenario_dir / "subgroup_metrics.csv", index=False)
            breakdowns.append(details)
            per_road_metrics(test, prediction, weights).to_parquet(scenario_dir / "per_road_metrics.parquet", index=False)
            saved_predictions = test[KEYS + ["sd", "date", "hour", "dow", "volume", "source", "borough", "n_training_days"]].reset_index(drop=True)
            saved_predictions.insert(0, "model_row", test.index.to_numpy())
            for col in prediction:
                saved_predictions[col] = prediction[col].to_numpy()
            saved_predictions.to_parquet(scenario_dir / "test_predictions.parquet", index=False, compression="zstd")
            importance = model.feature_importance()
            importance.insert(0, "scenario", scenario)
            importance.to_csv(scenario_dir / "feature_importance.csv", index=False)
            importances.append(importance)
            write_json(scenario_dir / "metrics.json", score)
            params = {**model.parameters, "seed": args.seed, "scenario": scenario, "model": MODEL_NAME,
                      "quantiles": [0.1, 0.5, 0.9], "train_rows": len(train), "test_rows": len(test),
                      "n_features": len(model.encoder.feature_columns), "target_transform": "log1p(volume)",
                      "model_table_sha256": model_meta["output"]["sha256"],
                      "split_sha256": split_meta["output"]["sha256"],
                      "history_oof_sha256": sha256(oof_path), "history_full_fit_sha256": sha256(history_path),
                      "history_folds": 3, "history_fold_group": "segment" if scenario == "unseen_roads" else "segment_date",
                      "weights": "equal_sd_normalized_mean_one", "interval_method": "direct_quantile_regression",
                      "quantile_ordering": "monotone_rearrangement_predeclared", "tuning": "none", "early_stopping": False}
            write_json(scenario_dir / "run_parameters.json", params)
            artifacts += [scenario_dir / name for name in ["training_oof_features.parquet", "subgroup_metrics.csv",
                          "per_road_metrics.parquet", "test_predictions.parquet", "feature_importance.csv", "metrics.json", "run_parameters.json"]]
            records.append({"scenario": scenario, "score": score, "params": params, "artifacts": artifacts})
            for reference in ["B1_global", "B2_road_scaled"]:
                reference_path = verified_baseline_file(baseline_meta, f"{scenario}/{reference}/test_predictions.parquet")
                ref = pd.read_parquet(reference_path)
                require(np.array_equal(ref.model_row, test.index.to_numpy()), "Reference test rows differ.")
                pd.testing.assert_frame_equal(ref[KEYS], test[KEYS].reset_index(drop=True))
                require(np.array_equal(ref.volume.to_numpy(), test.volume.to_numpy()), "Reference targets differ.")
                ref_score = evaluate_predictions(test, ref[["p10", "p50", "p90"]], weights)
                ref_score["r2"] = weighted_r2(test, ref[["p10", "p50", "p90"]], weights)
                prior_metrics.append({"scenario": scenario, "baseline": reference, **ref_score})
                comparison = paired_mae_bootstrap(test, ref[["p10", "p50", "p90"]], prediction,
                                                 n_bootstrap=args.bootstrap, seed=args.seed, weights=weights)
                comparison.update(scenario=scenario, reference_baseline=reference, candidate=MODEL_NAME,
                                  mae_reduction_percent=100 * comparison["mae_improvement_b1_minus_b2"] / comparison["mae_b1"])
                comparisons.append(comparison)
            print(f"  MAE={score['mae']:.2f}; RMSE={score['rmse']:.2f}; R2={score['r2']:.3f}; "
                  f"80% coverage={score['coverage_80']:.1%}; raw quantile crossings={crossing_fraction:.1%}", flush=True)
        metrics_frame = pd.DataFrame(metrics)
        all_metrics = pd.concat([pd.DataFrame(prior_metrics), metrics_frame], ignore_index=True)
        comparison_frame = pd.DataFrame(comparisons)
        feature_frame = pd.concat(importances, ignore_index=True)
        metrics_frame.to_csv(staging / "metrics.csv", index=False)
        all_metrics.to_csv(staging / "all_models_metrics.csv", index=False)
        comparison_frame.to_csv(staging / "paired_comparison.csv", index=False)
        pd.concat(breakdowns, ignore_index=True).to_csv(staging / "subgroup_metrics.csv", index=False)
        feature_frame.to_csv(staging / "all_feature_importance.csv", index=False)
        write_report(staging / "boosted_report.md", all_metrics, comparison_frame, feature_frame, notes=NOTES)
        plot_three_models(all_metrics, staging / "three_model_comparison.png")
        primary = metrics_frame.loc[metrics_frame.scenario.eq("unseen_roads")].iloc[0]
        primary_comparison = comparison_frame.loc[comparison_frame.scenario.eq("unseen_roads") & comparison_frame.reference_baseline.eq("B1_global")].iloc[0]
        acceptance = {"primary_test": "unseen_roads", "mae_improvement_over_global": float(primary_comparison.mae_reduction_percent),
                      "mae_improvement_ci95": [float(primary_comparison.ci95_lower), float(primary_comparison.ci95_upper)],
                      "clear_mae_improvement": bool(primary_comparison.ci95_lower > 0),
                      "interval_coverage": float(primary.coverage_80), "coverage_target": 0.80, "coverage_tolerance": 0.05,
                      "coverage_in_target_range": bool(0.75 <= primary.coverage_80 <= 0.85)}
        acceptance["both_benchmark_criteria_met"] = acceptance["clear_mae_improvement"] and acceptance["coverage_in_target_range"]
        acceptance["scope"] = "Pre-agreed benchmark criteria only; not production or citywide validation. No test-based tuning followed."
        write_json(staging / "benchmark_checks.json", acceptance)
        write_json(staging / "methodology.json", {"notes": NOTES, "seed": args.seed, "n_estimators": 300,
                                                  "tuning": "none", "bootstrap_repetitions": args.bootstrap})
        tracking = []
        common_artifacts = [paths["model_snapshot"], paths["split_snapshot"], staging / "baseline_reference_manifest.json",
                            staging / "boosted_report.md", staging / "three_model_comparison.png", staging / "methodology.json",
                            staging / "benchmark_checks.json"]
        for record in records:
            tracking.append({"scenario": record["scenario"], "baseline": MODEL_NAME,
                **log_run(PROJECT, record["scenario"], MODEL_NAME, record["params"], record["score"],
                          record["artifacts"] + common_artifacts,
                          tags={"stage": "gradient_boosted_baseline", "output_version": destination.name,
                                "test_data_used_for_tuning": "false"})})
            print(f"Logged MLflow: {record['scenario']} / {MODEL_NAME}", flush=True)
        write_json(staging / "mlflow_runs.json", tracking)
        manifest = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "output_version": destination.name,
                    "elapsed_seconds": round(time.perf_counter() - started, 3), "seed": args.seed,
                    "input_model_sha256": model_meta["output"]["sha256"], "input_splits_sha256": split_meta["output"]["sha256"],
                    "reference_manifest_sha256": sha256(paths["baseline_manifest"]), "methodology": NOTES,
                    "metrics": metrics, "benchmark_checks": acceptance, "mlflow_runs": tracking,
                    "package_versions": {name: importlib.metadata.version(name) for name in
                        ["lightgbm", "numpy", "pandas", "pyarrow", "mlflow", "matplotlib", "scikit-learn"]},
                    "source_code_sha256": {name: sha256(ROOT / "modeling" / name) for name in
                        ["gradient_boosted.py", "train_gradient_boosted.py", "boosted_reporting.py", "mobility_baselines.py", "baseline_evaluation.py", "baseline_tracking.py"]},
                    "files": {path.relative_to(staging).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size}
                              for path in sorted(staging.rglob("*")) if path.is_file()},
                    "checks": {"source_and_parent_fingerprints": True, "oof_history_train_only": True,
                               "train_only_categories": True, "saved_model_roundtrip": True,
                               "all_four_mlflow_runs_logged": True}}
        write_json(staging / "run_manifest.json", manifest)
        require(not destination.exists(), "Output appeared during training.")
        staging.rename(destination)
    print(f"\nFinished in {time.perf_counter() - started:.1f}s; saved {destination}", flush=True)
    print(metrics_frame[["scenario", "mae", "rmse", "r2", "coverage_80", "mean_interval_width"]].to_string(index=False), flush=True)
    print(json.dumps(acceptance, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "outputs/mobility/gradient_boosted_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bootstrap", type=int, default=1000)
    run(parser.parse_args())
