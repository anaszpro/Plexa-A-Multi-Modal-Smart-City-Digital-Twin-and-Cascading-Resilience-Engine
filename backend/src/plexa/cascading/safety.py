"""Release archived reports by availability and link eligible warnings to roads."""

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from .engine import demo_source_fids

WARNING_RULES = ("S311-02", "S311-09", "S311-10")
IDA_SAFETY_DIRECTORY = Path("artifacts/ida_311_valid_input_v1_0_1")
HISTORICAL_SAFETY_FEED = Path("artifacts/safety_feed_2024_2025_v1/reports_2024_2025.csv")


def build_ida_safety_view(safety_directory, scenario_window):
    source_directory = Path(safety_directory) / IDA_SAFETY_DIRECTORY
    request_path = source_directory / "request.json"
    result_path = source_directory / "result.json"

    request_bytes = request_path.read_bytes()
    result_bytes = result_path.read_bytes()

    request_document = json.loads(request_bytes.decode("utf-8-sig"))
    result_document = json.loads(result_bytes.decode("utf-8-sig"))

    expected_identity = {
        "schema_version": "0.1.0",
        "operation": "safety.classify_311",
        "package_id": "safety-311-rules-v1.0.1",
        "scenario_id": "ida-2021-static-v1",
    }

    for field, expected in expected_identity.items():
        if request_document.get(field) != expected:
            raise ValueError(f"Unexpected safety source identity: {field}.")

    for field in [*expected_identity, "run_id"]:
        if request_document.get(field) != result_document.get(field):
            raise ValueError(f"Request/result mismatch: {field}.")

    request_sha256 = hashlib.sha256(request_bytes).hexdigest()
    if result_document["request_sha256"] != request_sha256:
        raise ValueError("Safety result does not match these request bytes.")

    if not request_document["records"]:
        raise ValueError("The strict Ida request is empty.")

    requests = pd.json_normalize(
        request_document["records"], sep="."
    )
    results = pd.json_normalize(
        result_document["records"], sep="."
    ).rename(columns={
        "status": "safety_processing_status",
        "reason_code": "safety_reason_code",
        "warnings": "safety_warnings",
    })

    view = requests.merge(
        results,
        on="request_id",
        how="outer",
        validate="one_to_one",
        indicator="_source_pair",
    )

    if not view["_source_pair"].eq("both").all():
        raise ValueError("Safety request/result records do not match.")

    view = view.drop(columns="_source_pair")

    for column in ["source.dataset_id", "source.record_id"]:
        view[column] = view[column].astype("string")
        if view[column].isna().any() or view[column].str.strip().eq("").any():
            raise ValueError(f"Missing source identity: {column}.")

    view["incident_id"] = (
        view["source.dataset_id"] + ":" + view["source.record_id"]
    )
    if not view["incident_id"].is_unique:
        raise ValueError("Duplicate source incident IDs require review.")

    # Preserve native time fields; create separate assumed-time columns.
    if not (
        view["time.utc"].isna().all()
        and view["time.resolution"].eq("unresolved").all()
    ):
        raise ValueError(
            "Native Ida time states differ from the inspected source."
        )

    raw_time = view["time.raw"].astype("string").str.strip()
    supported_format = raw_time.str.fullmatch(
        r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"
        r"(?:\.\d{1,9})?",
        na=False,
    )

    local_naive = pd.to_datetime(
        raw_time.where(supported_format),
        format="ISO8601",
        errors="coerce",
    )
    localized = local_naive.dt.tz_localize(
        "America/New_York",
        ambiguous="NaT",
        nonexistent="NaT",
    )

    view["created_utc_assumed"] = localized.dt.tz_convert("UTC")
    view["availability_utc_assumed"] = view["created_utc_assumed"]

    view["derived_time_status"] = "unique_assumed"
    view.loc[
        local_naive.isna(), "derived_time_status"
    ] = "held_unparseable_raw_time"
    view.loc[
        local_naive.notna() & localized.isna(),
        "derived_time_status",
    ] = "held_ambiguous_or_nonexistent_local_time"

    outside_window = (
        view["created_utc_assumed"].notna()
        & (
            view["created_utc_assumed"].lt(scenario_window["start_utc"])
            | view["created_utc_assumed"].ge(
                scenario_window["end_utc_exclusive"]
            )
        )
    )
    view.loc[outside_window, "derived_time_status"] = "outside_ida_window"

    policy_id = "ida-created-ny-release-at-creation-v0.1.0"
    view["time_policy_id"] = policy_id
    view["integration_scenario"] = "ida"

    view = view.sort_values(
        ["availability_utc_assumed", "incident_id"],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)

    metadata = {
        "view_id": "plexa-ida-safety-timed-view-v0.1.0",
        "time_policy_id": policy_id,
        "native_scenario_id": request_document["scenario_id"],
        "native_run_id": request_document["run_id"],
        "package_id": request_document["package_id"],
        "source_request_path": str(request_path),
        "source_result_path": str(result_path),
        "source_request_sha256": request_sha256,
        "source_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "timezone_assumed": "America/New_York",
        "release_at_creation_assumed": True,
        "historical_publication_times_verified": False,
        "operator_clearance_applied": False,
        "pandas_version": pd.__version__,
    }

    return view, metadata


