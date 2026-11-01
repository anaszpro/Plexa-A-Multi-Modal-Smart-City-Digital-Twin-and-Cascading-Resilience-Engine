"""Package the evaluated pre-2024 baseline and normalized approved-scope inputs.

No fitting or 2024 outcome evaluation. Existing models/scenarios are untouched.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import tempfile

import geopandas as gpd
import numpy as np
import pandas as pd

from predict_climate_history import ClimateHistoryPredictor, FORMAT, sha256, utc_text
from train_climate_pre2024 import ROOT, DATASET, CONFIG

PACKAGE_ID = "climate-pre2024-v1-prototype-01"
DESTINATION = ROOT / "models/climate_pre2024_v1"
START = pd.Timestamp("2024-01-01T05:00:00Z")
END = pd.Timestamp("2025-01-01T05:00:00Z")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def asset_hashes(directory, exclude=None):
    return {path.name: sha256(path) for path in sorted(directory.iterdir())
            if path.is_file() and path.name != exclude}


def normalized_weather(paths, identities):
    if identities.grid_id.nunique() != 1:
        raise ValueError("Mixed approved radar grids")
    grid = identities.grid_id.iloc[0]
    cells = identities.cell_id.unique().tolist()
    columns = ["grid_id", "cell_id", "prediction_time_utc", "rain_1h_mm", "rain_3h_mm", "rain_6h_mm", "rain_24h_mm", "weather_features_valid"]
    parts = [pd.read_parquet(path, columns=columns, filters=[("grid_id", "==", grid), ("cell_id", "in", cells),
        ("prediction_time_utc", ">=", START.to_pydatetime()), ("prediction_time_utc", "<", END.to_pydatetime())]) for path in paths]
    weather = pd.concat(parts, ignore_index=True).sort_values(["grid_id", "cell_id", "prediction_time_utc"]).reset_index(drop=True)
    if weather.duplicated(["grid_id", "cell_id", "prediction_time_utc"]).any():
        raise ValueError("Duplicate normalized cell-hour")
    hours = pd.date_range(START, END, freq="h", inclusive="left")
    if len(weather) != len(cells)*len(hours) or not weather.prediction_time_utc.isin(hours).all():
        raise ValueError("Incomplete normalized hourly keys")
    if not weather.groupby(["grid_id", "cell_id"]).size().eq(len(hours)).all():
        raise ValueError("Incomplete cell coverage")
    values = weather[columns[3:7]].to_numpy(dtype=float)
    weather["rainfall_inputs_valid"] = (weather.weather_features_valid.eq(True)
        & np.isfinite(values).all(axis=1) & (values >= 0).all(axis=1)
        & (np.diff(values, axis=1) >= -1e-9).all(axis=1))
    gap = pd.date_range("2024-03-19T20:00:00Z", "2024-03-20T22:00:00Z", freq="h", inclusive="left")
    unavailable = weather.loc[~weather.rainfall_inputs_valid]
    if (set(unavailable.prediction_time_utc) != set(gap)
            or not unavailable.groupby("prediction_time_utc").size().eq(len(cells)).all()):
        raise ValueError("Approved-scope gaps differ from verified March gap")
    return weather


def build(run_dir, destination=DESTINATION):
    RUN = Path(run_dir).resolve()
    DESTINATION = Path(destination).resolve()
    if DESTINATION.exists():
        raise FileExistsError("Release already exists; preserve it")
    checkpoint = json.loads((RUN / "checkpoint_manifest.json").read_text())
    experiment = json.loads((RUN / "experiment.json").read_text())
    metrics = json.loads((RUN / "development_validation/validation_metrics.json").read_text())
    plan_path = RUN / "development_validation/evaluation_plan.json"
    plan = json.loads(plan_path.read_text())
    if (metrics["evaluation_plan_sha256"] != sha256(plan_path)
            or plan["checkpoint_sha256"] != sha256(RUN / "checkpoint_manifest.json")
            or metrics["run_id"] != checkpoint["run_id"] or metrics["test_data_queried"]
            or experiment["development_interval"] != ["2023-07-01T04:00:00Z", "2023-12-25T05:00:00Z"]):
        raise ValueError("Development evidence differs from approved fit")
    for name, info in checkpoint["assets"].items():
        if sha256(RUN / name) != info["sha256"]:
            raise ValueError(f"Checkpoint changed: {name}")
    assessment = ROOT / "data/processed/climate_coverage_assessments/pre2024-mobility-assessment-v1"
    assessment_manifest = json.loads((assessment / "review_manifest.json").read_text())
    for name, digest in assessment_manifest["files"].items():
        if sha256(assessment / name) != digest:
            raise ValueError(f"Approved assessment changed: {name}")
    crosswalk = pd.read_csv(assessment / "proposed_mobility_crosswalk.csv", dtype={"mobility_segment_id": str})
    approved = crosswalk.loc[crosswalk.relationship.eq("single_native_candidate")
                             & ~crosswalk.native_shared_between_mobility_roads].copy()
    approved["scope_status"] = "approved_initial_common_scope"
    approved["mapping_status"] = "approved_initial_common_scope"
    if len(approved) != 441 or approved.street_segment_id.nunique() != 441:
        raise ValueError("Serving scope differs from approved 441 one-to-one roads")
    contexts = pd.read_csv(assessment / "request_reference/supported_mobility_contexts.csv", dtype=str)
    contexts = contexts.loc[contexts.mobility_segment_id.isin(approved.mobility_segment_id)].copy()
    if len(contexts) != 666:
        raise ValueError("Serving context count differs from approval")
    static_path = ROOT / "data/processed/baseline_street_features_v1/street_features.parquet"
    static = pd.read_parquet(static_path)
    chosen = approved[["street_segment_id"]].merge(static, on="street_segment_id", validate="one_to_one", how="left")
    if not chosen.static_features_valid.eq(True).all():
        raise ValueError("Approved static input unavailable")
    bridge = pd.read_parquet(RUN / "street_cell_bridge.parquet")
    scoped_bridge = chosen[["street_segment_id", "grid_id", "cell_id"]].merge(
        bridge, on=["street_segment_id", "grid_id", "cell_id"], validate="one_to_one")
    if len(scoped_bridge) != 441:
        raise ValueError("Approved street/grid identity differs from fitted bridge")
    parent_manifest = json.loads((DATASET / "manifest.json").read_text())
    paths, source_evidence = [], {}
    for month in pd.period_range("2024-01", "2025-01", freq="M"):
        directory = ROOT / "data/processed/baseline_weather_v1" / str(month)
        for path in [directory / "weather_features.parquet", directory / "manifest.json"]:
            actual = sha256(path)
            if parent_manifest["input_sha256"].get(str(path)) != actual:
                raise ValueError(f"Prepared normalized source changed: {path}")
            source_evidence[str(path.relative_to(ROOT))] = actual
        paths.append(directory / "weather_features.parquet")
    weather = normalized_weather(paths, chosen)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".pre2024-release-", dir=DESTINATION.parent))
    try:
        model_dir, inputs = temporary / "model", temporary / "inputs_2024"
        model_dir.mkdir(); inputs.mkdir()
        for name in ["history_tree.joblib", "history_events.parquet", "experiment.json"]:
            shutil.copy2(RUN / name, model_dir / name)
        label_manifest_path = ROOT / "data/processed/hourly_report_labels_v1/manifest.json"
        label_manifest = json.loads(label_manifest_path.read_text())
        reports_path = ROOT / "data/processed/physical_311_mapping_v1/matched_reports.parquet"
        if sha256(reports_path) != label_manifest["input_sha256"]["data/processed/physical_311_mapping_v1/matched_reports.parquet"]:
            raise ValueError("Historical report lineage changed")
        reports = pd.read_parquet(reports_path)
        reports["prediction_time_utc"] = pd.to_datetime(reports.created_at, utc=True).dt.floor("h")
        history_events = pd.read_parquet(RUN / "history_events.parquet")
        history_lineage = history_events[["street_segment_id", "prediction_time_utc"]].merge(
            reports, on=["street_segment_id", "prediction_time_utc"], how="left", validate="one_to_many")
        if history_lineage.unique_key.isna().any():
            raise ValueError("History event lacks original 311 lineage")
        history_lineage["source_dataset_id"] = "nyc_311"
        history_lineage["source_record_id"] = history_lineage.unique_key.astype(str)
        history_lineage.to_parquet(model_dir / "history_report_lineage.parquet", index=False)
        write_json(model_dir / "source_provenance.json", {
            "prepared_dataset_manifest_sha256": sha256(DATASET / "manifest.json"),
            "target_rules_sha256": sha256(ROOT / "config/climate_baseline_target.json"),
            "label_manifest_sha256": sha256(label_manifest_path), "report_mapping_sha256": sha256(reports_path),
            "history_unit": "positive_segment_hours; source_record_id identifies original 311 reports",
            "history_lineage_contains_2024_outcomes": False,
            "shared_311_evidence": "do_not_treat_as_independent_corroboration_with_safety"})
        scoped_bridge.to_parquet(model_dir / "street_cell_bridge.parquet", index=False)
        for source, target in [("predict_climate_history.py", "predict.py"),
                               ("climate_history_features.py", "climate_history_features.py"),
                               ("prepare_climate_pre2024_request.py", "prepare_request.py")]:
            shutil.copy2(ROOT / "scripts" / source, model_dir / target)
        for name in ["validation_metrics.json", "daily_metrics.csv", "borough_metrics.csv"]:
            shutil.copy2(RUN / "development_validation" / name, model_dir / name)
        confusion_plot = RUN / "development_validation/development_confusion_matrix.png"
        if confusion_plot.exists():
            shutil.copy2(confusion_plot, model_dir / confusion_plot.name)
        versions = {name: checkpoint["package_versions"][name] for name in
                    ["numpy", "pandas", "scipy", "scikit-learn", "joblib", "threadpoolctl", "pyarrow"]}
        (model_dir / "requirements.txt").write_text("\n".join(f"{name}=={version}" for name, version in versions.items())+"\n")
        write_json(model_dir / "feature_schema.json", {
            "external_order": experiment["base_features"], "model_order": experiment["features"],
            "units": {"elevation_mean_m": "m", "terrain_endpoint_grade_abs": "dimensionless_abs_grade",
                      "terrain_relief_m": "m", "length_m": "m", **{f"rain_{h}h_mm": "mm" for h in [1,3,6,24]},
                      "segment_positive_hours_365d": "positive_segment_hours", "cell_positive_hours_365d": "positive_segment_hours"},
            "learned_preprocessing": "none; ten numeric inputs to histogram gradient boosting",
            "history_computed_internally": True, "missing_features": "record_error_never_zero_fill",
            "scope": "441 approved native IDs; mobility directions are metadata, not model inputs"})
        # Normalized input assets: no 2024 outcomes or duplicated static-by-hour rows.
        chosen.to_parquet(inputs / "static_by_road.parquet", index=False)
        weather.to_parquet(inputs / "rainfall_by_cell_hour.parquet", index=False)
        approved.to_csv(inputs / "approved_mobility_crosswalk.csv", index=False)
        contexts.to_csv(inputs / "supported_mobility_contexts.csv", index=False)
        geometry = gpd.read_parquet(ROOT / "data/processed/baseline_street_features_v1/street_segments.parquet")
        geometry.loc[geometry.street_segment_id.isin(chosen.street_segment_id)].to_crs(4326).to_file(
            inputs / "native_segments.geojson", driver="GeoJSON")
        edge_index = pd.read_parquet(ROOT / "data/processed/physical_street_index_v1.parquet")
        edge_index.loc[edge_index.street_segment_id.isin(chosen.street_segment_id)].to_parquet(
            inputs / "directed_edge_bridge.parquet", index=False)
        unavailable = weather.loc[~weather.rainfall_inputs_valid, ["grid_id", "cell_id", "prediction_time_utc"]].copy()
        unavailable["reason"] = "missing_raw_source_in_required_accumulation"
        unavailable.to_parquet(inputs / "unavailable_cell_hours.parquet", index=False)
        gap_hours = unavailable.groupby("prediction_time_utc").size().rename("unavailable_cells").reset_index()
        gap_hours["prediction_time_new_york"] = gap_hours.prediction_time_utc.dt.tz_convert("America/New_York")
        gap_hours.to_csv(inputs / "unavailable_prediction_hours.csv", index=False)
        hourly = weather.groupby("prediction_time_utc").agg(cell_rows=("cell_id", "size"), valid_cell_rows=("rainfall_inputs_valid", "sum"))
        hourly["month_new_york"] = hourly.index.tz_convert("America/New_York").strftime("%Y-%m")
        hourly["fully_usable"] = hourly.cell_rows.eq(hourly.valid_cell_rows)
        monthly = hourly.groupby("month_new_york").agg(expected_hours=("cell_rows", "size"),
            fully_usable_hours=("fully_usable", "sum"), valid_cell_hours=("valid_cell_rows", "sum"))
        monthly["unavailable_hours"] = monthly.expected_hours-monthly.fully_usable_hours
        monthly.to_csv(inputs / "monthly_input_coverage.csv")
        for name in ["rainfall_source_ledger.csv", "boundary_source_ledger.csv"]:
            shutil.copy2(assessment / name, inputs / name)
        write_json(inputs / "source_fingerprints.json", source_evidence)
        write_json(inputs / "join_schema.json", {
            "road_to_rainfall": ["grid_id", "cell_id"], "rainfall_hour": "prediction_time_utc",
            "one_prediction": "native physical segment at UTC hour, target [t,t+1h)",
            "rainfall_intervals": "accumulation-end semantics; latest end t-3h; windows 1/3/6/24 consecutive hours",
            "lookback_sources": "rainfall_source_ledger.csv; historic arrival times unverified",
            "rainfall_validity_flag": "rainfall_inputs_valid", "missing_inputs": "retain_unavailable_never_zero_fill",
            "history": "computed from model/history_events.parquet, not 2024 outcome data",
            "scope": "approved 441 one-to-one physical mappings; 666 mobility directions remain metadata"})
        (inputs / "README.md").write_text("""# Normalized 2024 climate inputs

