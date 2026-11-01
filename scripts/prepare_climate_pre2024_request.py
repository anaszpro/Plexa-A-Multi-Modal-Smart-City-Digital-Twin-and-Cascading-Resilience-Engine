"""Prepare native requests from verified normalized climate inputs, optionally infer.

Missing rainfall yields an identified native request with rainfall fields omitted;
the native runtime returns MISSING_REQUIRED_FEATURE. No input is zero-filled.
This module is copied into the standalone model bundle as prepare_request.py.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

BASE = ["elevation_mean_m", "terrain_endpoint_grade_abs", "terrain_relief_m", "length_m",
        "rain_1h_mm", "rain_3h_mm", "rain_6h_mm", "rain_24h_mm"]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def prepare(inputs, bundle, timestamp, mobility_ids=None):
    inputs, bundle = Path(inputs).resolve(), Path(bundle).resolve()
    manifest = json.loads((inputs / "input_manifest.json").read_text())
    for name, digest in manifest["assets"].items():
        path = (inputs / name).resolve()
        if path.parent != inputs or sha256(path) != digest:
            raise ValueError(f"Normalized input asset changed: {name}")
    if sha256(bundle / "release_manifest.json") != manifest["model_release_manifest_sha256"]:
        raise ValueError("Normalized inputs reference a different model release")
    release = json.loads((bundle / "release_manifest.json").read_text())
    if release["package_id"] != manifest["package_id"]:
        raise ValueError("Model/input package identity differs")
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise ValueError("Prediction time must be a UTC ISO timestamp ending Z")
    time = pd.Timestamp(timestamp)
    if time.tz is None or pd.isna(time) or time != time.floor("h"):
        raise ValueError("Prediction time must be a resolved whole UTC hour")
    start, end = map(pd.Timestamp, manifest["utc_interval"])
    if not start <= time < end:
        raise ValueError("Prediction hour outside normalized scenario coverage")
    crosswalk = pd.read_csv(inputs / "approved_mobility_crosswalk.csv", dtype={"mobility_segment_id": str})
    if crosswalk.mobility_segment_id.duplicated().any() or crosswalk.street_segment_id.duplicated().any():
        raise ValueError("Initial serving scope requires one-to-one reviewed mapping")
    if mobility_ids is not None:
        if not mobility_ids or len(mobility_ids) != len(set(mobility_ids)):
            raise ValueError("Specify distinct nonempty mobility IDs")
        unsupported = set(mobility_ids)-set(crosswalk.mobility_segment_id)
        if unsupported:
            raise ValueError(f"Unsupported or pending mobility roads: {sorted(unsupported)}")
        crosswalk = crosswalk.loc[crosswalk.mobility_segment_id.isin(mobility_ids)]
    static = pd.read_parquet(inputs / "static_by_road.parquet")
    weather = pd.read_parquet(inputs / "rainfall_by_cell_hour.parquet", filters=[
        ("prediction_time_utc", "==", time.to_pydatetime())])
    if weather.duplicated(["grid_id", "cell_id"]).any():
        raise ValueError("Duplicate rainfall cell at selected hour")
    rows = crosswalk[["mobility_segment_id", "street_segment_id"]].merge(
        static, on="street_segment_id", how="left", validate="one_to_one").merge(
            weather, on=["grid_id", "cell_id"], how="left", validate="many_to_one")
    if rows.empty or not np.isfinite(rows[BASE[:4]].to_numpy(dtype=float)).all():
        raise ValueError("Missing or invalid static road inputs")
    records, lineage = [], []
    for row in rows.sort_values("street_segment_id").itertuples(index=False):
        identifier = f"pre2024:{row.street_segment_id}:{time:%Y%m%dT%H%M%SZ}"
        features = {name: float(getattr(row, name)) for name in BASE[:4]}
        rain = np.array([getattr(row, name) for name in BASE[4:]], dtype=float)
        valid = (row.rainfall_inputs_valid == True and np.isfinite(rain).all()
                 and (rain >= 0).all() and (np.diff(rain) >= -1e-9).all())
        if valid:
            features.update({name: float(value) for name, value in zip(BASE[4:], rain)})
        records.append({"request_id": identifier, "street_segment_id": row.street_segment_id,
                        "prediction_time_utc": time.isoformat().replace("+00:00", "Z"), "features": features})
        lineage.append({"request_id": identifier, "mobility_segment_id": row.mobility_segment_id,
                        "street_segment_id": row.street_segment_id, "grid_id": row.grid_id,
                        "cell_id": int(row.cell_id),
                        "input_coverage_status": "available" if valid else "rainfall_inputs_unavailable",
                        "latest_rainfall_end_utc": (time-pd.Timedelta(hours=3)).isoformat().replace("+00:00", "Z")})
    return ({"format_version": release["native_format_version"], "package_id": release["package_id"], "records": records},
            {"input_manifest_sha256": sha256(inputs / "input_manifest.json"),
             "model_release_manifest_sha256": sha256(bundle / "release_manifest.json"), "records": lineage})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--time", required=True)
    parser.add_argument("--mobility-id", action="append")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    context_path = args.request.with_name(args.request.stem + ".context.json")
    try:
        for path in [args.request, context_path, args.result]:
            if path is not None and path.exists():
                raise FileExistsError(f"Use a new output name: {path}")
        request, context = prepare(args.inputs, args.bundle, args.time, args.mobility_id)
        write_new(args.request, request)
        write_new(context_path, context)
        print(f"Prepared {len(request['records'])} records; context: {context_path}")
        if args.result is not None:
            result = subprocess.run([sys.executable, str(args.bundle.resolve() / "predict.py"),
                "--bundle", str(args.bundle.resolve()), "--input", str(args.request), "--output", str(args.result)])
            return result.returncode
        return 0
    except Exception as error:
        print(f"FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
