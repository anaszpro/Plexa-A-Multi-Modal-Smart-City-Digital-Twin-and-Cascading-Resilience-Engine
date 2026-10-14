"""Scientific boundary cases, independent of the database and network."""
import sys
import unittest
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_baseline_weather import rolling_features, build
from build_hourly_report_labels import make_labels
from build_baseline_dataset import labeled_sql
import duckdb


class WeatherTests(unittest.TestCase):
    def test_product_lag_prevents_future_rainfall_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            times = pd.date_range('2021-01-01', periods=28, freq='h', tz='UTC')
            for i, timestamp in enumerate(times):
                frame = pd.DataFrame({
                    'ts_utc': pd.DatetimeIndex([timestamp] * 4),
                    'cell_id': np.arange(4),
                    'latitude': [40., 40., 41., 41.],
                    'longitude': [-74., -73., -74., -73.],
                    'rain_mm': np.full(4, 1. if i <= 23 else 999.),
                    'is_missing': np.zeros(4, dtype=bool), 'grid_id': 'fixture',
                })
                frame.to_parquet(root / f'radar_{timestamp:%Y%m%d_%H%M%S}.parquet', index=False)
            config = {'rainfall_availability_lag_hours': 3, 'rainfall_windows_hours': [1, 24]}
            start = pd.Timestamp('2021-01-02T02:00Z')
            build(start, start + pd.Timedelta(hours=1), root,
                  root / 'radar_20210101_000000.parquet', root / 'out', config)
            output = pd.read_parquet(root / 'out/weather_features.parquet')
            self.assertTrue(output.rain_1h_mm.eq(1).all())
            self.assertTrue(output.rain_24h_mm.eq(24).all())
            self.assertTrue(output.weather_features_valid.all())

    def test_windows_count_hours_not_wet_rows(self):
        data = np.array([[1.], [0.], [2.], [0.]])
        result = rolling_features(data, [1, 3])
        self.assertEqual(result['rain_3h_mm'][2, 0], 3)
        self.assertEqual(result['rain_3h_mm'][3, 0], 2)
        self.assertEqual(result['rain_1h_mm'][3, 0], 0)

    def test_gap_is_not_dry_and_cannot_be_skipped(self):
        data = np.array([[1.], [np.nan], [2.], [3.], [4.]])
        values = rolling_features(data, [3])['rain_3h_mm'][:, 0]
        self.assertTrue(np.isnan(values[:4]).all())
        self.assertEqual(values[4], 9)


class LabelTests(unittest.TestCase):
    def inputs(self):
        audit = pd.DataFrame({
            'unique_key': [1, 2, 3, 4],
            'created_at': pd.to_datetime(['2021-11-07T05:00Z', '2021-11-07T05:59Z',
                                         '2021-11-07T06:00Z', '2021-11-07T06:30Z']),
            'match_status': ['matched_unique_segment', 'matched_unique_segment',
                             'ambiguous_segment', 'missing_or_invalid_coordinates'],
        })
        candidates = pd.DataFrame({'unique_key': [3, 3], 'street_segment_id': ['A', 'B']})
        matched = audit.iloc[:2].assign(street_segment_id='A')
        rules = {'positive_report_rules': [{'complaint_type': 'Sewer', 'descriptor': 'Street Flooding (SJ)'}]}
        return audit, candidates, matched, rules

    def test_dedup_hour_and_boundary_and_ties(self):
        audit, candidates, matched, rules = self.inputs()
        positive, spatial, global_mask, _ = make_labels(audit, candidates, matched, [], rules)
        self.assertEqual(len(positive), 1)
        self.assertEqual(positive.report_count.iloc[0], 2)
        self.assertEqual(positive.prediction_time_utc.iloc[0], pd.Timestamp('2021-11-07T05:00Z'))
        self.assertEqual(set(spatial.street_segment_id), {'A', 'B'})
        self.assertEqual(global_mask.prediction_time_utc.iloc[0], pd.Timestamp('2021-11-07T06:00Z'))

    def test_dst_review_masks_25_real_hours_and_keeps_positives(self):
        audit, candidates, matched, rules = self.inputs()
        review = [{'created_date_local': '2021-11-07T01:59:00', 'source_record': rules['positive_report_rules'][0]}]
        positive, _, global_mask, count = make_labels(audit, candidates, matched, review, rules)
        self.assertEqual(count, 1)
        self.assertEqual(len(global_mask), 25)
        self.assertEqual(len(positive), 1)


class DatasetTests(unittest.TestCase):
    def test_unknown_masks_and_positive_precedence(self):
        con = duckdb.connect()
        try:
            con.execute('''CREATE TABLE street_features AS SELECT segment AS street_segment_id,
                1 AS cell_id, 'g' AS grid_id, 2.0 AS elevation_mean_m,
                0.1 AS terrain_endpoint_grade_abs, 1.0 AS terrain_relief_m,
                100.0 AS length_m, true AS static_features_valid FROM
                (VALUES ('A'),('B')) t(segment)''')
            con.execute('''CREATE TABLE weather AS SELECT ts AS prediction_time_utc,
                1 AS cell_id, 'g' AS grid_id, 1.0 AS rain_1h_mm, 3.0 AS rain_3h_mm,
                6.0 AS rain_6h_mm, 24.0 AS rain_24h_mm, true AS weather_features_valid
                FROM generate_series(TIMESTAMPTZ '2021-01-01 00:00:00+00',
                TIMESTAMPTZ '2021-01-01 02:00:00+00', INTERVAL 1 HOUR) t(ts)''')
            con.execute('''CREATE TABLE positive AS SELECT 'A' AS street_segment_id,
                min(prediction_time_utc) AS prediction_time_utc, 1 AS label, 2 AS report_count FROM weather''')
            con.execute('CREATE TABLE global_unknown AS SELECT min(prediction_time_utc) AS prediction_time_utc FROM weather')
            con.execute('CREATE TABLE segment_unknown AS SELECT street_segment_id, prediction_time_utc FROM positive WHERE false')
            con.execute('''CREATE TABLE coverage AS SELECT prediction_time_utc FROM weather
                WHERE prediction_time_utc < TIMESTAMPTZ '2021-01-01 02:00:00+00' ''')
            con.execute('CREATE VIEW result AS ' + labeled_sql())
            counts = dict(con.execute('SELECT label,count(*) FROM result GROUP BY label').fetchall())
            self.assertEqual(counts, {1: 1, 0: 2, None: 3})
        finally:
            con.close()


if __name__ == '__main__':
    unittest.main()
