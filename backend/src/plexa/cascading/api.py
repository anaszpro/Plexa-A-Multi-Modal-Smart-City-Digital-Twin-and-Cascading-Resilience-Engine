"""Local HTTP entry point for the historical replay prototype."""

import argparse
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .engine import UnknownEndpointError
from .incidents import pack_incident_page
from .replay import SCENARIO_WINDOWS, make_clock, utc_z
from .safety import HISTORICAL_SAFETY_FEED, IDA_SAFETY_DIRECTORY
from .session import ReplaySession

LOGGER = logging.getLogger(__name__)
LOCAL_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
Scenario = Literal["ida", "2024"]
EndpointRole = Literal["origin", "destination"]
ClockText = Annotated[str, Field(min_length=1, max_length=40)]

COMMON_ASSETS = {
    "LION geodatabase": (),
    "Frozen mobility roads": (),
    "Mobility assets": ("approved_joint_contexts.json", "road_contexts.json",
                        "approved_joint_crosswalk.csv"),
}
SCENARIO_ASSETS = {
    "ida": {
        "Mobility Ida cache": ("cache_manifest.json",),
        "Climate Ida extension": ("actual_predictions.parquet",),
        "Safety delivery": (IDA_SAFETY_DIRECTORY / "request.json",
                            IDA_SAFETY_DIRECTORY / "result.json"),
    },
    "2024": {
        "Mobility 2024 cache": ("cache_manifest.json",),
        "Climate 2024 inputs": ("input_manifest.json", "unavailable_prediction_hours.csv"),
        "Climate model": ("prepare_request.py", "predict.py", "history_tree.joblib"),
        "Safety delivery": (HISTORICAL_SAFETY_FEED,),
    },
}


class LocalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locations: dict[str, Path]
    climate_python: Path | None = None


class NodeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: Annotated[str, Field(pattern=r"^[0-9]{1,7}$")]
    node_level: Annotated[str, Field(pattern=r"^[A-Za-z]$")]


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Scenario
    as_of_utc: ClockText
    origin: NodeSelection
    destination: NodeSelection


def load_config(path: Path) -> LocalConfig:
    path = path.resolve()
    config = LocalConfig.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))
    return LocalConfig(
        locations={name: (path.parent / asset).resolve() for name, asset in config.locations.items()},
        climate_python=(path.parent / config.climate_python).resolve() if config.climate_python else None,
    )


def _missing_assets(locations, requirements):
    missing = []
    for label, files in requirements.items():
        path = locations.get(label)
        if path is None or not path.exists() or any(not (path / name).is_file() for name in files):
            missing.append(label)
    return missing


def _http_error(status, code, message):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _clock(scenario, as_of_utc):
    try:
        return make_clock(scenario, as_of_utc)
    except (TypeError, ValueError, OverflowError) as error:
        raise _http_error(422, "invalid_clock", str(error)) from error


