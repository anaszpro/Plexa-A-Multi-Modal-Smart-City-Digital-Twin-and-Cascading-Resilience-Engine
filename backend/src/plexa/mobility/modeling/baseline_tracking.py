"""Explicit, local MLflow logging for mobility baseline evaluation.

Importing this module creates no directories, tracking stores, or runs. The
caller chooses each parameter, metric, and artifact file to log. No server,
autologging, inference calls, or model registry is needed.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import unquote


EXPERIMENT_NAME = "Plexa Mobility Baselines"


def _local_client(root: str | Path):
    # Set these before the first MLflow import. Both flags exist in the project's
    # installed MLflow 3.17.1. Explicit client URIs ignore remote environment URIs.
    os.environ["MLFLOW_DISABLE_TELEMETRY"] = "1"
    os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
    from mlflow.tracking import MlflowClient

    project_root = Path(root).resolve()
    tracking_dir = project_root / "outputs" / "mobility"
    artifact_dir = tracking_dir / "mlartifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    tracking_uri = "sqlite:///" + (tracking_dir / "mlflow.db").as_posix()
    client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)
    return client, tracking_uri, artifact_dir.as_uri()


def _same_artifact_uri(left: str, right: str) -> bool:
    left = unquote(left).replace("\\", "/").rstrip("/")
    right = unquote(right).replace("\\", "/").rstrip("/")
    if os.name == "nt":
        left, right = left.casefold(), right.casefold()
    return left == right


def configure_tracking(root: str | Path) -> dict[str, str]:
    """Create/reuse the project's local experiment and return its identifiers.

    An existing experiment must be active and use this project's local artifact
    directory; silently reusing a remote artifact store would defeat local-only
    tracking. Existing runs and artifacts are never overwritten.
    """
    client, tracking_uri, artifact_uri = _local_client(root)
    experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
    if experiment is None:
        try:
            experiment_id = client.create_experiment(
                EXPERIMENT_NAME,
                artifact_location=artifact_uri,
                tags={"project": "Plexa", "module": "mobility"},
            )
        except Exception:
            # Another local process may have created the experiment meanwhile.
            experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
            if experiment is None:
                raise
        else:
            experiment = client.get_experiment(experiment_id)
    if experiment.lifecycle_stage != "active":
        raise RuntimeError(f"MLflow experiment {EXPERIMENT_NAME!r} is deleted; restore it first.")
    if not _same_artifact_uri(experiment.artifact_location, artifact_uri):
        raise RuntimeError(
            "The existing mobility experiment has a different artifact location. "
            f"Expected the project's local directory: {artifact_uri}"
        )
    return {"tracking_uri": tracking_uri, "experiment_id": str(experiment.experiment_id)}


def _parameter_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value)


def log_run(
    root: str | Path,
    scenario: str,
    model_name: str,
    params: dict,
    metrics: dict,
    artifacts: list[Path],
    tags: dict,
) -> dict[str, str]:
    """Log one completed (scenario, model) evaluation as a new local run.

    Artifacts must be explicit existing files, with distinct basenames. Logging
    a directory recursively is deliberately unsupported. Runs use the client
    API, so an unrelated fluent MLflow active run is neither reused nor ended.
    """
    artifact_files = [Path(path).resolve(strict=True) for path in artifacts]
    if any(not path.is_file() for path in artifact_files):
        raise ValueError("MLflow artifacts must be individual files, not directories.")
    if len({path.name.casefold() for path in artifact_files}) != len(artifact_files):
        raise ValueError("MLflow artifact filenames must be distinct within a run.")
    numeric_metrics = {str(key): float(value) for key, value in metrics.items()}
    if any(not math.isfinite(value) for value in numeric_metrics.values()):
        raise ValueError("MLflow evaluation metrics must be finite numbers.")
    parameter_values = {str(key): _parameter_value(value) for key, value in params.items()}
    run_tags = {str(key): _parameter_value(value) for key, value in tags.items()}
    run_tags.update({"project": "Plexa", "module": "mobility", "scenario": scenario,
                     "model_name": model_name})

    config = configure_tracking(root)
    client, _, _ = _local_client(root)
    from mlflow.entities import Metric, Param

    run = client.create_run(
        config["experiment_id"], run_name=f"{scenario} / {model_name}", tags=run_tags
    )
    run_id = run.info.run_id
    try:
        timestamp = int(time.time() * 1000)
        parameter_entities = [Param(key, value) for key, value in parameter_values.items()]
        metric_entities = [Metric(key, value, timestamp, 0)
                           for key, value in numeric_metrics.items()]
        # Respect MLflow's batch limits; always wait for local writes to finish.
        for offset in range(0, len(parameter_entities), 100):
            client.log_batch(run_id, params=parameter_entities[offset:offset + 100], synchronous=True)
        for offset in range(0, len(metric_entities), 1000):
            client.log_batch(run_id, metrics=metric_entities[offset:offset + 1000], synchronous=True)
        for path in artifact_files:
            client.log_artifact(run_id, str(path))
        client.set_terminated(run_id, status="FINISHED")
    except BaseException:
        try:
            client.set_terminated(run_id, status="FAILED")
        except Exception:
            pass  # Preserve the original logging failure for the caller.
        raise
    return {**config, "run_id": run_id}
