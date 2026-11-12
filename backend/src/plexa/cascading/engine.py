"""Score a prepared replay hour and return frontend-ready route alternatives."""

from collections.abc import Callable, Collection, Mapping
from math import isfinite

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import mapping

from .replay import make_clock, utc_z

ROAD_ACCESS = "Ordinary roads + mapped U-turns"
VOLUME_REFERENCE_VPH = 600.0
MAX_TRAFFIC_RATE = 0.30
MISSING_TRAFFIC_RATE = 0.30
ROAD_WARNING_PENALTY_M = 175.0
DISPLAY_TIMEZONE = "America/New_York"

ENDPOINT_COLUMNS = ["from_node_index", "to_node_index"]


class UnknownEndpointError(ValueError):
    """The selected node ID and level are absent from the routing graph."""


def demo_source_fids(roads: pd.DataFrame) -> pd.Series:
    """Select allowed source rows from the prepared vehicle network."""
    features = roads["FeatureTyp"].astype("string").str.strip()
    roadway = roads["RW_TYPE"].astype("string").str.strip()
    return roads.loc[
        features.isin(["0", "C"]) & roadway.isin(["1", "2", "3", "4", "9", "13"]),
        "source_fid",
    ]


def score_arcs(arcs: pd.DataFrame, warning_links: pd.DataFrame) -> pd.DataFrame:
    """Apply the approved penalties in distance-equivalent metres."""
    scored = arcs.copy()
    volume = scored["mobility_p50"]
    length = scored["length_m"]

    volume_fraction = volume.div(VOLUME_REFERENCE_VPH).clip(0, 1).fillna(0)
    scored["traffic_penalty_m"] = length * MAX_TRAFFIC_RATE * volume_fraction
    scored["missing_data_penalty_m"] = length.where(volume.isna(), 0) * MISSING_TRAFFIC_RATE
    scored["has_safety_warning"] = scored["source_fid"].isin(warning_links["source_fid"])
    scored["has_climate_alert"] = scored["climate_model_alert"].eq(True).fillna(False)
    warned = scored["has_safety_warning"] | scored["has_climate_alert"]
    scored["road_warning_penalty_m"] = ROAD_WARNING_PENALTY_M * warned.astype(float)
    scored["routing_cost_m"] = (
        length + scored["traffic_penalty_m"] + scored["missing_data_penalty_m"]
        + scored["road_warning_penalty_m"]
    )
    return scored


def _shortest_path(
    arcs: pd.DataFrame, node_count: int, origin: int, destination: int, cost_field: str
) -> pd.DataFrame | None:
    # Resolve parallel arcs by the requested cost before building the matrix.
    links = (
        arcs.sort_values([cost_field, "source_fid", "travel_sense"])
        .drop_duplicates(ENDPOINT_COLUMNS)
        .set_index(ENDPOINT_COLUMNS, drop=False, verify_integrity=True)
    )
    graph = csr_matrix(
        (links[cost_field].to_numpy(), tuple(links[field].to_numpy() for field in ENDPOINT_COLUMNS)),
        shape=(node_count, node_count),
    )
    distances, predecessors = dijkstra(
        graph, directed=True, indices=origin, return_predecessors=True
    )
    if not isfinite(distances[destination]):
        return None

    node_path = [destination]
    while node_path[-1] != origin:
        node_path.append(int(predecessors[node_path[-1]]))
    node_path.reverse()

    keys = pd.MultiIndex.from_arrays(
        [node_path[:-1], node_path[1:]], names=ENDPOINT_COLUMNS
    )
    return links.loc[keys].reset_index(drop=True)


def _resolve_endpoint(selection: Mapping, nodes: pd.MultiIndex) -> int:
    node_id = str(selection["node_id"]).strip().zfill(7)
    node_level = str(selection["node_level"]).strip().upper()
    try:
        return int(nodes.get_loc((node_id, node_level)))
    except KeyError as error:
        raise UnknownEndpointError(f"No routing node matches {(node_id, node_level)}.") from error


