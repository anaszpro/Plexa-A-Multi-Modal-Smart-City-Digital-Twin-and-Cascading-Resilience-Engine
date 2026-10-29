"""Behavioral checks for reporting weights, intervals and segment uncertainty."""

import unittest

import numpy as np
import pandas as pd

try:
    from .baseline_evaluation import (
        evaluate_predictions, paired_mae_bootstrap, per_road_metrics, subgroup_metrics,
    )
except ImportError:
    # Also support unittest discovery with modeling/ as the starting directory.
    from baseline_evaluation import (
        evaluate_predictions, paired_mae_bootstrap, per_road_metrics, subgroup_metrics,
    )


def predicted(medians, lower=None, upper=None):
    medians = np.asarray(medians, dtype=float)
    return pd.DataFrame({"p10": medians if lower is None else lower,
                         "p50": medians,
                         "p90": medians if upper is None else upper})


class BaselineEvaluationTests(unittest.TestCase):
    def test_repeating_a_roads_campaign_does_not_change_its_influence(self):
        short = pd.DataFrame({"sd": ["a", "b"], "segment_id": [1, 2], "volume": [0, 100]})
        long = pd.concat([short.iloc[[0]]] * 100 + [short.iloc[[1]]], ignore_index=True)
        first = evaluate_predictions(short, predicted([10, 0]))
        repeated = evaluate_predictions(long, predicted([10] * 100 + [0]))
        self.assertEqual(first["mae"], 55)
        for metric in ["mae", "rmse", "log1p_mae", "weighted_interval_score", "coverage_80"]:
            self.assertAlmostEqual(first[metric], repeated[metric])
        roads = per_road_metrics(long, predicted([10] * 100 + [0]))
        self.assertAlmostEqual(first["mae"], roads.mae.mean())
        # Root of the overall mean squared error is not mean road RMSE.
        self.assertGreater(first["rmse"], roads.rmse.mean())

    def test_subgroup_rebalances_roads_with_different_source_coverage(self):
        rows = pd.DataFrame({"sd": ["a"] * 10 + ["b"], "segment_id": [1] * 10 + [2],
                             "volume": [0] * 11, "source": ["auto"] + ["hist"] * 9 + ["auto"]})
        summary = subgroup_metrics(rows, predicted([0] + [100] * 10), ["source"])
        self.assertEqual(summary.set_index("group_value").loc["auto", "mae"], 50)
        self.assertEqual(summary.set_index("group_value").loc["hist", "mae"], 100)

    def test_zero_volumes_are_finite_and_boundaries_are_covered(self):
        rows = pd.DataFrame({"sd": ["a", "a"], "segment_id": [1, 1], "volume": [0, 10]})
        exact = evaluate_predictions(rows, predicted([0, 10]))
        for key in ["mae", "rmse", "log1p_mae", "log1p_rmse", "weighted_interval_score"]:
            self.assertEqual(exact[key], 0)
        self.assertEqual(exact["coverage_80"], 1)
        narrow = evaluate_predictions(rows, predicted([0, 10], [0, 10], [1, 11]))
        broad = evaluate_predictions(rows, predicted([0, 10], [0, 0], [100, 100]))
        self.assertEqual(narrow["coverage_80"], broad["coverage_80"])
        self.assertLess(narrow["weighted_interval_score"], broad["weighted_interval_score"])
        missed = evaluate_predictions(rows, predicted([2, 12], [1, 11], [3, 13]))
        self.assertEqual(missed["coverage_80"], 0)
        self.assertGreater(missed["interval_score_80"], missed["mean_interval_width"])

    def test_segment_bootstrap_keeps_opposing_direction_errors_together(self):
        rows = pd.DataFrame({"sd": ["1|N", "1|S", "2|N"], "segment_id": [1, 1, 2],
                             "volume": [10, 10, 10]})
        b1, b2 = predicted([20, 10, 10]), predicted([10, 20, 10])
        first = paired_mae_bootstrap(rows, b1, b2, n_bootstrap=100, seed=42)
        second = paired_mae_bootstrap(rows, b1, b2, n_bootstrap=100, seed=42)
        self.assertEqual(first, second)
        self.assertEqual(first["mae_improvement_b1_minus_b2"], 0)
        self.assertEqual(first["ci95_lower"], 0)
        self.assertEqual(first["ci95_upper"], 0)
        # Resampling the two directions independently would produce a spread.
        positive = paired_mae_bootstrap(rows, predicted([30, 30, 30]), predicted([15, 15, 15]),
                                       n_bootstrap=100)
        self.assertEqual(positive["mae_improvement_b1_minus_b2"], 15)
        self.assertEqual(positive["ci95_lower"], 15)
        self.assertEqual(positive["ci95_upper"], 15)

    def test_weight_and_quantile_contracts_reject_silent_evaluation_errors(self):
        rows = pd.DataFrame({"sd": ["a", "a", "b"], "segment_id": [1, 1, 2], "volume": [0, 1, 2]})
        with self.assertRaisesRegex(ValueError, "sum to one"):
            evaluate_predictions(rows, predicted([0, 1, 2]), weights=[1, 1, 1])
        with self.assertRaisesRegex(ValueError, "cross"):
            evaluate_predictions(rows, predicted([0, 1, 2], [0, 2, 2], [0, 1, 2]))
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            evaluate_predictions(rows, predicted([-1, 1, 2]))


if __name__ == "__main__":
    unittest.main()
