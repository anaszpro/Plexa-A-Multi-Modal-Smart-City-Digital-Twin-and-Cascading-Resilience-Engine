"""Resumable, natural-prevalence 2025 development evaluation; never fits models.

Example:
    python scripts/validate_climate_history.py --run-dir data/processed/climate_baseline_v3_runs/RUN
Use --max-windows 1 for a resumable first-window benchmark. Results are reused
development evidence, not an independent test of the history experiments.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile
import time

import duckdb
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from climate_history_features import HistoricalReportFeatures

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_text(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def atomic_write(path, text):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_once(path, text):
    path = Path(path)
    if path.exists():
        if path.read_text() != text:
            raise ValueError(f"Existing artifact differs: {path}; use a new output directory")
    else:
        atomic_write(path, text)


def utc_windows(start, end):
    """UTC calendar windows, with partial first/last days."""
    while start < end:
        stop = min(start.normalize() + pd.Timedelta(days=1), end)
        yield start, stop
        start = stop


def score_bins(scores, edges):
    scores = np.asarray(scores)
    if not (np.isfinite(scores).all() and (scores >= 0).all() and (scores <= 1).all()):
        raise ValueError("Nonfinite or out-of-range model probabilities")
    return np.searchsorted(edges, scores, side="right") - 1


def curve_from_bins(pos, neg, edges):
    pos, neg = np.asarray(pos), np.asarray(neg)
    if (pos.shape != edges.shape or neg.shape != edges.shape
            or not np.issubdtype(pos.dtype, np.integer)
            or not np.issubdtype(neg.dtype, np.integer)
            or (pos < 0).any() or (neg < 0).any()):
        raise ValueError("Invalid score-bin counts")
    positives = int(pos.sum())
    tp, fp = np.cumsum(pos[::-1]), np.cumsum(neg[::-1])
    alerts = tp + fp
    precision = np.divide(tp, alerts, out=np.zeros(len(pos)), where=alerts > 0)
    recall = tp / positives if positives else np.zeros(len(pos))
    f1 = np.divide(2.0 * tp, positives + alerts,
                   out=np.zeros(len(pos)), where=(positives + alerts) > 0)
    ap = float(np.sum(pos[::-1] / positives * precision)) if positives else None
    curve = pd.DataFrame({"threshold": edges[::-1], "precision": precision,
                          "recall": recall, "F1": f1, "TP": tp, "FP": fp,
                          "FN": positives - tp, "TN": int(neg.sum()) - fp,
                          "alerts": alerts})
    return curve, ap


def best_threshold(curve):
    return curve.loc[curve.F1 == curve.F1.max()].sort_values(
        ["alerts", "threshold"], ascending=[True, False]).iloc[0]


def metrics_at_threshold(pos, neg, edges, threshold):
    matching = np.flatnonzero(edges == threshold)
    if len(matching) != 1:
        raise ValueError("Threshold must match exactly one score-bin edge")
    index = int(matching[0])
    _, ap = curve_from_bins(pos, neg, edges)
    positives, negatives = int(pos.sum()), int(neg.sum())
    tp, fp = int(pos[index:].sum()), int(neg[index:].sum())
    alerts = tp + fp
    return {"rows": positives + negatives, "positive_rows": positives,
            "negative_rows": negatives, "approximate_AP": ap,
            "precision": tp / alerts if alerts else 0.0,
            "recall": tp / positives if positives else None,
            "F1": 2 * tp / (positives + alerts) if positives + alerts else 0.0,
            "TP": tp, "FP": fp, "FN": positives - tp, "TN": negatives - fp,
            "alerts": alerts}


def verify_saved_window(path, plan_hash, start, end, edges, boroughs):
    with np.load(path, allow_pickle=False) as saved:
        metadata = json.loads(str(saved["metadata_json"].item()))
        if (metadata["evaluation_plan_sha256"] != plan_hash
                or metadata["start_utc"] != start.isoformat()
                or metadata["end_utc_exclusive"] != end.isoformat()
                or not np.array_equal(saved["score_edges"], edges)
                or saved["boroughs"].tolist() != boroughs):
            raise ValueError(f"Checkpoint provenance mismatch: {path}")
        pos, neg = saved["positive"].copy(), saved["negative"].copy()
    shape = (len(boroughs), len(edges))
    for array in (pos, neg):
        if array.shape != shape or not np.issubdtype(array.dtype, np.integer) or (array < 0).any():
            raise ValueError(f"Invalid checkpoint counts: {path}")
    if (int(pos.sum()) != metadata["positive_rows"]
            or int(neg.sum()) != metadata["negative_rows"]
            or int(pos.sum() + neg.sum()) != metadata["rows"]):
        raise ValueError(f"Checkpoint totals mismatch: {path}")
    return pos, neg, metadata


def evaluate_window(con, model, features, start, end, edges, boroughs, batch_size):
    shape = (len(boroughs), len(edges))
    pos, neg = np.zeros(shape, dtype=np.int64), np.zeros(shape, dtype=np.int64)
    borough_index = {name: index for index, name in enumerate(boroughs)}
    # Names were validated before interpolation. The lookup owns the extra features.
    select = ", ".join(f'{"h" if name.startswith(("segment_positive_hours_", "cell_positive_hours_")) else "e"}."{name}"'
                       for name in features)
    reader = con.execute(f'''SELECT {select}, e.label, h.borough
        FROM validation_full e JOIN history_lookup h USING(street_segment_id)
        WHERE e.prediction_time_utc >= ?::TIMESTAMPTZ
          AND e.prediction_time_utc < ?::TIMESTAMPTZ''',
        [start.isoformat(), end.isoformat()]).to_arrow_reader(batch_size=batch_size)
    with threadpool_limits(limits=2):
        for batch in reader:
            frame = batch.to_pandas()
            inputs = frame[features].astype("float64")
            labels = frame.label.to_numpy()
            if not np.isfinite(inputs.to_numpy()).all() or not np.isin(labels, [0, 1]).all():
                raise ValueError("Invalid eligible development row")
            bins = score_bins(model.predict_proba(inputs)[:, 1], edges)
            for borough, positions in frame.groupby("borough", sort=False).indices.items():
                index = borough_index[borough]
                selected = labels[positions] == 1
                pos[index] += np.bincount(bins[positions][selected], minlength=len(edges))
                neg[index] += np.bincount(bins[positions][~selected], minlength=len(edges))
    return pos, neg


def run(args):
    run_dir = args.run_dir.resolve()
    dataset_dir = args.dataset_dir.resolve()
    output = args.output_dir.resolve() if args.output_dir else run_dir / "development_validation"
    checkpoint_path = run_dir / "checkpoint_manifest.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    experiment = json.loads((run_dir / "experiment.json").read_text())
    for name, evidence in checkpoint["assets"].items():
        asset = (run_dir / name).resolve()
        if asset.parent != run_dir or sha256(asset) != evidence["sha256"]:
            raise ValueError(f"Asset hash/path mismatch: {name}")
    helper = ROOT / checkpoint["history_helper"]["project_path"]
    if sha256(helper) != checkpoint["history_helper"]["sha256"]:
        raise ValueError("Historical feature helper changed")
    for package, expected in checkpoint["package_versions"].items():
        if importlib.metadata.version(package) != expected:
            raise ValueError(f"Environment version differs for {package}")
    if sys.version.split()[0] != checkpoint["python_version"]:
        raise ValueError("Python version differs from the model checkpoint")
    dataset_manifest_path = dataset_dir / "manifest.json"
    if sha256(dataset_manifest_path) != checkpoint["preparation_evidence_sha256"]["dataset_manifest"]:
        raise ValueError("Dataset manifest does not match the training evidence")
    dataset_manifest = json.loads(dataset_manifest_path.read_text())
    for name, expected in dataset_manifest["input_sha256"].items():
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        if sha256(path) != expected:
            raise ValueError(f"Prepared dataset input changed: {path}")
    preparation = dataset_manifest["configuration"]
    if (experiment["development_interval"] != preparation["splits"]["validation"]
            or experiment["training_interval"] != preparation["splits"]["train"]):
        raise ValueError("Experiment intervals differ from the prepared dataset")
    start, end = map(pd.Timestamp, experiment["development_interval"])
    policy = experiment["history_policy"]
    train_start, train_end = map(pd.Timestamp, experiment["training_interval"])
    if start - pd.Timedelta(days=policy["availability_delay_days"]) < train_end:
        raise ValueError("History cannot be cached: cutoff varies within development")
    model = joblib.load(run_dir / "history_tree.joblib")
    features = experiment["features"]
    if (list(model.classes_) != [0, 1] or list(model.feature_names_in_) != features
            or features != checkpoint["features"]
            or not all(name.replace("_", "").isalnum() for name in features)):
        raise ValueError("Model class/feature definition mismatch")
    for parameter, value in experiment["proposed_tree_parameters"].items():
        if model.get_params()[parameter] != value:
            raise ValueError(f"Model parameter mismatch: {parameter}")
    history = pd.read_parquet(run_dir / "history_events.parquet")
    if len(history) != checkpoint["training_positive_rows"]:
        raise ValueError("Historical source count mismatch")
    bridge = pd.read_parquet(run_dir / "street_cell_bridge.parquet")
    if bridge.street_segment_id.duplicated().any():
        raise ValueError("Duplicate segment in bridge")
    builder = HistoricalReportFeatures(history, training_start=train_start,
        training_end=train_end, lookback_days=policy["lookback_days"],
        delay_days=policy["availability_delay_days"])
    if features != experiment["base_features"] + builder.feature_names:
        raise ValueError("Historical feature definitions mismatch")
    identities = bridge.assign(prediction_time_utc=start)
    transformed = builder.transform(identities)
    if not transformed.history_cutoff_utc.eq(train_end).all():
        raise ValueError("Expected frozen training history")
    lookup = bridge.copy()
    for name in builder.feature_names:
        lookup[name] = transformed[name].to_numpy()
    edges = np.unique(np.concatenate(([0.0], np.geomspace(1e-12, 1.0, 4096))))
    report_path = ROOT / checkpoint.get("pretraining_report_project_path",
                                        "data/processed/baseline_pretraining_report.json")
    if sha256(report_path) != checkpoint["preparation_evidence_sha256"]["pretraining_report"]:
        raise ValueError("Preparation report changed")
    preparation_report = json.loads(report_path.read_text())
    expected_counts = {row["label"]: row["rows"] for row in preparation_report["split_counts"]
                       if row["split"] == "validation"}
    con = duckdb.connect(str(dataset_dir / "dataset.duckdb"), read_only=True)
    try:
        con.execute("SET threads=2")
        con.execute("SET memory_limit='2GB'")
        con.execute("SET TimeZone='UTC'")
        native = con.execute('''SELECT street_segment_id, grid_id, cell_id,
            coalesce(borough_at_midpoint, 'Unassigned') AS borough FROM street_features''').fetchdf()
        if len(native) != len(bridge) or native.street_segment_id.duplicated().any():
            raise ValueError("Dataset street bridge cardinality differs")
        lookup = lookup.merge(native, on=["street_segment_id", "grid_id", "cell_id"],
                              validate="one_to_one", how="inner")
        if len(lookup) != len(bridge):
            raise ValueError("Dataset street/grid identities differ from saved bridge")
        boroughs = sorted(lookup.borough.unique().tolist())
        con.register("history_lookup", lookup)
        plan = {"run_id": checkpoint["run_id"], "checkpoint_sha256": sha256(checkpoint_path),
            "dataset_manifest_sha256": sha256(dataset_manifest_path),
            "dataset_database_sha256": sha256(dataset_dir / "dataset.duckdb"),
            "validator_sha256": sha256(Path(__file__)),
            "start_utc": start.isoformat(), "end_utc_exclusive": end.isoformat(),
            "feature_names": features, "boroughs": boroughs, "batch_size": args.batch_size,
            "score_bins": "unique([0] + geomspace(1e-12, 1, 4096))",
            "threshold_selection": "maximum_F1_then_fewest_alerts_then_highest_threshold",
            "AP": "global_grouped_score_approximation_not_mean_batch_AP",
            "population": experiment.get("evaluation_population", "natural_eligible_2025_reused_development"),
            "history_cutoff_utc": train_end.isoformat(), "test_data_queried": False}
        output.mkdir(parents=True, exist_ok=True)
        write_once(output / "evaluation_plan.json", json_text(plan))
        plan_hash = sha256(output / "evaluation_plan.json")
        windows = list(utc_windows(start, end))
        records, pos_total, neg_total = [], np.zeros((len(boroughs), len(edges)), dtype=np.int64), np.zeros((len(boroughs), len(edges)), dtype=np.int64)
        newly_processed = 0
        for number, (lower, upper) in enumerate(windows, 1):
            path = output / f"{lower:%Y%m%dT%H%M%SZ}.npz"
            if not path.exists():
                if args.max_windows is not None and newly_processed >= args.max_windows:
                    break
                began = time.perf_counter()
                pos, neg = evaluate_window(con, model, features, lower, upper, edges, boroughs, args.batch_size)
                metadata = {"evaluation_plan_sha256": plan_hash,
                    "start_utc": lower.isoformat(), "end_utc_exclusive": upper.isoformat(),
                    "rows": int(pos.sum() + neg.sum()), "positive_rows": int(pos.sum()),
                    "negative_rows": int(neg.sum()), "elapsed_seconds": time.perf_counter() - began}
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=output, delete=False) as handle:
                        temporary = Path(handle.name)
                        np.savez_compressed(handle, positive=pos, negative=neg, score_edges=edges,
                            boroughs=np.asarray(boroughs, dtype=str), metadata_json=json_text(metadata))
                    temporary.replace(path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                newly_processed += 1
            pos, neg, metadata = verify_saved_window(path, plan_hash, lower, upper, edges, boroughs)
            records.append({**metadata, "file": path.name, "sha256": sha256(path)})
            pos_total += pos
            neg_total += neg
            if number % 10 == 0 or number == len(windows) or args.max_windows is not None:
                print(f"{number}/{len(windows)} windows verified; {int(pos_total.sum()+neg_total.sum()):,} rows; latest window {metadata['elapsed_seconds']:.2f}s", flush=True)
        complete = len(records) == len(windows)
        status = {"stage": "COMPLETE" if complete else "PARTIAL", "completed_windows": len(records),
                  "expected_windows": len(windows), "rows": int(pos_total.sum()+neg_total.sum()),
                  "positive_rows": int(pos_total.sum()), "test_data_queried": False}
        if complete:
            if int(pos_total.sum()) != expected_counts[1] or int(neg_total.sum()) != expected_counts[0]:
                raise ValueError("Full development totals differ from preparation evidence")
            curve, _ = curve_from_bins(pos_total.sum(axis=0), neg_total.sum(axis=0), edges)
            threshold = float(best_threshold(curve).threshold)
            metrics = metrics_at_threshold(pos_total.sum(axis=0), neg_total.sum(axis=0), edges, threshold)
            days = (end - start).total_seconds() / 86400
            metrics.update({"threshold": threshold, "mean_alerts_per_24h": metrics["alerts"]/days,
                            "mean_false_alerts_per_24h": metrics["FP"]/days})
            borough_rows = [{"borough": name, **metrics_at_threshold(pos_total[i], neg_total[i], edges, threshold)}
                            for i, name in enumerate(boroughs)]
            daily_rows = []
            for record, (lower, upper) in zip(records, windows):
                pos, neg, _ = verify_saved_window(output / record["file"], plan_hash, lower, upper, edges, boroughs)
                daily_rows.append({"start_utc": lower.isoformat(), "end_utc_exclusive": upper.isoformat(),
                    "window_hours": (upper-lower).total_seconds()/3600,
                    **metrics_at_threshold(pos.sum(axis=0), neg.sum(axis=0), edges, threshold)})
            for field in ["rows", "positive_rows", "negative_rows", "TP", "FP", "FN", "TN", "alerts"]:
                if sum(row[field] for row in daily_rows) != metrics[field] or sum(row[field] for row in borough_rows) != metrics[field]:
                    raise ValueError(f"Grouped metric totals do not reconcile: {field}")
            write_once(output / "precision_recall.csv", curve.to_csv(index=False))
            write_once(output / "borough_metrics.csv", pd.DataFrame(borough_rows).to_csv(index=False))
            write_once(output / "daily_metrics.csv", pd.DataFrame(daily_rows).to_csv(index=False))
            result = {"status": "development_evaluated_not_independently_tested",
                "run_id": checkpoint["run_id"], "evaluation_plan_sha256": plan_hash,
                "metrics": metrics, "score_calibrated": False,
                "test_data_queried": False, "validation_reused_for_development": True,
                "window_evidence": [{"file": r["file"], "sha256": r["sha256"]} for r in records]}
            write_once(output / "validation_metrics.json", json_text(result))
            print(json_text(metrics), flush=True)
        atomic_write(output / "progress.json", json_text(status))
        print(f"{status['stage']}: {output}", flush=True)
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "data/processed/baseline_dataset_v1")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--batch-size", type=int, default=100000)
    parser.add_argument("--max-windows", type=int)
    args = parser.parse_args()
    if args.batch_size < 1 or (args.max_windows is not None and args.max_windows < 1):
        parser.error("Batch size and max windows must be positive")
    run(args)


if __name__ == "__main__":
    main()
