"""Build the versioned mobility modelling table from a read-only DB snapshot."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import tempfile

import holidays
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[3]
TZ = "America/New_York"
EXPECTED = {"segments": 4488, "hourly": 1428980, "dst": 658, "baseline": 1412050}
ROAD_FEATURES = [
    "borough", "borough_source", "boro_disagreement", "lion_key_used", "posted_speed",
    "number_travel_lanes", "number_park_lanes", "number_total_lanes", "streetwidth_min",
    "streetwidth_max", "shape_length", "trafdir", "truck_route_type", "snow_priority",
    "bikelane", "lon", "lat",
]
# Full-snapshot coverage must never become training predictors or split-specific weights.
COVERAGE_COLUMNS = ["n_rows_sd", "n_days_sd", "n_years_sd", "n_seasons_sd", "w_road"]
AUDIT_COLUMNS = [
    "segment_id", "sd", "hour_start_utc", "hour_start", "date", "study_id", "source",
    "orig_interval_min", "partial_day", "covid", "sparse_year", "baseline_ok",
    "identity_conflict", "borough_source", "boro_disagreement", "lion_key_used",
    "road_type", "holiday_name",
]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def add_calendar(frame):
    frame["hour_start_utc"] = pd.to_datetime(frame.hour_start_utc, utc=True)
    frame["hour_start"] = frame.hour_start_utc.dt.tz_convert(TZ).dt.tz_localize(None)
    dt = frame.hour_start.dt
    frame["date"] = dt.date
    frame["hour"] = dt.hour.astype("int8")
    frame["dow"] = dt.dayofweek.astype("int8")
    frame["month"] = dt.month.astype("int8")
    frame["year"] = dt.year.astype("int16")
    seasons = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
               6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
    frame["season"] = frame.month.map(seasons).astype("category")
    frame["is_weekend"] = frame.dow.ge(5)
    # Adjacent dates can cross a year boundary, so populate the neighbouring years too.
    years = list(range(int(frame.year.min()) - 1, int(frame.year.max()) + 2))
    calendar = holidays.country_holidays(
        "US", subdiv="NY", years=years, observed=True, expand=False, language="en_US",
    )
    unique_dates = sorted(frame.date.unique())
    names = {day: calendar.get(day) for day in unique_dates}
    adjacent = {day: day - timedelta(days=1) in calendar or day + timedelta(days=1) in calendar
                for day in unique_dates}
    frame["holiday_name"] = frame.date.map(names).astype("string")
    frame["is_holiday"] = frame.holiday_name.notna()
    frame["holiday_adjacent"] = frame.date.map(adjacent).astype(bool)
    return {"country": "US", "subdivision": "NY", "observed": True,
            "categories": list(calendar.categories), "language": "en_US",
            "populated_years": years,
            "adjacent_definition": "Either the previous or next local date is a holiday; "
                                   "may also be true on a holiday.",
            "scope": "Package US/NY public-holiday calendar; not school holidays or "
                     "a custom NYC closure/event calendar."}


def build(output):
    output = output.resolve()
    require(output.suffix == ".parquet", "Output must have a .parquet extension.")
    snapshot_path = output.with_name(output.stem + "_snapshot.json")
    report_path = output.with_name(output.stem + "_report.md")
    for path in [output, snapshot_path, report_path]:
        require(not path.exists(), f"Refusing to overwrite {path}. Choose a new --output version.")
    inputs = [PROJECT / "data/mobility/clean/combined_hourly_v3.parquet",
              PROJECT / "data/mobility/clean/segment_table_v4.parquet"]
    archive = []
    for path in inputs:
        require(path.is_file(), f"Missing archive: {path}")
        archive.append({"path": path.relative_to(PROJECT).as_posix(), "bytes": path.stat().st_size,
                        "rows": pq.ParquetFile(path).metadata.num_rows, "sha256": sha256(path)})
    require([item["rows"] for item in archive] == [1429638, 4488], "Unexpected archive row counts.")
    url = make_url(os.environ["PLEXA_DB_URL"])
    print(f"Reading {url.host}:{url.port or 5432}/{url.database} in a read-only transaction.", flush=True)
    engine = create_engine(url, isolation_level="REPEATABLE READ", connect_args={
        "connect_timeout": 8, "options": "-c default_transaction_read_only=on -c timezone=UTC"})
    try:
        with engine.connect() as conn, conn.begin():
            require(conn.execute(text("SHOW transaction_read_only")).scalar() == "on", "Not read-only.")
            db_time = conn.execute(text("SELECT transaction_timestamp()")).scalar().isoformat()
            counts = dict(conn.execute(text("""
                SELECT (SELECT count(*) FROM segments) AS segments,
                       (SELECT count(*) FROM hourly) AS hourly,
                       (SELECT count(*) FROM hourly_dst_flagged) AS dst,
                       (SELECT count(*) FROM hourly WHERE baseline_ok) AS baseline
            """)).mappings().one())
            require(counts == EXPECTED, f"Database snapshot changed: {counts}; expected {EXPECTED}.")
            frame = pd.read_sql(text("""
                SELECT segment_id, direction, road_type, hour_start_utc, volume, source,
                       study_id, orig_interval_min, partial_day, covid, sparse_year,
                       baseline_ok, identity_conflict
                FROM hourly WHERE baseline_ok
                ORDER BY segment_id, direction, hour_start_utc
            """), conn)
            roads = pd.read_sql(text("SELECT segment_id, " + ", ".join(ROAD_FEATURES)
                                     + " FROM segments"), conn)
    finally:
        engine.dispose()
    require(len(frame) == counts["baseline"], "Extracted row count does not match the database.")
    require(frame.segment_id.isin(roads.segment_id).all(), "Some observations have no segment row.")
    require(frame.road_type.eq("unspecified").all(), "An excluded road type entered the extract.")
    require(frame.baseline_ok.all() and not frame.identity_conflict.any(), "Ineligible observations.")
    for column in ["direction", "road_type", "source"]:
        frame[column] = frame[column].astype("category")
    for column in ["borough", "borough_source", "lion_key_used", "trafdir", "snow_priority"]:
        roads[column] = roads[column].astype("category")
    frame = frame.merge(roads, on="segment_id", how="left", validate="many_to_one", sort=False)
    require(len(frame) == counts["baseline"], "Joining road attributes changed the row count.")
    frame["sd"] = frame.segment_id.astype(str) + "|" + frame.direction.astype(str)
    frame["sd"] = frame.sd.astype("category")
    calendar_config = add_calendar(frame)
    require(np.isfinite(frame.volume).all() and frame.volume.ge(0).all(), "Invalid target volumes.")
    frame["log_volume"] = np.log1p(frame.volume)
    coverage = frame.groupby("sd", observed=True).agg(
        n_rows_sd=("volume", "size"), n_days_sd=("date", "nunique"),
        n_years_sd=("year", "nunique"), n_seasons_sd=("season", "nunique"))
    for column in coverage.columns:
        frame[column] = frame.sd.map(coverage[column]).astype("int32")
    frame["w_road"] = 1.0 / frame.n_rows_sd
    require(not frame.duplicated(["segment_id", "direction", "hour_start"]).any(), "Duplicate local keys.")
    require(frame.direction.isin(["NB", "SB", "EB", "WB"]).all(), "Unexpected direction.")
    require(frame.hour_start_utc.eq(frame.hour_start_utc.dt.floor("h")).all(), "Non-hourly timestamp.")
    localized = frame.hour_start.dt.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
    require(localized.notna().all(), "A DST-ambiguous/nonexistent hour entered the table.")
    require(localized.dt.tz_convert("UTC").equals(frame.hour_start_utc), "UTC/local round-trip failed.")
    require(np.isclose(frame.w_road.sum(), len(coverage), rtol=0, atol=1e-8), "Road weights do not reconcile.")
    per_road_weight = frame.groupby("sd", observed=True).w_road.sum()
    require(np.allclose(per_road_weight, 1.0, rtol=0, atol=1e-12), "A road's weights do not sum to one.")
    require(np.allclose(np.expm1(frame.log_volume), frame.volume), "Log target does not round-trip.")
    require(frame.is_holiday.any() and (~frame.is_holiday).any(), "Holiday feature is constant.")
    missing = {col: {"rows": int(frame[col].isna().sum()),
                     "percent": round(float(frame[col].isna().mean() * 100), 4)} for col in ROAD_FEATURES}
    holiday_share = float(frame.is_holiday.mean())
    print(f"Extracted {len(frame):,} rows; {frame.segment_id.nunique():,} segments; "
          f"{len(coverage):,} road/direction units.", flush=True)
    print(f"Holiday observations: {int(frame.is_holiday.sum()):,} ({holiday_share:.2%}); "
          f"road weights sum to {frame.w_road.sum():,.0f}.", flush=True)
    versions = {name: importlib.metadata.version(name) for name in
                ["pandas", "numpy", "pyarrow", "SQLAlchemy", "psycopg2-binary", "holidays"]}
    try:
        versions["mlflow"] = importlib.metadata.version("mlflow")
    except importlib.metadata.PackageNotFoundError:
        versions["mlflow"] = None
    snapshot = {
        "dataset": output.stem, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_transaction_at_utc": db_time,
        "database": {"host": url.host, "port": url.port or 5432, "database": url.database,
                     "isolation": "REPEATABLE READ", "read_only": True, "counts": counts},
        "archive_references": archive, "local_timezone": TZ, "calendar": calendar_config,
        "rows": len(frame), "columns": len(frame.columns),
        "segments": int(frame.segment_id.nunique()), "road_direction_units": len(coverage),
        "local_time_range": [frame.hour_start.min().isoformat(), frame.hour_start.max().isoformat()],
        "days_observed_per_road": {"min": int(coverage.n_days_sd.min()),
                                   "median": float(coverage.n_days_sd.median()),
                                   "max": int(coverage.n_days_sd.max())},
        "rows_by_source": {str(k): int(v) for k, v in frame.source.value_counts().items()},
        "rows_by_borough": {str(k): int(v) for k, v in frame.borough.value_counts(dropna=False).items()},
        "rows_by_season": {str(k): int(v) for k, v in frame.season.value_counts().items()},
        "holiday_rows": int(frame.is_holiday.sum()), "holiday_row_fraction": holiday_share,
        "holiday_adjacent_rows": int(frame.holiday_adjacent.sum()),
        "road_weight_sum": float(frame.w_road.sum()), "missing_road_features": missing,
        "column_dtypes": {col: str(dtype) for col, dtype in frame.dtypes.items()},
        "targets": ["volume", "log_volume"], "target_unit": "vehicles/hour",
        "audit_columns": AUDIT_COLUMNS, "full_snapshot_bookkeeping_columns": COVERAGE_COLUMNS,
        "leakage_rules": [
            "No train/test splits or target-derived road-history predictors are created here.",
            "Coverage columns are full-snapshot diagnostics, not predictors.",
            "Recompute inverse-road weights separately within each training/evaluation partition.",
            "Compute all learned road-history features using training/OOF data only.",
            "Static LION attributes describe the available street-network release, not historical vintages.",
        ],
        "python_version": platform.python_version(), "package_versions": versions,
        "builder_sha256": sha256(Path(__file__)),
        "checks": {"expected_rows": True, "eligible_only": True, "unique_local_keys": True,
                   "utc_local_round_trip": True, "finite_nonnegative_targets": True,
                   "equal_total_weight_per_road": True, "missing_road_features_preserved": True},
    }
    # Stage all artifacts first. Renaming on Windows refuses to replace an existing file.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".model-table-", dir=output.parent) as temp:
        staged = Path(temp)
        staged_table = staged / output.name
        frame.to_parquet(staged_table, index=False, compression="zstd")
        require(pq.ParquetFile(staged_table).metadata.num_rows == len(frame), "Saved row count mismatch.")
        snapshot["output"] = {"path": output.relative_to(PROJECT).as_posix()
                              if output.is_relative_to(PROJECT) else str(output),
                              "bytes": staged_table.stat().st_size, "sha256": sha256(staged_table)}
        (staged / snapshot_path.name).write_text(json.dumps(snapshot, indent=2, allow_nan=False) + "\n",
                                                encoding="utf-8")
        report = make_report(snapshot, frame)
        (staged / report_path.name).write_text(report, encoding="utf-8")
        for destination in [output, report_path, snapshot_path]:
            require(not destination.exists(), f"Refusing to overwrite {destination}.")
            (staged / destination.name).rename(destination)
    for path in [output, snapshot_path, report_path]:
        print(f"Saved: {path}", flush=True)
    print("All modelling-table checks passed. Database unchanged.", flush=True)


def make_report(snapshot, frame):
    lines = ["# Plexa mobility modelling table v1", "", "Status: validation passed.", "",
             f"- Observations: {snapshot['rows']:,}", f"- Columns: {snapshot['columns']}",
             f"- Segments: {snapshot['segments']:,}",
             f"- Road/direction units: {snapshot['road_direction_units']:,}",
             f"- Local time range: {' to '.join(snapshot['local_time_range'])}",
             f"- Median observed dates per road/direction: {snapshot['days_observed_per_road']['median']:g}",
             f"- Holiday observations: {snapshot['holiday_rows']:,} ({snapshot['holiday_row_fraction']:.2%})",
             f"- Adjacent-to-holiday observations: {snapshot['holiday_adjacent_rows']:,}",
             f"- Sum of road weights: {snapshot['road_weight_sum']:,.0f}", "",
             "Calendar: Python holidays, US/NY public holidays, including observed dates. "
             "All calendar fields use America/New_York local time. "
             "Holiday-adjacent means the previous or next date is a holiday; "
             "it can also be true on a holiday. This is not a school-holiday or NYC event calendar.", "",
             "## Road features with missing values", "", "Missing values were preserved.", "",
             "| Feature | Missing rows | Share |", "|---|---:|---:|"]
    for column, missing in snapshot["missing_road_features"].items():
        if missing["rows"]:
            lines.append(f"| {column} | {missing['rows']:,} | {missing['percent']:.2f}% |")
    lines += ["", "## Holiday observations", "", "| Holiday label | Rows |", "|---|---:|"]
    for label, count in frame.loc[frame.is_holiday, "holiday_name"].value_counts().items():
        lines.append(f"| {label} | {count:,} |")
    lines += ["", "## Use in training", ""] + ["- " + item for item in snapshot["leakage_rules"]]
    lines += ["", "The table is a modelling input, not a trained model. No evaluation splits "
              "have been assigned. No model has been fitted. Source data and database were not modified.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "data/mobility/clean/model_table_v1.parquet")
    args = parser.parse_args()
    build(args.output)
