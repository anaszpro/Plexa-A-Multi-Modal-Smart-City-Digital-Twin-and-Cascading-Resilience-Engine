# Cascading replay routing

Run the [public demo notebook](../../../../notebooks/Integration/Plexa_Cascading_Baseline_v1.ipynb) to exercise routing with synthetic road shapes, forecasts, and a warning. The notebook uses the package in this directory. Its two [frontend examples](../../../../docs/integration/contracts/v0.1/examples/) are `cascading_route.synthetic.json` and `cascading_incidents.synthetic.json`. The route example wraps the request and response, and the incident example supplies the warning ID used on the shortest route.

The Python environment needs NumPy, pandas, SciPy, GeoPandas, Shapely, PyProj, PyOgrio, and PyArrow. Add the repository's `backend/src` directory to Python's import path.

## Local replay

`ReplaySession` prepares the LION graph and the approved 441-road correspondence from local delivery files. It indexes reports for a scenario on its first request. Create one session and reuse it for subsequent requests.

Pass a mapping of these names to local paths.

| Location key | Expected asset |
| --- | --- |
| `LION geodatabase` | LION file geodatabase used for the approved bridge |
| `Frozen mobility roads` | Frozen `segment_table_v4.parquet` |
| `Mobility assets` | v0.2.0 directory containing the approved contexts, registry, and crosswalk |
| `Mobility Ida cache` | Complete joint Ida cache directory |
| `Mobility 2024 cache` | Complete joint 2024 cache directory |
| `Climate Ida extension` | Approved Ida bundle with `actual_predictions.parquet` |
| `Climate 2024 inputs` | Climate input directory with the rainfall availability ledger |
| `Climate model` | Frozen Climate package directory with the request builder and predictor |
| `Safety delivery` | Safety release root containing the Ida and 2024–2025 artifacts |

Use the separate frozen Climate environment's Python for 2024. Supply that executable as `climate_python`.

```python
from plexa.cascading import ReplaySession

session = ReplaySession(locations, climate_python=climate_python)
origin_candidates = session.endpoint_candidates(start_lat_lon, role="origin")
destination_candidates = session.endpoint_candidates(end_lat_lon, role="destination")

# Use the node ID and level selected from each candidate table.
request = {
    "scenario": "ida",
    "as_of_utc": "2021-09-02T02:00:00Z",
    "origin": {"node_id": origin_id, "node_level": origin_level},
    "destination": {"node_id": destination_id, "node_level": destination_level},
}
response = session.route(request)
```

The request clock accepts an exact hour with an explicit timezone. Full routing covers the 48-hour Ida replay and the 2024 New York calendar year. For 2025, the delivered Safety feed has report coverage and full replay routing awaits Mobility and Climate deliveries.

Operator actions can be supplied to `session.route` as `cleared_incident_ids` and `closed_source_fids`. Clearing an incident removes its warning for that request. A closure supplies every equivalent LION source row for the selected road and removes both travel directions from route choices. The caller owns operator action state.

## Frontend response

The Python call returns a JSON-ready `plexa.route.response.v0.1.0` object. It contains the clock, selected nodes, shortest and recommended routes, and a `changed_from_shortest` flag. Each route supplies actual distance, routing cost, penalty totals, traffic coverage by distance, linked Safety incident IDs, and a WGS84 GeoJSON FeatureCollection in route order. A request without a directed path returns `route_found: false` and null route alternatives.

Routing cost uses distance-equivalent metres. Known traffic volume adds up to 30% of edge length at 600 estimated vehicles per hour. Missing traffic adds 30%. An active road-relevant Safety report or a native Climate alert adds 175 m once per warned segment. Climate gaps appear in `climate_road_statuses` and produce null climate values on affected edges. The prototype uses a directed LION graph with the approved demo access filter and mapped U-turn links. Vehicle-specific access and turn restrictions require separate validation for operational use.

The notebook and fixtures are static frontend handoff examples. `ReplaySession` is the local Python entry point.