def create_app(config: LocalConfig) -> FastAPI:
    replay_lock = Lock()

    @asynccontextmanager
    async def lifespan(app):
        common_missing = _missing_assets(config.locations, COMMON_ASSETS)
        scenarios = {}
        for scenario, requirements in SCENARIO_ASSETS.items():
            missing = common_missing + _missing_assets(config.locations, requirements)
            if scenario == "2024" and (
                config.climate_python is None or not config.climate_python.is_file()
            ):
                missing.append("Climate Python")
            window = SCENARIO_WINDOWS[scenario]
            scenarios[scenario] = {
                "state": "assets_unavailable" if missing else "ready",
                "missing_assets": missing,
                "start_utc": utc_z(window["start_utc"]),
                "end_utc_exclusive": utc_z(window["end_utc_exclusive"]),
            }

        app.state.session = None
        app.state.scenarios = scenarios
        if any(details["state"] == "ready" for details in scenarios.values()):
            try:
                app.state.session = ReplaySession(config.locations, climate_python=config.climate_python)
            except Exception:
                LOGGER.exception("Could not initialize the replay network.")
                for details in scenarios.values():
                    if details["state"] == "ready":
                        details["state"] = "initialization_failed"
        yield

    app = FastAPI(title="PLEXA Cascading Replay", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=LOCAL_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        detail = error.detail
        if not isinstance(detail, dict):
            detail = {"code": "http_error", "message": str(detail)}
        return JSONResponse({"error": detail}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        fields = [".".join(map(str, item["loc"])) for item in error.errors()]
        return JSONResponse({"error": {
            "code": "invalid_request", "message": "Check the request fields.", "fields": fields,
        }}, status_code=422)

    @app.exception_handler(UnknownEndpointError)
    async def endpoint_error(request, error):
        return JSONResponse({"error": {
            "code": "unknown_node", "message": "Select a node ID and level returned by the service.",
        }}, status_code=422)

    @app.exception_handler(FileNotFoundError)
    async def unavailable_asset(request, error):
        LOGGER.exception("A replay asset could not be opened.")
        return JSONResponse({"error": {
            "code": "assets_unavailable", "message": "A required local replay asset is unavailable.",
        }}, status_code=503)

    @app.exception_handler(Exception)
    async def processing_error(request, error):
        LOGGER.exception("Replay request failed.")
        return JSONResponse({"error": {
            "code": "processing_failed", "message": "The replay request could not be processed.",
        }}, status_code=500, headers={
            "Access-Control-Allow-Origin": request.headers["origin"], "Vary": "Origin",
        } if request.headers.get("origin") in LOCAL_ORIGINS else None)

    def require_session(scenario=None):
        if scenario is not None:
            state = app.state.scenarios[scenario]["state"]
            if state == "assets_unavailable":
                raise _http_error(503, state, "Local assets for this scenario are unavailable.")
        if app.state.session is None:
            raise _http_error(503, "service_unavailable", "The replay network is unavailable. Check /health.")
        return app.state.session

    @app.get("/health")
    def health():
        ready = app.state.session is not None
        return JSONResponse({
            "status": "ready" if ready else "unavailable",
            "scenarios": app.state.scenarios,
        }, status_code=200 if ready else 503)

    @app.get("/api/v1/incidents")
    def incidents(
        scenario: Scenario,
        as_of_utc: Annotated[str, Query(min_length=1, max_length=40)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        incident_id: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    ):
        clock = _clock(scenario, as_of_utc)
        session = require_session(scenario)
        with replay_lock:
            reports, warnings = session.incidents_at(clock)
        return pack_incident_page(
            clock, reports, warnings, limit=limit, offset=offset, incident_id=incident_id
        )

    @app.get("/api/v1/endpoint-candidates")
    def endpoint_candidates(
        latitude: Annotated[float, Query(ge=-90, le=90)],
        longitude: Annotated[float, Query(ge=-180, le=180)],
        role: EndpointRole,
        limit: Annotated[int, Query(ge=1, le=20)] = 5,
    ):
        session = require_session()
        try:
            candidates = session.endpoint_candidates((latitude, longitude), role, limit=limit)
        except ValueError as error:
            raise _http_error(422, "invalid_pin", str(error)) from error
        return {
            "schema_id": "plexa.endpoint_candidates.response.v0.1.0",
            "role": role,
            "pin": {"latitude": latitude, "longitude": longitude},
            "candidates": candidates.drop(columns="node_index").to_dict("records"),
        }

    @app.post("/api/v1/routes")
    def routes(request: RouteRequest):
        clock = _clock(request.scenario, request.as_of_utc)
        session = require_session(request.scenario)
        data = request.model_dump()
        data["as_of_utc"] = utc_z(clock["as_of_utc"])
        with replay_lock:
            return session.route(data)

    return app


def main():
    parser = argparse.ArgumentParser(description="Run the local PLEXA replay HTTP service.")
    parser.add_argument("--config", required=True, type=Path, help="Path to the local asset configuration.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = load_config(args.config)
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
