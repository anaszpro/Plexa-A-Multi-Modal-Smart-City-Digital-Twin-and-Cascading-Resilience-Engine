"""Map configured flood reports to street edges without guessing nearest ties.

Creates a new output directory containing matched reports, all nearest-edge
candidates, one audit row per report, and a JSON manifest.

These are candidate positive reports, not a finished training dataset.
Existing outputs are never replaced. Unresolved-time reports remain in
PostgreSQL's review table.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import psycopg2


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for block in iter(
            lambda: source.read(1024 * 1024), b""
        ):
            digest.update(block)

    return digest.hexdigest()


def load_reports(rules, start, end):
    pairs = [
        (rule["complaint_type"], rule["descriptor"])
        for rule in rules["positive_report_rules"]
    ]

    if not pairs or len(pairs) != len(set(pairs)):
        raise ValueError(
            "Positive rules must be nonempty and unique"
        )

    slots = ", ".join(["(%s, %s)"] * len(pairs))
    values = [value for pair in pairs for value in pair]

    with closing(
        psycopg2.connect(
            dbname=os.environ.get(
                "PLEXA_DB", "plexa_climate"
            )
        )
    ) as conn:
        conn.set_session(
            readonly=True,
            isolation_level="REPEATABLE READ",
        )

        with conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    unique_key, created_at, complaint_type,
                    descriptor, latitude, longitude
                FROM raw.complaints_311
                WHERE created_at >= (
                    %s::timestamp AT TIME ZONE 'America/New_York'
                )
                  AND created_at < (
                    %s::timestamp AT TIME ZONE 'America/New_York'
                )
                  AND (complaint_type, descriptor) IN ({slots})
                ORDER BY unique_key
                """,
                [str(start), str(end), *values],
            )

            reports = pd.DataFrame(
                cursor.fetchall(),
                columns=[column[0] for column in cursor.description],
            )

            cursor.execute(
                f"""
                SELECT count(*)
                FROM raw.complaints_311_time_review
                WHERE created_date_local::timestamp >= %s::timestamp
                  AND created_date_local::timestamp < %s::timestamp
                  AND (
                      source_record->>'complaint_type',
                      source_record->>'descriptor'
                  ) IN ({slots})
                """,
                [str(start), str(end), *values],
            )

            review_count = cursor.fetchone()[0]

    reports["created_at"] = pd.to_datetime(
        reports.created_at, utc=True
    )

    return reports, review_count


