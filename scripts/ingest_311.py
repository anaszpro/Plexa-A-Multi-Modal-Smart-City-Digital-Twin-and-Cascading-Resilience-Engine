"""Load NYC 311 Sewer complaints into Postgres (raw.complaints_311).

Usage:
    python scripts/ingest_311.py --start 2021-08-28 --end 2021-09-03
    python scripts/ingest_311.py                  # incremental: from last loaded row to now

Dates are New York local dates (end exclusive). Timestamps are converted to
UTC before storing. Rows are upserted on unique_key, so re-runs are safe.
Optional: set SOCRATA_APP_TOKEN in the environment for higher rate limits.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import psycopg2
import requests
from psycopg2.extras import execute_values

URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
COMPLAINT_TYPE = "Sewer"
DEFAULT_START = "2020-10-01"   # radar archive starts mid Oct 2020
PAGE_SIZE = 5000
NY_TZ = "America/New_York"

UPSERT_SQL = """
    INSERT INTO raw.complaints_311
        (unique_key, created_at, complaint_type, borough, latitude, longitude)
    VALUES %s
    ON CONFLICT (unique_key) DO UPDATE SET
        created_at = EXCLUDED.created_at,
        complaint_type = EXCLUDED.complaint_type,
        borough = EXCLUDED.borough,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        ingested_at = now()
"""


def fetch_window(start, end):
    """All Sewer complaints with start <= created_date < end (NY local strings)."""
    headers = {}
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token

    records, offset = [], 0
    while True:
        params = {
            "$select": "unique_key,created_date,complaint_type,borough,latitude,longitude",
            "$where": (
                f"created_date >= '{start}T00:00:00' AND created_date < '{end}T00:00:00' "
                f"AND complaint_type = '{COMPLAINT_TYPE}'"
            ),
            "$order": "created_date,unique_key",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
        for attempt in range(4):
            resp = requests.get(URL, params=params, headers=headers, timeout=120)
            if resp.status_code == 200:
                break
            time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"Socrata request failed: {resp.status_code} {resp.text[:200]}")
        batch = resp.json()
        if not batch:
            break
        records.extend(batch)
        offset += PAGE_SIZE
    return records


def to_rows(records):
    if not records:
        return []
    df = pd.DataFrame(records)
    for col in ("borough", "latitude", "longitude"):
        if col not in df:
            df[col] = None
    local = pd.to_datetime(df["created_date"])
    # 311 times are NY local with no timezone; convert to UTC for storage
    df["created_at"] = local.dt.tz_localize(
        NY_TZ, ambiguous=True, nonexistent="shift_forward"
    ).dt.tz_convert("UTC")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.drop_duplicates("unique_key")

    def nn(x):
        return None if pd.isna(x) else x

    return [
        (int(r.unique_key), r.created_at.to_pydatetime(), r.complaint_type,
         nn(r.borough), nn(r.latitude), nn(r.longitude))
        for r in df.itertuples()
    ]


def month_windows(start, end):
    cur = datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    while cur < stop:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        nxt = min(nxt, stop)
        yield cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")
        cur = nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()

    conn = psycopg2.connect(dbname=os.environ.get("PLEXA_DB", "plexa_climate"))

    start = args.start
    if not start:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT max(created_at AT TIME ZONE 'America/New_York')::date "
                "FROM raw.complaints_311"
            )
            last = cur.fetchone()[0]
        # step back one day so late-arriving records are picked up
        start = (last - timedelta(days=1)).strftime("%Y-%m-%d") if last else DEFAULT_START
    end = args.end or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    if start >= end:
        sys.exit(f"Nothing to do: start {start} >= end {end}")

    print(f"Pulling '{COMPLAINT_TYPE}' complaints from {start} to {end} (end exclusive)")
    total = 0
    for w_start, w_end in month_windows(start, end):
        rows = to_rows(fetch_window(w_start, w_end))
        if rows:
            with conn, conn.cursor() as cur:
                execute_values(cur, UPSERT_SQL, rows, page_size=1000)
        total += len(rows)
        print(f"  {w_start} -> {w_end}: {len(rows)} complaints")
    conn.close()
    print(f"Done. {total} complaints loaded.")


if __name__ == "__main__":
    main()
