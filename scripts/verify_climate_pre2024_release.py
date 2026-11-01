"""Verify standalone hashes, approved scope, real replay and unavailable inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pandas as pd


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(root):
    root = Path(root).resolve()
    handover = json.loads((root / "handover_manifest.json").read_text())
    for name, digest in handover["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or sha256(path) != digest:
            raise ValueError(f"Handover hash mismatch: {name}")
    model = root / "model"
    inputs = root / "inputs_2024"
    release = json.loads((model / "release_manifest.json").read_text())
    for name, digest in release["assets"].items():
        if sha256(model / name) != digest:
            raise ValueError(f"Model asset mismatch: {name}")
    manifest = json.loads((inputs / "input_manifest.json").read_text())
    if manifest["model_release_manifest_sha256"] != sha256(model / "release_manifest.json"):
        raise ValueError("Input/model release mismatch")
    crosswalk = pd.read_csv(inputs / "approved_mobility_crosswalk.csv", dtype={"mobility_segment_id": str})
    assert len(crosswalk) == 441 and crosswalk.street_segment_id.nunique() == 441
    assert not crosswalk.native_shared_between_mobility_roads.any()
    assert crosswalk.relationship.eq("single_native_candidate").all()
    assert len(pd.read_csv(inputs / "supported_mobility_contexts.csv")) == 666
    assert len(pd.read_parquet(model / "street_cell_bridge.parquet")) == 441
    history = pd.read_parquet(model / "history_events.parquet")
    assert len(history) == 7843
    assert history.prediction_time_utc.lt(pd.Timestamp("2023-06-24T04:00:00Z")).all()
    assert not history.duplicated(["street_segment_id", "prediction_time_utc"]).any()
    assert manifest["hourly_starts"] == 8784 and manifest["unavailable_prediction_hours"] == 26
    monthly = pd.read_csv(inputs / "monthly_input_coverage.csv")
    assert monthly.expected_hours.sum() == 8784 and monthly.unavailable_hours.sum() == 26
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LOKY_MAX_CPU_COUNT="2")
    with tempfile.TemporaryDirectory(prefix="plexa-pre2024-verify-") as temporary:
        directory = Path(temporary)
        replay = directory / "example_replay.json"
        result = subprocess.run([sys.executable, str(model / "predict.py"), "--bundle", str(model),
            "--input", str(model / "example_request.json"), "--output", str(replay)],
            cwd=directory, env=environment, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(result.stderr)
        assert json.loads(replay.read_text()) == json.loads((model / "example_result.json").read_text())
        for stamp, name, expected_code in [("2024-01-01T05:00:00Z", "available", 0),
                                           ("2024-03-19T20:00:00Z", "unavailable", 1)]:
            request_path, output_path = directory / f"{name}_request.json", directory / f"{name}_result.json"
            result = subprocess.run([sys.executable, str(model / "prepare_request.py"), "--bundle", str(model),
                "--inputs", str(inputs), "--time", stamp, "--request", str(request_path), "--result", str(output_path)],
                cwd=directory, env=environment, capture_output=True, text=True)
            if result.returncode != expected_code:
                raise ValueError(f"{name}: {result.stdout}\n{result.stderr}")
            output = json.loads(output_path.read_text())
            assert len(output["records"]) == 441
            assert output["request_sha256"] == sha256(request_path)
            if expected_code == 0:
                assert all(record["status"] == "ok" for record in output["records"])
                assert all(record["result"]["model_alert_threshold"] == release["alert_threshold"] for record in output["records"])
                assert all(record["result"]["history_cutoff_utc"] == "2023-06-24T04:00:00Z" for record in output["records"])
            else:
                assert all(record["reason_code"] == "MISSING_REQUIRED_FEATURE" and record["result"] is None for record in output["records"])
        rejected = directory / "rejected.json"
        result = subprocess.run([sys.executable, str(model / "prepare_request.py"), "--bundle", str(model),
            "--inputs", str(inputs), "--time", "2024-01-01T05:00:00Z", "--mobility-id", "145550", "--request", str(rejected)],
            cwd=directory, env=environment, capture_output=True, text=True)
        assert result.returncode == 2 and not rejected.exists()
    print("Handover and nested model/input hashes verified.")
    print("Approved scope verified: 441 roads / 666 contexts.")
    print("Standalone actual example replay: exact match.")
    print("441 available-hour predictions: OK; 441 March-gap records: explicit missing-feature errors.")
    print("Pending shared-native mobility ID rejected without fabricated output.")
    print("No fitting, model changes or 2024 outcome evaluation performed.")


def main():
    own_directory = Path(__file__).resolve().parent
    default = own_directory if (own_directory / "model/release_manifest.json").exists() else (
        own_directory.parent / "models/climate_pre2024_v1")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=default)
    verify(parser.parse_args().release)


if __name__ == "__main__":
    main()
