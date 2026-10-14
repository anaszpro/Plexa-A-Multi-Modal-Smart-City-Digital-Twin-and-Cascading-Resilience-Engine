"""Export incidents from the SQLite file to a JSON file the frontend can read.

    python -m safety_governance.export_incidents

Reads <data dir>/incidents.db (written by ingest_311) and writes frontend/public/data/incidents.json.
Only produced incident nodes are exported (no raw report text).
"""
import argparse
import json
import sqlite3
from pathlib import Path

from .pipeline import DATA_DIR, REPO_ROOT


def export(db_path, out_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM incidents ORDER BY reported_at").fetchall()
    conn.close()

    items = []
    for r in rows:
        d = dict(r)
        d["timestamp"] = d.pop("reported_at")
        d.pop("source_key", None)
        items.append(d)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items), encoding="utf-8")
    return len(items), sum(1 for d in items if d["lat"] is not None and d["lon"] is not None)


def main():
    ap = argparse.ArgumentParser(description="incidents.db -> frontend/public/data/incidents.json")
    ap.add_argument("--db", default=str(DATA_DIR / "incidents.db"))
    ap.add_argument("--out", default=str(REPO_ROOT / "frontend" / "public" / "data" / "incidents.json"))
    a = ap.parse_args()
    if not Path(a.db).exists():
        raise SystemExit(f"{a.db} not found. Run: python -m safety_governance.ingest_311 --start 2021-08-28 --end 2021-09-03")
    n, with_coords = export(a.db, a.out)
    print(f"exported {n} incidents ({with_coords} with coordinates) to {a.out}")


if __name__ == "__main__":
    main()
