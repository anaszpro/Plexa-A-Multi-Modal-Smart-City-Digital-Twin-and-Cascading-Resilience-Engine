"""Behavioral regression checks for weighted R² and importance summaries."""

import unittest

import numpy as np
import pandas as pd

try:
    from .boosted_reporting import _importance_table, weighted_r2
except ImportError:
    from boosted_reporting import _importance_table, weighted_r2


class BoostedReportingTests(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame({"sd": ["a", "b"], "segment_id": [1, 2], "volume": [0, 10]})

    def test_mean_perfect_and_worse_predictions_have_expected_interpretation(self):
        self.assertEqual(weighted_r2(self.rows, pd.DataFrame({"p50": [5, 5]})), 0)
        self.assertEqual(weighted_r2(self.rows, pd.DataFrame({"p50": [0, 10]})), 1)
        self.assertLess(weighted_r2(self.rows, pd.DataFrame({"p50": [10, 0]})), 0)

    def test_long_campaign_cannot_increase_its_roads_influence(self):
        repeated = pd.concat([self.rows.iloc[[0]]] * 100 + [self.rows.iloc[[1]]], ignore_index=True)
        score = weighted_r2(self.rows, pd.DataFrame({"p50": [3, 8]}))
        repeated_score = weighted_r2(repeated, pd.DataFrame({"p50": [3] * 100 + [8]}))
        self.assertAlmostEqual(score, repeated_score)
        explicit = np.concatenate([np.full(100, .01), [1]])
        self.assertAlmostEqual(score, weighted_r2(repeated, pd.DataFrame({"p50": [3] * 100 + [8]}), explicit))

    def test_constant_target_policy_stays_finite(self):
        rows = self.rows.assign(volume=0)
        self.assertEqual(weighted_r2(rows, pd.DataFrame({"p50": [0, 0]})), 1)
        self.assertEqual(weighted_r2(rows, pd.DataFrame({"p50": [1, 0]})), 0)
        repeated = pd.concat([self.rows.iloc[[0]]] * 100, ignore_index=True).assign(volume=.1)
        self.assertEqual(weighted_r2(repeated, pd.DataFrame({"p50": [.1] * 100})), 1)
        self.assertEqual(weighted_r2(repeated, pd.DataFrame({"p50": [.2] * 100})), 0)

    def test_invalid_weights_or_misaligned_predictions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "one finite"):
            weighted_r2(self.rows, pd.DataFrame({"p50": [0]}))
        with self.assertRaisesRegex(ValueError, "one finite"):
            weighted_r2(self.rows, pd.DataFrame({"p50": [np.nan, 2]}))
        with self.assertRaisesRegex(ValueError, "sum to one"):
            weighted_r2(self.rows, pd.DataFrame({"p50": [1, 2]}), [1, 2])

    def test_feature_gains_do_not_let_large_training_scenarios_dominate(self):
        importance = pd.DataFrame({"scenario": ["a", "a", "b"], "feature": ["x", "y", "y"],
                                   "gain": [9000, 1000, 10]})
        table, column = _importance_table(importance)
        shares = table.set_index("Feature")["Mean within-scenario share"]
        self.assertEqual(column, "gain")
        self.assertEqual(shares["x"], "45.0%")
        self.assertEqual(shares["y"], "55.0%")


if __name__ == "__main__":
    unittest.main()