def _nullable_float(value: object) -> float | None:
    if pd.isna(value):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _pack_path(path: pd.DataFrame, roads: gpd.GeoDataFrame, warnings: pd.DataFrame) -> dict:
    distance_m = float(path["length_m"].sum())
    covered_m = float(path.loc[path["mobility_p50"].notna(), "length_m"].sum())
    path_warnings = (
        warnings.loc[warnings["source_fid"].isin(path["source_fid"]), ["incident_id", "result.rule_id"]]
        .drop_duplicates()
        .rename(columns={"result.rule_id": "rule_id"})
    )

    features = []
    for edge in path.itertuples(index=False):
        road = roads.loc[edge.source_fid]
        features.append({
            "type": "Feature",
            "geometry": mapping(road.geometry),
            "properties": {
                "lion_segment_id": None if pd.isna(edge.lion_segment_id) else str(edge.lion_segment_id),
                "street": None if pd.isna(road["Street"]) else str(road["Street"]),
                "distance_m": float(edge.length_m),
                "estimated_vehicles_per_hour": _nullable_float(edge.mobility_p50),
                "climate_flood_report_score": _nullable_float(edge.climate_reported_flooding_score),
                "climate_alert": (
                    None if pd.isna(edge.climate_model_alert) else bool(edge.climate_model_alert)
                ),
                "has_safety_warning": bool(edge.has_safety_warning),
            },
        })

    return {
        "distance_m": distance_m,
        "routing_cost_m": float(path["routing_cost_m"].sum()),
        "penalties_m": {
            "traffic": float(path["traffic_penalty_m"].sum()),
            "missing_traffic": float(path["missing_data_penalty_m"].sum()),
            "road_warning": float(path["road_warning_penalty_m"].sum()),
        },
        "traffic_coverage": {
            "covered_distance_m": covered_m,
            "percent": 100 * covered_m / distance_m if distance_m else None,
        },
        "safety_warnings": path_warnings.to_dict("records"),
        "geometry": {"type": "FeatureCollection", "features": features},
    }


def _pack_endpoint(
    index: int, nodes: pd.MultiIndex, positions: pd.DataFrame, to_wgs84: Transformer
) -> dict:
    node_id, level = nodes[index]
    point = positions.loc[index]
    lon, lat = to_wgs84.transform(float(point["x_m"]), float(point["y_m"]))
    return {
        "node_id": str(node_id),
        "node_level": str(level),
        "latitude": _nullable_float(lat),
        "longitude": _nullable_float(lon),
    }


def route_request(
    request: Mapping,
    *,
    load_hour: Callable[[dict], Mapping],
    nodes: pd.MultiIndex,
    roads: gpd.GeoDataFrame,
    node_positions: pd.DataFrame,
    cleared_incident_ids: Collection[str] = (),
    closed_source_fids: Collection[int] = (),
) -> dict:
    """Route on the selected replay hour using its prepared graph and warning links.

    `nodes` is the ordered (node ID, level) index used by the arcs.
    `node_positions` is indexed by node position with EPSG:32618 x_m and y_m.
    `load_hour` receives a validated clock and returns arcs, climate and the
    road-warning links released at that cutoff. A closure supplies all source
    rows for the selected road, including equivalent rows.
    """
    clock = make_clock(request["scenario"], request["as_of_utc"])
    origin = _resolve_endpoint(request["origin"], nodes)
    destination = _resolve_endpoint(request["destination"], nodes)
    hour = load_hour(clock)

    warning_links = hour["warning_links"]
    active = warning_links.loc[~warning_links["incident_id"].isin(cleared_incident_ids)]
    allowed = demo_source_fids(roads)
    arcs = hour["arcs"]
    open_arcs = arcs.loc[
        arcs["source_fid"].isin(allowed) & ~arcs["source_fid"].isin(closed_source_fids)
    ]
    scored = score_arcs(open_arcs, active)
    shortest = _shortest_path(scored, len(nodes), origin, destination, "length_m")
    recommended = _shortest_path(scored, len(nodes), origin, destination, "routing_cost_m")
    route_found = shortest is not None and recommended is not None

    paths = {"shortest": None, "recommended": None}
    changed = False
    if route_found:
        used_fids = pd.concat([shortest["source_fid"], recommended["source_fid"]]).unique()
        path_roads = (
            roads.set_index("source_fid", verify_integrity=True)
            .loc[used_fids, ["Street", "geometry"]]
            .to_crs("EPSG:4326")
        )
        paths = {
            "shortest": _pack_path(shortest, path_roads, active),
            "recommended": _pack_path(recommended, path_roads, active),
        }
        arc_identity = ["source_fid", "travel_sense"]
        changed = not shortest[arc_identity].equals(recommended[arc_identity])

    to_wgs84 = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    return {
        "schema_version": "plexa.route.response.v0.1.0",
        "scenario_id": clock["scenario"],
        "as_of_utc": utc_z(clock["as_of_utc"]),
        "target_start_utc": utc_z(clock["target_start_utc"]),
        "target_end_utc": utc_z(clock["target_end_utc"]),
        "display_timezone": DISPLAY_TIMEZONE,
        "road_access": ROAD_ACCESS,
        "origin": _pack_endpoint(origin, nodes, node_positions, to_wgs84),
        "destination": _pack_endpoint(destination, nodes, node_positions, to_wgs84),
        "route_found": route_found,
        "changed_from_shortest": changed,
        "climate_road_statuses": {
            str(status): int(count)
            for status, count in hour["climate"]["climate_status"].value_counts().items()
        },
        "routes": paths,
    }
