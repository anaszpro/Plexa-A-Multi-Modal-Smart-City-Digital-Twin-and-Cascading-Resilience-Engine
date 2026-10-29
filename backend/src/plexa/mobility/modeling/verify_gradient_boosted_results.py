"""Read-only verification of completed gradient-boosted baseline artifacts.

Run in a fresh Python process after training finishes. No PostgreSQL connection,
model fitting, MLflow logging, downloads, or output files are needed. The MLflow
SQLite connection is opened in database-enforced read-only mode.
"""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import url2pathname

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modeling.baseline_evaluation import evaluate_predictions
from modeling.boosted_reporting import weighted_r2
from modeling.gradient_boosted import BoostedTrafficModel


SCENARIOS = ["within_road", "unseen_roads", "time", "season_transfer"]
KEYS = ["segment_id", "direction", "road_type", "hour_start_utc"]
QUANTILES = ["p10", "p50", "p90"]
BASELINE = "B3_gradient_boosted"


class Checks:
    def __init__(self):
        self.count = 0

    def require(self, condition, message):
        if not condition:
            raise RuntimeError(message)
        self.count += 1

    def numbers(self, actual, expected, label):
        for key, value in expected.items():
            self.require(key in actual, f"{label}: missing metric {key}")
            self.require(np.isfinite(float(actual[key])) and
                         np.isclose(float(actual[key]), float(value), rtol=1e-12, atol=1e-10),
                         f"{label}: {key} differs ({actual[key]} vs {value})")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file_manifest(directory, manifest, checks):
    directory = Path(directory).resolve()
    for relative, metadata in manifest["files"].items():
        path = (directory / relative).resolve()
        checks.require(path.is_relative_to(directory) and path.is_file(), f"Missing artifact: {relative}")
        checks.require(path.stat().st_size == metadata["bytes"], f"Artifact size changed: {relative}")
        checks.require(sha256(path) == metadata["sha256"], f"Artifact hash changed: {relative}")


def verify_sources(manifest, checks):
    """Check current source or the exact path-only migration from frozen Git source."""
    record_path = PROJECT / "outputs/mobility/mlflow_relocation.json"
    record = read_json(record_path) if record_path.is_file() else {}
    migration = record.get("clone_migration", {})
    updates = migration.get("source_path_updates", {})
    for name, expected in manifest["source_code_sha256"].items():
        checks.require(Path(name).name == name and name.endswith(".py"), "Invalid source filename")
        current = sha256(ROOT / "modeling" / name)
        if current == expected:
            checks.require(True, f"Source matches: {name}")
            continue
        update = updates.get(name, {})
        checks.require(update.get("original_sha256") == expected
                       and update.get("current_sha256") == current,
                       f"Source changed without an exact recorded path migration: {name}")
        commit = migration.get("source_code_commit", "")
        checks.require(len(commit) == 40 and all(c in "0123456789abcdef" for c in commit),
                       "Missing frozen source Git commit")
        historical = subprocess.run(
            ["git", "-C", str(PROJECT), "show",
             f"{commit}:backend/src/plexa/mobility/modeling/{name}"],
            capture_output=True, check=False,
        )
        checks.require(historical.returncode == 0
                       and hashlib.sha256(historical.stdout).hexdigest() == expected,
                       f"Frozen source is unavailable or differs in Git: {name}")


def verify_rows(saved, source, offsets, checks, label):
    checks.require(np.array_equal(saved.model_row.to_numpy(), offsets), f"{label}: test offsets differ")
    pd.testing.assert_frame_equal(saved[KEYS], source[KEYS].reset_index(drop=True), check_exact=True)
    checks.require(np.array_equal(saved.volume.to_numpy(), source.volume.to_numpy()),
                   f"{label}: targets differ")
    checks.require(np.array_equal(saved.sd.to_numpy(), source.sd.to_numpy()), f"{label}: road IDs differ")


def score_rows(rows, predictions, weights):
    score = evaluate_predictions(rows, predictions[QUANTILES], weights)
    score["r2"] = weighted_r2(rows, predictions[QUANTILES], weights)
    return score


def table_score(frame, scenario, baseline, checks, label):
    selected = frame.loc[frame.scenario.eq(scenario) & frame.baseline.eq(baseline)]
    checks.require(len(selected) == 1, f"{label}: expected one {scenario}/{baseline} row")
    return selected.iloc[0].to_dict()