Join static_by_road.parquet to rainfall_by_cell_hour.parquet using grid_id/cell_id,
then select the requested UTC prediction hour. There are 441 physical roads and
8,784 hourly starts per road. Do not duplicate or treat the 666 mobility direction
contexts as independent climate locations. No 2024 report outcomes are included.

rainfall_inputs_valid=false means unavailable, not dry. The 26-hour March gap is
retained. The native preparer omits unavailable rainfall fields so inference
returns an identified MISSING_REQUIRED_FEATURE error rather than a fake score.
See monthly_input_coverage.csv, unavailable_prediction_hours.csv, join_schema.json
and the source ledgers. Normalized files identify source feature rows; the runtime
performs no raw download. Historic geometry and publication timing are unverified.
""")
        (model_dir / "README.md").write_text(f"""# {PACKAGE_ID}

Python {checkpoint['python_version']}; install the exact requirements.txt versions.
The model was fitted citywide on eligible pre-2024 rows; serving is limited to the
approved 441 native physical IDs in street_cell_bridge.parquet. It predicts a
qualifying NYC311 flooding report in [t,t+1h), not depth, closure or safe passage.
Eight external numeric features and two internally computed history counts are
documented in feature_schema.json. No learned scaler or encoder is required.

Training ends 2023-06-24T04:00:00Z. Development is 2023-07-01T04:00:00Z through
2023-12-25T05:00:00Z, end exclusive. The threshold was selected on this full
pre-2024 development population; see validation_metrics.json. Performance is
reported without an operational accuracy claim. Earlier project exploration
examined 2024 data; this is not a pristine independent holdout.

