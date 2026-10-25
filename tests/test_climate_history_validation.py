import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_climate_history import (best_threshold, curve_from_bins,
    json_text, metrics_at_threshold, score_bins, utc_windows,
    verify_saved_window, write_once)


class HistoryValidationTests(unittest.TestCase):
    def test_grouped_ap_matches_quantized_sklearn_and_threshold_counts(self):
        edges = np.array([0.0, 0.1, 0.5, 1.0])
        scores = np.array([0.0, 0.09, 0.1, 0.15, 0.6, 1.0])
        labels = np.array([0, 1, 1, 0, 1, 0])
        bins = score_bins(scores, edges)
        pos = np.bincount(bins[labels == 1], minlength=len(edges))
        neg = np.bincount(bins[labels == 0], minlength=len(edges))
        _, ap = curve_from_bins(pos, neg, edges)
        self.assertAlmostEqual(ap, average_precision_score(labels, edges[bins]))
        for threshold in edges:
            result = metrics_at_threshold(pos, neg, edges, threshold)
            alert = scores >= threshold
            self.assertEqual(result["TP"], int((alert & (labels == 1)).sum()))
            self.assertEqual(result["FP"], int((alert & (labels == 0)).sum()))

    def test_no_positives_and_tie_break(self):
        edges = np.array([0.0, 0.5, 1.0])
        curve, ap = curve_from_bins(np.zeros(3, dtype=int), np.array([4, 0, 0]), edges)
        self.assertIsNone(ap)
        self.assertEqual(best_threshold(curve).threshold, 1.0)
        result = metrics_at_threshold(np.zeros(3, dtype=int), np.array([4, 0, 0]), edges, 1.0)
        self.assertIsNone(result["recall"])
        self.assertEqual(result["precision"], 0)

    def test_rejects_invalid_scores_and_counts(self):
        edges = np.array([0.0, 1.0])
        for bad in [[np.nan], [np.inf], [-0.1], [1.1]]:
            with self.assertRaises(ValueError):
                score_bins(bad, edges)
        with self.assertRaises(ValueError):
            curve_from_bins(np.array([-1, 2]), np.array([1, 1]), edges)

    def test_calendar_windows_preserve_partial_edges(self):
        start, end = pd.Timestamp("2025-01-01T05:00:00Z"), pd.Timestamp("2025-01-03T04:00:00Z")
        windows = list(utc_windows(start, end))
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0][0], start)
        self.assertEqual(windows[-1][1], end)
        self.assertEqual(sum((b-a).total_seconds() for a, b in windows), (end-start).total_seconds())

    def test_resume_provenance_counts_and_no_overwrite(self):
        start, end = pd.Timestamp("2025-01-01T05:00:00Z"), pd.Timestamp("2025-01-02T00:00:00Z")
        edges = np.array([0.0, 1.0])
        metadata = {"evaluation_plan_sha256": "a", "start_utc": start.isoformat(),
                    "end_utc_exclusive": end.isoformat(), "positive_rows": 1,
                    "negative_rows": 2, "rows": 3}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "window.npz"
            np.savez_compressed(path, positive=np.array([[1, 0]]), negative=np.array([[2, 0]]),
                score_edges=edges, boroughs=np.array(["Queens"]), metadata_json=json_text(metadata))
            pos, neg, _ = verify_saved_window(path, "a", start, end, edges, ["Queens"])
            self.assertEqual(int(pos.sum()+neg.sum()), 3)
            with self.assertRaises(ValueError):
                verify_saved_window(path, "changed", start, end, edges, ["Queens"])
            text_path = Path(directory) / "plan.json"
            write_once(text_path, json_text(metadata))
            write_once(text_path, json_text(metadata))
            with self.assertRaises(ValueError):
                write_once(text_path, json.dumps({}))


if __name__ == "__main__":
    unittest.main()