def release_ida_reports(clock, timed_view):
    if clock["scenario"] != "ida":
        raise ValueError("This report view supports Ida only.")

    if not timed_view["integration_scenario"].eq("ida").all():
        raise ValueError("Unexpected report scenario.")

    eligible = (
        timed_view["derived_time_status"].eq("unique_assumed")
        & timed_view["availability_utc_assumed"].le(clock["as_of_utc"])
    )

    return timed_view.loc[eligible].copy().reset_index(drop=True)


def find_report_road_candidates(released_reports, lion_path, graph_roads):
    review_radius_m = 30.0
    reports = released_reports[
        ["incident_id", "safety_processing_status", "input.complaint_type",
         "location.latitude", "location.longitude"]
    ].copy()

    lat = pd.to_numeric(reports["location.latitude"], errors="coerce")
    lon = pd.to_numeric(reports["location.longitude"], errors="coerce")
    numeric_pair = lat.notna() & lon.notna()
    valid_pair = lat.between(-90, 90) & lon.between(-180, 180)

    reports["location_check"] = np.select(
        [~numeric_pair, ~valid_pair],
        ["missing_or_unreadable_pair", "out_of_coordinate_range"],
        default="outside_lion_envelope",
    )

    lion = pyogrio.read_dataframe(
        lion_path,
        layer="lion",
        columns=["SegmentID", "Street", "FeatureTyp", "SegmentTyp", "RB_Layer", "TrafDir"],
        fid_as_index=True,
    ).rename_axis("source_fid").reset_index()
    usable_line = (
        lion.geometry.geom_type.isin(["LineString", "MultiLineString"])
        & ~lion.geometry.is_empty
    )
    lion = lion.loc[usable_line].to_crs("EPSG:32618").reset_index(drop=True)
    lion["in_candidate_vehicle_graph"] = lion["source_fid"].isin(graph_roads["source_fid"])

    points = gpd.GeoSeries(
        gpd.points_from_xy(lon.loc[valid_pair], lat.loc[valid_pair]),
        index=reports.index[valid_pair],
        crs="EPSG:4326",
    ).to_crs(lion.crs)
    xmin, ymin, xmax, ymax = lion.total_bounds
    inside_envelope = points.x.between(xmin, xmax) & points.y.between(ymin, ymax)
    points = points.loc[inside_envelope]
    reports.loc[points.index, "location_check"] = "candidates_found"

    point_shapes = points.to_numpy()
    line_shapes = lion.geometry.to_numpy()
    tree = shapely.STRtree(line_shapes)

    nearest_pairs = tree.query_nearest(point_shapes, all_matches=True)
    nearby_pairs = tree.query(
        point_shapes, predicate="dwithin", distance=review_radius_m
    )
    pairs = pd.DataFrame(
        np.concatenate([nearest_pairs, nearby_pairs], axis=1).T,
        columns=["point_position", "line_position"],
    ).drop_duplicates()

    point_positions = pairs["point_position"].to_numpy()
    line_positions = pairs["line_position"].to_numpy()
    candidates = lion.iloc[line_positions].drop(columns="geometry").reset_index(drop=True)
    candidates.insert(
        0, "incident_id", reports.loc[points.index, "incident_id"].to_numpy()[point_positions]
    )
    candidates["distance_m"] = shapely.distance(
        point_shapes[point_positions], line_shapes[line_positions]
    )
    candidates["within_review_window"] = candidates["distance_m"].le(review_radius_m)

    nearest_distance = candidates.groupby("incident_id")["distance_m"].transform("min")
    nearest = candidates.loc[candidates["distance_m"].eq(nearest_distance)]
    nearby = candidates.loc[candidates["within_review_window"]]

    nearest_stats = nearest.groupby("incident_id").agg(
        nearest_lion_distance_m=("distance_m", "min"),
        nearest_has_graph_row=("in_candidate_vehicle_graph", "any"),
    )
    nearby_ids = nearby.groupby("incident_id")["SegmentID"].nunique().rename(
        "nearby_lion_ids"
    )
    reports = reports.join(nearest_stats, on="incident_id").join(nearby_ids, on="incident_id")
    reports["nearby_lion_ids"] = reports["nearby_lion_ids"].fillna(0).astype(int)
    return reports, candidates