The history window in the 2024 scenario is fixed at 2022-06-24T04:00:00Z through
2023-06-24T04:00:00Z, end exclusive. Training uses past-only construction. Neither
development nor 2024 outcomes enter the 7,843-event training-only pool. Seven-day
report delay and three-hour rainfall lag are availability assumptions.

Native format climate-history-native-0.1 remains unchanged. Inputs use native
street IDs, UTC hourly timestamps ending Z and exactly eight base features. One
result per request ID includes score, alert/threshold, target interval, rainfall
and history cutoffs, warnings, and null depth/severity. Scores are uncalibrated.
Unsupported IDs and missing/invalid inputs return explicit record errors. Invalid
envelopes/dependencies/assets fail the run. Existing output files are preserved.

From this model folder: python predict.py --input example_request.json --output replay.json
CLI exits 0 for all successful records, 1 for output containing record errors,
and 2 for run-level errors. All manifest-listed files must be retained. Only load
trusted model assets whose hashes match this release. Shared adapter/cascade rules
are Anas's responsibility; a model alert is not an approved road-closure trigger.
""")
        manifest = {"package_id": PACKAGE_ID, "native_format_version": FORMAT,
            "target": experiment["target"], "status": "prototype_baseline_pre2024_development_evaluated",
            "model_feature_order": experiment["features"], "external_base_features": experiment["base_features"],
            "internal_history_features": experiment["additional_features"], "python_version": checkpoint["python_version"],
            "inference_package_versions": versions, "checkpoint_run_id": checkpoint["run_id"],
            "checkpoint_manifest_sha256": sha256(RUN / "checkpoint_manifest.json"),
            "supported_snapshot_interval": ["2020-10-14T04:00:00Z", utc_text(END)],
            "approved_scope": {"physical_roads":441,"mobility_contexts":666},
            "alert_threshold": metrics["metrics"]["threshold"], "score_calibrated": False,
            "development_metrics": metrics["metrics"], "development_interval": experiment["development_interval"],
            "fresh_independent_test_assessment": False, "2024_used_in_this_fit_tuning_history": False,
            "2024_previously_examined_in_project": True, "ida_training_overlap": True,
            "history_policy": experiment["history_policy"], "historical_rainfall_availability": "assumed_3_hour_lag",
            "prediction_warnings": ["UNCALIBRATED_REPORT_PROXY", "HISTORICAL_AVAILABILITY_ASSUMED",
                                    "FIXED_TRAINING_CONTEXT", "PREVIOUSLY_EXAMINED_HISTORICAL_SCENARIO"],
            "assets": asset_hashes(model_dir)}
        write_json(model_dir / "release_manifest.json", manifest)
        predictor = ClimateHistoryPredictor(model_dir)
        first = chosen.sort_values("street_segment_id").iloc[0]
        rain = weather.loc[weather.grid_id.eq(first.grid_id) & weather.cell_id.eq(first.cell_id)
                           & weather.prediction_time_utc.eq(START)].iloc[0]
        feature_values = {name: float(first[name] if name in chosen.columns else rain[name]) for name in experiment["base_features"]}
        request = {"format_version": FORMAT, "package_id": PACKAGE_ID, "records": [{
            "request_id": f"pre2024:{first.street_segment_id}:{START:%Y%m%dT%H%M%SZ}",
            "street_segment_id": first.street_segment_id, "prediction_time_utc": utc_text(START), "features": feature_values}]}
        write_json(model_dir / "example_request.json", request)
        result = predictor.predict(request, sha256(model_dir / "example_request.json"))
        if result["records"][0]["status"] != "ok":
            raise ValueError("Actual supported example failed")
        write_json(model_dir / "example_result.json", result)
        missing = copy.deepcopy(request)
        missing["records"][0]["request_id"] = "actual-unavailable-march-hour"
        missing["records"][0]["prediction_time_utc"] = "2024-03-19T20:00:00Z"
        for name in experiment["base_features"][4:]:
            missing["records"][0]["features"].pop(name)
        write_json(model_dir / "unavailable_hour_request.json", missing)
        missing_result = predictor.predict(missing, sha256(model_dir / "unavailable_hour_request.json"))
        if missing_result["records"][0]["reason_code"] != "MISSING_REQUIRED_FEATURE":
            raise ValueError("Unavailable rainfall did not produce an identified error")
        write_json(model_dir / "unavailable_hour_result.json", missing_result)
        write_json(model_dir / "example_lineage.json", {"selection": "first_approved_native_ID_before_inspecting_scores_or_outcomes",
            "street_segment_id": first.street_segment_id, "grid_id": first.grid_id, "cell_id": int(first.cell_id),
            "prediction_time_utc": utc_text(START), "source_dataset_manifest_sha256": sha256(DATASET / "manifest.json"),
            "normalized_static_sha256": sha256(inputs / "static_by_road.parquet"),
            "normalized_rainfall_sha256": sha256(inputs / "rainfall_by_cell_hour.parquet")})
        manifest["assets"] = asset_hashes(model_dir, "release_manifest.json")
        write_json(model_dir / "release_manifest.json", manifest)
        predictor = ClimateHistoryPredictor(model_dir)
        if predictor.predict(request, sha256(model_dir / "example_request.json")) != result:
            raise ValueError("Final model bundle replay differs")
        input_manifest = {"input_id": "climate-pre2024-approved-441-normalized-2024-v1", "package_id": PACKAGE_ID,
            "model_release_manifest_sha256": sha256(model_dir / "release_manifest.json"),
            "utc_interval": [utc_text(START), utc_text(END)], "calendar_timezone": "America/New_York",
            "physical_roads": 441, "mobility_contexts": 666, "rainfall_cells": int(chosen.cell_id.nunique()),
            "hourly_starts": 8784, "unavailable_prediction_hours": 26,
            "expected_native_segment_hours":441*8784,"valid_native_segment_hours":441*(8784-26),
            "contains_2024_outcomes": False, "assets": asset_hashes(inputs)}
        write_json(inputs / "input_manifest.json", input_manifest)
        # New outputs for the reviewed aligned Ida request; original package unchanged.
        ida_source = ROOT / "data/processed/climate_scenarios/ida-2021-lion-aligned-candidate-v1"
        ida_manifest = json.loads((ida_source / "candidate_manifest.json").read_text())
        if sha256(ida_source / "request.json") != ida_manifest["files"]["request.json"]:
            raise ValueError("Reviewed Ida source request changed")
        ida_dir = temporary / "ida_compatibility"; ida_dir.mkdir()
        ida_request = json.loads((ida_source / "request.json").read_text())
        ida_request["package_id"] = PACKAGE_ID
        write_json(ida_dir / "request.json", ida_request)
        ida_result = predictor.predict(ida_request, sha256(ida_dir / "request.json"))
        write_json(ida_dir / "result.json", ida_result)
        status = pd.json_normalize(ida_result["records"])
        write_json(ida_dir / "compatibility_summary.json", {"source_request_sha256":sha256(ida_source/"request.json"),
            "package_id":PACKAGE_ID,"requested_rows":len(status),"successful_rows":int(status.status.eq("ok").sum()),
            "unsupported_or_error_rows":int(status.status.ne("ok").sum()),
            "successful_model_alerts":int(sum(r["result"]["model_alert"] for r in ida_result["records"] if r["status"]=="ok")),
            "training_overlap":True,"original_Ida_release_unchanged":True})
        (ida_dir / "README.md").write_text("New-model compatibility outputs for the previously reviewed 192-row aligned Ida request. All original feature inputs and request IDs are retained; only package_id changes. Errors, if any, remain explicit for scope review. This is a training-overlapping replay, not independent evaluation. The original model and Ida candidate are unchanged.\n")
        for name in ["pretraining_report.json", "checkpoint_manifest.json"]:
            shutil.copy2(RUN / name, temporary / name)
        shutil.copy2(CONFIG, temporary / "frozen_experiment.json")
        for name in ["train_climate_pre2024.py", "validate_climate_history.py", "climate_history_features.py",
                     "package_climate_pre2024.py", "predict_climate_history.py", "prepare_climate_pre2024_request.py"]:
            target = temporary / "source/scripts" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "scripts" / name, target)
        for name in ["test_climate_pre2024.py", "test_climate_history_features.py", "test_climate_history_validation.py"]:
            target = temporary / "source/tests" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "tests" / name, target)
        notebook_target = temporary / "source/notebooks/05_climate_pre2024_baseline.ipynb"
        notebook_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "notebooks/05_climate_pre2024_baseline.ipynb", notebook_target)
        (temporary / "README.md").write_text(f"""# {PACKAGE_ID} — approved 441-road prototype baseline

