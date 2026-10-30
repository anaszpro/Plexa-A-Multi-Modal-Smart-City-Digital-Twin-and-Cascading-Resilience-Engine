from pathlib import Path
import sys
import unittest

import geopandas as gpd
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_aligned_ida_climate_candidate import geometry_candidates, propose_mapping


class AlignedMappingTests(unittest.TestCase):
    def fixtures(self):
        lion = gpd.GeoDataFrame({
            "lion_segment_id": ["L1"], "street_name": ["Requested street"],
            "supported_mobility_directions": ["EB,WB"],
        }, geometry=[LineString([(500000, 4500000), (500200, 4500000)])], crs=32618)
        climate = gpd.GeoDataFrame({"street_segment_id": ["aligned", "crossing", "endpoint"]},
            geometry=[LineString([(500000, 4500001), (500200, 4500001)]),
                      LineString([(500100, 4499900), (500100, 4500100)]),
                      LineString([(500000, 4500000), (500005, 4500000)])], crs=32618)
        return lion, climate

    def test_crossing_and_short_endpoint_remain_alternatives(self):
        lion, climate = self.fixtures()
        candidates = geometry_candidates(lion, climate)
        chosen = propose_mapping(candidates, ["L1"])
        self.assertEqual(chosen.street_segment_id.tolist(), ["aligned"])
        self.assertEqual(set(candidates.street_segment_id), {"aligned", "crossing", "endpoint"})
        other = geometry_candidates(lion, climate.sample(frac=1, random_state=42))
        self.assertEqual(candidates.street_segment_id.tolist(), other.street_segment_id.tolist())

    def test_ambiguous_and_missing_matches_stop_instead_of_arbitrary_choice(self):
        lion, climate = self.fixtures()
        duplicate = climate.iloc[[0]].copy()
        duplicate["street_segment_id"] = "another_aligned"
        import pandas as pd
        alternatives = gpd.GeoDataFrame(pd.concat([climate, duplicate], ignore_index=True), crs=32618)
        with self.assertRaises(ValueError):
            propose_mapping(geometry_candidates(lion, alternatives), ["L1"])
        with self.assertRaises(ValueError):
            propose_mapping(geometry_candidates(lion, climate), ["L1", "missing"])

    def test_partial_native_segments_require_explicit_review(self):
        lion, climate = self.fixtures()
        climate.loc[0, "geometry"] = LineString([(500000, 4500001), (500100, 4500001)])
        with self.assertRaises(ValueError):
            propose_mapping(geometry_candidates(lion, climate), ["L1"])


if __name__ == "__main__":
    unittest.main()
