from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from train_climate_pre2024 import availability_mask, longest_dry_run
from climate_history_features import HistoricalReportFeatures
from prepare_climate_pre2024_request import prepare, sha256
from package_climate_pre2024 import normalized_weather
import json
import tempfile
import numpy as np


class Pre2024PolicyTests(unittest.TestCase):
    def test_complete_target_and_delay_required_at_cutoff(self):
        times = pd.Series(pd.to_datetime(["2023-06-24T03:00:00Z", "2023-06-24T04:00:00Z"]))
        mask = availability_mask(times, "2020-10-14T04:00:00Z", "2023-06-24T05:00:00Z", "2023-07-01T04:00:00Z")
        self.assertEqual(mask.tolist(), [True, False])
        mask = availability_mask(times, "2020-10-14T04:00:00Z", "2023-06-24T04:00:00Z", "2023-07-02T04:00:00Z")
        self.assertEqual(mask.tolist(), [True, False])

    def test_new_fixed_history_bounds_exclude_old_and_cutoff_events(self):
        events = pd.DataFrame({"street_segment_id": ["a"]*3, "grid_id": ["g"]*3, "cell_id": [1]*3,
            "prediction_time_utc": pd.to_datetime(["2022-06-24T03:00:00Z", "2022-06-24T04:00:00Z", "2023-06-24T03:00:00Z"])})
        history = HistoricalReportFeatures(events, training_start="2020-10-14T04:00:00Z", training_end="2023-06-24T04:00:00Z")
        rows = pd.DataFrame({"street_segment_id": ["a", "a"], "grid_id": ["g", "g"], "cell_id": [1, 1],
            "prediction_time_utc": pd.to_datetime(["2024-01-01T05:00:00Z", "2025-01-01T04:00:00Z"])})
        actual = history.transform(rows)
        self.assertEqual(actual.segment_positive_hours_365d.tolist(), [2, 2])
        self.assertTrue(actual.history_window_start_utc.eq(pd.Timestamp("2022-06-24T04:00:00Z")).all())
        events.loc[0, "prediction_time_utc"] = pd.Timestamp("2023-06-24T04:00:00Z")
        with self.assertRaises(ValueError):
            HistoricalReportFeatures(events, training_start="2020-10-14T04:00:00Z", training_end="2023-06-24T04:00:00Z")

    def test_incomplete_or_wet_hour_interrupts_dry_break(self):
        frame = pd.DataFrame({"cells": [2537]*8, "valid_cells": [2537]*8, "max_rain": [0.0]*8})
        self.assertEqual(longest_dry_run(frame, 2537), 8)
        frame.loc[3, "valid_cells"] = 2536
        self.assertEqual(longest_dry_run(frame, 2537), 4)
        frame.loc[5, "max_rain"] = 1.0
        self.assertEqual(longest_dry_run(frame, 2537), 3)


class NormalizedRuntimeTests(unittest.TestCase):
    def fixture(self, root):
        inputs, bundle = root / "inputs", root / "model"
        inputs.mkdir(); bundle.mkdir()
        (bundle / "release_manifest.json").write_text(json.dumps({"package_id":"test", "native_format_version":"climate-history-native-0.1"}))
        pd.DataFrame({"mobility_segment_id":["approved"],"street_segment_id":["native"]}).to_csv(inputs/"approved_mobility_crosswalk.csv",index=False)
        pd.DataFrame({"street_segment_id":["native"],"grid_id":["g"],"cell_id":[1],
            "elevation_mean_m":[0.0],"terrain_endpoint_grade_abs":[0.0],"terrain_relief_m":[0.0],"length_m":[10.0]}).to_parquet(inputs/"static_by_road.parquet",index=False)
        pd.DataFrame({"grid_id":["g","g"],"cell_id":[1,1],
            "prediction_time_utc":pd.to_datetime(["2024-01-01T05:00:00Z","2024-03-19T20:00:00Z"]),
            **{f"rain_{h}h_mm":[0.0,np.nan] for h in [1,3,6,24]},
            "rainfall_inputs_valid":[True,False]}).to_parquet(inputs/"rainfall_by_cell_hour.parquet",index=False)
        (inputs/"input_manifest.json").write_text(json.dumps({"package_id":"test",
            "utc_interval":["2024-01-01T05:00:00Z","2025-01-01T05:00:00Z"],
            "model_release_manifest_sha256":sha256(bundle/"release_manifest.json"),
            "assets":{p.name:sha256(p) for p in inputs.iterdir()}}))
        return inputs,bundle

    def test_actual_gap_omits_rainfall_instead_of_zero_filling(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs,bundle=self.fixture(Path(temporary))
            request,context=prepare(inputs,bundle,"2024-01-01T05:00:00Z",["approved"])
            self.assertEqual(len(request["records"][0]["features"]),8)
            request,context=prepare(inputs,bundle,"2024-03-19T20:00:00Z",["approved"])
            self.assertEqual(len(request["records"][0]["features"]),4)
            self.assertEqual(context["records"][0]["input_coverage_status"],"rainfall_inputs_unavailable")

    def test_pending_scope_bad_time_and_tampered_assets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs,bundle=self.fixture(Path(temporary))
            for stamp,ids in [("2024-01-01T05:00:00Z",["pending"]),("2024-01-01T05:30:00Z",["approved"]),
                              ("2025-01-01T05:00:00Z",["approved"]),("2024-01-01T05:00:00Z",["approved","approved"])]:
                with self.assertRaises(ValueError):prepare(inputs,bundle,stamp,ids)
            (inputs/"approved_mobility_crosswalk.csv").write_text("tampered")
            with self.assertRaises(ValueError):prepare(inputs,bundle,"2024-01-01T05:00:00Z")

    def test_normalization_retains_exact_gap_and_rejects_extra_gap(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"weather.parquet"
            hours=pd.date_range("2024-01-01T05:00:00Z","2025-01-01T05:00:00Z",freq="h",inclusive="left")
            gap=(hours>=pd.Timestamp("2024-03-19T20:00:00Z")) & (hours<pd.Timestamp("2024-03-20T22:00:00Z"))
            frame=pd.DataFrame({"grid_id":"g","cell_id":1,"prediction_time_utc":hours,
                **{f"rain_{h}h_mm":np.where(gap,np.nan,0.0) for h in [1,3,6,24]},"weather_features_valid":~gap})
            frame.to_parquet(path,index=False)
            identities=pd.DataFrame({"grid_id":["g"],"cell_id":[1]})
            result=normalized_weather([path],identities)
            self.assertEqual((~result.rainfall_inputs_valid).sum(),26)
            self.assertTrue(result.loc[~result.rainfall_inputs_valid,"rain_1h_mm"].isna().all())
            frame.loc[0,"weather_features_valid"]=False
            frame.to_parquet(path,index=False)
            with self.assertRaises(ValueError):normalized_weather([path],identities)


if __name__ == "__main__":
    unittest.main()
