"""Native offline climate inference. This is not the shared contract v0.2 adapter."""
import argparse
import hashlib
import importlib.metadata
import json
import math
from numbers import Real
from pathlib import Path
import sys
import tempfile

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from climate_history_features import HistoricalReportFeatures

FORMAT = "climate-history-native-0.1"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_text(value):
    return value.isoformat().replace("+00:00", "Z")


class RecordError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ClimateHistoryPredictor:
    def __init__(self, bundle):
        self.bundle = Path(bundle).resolve()
        self.manifest = json.loads((self.bundle / "release_manifest.json").read_text())
        if self.manifest["native_format_version"] != FORMAT:
            raise ValueError("Unsupported native interface version")
        for name, digest in self.manifest["assets"].items():
            path = (self.bundle / name).resolve()
            if path.parent != self.bundle or sha256(path) != digest:
                raise ValueError(f"Package asset mismatch: {name}")
        for package, version in self.manifest["inference_package_versions"].items():
            if importlib.metadata.version(package) != version:
                raise ValueError(f"Package version mismatch: {package}")
        if sys.version.split()[0] != self.manifest["python_version"]:
            raise ValueError("Python version differs from the tested release")
        self.experiment = json.loads((self.bundle / "experiment.json").read_text())
        self.model = joblib.load(self.bundle / "history_tree.joblib")
        if (list(self.model.classes_) != [0, 1]
                or list(self.model.feature_names_in_) != self.experiment["features"]):
            raise ValueError("Model class/feature definition mismatch")
        self.bridge = pd.read_parquet(self.bundle / "street_cell_bridge.parquet")
        if self.bridge.street_segment_id.duplicated().any() or self.bridge.isna().any().any():
            raise ValueError("Invalid historical street bridge")
        self.bridge = self.bridge.set_index("street_segment_id")
        policy = self.experiment["history_policy"]
        self.history = HistoricalReportFeatures(
            pd.read_parquet(self.bundle / "history_events.parquet"),
            training_start=self.experiment["training_interval"][0],
            training_end=self.experiment["training_interval"][1],
            lookback_days=policy["lookback_days"], delay_days=policy["availability_delay_days"])
        self.base_features = self.experiment["base_features"]

    def _prepare(self, record):
        required = {"request_id", "street_segment_id", "prediction_time_utc", "features"}
        missing = required - record.keys()
        if missing:
            raise RecordError("INVALID_INPUT", f"Missing fields: {sorted(missing)}")
        if record.keys() != required:
            raise RecordError("INVALID_INPUT", "Unexpected record fields")
        segment = record["street_segment_id"]
        if not isinstance(segment, str) or segment not in self.bridge.index:
            raise RecordError("UNSUPPORTED_CONTEXT", "Unknown physical street segment")
        raw_time = record["prediction_time_utc"]
        if not isinstance(raw_time, str) or not raw_time.endswith("Z"):
            raise RecordError("INVALID_INPUT", "Prediction time must be ISO 8601 UTC ending Z")
        try:
            timestamp = pd.Timestamp(raw_time)
            if pd.isna(timestamp) or timestamp.tz is None or timestamp != timestamp.floor("h"):
                raise ValueError("Not a resolved UTC hour")
        except (ValueError, TypeError, OverflowError) as error:
            raise RecordError("INVALID_INPUT", "Invalid prediction hour") from error
        lower, upper = map(pd.Timestamp, self.manifest["supported_snapshot_interval"])
        if not lower <= timestamp < upper:
            raise RecordError("UNSUPPORTED_CONTEXT", "Prediction outside this retrospective snapshot")
        features = record["features"]
        if not isinstance(features, dict):
            raise RecordError("INVALID_INPUT", "Features must be an object")
        missing = set(self.base_features) - features.keys()
        if missing:
            raise RecordError("MISSING_REQUIRED_FEATURE", f"Missing features: {sorted(missing)}")
        if set(features) != set(self.base_features):
            raise RecordError("INVALID_INPUT", "Supply exactly the eight base features; history is computed internally")
        for name in self.base_features:
            value = features[name]
            try:
                valid_number = (not isinstance(value, bool) and isinstance(value, Real)
                                and math.isfinite(float(value)))
            except (OverflowError, ValueError, TypeError):
                valid_number = False
            if not valid_number:
                raise RecordError("INVALID_INPUT", f"Invalid numeric feature: {name}")
            if name != "elevation_mean_m" and value < 0:
                raise RecordError("INVALID_INPUT", f"Negative feature: {name}")
        if features["length_m"] <= 0:
            raise RecordError("INVALID_INPUT", "Street length must be positive")
        rain = [features[f"rain_{hour}h_mm"] for hour in [1, 3, 6, 24]]
        if (np.diff(rain) < -1e-9).any():
            raise RecordError("INVALID_INPUT", "Rainfall accumulations must be nondecreasing")
        cell = self.bridge.loc[segment]
        identity = {"street_segment_id": segment, "prediction_time_utc": timestamp,
                    "grid_id": cell.grid_id, "cell_id": int(cell.cell_id)}
        return timestamp, features, identity

    def predict(self, request, request_sha256=None):
        if (not isinstance(request, dict) or set(request) != {"format_version", "package_id", "records"}
                or request["format_version"] != FORMAT
                or request["package_id"] != self.manifest["package_id"]):
            raise ValueError("Invalid native request envelope or package ID")
        records = request["records"]
        if not isinstance(records, list) or not records:
            raise ValueError("Records must be a nonempty array")
        identifiers = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Each record must be an identified object")
            identifier = record.get("request_id")
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("Each record needs a nonempty string request_id")
            identifiers.append(identifier)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate request IDs")
        results, prepared = [], []
        for record in records:
            result = {"request_id": record["request_id"], "status": "error",
                      "reason_code": None, "warnings": [], "result": None}
            try:
                timestamp, features, identity = self._prepare(record)
                prepared.append((len(results), timestamp, features, identity))
            except RecordError as error:
                result.update(reason_code=error.code, message=str(error))
            results.append(result)
        if prepared:
            identities = pd.DataFrame([item[3] for item in prepared])
            history = self.history.transform(identities)
            inputs = pd.DataFrame([item[2] for item in prepared], columns=self.base_features).astype("float64")
            for name in self.history.feature_names:
                inputs[name] = history[name].to_numpy()
            with threadpool_limits(limits=2):
                scores = self.model.predict_proba(inputs[self.experiment["features"]])[:, 1]
            if not (np.isfinite(scores).all() and (scores >= 0).all() and (scores <= 1).all()):
                raise RuntimeError("Inference returned invalid probabilities")
            train_start, train_end = map(pd.Timestamp, self.experiment["training_interval"])
            for i, (position, timestamp, _, identity) in enumerate(prepared):
                warnings = ["UNCALIBRATED_REPORT_PROXY", "WEAK_REUSED_DEVELOPMENT_PERFORMANCE",
                            "HISTORICAL_AVAILABILITY_ASSUMED"]
                if train_start <= timestamp < train_end:
                    warnings.append("TRAINING_PERIOD_REPLAY")
                score = float(scores[i])
                results[position].update(status="ok", warnings=warnings, result={
                    "target": self.manifest["target"],
                    "street_segment_id": identity["street_segment_id"],
                    "prediction_time_utc": utc_text(timestamp),
                    "target_start_utc": utc_text(timestamp),
                    "target_end_utc": utc_text(timestamp + pd.Timedelta(hours=1)),
                    "prediction_horizon_hours": 1,
                    "latest_rainfall_end_utc": utc_text(timestamp - pd.Timedelta(hours=3)),
                    "availability_policy_id": "mrms-pass2-assumed-lag3h-v1",
                    "history_cutoff_utc": utc_text(history.iloc[i].history_cutoff_utc),
                    "history_policy_id": "training-only-365d-delay7d-v1",
                    "reported_flooding_score": score, "score_calibrated": False,
                    "model_alert": score >= self.manifest["alert_threshold"],
                    "model_alert_threshold": self.manifest["alert_threshold"],
                    "severity": None, "water_depth_m": None})
        return {"format_version": FORMAT, "package_id": self.manifest["package_id"],
                "request_sha256": request_sha256, "records": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    temporary = None
    try:
        if args.output.exists():
            raise FileExistsError("Use a new output filename; completed results are immutable")
        raw = args.input.read_bytes()
        request = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid JSON number: {value}")))
        output = ClimateHistoryPredictor(args.bundle).predict(request, hashlib.sha256(raw).hexdigest())
        with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(output, handle, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(args.output)
        print(f"Saved {len(output['records'])} results: {args.output}")
        return 1 if any(record["status"] == "error" for record in output["records"]) else 0
    except Exception as error:
        print(f"FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
