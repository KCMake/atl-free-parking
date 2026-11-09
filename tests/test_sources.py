"""Schema checks for data/sources/paid_sources.json, the hand-compiled evidence file.

These run on every change so a new listing or rule can't land malformed.
"""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build  # noqa: E402

DISTRICT_KINDS = {"campus", "private", "limited", "mapped", "permit"}


class SourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = json.loads(build.SOURCES.read_text())

    def test_listing_fields(self):
        for item in self.src["listings"]["items"]:
            with self.subTest(addr=item.get("addr")):
                self.assertRegex(item["addr"], r"^\d+ \S")
                self.assertIn(item["rule"], build.RULE_RANK)
                self.assertTrue(item["url"].startswith("https://"))
                self.assertTrue(item["detail"].strip())
                if "until" in item:
                    self.assertEqual(item["rule"], "meter")
                    self.assertIsInstance(item["until"], int)
                    self.assertTrue(8 <= item["until"] <= 22)

    def test_listing_addresses_are_unique(self):
        dupes = [a for a, n in Counter(i["addr"] for i in self.src["listings"]["items"]).items() if n > 1]
        self.assertEqual(dupes, [])

    def test_district_fields(self):
        for name, d in self.src["districts"].items():
            if name.startswith("_"):
                continue
            with self.subTest(district=name):
                self.assertIn(d["kind"], DISTRICT_KINDS)
                self.assertTrue(d["reason"].strip())
                self.assertTrue(d["source"].startswith("https://"))

    def test_street_rule_fields(self):
        for r in self.src["street_rules"]["items"]:
            with self.subTest(street=r.get("street")):
                self.assertIn(r["rule"], {"limited", "check"})
                self.assertTrue(r["reason"].strip())
                self.assertTrue(r["source"].startswith("https://"))
                if "between" in r:
                    self.assertEqual(len(r["between"]), 2)

    def test_enforcement_hours(self):
        enf = self.src["enforcement"]
        self.assertTrue(enf["sunday_free"])
        self.assertTrue(enf["holidays"])


if __name__ == "__main__":
    unittest.main()
