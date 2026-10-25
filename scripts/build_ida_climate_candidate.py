"""Export a review candidate for Ida integration, not an approved scenario.

Roads are selected by feature/geometry coverage and sorted ID, before prediction.
The native package is not altered. No model fitting or test evaluation occurs.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd

from predict_climate_history import ClimateHistoryPredictor, FORMAT, sha256, utc_text

ROOT = Path(__file__).resolve().parents[1]
BOROUGHS = ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]
START = pd.Timestamp("2021-09-01T04:00:00Z")
END = pd.Timestamp("2021-09-03T04:00:00Z")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def choose_roads(static, weather, geometry_ids, per_borough):
    if weather.duplicated(["grid_id", "cell_id", "prediction_time_utc"]).any():
        raise ValueError("Duplicate weather cell-hour")
    cells = weather.groupby(["grid_id", "cell_id"], as_index=False).agg(
        hours=("prediction_time_utc", "nunique"), valid=("weather_features_valid", "all"))
    cells = cells.loc[(cells.hours == 48) & cells.valid]
    candidates = static.loc[static.static_features_valid
        & static.borough_at_midpoint.isin(BOROUGHS)
        & static.street_segment_id.isin(geometry_ids)].merge(
            cells[["grid_id", "cell_id"]], on=["grid_id", "cell_id"], validate="many_to_one")
    if candidates.street_segment_id.duplicated().any():
        raise ValueError("Duplicate physical segment")
    chosen = candidates.sort_values(["borough_at_midpoint", "street_segment_id"]).groupby(
        "borough_at_midpoint", sort=True).head(per_borough).reset_index(drop=True)
    counts = chosen.groupby("borough_at_midpoint").size().to_dict()
    if counts != {name: per_borough for name in BOROUGHS}:
        raise ValueError(f"Insufficient supported roads for selection: {counts}")
    return chosen


def build(bundle, destination, per_borough):
    predictor = ClimateHistoryPredictor(bundle)
    manifest = predictor.manifest
    release_config_path = ROOT / "config/climate_history_release_v3_02.json"
    release_config = json.loads(release_config_path.read_text())
    if (release_config["release_id"] != manifest["package_id"]
            or release_config["release_manifest_sha256"] != sha256(bundle / "release_manifest.json")):
        raise ValueError("Bundle differs from the recorded experimental release")
    if destination.exists():
        raise FileExistsError("Scenario candidate already exists; use a new version")
    processed = ROOT / "data/processed"
    dataset_dir = processed / "baseline_dataset_v1"
    dataset_manifest_path = dataset_dir / "manifest.json"
    checkpoint_path = processed / "climate_baseline_v3_runs" / manifest["checkpoint_run_id"] / "checkpoint_manifest.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    if sha256(dataset_manifest_path) != checkpoint["preparation_evidence_sha256"]["dataset_manifest"]:
        raise ValueError("Prepared dataset differs from model provenance")
    dataset_manifest = json.loads(dataset_manifest_path.read_text())
    for name, digest in dataset_manifest["input_sha256"].items():
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        if sha256(path) != digest:
            raise ValueError(f"Dataset source changed: {path}")
    static_path = processed / "baseline_street_features_v1/street_features.parquet"
    geometry_path = processed / "baseline_street_features_v1/street_segments.parquet"
    feature_manifest_path = processed / "baseline_street_features_v1/manifest.json"
    feature_manifest = json.loads(feature_manifest_path.read_text())
    network_path = ROOT / "data/raw/nyc_streets.parquet"
    network_hash = sha256(network_path)
    if network_hash != feature_manifest["input_sha256"]["data/raw/nyc_streets.parquet"]:
        raise ValueError("Current OSM source differs from static feature provenance")
    static = pd.read_parquet(static_path)
    geometry = gpd.read_parquet(geometry_path)
    if geometry.crs is None or geometry.street_segment_id.duplicated().any():
        raise ValueError("Missing CRS or duplicate segment geometry")
    valid_geometry = geometry.geometry.notna() & ~geometry.geometry.is_empty & geometry.geometry.is_valid
    weather_path = processed / "baseline_weather_v1/2021-09/weather_features.parquet"
    weather = pd.read_parquet(weather_path, filters=[
        ("prediction_time_utc", ">=", START.to_pydatetime()),
        ("prediction_time_utc", "<", END.to_pydatetime())])
    chosen = choose_roads(static, weather, geometry.loc[valid_geometry, "street_segment_id"], per_borough)
    base = predictor.base_features
    if not all(name.replace("_", "").isalnum() for name in base):
        raise ValueError("Unexpected feature names")
    con = duckdb.connect(str(dataset_dir / "dataset.duckdb"), read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        con.execute("SET threads=2")
        con.execute("SET memory_limit='2GB'")
        con.register("candidate_roads", chosen[["street_segment_id"]])
        rows = con.execute('''SELECT e.street_segment_id,
            e.prediction_time_utc::VARCHAR AS prediction_time_utc,
            e.label, e.report_count, e.source_coverage_verified, '''
            + ','.join(f'e."{name}"' for name in base)
            + ''' FROM labeled e JOIN candidate_roads USING(street_segment_id)
            WHERE e.prediction_time_utc >= ?::TIMESTAMPTZ AND e.prediction_time_utc < ?::TIMESTAMPTZ
            ORDER BY e.street_segment_id,e.prediction_time_utc''',
            [START.isoformat(), END.isoformat()]).fetchdf()
    finally:
        con.close()
    if len(rows) != len(chosen)*48 or rows.duplicated(["street_segment_id", "prediction_time_utc"]).any():
        raise ValueError("Selected streets do not have exactly 48 unique feature hours")
    rows["prediction_time_utc"] = pd.to_datetime(rows.prediction_time_utc, utc=True)
    if not np.isfinite(rows[base].to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite selected features")
    rows["request_id"] = [f"ida:{segment}:{timestamp:%Y%m%dT%H%M%SZ}"
                          for segment,timestamp in zip(rows.street_segment_id, rows.prediction_time_utc)]
    request = {"format_version": FORMAT, "package_id": manifest["package_id"], "records": [{
        "request_id": row.request_id, "street_segment_id": row.street_segment_id,
        "prediction_time_utc": utc_text(row.prediction_time_utc),
        "features": {name: float(getattr(row, name)) for name in base}}
        for row in rows.itertuples(index=False)]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".ida-candidate-", dir=destination.parent))
    try:
        write_json(temporary / "request.json", request)
        request_hash = sha256(temporary / "request.json")
        result = predictor.predict(request, request_hash)
        if any(record["status"] != "ok" for record in result["records"]):
            raise ValueError("Candidate contains inference errors; preserve unavailable cases in a separately reviewed scenario")
        write_json(temporary / "result.json", result)
        rows["label"] = rows.label.astype("Int16")
        rows.to_parquet(temporary / "feature_rows_and_label_review.parquet", index=False)
        chosen.to_parquet(temporary / "street_features.parquet", index=False)
        native = geometry.loc[geometry.street_segment_id.isin(chosen.street_segment_id)].merge(
            chosen[["street_segment_id", "borough_at_midpoint"]], on="street_segment_id", validate="one_to_one")
        native.to_crs("EPSG:4326").to_file(temporary / "native_segments.geojson", driver="GeoJSON")
        index_path = processed / "physical_street_index_v1.parquet"
        if sha256(index_path) != feature_manifest["input_sha256"]["data/processed/physical_street_index_v1.parquet"]:
            raise ValueError("Physical index differs from feature provenance")
        edge_index = pd.read_parquet(index_path)
        selected_edges = edge_index.loc[edge_index.street_segment_id.isin(chosen.street_segment_id)]
        if set(selected_edges.street_segment_id) != set(chosen.street_segment_id) or not selected_edges.street_network_sha256.eq(network_hash).all():
            raise ValueError("Directed-edge bridge does not match the selected source network")
        selected_edges.to_parquet(temporary / "directed_edge_bridge.parquet", index=False)
        # Original 311 identity stays separate from the feature-row source identity.
        mapping = processed / "physical_311_mapping_v1"
        matched_path = mapping / "matched_reports.parquet"
        label_manifest_path = processed / "hourly_report_labels_v1/manifest.json"
        label_manifest = json.loads(label_manifest_path.read_text())
        if sha256(matched_path) != label_manifest["input_sha256"]["data/processed/physical_311_mapping_v1/matched_reports.parquet"]:
            raise ValueError("Report mapping differs from label provenance")
        matched = pd.read_parquet(matched_path)
        matched["prediction_time_utc"] = pd.to_datetime(matched.created_at, utc=True).dt.floor("h")
        scenario_reports = matched.loc[matched.street_segment_id.isin(chosen.street_segment_id)
            & matched.prediction_time_utc.ge(START) & matched.prediction_time_utc.lt(END)].copy()
        scenario_reports["source_dataset_id"] = "nyc_311"
        scenario_reports["source_record_id"] = scenario_reports.unique_key.astype(str)
        scenario_reports.to_parquet(temporary / "scenario_report_lineage.parquet", index=False)
        report_counts = scenario_reports.groupby(["street_segment_id", "prediction_time_utc"]).unique_key.nunique()
        for row in rows.itertuples(index=False):
            if int(report_counts.get((row.street_segment_id, row.prediction_time_utc), 0)) != row.report_count:
                raise ValueError("Selected label report counts do not reconcile")
        history_events = pd.read_parquet(bundle / "history_events.parquet")
        history_lineage = history_events[["street_segment_id", "prediction_time_utc"]].merge(
            matched, on=["street_segment_id", "prediction_time_utc"], validate="one_to_many", how="left")
        if history_lineage.unique_key.isna().any():
            raise ValueError("Some historical feature events lack report lineage")
        history_lineage["source_dataset_id"] = "nyc_311"
        history_lineage["source_record_id"] = history_lineage.unique_key.astype(str)
        history_lineage.to_parquet(temporary / "history_label_lineage.parquet", index=False)
        # Preserve feature-row context and record actual raw-hour hashes for lookback.
        feature_lineage = rows[["request_id", "street_segment_id", "prediction_time_utc"]].merge(
            chosen[["street_segment_id", "grid_id", "cell_id"]], validate="many_to_one", on="street_segment_id")
        feature_lineage["source_dataset_id"] = "plexa_climate_prepared_features"
        feature_lineage["source_snapshot_id"] = sha256(dataset_manifest_path)
        feature_lineage["source_kind"] = "historical"
        feature_lineage["latest_rainfall_end_utc"] = feature_lineage.prediction_time_utc - pd.Timedelta(hours=3)
        feature_lineage["rain_24h_interval_start_utc"] = feature_lineage.latest_rainfall_end_utc - pd.Timedelta(hours=24)
        feature_lineage.to_parquet(temporary / "feature_row_lineage.parquet", index=False)
        raw_hours = pd.date_range(START-pd.Timedelta(hours=26), END-pd.Timedelta(hours=3), freq="h", inclusive="left")
        weather_manifest_path = weather_path.with_name("manifest.json")
        weather_manifest = json.loads(weather_manifest_path.read_text())
        source_hours = []
        for stamp in raw_hours:
            name = f"radar_{stamp:%Y%m%d_%H%M%S}.parquet"
            path = ROOT / "data/raw/radar_parquet_v2" / name
            digest = sha256(path)
            if weather_manifest["source_sha256"].get(name) != digest:
                raise ValueError(f"Raw rainfall source differs: {name}")
            source_hours.append({"file": name, "sha256": digest,
                "interval_start_utc": utc_text(stamp-pd.Timedelta(hours=1)), "interval_end_utc": utc_text(stamp)})
        write_json(temporary / "rainfall_source_hours.json", source_hours)
        labels = {"positive": int(rows.label.eq(1).sum()), "no_report_observed": int(rows.label.eq(0).sum()),
                  "unknown": int(rows.label.isna().sum())}
        alerts = sum(record["result"]["model_alert"] for record in result["records"])
        summary = {"candidate_id": destination.name, "status": "candidate_for_Anas_review_not_approved",
            "shared_contract_status": "native_format_only_revision_pending",
            "package_id": manifest["package_id"], "release_manifest_sha256": sha256(bundle / "release_manifest.json"),
            "utc_interval": [utc_text(START), utc_text(END)], "local_timezone": "America/New_York",
            "selection": "complete_weather_and_valid_geometry_then_first_IDs_per_borough_before_prediction",
            "roads_per_borough": per_borough, "physical_segments": len(chosen),
            "directed_edge_rows": len(selected_edges), "prediction_rows": len(rows),
            "label_review_counts": labels, "model_alerts": alerts,
            "source_coverage_verified_rows": int(rows.source_coverage_verified.sum()),
            "rainfall_source_hours": len(source_hours), "native_geometry_crs": "EPSG:4326",
            "native_geometry_original_crs": str(geometry.crs), "coordinate_order": "longitude,latitude",
            "model_score_calibrated": False, "ida_in_training_period": True,
            "independent_event_test": False, "lion_crosswalk_included": False,
            "no_report_label_does_not_confirm_no_flooding": True,
            "source_network_sha256": network_hash,
            "source_fingerprints": {"prepared_dataset_manifest": sha256(dataset_manifest_path),
                "street_geometry": sha256(geometry_path), "street_feature_manifest": sha256(feature_manifest_path),
                "physical_street_index": sha256(index_path), "label_manifest": sha256(label_manifest_path),
                "report_mapping": sha256(matched_path), "weather_manifest": sha256(weather_manifest_path),
                "builder": sha256(Path(__file__))}}
        readme = '''# Ida climate candidate — awaiting Anas's review

This is a reproducible candidate, not the team's accepted road/hour subset.
Roads were chosen by complete feature/geometry coverage and sorted physical ID,
without using labels or scores. The model is experimental and weak on reused
2025 development data. Ida lies in training and is a replay, not an unseen test.

request.json/result.json use the release's native interface, not an accepted
shared contract v0.2. Use the separately supplied model bundle to reproduce:

python /path/to/model-bundle/predict.py --input request.json --output replay.json

feature_rows_and_label_review.parquet includes unknown labels separately from
inference: unknown-label rows can receive scores, but cannot be evaluated as zero.
Report keys in scenario_report_lineage.parquet and history_label_lineage.parquet
are 311 identities; request IDs identify prepared feature rows. Preserve their
possible shared-311 dependence with safety; do not add a corroboration bonus.

native_segments.geojson and directed_edge_bridge.parquet retain climate/OSM
identity for Anas's reviewed LION crosswalk. They do not establish supported
traffic directions, road closure or historic road geometry validity.
rainfall_source_hours.json records all contributing raw-hour hashes and intervals;
actual publication times remain unverified. The three-hour rainfall and seven-day
history availability margins remain assumptions.

Anas must review common road coverage, road/time subset, shared schema and cascade
rules before accepting this scenario. No LION match or downstream effect is invented.
The absence of model alerts must remain visible; do not lower the threshold or
select different roads merely to produce a dramatic demonstration.
'''
        (temporary / "README.md").write_text(readme)
        summary["files"] = {p.name: sha256(p) for p in sorted(temporary.iterdir())}
        write_json(temporary / "candidate_manifest.json", summary)
        # Replay before publishing the complete directory.
        if predictor.predict(request, request_hash) != result:
            raise ValueError("Candidate replay mismatch")
        temporary.rename(destination)
        print(json.dumps({key: summary[key] for key in ["physical_segments", "prediction_rows", "label_review_counts", "model_alerts", "rainfall_source_hours"]}, indent=2))
        print(f"Candidate saved: {destination}")
        print("Road selection, LION crosswalk and shared contract remain pending Anas review.")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "data/processed/climate_releases/climate-history-v3-02-experimental-01")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed/climate_scenarios/ida-2021-history-candidate-v1")
    parser.add_argument("--roads-per-borough", type=int, default=2)
    args = parser.parse_args()
    if args.roads_per_borough < 1:
        parser.error("Road count must be positive")
    build(args.bundle.resolve(), args.output_dir.resolve(), args.roads_per_borough)


if __name__ == "__main__":
    main()
