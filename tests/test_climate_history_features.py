import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from climate_history_features import HistoricalReportFeatures


class HistoryTests(unittest.TestCase):
    def events(self):
        return pd.DataFrame({
            "street_segment_id": ["a", "a", "a", "a", "b", "c"],
            "grid_id": ["g", "g", "g", "g", "g", "other"],
            "cell_id": [1, 1, 1, 1, 1, 1],
            "prediction_time_utc": pd.to_datetime([
                "2023-12-25T00:00:00Z", "2023-12-26T00:00:00Z",
                "2024-12-24T00:00:00Z", "2024-12-25T00:00:00Z",
                "2024-12-24T00:00:00Z", "2024-12-24T00:00:00Z"], utc=True),
        })

    def builder(self, events=None):
        return HistoricalReportFeatures(
            self.events() if events is None else events,
            training_start="2020-01-01T00:00:00Z",
            training_end="2025-01-01T00:00:00Z")

    def rows(self, dates, street="a", grid="g", cell=1):
        return pd.DataFrame({"street_segment_id": street, "grid_id": grid,
                             "cell_id": cell, "prediction_time_utc": pd.to_datetime(dates, utc=True)})

    def test_half_open_window_delay_and_grid_identity(self):
        # Jan 1 minus 7 days = Dec 25. 365-day lower bound is Dec 26, 2023.
        result = self.builder().transform(self.rows(["2025-01-01T00:00:00Z"]))
        self.assertEqual(result.segment_positive_hours_365d.iloc[0], 2)
        self.assertEqual(result.cell_positive_hours_365d.iloc[0], 3)

    def test_validation_freezes_at_training_end(self):
        result = self.builder().transform(self.rows([
            "2025-02-01T00:00:00Z", "2025-10-01T00:00:00Z"]))
        self.assertEqual(result.segment_positive_hours_365d.tolist(), [2, 2])
        self.assertTrue((result.history_cutoff_utc == pd.Timestamp("2025-01-01T00:00:00Z")).all())

    def test_cold_start_and_unknown_street(self):
        result = self.builder().transform(self.rows(["2020-01-01T00:00:00Z"]))
        self.assertEqual(result.segment_positive_hours_365d.iloc[0], 0)
        self.assertEqual(result.history_window_snapshot_span_days.iloc[0], 0)
        result = self.builder().transform(self.rows(["2025-02-01T00:00:00Z"], street="new"))
        self.assertEqual(result.segment_positive_hours_365d.iloc[0], 0)
        self.assertEqual(result.cell_positive_hours_365d.iloc[0], 3)

    def test_rejects_duplicate_future_and_naive_events(self):
        events = self.events()
        with self.assertRaises(ValueError):
            self.builder(pd.concat([events, events.iloc[[0]]]))
        events.loc[0, "prediction_time_utc"] = pd.Timestamp("2025-01-02T00:00:00Z")
        with self.assertRaises(ValueError):
            self.builder(events)
        events = self.events()
        events["prediction_time_utc"] = events.prediction_time_utc.dt.tz_localize(None)
        with self.assertRaises(ValueError):
            self.builder(events)

    def test_order_index_and_segment_cell_consistency(self):
        rows = self.rows(["2025-02-01T00:00:00Z", "2020-01-01T00:00:00Z"])
        rows.index = [99, 4]
        result = self.builder().transform(rows)
        self.assertEqual(result.index.tolist(), [99, 4])
        self.assertEqual(result.segment_positive_hours_365d.tolist(), [2, 0])
        with self.assertRaises(ValueError):
            self.builder().transform(self.rows(["2025-02-01T00:00:00Z"], cell=2))


if __name__ == "__main__":
    unittest.main()
