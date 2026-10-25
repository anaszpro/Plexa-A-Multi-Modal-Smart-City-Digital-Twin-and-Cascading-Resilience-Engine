from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_ida_climate_candidate import BOROUGHS, START, choose_roads


class IdaCandidateTests(unittest.TestCase):
    def fixtures(self):
        roads, weather = [], []
        hours = pd.date_range(START, periods=48, freq="h")
        for borough_index, borough in enumerate(BOROUGHS):
            for road in range(3):
                cell = borough_index*3 + road
                segment = f"{borough_index}-{road}"
                roads.append({"street_segment_id": segment, "borough_at_midpoint": borough,
                    "static_features_valid": True, "grid_id": "g", "cell_id": cell})
                for stamp in hours:
                    weather.append({"grid_id": "g", "cell_id": cell,
                                    "prediction_time_utc": stamp, "weather_features_valid": True})
        return pd.DataFrame(roads), pd.DataFrame(weather)

    def test_selection_deterministic_and_uses_coverage_not_scores(self):
        roads, weather = self.fixtures()
        chosen = choose_roads(roads, weather, roads.street_segment_id, 2)
        shuffled = roads.sample(frac=1, random_state=42).assign(score=1.0, label=1)
        other = choose_roads(shuffled, weather.sample(frac=1, random_state=42), roads.street_segment_id, 2)
        self.assertEqual(chosen.street_segment_id.tolist(), other.street_segment_id.tolist())
        self.assertEqual(len(chosen), 10)
        self.assertTrue(chosen.groupby("borough_at_midpoint").size().eq(2).all())

    def test_missing_weather_invalid_flag_and_geometry_are_excluded(self):
        roads, weather = self.fixtures()
        weather = weather.drop(weather.loc[weather.cell_id == 0].index[0])
        weather.loc[weather.cell_id == 3, "weather_features_valid"] = False
        geometry = roads.loc[roads.cell_id != 6, "street_segment_id"]
        chosen = choose_roads(roads, weather, geometry, 2)
        self.assertTrue({"0-0", "1-0", "2-0"}.isdisjoint(set(chosen.street_segment_id)))

    def test_duplicate_weather_and_insufficient_roads_rejected(self):
        roads, weather = self.fixtures()
        with self.assertRaises(ValueError):
            choose_roads(roads, pd.concat([weather, weather.iloc[[0]]]), roads.street_segment_id, 2)
        with self.assertRaises(ValueError):
            choose_roads(roads, weather, roads.street_segment_id, 4)


if __name__ == "__main__":
    unittest.main()
