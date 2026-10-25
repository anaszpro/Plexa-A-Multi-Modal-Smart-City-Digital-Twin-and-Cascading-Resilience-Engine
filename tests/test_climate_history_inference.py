import copy
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from climate_history_features import HistoricalReportFeatures
from predict_climate_history import ClimateHistoryPredictor, FORMAT


class DiagnosticEstimator:
    def predict_proba(self, inputs):
        self.last_inputs = inputs.copy()
        p = np.full(len(inputs), 0.01)
        return np.column_stack([1-p, p])


class HistoryInferenceTests(unittest.TestCase):
    def setUp(self):
        self.predictor = ClimateHistoryPredictor.__new__(ClimateHistoryPredictor)
        self.predictor.base_features = ["elevation_mean_m", "terrain_endpoint_grade_abs",
            "terrain_relief_m", "length_m", "rain_1h_mm", "rain_3h_mm", "rain_6h_mm", "rain_24h_mm"]
        self.predictor.manifest = {"package_id": "test-package", "supported_snapshot_interval":
            ["2020-01-01T00:00:00Z", "2026-01-01T00:00:00Z"], "alert_threshold": 0.02,
            "target": "reported_street_or_highway_flooding_next_hour"}
        self.predictor.experiment = {"training_interval": ["2020-01-01T00:00:00Z", "2025-01-01T00:00:00Z"],
            "features": self.predictor.base_features + ["segment_positive_hours_365d", "cell_positive_hours_365d"]}
        self.predictor.bridge = pd.DataFrame({"street_segment_id": ["a"], "grid_id": ["g"], "cell_id": [1]}).set_index("street_segment_id")
        events = pd.DataFrame({"street_segment_id": ["a", "a"], "grid_id": ["g", "g"], "cell_id": [1, 1],
            "prediction_time_utc": pd.to_datetime(["2021-01-01T00:00:00Z", "2022-01-01T00:00:00Z"], utc=True)})
        self.predictor.history = HistoricalReportFeatures(events,
            training_start="2020-01-01T00:00:00Z", training_end="2025-01-01T00:00:00Z")
        self.predictor.model = DiagnosticEstimator()
        self.request = {"format_version": FORMAT, "package_id": "test-package", "records": [{
            "request_id": "001", "street_segment_id": "a", "prediction_time_utc": "2021-01-10T00:00:00Z",
            "features": dict(zip(self.predictor.base_features, [-2.0, 0.01, 1.0, 100.0, 1.0, 2.0, 3.0, 4.0]))}]}

    def test_history_is_computed_internally_and_future_event_excluded(self):
        output = self.predictor.predict(self.request, "hash")
        record = output["records"][0]
        self.assertEqual(record["status"], "ok")
        self.assertEqual(output["request_sha256"], "hash")
        self.assertEqual(len(self.predictor.model.last_inputs.columns), 10)
        self.assertEqual(self.predictor.model.last_inputs.segment_positive_hours_365d.iloc[0], 1)
        self.assertEqual(record["result"]["latest_rainfall_end_utc"], "2021-01-09T21:00:00Z")
        self.assertEqual(record["result"]["target_end_utc"], "2021-01-10T01:00:00Z")
        self.assertFalse(record["result"]["model_alert"])
        self.assertIsNone(record["result"]["water_depth_m"])
        self.assertIn("TRAINING_PERIOD_REPLAY", record["warnings"])

    def test_per_record_errors_preserve_success_and_ids(self):
        invalid = copy.deepcopy(self.request["records"][0])
        invalid["request_id"] = "bad"
        del invalid["features"]["rain_1h_mm"]
        self.request["records"].append(invalid)
        result = self.predictor.predict(self.request)["records"]
        self.assertEqual([r["request_id"] for r in result], ["001", "bad"])
        self.assertEqual(result[0]["status"], "ok")
        self.assertEqual(result[1]["reason_code"], "MISSING_REQUIRED_FEATURE")
        self.assertIsNone(result[1]["result"])

    def test_duplicate_ids_reject_batch(self):
        self.request["records"].append(copy.deepcopy(self.request["records"][0]))
        with self.assertRaises(ValueError):
            self.predictor.predict(self.request)

    def test_rejects_invalid_numbers_and_nonmonotonic_rain(self):
        for value in [True, "2", None, float("nan"), float("inf"), 10**1000]:
            request = copy.deepcopy(self.request)
            request["records"][0]["features"]["rain_1h_mm"] = value
            output = self.predictor.predict(request)["records"][0]
            self.assertEqual(output["status"], "error")
            self.assertIsNone(output["result"])
        request = copy.deepcopy(self.request)
        request["records"][0]["features"]["rain_1h_mm"] = 8.0
        self.assertEqual(self.predictor.predict(request)["records"][0]["status"], "error")

    def test_unknown_street_naive_time_and_extra_history_rejected(self):
        for key, value in [("street_segment_id", "missing"), ("prediction_time_utc", "2021-01-10T00:00:00")]:
            request = copy.deepcopy(self.request)
            request["records"][0][key] = value
            self.assertEqual(self.predictor.predict(request)["records"][0]["status"], "error")
        request = copy.deepcopy(self.request)
        request["records"][0]["features"]["segment_positive_hours_365d"] = 999
        self.assertEqual(self.predictor.predict(request)["records"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
