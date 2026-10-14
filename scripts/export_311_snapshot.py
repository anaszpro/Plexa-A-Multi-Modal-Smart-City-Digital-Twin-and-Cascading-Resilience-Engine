"""Export a repeatable-read database snapshot; never modify PostgreSQL."""
import argparse
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2

from climate_artifacts import ROOT, new_artifact, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2020-10-14')
    parser.add_argument('--end', default='2026-10-07')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'data/processed/complaints_snapshot_v1')
    args = parser.parse_args()
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    if start >= end or start != start.normalize() or end != end.normalize():
        raise ValueError('Use increasing local calendar dates.')
    with closing(psycopg2.connect(dbname=os.getenv('PLEXA_DB', 'plexa_climate'))) as conn:
        conn.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with conn, conn.cursor() as cursor:
            cursor.execute("""SELECT unique_key, created_at, complaint_type,
                descriptor, borough, latitude, longitude FROM raw.complaints_311
                WHERE created_at >= %s::timestamp AT TIME ZONE 'America/New_York'
                AND created_at < %s::timestamp AT TIME ZONE 'America/New_York'
                AND complaint_type IN ('Sewer', 'Sewer Maintenance')
                ORDER BY unique_key""", (args.start, args.end))
            normal = pd.DataFrame(cursor.fetchall(), columns=[d[0] for d in cursor.description])
            cursor.execute("""SELECT unique_key, created_date_local, time_issue,
                source_dataset, source_record FROM raw.complaints_311_time_review
                WHERE created_date_local::timestamp >= %s::timestamp
                AND created_date_local::timestamp < %s::timestamp
                ORDER BY unique_key""", (args.start, args.end))
            review = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    normal['created_at'] = pd.to_datetime(normal.created_at, utc=True)
    if normal.unique_key.duplicated().any() or set(normal.unique_key) & {r['unique_key'] for r in review}:
        raise ValueError('Complaint IDs overlap or are duplicated.')
    with new_artifact(args.output) as out:
        normal.to_parquet(out / 'normal.parquet', index=False)
        write_json(out / 'time_review.json', review)
        write_json(out / 'manifest.json', {
            'created_utc': datetime.now(timezone.utc),
            'start_local_inclusive': args.start, 'end_local_exclusive': args.end,
            'normal_records': len(normal), 'review_records': len(review),
            'normal_sha256': sha256(out / 'normal.parquet'),
            'review_sha256': sha256(out / 'time_review.json'),
            'coverage_status': 'NOT_VERIFIED_AGAINST_SOURCE',
        })
    print(f'Exported {len(normal)} normal and {len(review)} review records to {args.output}')


if __name__ == '__main__':
    main()
