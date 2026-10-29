"""Create four fixed evaluation assignments without reading traffic targets."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[3]
ROLES = ["train", "test", "excluded"]
SCENARIOS = ["within_road", "unseen_roads", "time", "season_transfer"]
KEYS = ["segment_id", "direction", "road_type", "hour_start_utc"]
MIN_TRAIN_DAYS = 3
MIN_TIME_TEST_DAYS = 3
MIN_SEASON_FULL_DAYS = 3


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def relative(path):
    return path.relative_to(PROJECT).as_posix() if path.is_relative_to(PROJECT) else str(path)


def road_day_counts(data, roles, role):
    return data.loc[roles.eq(role)].groupby("sd", observed=True).date.nunique()


def within_road(data, fraction, seed):
    # Opposite directions share the segment/date assignment. This prevents one
    # direction from supplying that date's contemporaneous traffic to the other.
    days = data[["segment_id", "date"]].drop_duplicates().sort_values(["segment_id", "date"])
    days["score"] = np.random.default_rng(seed).random(len(days))
    days = days.sort_values(["segment_id", "score", "date"])
    n = days.groupby("segment_id").segment_id.transform("size")
    rank = days.groupby("segment_id").cumcount()
    n_test = np.minimum(np.ceil(n * fraction).astype(int), n - MIN_TRAIN_DAYS).clip(lower=0)
    days["role"] = np.where(n > MIN_TRAIN_DAYS,
                            np.where(rank < n_test, "test", "train"), "excluded")
    lookup = days.set_index(["segment_id", "date"]).role
    labels = pd.Series(lookup.reindex(pd.MultiIndex.from_frame(data[["segment_id", "date"]])).to_numpy(),
                       index=data.index)
    train_days = road_day_counts(data, labels, "train")
    test_days = road_day_counts(data, labels, "test")
    eligible = train_days[train_days.ge(MIN_TRAIN_DAYS)].index.intersection(test_days[test_days.ge(1)].index)
    labels.loc[~data.sd.isin(eligible)] = "excluded"
    return labels


def unseen_roads(data, fraction, seed):
    roads = data[["segment_id", "borough"]].drop_duplicates("segment_id").sort_values("segment_id")
    roads["stratum"] = roads.borough.astype("string").fillna("__MISSING_BOROUGH__")
    rng = np.random.default_rng(seed)
    held_out = []
    for _, stratum in roads.groupby("stratum", sort=True):
        if len(stratum) < 2:
            continue
        n_test = max(1, min(len(stratum) - 1, int(np.floor(len(stratum) * fraction + 0.5))))
        held_out.extend(rng.choice(stratum.segment_id.to_numpy(), size=n_test, replace=False).tolist())
    return pd.Series(np.where(data.segment_id.isin(held_out), "test", "train"), index=data.index)


def time_split(data, cutoff):
    days = data[["sd", "date", "year"]].drop_duplicates(["sd", "date"])
    early = days.loc[days.year.le(cutoff)].groupby("sd", observed=True).size()
    late = days.loc[days.year.gt(cutoff)].groupby("sd", observed=True).size()
    eligible = early[early.ge(MIN_TRAIN_DAYS)].index.intersection(late[late.ge(MIN_TIME_TEST_DAYS)].index)
    labels = pd.Series("excluded", index=data.index)
    matched = data.sd.isin(eligible)
    labels.loc[matched & data.year.le(cutoff)] = "train"
    labels.loc[matched & data.year.gt(cutoff)] = "test"
    return labels


def season_split(data):
    daily = data.groupby(["sd", "date"], observed=True).agg(
        n_hours=("hour", "nunique"), year=("year", "first"), season=("season", "first"))
    full = daily.loc[daily.n_hours.eq(24)].reset_index()
    coverage = full.groupby(["sd", "year", "season"], observed=True).size().unstack(fill_value=0)
    coverage = coverage.reindex(columns=["SON", "JJA"], fill_value=0)
    pairs = coverage.loc[coverage.SON.ge(MIN_SEASON_FULL_DAYS) & coverage.JJA.ge(MIN_SEASON_FULL_DAYS)]
    full_day_mask = pd.MultiIndex.from_frame(data[["sd", "date"]]).isin(
        pd.MultiIndex.from_frame(full[["sd", "date"]]))
    paired_mask = pd.MultiIndex.from_frame(data[["sd", "year"]]).isin(pairs.index)
    labels = pd.Series("excluded", index=data.index)
    labels.loc[full_day_mask & paired_mask & data.season.eq("SON")] = "train"
    labels.loc[full_day_mask & paired_mask & data.season.eq("JJA")] = "test"
    records = [{"sd": str(sd), "year": int(year), "autumn_full_days": int(row.SON),
                "summer_full_days": int(row.JJA)} for (sd, year), row in pairs.sort_index().iterrows()]
    return labels, records


def weights(data, labels):
    sizes = data.groupby([data.sd, labels], observed=True).segment_id.transform("size")
    return pd.Series(np.where(labels.ne("excluded"), 1.0 / sizes, 0.0), index=data.index)


def validate(data, labels, weight, name, cutoff):
    require(labels.isin(ROLES).all() and labels.notna().all(), f"{name}: invalid labels.")
    # A road/direction/date is never split between partitions, even on a partial day.
    day_roles = pd.DataFrame({"sd": data.sd, "date": data.date, "role": labels})
    require(day_roles.groupby(["sd", "date"], observed=True).role.nunique().eq(1).all(),
            f"{name}: a road/day spans multiple partitions.")
    train = data.loc[labels.eq("train")]
    test = data.loc[labels.eq("test")]
    require(len(train) > 0 and len(test) > 0, f"{name}: empty training or test set.")
    train_roads, test_roads = set(train.sd), set(test.sd)
    if name == "unseen_roads":
        require(set(train.segment_id).isdisjoint(set(test.segment_id)), "Segment leakage in unseen roads.")
    else:
        require(train_roads == test_roads, f"{name}: training/test road cohorts differ.")
    if name == "within_road":
        active = pd.DataFrame({"segment_id": data.segment_id, "date": data.date, "role": labels})
        active = active.loc[active.role.ne("excluded")]
        require(active.groupby(["segment_id", "date"]).role.nunique().eq(1).all(),
                "Opposite directions share a date across train and test.")
        require(train.groupby("sd", observed=True).date.nunique().ge(MIN_TRAIN_DAYS).all(),
                "Within-road training history is too short.")
    if name == "time":
        require(train.year.le(cutoff).all() and test.year.gt(cutoff).all(), "Time cutoff violated.")
        require(train.hour_start.max() < test.hour_start.min(), "Future observations in time training.")
        require(test.groupby("sd", observed=True).date.nunique().ge(MIN_TIME_TEST_DAYS).all(),
                "Time test history is too short.")
        require(train.groupby("sd", observed=True).date.nunique().ge(MIN_TRAIN_DAYS).all(),
                "Time training history is too short.")
    if name == "season_transfer":
        require(train.season.eq("SON").all() and test.season.eq("JJA").all(), "Wrong seasonal cohort.")
        require(set(zip(train.sd, train.year)) == set(zip(test.sd, test.year)), "Seasonal road-years differ.")
        for subset in [train, test]:
            require(subset.groupby(["sd", "date"], observed=True).hour.nunique().eq(24).all(),
                    "Partial day in season transfer.")
            require(subset.groupby(["sd", "year"], observed=True).date.nunique().ge(MIN_SEASON_FULL_DAYS).all(),
                    "Seasonal cohort has fewer than three full days.")
    require(np.isfinite(weight).all() and weight.ge(0).all(), f"{name}: invalid weights.")
    require(weight.loc[labels.eq("excluded")].eq(0).all(), f"{name}: excluded rows have weight.")
    totals = weight.loc[labels.ne("excluded")].groupby(
        [data.sd.loc[labels.ne("excluded")], labels.loc[labels.ne("excluded")]], observed=True).sum()
    require(np.allclose(totals, 1, rtol=0, atol=1e-12), f"{name}: weights do not balance roads per partition.")


def summarize(data, labels, weight):
    summary = {}
    for role in ROLES:
        part = data.loc[labels.eq(role)]
        summary[role] = {
            "rows": len(part), "segments": int(part.segment_id.nunique()),
            "road_direction_units": int(part.sd.nunique()),
            "road_direction_days": len(part[["sd", "date"]].drop_duplicates()),
            "local_date_range": [str(part.date.min()), str(part.date.max())] if len(part) else None,
            "weight_sum": float(weight.loc[labels.eq(role)].sum()),
            "rows_by_borough": {str(k): int(v) for k, v in part.borough.astype("string").fillna("unknown").value_counts().items()},
            "rows_by_source": {str(k): int(v) for k, v in part.source.value_counts().items()},
            "rows_by_season": {str(k): int(v) for k, v in part.season.value_counts().items()},
        }
    return summary


def build(args):
    source, output = args.input.resolve(), args.output.resolve()
    require(0 < args.test_fraction < 1, "Test fraction must lie between zero and one.")
    require(output.suffix == ".parquet", "Output must be a parquet file.")
    snapshot_path = output.with_name(output.stem + "_snapshot.json")
    report_path = output.with_name(output.stem + "_report.md")
    for path in [output, snapshot_path, report_path]:
        require(not path.exists(), f"Refusing to overwrite {path}. Choose a new output version.")
    source_snapshot = source.with_name(source.stem + "_snapshot.json")
    parent = json.loads(source_snapshot.read_text(encoding="utf-8"))
    digest = sha256(source)
    require(digest == parent["output"]["sha256"], "Modelling dataset differs from its recorded snapshot.")
    # This projection deliberately excludes volume, log_volume and coverage columns.
    read_columns = KEYS + ["sd", "date", "hour_start", "hour", "year", "season", "borough", "source"]
    data = pd.read_parquet(source, columns=read_columns)
    require(len(data) == parent["rows"], "Source row count does not match its snapshot.")
    require(not data.duplicated(KEYS).any(), "Source keys are not unique.")
    require(data["date"].eq(data.hour_start.dt.date).all(), "Local dates are inconsistent.")
    output_frame = data[KEYS].copy()
    output_frame.insert(0, "model_row", np.arange(len(data), dtype=np.int64))
    labels = {
        "within_road": within_road(data, args.test_fraction, args.seed),
        "unseen_roads": unseen_roads(data, args.test_fraction, args.seed),
        "time": time_split(data, args.time_cutoff),
    }
    labels["season_transfer"], pairs = season_split(data)
    summaries = {}
    for name in SCENARIOS:
        label = labels[name]
        weight = weights(data, label)
        validate(data, label, weight, name, args.time_cutoff)
        output_frame["split_" + name] = pd.Categorical(label, categories=ROLES)
        output_frame["w_" + name] = weight
        summaries[name] = summarize(data, label, weight)
        counts = summaries[name]
        require(sum(counts[role]["rows"] for role in ROLES) == len(data), f"{name}: row reconciliation failed.")
        print(f"{name}: train={counts['train']['rows']:,}; test={counts['test']['rows']:,}; "
              f"excluded={counts['excluded']['rows']:,}", flush=True)
    manifest = {
        "dataset": output.stem, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"path": relative(source), "sha256": digest, "rows": len(data),
                  "snapshot_path": relative(source_snapshot), "snapshot_sha256": sha256(source_snapshot)},
        "seed": args.seed, "nominal_test_fraction": args.test_fraction,
        "time_last_training_year": args.time_cutoff,
        "time_cutoff_reason": "2018 retains broad repeat-road coverage before the historical "
                              "archive ends. Chosen using date coverage, without reading targets.",
        "definitions": {
            "within_road": "Random held-out local dates per segment, shared across directions; "
                           "at least 3 training dates and 1 test date per road/direction; otherwise excluded.",
            "unseen_roads": "Approximately 20% of whole segments withheld, stratified by borough "
                            "with missing borough as a separate stratum; every direction and date stays together.",
            "time": f"Matched road/direction units only; train through {args.time_cutoff}, "
                    f"test from {args.time_cutoff + 1}; at least 3 observed dates on each side.",
            "season_transfer": "Matched road/direction/year pairs with >=3 full 24-hour days "
                               "in each season. Only full autumn days train and full summer days test. "
                               "Restricted paired-cohort sensitivity test; not a chronological forecast.",
        },
        "minimum_training_observed_dates": MIN_TRAIN_DAYS,
        "minimum_time_test_observed_dates": MIN_TIME_TEST_DAYS,
        "minimum_full_days_per_season": MIN_SEASON_FULL_DAYS,
        "season_transfer_pairs": pairs, "summaries": summaries,
        "column_dtypes": {col: str(dtype) for col, dtype in output_frame.dtypes.items()},
        "builder_sha256": sha256(Path(__file__)),
        "package_versions": {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "pyarrow"]},
        "training_rules": [
            "Each scenario is an independent experiment. Never combine their assignments into one split.",
            "Join assignments to the parent modelling dataset on the four key columns, one-to-one.",
            "model_row is a convenience offset for this exact input file, not a model feature.",
            "Use split_<scenario> and w_<scenario>; excluded rows have zero weight and must be removed.",
            "Weights sum to one per road/direction separately within train and test.",
            "Never use n_rows_sd/n_days_sd/n_years_sd/n_seasons_sd/w_road from the full snapshot as predictors.",
            "All target encodings, imputation and learned transforms must fit on training data only.",
            "Road-history predictors for training rows must be out-of-fold, grouped by whole days; "
            "all directions of a segment/date must share a fold. Cold-start validation requires segment folds.",
            "Do not tune or calibrate intervals on test rows. Use grouped folds inside training data.",
            "Unseen-segment performance does not establish transfer to entirely unseen corridors or regions.",
            "Within-road and season-transfer results do not establish forecasting performance.",
        ],
        "checks": {"source_fingerprint": True, "whole_days": True,
                   "unseen_segments_disjoint": True, "time_chronological": True,
                   "season_pairs_and_full_days": True, "partition_road_weights": True,
                   "every_row_reconciled": True, "targets_not_read": True},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".model-splits-", dir=output.parent) as temp:
        staged = Path(temp)
        staged_output = staged / output.name
        output_frame.to_parquet(staged_output, index=False, compression="zstd")
        manifest["output"] = {"path": relative(output), "sha256": sha256(staged_output),
                              "bytes": staged_output.stat().st_size, "rows": len(output_frame)}
        (staged / snapshot_path.name).write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (staged / report_path.name).write_text(report(manifest), encoding="utf-8")
        for destination in [output, report_path, snapshot_path]:
            require(not destination.exists(), f"Refusing to overwrite {destination}.")
            (staged / destination.name).rename(destination)
    print(f"Saved {output.name}, {snapshot_path.name}, and {report_path.name}.", flush=True)
    print("All split checks passed. No models trained; source dataset and database unchanged.", flush=True)


def report(manifest):
    lines = ["# Plexa mobility evaluation splits", "", "Status: validation passed.", "",
             f"Parent observations: {manifest['input']['rows']:,}. Seed: {manifest['seed']}.", "",
             "The four columns define independent experiments, not four pieces of one partition.", "",
             "| Evaluation | Training rows | Test rows | Excluded rows | Test road/direction units |",
             "|---|---:|---:|---:|---:|"]
    for name, summary in manifest["summaries"].items():
        lines.append(f"| {name} | {summary['train']['rows']:,} | {summary['test']['rows']:,} | "
                     f"{summary['excluded']['rows']:,} | {summary['test']['road_direction_units']:,} |")
    for name, description in manifest["definitions"].items():
        summary = manifest["summaries"][name]
        lines += ["", f"## {name}", "", description, "",
                  f"Training dates: {summary['train']['local_date_range']}. "
                  f"Test dates: {summary['test']['local_date_range']}.", "",
                  f"Training segments: {summary['train']['segments']:,}; "
                  f"test segments: {summary['test']['segments']:,}.", "",
                  "| Test borough | Rows |", "|---|---:|"]
        for borough, n in summary["test"]["rows_by_borough"].items():
            lines.append(f"| {borough} | {n:,} |")
    lines += ["", "## Seasonal evidence", "",
              f"Only {len(manifest['season_transfer_pairs'])} road/year pairs qualify. "
              "Treat season-transfer scores as indicative; do not infer citywide seasonal accuracy.", "",
              "## Training and evaluation rules", ""]
    lines += ["- " + rule for rule in manifest["training_rules"]]
    lines += ["", "No model selection, interval calibration or tuning has been performed on test data.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=PROJECT / "data/mobility/clean/model_table_v1.parquet")
    parser.add_argument("--output", type=Path, default=PROJECT / "data/mobility/clean/model_splits_v1.parquet")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--time-cutoff", type=int, default=2018)
    build(parser.parse_args())