def compare_report_road_candidates(review, candidates, graph_roads):
    roads = graph_roads.loc[
        graph_roads["source_fid"].isin(candidates["source_fid"])
    ].copy()

    # Collapse equivalent source rows for this proximity check.
    # Use both endpoint levels, regardless of the row's travel direction.
    roads["endpoint_pair"] = [
        tuple(sorted(((start, start_level), (end, end_level))))
        for start, start_level, end, end_level in zip(
            roads["NodeIDFrom"], roads["NodeLevelF"],
            roads["NodeIDTo"], roads["NodeLevelT"],
        )
    ]
    roads["shape_key"] = shapely.to_wkb(
        shapely.normalize(roads.geometry.to_numpy())
    )
    physical_fields = [
        "lion_segment_id", "endpoint_pair", "shape_key",
        "FeatureTyp", "SegmentTyp", "RB_Layer",
    ]
    group_number = roads.groupby(physical_fields, sort=False, dropna=False).ngroup()
    roads["candidate_group"] = "graph:" + group_number.astype("string")

    pairs = candidates[
        ["incident_id", "source_fid", "distance_m", "in_candidate_vehicle_graph"]
    ].merge(
        roads[["source_fid", "candidate_group"]],
        on="source_fid",
        how="left",
        validate="many_to_one",
    )
    # Keep lines outside the vehicle graph in the comparison.
    pairs["candidate_group"] = pairs["candidate_group"].fillna(
        "outside:" + pairs["source_fid"].astype("string")
    )

    ranked = (
        pairs.groupby(["incident_id", "candidate_group"], as_index=False)
        .agg(
            distance_m=("distance_m", "min"),
            in_graph=("in_candidate_vehicle_graph", "any"),
        )
        .sort_values("distance_m", kind="stable")
    )
    ranked["rank"] = ranked.groupby("incident_id").cumcount()
    closest = ranked.loc[ranked["rank"].eq(0)].set_index("incident_id")
    runner_up = ranked.loc[ranked["rank"].eq(1)].set_index("incident_id")

    comparison = review.join(
        closest[["distance_m", "in_graph"]].rename(columns={
            "distance_m": "closest_candidate_m",
            "in_graph": "closest_in_graph",
        }),
        on="incident_id",
    ).join(
        runner_up["distance_m"].rename("next_candidate_m"),
        on="incident_id",
    )
    return comparison


def report_location_probe(comparison, max_distance_m, min_gap_m):
    # For these cutoffs, a competitor outside the 30 m search window
    # is already far enough away to pass the gap check.
    gap_m = (
        comparison["next_candidate_m"].fillna(np.inf)
        - comparison["closest_candidate_m"]
    )
    return (
        comparison["closest_in_graph"].eq(True)
        & comparison["closest_candidate_m"].le(max_distance_m)
        & gap_m.ge(min_gap_m)
    )


