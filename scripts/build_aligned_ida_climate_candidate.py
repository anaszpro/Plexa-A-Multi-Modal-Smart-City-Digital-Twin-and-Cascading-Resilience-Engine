"""Build Anas's draft LION-aligned Ida replay without changing the model.

Mapping is selected using geometry before inference. Ambiguous, partial or
unsupported mappings stop publication rather than silently choosing a road.
The shared builder verifies dataset provenance, lineage and exact replay.
"""
import argparse
import json
from pathlib import Path
import shutil

import geopandas as gpd
import pandas as pd

from build_ida_climate_candidate import ROOT, START, END, build
from predict_climate_history import sha256, utc_text

METRIC_CRS = "EPSG:32618"
SEARCH_M = 20
BUFFER_M = 10
MIN_COVERAGE_PERCENT = 95
REQUEST_FILES = {
    "requested_lion_roads.geojson", "request_spec.json", "message_for_kaleem.txt"
}


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def geometry_candidates(lion, climate):
    """Retain all nearby alternatives; rank without labels or model scores."""
    if lion.crs is None or climate.crs is None:
        raise ValueError("Missing geometry CRS")
    if climate.street_segment_id.duplicated().any() or lion.lion_segment_id.duplicated().any():
        raise ValueError("Duplicate road geometry IDs")
    lion = lion.to_crs(METRIC_CRS)
    climate = climate.to_crs(METRIC_CRS)
    for frame in [lion, climate]:
        if (frame.geometry.isna().any() or frame.geometry.is_empty.any()
                or (~frame.geometry.is_valid).any() or (frame.geometry.length <= 0).any()):
            raise ValueError("Invalid road geometry")
    records = []
    for road in lion.itertuples(index=False):
        positions = climate.sindex.query(road.geometry.buffer(SEARCH_M), predicate="intersects")
        for segment in climate.iloc[positions].itertuples(index=False):
            records.append({
                "lion_segment_id": str(road.lion_segment_id),
                "street_name": road.street_name,
                "supported_mobility_directions": road.supported_mobility_directions,
                "street_segment_id": segment.street_segment_id,
                "minimum_distance_m": road.geometry.distance(segment.geometry),
                "lion_length_m": road.geometry.length,
                "climate_length_m": segment.geometry.length,
                "lion_covered_percent": 100 * road.geometry.intersection(
                    segment.geometry.buffer(BUFFER_M)).length / road.geometry.length,
                "climate_covered_percent": 100 * segment.geometry.intersection(
                    road.geometry.buffer(BUFFER_M)).length / segment.geometry.length,
            })
    if not records:
        raise ValueError("No geometry candidates; no export published")
    candidates = pd.DataFrame(records).sort_values(
        ["lion_segment_id", "lion_covered_percent", "climate_covered_percent",
         "minimum_distance_m", "street_segment_id"],
        ascending=[True, False, False, True, True]).reset_index(drop=True)
    candidates["candidate_rank"] = candidates.groupby("lion_segment_id").cumcount() + 1
    candidates["strong_mutual_overlap"] = (
        candidates.lion_covered_percent.ge(MIN_COVERAGE_PERCENT)
        & candidates.climate_covered_percent.ge(MIN_COVERAGE_PERCENT))
    return candidates


def propose_mapping(candidates, requested_ids):
    proposed = candidates.loc[candidates.strong_mutual_overlap].copy()
    counts = proposed.groupby("lion_segment_id").size().reindex(requested_ids, fill_value=0)
    if not counts.eq(1).all():
        raise ValueError(f"Mapping needs manual review; no partial/ambiguous export published: {counts.to_dict()}")
    if proposed.street_segment_id.duplicated().any():
        raise ValueError("Shared native segments require explicit many-to-one review")
    proposed["mapping_status"] = "proposed_pending_Anas_review"
    proposed["mapping_cardinality"] = "one_to_one_proposed"
    proposed["traffic_direction_alignment_verified"] = False
    return proposed


