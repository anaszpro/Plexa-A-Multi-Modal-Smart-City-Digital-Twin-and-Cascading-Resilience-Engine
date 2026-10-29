from pathlib import Path
import pandas as pd

OUT = Path(__file__).resolve().parents[5] / "data" / "mobility" / "clean"

# 1. Combined hourly: reinstate the historical conflict rows for segment 29555
C = pd.read_parquet(f"{OUT}/combined_hourly_v2.parquet")
m = (C["source"] == "historical") & (C["segment_id"] == 29555) & C["identity_conflict"]
C.loc[m, "identity_conflict"] = False
C["baseline_ok"] = C["road_type"].eq("unspecified") & ~C["identity_conflict"] & ~C["dst_issue"]
C.to_parquet(f"{OUT}/combined_hourly_v3.parquet", index=False)
print("29555 rows reinstated:", int(m.sum()), "(expect 251)")
print("identity_conflict (incl. DST rows):", int(C["identity_conflict"].sum()), "(expect 11849)")
print("baseline_ok:", int(C["baseline_ok"].sum()), "(expect 1412050)")

# 2. Segment table: borough decisions
S = pd.read_parquet(f"{OUT}/segment_table_v3.parquet")
REVIEW = {155079: "Manhattan", 158339: "Brooklyn", 66420: "Brooklyn"}
r = S["segment_id"].isin(REVIEW.keys())
S.loc[r, "borough"] = S.loc[r, "segment_id"].map(REVIEW)
S.loc[r, "borough_source"] = "reviewed"
S.loc[S["segment_id"].eq(136594), "borough_source"] = "automated_unresolved"
S.to_parquet(f"{OUT}/segment_table_v4.parquet", index=False)
print(S.loc[S["segment_id"].isin([155079, 158339, 66420, 136594]),
            ["segment_id", "borough", "borough_source"]].to_string(index=False))