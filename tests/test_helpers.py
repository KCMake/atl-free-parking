"""Unit tests for the geometry, street-name and OSM-tag helpers in scripts/build.py."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build  # noqa: E402


class StreetNameTest(unittest.TestCase):
    def test_norm_abbreviates_suffixes_and_directions(self):
        self.assertEqual(build.norm("West Peachtree Street Northwest"), "w peachtree st nw")

    def test_norm_turns_spelled_ordinals_into_numbers(self):
        self.assertEqual(build.norm("Third Street NW"), "3rd st nw")

    def test_norm_ignores_case_punctuation_and_spacing(self):
        self.assertEqual(build.norm("  Ponce De Leon Ave., NE "), "ponce de leon ave ne")

    def test_norm_handles_missing_names(self):
        self.assertEqual(build.norm(None), "")
        self.assertEqual(build.norm(""), "")

    def test_parse_address(self):
        self.assertEqual(build.parse_address("1045 Hemphill Avenue Northwest"), (1045, "hemphill ave nw"))


class GeometryTest(unittest.TestCase):
    def test_dist_m_uses_local_meters(self):
        # 0.001 degrees of latitude is about 111 m everywhere.
        self.assertAlmostEqual(build.dist_m([-84.39, 33.770], [-84.39, 33.771]), 111.32, places=1)

    def test_midpoint_of_a_straight_line(self):
        mid = build.midpoint([[-84.39, 33.77], [-84.38, 33.77]])
        self.assertAlmostEqual(mid[0], -84.385)
        self.assertAlmostEqual(mid[1], 33.77)

    def test_point_in_polygon_respects_holes(self):
        outer = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
        hole = [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]
        geom = {"type": "Polygon", "coordinates": [outer, hole]}
        self.assertTrue(build.in_geom([0.5, 0.5], geom))
        self.assertFalse(build.in_geom([2, 2], geom))
        self.assertFalse(build.in_geom([5, 5], geom))

    def test_simplify_drops_collinear_points(self):
        line = [[-84.390, 33.77], [-84.389, 33.77], [-84.388, 33.77]]
        self.assertEqual(build.simplify(line, 1), [line[0], line[-1]])

    def test_simplify_keeps_corners(self):
        corner = [[-84.390, 33.770], [-84.389, 33.770], [-84.389, 33.771]]
        self.assertEqual(build.simplify(corner, 1), corner)

    def test_label_point_stays_inside_an_l_shape(self):
        # The vertex average of this L falls in its notch; the label must not.
        ell = [[0, 0], [10, 0], [10, 2], [2, 2], [2, 10], [0, 10], [0, 0]]
        geom = {"type": "Polygon", "coordinates": [ell]}
        self.assertTrue(build.in_geom(build.label_point(geom), geom))


class FullDocumentTest(unittest.TestCase):
    def test_title_moves_into_head(self):
        doc = build.full_document("<title>Free Curb ATL</title>\n<div id=map></div>")
        head, body = doc.split("<body>")
        self.assertTrue(doc.startswith("<!doctype html>"))
        self.assertEqual(doc.count("<title>"), 1)
        self.assertIn("<title>Free Curb ATL</title>", head)
        self.assertIn('<meta name="description"', head)
        self.assertIn('<link rel="icon"', head)
        self.assertIn("<div id=map></div>", body)


class OsmVerdictTest(unittest.TestCase):
    def test_no_parking_on_both_sides(self):
        self.assertEqual(build.osm_verdict({"parking:both": "no"}), "none")

    def test_lane_with_fee_is_paid(self):
        self.assertEqual(build.osm_verdict({"parking:right": "lane", "parking:right:fee": "yes"}), "paid")

    def test_lane_without_fee(self):
        tags = {"parking:left": "lane", "parking:left:orientation": "parallel"}
        self.assertEqual(build.osm_verdict(tags), "lane")

    def test_untagged_way_has_no_verdict(self):
        self.assertIsNone(build.osm_verdict({"highway": "residential"}))


if __name__ == "__main__":
    unittest.main()
