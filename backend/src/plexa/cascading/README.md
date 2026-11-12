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

## Local HTTP service

The HTTP entry point uses FastAPI and Uvicorn in the main backend environment. It listens on `127.0.0.1:8000` by default. Store local asset paths in `tmp/cascading-api.local.json`, which Git ignores. The JSON object takes a `locations` object with the keys in the table above and an optional `climate_python` path. Relative paths resolve next to the config file. Ida can run with its own assets while 2024 shows as unavailable.

From the repository root in PowerShell:

```powershell
Push-Location .\backend\src
try {
    & ..\..\.venv\Scripts\python.exe -B -m plexa.cascading.api --config ..\..\tmp\cascading-api.local.json
} finally {
    Pop-Location
}
```

Open `/docs` on the local service for the request parameters. Browser requests from `http://localhost:3000` and `http://127.0.0.1:3000` are allowed by CORS. The server uses one `ReplaySession` per process. Incident and route calls serialize access to replay preparation. The first request for a scenario indexes its Safety reports. Selecting a new 2024 routing hour runs the frozen Climate predictor. The session retains the most recent 2024 Climate result in memory for repeat route requests at that hour.

| Method and path | Inputs and response |
| --- | --- |
| `GET /health` | Overall readiness and per-scenario `state`, `missing_assets`, `start_utc`, and `end_utc_exclusive`. `ready` means the graph is initialized and the required asset entry files exist. |
| `GET /api/v1/incidents` | `scenario`, `as_of_utc`, optional `limit` (default 100, maximum 200), `offset` (default 0), and `incident_id` for looking up a route warning. |
| `GET /api/v1/endpoint-candidates` | `latitude`, `longitude`, `role` (`origin` or `destination`), optional `limit` (default 5, maximum 20). Each candidate includes its `node_id`, `node_level`, coordinates, distance from the pin in metres, and incoming and outgoing connection counts. |
| `POST /api/v1/routes` | The same request body and `plexa.route.response.v0.1.0` response as the route fixture. Use actual node IDs and levels returned by the candidate endpoint. |

The incident response uses `schema_id: "plexa.incidents.response.v0.1.0"` with `scenario_id`, `as_of_utc`, `display_timezone`, `pagination`, and `incidents`. The page order is latest available first, then incident ID. Read `pagination.next_offset` for the next page and `pagination.total` for the matching count. An empty result returns an empty array. Each record includes the same nested location, time, source, classification, and lifecycle fields as the fixture, with live historical values and nullable fields where the source supplies none. `coordinate_status` is `provided` or `missing`. `road_association_status: "matched"` identifies a report with a usable demo-network warning link. `not_available` means the response has no such link. All released reports are available to the list, including reports that were skipped by classification or lack coordinates.

Errors use an `error` object with `code` and `message`. Invalid inputs return HTTP 422, unavailable local assets or an uninitialized service return 503, and processing failures return 500. A valid request with no directed path returns HTTP 200 and `route_found: false`. The service exposes read-only replay. Operator action state and persistence remain follow-up work.
