"""Build a self-contained experimental native-inference bundle; never trains."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import duckdb
import pandas as pd

from predict_climate_history import ClimateHistoryPredictor, FORMAT, sha256

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def build(run_dir, destination, package_id):
    run_dir, destination = run_dir.resolve(), destination.resolve()
    if destination.exists():
        raise FileExistsError("Release already exists; preserve it or use a new version")
    checkpoint = json.loads((run_dir / "checkpoint_manifest.json").read_text())
    experiment = json.loads((run_dir / "experiment.json").read_text())
    evaluation_dir = run_dir / "development_validation"
    evaluation = json.loads((evaluation_dir / "validation_metrics.json").read_text())
    evaluation_plan = json.loads((evaluation_dir / "evaluation_plan.json").read_text())
    if (evaluation["evaluation_plan_sha256"] != sha256(evaluation_dir / "evaluation_plan.json")
            or evaluation_plan["checkpoint_sha256"] != sha256(run_dir / "checkpoint_manifest.json")
            or evaluation["run_id"] != checkpoint["run_id"]
            or evaluation["test_data_queried"] or evaluation_plan["test_data_queried"]):
        raise ValueError("Evaluation does not match the candidate checkpoint")
    for name, info in checkpoint["assets"].items():
        path = (run_dir / name).resolve()
        if path.parent != run_dir or sha256(path) != info["sha256"]:
            raise ValueError(f"Candidate asset changed: {name}")
    helper = ROOT / "scripts/climate_history_features.py"
    if sha256(helper) != checkpoint["history_helper"]["sha256"]:
        raise ValueError("History helper differs from the fitted candidate")
    preparation_path = ROOT / "config/climate_preparation.json"
    if sha256(preparation_path) != checkpoint["preparation_evidence_sha256"]["preparation_config"]:
        raise ValueError("Preparation settings differ from checkpoint evidence")
    preparation = json.loads(preparation_path.read_text())
    if preparation["rainfall_availability_lag_hours"] != 3:
        raise ValueError("Native adapter requires the documented three-hour rainfall policy")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".climate-release-", dir=destination.parent))
    try:
        for name in ["history_tree.joblib", "history_events.parquet", "street_cell_bridge.parquet", "experiment.json"]:
            shutil.copy2(run_dir / name, temporary / name)
        shutil.copy2(helper, temporary / "climate_history_features.py")
        shutil.copy2(ROOT / "scripts/predict_climate_history.py", temporary / "predict.py")
        for name in ["validation_metrics.json", "borough_metrics.csv", "daily_metrics.csv"]:
            shutil.copy2(evaluation_dir / name, temporary / name)
        versions = {name: checkpoint["package_versions"][name] for name in
                    ["numpy", "pandas", "scipy", "scikit-learn", "joblib", "threadpoolctl", "pyarrow"]}
        (temporary / "requirements.txt").write_text("\n".join(f"{name}=={version}" for name, version in versions.items()) + "\n")
        readme = f'''# PLEXA climate history baseline — experimental native handover

Package: `{package_id}`. Python: {checkpoint['python_version']}.

This model predicts qualifying NYC 311 street/highway flooding report occurrence
on one physical segment during the next hour. Scores are uncalibrated. It does not
predict water depth, safe passage or road closure. Development precision and recall
are weak; see validation_metrics.json. The 2025 development set informed tuning.
This candidate has no fresh independent test assessment; 2026 was inspected for v2.
Ida is inside the training period and is an integration replay.

## Run after extraction

Install requirements.txt in an isolated environment using the stated Python version.
All model/history assets are local; inference performs no download or database query.

```sh
python predict.py --input example_request.json --output replay_result.json
```

Use a new output filename each time. Compare the result with example_result.json.
The expected tolerance for scores is absolute 1e-12 in the pinned environment.
Optional `--bundle PATH` locates an extracted release from another working directory.

## Inputs and output

The native JSON envelope has format_version, package_id and records. Every record
has request_id, street_segment_id, prediction_time_utc (UTC hour ending Z), and
features containing exactly the eight physical/rainfall inputs in the example.
IDs are strings. Missing/invalid values are never filled with zero. Negative mean
elevation is allowed; length must be positive and other features nonnegative.
Rainfall accumulations must be nondecreasing and use the lagged source convention.
The caller must supply prepared features; this adapter does not download rainfall.

The estimator has TEN inputs. The adapter calculates the two history counts from
the supplied street ID, prediction time and saved training-only assets. It does
not require Anas's backend to reconstruct history or submit target-hour reports.
History uses a 365-day window ending min(prediction time minus 7 days, training end).
Initial history is incomplete. Seven days is an assumed availability margin, not
verified historical publication evidence. Counts are positive segment-hours, not
independent incidents. The radar-cell feature counts across streets in that cell.

Output preserves request IDs and returns one ok/error result per input. A successful
result contains score, target interval, rainfall/history cutoff, model-alert flag
and threshold, null severity/depth, and limitations as warnings. The model-alert
threshold was selected on development F1; it is NOT an approved cascade trigger.
An absent report label does not establish absence of physical flooding.

CLI exits: 0 = all records OK, 1 = completed output contains record errors,
2 = invalid envelope, asset/dependency failure, or another run-level failure.
Run-level failures do not publish a completed output file. Existing output files
are preserved. Loading is restricted to this release's tested package versions.

## Interface and spatial boundary

This is a NATIVE interface, not the shared contract v0.1 or an accepted v0.2 schema.
The proposed shared operation is climate.predict_report_next_hour. Anas must review
the revised common envelope and domain fields before contract integration is claimed.
Inputs use climate physical IDs, not LION IDs. Native geometry and the directed-edge
bridge for a reviewed Ida subset are a separate scenario delivery. No LION match,
closure duration, traffic multiplier or energy failure is invented by this package.
The example is one reproducible input, not the team's approved scenario road subset.

## Provenance

release_manifest.json hashes all package assets. The model is copied unchanged from
the selected regularized v3 checkpoint. example_lineage.json identifies the prepared
feature row and source manifests. Label evidence is separate from inference inputs.
The source bridge and current OSM network are retrospective spatial approximations.
Only the declared prepared snapshot interval is supported by this release.
'''
        (temporary / "README.md").write_text(readme)
        manifest = {"package_id": package_id, "native_format_version": FORMAT,
            "interface_status": "native_only_shared_contract_revision_pending",
            "proposed_shared_operation": "climate.predict_report_next_hour",
            "status": "experimental_weak_development_performance",
            "target": experiment["target"], "model_feature_order": experiment["features"],
            "external_base_features": experiment["base_features"],
            "internal_history_features": experiment["additional_features"],
            "python_version": checkpoint["python_version"], "inference_package_versions": versions,
            "checkpoint_run_id": checkpoint["run_id"],
            "checkpoint_manifest_sha256": sha256(run_dir / "checkpoint_manifest.json"),
            "supported_snapshot_interval": [preparation["snapshot_start_utc"], preparation["snapshot_end_utc_exclusive"]],
            "alert_threshold": evaluation["metrics"]["threshold"],
            "score_calibrated": False, "development_metrics": evaluation["metrics"],
            "fresh_independent_test_assessment": False, "ida_training_overlap": True,
            "historical_rainfall_availability": "assumed_3_hour_lag",
            "history_policy": experiment["history_policy"],
            "assets": {p.name: sha256(p) for p in sorted(temporary.iterdir())}}
        write_json(temporary / "release_manifest.json", manifest)

        # Choose the first valid Queens segment by ID, without using scores or labels.
        dataset_dir = ROOT / "data/processed/baseline_dataset_v1"
        if sha256(dataset_dir / "manifest.json") != checkpoint["preparation_evidence_sha256"]["dataset_manifest"]:
            raise ValueError("Example dataset differs from the fitted evidence")
        con = duckdb.connect(str(dataset_dir / "dataset.duckdb"), read_only=True)
        try:
            con.execute("SET TimeZone='UTC'")
            con.execute("SET threads=2")
            con.execute("SET memory_limit='2GB'")
            segment = con.execute('''SELECT street_segment_id FROM street_features
                WHERE static_features_valid AND borough_at_midpoint='Queens'
                ORDER BY street_segment_id LIMIT 1''').fetchone()[0]
            base = experiment["base_features"]
            if not all(name.replace("_", "").isalnum() for name in base):
                raise ValueError("Unexpected feature names")
            values = con.execute('SELECT ' + ','.join(f'"{name}"' for name in base)
                + " FROM labeled WHERE street_segment_id=? AND prediction_time_utc=TIMESTAMPTZ '2021-09-02 03:00:00+00'",
                [segment]).fetchall()
            if len(values) != 1:
                raise ValueError("Expected one valid example feature row")
        finally:
            con.close()
        request = {"format_version": FORMAT, "package_id": package_id, "records": [{
            "request_id": "ida-feature-example-001", "street_segment_id": segment,
            "prediction_time_utc": "2021-09-02T03:00:00Z",
            "features": {name: float(value) for name, value in zip(base, values[0])}}]}
        write_json(temporary / "example_request.json", request)
        raw = (temporary / "example_request.json").read_bytes()
        predictor = ClimateHistoryPredictor(temporary)
        actual = predictor.predict(request, hashlib.sha256(raw).hexdigest())
        if any(record["status"] != "ok" for record in actual["records"]):
            raise ValueError("Real example inference failed")
        write_json(temporary / "example_result.json", actual)
        missing = copy.deepcopy(request)
        missing["records"][0]["request_id"] = "missing-feature-example-001"
        del missing["records"][0]["features"][base[0]]
        write_json(temporary / "missing_feature_request.json", missing)
        raw_missing = (temporary / "missing_feature_request.json").read_bytes()
        missing_result = predictor.predict(missing, hashlib.sha256(raw_missing).hexdigest())
        if missing_result["records"][0]["reason_code"] != "MISSING_REQUIRED_FEATURE":
            raise ValueError("Missing-feature behavior failed")
        write_json(temporary / "missing_feature_result.json", missing_result)
        lineage = {"record_id": f"{segment}|2021-09-02T03:00:00Z",
            "source_kind": "historical_prepared_feature_row", "street_segment_id": segment,
            "prediction_time_utc": "2021-09-02T03:00:00Z",
            "selection": "first_valid_Queens_segment_by_ID_before_prediction_no_label_or_score_selection",
            "prepared_dataset_manifest_sha256": sha256(dataset_dir / "manifest.json"),
            "training_label_keys": "not_inferred_from_feature_row",
            "shared_scenario_subset_approved": False}
        write_json(temporary / "example_lineage.json", lineage)
        manifest["assets"] = {p.name: sha256(p) for p in sorted(temporary.iterdir())
                              if p.name != "release_manifest.json"}
        write_json(temporary / "release_manifest.json", manifest)
        # Final manifest verifies the example assets too.
        replay = ClimateHistoryPredictor(temporary).predict(request, hashlib.sha256(raw).hexdigest())
        if replay != actual:
            raise ValueError("Example repeatability mismatch")
        temporary.rename(destination)
        print(f"Built package: {destination}")
        print("Real example and explicit missing-feature result verified.")
        print("Native interface only; shared contract alignment and scenario mapping remain pending.")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--package-id", default="climate-history-v3-02-experimental-01")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not args.package_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.package_id):
        parser.error("Package ID must use letters, digits, hyphens and underscores")
    output = args.output_dir or ROOT / "data/processed/climate_releases" / args.package_id
    build(args.run_dir, output, args.package_id)


if __name__ == "__main__":
    main()
