"""Prepare local delivery files once and serve notebook replay requests."""

from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
from pyproj import Transformer

from .engine import demo_source_fids, route_request
from .network import (
    build_city_lion_graph,
    build_lion_bridge_candidates,
    build_lion_traversal_candidates,
    check_traversal_headings,
    load_common_contexts,
    match_context_directions,
)
from .replay import (
    SCENARIO_WINDOWS,
    assemble_replay_hour,
    load_2024_climate_hour,
    load_ida_climate_hour,
    load_mobility_hour,
    make_clock,
)
from .safety import (
    build_ida_safety_view,
    match_demo_warning_links,
    release_2024_safety_reports,
    release_ida_reports,
)


class ReplaySession:
    """Run the prototype with the accepted local Mobility, Climate and Safety deliveries."""

    def __init__(self, locations, *, climate_python=None):
        self.locations = {name: Path(path).resolve() for name, path in locations.items()}
        self.climate_python = Path(climate_python).resolve() if climate_python else None
        self.common_contexts, self.alignment_metadata = load_common_contexts(self.locations)
        self.lion_path = self.locations["LION geodatabase"]

        road_bridge, candidates = build_lion_bridge_candidates(self.common_contexts, self.locations)
        _, _, traversals = build_lion_traversal_candidates(road_bridge, candidates)
        self.traversals = check_traversal_headings(traversals, self.lion_path)
        self.direction_bridge = match_context_directions(self.common_contexts, self.traversals)

        self.roads, base_arcs, self.nodes = build_city_lion_graph(self.lion_path)
        self.arcs = base_arcs.loc[base_arcs["source_fid"].isin(demo_source_fids(self.roads))].copy()
        self.endpoint_nodes = self._build_endpoint_nodes()
        self.safety_indexes = {}

    def _build_endpoint_nodes(self):
        points = pyogrio.read_dataframe(self.lion_path, layer="node", columns=["NODEID"])
        points = points.to_crs("EPSG:32618")
        positions = pd.DataFrame({
            "node_id": points["NODEID"].astype("string").str.zfill(7),
            "x_m": points.geometry.x,
            "y_m": points.geometry.y,
        })

        nodes = self.nodes.to_frame(index=False)
        nodes.insert(0, "node_index", np.arange(len(nodes)))
        links = self.arcs[["from_node_index", "to_node_index"]].drop_duplicates()
        links = links.loc[links["from_node_index"].ne(links["to_node_index"])]
        nodes["outgoing_connections"] = np.bincount(links["from_node_index"], minlength=len(nodes))
        nodes["incoming_connections"] = np.bincount(links["to_node_index"], minlength=len(nodes))
        return nodes.merge(positions, on="node_id", how="left", validate="many_to_one").set_index(
            "node_index", verify_integrity=True
        )

    def endpoint_candidates(self, pin, role, limit=5):
        """List nearby level-aware nodes for a (latitude, longitude) pin."""
        lat, lon = map(float, pin)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Enter a latitude and longitude in degrees.")
        connection = {"origin": "outgoing_connections", "destination": "incoming_connections"}[role]
        x_m, y_m = Transformer.from_crs(
            "EPSG:4326", "EPSG:32618", always_xy=True
        ).transform(lon, lat)
        if not np.isfinite([x_m, y_m]).all():
            raise ValueError("The pin could not be projected onto the routing map.")

        nodes = self.endpoint_nodes
        usable = nodes.loc[
            nodes[connection].gt(0) & np.isfinite(nodes["x_m"]) & np.isfinite(nodes["y_m"])
        ].reset_index()
        usable["distance_from_pin_m"] = np.hypot(usable["x_m"] - x_m, usable["y_m"] - y_m)
        return usable.sort_values(["distance_from_pin_m", "node_index"]).head(limit)[[
            "node_index", "node_id", "node_level", "distance_from_pin_m",
            "outgoing_connections", "incoming_connections",
        ]]

    def _safety_index(self, scenario):
        if scenario not in self.safety_indexes:
            window = SCENARIO_WINDOWS[scenario]
            last_clock = make_clock(scenario, window["end_utc_exclusive"] - pd.Timedelta(hours=1))
            safety = self.locations["Safety delivery"]
            if scenario == "ida":
                timed, _ = build_ida_safety_view(safety, window)
                reports = release_ida_reports(last_clock, timed)
            else:
                feed = safety / "artifacts/safety_feed_2024_2025_v1/reports_2024_2025.csv"
                reports, _ = release_2024_safety_reports(feed, last_clock)

            links = match_demo_warning_links(reports, self.lion_path, self.roads)
            self.safety_indexes[scenario] = reports, links
        return self.safety_indexes[scenario]

    def load_hour(self, clock):
        """Prepare an hourly snapshot and release the reports available by its cutoff."""
        scenario = clock["scenario"]
        mobility, _ = load_mobility_hour(
            clock, self.locations, self.common_contexts, self.alignment_metadata
        )
        if scenario == "ida":
            climate, _ = load_ida_climate_hour(
                clock, self.locations["Climate Ida extension"], self.common_contexts
            )
        else:
            if self.climate_python is None:
                raise ValueError("Supply the frozen Climate environment's Python for 2024 replay.")
            climate = load_2024_climate_hour(
                clock, self.climate_python, self.locations["Climate 2024 inputs"],
                self.locations["Climate model"], self.common_contexts,
            )

        reports, warning_links = self._safety_index(scenario)
        return assemble_replay_hour(
            clock, common_contexts=self.common_contexts, mobility=mobility, climate=climate,
            traversals=self.traversals, direction_bridge=self.direction_bridge, base_arcs=self.arcs,
            report_index=reports, warning_index=warning_links,
        )

    def route(self, request, *, cleared_incident_ids=(), closed_source_fids=()):
        """Return a route response with optional operator actions applied for this request."""
        return route_request(
            request, load_hour=self.load_hour, nodes=self.nodes, roads=self.roads,
            node_positions=self.endpoint_nodes, cleared_incident_ids=cleared_incident_ids,
            closed_source_fids=closed_source_fids,
        )
