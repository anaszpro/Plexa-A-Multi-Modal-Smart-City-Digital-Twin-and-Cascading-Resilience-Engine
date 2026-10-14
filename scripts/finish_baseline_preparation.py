"""Finish monthly weather and dataset checks after the radar job; never train.

Resume this script without the wait flag after an interruption. Existing monthly
partitions are reused only when their configuration and source hashes still agree.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from build_baseline_weather import build
from climate_artifacts import ROOT, sha256, write_json


def monthly_windows(start, end):
    current = start
    while current < end:
        next_month = (current.normalize().replace(day=1) + pd.offsets.MonthBegin(1))
        stop = min(next_month, end)
        yield current, stop
        current = stop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-for-download-log', type=Path)
    parser.add_argument('--maximum-wait-hours', type=float, default=72)
    args = parser.parse_args()
    status_path = ROOT / 'data/processed/baseline_preparation_status.json'

    def status(stage, **extra):
        write_json(status_path, {'updated_utc': datetime.now(timezone.utc), 'stage': stage,
                                'training_performed': False, **extra})
        print(stage, extra, flush=True)

    try:
        if args.wait_for_download_log:
            deadline = time.monotonic() + args.maximum_wait_hours * 3600
            status('WAITING_FOR_RADAR', log=str(args.wait_for_download_log))
            while True:
                if args.wait_for_download_log.exists():
                    with args.wait_for_download_log.open('rb') as handle:
                        handle.seek(max(0, args.wait_for_download_log.stat().st_size - 4096))
                        tail = handle.read().decode(errors='replace')
                    if re.search(r'Summary: written=\d+, validated_skips=\d+, failed=\d+', tail):
                        break
                if time.monotonic() >= deadline:
                    raise TimeoutError('No downloader completion summary within the wait limit.')
                time.sleep(30)
        config = json.loads((ROOT / 'config/climate_preparation.json').read_text())
        processed = ROOT / 'data/processed'
        radar = ROOT / 'data/raw/radar_parquet_v2'
        weather = processed / 'baseline_weather_v1'
        reference = processed / 'radar_download_validation/radar_20210902_000000.parquet'
        start, end = pd.Timestamp(config['snapshot_start_utc']), pd.Timestamp(config['snapshot_end_utc_exclusive'])
        for lower, upper in monthly_windows(start, end):
            destination = weather / lower.strftime('%Y-%m')
            status('BUILDING_WEATHER', month=destination.name)
            if destination.exists():
                prior = json.loads((destination / 'manifest.json').read_text())
                if prior['preparation_config'] != config:
                    raise ValueError(f'Configuration changed for {destination}; use a new output version.')
                if any(sha256(radar / name) != digest for name, digest in prior['source_sha256'].items()):
                    raise ValueError(f'Rainfall changed for {destination}; rebuild into a new output version.')
                for timestamp in prior['missing_source_hours']:
                    if (radar / f'radar_{pd.Timestamp(timestamp):%Y%m%d_%H%M%S}.parquet').exists():
                        raise ValueError(f'Previously missing rainfall arrived for {destination}; rebuild into a new version.')
                if prior['invalid_source_files']:
                    raise ValueError(f'Invalid source files need review: {destination}')
                continue
            build(lower, upper, radar, reference, destination, config)
        dataset = processed / 'baseline_dataset_v1'
        status('BUILDING_DATASET')
        if not dataset.exists():
            subprocess.run([sys.executable, str(ROOT / 'scripts/build_baseline_dataset.py'),
                            '--weather-dir', str(weather), '--output', str(dataset)], check=True)
        dataset_meta = json.loads((dataset / 'manifest.json').read_text())
        if dataset_meta['configuration'] != config:
            raise ValueError('Dataset config changed; use a new dataset version.')
        for path, digest in dataset_meta['input_sha256'].items():
            if sha256(Path(path)) != digest:
                raise ValueError(f'Dataset input changed: {path}')
        status('CHECKING_SPLITS')
        con = duckdb.connect(str(dataset / 'dataset.duckdb'), read_only=True)
        try:
            con.execute('SET threads=2')
            con.execute("SET memory_limit='2GB'")
            rows = con.execute('''SELECT split, label, count(*) FROM eligible
                                  GROUP BY split,label ORDER BY split,label''').fetchall()
            report = [{'split': split, 'label': label, 'rows': count} for split, label, count in rows]
            gate = all(any(row['split'] == split and row['label'] == label and row['rows'] > 0
                           for row in report) for split in ['train', 'validation', 'test'] for label in [0, 1])
            for split in ['train', 'validation', 'test']:
                status('CHECKING_SPLITS', complete=split)
            gaps = []
            names = ['train', 'validation', 'test']
            for left, right in zip(names, names[1:]):
                a, b = config['splits'][left][1], config['splits'][right][0]
                # Dry citywide rainfall hours break events. Missing cells cannot prove a dry break.
                hourly = con.execute('''SELECT prediction_time_utc::VARCHAR,
                    max(rain_1h_mm), count(*) FILTER (WHERE rain_1h_mm IS NULL)
                    FROM weather WHERE prediction_time_utc >= ?::TIMESTAMPTZ
                      AND prediction_time_utc < ?::TIMESTAMPTZ GROUP BY 1 ORDER BY 1''', [a, b]).fetchall()
                run = longest = 0
                previous = None
                for stamp, maximum, missing in hourly:
                    current = pd.Timestamp(stamp)
                    if previous is not None and current - previous != pd.Timedelta(hours=1):
                        run = 0
                    run = run + 1 if maximum is not None and maximum <= 0.1 and missing == 0 else 0
                    longest = max(longest, run)
                    previous = current
                gaps.append({'between': [left, right], 'longest_citywide_dry_run_hours': longest,
                             'six_hour_event_break_found': longest >= 6})
            gate = gate and all(g['six_hour_event_break_found'] for g in gaps)
            totals = con.execute('SELECT count(*),count(*) FILTER(WHERE weather_features_valid) FROM weather').fetchone()
            valid_fraction = totals[1] / totals[0] if totals[0] else 0
            gate = gate and valid_fraction >= 0.95
        finally:
            con.close()
        report_path = processed / 'baseline_pretraining_report.json'
        write_json(report_path, {'split_counts': report, 'event_boundary_checks': gaps,
            'valid_weather_fraction': valid_fraction, 'checks_passed': gate,
            'not_complete_until_reviewed': ['Assess exclusion bias by borough and storm.',
                'Review raw archive gaps, source provenance, and static terrain limitations.',
                'Review Git changes and freeze the dataset.'], 'training_performed': False})
        status('AWAITING_FINAL_REVIEW' if gate else 'QUALITY_REVIEW_REQUIRED', report=str(report_path))
    except Exception as error:
        status('FAILED', error=f'{type(error).__name__}: {error}')
        raise


if __name__ == '__main__':
    main()
