"""Regression tests for the block classifier, run against the committed data.

Each test pins a mistake the map has made or could make: free parking on a
highway, a free block shown as paid, a Sunday-free meter that is really
enforced every day.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build  # noqa: E402

STATUSES = {"free", "limited", "night", "check", "paid", "campus", "none"}


class ClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = build.build()
        cls.features = cls.result["streets"]["features"]
        cls.blocks = [f["properties"] for f in cls.features]

    def block(self, name, numbers):
        hits = [b for b in self.blocks if b["name"] == name and b["block"] == numbers]
        self.assertEqual(len(hits), 1, f"expected one {name} {numbers} block, found {len(hits)}")
        return hits[0]

    def km(self, status):
        return sum(b["len"] for b in self.blocks if b["status"] == status) / 1000

    def test_every_block_has_a_known_status(self):
        self.assertEqual({b["status"] for b in self.blocks} - STATUSES, set())

    # ------------------------------------------------ hard exclusions

    def test_highways_and_ramps_show_no_parking(self):
        for b in self.blocks:
            if b["class"] in build.HIGHWAY_CLASSES or build.norm(b["name"]).startswith(build.HIGHWAY_PREFIXES):
                self.assertEqual(b["status"], "none", b)

    def test_roads_without_curb_parking_show_none_outside_downtown(self):
        for b in self.blocks:
            if build.norm(b["name"]) in build.NO_PARKING_ROADS and b["hood"] != "Downtown":
                self.assertEqual(b["status"], "none", b)

    def test_drives_inside_parks_show_no_parking(self):
        # Streets along a park's edge (Barksdale Dr at Winn Park) are public curbs,
        # so only blocks clearly inside a park, more than 2 m from its edge, count.
        parks = [f["geometry"] for f in build.load("osm_areas.geojson")["features"]
                 if f["properties"].get("leisure") == "park"]
        for f in self.features:
            mid = build.midpoint(f["geometry"]["coordinates"])
            for g in parks:
                edge = min(build.point_line_dist(mid, ring) for poly in build.polygons(g) for ring in poly)
                if build.in_geom(mid, g) and edge > 2:
                    self.assertEqual(f["properties"]["status"], "none", f["properties"])

    def test_georgia_tech_campus_is_never_free(self):
        statuses = {b["status"] for b in self.blocks if b["hood"] == "Georgia Tech"}
        self.assertLessEqual(statuses, {"campus", "none"})

    # ------------------------------------------------ block listings

    def test_listings_match_city_blocks(self):
        self.assertLessEqual(len(self.result["unmatched"]), 2, self.result["unmatched"])

    def test_meter_listing_carries_its_own_end_hour(self):
        b = self.block("3rd St NW", "44–100")
        self.assertEqual((b["status"], b["timed"], b.get("until"), b.get("conf")), ("paid", True, 19, "listing"))

    def test_free_listing_filed_under_a_meter_url_is_not_paid(self):
        # Parkopedia files every on-street listing under /meter/, free ones included.
        b = self.block("Hemphill Ave NW", "983–1051")
        self.assertIn("/meter/", b["source"])
        self.assertEqual(b["status"], "limited")

    def test_evening_only_listing(self):
        self.assertEqual(self.block("Hirsch St NW", "980–1004")["status"], "night")

    def test_most_restrictive_listing_wins(self):
        # 935 Marietta is a loading zone ("check"); 950 and 962 on the same block are meters.
        self.assertEqual(self.block("Marietta St NW", "911–997")["status"], "paid")

    # ------------------------------------------------ meter hours

    def test_atlantic_station_is_paid_every_day(self):
        paid = [b for b in self.blocks if b["hood"] == "Atlantic Station" and b["status"] == "paid"]
        self.assertTrue(paid)
        # "timed" blocks turn free on Sundays and holidays in the page.
        self.assertFalse([b for b in paid if b["timed"]])

    def test_meter_end_hours_fall_inside_city_hours(self):
        for b in self.blocks:
            if "until" in b:
                self.assertTrue(8 <= b["until"] <= 22, b)

    # ------------------------------------------------ evidence

    def test_restricted_blocks_cite_a_source(self):
        for b in self.blocks:
            if b["status"] in {"paid", "limited", "night"}:
                self.assertTrue(b.get("source", "").startswith("https://"), b)

    def test_coverage_totals_are_plausible(self):
        # A rule-order regression tends to flip whole districts at once.
        self.assertGreater(len(self.blocks), 1900)
        self.assertGreater(self.km("free"), 60)
        self.assertLess(self.km("paid"), 80)


if __name__ == "__main__":
    unittest.main()
