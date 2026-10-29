"""Behavioral checks for leakage isolation and boosted model inference."""

import copy
import pickle
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from modeling.gradient_boosted import (
    BoostedTrafficModel,
    FeatureEncoder,
    make_training_history,
)
from modeling.mobility_baselines import RoadScaledProfile


def fixture():
    records = []
    for segment in range(1, 9):
        for date in pd.date_range("2020-01-06", periods=6):
            # More than 200 rows lets the production min_child_samples=100
            # produce real splits rather than testing only constant trees.
            for hour in [0, 3, 6, 8, 12, 17, 20, 22]:
                records.append({
                    "segment_id": segment, "sd": f"{segment}|NB", "direction": "NB",
                    "date": date.date(), "hour": hour, "dow": date.dayofweek,
                    "month": date.month, "year": date.year,
                    "is_weekend": date.dayofweek >= 5, "is_holiday": False,
                    "holiday_adjacent": False, "covid": False,
                    "borough": "Queens" if segment % 2 else "Brooklyn",
                    "posted_speed": np.nan if segment == 1 else 25.0,
                    "number_travel_lanes": float(1 + segment % 3),
                    "number_park_lanes": 1.0, "number_total_lanes": float(2 + segment % 3),
                    "streetwidth_min": 30.0, "streetwidth_max": 36.0,
                    "shape_length": float(100 + segment), "lon": -73.9, "lat": 40.7,
                    "trafdir": "TW", "truck_route_type": "0", "snow_priority": "P",
                    "bikelane": None if segment == 2 else "0", "source": "fixture",
                    "volume": segment * 60 * (1 + hour / 24) + date.day,
                })
    rows = pd.DataFrame(records)
    # Nontrivial offsets catch accidental positional joins to saved OOF rows.
    rows.index = np.arange(1000, 1000 + len(rows))
    return rows


def oof_fixture(rows, scenario="within_road"):
    if scenario == "unseen_roads":
        folds = rows.segment_id.to_numpy() % 3
    else:
        folds = np.array([date.day % 3 for date in rows.date])
    return pd.DataFrame({
        "model_row": rows.index,
        "fold": folds,
        "base_log_prediction": np.linspace(2.0, 4.0, len(rows)),
        "observed_volume": rows.volume.to_numpy(),
        "log_residual": np.full(len(rows), -999.0),
    }).iloc[::-1].reset_index(drop=True)


class BoostedBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = fixture()
        cls.history = make_training_history(cls.rows, oof_fixture(cls.rows), "within_road")
        cls.history_model = RoadScaledProfile.fit(cls.rows)
        # The full-fit history model must not provide in-sample training features.
        # The saved OOF arrays supplied by the caller are the only allowed source.
        with patch.object(cls.history_model, "point_log", side_effect=AssertionError("in-sample history used")):
            cls.model = BoostedTrafficModel.fit(
                cls.rows,
                cls.history.history_profile_log.to_numpy(),
                cls.history.history_days.to_numpy(),
                cls.history_model,
                seed=42,
                n_estimators=8,
            )

    def test_oof_alignment_and_history_support_are_fold_specific(self):
        saved = oof_fixture(self.rows)
        history = make_training_history(self.rows, saved, "within_road")
        expected = saved.set_index("model_row").reindex(self.rows.index)
        np.testing.assert_array_equal(history.history_profile_log, expected.base_log_prediction)
        self.assertTrue(history.history_days.eq(4).all())
        self.assertTrue(history.has_history.eq(1).all())
        self.assertEqual(set(history), {"history_profile_log", "history_days", "has_history", "fold"})
        changed = saved.assign(log_residual=10**12)
        pd.testing.assert_frame_equal(history, make_training_history(self.rows, changed, "within_road"))
        cold = make_training_history(self.rows, oof_fixture(self.rows, "unseen_roads"), "unseen_roads")
        self.assertTrue(cold.history_days.eq(0).all())
        self.assertTrue(cold.has_history.eq(0).all())

    def test_encoder_preserves_missing_numeric_and_does_not_learn_test_categories(self):
        encoder = FeatureEncoder.fit(self.rows)
        original_categories = copy.deepcopy(encoder.categories)
        probe = self.rows.iloc[:3].copy()
        probe["borough"] = "new-test-only-borough"
        probe["posted_speed"] = np.nan
        features = encoder.transform(probe, np.ones(3), np.zeros(3))
        self.assertTrue(features.posted_speed.isna().all())
        self.assertTrue(features.borough.isna().all())
        self.assertEqual(encoder.categories, original_categories)
        self.assertNotIn("new-test-only-borough", encoder.categories["borough"])
        missing_category = probe.assign(borough=None)
        encoded_missing = encoder.transform(missing_category, np.ones(3), np.zeros(3))
        self.assertTrue(encoded_missing.borough.notna().all())

    def test_targets_ids_and_full_snapshot_statistics_never_enter_tree_features(self):
        forbidden = [
            "volume", "log_volume", "segment_id", "sd", "model_row", "observed_volume",
            "log_residual", "base_log_prediction", "n_rows_sd", "n_days_sd", "n_years_sd",
            "n_seasons_sd", "w_road", "study_id", "orig_interval_min", "identity_conflict",
            "borough_source", "boro_disagreement", "lion_key_used", "baseline_ok", "road_type",
        ]
        contaminated = self.rows.assign(**{name: 10**9 for name in forbidden})
        clean_features = self.model.encoder.transform(
            self.rows, self.history.history_profile_log, self.history.history_days
        )
        contaminated_features = self.model.encoder.transform(
            contaminated, self.history.history_profile_log, self.history.history_days
        )
        self.assertTrue(set(forbidden).isdisjoint(clean_features.columns))
        pd.testing.assert_frame_equal(clean_features, contaminated_features)

    def test_prediction_ignores_targets_and_handles_unseen_roads(self):
        probe = self.rows.iloc[:7].copy()
        first = self.model.predict(probe)
        probe["volume"] = 10**12
        probe["log_volume"] = -10**12
        probe["n_days_sd"] = 10**12
        pd.testing.assert_frame_equal(first, self.model.predict(probe))

        probe["sd"] = "999|NB"
        probe["segment_id"] = 999
        probe["borough"] = "unseen-borough"
        probe["posted_speed"] = np.nan
        encoded = self.model.features_for_prediction(probe)
        self.assertTrue(encoded.history_days.eq(0).all())
        self.assertTrue(encoded.has_history.eq(0).all())
        predictions = self.model.predict(probe)
        self.assertEqual(list(predictions), ["p10", "p50", "p90"])
        self.assertTrue(np.isfinite(predictions.to_numpy()).all())
        self.assertTrue((predictions.to_numpy() >= 0).all())
        self.assertTrue((np.diff(predictions.to_numpy(), axis=1) >= 0).all())

    def test_pickle_roundtrip_preserves_predictions(self):
        restored = pickle.loads(pickle.dumps(self.model, protocol=pickle.HIGHEST_PROTOCOL))
        pd.testing.assert_frame_equal(self.model.predict(self.rows), restored.predict(self.rows))
        np.testing.assert_array_equal(self.model.raw_log_predictions(self.rows),
                                      restored.raw_log_predictions(self.rows))

    def test_oof_identity_mismatch_is_rejected(self):
        saved = oof_fixture(self.rows)
        saved.loc[0, "observed_volume"] += 1
        with self.assertRaisesRegex(ValueError, "do not match"):
            make_training_history(self.rows, saved, "within_road")


if __name__ == "__main__":
    unittest.main()
