"""Level-2 mini pipeline: NYC 311 API -> Safety/Governance module -> SQLite.

    python -m safety_governance.ingest_311 --start 2021-08-31 --end 2021-09-02

Pulls 311 reports created in [start, end) from the Socrata API, skips reports that were already
processed, runs the rest through SafetyGovernancePipeline.process_311, and stores the incident
nodes in a SQLite file (default: <data dir>/incidents.db, git-ignored).

Only produced nodes are stored (never raw report rows). Guidance is stored as a source id + score.
Running the same window twice adds nothing new.

Note: these are REPORTS turned into nodes, not merged incidents; many reports can describe the
same flooded street. Merging by place/time is a later step.
"""
import argparse
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .pipeline import DATA_DIR, SafetyGovernancePipeline

API = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
COLUMNS = "unique_key,created_date,complaint_type,descriptor,incident_address,borough,latitude,longitude"

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_reports (
    source_key   TEXT PRIMARY KEY,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    incident_id     TEXT PRIMARY KEY,
    source          TEXT,
    source_key      TEXT,
    method          TEXT,
    reported_at     TEXT,
    hazard_type     TEXT,
    severity        TEXT,
    confidence      REAL,
    score_type      TEXT,
    location_text   TEXT,
    borough         TEXT,
    lat             REAL,
    lon             REAL,
    guidance_source TEXT,
    guidance_score  REAL,
    status          TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_time ON incidents(reported_at);
"""


def fetch_page(where, offset, page):
    params = {"$select": COLUMNS, "$where": where, "$order": "unique_key",
              "$limit": page, "$offset": offset}
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params))
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if token:
        req.add_header("X-App-Token", token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_all(start, end, max_rows=200000, page=10000):
    where = f"created_date between '{start}T00:00:00' and '{end}T00:00:00'"
    rows, offset = [], 0
    while offset < max_rows:
        batch = fetch_page(where, offset, page)
        if not batch:
            break
        rows.extend(batch)
        offset += page
    return rows


def ingest(start, end, db_path, max_rows=200000, fetch=None, pipe_factory=None):
    datetime.strptime(start, "%Y-%m-%d")
    datetime.strptime(end, "%Y-%m-%d")
    fetch = fetch or fetch_all
    pipe_factory = pipe_factory or SafetyGovernancePipeline

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    rows = fetch(start, end, max_rows)
    seen = {r[0] for r in conn.execute("SELECT source_key FROM processed_reports")}
    new_rows = [r for r in rows if str(r.get("unique_key")) not in seen]
    stats = {"pulled": len(rows), "already_processed": len(rows) - len(new_rows),
             "new_reports": len(new_rows), "new_incidents": 0}

    if new_rows:
        pipe = pipe_factory()                      # loads models only when there is work to do
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            for r in new_rows:
                key = str(r["unique_key"])
                node = pipe.process_311(r)
                if node:
                    g = node.get("guidance") or {}
                    conn.execute(
                        "INSERT OR IGNORE INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (node["incident_id"], node["source"], key, node["method"], node["timestamp"],
                         node["hazard_type"], node["severity"], node["confidence"], node["score_type"],
                         node["location_text"], node["borough"], node["lat"], node["lon"],
                         g.get("guidance_source"), g.get("score"), node["status"]))
                    stats["new_incidents"] += 1
                conn.execute("INSERT OR IGNORE INTO processed_reports VALUES (?,?)", (key, now))

    stats["total_incidents"] = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    stats["by_type"] = dict(conn.execute(
        "SELECT hazard_type, COUNT(*) FROM incidents GROUP BY hazard_type ORDER BY 2 DESC").fetchall())
    stats["by_day"] = conn.execute(
        "SELECT substr(reported_at,1,10) AS day, hazard_type, COUNT(*) FROM incidents "
        "GROUP BY day, hazard_type ORDER BY day").fetchall()
    conn.close()
    return stats


def main():
    ap = argparse.ArgumentParser(description="NYC 311 -> Safety/Governance -> SQLite")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--db", default=str(DATA_DIR / "incidents.db"))
    ap.add_argument("--max-rows", type=int, default=200000)
    a = ap.parse_args()

    s = ingest(a.start, a.end, a.db, a.max_rows)
    print(f"\nWindow {a.start} to {a.end}  |  database: {a.db}")
    print(f"pulled {s['pulled']} reports: {s['already_processed']} already processed, "
          f"{s['new_reports']} new -> {s['new_incidents']} new incident nodes")
    print(f"total incidents in database: {s['total_incidents']}  {s['by_type']}")
    print("\nincidents per day and type:")
    for day, htype, n in s["by_day"]:
        print(f"  {day}  {htype:14s} {n}")


if __name__ == "__main__":
    main()