Python {checkpoint['python_version']}. Install model/requirements.txt in an isolated environment.
model/ contains the trained model, 7,843-event training-only history, feature schema,
native predictor, request preparer, pinned dependencies and actual examples/metrics.
inputs_2024/ contains normalized static-by-road and rainfall-by-cell/hour data for
441 physical roads / 666 mobility contexts. See the join schema and gap ledgers.
ida_compatibility/ contains separately versioned actual outputs for reviewed Ida inputs.

From this folder, prepare and predict all approved roads for one supported hour:

```bash
python model/prepare_request.py --inputs inputs_2024 --time 2024-01-01T05:00:00Z --request request.json --result result.json
```

Use fresh output names. Add --mobility-id ID to restrict a request (repeat for more
roads). Unapproved/pending mobility IDs fail clearly. On a March-gap hour, requests
retain static inputs but omit unavailable rainfall; results are identified errors,
not artificial zero-rain predictions. Context sidecars preserve mobility/native IDs
and source references. Direct native inference remains available via model/predict.py.

The fixed 365-day history interval in 2024 is [2022-06-24T04:00:00Z,
2023-06-24T04:00:00Z). No 2023 development or 2024 outcomes enter the pool. Training
uses only each example's permitted past. The 26-hour March gap and assumed rainfall
lag are unchanged. No year-long outcome evaluation or monolithic prediction JSON
is included. Initial parameters inherited earlier exploration; 2024 is not pristine.

