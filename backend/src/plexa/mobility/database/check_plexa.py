from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text
import os
URL = os.environ["PLEXA_DB_URL"]
eng = create_engine(URL)
OUT = Path(__file__).resolve().parents[5] / "data" / "mobility" / "clean"

checks = {
    "segments (4488)": ("SELECT count(*) FROM segments", 4488),
    "hourly (1428980)": ("SELECT count(*) FROM hourly", 1428980),
    "dst flagged (658)": ("SELECT count(*) FROM hourly_dst_flagged", 658),
    "baseline_ok (1411799)": ("SELECT count(*) FROM hourly WHERE baseline_ok", 1411799),
    "orphan hours (0)": ("SELECT count(*) FROM hourly h LEFT JOIN segments s USING (segment_id) "
                         "WHERE s.segment_id IS NULL", 0),
    "LION geometry missing (0)": ("SELECT count(*) FROM segments WHERE lion_wkt_2263 IS NOT NULL "
                                  "AND lion_geom IS NULL", 0),
    "hypertable registered (1)": ("SELECT count(*) FROM timescaledb_information.hypertables "
                                  "WHERE hypertable_name = 'hourly'", 1),
}
with eng.connect() as c:
    for name, (sql, expected) in checks.items():
        got = c.execute(text(sql)).scalar()
        print(f"{name:30s} got={got} {'OK' if got == expected else 'MISMATCH'}")

    db = pd.read_sql("SELECT source, sum(volume) AS total FROM hourly GROUP BY source", c)
C = pd.read_parquet(f"{OUT}/combined_hourly_v2.parquet", columns=["source", "volume", "dst_issue"])
ref = C[~C["dst_issue"]].groupby("source")["volume"].sum()
print("\nVolume sums, database vs parquet (non-DST rows):")
for _, row in db.iterrows():
    diff = abs(row["total"] - ref[row["source"]])
    print(f"  {row['source']:10s} db={row['total']:.1f} parquet={ref[row['source']]:.1f} "
          f"diff={diff:.6f} {'OK' if diff < 1e-3 else 'MISMATCH'}")