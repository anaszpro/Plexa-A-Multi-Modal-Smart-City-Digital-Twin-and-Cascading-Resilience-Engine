"""Compare every monthly source record with the frozen database snapshot.

Writes resumable evidence per window. A failed window never authorizes zeros.
API count checks detect changes during paging, but are not an upstream snapshot.
"""
import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from climate_artifacts import ROOT, sha256, write_json
from ingest_311 import fetch_window, month_windows, to_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'data/processed/complaints_snapshot_v1')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/processed/complaint_coverage_v1')
    args = parser.parse_args()
    meta = json.loads((args.snapshot / 'manifest.json').read_text())
    normal = pd.read_parquet(args.snapshot / 'normal.parquet')
    review = json.loads((args.snapshot / 'time_review.json').read_text())
    fingerprint = {name: sha256(args.snapshot / name)
                   for name in ['normal.parquet', 'time_review.json', 'manifest.json']}
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = []
    with requests.Session() as session:
        for start, end in month_windows(date.fromisoformat(meta['start_local_inclusive']),
                                        date.fromisoformat(meta['end_local_exclusive'])):
            path = args.output / f'{start}_{end}.json'
            if path.exists():
                prior = json.loads(path.read_text())
                if prior['snapshot_sha256'] != fingerprint:
                    raise ValueError('Coverage evidence refers to another snapshot.')
                if prior['status'] == 'verified':
                    evidence.append(prior)
                    print(f'SKIP verified {start} -> {end}', flush=True)
                    continue
            item = {'start_local': str(start), 'end_local_exclusive': str(end),
                    'checked_utc': datetime.now(timezone.utc).isoformat(),
                    'snapshot_sha256': fingerprint, 'status': 'failed'}
            try:
                records = fetch_window(session, start, end)
                rows, unresolved, _ = to_rows(records)
                columns = ['unique_key', 'created_at', 'complaint_type', 'descriptor',
                           'borough', 'latitude', 'longitude']
                incoming = pd.DataFrame(rows, columns=columns)
                incoming['created_at'] = pd.to_datetime(incoming.created_at, utc=True)
                lower = pd.Timestamp(start, tz='America/New_York').tz_convert('UTC')
                upper = pd.Timestamp(end, tz='America/New_York').tz_convert('UTC')
                stored = normal.loc[normal.created_at.ge(lower) & normal.created_at.lt(upper), columns]
                pd.testing.assert_frame_equal(
                    incoming.sort_values('unique_key').reset_index(drop=True),
                    stored.sort_values('unique_key').reset_index(drop=True),
                    check_dtype=False, check_exact=True)
                expected_review = {r['unique_key']: r for r in review
                                   if str(start) <= r['created_date_local'][:10] < str(end)}
                actual_review = {r[0]: {'unique_key': r[0], 'created_date_local': r[1],
                    'time_issue': r[2], 'source_dataset': r[3], 'source_record': r[4].adapted}
                    for r in unresolved}
                if expected_review != actual_review:
                    raise ValueError('Unresolved-time records differ from source.')
                item.update(status='verified', normal_records=len(rows), review_records=len(unresolved),
                            source_records=len(records), start_utc=lower.isoformat(), end_utc_exclusive=upper.isoformat())
            except Exception as error:
                item['error'] = f'{type(error).__name__}: {error}'[:2000]
            temporary = path.with_suffix('.tmp')
            write_json(temporary, item)
            temporary.replace(path)
            evidence.append(item)
            print(f"{item['status'].upper()} {start} -> {end}", flush=True)
    summary = {'windows': len(evidence), 'verified': sum(e['status'] == 'verified' for e in evidence),
               'snapshot_sha256': fingerprint,
               'limitation': 'Agreement with a paginated live API at check time, not proof of complete public reporting.'}
    write_json(args.output / 'manifest.json', summary)
    print(json.dumps(summary, indent=2))
    if summary['verified'] != summary['windows']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
