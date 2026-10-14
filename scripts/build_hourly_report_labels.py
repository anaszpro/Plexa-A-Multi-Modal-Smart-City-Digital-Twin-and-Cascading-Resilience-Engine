"""Build sparse positives and uncertainty masks, not a dense street/hour grid."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from climate_artifacts import ROOT, new_artifact, sha256, write_json


def make_labels(audit, candidates, matched, review, rules):
    audit = audit.copy()
    audit['prediction_time_utc'] = pd.to_datetime(audit.created_at, utc=True).dt.floor('h')
    matched = matched.copy()
    matched['prediction_time_utc'] = pd.to_datetime(matched.created_at, utc=True).dt.floor('h')
    positive = matched.groupby(['street_segment_id', 'prediction_time_utc'], as_index=False).agg(
        report_count=('unique_key', 'nunique'))
    positive['label'] = 1
    spatial = candidates.merge(
        audit.loc[audit.match_status.eq('ambiguous_segment'), ['unique_key', 'prediction_time_utc']],
        on='unique_key', validate='many_to_one')
    spatial = spatial[['street_segment_id', 'prediction_time_utc']].drop_duplicates()
    global_hours = set(audit.loc[audit.match_status.isin([
        'missing_or_invalid_coordinates', 'outside_match_distance']), 'prediction_time_utc'])
    pairs = {(r['complaint_type'], r['descriptor']) for r in rules['positive_report_rules']}
    excluded_review = 0
    for item in review:
        record = item['source_record']
        if (record.get('complaint_type'), record.get('descriptor')) not in pairs:
            continue
        excluded_review += 1
        day = pd.Timestamp(item['created_date_local']).date()
        # Localize both midnights independently: DST dates have 23 or 25 hours.
        start = pd.Timestamp(day).tz_localize('America/New_York').tz_convert('UTC')
        end = pd.Timestamp(day + timedelta(days=1)).tz_localize('America/New_York').tz_convert('UTC')
        global_hours.update(pd.date_range(start, end, freq='h', inclusive='left'))
    global_mask = pd.DataFrame({'prediction_time_utc': pd.DatetimeIndex(sorted(global_hours), tz='UTC')})
    return positive, spatial, global_mask, excluded_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/processed/hourly_report_labels_v1')
    args = parser.parse_args()
    processed = ROOT / 'data/processed'
    mapping = processed / 'physical_311_mapping_v1'
    snapshot = processed / 'complaints_snapshot_v1'
    config_path = ROOT / 'config/climate_baseline_target.json'
    config = json.loads(config_path.read_text())
    rules_path = ROOT / config['positive_rules']['path']
    if sha256(rules_path) != config['positive_rules']['sha256']:
        raise ValueError('Positive rules hash mismatch.')
    if config['row_definition']['prediction_horizon_hours'] != 1:
        raise ValueError('This implementation supports a one-hour horizon only.')
    rules = json.loads(rules_path.read_text())
    manifest = json.loads((mapping / 'manifest.json').read_text())
    original = manifest['source_mapping_manifest']
    if original['rules_sha256'] != sha256(rules_path):
        raise ValueError('Mapping uses different positive rules.')
    normal = pd.read_parquet(snapshot / 'normal.parquet')
    review = json.loads((snapshot / 'time_review.json').read_text())
    audit = pd.read_parquet(mapping / 'report_audit.parquet')
    candidates = pd.read_parquet(mapping / 'candidate_segments.parquet')
    matched = pd.read_parquet(mapping / 'matched_reports.parquet')
    pairs = {(r['complaint_type'], r['descriptor']) for r in rules['positive_report_rules']}
    selected = normal.loc[[(a, b) in pairs for a, b in zip(normal.complaint_type, normal.descriptor)]]
    columns = ['unique_key', 'created_at', 'complaint_type', 'descriptor', 'latitude', 'longitude']
    pd.testing.assert_frame_equal(
        selected[columns].sort_values('unique_key').reset_index(drop=True),
        audit[columns].sort_values('unique_key').reset_index(drop=True), check_dtype=False)
    snapshot_meta = json.loads((snapshot / 'manifest.json').read_text())
    for key in ['start_local_inclusive', 'end_local_exclusive']:
        if snapshot_meta[key] != original[key]:
            raise ValueError('Snapshot and mapping date windows differ.')
    positive, spatial, global_mask, excluded_review = make_labels(audit, candidates, matched, review, rules)
    if excluded_review != original['unresolved_time_candidates_excluded']:
        raise ValueError('Unresolved timestamp count changed; rebuild mapping.')
    start = pd.Timestamp(original['start_local_inclusive'], tz='America/New_York').tz_convert('UTC')
    end = pd.Timestamp(original['end_local_exclusive'], tz='America/New_York').tz_convert('UTC')
    global_mask = global_mask.loc[global_mask.prediction_time_utc.ge(start) & global_mask.prediction_time_utc.lt(end)]
    inputs = [config_path, rules_path, snapshot / 'normal.parquet', snapshot / 'time_review.json',
              snapshot / 'manifest.json', mapping / 'manifest.json', mapping / 'report_audit.parquet',
              mapping / 'candidate_segments.parquet', mapping / 'matched_reports.parquet']
    summary = {
        'created_utc': datetime.now(timezone.utc), 'start_utc': start, 'end_utc_exclusive': end,
        'positive_segment_hours': len(positive), 'positive_reports': int(positive.report_count.sum()),
        'global_unknown_hours': len(global_mask), 'segment_unknown_pairs': len(spatial),
        'unresolved_time_reports': excluded_review,
        'zero_labels_created': 0,
        'zero_policy': 'Only verified coverage hours without global/segment masks may receive zero. Positive labels take precedence.',
        'input_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in inputs},
        'script_sha256': sha256(Path(__file__)),
    }
    with new_artifact(args.output) as out:
        positive.to_parquet(out / 'positive_segment_hours.parquet', index=False)
        spatial.to_parquet(out / 'unknown_segment_hours.parquet', index=False)
        global_mask.to_parquet(out / 'unknown_global_hours.parquet', index=False)
        write_json(out / 'manifest.json', summary)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == '__main__':
    main()
