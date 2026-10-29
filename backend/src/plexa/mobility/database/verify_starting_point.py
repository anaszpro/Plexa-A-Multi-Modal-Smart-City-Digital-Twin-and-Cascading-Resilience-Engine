"""Read-only checks of the current Plexa database and final parquet archive.

Run from any directory: python database/verify_starting_point.py
Requires PLEXA_DB_URL to be set to the database connection URL.
No database changes or output files are written.
"""

from pathlib import Path
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT.parents[3] / "data" / "mobility" / "clean"
EXPECTED = {
    "segments": 4488,
    "hourly": 1428980,
    "dst": 658,
    "baseline": 1412050,
    "conflicts": 11844,
}


def main():
    failures = []

    def check(label, condition, detail=""):
        print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f": {detail}" if detail else ""))
        if not condition:
            failures.append(label)

    database_url = os.environ.get("PLEXA_DB_URL")
    if not database_url or not database_url.strip():
        raise SystemExit("Set PLEXA_DB_URL before running database verification.")
    url = make_url(database_url)
    print(f"Database: {url.host}:{url.port or 5432}/{url.database}")
    print("Read-only, consistent database snapshot; archive: combined v3 / segments v4")
    c = pd.read_parquet(CLEAN / "combined_hourly_v3.parquet")
    s = pd.read_parquet(CLEAN / "segment_table_v4.parquet")
    s.columns = s.columns.str.lower()
    keys = ["segment_id", "direction", "road_type"]
    check("Archive row counts", len(c) == 1429638 and len(s) == 4488,
          f"hourly={len(c):,}, segments={len(s):,}")
    check("Archive hourly keys unique", not c.duplicated(keys + ["hour_start"]).any())
    check("Archive segment keys unique", not s.segment_id.duplicated().any())
    expected_ok = c.road_type.eq("unspecified") & ~c.identity_conflict & ~c.dst_issue
    check("Archive eligibility rule", c.baseline_ok.equals(expected_ok))
    check("Archive baseline count", int(c.baseline_ok.sum()) == EXPECTED["baseline"],
          f"{int(c.baseline_ok.sum()):,}")
    check("Archive volumes finite and nonnegative", bool(
        c.volume.notna().all() & c.volume.ge(0).all() & c.volume.lt(float("inf")).all()))
    eligible = c.loc[c.baseline_ok].copy()
    eligible["local_date"] = eligible.hour_start.dt.date
    days = eligible.groupby(["segment_id", "direction"]).local_date.nunique()
    print(f"Baseline coverage: {len(days):,} segment-direction units; "
          f"median {days.median():g} observed dates")

    engine = create_engine(
        url,
        isolation_level="REPEATABLE READ",
        connect_args={"connect_timeout": 8,
                      "options": "-c default_transaction_read_only=on -c timezone=UTC"},
    )
    try:
        with engine.connect() as conn, conn.begin():
            check("Database transaction is read-only",
                  conn.execute(text("SHOW transaction_read_only")).scalar() == "on")
            counts = conn.execute(text("""
                SELECT (SELECT count(*) FROM segments) AS segments,
                       (SELECT count(*) FROM hourly) AS hourly,
                       (SELECT count(*) FROM hourly_dst_flagged) AS dst,
                       (SELECT count(*) FROM hourly WHERE baseline_ok) AS baseline,
                       (SELECT count(*) FROM hourly WHERE identity_conflict) AS conflicts
            """)).mappings().one()
            for name, expected in EXPECTED.items():
                check(f"Database {name} count", counts[name] == expected, f"{counts[name]:,}")
            check("PostGIS and TimescaleDB installed", conn.execute(text("""
                SELECT count(*) FROM pg_extension WHERE extname IN ('postgis','timescaledb')
            """)).scalar() == 2)
            check("Hourly table registered as hypertable", conn.execute(text("""
                SELECT count(*) FROM timescaledb_information.hypertables
                WHERE hypertable_schema='public' AND hypertable_name='hourly'
            """)).scalar() == 1)
            invalid = conn.execute(text("""
                SELECT count(*) FROM hourly h LEFT JOIN segments s USING (segment_id)
                WHERE s.segment_id IS NULL OR volume < 0
                   OR volume IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)
                   OR baseline_ok IS DISTINCT FROM
                      (road_type='unspecified' AND NOT identity_conflict)
            """)).scalar()
            check("Database references, volumes and eligibility", invalid == 0, f"invalid={invalid}")
            geometry = conn.execute(text("""
                SELECT count(*) FILTER (WHERE lion_wkt_2263 IS NOT NULL AND
                     (lion_geom IS NULL OR ST_SRID(lion_geom)<>2263 OR NOT ST_IsValid(lion_geom))) AS bad_line,
                       count(*) FILTER (WHERE lon IS NOT NULL AND lat IS NOT NULL AND
                     (geom_4326 IS NULL OR ST_SRID(geom_4326)<>4326)) AS bad_point
                FROM segments
            """)).mappings().one()
            check("Expected spatial geometry present and valid",
                  geometry["bad_line"] == 0 and geometry["bad_point"] == 0)

            # Compare each road/source group rather than just citywide totals.
            groups = keys + ["source"]
            for table, mask, time_column in [
                ("hourly", ~c.dst_issue, "hour_start_utc"),
                ("hourly_dst_flagged", c.dst_issue, "hour_start_local"),
            ]:
                archive = c.loc[mask].copy()
                archive["checked_time"] = (
                    archive.hour_start_tz.dt.tz_convert("UTC")
                    if table == "hourly" else archive.hour_start
                )
                aggregations = dict(
                    n=("volume", "size"), volume_sum=("volume", "sum"),
                    conflicts=("identity_conflict", "sum"),
                    first_time=("checked_time", "min"), last_time=("checked_time", "max"),
                )
                baseline_sql = ", count(*) FILTER (WHERE baseline_ok) AS baseline" if table == "hourly" else ""
                if table == "hourly":
                    aggregations["baseline"] = ("baseline_ok", "sum")
                ref = archive.groupby(groups, dropna=False).agg(**aggregations).sort_index()
                db = pd.read_sql(text(f"""
                    SELECT segment_id, direction, road_type, source,
                           count(*) AS n, sum(volume) AS volume_sum,
                           count(*) FILTER (WHERE identity_conflict) AS conflicts,
                           min({time_column}) AS first_time, max({time_column}) AS last_time
                           {baseline_sql}
                    FROM {table} GROUP BY segment_id, direction, road_type, source
                """), conn).set_index(groups).sort_index()
                try:
                    pd.testing.assert_frame_equal(db[ref.columns], ref, check_dtype=False,
                                                  rtol=1e-12, atol=1e-6)
                    check(f"{table} matches archive by road and source", True,
                          f"{len(ref):,} groups; counts, sums, flags and time ranges")
                except AssertionError as exc:
                    check(f"{table} matches archive by road and source", False, str(exc)[:700])

            db_segments = pd.read_sql(text("""
                SELECT * FROM segments ORDER BY segment_id
            """), conn).set_index("segment_id")
            segment_ref = s.set_index("segment_id").sort_index()
            common = [col for col in segment_ref.columns if col in db_segments.columns]
            try:
                pd.testing.assert_frame_equal(db_segments[common], segment_ref[common],
                                              check_dtype=False, rtol=1e-12, atol=1e-8)
                check("Segment attributes match archive", True, f"{len(common)} shared fields")
            except AssertionError as exc:
                check("Segment attributes match archive", False, str(exc)[:700])

            print("\nBaseline rows by source:")
            print(pd.read_sql(text("""
                SELECT source, count(*) AS rows, min(hour_start_utc) AS first_utc,
                       max(hour_start_utc) AS last_utc
                FROM hourly WHERE baseline_ok GROUP BY source ORDER BY source
            """), conn).to_string(index=False))
            print("\nBaseline attribute coverage:")
            print(pd.read_sql(text("""
                SELECT count(DISTINCT h.segment_id) AS segments,
                       count(*) FILTER (WHERE s.borough IS NULL) AS rows_without_borough,
                       count(*) FILTER (WHERE s.posted_speed IS NULL) AS rows_without_speed,
                       count(*) FILTER (WHERE s.number_travel_lanes IS NULL) AS rows_without_lanes,
                       count(*) FILTER (WHERE s.lon IS NULL OR s.lat IS NULL) AS rows_without_point
                FROM hourly h JOIN segments s USING(segment_id) WHERE h.baseline_ok
            """), conn).to_string(index=False))
    finally:
        engine.dispose()
    print(f"\nResult: {'FAILED: ' + ', '.join(failures) if failures else 'ALL CHECKS PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nVerification stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
