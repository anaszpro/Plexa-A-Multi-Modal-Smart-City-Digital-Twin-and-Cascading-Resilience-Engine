"""Collect NYC 311 Sewer complaints and preserve unresolved local times.

Dates use New York local time; --end is exclusive. Apply migrations 001
and 002 first. Normal and time-review records are saved in one transaction.

Without dates, resume with a one-day overlap through yesterday.
Use explicit date ranges for historical backfills: the latest stored
timestamp does not prove that all earlier periods were collected.
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
from psycopg2.extras import Json, execute_values


DATASET = "erm2-nwe9"
URL = f"https://data.cityofnewyork.us/resource/{DATASET}.json"
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
        unique_key, created_at, complaint_type,
        descriptor, borough, latitude, longitude
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

REVIEW_SQL = """
INSERT INTO raw.complaints_311_time_review
    (
        unique_key, created_date_local, time_issue,
        source_dataset, source_record
    )
VALUES %s
ON CONFLICT (unique_key) DO UPDATE SET
    created_date_local = EXCLUDED.created_date_local,
    time_issue = EXCLUDED.time_issue,
    source_dataset = EXCLUDED.source_dataset,
    source_record = EXCLUDED.source_record,
    last_seen_at = now()
"""


def request_json(session, params):
    """Retry transport failures, rate limits and server errors."""
    for attempt in range(4):
        try:
            with session.get(
                URL, params=params, timeout=(15, 120)
            ) as response:
                if (
                    response.status_code == 429
                    or response.status_code >= 500
                ):
                    raise requests.RequestException(
                        f"Temporary HTTP {response.status_code}"
                    )

                if response.status_code != 200:
                    raise RuntimeError(
                        f"Socrata HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )

                payload = response.json()

                if not isinstance(payload, list):
                    raise RuntimeError(
                        "Expected a list of source records"
                    )

                return payload

        except (requests.RequestException, ValueError) as error:
            if attempt == 3:
                raise RuntimeError(
                    "Socrata request failed after four attempts"
                ) from error

            time.sleep(2 ** attempt)


def fetch_window(session, start, end):
    """Check pagination counts.

    These checks detect some source changes but do not create
    a frozen snapshot of the API.
    """
    where = (
        f"created_date >= '{start}T00:00:00' "
        f"AND created_date < '{end}T00:00:00' "
        "AND complaint_type = 'Sewer'"
    )

    def count_source():
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

    expected = count_source()
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

    final_count = count_source()
    keys = [int(record["unique_key"]) for record in records]

    if len(keys) != len(set(keys)):
        raise RuntimeError(
            "Duplicate source IDs; retry this window"
        )

    if expected != final_count or len(records) != expected:
        raise RuntimeError(
            f"Coverage mismatch: before={expected}, "
            f"fetched={len(records)}, after={final_count}. "
            "Retry this window."
        )

    return records


def to_rows(records):
    """Partition every record into normal or time-review storage."""
    if not records:
        return [], [], {
            "missing_descriptors": 0,
            "missing_coordinates": 0,
        }

    frame = pd.DataFrame(records)

    for column in ("unique_key", "created_date", "complaint_type"):
        if column not in frame or frame[column].isna().any():
            raise ValueError(f"Missing required field: {column}")

    frame["unique_key"] = frame.unique_key.map(int)

    if frame.unique_key.duplicated().any():
        raise ValueError("Duplicate complaint IDs")

    if not frame.complaint_type.eq("Sewer").all():
        raise ValueError("Unexpected complaint type")

    for column in (
        "descriptor", "borough", "latitude", "longitude"
    ):
        if column not in frame:
            frame[column] = None

    local = pd.to_datetime(
        frame.created_date,
        errors="raise",
        format="mixed",
    )

    if local.isna().any() or local.dt.tz is not None:
        raise ValueError(
            "Expected nonempty, timezone-naive local timestamps"
        )

    # Classify timestamp problems separately.
    # Adjusted times from these two checks are never stored.
    ambiguous = local.dt.tz_localize(
        NY_TZ,
        ambiguous="NaT",
        nonexistent="shift_forward",
    ).isna()

    nonexistent = local.dt.tz_localize(
        NY_TZ,
        ambiguous=True,
        nonexistent="NaT",
    ).isna()

    unresolved = ambiguous | nonexistent

    # Only unambiguous, existing local times receive UTC values.
    frame["created_at"] = local.dt.tz_localize(
        NY_TZ,
        ambiguous="NaT",
        nonexistent="NaT",
    ).dt.tz_convert("UTC")

    review_rows = []

    for index in frame.index[unresolved]:
        issue = (
            "ambiguous_local_time"
            if ambiguous.loc[index]
            else "nonexistent_local_time"
        )

        review_rows.append(
            (
                int(frame.at[index, "unique_key"]),
                records[index]["created_date"],
                issue,
                DATASET,
                Json(records[index]),
            )
        )

    for column in ("latitude", "longitude"):
        frame[column] = pd.to_numeric(
            frame[column], errors="coerce"
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
        r"^\s*$", None, regex=True
    )

    def nullable(value):
        return None if pd.isna(value) else value

    normal_rows = [
        (
            int(row.unique_key),
            row.created_at.to_pydatetime(),
            row.complaint_type,
            nullable(row.descriptor),
            nullable(row.borough),
            nullable(row.latitude),
            nullable(row.longitude),
        )
        for row in frame.loc[~unresolved].itertuples()
    ]

    if len(normal_rows) + len(review_rows) != len(records):
        raise RuntimeError(
            "Not every source record was accounted for"
        )

    quality = {
        "missing_descriptors": int(
            frame.descriptor.isna().sum()
        ),
        "missing_coordinates": int(
            invalid_coordinates.sum()
        ),
    }

    return normal_rows, review_rows, quality


def save_window(conn, normal_rows, review_rows):
    """Commit both groups together; keep each ID in only one table.

    If a source correction changes a record's time status, remove its
    old copy from the opposite table in the same transaction.
    """
    with conn, conn.cursor() as cursor:
        if normal_rows:
            execute_values(
                cursor,
                UPSERT_SQL,
                normal_rows,
                page_size=1000,
            )

            cursor.execute(
                """
                DELETE FROM raw.complaints_311_time_review
                WHERE unique_key = ANY(%s)
                """,
                ([row[0] for row in normal_rows],),
            )

        if review_rows:
            execute_values(
                cursor,
                REVIEW_SQL,
                review_rows,
                page_size=1000,
            )

            cursor.execute(
                """
                DELETE FROM raw.complaints_311
                WHERE unique_key = ANY(%s)
                """,
                ([row[0] for row in review_rows],),
            )


def month_windows(start, end):
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
                "PLEXA_DB", "plexa_climate"
            )
        )
    ) as conn, requests.Session() as session:
        token = os.environ.get("SOCRATA_APP_TOKEN")

        if token:
            session.headers["X-App-Token"] = token

        with conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT descriptor "
                "FROM raw.complaints_311 LIMIT 0"
            )

            cursor.execute(
                "SELECT source_record "
                "FROM raw.complaints_311_time_review LIMIT 0"
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

        total_normal = 0
        total_review = 0

        for window_start, window_end in month_windows(start, end):
            records = fetch_window(
                session, window_start, window_end
            )

            normal, review, quality = to_rows(records)

            save_window(conn, normal, review)

            total_normal += len(normal)
            total_review += len(review)

            print(
                f"{window_start} -> {window_end}: "
                f"source={len(records)}, "
                f"normal_upserted={len(normal)}, "
                f"time_review_upserted={len(review)}, "
                f"missing_descriptors="
                f"{quality['missing_descriptors']}, "
                f"missing_coordinates="
                f"{quality['missing_coordinates']}"
            )

        print(
            f"Done. normal_upserted={total_normal}, "
            f"time_review_upserted={total_review}"
        )

        print(
            "Upsert counts include updates. "
            "Quality counts include both groups."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAILED: {error}", file=sys.stderr)
        sys.exit(1)