def export(request_dir, bundle, destination):
    manifest = read_json(request_dir / "manifest.json")
    if set(manifest["files"]) != REQUEST_FILES:
        raise ValueError("Unexpected request manifest contents")
    for name, expected in manifest["files"].items():
        path = request_dir / name
        if path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
            raise ValueError(f"Request integrity failure: {name}")
    spec = read_json(request_dir / "request_spec.json")
    release = read_json(ROOT / "config/climate_history_release_v3_02.json")
    experiment = read_json(bundle / "experiment.json")
    if (release["release_id"] != spec["existing_climate_package_id"]
            or sha256(bundle / "release_manifest.json") != spec["existing_climate_manifest_sha256"]
            or spec["model_policy"]["alert_threshold"] != release["threshold"]
            or spec["interface"] != release["native_format_version"]
            or spec["preparation_requirements"]["base_features"] != release["native_external_features"]
            or spec["preparation_requirements"]["history_policy"] != experiment["history_policy"]
            or spec["model_policy"]["retrain"] is not False
            or spec["model_policy"]["change_threshold"] is not False):
        raise ValueError("Request differs from unchanged release policies")
    hours = pd.date_range(START, END, freq="h", inclusive="left")
    if not pd.DatetimeIndex(pd.to_datetime(spec["requested_hour_starts_utc"], utc=True)).equals(hours):
        raise ValueError("Unexpected requested hours")
    if spec["utc_window_end_exclusive"] != [utc_text(START), utc_text(END)]:
        raise ValueError("Unexpected replay interval")
    if spec["geometry"]["crs"] != "EPSG:4326":
        raise ValueError("Unexpected requested CRS")
    lion = gpd.read_file(request_dir / "requested_lion_roads.geojson")
    requested_ids = spec["requested_lion_segments"]
    if set(lion.lion_segment_id.astype(str)) != set(requested_ids):
        raise ValueError("Requested IDs differ from geometries")
    geometry = gpd.read_parquet(ROOT / "data/processed/baseline_street_features_v1/street_segments.parquet")
    candidates = geometry_candidates(lion, geometry)
    proposed = propose_mapping(candidates, requested_ids)
    bridge = pd.read_parquet(bundle / "street_cell_bridge.parquet")

    def select(static, weather, geometry_ids):
        chosen = proposed[["street_segment_id"]].merge(
            static, on="street_segment_id", how="left", validate="one_to_one")
        if (not chosen.static_features_valid.eq(True).all()
                or not chosen.street_segment_id.isin(geometry_ids).all()):
            raise ValueError("Missing static/geometry support; export stopped")
        support = chosen[["street_segment_id", "grid_id", "cell_id"]].merge(
            bridge[["street_segment_id", "grid_id", "cell_id"]],
            on=["street_segment_id", "grid_id", "cell_id"], validate="one_to_one")
        if len(support) != len(chosen):
            raise ValueError("Selected segment/grid differs from model bridge")
        cell_coverage = weather.groupby(["grid_id", "cell_id"], as_index=False).agg(
            hours=("prediction_time_utc", "nunique"), valid=("weather_features_valid", "all"))
        support = chosen.merge(cell_coverage, on=["grid_id", "cell_id"], how="left", validate="many_to_one")
        if not (support.hours.eq(48) & support.valid.eq(True)).all():
            raise ValueError("Incomplete hourly rainfall coverage; export stopped")
        return chosen.sort_values("street_segment_id").reset_index(drop=True)

    def finalize(directory, summary, rows, chosen):
        proposed.to_csv(directory / "proposed_lion_crosswalk.csv", index=False)
        candidates.to_csv(directory / "mapping_candidates.csv", index=False)
        for name in ["manifest.json", *sorted(REQUEST_FILES)]:
            target = "input_request_manifest.json" if name == "manifest.json" else name
            shutil.copy2(request_dir / name, directory / target)
        coverage = rows[["request_id", "street_segment_id", "prediction_time_utc",
                         "label", "report_count", "source_coverage_verified"]].merge(
            proposed[["lion_segment_id", "street_segment_id"]],
            on="street_segment_id", validate="many_to_one")
        coverage["coverage_status"] = "available"
        coverage["exclusion_reason"] = pd.Series(None, index=coverage.index, dtype="string")
        coverage["model_bridge_verified"] = True
        coverage["static_inputs_valid"] = True
        coverage["rainfall_inputs_valid"] = True
        coverage.to_parquet(directory / "coverage_ledger.parquet", index=False)
        coverage.to_csv(directory / "coverage_ledger.csv", index=False)
        coverage.iloc[:0].to_csv(directory / "exclusions.csv", index=False)
        summary.update({
            "request_id": spec["request_id"],
            "selection": spec["selection"],
            "roads_per_borough": None,
            "requested_lion_ids": requested_ids,
            "lion_crosswalk_included": True,
            "shared_road_subset_approved": False,
            "mapping_status": "proposed_pending_Anas_review",
            "mapping_policy": {"crs": METRIC_CRS, "search_distance_m": SEARCH_M,
                               "overlap_buffer_m": BUFFER_M, "minimum_mutual_overlap_percent": MIN_COVERAGE_PERCENT,
                               "ambiguous_or_partial_mapping": "stop_for_manual_review",
                               "direction_alignment_verified": False},
            "expected_segment_hours": len(chosen) * 48,
            "available_segment_hours": len(rows),
            "excluded_segment_hours": 0,
            "target_geometry_historical_validity_verified": False,
        })
        summary["source_fingerprints"]["aligned_builder"] = sha256(Path(__file__))
        summary["source_fingerprints"]["input_request_manifest"] = sha256(request_dir / "manifest.json")
        existing_readme = (directory / "README.md").read_text()
        # Replace selection-specific paragraphs; preserve inference/lineage limitations.
        existing_readme = existing_readme.replace(
            "Roads were chosen by complete feature/geometry coverage and sorted physical ID,\nwithout using labels or scores.",
            "Roads were requested by Anas using safety-report and mobility coverage.\nClimate physical matches were proposed using geometry before climate inference.")
        existing_readme = existing_readme.replace(
            "identity for Anas's reviewed LION crosswalk.",
            "identity alongside the proposed LION crosswalk, pending Anas's review.")
        existing_readme = existing_readme.replace(
            "No LION match or downstream effect is invented.",
            "The proposed LION correspondence does not establish direction-specific traffic support or a downstream effect.")
        (directory / "README.md").write_text(existing_readme + """

## Aligned roads and coverage

See proposed_lion_crosswalk.csv for four proposed physical-to-LION matches,
mapping_candidates.csv for all nearby alternatives, and requested_lion_roads.geojson
for the original target geometries. Buffer overlap is geometry evidence, not
proof of historic alignment or traffic-direction equivalence. Short crossing or
endpoint candidates remain in the alternative ledger; none are silently merged.
Partial or ambiguous mappings require a separately reviewed mapping policy.

coverage_ledger.csv/parquet retain all 192 requested segment-hours, including
unknown report labels. All have valid inference inputs; exclusions.csv is empty
apart from its header. Unknown labels are not missing input coverage.
This four-road request was selected using safety reports, and Ida overlaps model
training. Neither road selection nor this replay constitutes independent evaluation.
No retraining, threshold changes, positive-alert requirement or fabricated inputs.
The model bundle is supplied separately and must retain its manifest-listed files.
""")

    build(bundle, destination, None, road_selector=select,
          candidate_finalizer=finalize, request_id_prefix="ida-aligned")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-dir", required=True, type=Path)
    parser.add_argument("--bundle", type=Path,
                        default=ROOT / "data/processed/climate_releases/climate-history-v3-02-experimental-01")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data/processed/climate_scenarios/ida-2021-lion-aligned-candidate-v1")
    args = parser.parse_args()
    export(args.request_dir.resolve(), args.bundle.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