def release_2024_safety_reports(feed_path, clock):
    if clock["scenario"] != "2024":
        raise ValueError("Use the 2024 replay clock for this report view.")

    fields = [
        "unique_key", "capture_year", "created_date",
        "normalized_created_utc_assumed", "normalized_time_status",
        "availability_utc_assumed", "complaint_type", "descriptor",
        "incident_address", "street_name", "borough", "latitude", "longitude",
        "safety_processing_status", "matched_rule_id", "hazard_type_unverified",
        "rule_score_value", "rule_score_kind", "rule_score_calibrated",
        "severity", "severity_basis", "hazard_verification",
        "source_dataset_api_id", "source_snapshot_id",
    ]

    released_chunks = []
    counts = {"year_rows": 0, "after_cutoff": 0, "unresolved_availability": 0}
    cutoff = clock["as_of_utc"]

    for chunk in pd.read_csv(
        feed_path,
        usecols=fields,
        dtype={"unique_key": "string", "capture_year": "Int64"},
        chunksize=50_000,
    ):
        year = chunk.loc[chunk["capture_year"].eq(2024)].copy()

        # Use the feed's supplied availability, which is already expressed in UTC.
        availability = pd.to_datetime(
            year["availability_utc_assumed"],
            format="ISO8601",
            utc=True,
            errors="coerce",
        )
        counts["year_rows"] += len(year)
        counts["after_cutoff"] += int(availability.gt(cutoff).sum())
        counts["unresolved_availability"] += int(availability.isna().sum())

        year["availability_utc_assumed"] = availability
        released_chunks.append(year.loc[availability.le(cutoff)])

    reports = pd.concat(released_chunks, ignore_index=True)
    source_ids = reports["unique_key"].str.strip()
    if source_ids.isna().any() or source_ids.eq("").any() or source_ids.duplicated().any():
        raise ValueError("Expected a unique NYC 311 key for each released report.")

    reports.insert(0, "incident_id", "nyc_311:" + source_ids)
    reports["integration_scenario"] = "2024"
    reports = reports.rename(columns={
        "complaint_type": "input.complaint_type",
        "descriptor": "input.descriptor",
        "latitude": "location.latitude",
        "longitude": "location.longitude",
        "matched_rule_id": "result.rule_id",
        "hazard_type_unverified": "result.hazard_type",
    })
    return reports, counts


def match_demo_warning_links(reports, lion_path, graph_roads):
    """Link approved warning types using the 2 m distance and 3 m gap."""
    reports = reports.loc[
        reports["safety_processing_status"].eq("ok") & reports["result.rule_id"].isin(WARNING_RULES)
    ]
    if reports.empty:
        return pd.DataFrame({
            "incident_id": pd.Series(dtype="string"),
            "source_fid": pd.Series(dtype="int64"),
            "result.rule_id": pd.Series(dtype="string"),
        })

    review, candidates = find_report_road_candidates(
        reports, lion_path, graph_roads
    )
    review = compare_report_road_candidates(
        review, candidates, graph_roads
    )
    passed = review.loc[
        report_location_probe(review, max_distance_m=2, min_gap_m=3),
        ["incident_id", "closest_candidate_m"],
    ]
    rows = candidates.loc[
        candidates["in_candidate_vehicle_graph"],
        ["incident_id", "source_fid", "distance_m"],
    ].merge(passed, on="incident_id", validate="many_to_one")

    closest = rows.loc[
        np.isclose(rows["distance_m"], rows["closest_candidate_m"], rtol=0, atol=1e-6),
        ["incident_id", "source_fid"],
    ].drop_duplicates()
    if closest["incident_id"].nunique() != len(passed):
        raise ValueError("A report passed the location check without a matching LION row.")

    return closest.loc[closest["source_fid"].isin(demo_source_fids(graph_roads))].merge(
        reports[["incident_id", "result.rule_id"]],
        on="incident_id",
        validate="many_to_one",
    )