def map_reports(reports, streets, max_distance_ft):
    if max_distance_ft <= 0:
        raise ValueError("Maximum distance must be positive")

    if (
        reports.unique_key.isna().any()
        or reports.unique_key.duplicated().any()
    ):
        raise ValueError(
            "Complaint IDs must be nonnull and unique"
        )

    if streets.crs is None:
        raise ValueError("Street CRS is missing")

    keys = ["u", "v", "key"]

    if (
        streets[keys].isna().any().any()
        or streets.duplicated(keys).any()
    ):
        raise ValueError(
            "Street edge keys must be nonnull and unique"
        )

    if (
        streets.geometry.isna().any()
        or streets.geometry.is_empty.any()
        or not streets.is_valid.all()
    ):
        raise ValueError(
            "Street geometry contains missing, empty or invalid values"
        )

    audit = reports.copy().reset_index(drop=True)

    lat = pd.to_numeric(audit.latitude, errors="coerce")
    lon = pd.to_numeric(audit.longitude, errors="coerce")

    usable = (
        lat.between(-90, 90)
        & lon.between(-180, 180)
    )

    audit["match_status"] = "missing_or_invalid_coordinates"
    audit.loc[usable, "match_status"] = "outside_match_distance"

    points = gpd.GeoDataFrame(
        audit.loc[usable, reports.columns].copy(),
        geometry=gpd.points_from_xy(
            lon[usable], lat[usable]
        ),
        crs="EPSG:4326",
    ).to_crs("EPSG:2263")

    edges = streets[
        ["u", "v", "key", "osmid", "geometry"]
    ].to_crs("EPSG:2263")

    nearest = gpd.sjoin_nearest(
        points,
        edges,
        how="inner",
        max_distance=max_distance_ft,
        distance_col="dist_to_street_ft",
    )

    candidates = pd.DataFrame(
        nearest.drop(columns=["geometry", "index_right"])
    )

    for column in keys:
        candidates[column] = candidates[column].astype("int64")

    if candidates.duplicated(
        ["unique_key", *keys]
    ).any():
        raise ValueError(
            "Duplicate complaint/edge candidate pairs"
        )

    counts = candidates.groupby("unique_key").size()

    distances = (
        candidates.groupby("unique_key")
        .dist_to_street_ft.min()
    )

    audit["nearest_edge_count"] = (
        audit.unique_key.map(counts)
        .fillna(0)
        .astype(int)
    )

    audit["nearest_distance_ft"] = (
        audit.unique_key.map(distances)
    )

    audit.loc[
        audit.nearest_edge_count.eq(1),
        "match_status",
    ] = "matched_unique"

    audit.loc[
        audit.nearest_edge_count.gt(1),
        "match_status",
    ] = "ambiguous_nearest"

    status = audit.set_index("unique_key").match_status

    candidates["match_status"] = (
        candidates.unique_key.map(status)
    )

    candidates = candidates.sort_values(
        ["unique_key", *keys]
    ).reset_index(drop=True)

    matched = candidates.loc[
        candidates.match_status.eq("matched_unique")
    ].copy()

    if matched.unique_key.duplicated().any():
        raise ValueError(
            "Unique-match output contains duplicate complaint IDs"
        )

    return audit, candidates, matched


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=date(2020, 10, 14),
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=date(2026, 10, 7),
    )
    parser.add_argument(
        "--max-distance-ft",
        type=float,
        default=100,
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=ROOT / "config/climate_label_rules.json",
    )
    parser.add_argument(
        "--streets",
        type=Path,
        default=ROOT / "data/raw/nyc_streets.parquet",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/processed/street_311_mapping_v2",
    )

    args = parser.parse_args()

    if (
        args.start >= args.end
        or not 0 < args.max_distance_ft < float("inf")
    ):
        parser.error(
            "Use an increasing date range and a finite positive distance"
        )

    if args.output_dir.exists():
        parser.error(
            "Output directory already exists. "
            "Choose a new --output-dir."
        )

    rules = json.loads(args.rules.read_text())

    reports, review_count = load_reports(
        rules, args.start, args.end
    )

    if reports.empty:
        raise ValueError(
            "No candidate reports found; investigate before mapping"
        )

    streets = gpd.read_parquet(args.streets)

    audit, candidates, matched = map_reports(
        reports, streets, args.max_distance_ft
    )

    counts = {
        str(key): int(value)
        for key, value in audit.match_status.value_counts().items()
    }

    if sum(counts.values()) != len(reports):
        raise ValueError("Report accounting failed")

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "start_local_inclusive": str(args.start),
        "end_local_exclusive": str(args.end),
        "date_timezone": "America/New_York",
        "rule_version": rules["version"],
        "target": rules["target"],
        "rules_sha256": sha256(args.rules),
        "street_network_sha256": sha256(args.streets),
        "script_sha256": sha256(Path(__file__)),
        "max_distance_us_survey_feet": args.max_distance_ft,
        "candidate_reports_with_resolved_time": len(reports),
        "unresolved_time_candidates_excluded": review_count,
        "status_counts": counts,
        "nearest_edge_candidate_rows": len(candidates),
        "uniquely_matched_reports": len(matched),
        "tie_policy": (
            "Preserve all nearest candidates; "
            "do not choose an arbitrary edge."
        ),
        "note": (
            "No negative labels or forecast targets are created. "
            "Unresolved and unlocated reports remain relevant "
            "to label uncertainty."
        ),
    }

    args.output_dir.parent.mkdir(
        parents=True, exist_ok=True
    )

    staging = Path(
        tempfile.mkdtemp(
            prefix=".street-mapping-",
            dir=args.output_dir.parent,
        )
    )

    try:
        audit.to_parquet(
            staging / "report_audit.parquet",
            index=False,
        )

        candidates.to_parquet(
            staging / "nearest_edge_candidates.parquet",
            index=False,
        )

        matched.to_parquet(
            staging / "matched_reports.parquet",
            index=False,
        )

        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )

        if args.output_dir.exists():
            raise FileExistsError(
                "Output directory appeared during the run"
            )

        staging.rename(args.output_dir)

    finally:
        if staging.exists():
            shutil.rmtree(staging)

    print(json.dumps(manifest, indent=2))
    print(f"Saved mapping outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
