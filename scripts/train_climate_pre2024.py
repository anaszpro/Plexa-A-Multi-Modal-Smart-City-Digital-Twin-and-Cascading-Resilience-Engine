"""Approved pre-2024 preflight, isolated dataset views and versioned baseline fit.

Citywide fitting; the approved 441-road scope controls later serving/export.
No 2024 outcomes are queried for fitting, diagnostics or threshold selection.
"""
import argparse
import copy
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import time
import warnings

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits

from climate_history_features import HistoricalReportFeatures
from validate_climate_history import atomic_write, json_text, sha256

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / "config/climate_pre2024_temporal_proposal_v2.json"
CONFIG = ROOT / "config/climate_pre2024_experiment_v1.json"
RUN = ROOT / "data/processed/climate_pre2024_runs/baseline-v1"
DATASET = ROOT / "data/processed/baseline_dataset_pre2024_v1"


def write_new(path, value):
    with Path(path).open("x") as handle:
        handle.write(json_text(value))


def availability_mask(times, start, end, available_by):
    times = pd.to_datetime(times, utc=True)
    return (times.ge(pd.Timestamp(start)) & times.lt(pd.Timestamp(end))
            & (times + pd.Timedelta(hours=1, days=7)).le(pd.Timestamp(available_by)))


def longest_dry_run(frame, expected_cells):
    run = longest = 0
    for row in frame.itertuples(index=False):
        dry = (row.cells == expected_cells and row.valid_cells == expected_cells
               and row.max_rain == 0)
        run = run + 1 if dry else 0
        longest = max(longest, run)
    return longest


