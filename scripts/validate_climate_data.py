"""Read-only inventory of the climate baseline's existing Parquet inputs.

Run from the repository root:
    python scripts/validate_climate_data.py

This reports structural evidence; it does not certify training readiness.
"""
import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow.parquet as pq


def parquet_summary(path):
    metadata = pq.read_metadata(path)
    return {"rows": metadata.num_rows, "bytes": path.stat().st_size}


def matrix_summary(path):
    result = parquet_summary(path)
    metadata = pq.read_metadata(path)
    index = metadata.schema.names.index("timestamp")
    starts, ends = [], []
    for group in range(metadata.num_row_groups):
        stats = metadata.row_group(group).column(index).statistics
        if stats is not None and stats.has_min_max:
            starts.append(stats.min)
            ends.append(stats.max)
    result["timestamp_min"] = min(starts) if starts else None
    result["timestamp_max"] = max(ends) if ends else None
    result["note"] = "Timestamp bounds come from available Parquet statistics."
    return result


def radar_summary(directory):
    files = sorted(directory.glob("radar_*.parquet"))
    if not files:
        raise FileNotFoundError(f"No radar Parquet files in {directory}")
    schemas, years = Counter(), Counter()
    hours = []
    rows = empty = 0
    for path in files:
        hour = datetime.strptime(path.stem, "radar_%Y%m%d_%H%M%S")
        hours.append(hour)
        years[hour.year] += 1
        metadata = pq.read_metadata(path)
        schemas[str(metadata.schema.to_arrow_schema().field("ts_utc").type)] += 1
        rows += metadata.num_rows
        empty += metadata.num_rows == 0
    expected = int((max(hours) - min(hours)).total_seconds() / 3600) + 1
    return {
        "files": len(files),
        "rows": rows,
        "empty_files": empty,
        "files_by_year": dict(sorted(years.items())),
        "timestamp_schemas": dict(schemas),
        "first_filename_hour": min(hours),
        "last_filename_hour": max(hours),
        "missing_filename_hours": expected - len(set(hours)),
        "note": "Filename coverage does not establish valid radar observations.",
    }


def inspect(root):
    paths = {
        "streets": root / "data/raw/nyc_streets.parquet",
        "topography": root / "data/processed/street_topography.parquet",
        "bridge": root / "data/processed/street_to_radar_cell.parquet",
        "labels": root / "data/processed/street_311_labels.parquet",
        "matrix": root / "data/processed/ml_feature_matrix.parquet",
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"Required file not found: {path}")

    report = {"purpose": "Baseline inventory; not a training-readiness certificate"}
    with duckdb.connect(":memory:") as con:
        con.execute("SET TimeZone='UTC'")
        con.execute("SET threads=2")
        con.execute("SET memory_limit='1GB'")
        # In-memory views read existing files without modifying them.
        for name in ("streets", "topography", "bridge", "labels"):
            con.read_parquet(str(paths[name])).create_view(name)

        report["street_tables"] = {}
        for name in ("streets", "topography", "bridge"):
            row = con.execute(f"""
                SELECT count(*), count(DISTINCT (u,v,key)),
                       count(DISTINCT osmid),
                       count(*) FILTER (WHERE u IS NULL OR v IS NULL OR key IS NULL)
                FROM {name}
            """).fetchone()
            report["street_tables"][name] = dict(zip(
                ["rows", "unique_edge_keys", "unique_osmids", "null_edge_keys"], row
            ))

        row = con.execute("""
            SELECT count(*),
                   count(*) FILTER (WHERE (t.u,t.v,t.key) <> (b.u,b.v,b.key))
            FROM topography t JOIN bridge b USING (osmid)
        """).fetchone()
        report["current_osmid_join"] = dict(zip(
            ["joined_rows", "pairs_with_different_edge_keys"], row
        ))
        report["edge_key_join_rows"] = con.execute("""
            SELECT count(*) FROM topography JOIN bridge USING (u,v,key)
        """).fetchone()[0]

        row = con.execute("""
            SELECT count(*), count(DISTINCT unique_key),
                   min(created_at)::VARCHAR, max(created_at)::VARCHAR
            FROM labels
        """).fetchone()
        report["complaint_labels"] = dict(zip(
            ["mapping_rows", "unique_complaints", "first_utc", "last_utc"], row
        ))

    report["radar"] = radar_summary(root / "data/raw/radar_parquet")
    report["existing_matrix"] = matrix_summary(paths["matrix"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    report = inspect(args.repo_root.resolve())
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
