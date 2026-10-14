"""Load NYC 311 Sewer complaints, including descriptors, into PostgreSQL.

Explicit dates are New York local dates; the end date is exclusive.
Without dates, resume with a one-day overlap through yesterday.
Run sql/migrations/001_add_311_descriptor.sql before using this script.
"""

import argparse
import os
import sys
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
import requests
from psycopg2.extras import execute_values


URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
NY_TZ = "America/New_York"
PAGE_SIZE = 5000
DEFAULT_START = date(2020, 10, 1)

FIELDS = (
    "unique_key,created_date,complaint_type,"
    "descriptor,borough,latitude,longitude"
)

UPSERT_SQL = """
INSERT INTO raw.complaints_311
    (
        unique_key,
        created_at,
        complaint_type,
        descriptor,
        borough,
        latitude,
        longitude
    )
VALUES %s
ON CONFLICT (unique_key) DO UPDATE SET
    created_at = EXCLUDED.created_at,
    complaint_type = EXCLUDED.complaint_type,
    descriptor = EXCLUDED.descriptor,
    borough = EXCLUDED.borough,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    ingested_at = now()
"""


def request_json(session, params):
    """Retry transport failures, rate limits and server errors."""
    for attempt in range(4):
        try:
            with session.get(
                URL,
                params=params,
                timeout=(15, 120),
            ) as response:
                if (
                    response.status_code == 429
                    or response.status_code >= 500
                ):
                    raise requests.RequestException(
                        f"Temporary HTTP error {response.status_code}"
                    )

                if response.status_code != 200:
                    raise RuntimeError(
                        f"Socrata HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )

                payload = response.json()

                if not isinstance(payload, list):
                    raise RuntimeError(
                        "Expected a list of records from Socrata"
                    )

                return payload

        except (requests.RequestException, ValueError) as error:
            if attempt == 3:
                raise RuntimeError(
                    "Socrata request failed after four attempts"
                ) from error

            time.sleep(2 ** attempt)


def fetch_window(session, start, end):
    """Check counts and duplicate keys before accepting a monthly window.

    Count checks detect some source changes; the API is not a frozen snapshot.
    """
    where = (
        f"created_date >= '{start}T00:00:00' "
        f"AND created_date < '{end}T00:00:00' "
        "AND complaint_type = 'Sewer'"
    )

    def source_count():
        result = request_json(
            session,
            {
                "$where": where,
                "$select": "count(*) AS total",
            },
        )

        if len(result) != 1 or "total" not in result[0]:
            raise RuntimeError("Unexpected source-count response")

        return int(result[0]["total"])

    expected = source_count()
    records = []
    offset = 0

    while True:
        batch = request_json(
            session,
            {
                "$select": FIELDS,
                "$where": where,
                "$order": "created_date,unique_key",
                "$limit": PAGE_SIZE,
                "$offset": offset,
            },
        )

        records.extend(batch)

        if len(batch) < PAGE_SIZE:
            break

        offset += len(batch)

    final_count = source_count()
    keys = [int(record["unique_key"]) for record in records]

    if len(keys) != len(set(keys)):
        raise RuntimeError(
            "Duplicate complaint keys returned; retry this window"
        )

    if expected != final_count or len(records) != expected:
        raise RuntimeError(
            f"Coverage mismatch: before={expected}, "
            f"fetched={len(records)}, after={final_count}. "
            "Retry this window."
        )

    return records


def to_rows(records):
    """Validate records and convert unambiguous New York times to UTC."""
    if not records:
        return [], {
            "missing_descriptors": 0,
            "missing_coordinates": 0,
        }

    frame = pd.DataFrame(records)

    for column in ("unique_key", "created_date", "complaint_type"):
        if column not in frame or frame[column].isna().any():
            raise ValueError(
                f"Missing required source field: {column}"
            )

    if not frame.complaint_type.eq("Sewer").all():
        raise ValueError("Unexpected complaint type")

    if frame.unique_key.duplicated().any():
        raise ValueError("Duplicate complaint keys")

    for column in (
        "descriptor",
        "borough",
        "latitude",
        "longitude",
    ):
        if column not in frame:
            frame[column] = None

    local_time = pd.to_datetime(
        frame.created_date,
        errors="raise",
    )

    if local_time.dt.tz is not None:
        raise ValueError(
            "Expected timezone-naive New York source timestamps"
        )

    checked = local_time.dt.tz_localize(
        NY_TZ,
        ambiguous="NaT",
        nonexistent="NaT",
    )

    if checked.isna().any():
        raise ValueError(
            f"{checked.isna().sum()} ambiguous or nonexistent "
            "local timestamps. This window was not saved; "
            "investigate before assigning UTC times."
        )

    frame["created_at"] = checked.dt.tz_convert("UTC")

    for column in ("latitude", "longitude"):
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    invalid_coordinates = (
        ~frame.latitude.between(-90, 90)
        | ~frame.longitude.between(-180, 180)
    )

    frame.loc[
        invalid_coordinates,
        ["latitude", "longitude"],
    ] = float("nan")

    frame["descriptor"] = frame.descriptor.replace(
        r"^\s*$",
        None,
        regex=True,
    )

    def nullable(value):
        return None if pd.isna(value) else value

    rows = [
        (
            int(row.unique_key),
            row.created_at.to_pydatetime(),
            row.complaint_type,
            nullable(row.descriptor),
            nullable(row.borough),
            nullable(row.latitude),
            nullable(row.longitude),
        )
        for row in frame.itertuples()
    ]

    quality = {
        "missing_descriptors": int(
            frame.descriptor.isna().sum()
        ),
        "missing_coordinates": int(
            invalid_coordinates.sum()
        ),
    }

    return rows, quality


def month_windows(start, end):
    """Split the requested period into bounded monthly windows."""
    current = start

    while current < end:
        next_month = (
            current.replace(day=1) + timedelta(days=32)
        ).replace(day=1)

        stop = min(next_month, end)

        yield current, stop

        current = stop


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)

    args = parser.parse_args()

    if (args.start is None) != (args.end is None):
        parser.error(
            "Provide both --start and --end, or neither"
        )

    if args.start is not None and args.start >= args.end:
        parser.error(
            "Start must be earlier than the exclusive end date"
        )

    with closing(
        psycopg2.connect(
            dbname=os.environ.get(
                "PLEXA_DB",
                "plexa_climate",
            )
        )
    ) as conn, requests.Session() as session:
        token = os.environ.get("SOCRATA_APP_TOKEN")

        if token:
            session.headers["X-App-Token"] = token

        with conn, conn.cursor() as cursor:
            # Fail early if the descriptor migration was not applied.
            cursor.execute(
                "SELECT descriptor "
                "FROM raw.complaints_311 LIMIT 0"
            )

            cursor.execute("""
                SELECT max(
                    created_at AT TIME ZONE 'America/New_York'
                )::date
                FROM raw.complaints_311
            """)

            last = cursor.fetchone()[0]

        start = args.start or (
            last - timedelta(days=1)
            if last
            else DEFAULT_START
        )

        # Exclude today's incomplete local day in incremental mode.
        end = args.end or datetime.now(
            ZoneInfo(NY_TZ)
        ).date()

        if start >= end:
            print("No completed daily window to ingest.")
            return

        print(
            f"Collecting Sewer complaints: {start} to {end} "
            "(end exclusive)"
        )

        total = 0

        for window_start, window_end in month_windows(start, end):
            records = fetch_window(
                session,
                window_start,
                window_end,
            )

            rows, quality = to_rows(records)

            # Each window is committed as one transaction.
            with conn, conn.cursor() as cursor:
                if rows:
                    execute_values(
                        cursor,
                        UPSERT_SQL,
                        rows,
                        page_size=1000,
                    )

            total += len(rows)

            print(
                f"{window_start} -> {window_end}: "
                f"source={len(records)}, "
                f"upserted={len(rows)}, "
                f"missing_descriptors="
                f"{quality['missing_descriptors']}, "
                f"missing_coordinates="
                f"{quality['missing_coordinates']}"
            )

        print(
            f"Done. {total} complaint records upserted "
            "(inserted or updated)."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAILED: {error}", file=sys.stderr)
        sys.exit(1)