def run():
    if RUN.exists() or DATASET.exists() or CONFIG.exists():
        raise FileExistsError("New experiment assets already exist; do not overwrite or refit silently")
    began = time.perf_counter()
    proposal = json.loads(PROPOSAL.read_text())
    parent_dir = ROOT / "data/processed/baseline_dataset_v1"
    parent_manifest = json.loads((parent_dir / "manifest.json").read_text())
    if sha256(parent_dir / "manifest.json") != proposal["provenance"]["dataset_manifest_sha256"]:
        raise ValueError("Prepared manifest differs from approved proposal")
    if sha256(ROOT / "scripts/climate_history_features.py") != proposal["provenance"]["history_helper_sha256"]:
        raise ValueError("History implementation differs from approved proposal")
    versions = {name: importlib.metadata.version(name) for name in
                ["numpy", "pandas", "scipy", "scikit-learn", "joblib", "threadpoolctl", "duckdb", "pyarrow"]}
    previous = json.loads((ROOT / "data/processed/climate_baseline_v3_runs/20261025T030202Z_74a0334f/checkpoint_manifest.json").read_text())
    if versions != previous["package_versions"] or sys.version.split()[0] != previous["python_version"]:
        raise ValueError("Runtime differs from tested reference environment")
    for number, (name, expected) in enumerate(parent_manifest["input_sha256"].items(), 1):
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        if sha256(path) != expected:
            raise ValueError(f"Prepared asset changed: {name}")
        if number % 25 == 0:
            print(f"PREFLIGHT hashes {number}/{len(parent_manifest['input_sha256'])}", flush=True)
    experiment = {
        "experiment_id": "climate-pre2024-v1", "status": "approved_frozen_before_fitting",
        "approval": "Anas approval supplied directly by user: revised split, fixed history and 441-road serving scope",
        "proposal_sha256": sha256(PROPOSAL), "target": proposal["target"],
        "training_interval": [proposal["training"]["prediction_start_inclusive"], proposal["training"]["prediction_end_exclusive"]],
        "development_interval": [proposal["development"]["prediction_start_inclusive"], proposal["development"]["prediction_end_exclusive"]],
        "label_availability": proposal["availability_policy"],
        "partition_label_cutoffs": {name: proposal[name]["labels_available_by"] for name in ["training", "development"]},
        "features": proposal["model"]["features"], "base_features": proposal["model"]["features"][:8],
        "additional_features": proposal["model"]["features"][8:],
        "history_policy": {"source": "pre2024_training_eligible_positive_segment_hours",
                           "lookback_days": 365, "availability_delay_days": 7,
                           "cutoff": "min(prediction_time_utc - 7 days, training_end)",
                           "interval": "[cutoff - 365 days, cutoff)", "count_unit": "positive_segment_hours",
                           "validation_labels_used_as_history": False, "publication_availability": "assumed_not_verified"},
        "sampling_policy": proposal["training_sampling"],
        "proposed_tree_parameters": proposal["model"]["initial_reference_parameters_for_review"],
        "evaluation_population": "full_citywide_eligible_pre2024_July_to_December_2023_development",
        "evaluation_policy": proposal["model"], "agreed_serving_scope": proposal["agreed_scope"],
        "fit_geography": proposal["fitting_geography"], "fitting_weights": "original_inverse_inclusion_weights",
        "no_2024_outcomes_in_fitting_tuning_or_history": True,
    }
    train_start, train_end = map(pd.Timestamp, experiment["training_interval"])
    dev_start, dev_end = map(pd.Timestamp, experiment["development_interval"])
    assert train_end + pd.Timedelta(days=7) == dev_start
    assert dev_end + pd.Timedelta(days=7) == pd.Timestamp(proposal["availability_policy"]["final_freeze_utc"])
    parent_con = duckdb.connect(str(parent_dir / "dataset.duckdb"), read_only=True)
    parent_con.execute("SET threads=2")
    parent_con.execute("SET TimeZone='UTC'")
    gap = parent_con.execute('''SELECT prediction_time_utc,count(*) AS cells,
        count(*) FILTER (WHERE weather_features_valid) AS valid_cells,max(rain_1h_mm) AS max_rain
        FROM weather WHERE prediction_time_utc>=?::TIMESTAMPTZ AND prediction_time_utc<?::TIMESTAMPTZ
        GROUP BY 1 ORDER BY 1''', [train_end.isoformat(), dev_start.isoformat()]).df()
    parent_con.close()
    expected_gap_hours = pd.date_range(train_end, dev_start, freq="h", inclusive="left")
    if not pd.DatetimeIndex(gap.prediction_time_utc).equals(expected_gap_hours):
        raise ValueError("Missing gap review hours")
    longest = longest_dry_run(gap, 2537)
    if longest < 6:
        raise ValueError("No six-hour citywide dry break found in approved split gap; review before fitting")
    print(f"PREFLIGHT split gap: {longest} consecutive complete citywide dry hours", flush=True)
    write_new(CONFIG, experiment)
    RUN.mkdir(parents=True)
    DATASET.mkdir(parents=True)
    shutil.copy2(CONFIG, RUN / "experiment.json")
    shutil.copy2(parent_dir / "dataset.duckdb", DATASET / "dataset.duckdb")
    con = duckdb.connect(str(DATASET / "dataset.duckdb"))
    try:
        con.execute("SET threads=2")
        con.execute("SET memory_limit='2GB'")
        con.execute("SET TimeZone='UTC'")
        # Controlled ISO timestamps only; input provenance was verified above.
        con.execute(f'''CREATE OR REPLACE VIEW eligible AS SELECT *, CASE
            WHEN prediction_time_utc>=TIMESTAMPTZ '{train_start.isoformat()}'
             AND prediction_time_utc<TIMESTAMPTZ '{train_end.isoformat()}' THEN 'train'
            WHEN prediction_time_utc>=TIMESTAMPTZ '{dev_start.isoformat()}'
             AND prediction_time_utc<TIMESTAMPTZ '{dev_end.isoformat()}' THEN 'validation'
            ELSE NULL END AS split FROM labeled WHERE label IS NOT NULL AND source_coverage_verified''')
        con.execute('''CREATE OR REPLACE VIEW training_sample AS SELECT *,
            CASE WHEN label=1 THEN 1.0 ELSE 5000.0 END AS sample_weight
            FROM eligible WHERE split='train' AND (label=1 OR
            md5_number_lower(street_segment_id || '|' || epoch(prediction_time_utc)::VARCHAR || 'plexa-baseline-v1') % 5000=0)''')
        con.execute("CREATE OR REPLACE VIEW validation_full AS SELECT * FROM eligible WHERE split='validation'")
        con.execute("DROP VIEW IF EXISTS test_full")
        print("PREFLIGHT measuring complete eligible partition counts", flush=True)
        counts = con.execute('''SELECT split,label,count(*) AS rows FROM eligible
            WHERE split IS NOT NULL GROUP BY 1,2 ORDER BY 1,2''').df()
        for split, name in [("train", "training"), ("validation", "development")]:
            expected = {0: proposal[name]["eligible_negative_rows"], 1: proposal[name]["eligible_positive_rows"]}
            actual = counts.loc[counts.split.eq(split)].set_index("label").rows.to_dict()
            if actual != expected:
                raise ValueError(f"Eligible counts differ from approved proposal: {split}, {actual}")
        print(counts.to_string(index=False), flush=True)
        print("PREFLIGHT loading deterministic training sample", flush=True)
        sample = con.execute("SELECT * FROM training_sample ORDER BY street_segment_id,prediction_time_utc").df()
        bridge = con.execute("SELECT street_segment_id,grid_id,cell_id FROM street_features ORDER BY street_segment_id").df()
        con.execute("CHECKPOINT")
    finally:
        con.close()
    preparation = copy.deepcopy(parent_manifest["configuration"])
    preparation["splits"] = {"train": experiment["training_interval"], "validation": experiment["development_interval"]}
    derived = copy.deepcopy(parent_manifest)
    derived.update({"configuration": preparation, "source_preparation_configuration": parent_manifest["configuration"],
                    "parent_manifest_sha256": sha256(parent_dir / "manifest.json"),
                    "status": "derived_pre2024_temporal_views_no_source_data_changed",
                    "views": ["labeled", "eligible", "training_sample", "validation_full"],
                    "training_performed": False})
    derived["input_sha256"][str(CONFIG)] = sha256(CONFIG)
    write_new(DATASET / "manifest.json", derived)
    report = {"split_counts": counts.to_dict("records"), "checks_passed": True,
              "split_gap_longest_complete_citywide_dry_hours": longest, "training_performed": False,
              "source_input_hashes_verified": len(parent_manifest["input_sha256"]),
              "dataset_manifest_sha256": sha256(DATASET / "manifest.json")}
    write_new(RUN / "pretraining_report.json", report)
    if sample.duplicated(["street_segment_id", "prediction_time_utc"]).any():
        raise ValueError("Duplicate sampled target hours")
    if not availability_mask(sample.prediction_time_utc, train_start, train_end,
                             proposal["training"]["labels_available_by"]).all():
        raise ValueError("Training target availability violation")
    positive = sample.label.eq(1)
    if positive.sum() != proposal["training"]["eligible_positive_rows"]:
        raise ValueError("Not all eligible positives were retained")
    if not sample.sample_weight.eq(np.where(positive, 1.0, 5000.0)).all():
        raise ValueError("Sampling weights changed")
    history = sample.loc[positive, ["street_segment_id", "prediction_time_utc"]].merge(
        bridge, on="street_segment_id", validate="many_to_one")
    builder = HistoricalReportFeatures(history, training_start=train_start, training_end=train_end)
    identities = sample[["street_segment_id", "prediction_time_utc"]].merge(
        bridge, on="street_segment_id", validate="many_to_one", how="left")
    transformed = builder.transform(identities)
    for name in builder.feature_names:
        sample[name] = transformed[name].to_numpy()
    # Independent direct event filtering on representative training rows.
    for i in np.linspace(0, len(sample)-1, 40, dtype=int):
        row = identities.iloc[i]
        upper = min(row.prediction_time_utc-pd.Timedelta(days=7), train_end)
        events = history.loc[history.prediction_time_utc.ge(upper-pd.Timedelta(days=365))
                             & history.prediction_time_utc.lt(upper)]
        assert transformed.iloc[i][builder.feature_names[0]] == int(events.street_segment_id.eq(row.street_segment_id).sum())
        assert transformed.iloc[i][builder.feature_names[1]] == int((events.grid_id.eq(row.grid_id) & events.cell_id.eq(row.cell_id)).sum())
    X = sample[experiment["features"]].astype("float64")
    if not np.isfinite(X.to_numpy()).all() or not np.isin(sample.label, [0, 1]).all():
        raise ValueError("Nonfinite inputs or invalid labels")
    sample.to_parquet(RUN / "training_sample.parquet", index=False)
    history.to_parquet(RUN / "history_events.parquet", index=False)
    bridge.to_parquet(RUN / "street_cell_bridge.parquet", index=False)
    print(f"PREFLIGHT passed: {len(sample):,} rows, {int(positive.sum()):,} positives, ten finite features", flush=True)
    captured = []
    model = HistGradientBoostingClassifier(**experiment["proposed_tree_parameters"])
    fit_began = time.perf_counter()
    with warnings.catch_warnings(record=True) as captured, threadpool_limits(limits=2):
        warnings.simplefilter("always")
        model.fit(X, sample.label, sample_weight=sample.sample_weight)
    fit_seconds = time.perf_counter()-fit_began
    joblib.dump(model, RUN / "history_tree.joblib")
    diagnostic = X.iloc[np.linspace(0, len(X)-1, min(10000, len(X)), dtype=int)]
    with threadpool_limits(limits=2):
        scores = model.predict_proba(diagnostic)[:, 1]
        reloaded = joblib.load(RUN / "history_tree.joblib").predict_proba(diagnostic)[:, 1]
    if not np.isfinite(scores).all() or not np.array_equal(scores, reloaded):
        raise ValueError("Nonfinite predictions or reload mismatch")
    assets = ["history_tree.joblib", "history_events.parquet", "street_cell_bridge.parquet", "experiment.json", "training_sample.parquet"]
    checkpoint = {"run_id": "climate-pre2024-v1", "experiment_id": experiment["experiment_id"],
        "status": "fitted_development_pending", "features": experiment["features"], "classes": [0, 1],
        "training_rows": len(sample), "training_positive_rows": int(positive.sum()),
        "training_negative_rows": int((~positive).sum()), "fit_seconds": fit_seconds,
        "iterations": int(model.n_iter_), "fit_warnings": [str(w.message) for w in captured],
        "assets": {name: {"sha256": sha256(RUN / name)} for name in assets},
        "history_helper": {"project_path": "scripts/climate_history_features.py", "sha256": sha256(ROOT / "scripts/climate_history_features.py")},
        "preparation_evidence_sha256": {"dataset_manifest": sha256(DATASET / "manifest.json"),
            "preparation_config": sha256(ROOT / "config/climate_preparation.json"),
            "pretraining_report": sha256(RUN / "pretraining_report.json")},
        "pretraining_report_project_path": str((RUN / "pretraining_report.json").relative_to(ROOT)),
        "python_version": sys.version.split()[0], "package_versions": versions,
        "sampling_weights": "raw_inverse_inclusion_1_and_5000_no_normalization",
        "diagnostic": {"rows": len(diagnostic), "minimum_score": float(scores.min()), "maximum_score": float(scores.max()),
                       "exact_zero_scores": int((scores==0).sum()), "exact_one_scores": int((scores==1).sum()), "reload_exact": True},
        "preflight_and_fit_seconds": time.perf_counter()-began,
        "no_2024_outcomes_used": True, "fresh_independent_test_performed": False}
    write_new(RUN / "checkpoint_manifest.json", checkpoint)
    print(json_text({key: checkpoint[key] for key in ["run_id", "training_rows", "training_positive_rows", "training_negative_rows", "fit_seconds", "diagnostic"]}), flush=True)
    print("FIT COMPLETE; development evaluation remains pending", flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    run()
