"""Behavioural checks for sparse roads, cold start, fold isolation and serialization."""

import unittest
import numpy as np
import pandas as pd

from modeling.mobility_baselines import GlobalProfile, RoadScaledProfile, calibration_folds


def fixture():
    records = []
    for segment, scale in [(1, 80), (2, 400), (3, 900)]:
        for direction in ["NB", "SB"]:
            for day in pd.date_range("2020-01-01", periods=6):
                for hour in [3, 8, 17, 22]:
                    records.append({"segment_id": segment, "sd": f"{segment}|{direction}",
                                    "date": day.date(), "hour": hour, "dow": day.dayofweek,
                                    "borough": "Queens", "number_travel_lanes": 2.0,
                                    "volume": scale * (1 + hour / 24) * (1 if direction == "NB" else .8)})
    return pd.DataFrame(records)


class BaselineBehaviorTests(unittest.TestCase):
    def test_global_profile_does_not_overweight_a_repeated_road(self):
        data = fixture()
        repeated = pd.concat([data, data.loc[data.sd.eq("1|NB")]] * 2, ignore_index=True)
        a, b = GlobalProfile.fit(data), GlobalProfile.fit(repeated)
        np.testing.assert_allclose(a.predict(data), b.predict(data), rtol=1e-12)

    def test_cold_start_and_prediction_ignore_validation_targets(self):
        data = fixture()
        model = RoadScaledProfile.fit(data)
        model.calibrate(data, model.point_log(data))
        unseen = data.iloc[:5].copy()
        unseen["sd"] = "999|NB"
        unseen["segment_id"] = 999
        first = model.predict(unseen)
        unseen["volume"] = 10**9
        second = model.predict(unseen)
        pd.testing.assert_frame_equal(first, second)
        np.testing.assert_allclose(model.point_log(unseen), model.group_point_log(unseen))

    def test_sparse_slot_fallback_and_ordered_nonnegative_intervals(self):
        data = fixture()
        model = GlobalProfile.fit(data)
        probe = data.iloc[:2].copy()
        probe["dow"] = 6  # no Sunday in this six-day fixture
        probe["hour"] = [0, 12]  # no observations for these hours either
        predicted = model.predict(probe)
        self.assertTrue(np.isfinite(predicted.to_numpy()).all())
        self.assertTrue((predicted.to_numpy() >= 0).all())
        self.assertTrue((np.diff(predicted.to_numpy(), axis=1) >= 0).all())

    def test_grouped_calibration_preserves_or_withholds_road_history(self):
        data = fixture()
        for scenario in ["within_road", "unseen_roads"]:
            folds = calibration_folds(data, scenario)
            self.assertEqual(set(folds), {0, 1, 2})
            assignments = data.assign(fold=folds)
            self.assertTrue(assignments.groupby(["segment_id", "date"]).fold.nunique().eq(1).all())
            for fold in range(3):
                fit, held = data.loc[folds != fold], data.loc[folds == fold]
                if scenario == "unseen_roads":
                    self.assertTrue(set(fit.segment_id).isdisjoint(set(held.segment_id)))
                else:
                    self.assertTrue(set(held.sd).issubset(set(fit.sd)))


if __name__ == "__main__":
    unittest.main()
