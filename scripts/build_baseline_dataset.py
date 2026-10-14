"""Create a disk-backed, lazy dataset with explicit eligibility and split rules.

No model is fitted. Full validation/test rows are streamed by date when needed;
only training negatives are sampled. DuckDB views refer to immutable Parquet files.
"""
import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

from climate_artifacts import ROOT, new_artifact, sha256, write_json


def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def labeled_sql():
    return """
    SELECT s.street_segment_id, w.prediction_time_utc,
           s.elevation_mean_m, s.terrain_endpoint_grade_abs, s.terrain_relief_m, s.length_m,
           w.rain_1h_mm, w.rain_3h_mm, w.rain_6h_mm, w.rain_24h_mm,
           CASE WHEN p.label = 1 THEN 1
                WHEN c.prediction_time_utc IS NOT NULL
                 AND g.prediction_time_utc IS NULL AND u.street_segment_id IS NULL THEN 0
                ELSE NULL END::SMALLINT AS label,
           coalesce(p.report_count, 0) AS report_count,
           c.prediction_time_utc IS NOT NULL AS source_coverage_verified
    FROM street_features s JOIN weather w ON s.cell_id = w.cell_id AND s.grid_id = w.grid_id
    LEFT JOIN positive p USING(street_segment_id, prediction_time_utc)
    LEFT JOIN global_unknown g ON g.prediction_time_utc = w.prediction_time_utc
    LEFT JOIN segment_unknown u ON u.street_segment_id = s.street_segment_id
                               AND u.prediction_time_utc = w.prediction_time_utc
    LEFT JOIN coverage c ON c.prediction_time_utc = w.prediction_time_utc
    WHERE s.static_features_valid AND w.weather_features_valid
    """


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weather-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    weather_files = sorted(args.weather_dir.rglob('weather_features.parquet'))
    if not weather_files:
        raise ValueError('No prepared rainfall features.')
    processed = ROOT / 'data/processed'
    config_path = ROOT / 'config/climate_preparation.json'
    config = json.loads(config_path.read_text())
    target_path = ROOT / 'config/climate_baseline_target.json'
    labels = processed / 'hourly_report_labels_v1'
    label_meta = json.loads((labels / 'manifest.json').read_text())
    if label_meta['input_sha256']['config/climate_baseline_target.json'] != sha256(target_path):
        raise ValueError('Target changed after label generation.')
    snapshot = processed / 'complaints_snapshot_v1'
    fingerprint = {n: sha256(snapshot / n) for n in ['normal.parquet', 'time_review.json', 'manifest.json']}
    coverage_hours = set()
    coverage_files = sorted((processed / 'complaint_coverage_v1').glob('*_*.json'))
    for path in coverage_files:
        item = json.loads(path.read_text())
        if item['snapshot_sha256'] != fingerprint:
            raise ValueError('Coverage check refers to another snapshot.')
        if item['status'] == 'verified':
            coverage_hours.update(pd.date_range(item['start_utc'], item['end_utc_exclusive'],
                                               freq='h', inclusive='left'))
    coverage = pd.DataFrame({'prediction_time_utc': pd.DatetimeIndex(sorted(coverage_hours), tz='UTC')})
    input_paths = [config_path, target_path, labels / 'manifest.json',
                   processed / 'baseline_street_features_v1/manifest.json', *coverage_files]
    for path in weather_files:
        metadata = json.loads(path.with_name('manifest.json').read_text())
        if metadata['preparation_config'] != config:
            raise ValueError('Weather was built with different preparation settings.')
        input_paths.extend([path, path.with_name('manifest.json')])
    tables = {
        'street_features': processed / 'baseline_street_features_v1/street_features.parquet',
        'positive': labels / 'positive_segment_hours.parquet',
        'global_unknown': labels / 'unknown_global_hours.parquet',
        'segment_unknown': labels / 'unknown_segment_hours.parquet',
    }
    input_paths.extend(tables.values())
    with new_artifact(args.output) as out:
        con = duckdb.connect(str(out / 'dataset.duckdb'))
        try:
            con.execute('SET threads=2')
            con.execute("SET memory_limit='2GB'")
            con.execute("SET TimeZone='UTC'")
            con.register('coverage_input', coverage)
            con.execute('CREATE TABLE coverage AS SELECT * FROM coverage_input')
            for name, path in tables.items():
                con.execute(f'CREATE VIEW {name} AS SELECT * FROM read_parquet({sql_string(path.resolve())})')
            files_sql = '[' + ','.join(sql_string(p.resolve()) for p in weather_files) + ']'
            con.execute(f'CREATE VIEW weather AS SELECT * FROM read_parquet({files_sql})')
            duplicates = con.execute('''SELECT count(*) FROM (SELECT cell_id, prediction_time_utc
                FROM weather GROUP BY ALL HAVING count(*) > 1)''').fetchone()[0]
            if duplicates:
                raise ValueError('Weather partitions overlap or contain duplicate cell/hours.')
            if con.execute('SELECT count(DISTINCT grid_id) FROM weather').fetchone()[0] != 1:
                raise ValueError('Mixed radar grids.')
            if con.execute('''SELECT count(*) FROM street_features WHERE grid_id NOT IN
                               (SELECT DISTINCT grid_id FROM weather)''').fetchone()[0]:
                raise ValueError('Street and weather grids differ.')
            for table, keys in [('street_features', 'street_segment_id'),
                                ('positive', 'street_segment_id, prediction_time_utc'),
                                ('segment_unknown', 'street_segment_id, prediction_time_utc'),
                                ('global_unknown', 'prediction_time_utc')]:
                if con.execute(f'SELECT count(*) FROM (SELECT {keys} FROM {table} GROUP BY ALL HAVING count(*)>1)').fetchone()[0]:
                    raise ValueError(f'Duplicate keys in {table}.')
            con.execute('CREATE VIEW labeled AS ' + labeled_sql())
            cases = ' '.join(f"WHEN prediction_time_utc >= TIMESTAMPTZ {sql_string(a)} "
                f"AND prediction_time_utc < TIMESTAMPTZ {sql_string(b)} THEN {sql_string(split)}"
                for split, (a, b) in config['splits'].items())
            con.execute(f'CREATE VIEW eligible AS SELECT *, CASE {cases} ELSE NULL END AS split '
                        'FROM labeled WHERE label IS NOT NULL AND source_coverage_verified')
            fraction = config['training_negative_keep_fraction']
            if not 0 < fraction <= 1:
                raise ValueError('Invalid negative sampling fraction.')
            denominator = round(1 / fraction)
            if abs(1 / denominator - fraction) > 1e-12:
                raise ValueError('Sampling fraction must be the reciprocal of an integer.')
            seed = sql_string(config['training_sampling_seed'])
            con.execute(f'''CREATE VIEW training_sample AS SELECT *,
                CASE WHEN label=1 THEN 1.0 ELSE {denominator}.0 END AS sample_weight
                FROM eligible WHERE split='train' AND (label=1 OR
                md5_number_lower(street_segment_id || '|' || epoch(prediction_time_utc)::VARCHAR || {seed})
                % {denominator} = 0)''')
            for split in ['validation', 'test']:
                con.execute(f"CREATE VIEW {split}_full AS SELECT * FROM eligible WHERE split='{split}'")
            size = con.execute('''SELECT min(prediction_time_utc)::VARCHAR, max(prediction_time_utc)::VARCHAR,
                count(*), count(*) FILTER (WHERE weather_features_valid) FROM weather''').fetchone()
            con.execute('CHECKPOINT')
        finally:
            con.close()
        summary = {
            'status': 'PREPARED_NOT_TRAINING_APPROVED', 'training_performed': False,
            'weather_first_prediction': size[0], 'weather_last_prediction': size[1],
            'weather_cell_hours': size[2], 'valid_weather_cell_hours': size[3],
            'verified_complaint_hours': len(coverage),
            'views': ['labeled', 'eligible', 'training_sample', 'validation_full', 'test_full'],
            'input_sha256': {str(p.resolve()): sha256(p) for p in input_paths},
            'required_before_training': ['Full historical rainfall build and gap report',
                'Split class counts and weather-event boundary review',
                'Review uncertainty and feature exclusions by split and borough',
                'Freeze dataset and authorize training'],
            'configuration': config,
        }
        write_json(out / 'manifest.json', summary)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == '__main__':
    main()