The threshold was selected from full pre-2024 development only; see model/validation_metrics.json.
Development rows: {metrics['metrics']['rows']:,}; observed positive segment-hours: {metrics['metrics']['positive_rows']:,}.
Precision: {metrics['metrics']['precision']:.4%}; recall: {metrics['metrics']['recall']:.4%};
F1: {metrics['metrics']['F1']:.6f}; approximate AP: {metrics['metrics']['approximate_AP']:.8f}.
Confusion counts: TP={metrics['metrics']['TP']}, FP={metrics['metrics']['FP']},
FN={metrics['metrics']['FN']}, TN={metrics['metrics']['TN']}.
Frozen alert threshold: {metrics['metrics']['threshold']:.12g}.
No operational accuracy target, calibrated probability, closure or depth is claimed.
Native interface is unchanged; shared adapter/cascade rules remain Anas's. Pending
split/shared-native/unresolved mappings and original Ida releases remain separate.
handover_manifest.json hashes all delivered files except itself.

Verify this extracted release locally: python verify_release.py

source/ preserves the training/evaluation/packaging code, policy tests and notebook.
Rebuilding a fresh fit requires the original repository layout and prepared data;
the runtime example and normalized-input commands need only this release folder.
""")
        shutil.copy2(ROOT / "scripts/verify_climate_pre2024_release.py", temporary / "verify_release.py")
        write_json(temporary / "handover_manifest.json", {"package_id":PACKAGE_ID,
            "files":{str(p.relative_to(temporary)):sha256(p) for p in sorted(temporary.rglob("*")) if p.is_file()}})
        temporary.rename(DESTINATION)
        print(json.dumps({"package":str(DESTINATION),"metrics":metrics["metrics"],
                          "normalized_weather_rows":len(weather),"selected_cells":int(chosen.cell_id.nunique()),
                          "Ida_successful_rows":int(status.status.eq("ok").sum())},indent=2),flush=True)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DESTINATION)
    args = parser.parse_args()
    build(args.run_dir, args.output_dir)
