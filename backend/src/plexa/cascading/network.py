"""Prepare the approved road correspondences and the level-aware LION network."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import Geod, Transformer


def load_common_contexts(locations):
    """Read the accepted Mobility registry and Climate crosswalk."""
    subset_path = (
        locations["Mobility assets"] / "approved_joint_contexts.json"
    )
    registry_path = (
        locations["Mobility assets"] / "road_contexts.json"
    )
    crosswalk_path = (
        locations["Mobility assets"] / "approved_joint_crosswalk.csv"
    )

    with subset_path.open("r", encoding="utf-8-sig") as handle:
        subset_document = json.load(handle)

    with registry_path.open("r", encoding="utf-8-sig") as handle:
        registry_document = json.load(handle)

    crosswalk = pd.read_csv(
        crosswalk_path,
        dtype=str,
        keep_default_na=False,
    )

    context_keys = ["segment_id", "direction", "road_type"]

    approved_contexts = pd.DataFrame(
        subset_document["contexts"]
    )
    serving_contexts = pd.DataFrame(
        registry_document["contexts"]
    )

    # Preserve IDs as strings, including any leading zeros.
    for frame in [approved_contexts, serving_contexts]:
        for column in context_keys:
            frame[column] = frame[column].astype("string")

    if (
        subset_document["serving_context_id"]
        != registry_document["serving_context_id"]
    ):
        raise ValueError("Subset and serving registry reference different contexts.")

    if not crosswalk["acceptance_status"].eq(
        "approved_initial_common_coverage"
    ).all():
        raise ValueError("Crosswalk contains rows outside the accepted common scope.")


    # Add serving metadata to the approved contexts.
    registry_columns = context_keys + ["source", "trafdir"]

    common_road_contexts = approved_contexts[
        context_keys
    ].merge(
        serving_contexts[registry_columns].rename(
            columns={
                "source": "registry_source",
                "trafdir": "registry_trafdir",
            }
        ),
        on=context_keys,
        how="left",
        validate="one_to_one",
        indicator="_registry_match",
    )

    if not common_road_contexts["_registry_match"].eq("both").all():
        raise ValueError("An approved context has no matching serving entry.")

    common_road_contexts = common_road_contexts.drop(
        columns="_registry_match"
    )


    # Add the accepted climate correspondence.
    # The release's LION reference is retained as a reference:
    # it is not automatically treated as a resolved current routing ID.
    climate_correspondence = crosswalk[
        [
            "mobility_segment_id",
            "street_segment_id",
            "lion_segment_id",
            "mobility_lion_key_used",
        ]
    ].rename(columns={
        "mobility_segment_id": "segment_id",
        "street_segment_id": "climate_street_segment_id",
        "lion_segment_id": "release_lion_reference_id",
        "mobility_lion_key_used": "lion_resolution_method",
    })

    climate_correspondence["segment_id"] = (
        climate_correspondence["segment_id"].astype("string")
    )

    common_road_contexts = common_road_contexts.merge(
        climate_correspondence,
        on="segment_id",
        how="left",
        validate="many_to_one",
        indicator="_climate_match",
    )

    if not common_road_contexts["_climate_match"].eq("both").all():
        raise ValueError("An approved context has no climate correspondence.")

    common_road_contexts = common_road_contexts.drop(
        columns="_climate_match"
    )

    common_road_contexts["serving_context_id"] = (
        subset_document["serving_context_id"]
    )
    common_road_contexts["route_edge_mapping_status"] = "pending_review"

    common_road_contexts = (
        common_road_contexts
        .sort_values(context_keys)
        .reset_index(drop=True)
    )

    # Guard the cardinality of our new joins.
    assert len(common_road_contexts) == subset_document["mobility_contexts"]
    assert (
        common_road_contexts["segment_id"].nunique()
        == subset_document["physical_roads"]
    )
    assert (
        common_road_contexts["climate_street_segment_id"].nunique()
        == subset_document["physical_roads"]
    )

    road_alignment_metadata = {
        "alignment_id": "plexa-common-road-contexts-v0.1.0",
        "subset_id": subset_document["subset_id"],
        "approval_basis": subset_document["approval_basis"],
        "serving_context_id": subset_document["serving_context_id"],
        "historical_geometry_validity_verified": (
            subset_document["historical_geometry_validity_verified"]
        ),
        "direction_specific_hazard_equivalence_verified": (
            subset_document["direction_specific_hazard_equivalence_verified"]
        ),
        "source_files": [
            str(subset_path),
            str(registry_path),
            str(crosswalk_path),
        ],
    }

    return common_road_contexts, road_alignment_metadata


def build_lion_bridge_candidates(shared_contexts, locations):
    inspection_candidates = {
        "151662": "0349496",
        "156485": "0289311",
        "22023": "0312557",
        "38055": "0334846",
        "47443": "0294163",
        "97278": "0344174",
    }

    roads = shared_contexts[
        ["segment_id", "release_lion_reference_id", "lion_resolution_method"]
    ].drop_duplicates().copy()
    roads["segment_id"] = roads["segment_id"].astype("string").str.strip()

    if len(roads) != 441 or roads["segment_id"].duplicated().any():
        raise ValueError("Expected one road reference for each of the 441 shared roads.")

    inspected_id = roads["segment_id"].map(inspection_candidates)
    roads["candidate_source"] = np.where(
        inspected_id.notna(), "inspection_candidate", "release_reference"
    )
    roads["candidate_lion_id"] = inspected_id.fillna(
        roads["release_lion_reference_id"]
    ).astype("string").str.strip().str.zfill(7)

    lion = pyogrio.read_dataframe(
        locations["LION geodatabase"],
        layer="lion",
        columns=[
            "SegmentID", "LegacyID", "Street", "FeatureTyp", "SegmentTyp",
            "RB_Layer", "TrafDir", "Status", "NodeIDFrom", "NodeIDTo",
            "NodeLevelF", "NodeLevelT",
        ],
        fid_as_index=True,
    )
    lion["candidate_lion_id"] = (
        lion["SegmentID"].astype("string").str.strip().str.zfill(7)
    )
    lion = lion.loc[lion["candidate_lion_id"].isin(roads["candidate_lion_id"])]
    lion = lion.rename_axis("source_fid").reset_index().to_crs("EPSG:32618")

    # Reuse the temporary vehicle screen from the network inspection.
    endpoints = lion[["NodeIDFrom", "NodeIDTo", "NodeLevelF", "NodeLevelT"]]
    endpoints = endpoints.astype("string").fillna("")
    lion["passes_inspection_screen"] = (
        lion["TrafDir"].isin(["W", "A", "T"])
        & lion["RB_Layer"].isin(["R", "B"])
        & pd.to_numeric(lion["Status"], errors="coerce").eq(2)
        & ~endpoints.isin(["", "*"]).any(axis=1)
        & lion.geometry.notna()
        & ~lion.geometry.is_empty
    )

    frozen = pd.read_parquet(
        locations["Frozen mobility roads"],
        columns=["segment_id", "lion_wkt_2263"],
    )
    frozen["segment_id"] = frozen["segment_id"].astype("string").str.strip()
    frozen = frozen.loc[
        frozen["segment_id"].isin(roads["segment_id"])
    ].dropna(subset=["lion_wkt_2263"]).drop_duplicates().copy()

    frozen_geometry = gpd.GeoSeries.from_wkt(
        frozen["lion_wkt_2263"], crs="EPSG:2263"
    ).to_crs("EPSG:32618")
    frozen["frozen_geometry_m"] = frozen_geometry
    frozen = frozen.loc[~frozen_geometry.is_empty].copy()
    frozen["normalized_wkb"] = shapely.to_wkb(
        shapely.normalize(frozen["frozen_geometry_m"].to_numpy())
    )
    frozen = frozen.drop_duplicates(["segment_id", "normalized_wkb"])
    frozen_counts = frozen.groupby("segment_id").size().rename("frozen_shapes")
    single_shape = frozen.loc[~frozen["segment_id"].duplicated(keep=False)]

    # Keep each LION source row so we can review duplicates and directions.
    candidates = lion.merge(roads, on="candidate_lion_id", how="inner")
    candidates = candidates.merge(
        single_shape[["segment_id", "frozen_geometry_m"]],
        on="segment_id",
        how="left",
        validate="many_to_one",
    )
    comparable = (
        candidates["passes_inspection_screen"]
        & candidates["frozen_geometry_m"].notna()
    )
    candidates["hausdorff_m"] = np.nan
    candidates.loc[comparable, "hausdorff_m"] = shapely.hausdorff_distance(
        candidates.loc[comparable, "geometry"].to_numpy(),
        candidates.loc[comparable, "frozen_geometry_m"].to_numpy(),
        densify=0.25,
    )
    # The 1 cm probe measures near-identical shapes for review.
    candidates["within_1cm_probe"] = candidates["hausdorff_m"].le(0.01)

    candidate_counts = candidates.groupby("segment_id").agg(
        lion_rows=("source_fid", "size"),
        screened_rows=("passes_inspection_screen", "sum"),
        close_rows=("within_1cm_probe", "sum"),
        min_hausdorff_m=("hausdorff_m", "min"),
    )
    bridge = roads.join(frozen_counts, on="segment_id").join(
        candidate_counts, on="segment_id"
    )
    count_columns = ["frozen_shapes", "lion_rows", "screened_rows", "close_rows"]
    bridge[count_columns] = bridge[count_columns].fillna(0).astype(int)
    bridge["geometry_check"] = np.select(
        [
            bridge["lion_rows"].eq(0),
            bridge["screened_rows"].eq(0),
            bridge["frozen_shapes"].ne(1),
            bridge["close_rows"].eq(0),
            bridge["close_rows"].gt(1),
        ],
        [
            "lion_id_not_found",
            "no_row_passed_screen",
            "frozen_shape_needs_review",
            "shape_difference_needs_review",
            "multiple_close_source_rows",
        ],
        default="one_close_source_row",
    )
    return bridge, candidates


def lion_allowed_traversals(row):
    forward = (
        str(row.NodeIDFrom).strip(), str(row.NodeLevelF).strip(),
        str(row.NodeIDTo).strip(), str(row.NodeLevelT).strip(),
    )
    reverse = forward[2:] + forward[:2]

    if row.TrafDir == "W":
        return (forward,)
    if row.TrafDir == "A":
        return (reverse,)
    if row.TrafDir == "T":
        return tuple(sorted({forward, reverse}))
    raise ValueError(f"Unexpected LION travel direction {row.TrafDir!r}")


def build_lion_traversal_candidates(road_bridge, road_candidates):
    close_rows = road_candidates.loc[road_candidates["within_1cm_probe"]].copy()
    close_rows["allowed_traversals"] = [
        lion_allowed_traversals(row)
        for row in close_rows.itertuples(index=False)
    ]
    close_rows["geometry_key"] = shapely.to_wkb(
        shapely.normalize(close_rows.geometry.to_numpy())
    )

    edge_fields = [
        "segment_id", "candidate_lion_id", "allowed_traversals",
        "geometry_key", "FeatureTyp", "SegmentTyp", "RB_Layer",
    ]
    edge_variants = (
        close_rows.groupby(edge_fields, sort=False, dropna=False, as_index=False)
        .agg(source_fids=("source_fid", tuple))
    )
    variant_counts = edge_variants.groupby("segment_id").agg(
        edge_variants=("candidate_lion_id", "size"),
        traversal_patterns=("allowed_traversals", "nunique"),
        geometry_shapes=("geometry_key", "nunique"),
    )

    review = road_bridge.join(variant_counts, on="segment_id")
    counts = ["edge_variants", "traversal_patterns", "geometry_shapes"]
    review[counts] = review[counts].fillna(0).astype(int)
    review["edge_review"] = np.select(
        [
            review["edge_variants"].eq(0),
            review["traversal_patterns"].gt(1),
            review["geometry_shapes"].gt(1),
            review["edge_variants"].gt(1),
            review["close_rows"].gt(1),
        ],
        [
            "no_close_candidate",
            "different_travel_directions_or_endpoints",
            "different_geometry",
            "different_road_classification",
            "equivalent_source_rows",
        ],
        default="single_source_row",
    )

    ready = review.loc[
        review["edge_variants"].eq(1), ["segment_id", "candidate_source"]
    ]
    traversals = edge_variants.merge(
        ready, on="segment_id", how="inner", validate="many_to_one"
    ).explode("allowed_traversals", ignore_index=True)

    endpoint_fields = [
        "from_node_id", "from_node_level", "to_node_id", "to_node_level",
    ]
    traversals[endpoint_fields] = pd.DataFrame(
        traversals["allowed_traversals"].tolist(),
        columns=endpoint_fields,
        index=traversals.index,
    )
    return review, edge_variants, traversals


def check_traversal_headings(traversals, lion_path):
    arcs = traversals.copy()
    from_keys = arcs["from_node_id"].astype("string").str.zfill(7)
    to_keys = arcs["to_node_id"].astype("string").str.zfill(7)

    nodes = pyogrio.read_dataframe(lion_path, layer="node", columns=["NODEID"])
    nodes["node_key"] = nodes["NODEID"].astype("string").str.zfill(7)
    nodes = nodes.loc[nodes["node_key"].isin(pd.concat([from_keys, to_keys]))]
    nodes = nodes.set_index("node_key", verify_integrity=True).to_crs("EPSG:32618")
    from_points = nodes.geometry.reindex(from_keys).to_numpy()
    to_points = nodes.geometry.reindex(to_keys).to_numpy()

    lines = shapely.line_merge(shapely.from_wkb(arcs["geometry_key"].to_numpy()))
    first = shapely.get_point(lines, 0)
    last = shapely.get_point(lines, -1)

    first_from = shapely.distance(first, from_points)
    last_from = shapely.distance(last, from_points)
    first_to = shapely.distance(first, to_points)
    last_to = shapely.distance(last, to_points)

    forward = (first_from < last_from) & (last_to < first_to)
    reverse = (last_from < first_from) & (first_to < last_to)
    oriented = forward | reverse

    shape_from = np.where(forward, first, last)
    shape_to = np.where(forward, last, first)
    arcs["endpoint_gap_m"] = np.where(
        forward,
        np.maximum(first_from, last_to),
        np.where(reverse, np.maximum(last_from, first_to), np.nan),
    )

    transform = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    geod = Geod(ellps="WGS84")
    compass = np.array(["NB", "EB", "SB", "WB"])

    def bearing(start, end):
        start_lon, start_lat = transform.transform(shapely.get_x(start), shapely.get_y(start))
        end_lon, end_lat = transform.transform(shapely.get_x(end), shapely.get_y(end))
        azimuth, _, distance = geod.inv(start_lon, start_lat, end_lon, end_lat)
        return np.where(distance > 0, azimuth % 360, np.nan)

    def nearest_cardinal(angles):
        labels = np.full(len(angles), None, dtype=object)
        diagonal = np.isclose(angles % 90, 45, atol=1e-7, rtol=0)
        usable = np.isfinite(angles) & ~diagonal
        positions = np.floor((angles[usable] + 45) / 90).astype(int) % 4
        labels[usable] = compass[positions]
        return labels

    arcs["node_bearing_deg"] = bearing(from_points, to_points)
    arcs["shape_bearing_deg"] = np.where(oriented, bearing(shape_from, shape_to), np.nan)
    arcs["node_direction"] = nearest_cardinal(arcs["node_bearing_deg"].to_numpy())
    arcs["shape_direction"] = nearest_cardinal(arcs["shape_bearing_deg"].to_numpy())
    arcs["heading_check"] = np.select(
        [
            ~oriented,
            arcs["node_direction"].isna() | arcs["shape_direction"].isna(),
            arcs["node_direction"].ne(arcs["shape_direction"]),
        ],
        ["endpoints_need_review", "heading_needs_review", "headings_disagree"],
        default="headings_agree",
    )
    return arcs


def match_context_directions(common_contexts, traversals):
    """Retain unique compass matches and keep unresolved directions visible."""
    context_keys = ["segment_id", "direction", "road_type"]
    contexts = common_contexts[context_keys].copy()

    edge_fields = [
        "candidate_lion_id", "candidate_source", "source_fids",
        "from_node_id", "from_node_level", "to_node_id", "to_node_level",
        "shape_bearing_deg", "endpoint_gap_m",
    ]
    agreeing_arcs = traversals.loc[
        traversals["heading_check"].eq("headings_agree"),
        ["segment_id", "shape_direction"] + edge_fields,
    ].rename(columns={"shape_direction": "direction"})

    matches = contexts.merge(agreeing_arcs, on=["segment_id", "direction"], how="inner")
    match_counts = matches.groupby(context_keys, dropna=False).size().rename("match_count")
    single_matches = matches.loc[
        ~matches.duplicated(context_keys, keep=False), context_keys + edge_fields
    ]

    context_lion_bridge = (
        contexts.join(match_counts, on=context_keys)
        .merge(single_matches, on=context_keys, how="left", validate="one_to_one")
    )
    bridge = context_lion_bridge
    bridge["match_count"] = bridge["match_count"].fillna(0).astype(int)
    bridge["direction_review"] = np.select(
        [
            ~bridge["direction"].isin(["NB", "EB", "SB", "WB"]),
            bridge["match_count"].eq(0),
            bridge["match_count"].gt(1),
        ],
        ["label_needs_review", "no_agreeing_traversal", "multiple_agreeing_traversals"],
        default="one_proposed_match",
    )

    return context_lion_bridge


def build_city_lion_graph(lion_path):
    """Build source-row arcs before overlaying any forecast hour."""
    rows = pyogrio.read_dataframe(
        lion_path,
        layer="lion",
        columns=[
            "SegmentID", "Street", "FeatureTyp", "SegmentTyp", "RB_Layer",
            "NonPed", "RW_TYPE", "TrafDir", "Status",
            "NodeIDFrom", "NodeLevelF", "NodeIDTo", "NodeLevelT",
        ],
        fid_as_index=True,
    ).rename_axis("source_fid").reset_index()

    endpoint_fields = ["NodeIDFrom", "NodeLevelF", "NodeIDTo", "NodeLevelT"]
    for field in endpoint_fields:
        rows[field] = rows[field].astype("string").str.strip().fillna("")

    eligible = (
        rows["TrafDir"].isin(["W", "A", "T"])
        & rows["RB_Layer"].isin(["R", "B"])
        & pd.to_numeric(rows["Status"], errors="coerce").eq(2)
        & ~rows[endpoint_fields].isin(["", "*"]).any(axis=1)
        & rows.geometry.notna()
        & ~rows.geometry.is_empty
    )
    roads = rows.loc[eligible].copy().to_crs("EPSG:32618")
    roads["length_m"] = roads.geometry.length
    usable_length = np.isfinite(roads["length_m"]) & roads["length_m"].gt(0)
    roads = roads.loc[usable_length].copy()
    roads["lion_segment_id"] = (
        roads["SegmentID"].astype("string").str.strip().str.zfill(7)
    )

    directed_fields = [
        "from_node_id", "from_node_level", "to_node_id", "to_node_level",
    ]
    arc_fields = ["source_fid", "lion_segment_id", "length_m"] + endpoint_fields
    forward = roads.loc[roads["TrafDir"].isin(["W", "T"]), arc_fields].rename(
        columns=dict(zip(endpoint_fields, directed_fields))
    )
    reverse = roads.loc[roads["TrafDir"].isin(["A", "T"]), arc_fields].rename(
        columns=dict(zip(endpoint_fields, directed_fields[2:] + directed_fields[:2]))
    )
    arcs = pd.concat(
        [forward.assign(travel_sense="from_to"), reverse.assign(travel_sense="to_from")],
        ignore_index=True,
    )

    # Use the node ID and level together so stacked roads remain separate.
    node_names = ["node_id", "node_level"]
    starts = pd.MultiIndex.from_frame(arcs[directed_fields[:2]], names=node_names)
    ends = pd.MultiIndex.from_frame(arcs[directed_fields[2:]], names=node_names)
    nodes = starts.append(ends).unique()
    arcs["from_node_index"] = nodes.get_indexer(starts)
    arcs["to_node_index"] = nodes.get_indexer(ends)
    return roads, arcs, nodes