def verify_tracking(output, manifest, checks):
    database = PROJECT / "outputs/mobility/mlflow.db"
    checks.require(database.is_file(), "Local MLflow database does not exist")
    # Use SQLite directly: MLflow's Windows store initializer interprets a
    # read-only file URI as a directory and attempts to create it. This native
    # connection enforces read-only access without any MLflow initialization.
    read_only_uri = database.as_uri() + "?mode=ro"
    expected_tracking_uri = "sqlite:///" + database.as_posix()
    relocation_path = PROJECT / "outputs/mobility/mlflow_relocation.json"
    relocation = read_json(relocation_path) if relocation_path.is_file() else {}
    runs = read_json(output / "mlflow_runs.json")
    checks.require(runs == manifest["mlflow_runs"], "MLflow run index differs from manifest")
    checks.require(len(runs) == 4 and {run["scenario"] for run in runs} == set(SCENARIOS),
                   "Expected four MLflow scenario runs")
    checks.require(len({run["run_id"] for run in runs}) == 4, "MLflow run IDs are not unique")
    common = [PROJECT / "data/mobility/clean/model_table_v1_snapshot.json",
              PROJECT / "data/mobility/clean/model_splits_v1_snapshot.json"]
    common += [output / name for name in ["baseline_reference_manifest.json", "boosted_report.md",
               "three_model_comparison.png", "methodology.json", "benchmark_checks.json"]]
    with closing(sqlite3.connect(read_only_uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for record in runs:
            scenario, run_id = record["scenario"], record["run_id"]
            # Frozen manifests retain their creation-time URI. Only accept an
            # explicit relocation to this database for this recorded run ID.
            relocated = (
                relocation.get("current_tracking_uri") == expected_tracking_uri
                and record["tracking_uri"] in relocation.get("recorded_tracking_uris", [])
                and run_id in relocation.get("run_ids", [])
            )
            checks.require(record["tracking_uri"] == expected_tracking_uri or relocated,
                           "Run points at another tracking store without a matching relocation record")
            run = connection.execute(
                "SELECT status, experiment_id, artifact_uri, lifecycle_stage FROM runs WHERE run_uuid = ?",
                (run_id,),
            ).fetchone()
            checks.require(run is not None, f"{scenario}: MLflow run is missing")
            checks.require(run["status"] == "FINISHED" and run["lifecycle_stage"] == "active",
                           f"{scenario}: MLflow run is not active and FINISHED")
            checks.require(str(run["experiment_id"]) == record["experiment_id"], f"{scenario}: experiment differs")
            tags = dict(connection.execute("SELECT key, value FROM tags WHERE run_uuid = ?", (run_id,)))
            checks.require(tags.get("scenario") == scenario and tags.get("model_name") == BASELINE,
                           f"{scenario}: run tags differ")
            metrics = connection.execute(
                "SELECT key, value, is_nan FROM latest_metrics WHERE run_uuid = ?", (run_id,)
            ).fetchall()
            checks.require(all(not metric["is_nan"] for metric in metrics), f"{scenario}: MLflow has NaN metrics")
            logged_metrics = {metric["key"]: metric["value"] for metric in metrics}
            recorded_metrics = read_json(output / scenario / "metrics.json")
            checks.require(set(logged_metrics) == set(recorded_metrics), f"{scenario}: MLflow metric keys differ")
            checks.numbers(logged_metrics, recorded_metrics, f"MLflow {scenario}")
            experiment = connection.execute(
                "SELECT name FROM experiments WHERE experiment_id = ?", (run["experiment_id"],)
            ).fetchone()
            checks.require(experiment is not None and experiment["name"] == "Plexa Mobility Baselines",
                           "Unexpected MLflow experiment")
            parsed = urlsplit(run["artifact_uri"])
            checks.require(parsed.scheme == "file" and parsed.netloc in {"", "localhost"},
                           f"{scenario}: artifacts are not local files")
            artifact_directory = Path(url2pathname(parsed.path)).resolve()
            checks.require(artifact_directory.is_relative_to((PROJECT / "outputs/mobility/mlartifacts").resolve()),
                           f"{scenario}: artifact directory is outside the project")
            checks.require(artifact_directory.is_dir(), f"{scenario}: MLflow artifact directory is missing")
            expected_files = {path.name: path for path in (output / scenario).iterdir() if path.is_file()}
            expected_files.update({path.name: path for path in common})
            listed = list(artifact_directory.iterdir())
            checks.require(all(path.is_file() for path in listed), f"{scenario}: unexpected artifact subdirectory")
            checks.require({path.name for path in listed} == set(expected_files),
                           f"{scenario}: MLflow artifact list differs")
            for name, source in expected_files.items():
                logged = artifact_directory / name
                checks.require(logged.is_file() and sha256(logged) == sha256(source),
                               f"{scenario}: MLflow artifact changed: {name}")


def verify(output, native=True):
    checks = Checks()
    output = Path(output).resolve()
    manifest = read_json(output / "run_manifest.json")
    verify_file_manifest(output, manifest, checks)
    verify_sources(manifest, checks)
    model_path = PROJECT / "data/mobility/clean/model_table_v1.parquet"
    split_path = PROJECT / "data/mobility/clean/model_splits_v1.parquet"
    reference_directory = PROJECT / "outputs/mobility/baselines_v1"
    reference_path = reference_directory / "run_manifest.json"
    checks.require(sha256(model_path) == manifest["input_model_sha256"], "Frozen modelling dataset changed")
    checks.require(sha256(split_path) == manifest["input_splits_sha256"], "Frozen split assignments changed")
    checks.require(sha256(reference_path) == manifest["reference_manifest_sha256"] ==
                   sha256(output / "baseline_reference_manifest.json"), "Parent baseline manifest changed")
    reference_manifest = read_json(reference_path)
    verify_sources(reference_manifest, checks)
    verify_file_manifest(reference_directory, reference_manifest, checks)
    data, splits = pd.read_parquet(model_path), pd.read_parquet(split_path)
    checks.require(len(data) == len(splits), "Input lengths differ")
    checks.require(np.array_equal(splits.model_row, np.arange(len(data))), "Split model_row offsets changed")
    pd.testing.assert_frame_equal(data[KEYS], splits[KEYS], check_exact=True)
    summary = pd.read_csv(output / "metrics.csv")
    all_models = pd.read_csv(output / "all_models_metrics.csv")
    checks.require(len(summary) == 4 and len(all_models) == 12, "Summary metric row counts differ")
    manifest_scores = pd.DataFrame(manifest["metrics"])
    for scenario in SCENARIOS:
        mask = splits["split_" + scenario].eq("test").to_numpy()
        offsets = np.flatnonzero(mask)
        test = data.loc[mask]
        weights = splits.loc[mask, "w_" + scenario].to_numpy()
        scenario_directory = output / scenario
        saved = pd.read_parquet(scenario_directory / "test_predictions.parquet")
        verify_rows(saved, test, offsets, checks, scenario)
        score = score_rows(test, saved, weights)
        recorded = read_json(scenario_directory / "metrics.json")
        checks.numbers(recorded, score, f"JSON {scenario}")
        checks.require(0 <= recorded["raw_quantile_crossing_fraction"] <= 1, "Invalid raw crossing fraction")
        checks.numbers(table_score(summary, scenario, BASELINE, checks, "CSV"), recorded, f"CSV {scenario}")
        checks.numbers(table_score(all_models, scenario, BASELINE, checks, "All models"), recorded,
                       f"All models {scenario}")
        checks.numbers(table_score(manifest_scores, scenario, BASELINE, checks, "Manifest"), recorded,
                       f"Manifest {scenario}")
        for baseline in ["B1_global", "B2_road_scaled"]:
            parent_predictions = pd.read_parquet(reference_directory / scenario / baseline / "test_predictions.parquet")
            verify_rows(parent_predictions, test, offsets, checks, f"{scenario}/{baseline}")
            with (reference_directory / scenario / baseline / "model.pkl").open("rb") as stream:
                parent_model = pickle.load(stream)
            parent_probe = parent_model.predict(test.iloc[:200])
            checks.require(np.allclose(parent_probe[QUANTILES].to_numpy(),
                                       parent_predictions[QUANTILES].iloc[:len(parent_probe)].to_numpy(),
                                       rtol=1e-12, atol=1e-12),
                           f"{scenario}/{baseline}: restored predictions differ")
            parent_score = score_rows(test, parent_predictions, weights)
            checks.numbers(table_score(all_models, scenario, baseline, checks, "All models"), parent_score,
                           f"All models {scenario}/{baseline}")
        with (scenario_directory / "model.pkl").open("rb") as stream:
            model = pickle.load(stream)
        checks.require(isinstance(model, BoostedTrafficModel), f"{scenario}: unexpected serialized model type")
        probe = test.iloc[:200]
        bundled = model.predict(probe)
        checks.require(np.allclose(bundled.to_numpy(), saved[QUANTILES].iloc[:len(probe)].to_numpy(),
                                   rtol=1e-12, atol=1e-12), f"{scenario}: restored predictions differ")
        if native:
            import lightgbm as lgb
            features = model.features_for_prediction(probe)
            logs = np.column_stack([lgb.Booster(model_file=str(scenario_directory / f"quantile_{q:02d}.txt"))
                                    .predict(features, num_threads=4) for q in [10, 50, 90]])
            native_predictions = np.expm1(np.maximum(np.sort(logs, axis=1), 0))
            checks.require(np.allclose(native_predictions, bundled.to_numpy(), rtol=1e-12, atol=1e-12),
                           f"{scenario}: native booster predictions differ")
        print(f"PASS {scenario}: {len(test):,} test rows, metrics, saved model" +
              (", native boosters" if native else ""), flush=True)
    verify_tracking(output, manifest, checks)
    print(f"PASS {checks.count:,} checks: artifact hashes, frozen parents, metrics, models, four MLflow runs.",
          flush=True)
    return checks.count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "outputs/mobility/gradient_boosted_v1")
    parser.add_argument("--skip-native", action="store_true", help="Skip native LightGBM text-model parity checks")
    arguments = parser.parse_args()
    verify(arguments.output, native=not arguments.skip_native)
