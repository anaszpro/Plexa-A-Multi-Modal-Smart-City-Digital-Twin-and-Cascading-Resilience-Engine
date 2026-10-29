from pathlib import Path
import os
import pandas as pd
from sqlalchemy import create_engine, text

OUT = Path(__file__).resolve().parents[5] / "data" / "mobility" / "clean"
URL = os.environ["PLEXA_DB_URL"]
eng = create_engine(URL)

# 1. Segments
seg = pd.read_parquet(f"{OUT}/segment_table_v3.parquet")
seg.columns = [c.lower() for c in seg.columns]
SEG_COLS = ["segment_id", "borough", "borough_source", "boro_disagreement", "lion_boro", "automated_boro",
            "lion_key_used", "attr_source", "in_automated", "in_historical", "has_minor_series", "hist_series",
            "hist_rows", "conflict_rows", "conflict_share", "identity_conflict_all", "first_hour", "last_hour",
            "posted_speed", "number_travel_lanes", "number_park_lanes", "number_total_lanes",
            "streetwidth_min", "streetwidth_max", "shape_length", "trafdir", "truck_route_type",
            "snow_priority", "bikelane", "lboro", "rboro", "lon", "lat", "lion_wkt_2263"]
missing = [c for c in SEG_COLS if c not in seg.columns]
assert not missing, f"segment_table_v3 is missing: {missing}"
seg = seg[SEG_COLS].copy()
seg["segment_id"] = seg["segment_id"].astype("int64")
seg.to_sql("segments", eng, if_exists="append", index=False, chunksize=5000, method="multi")
print("segments loaded:", len(seg))

with eng.begin() as c:
    c.execute(text("UPDATE segments SET lion_geom = ST_GeomFromText(lion_wkt_2263, 2263) "
                   "WHERE lion_wkt_2263 IS NOT NULL"))
    c.execute(text("UPDATE segments SET geom_4326 = ST_SetSRID(ST_MakePoint(lon, lat), 4326) "
                   "WHERE lon IS NOT NULL AND lat IS NOT NULL"))

# 2. Hourly, UTC (unambiguous hours)
C = pd.read_parquet(f"{OUT}/combined_hourly_v2.parquet")
ok = C[~C["dst_issue"]].copy()
ok["hour_start_utc"] = ok["hour_start_tz"].dt.tz_convert("UTC")
HCOLS = ["segment_id", "direction", "road_type", "hour_start_utc", "volume", "source", "study_id",
         "orig_interval_min", "identity_conflict", "partial_day", "covid", "sparse_year", "baseline_ok"]
ok = ok[HCOLS].copy()
ok["segment_id"] = ok["segment_id"].astype("int64")
ok["study_id"] = ok["study_id"].astype("int64")
assert not ok.duplicated(["segment_id", "direction", "road_type", "hour_start_utc"]).any(), "duplicate PK"
ok.to_sql("hourly", eng, if_exists="append", index=False, chunksize=20000, method="multi")
print("hourly loaded:", len(ok))

# 3. DST-flagged hours, local clock time
d = C[C["dst_issue"]].copy()
d = d.rename(columns={"hour_start": "hour_start_local"})
DCOLS = ["segment_id", "direction", "road_type", "hour_start_local", "volume", "source", "study_id",
         "orig_interval_min", "identity_conflict"]
d = d[DCOLS].copy()
d["segment_id"] = d["segment_id"].astype("int64")
d["study_id"] = d["study_id"].astype("int64")
assert not d.duplicated(["segment_id", "direction", "road_type", "hour_start_local"]).any(), "duplicate PK"
d.to_sql("hourly_dst_flagged", eng, if_exists="append", index=False, chunksize=20000, method="multi")
print("dst rows loaded:", len(d))