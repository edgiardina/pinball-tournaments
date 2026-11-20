import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import intake  # noqa: E402

FORM = """### IFPA tournament from a past year

https://www.ifpapinball.com/tournaments/view.php?t=111895

### Event website

https://example.org/

### Time zone

Other

### Other time zone

_No response_
"""


class FormTests(unittest.TestCase):
    def test_parses_issue_form(self):
        f = intake.form_fields(FORM)
        self.assertEqual(f["event website"], "https://example.org/")
        self.assertEqual(f["time zone"], "Other")
        self.assertEqual(f["other time zone"], "")

    def test_clean_name_strips_years_ordinals_and_division(self):
        self.assertEqual(intake.clean_name("THE 4TH ANNUAL ABILENE SHOWDOWN MAIN EVENT"), "Abilene Showdown")
        self.assertEqual(intake.clean_name("Silverball Super Showdown 5"), "Silverball Super Showdown")
        self.assertEqual(intake.clean_name("Pinball Expo 2026 - Main"), "Pinball Expo")

    def test_pattern_matches_the_source_name(self):
        for n in ["THE 4TH ANNUAL ABILENE SHOWDOWN MAIN EVENT", "Hello (World) Open IV"]:
            self.assertRegex(n, re.compile(intake.name_pattern(intake.clean_name(n)), re.I))

    def test_region(self):
        self.assertEqual(intake.region_for("America/Chicago", "US"), "US Central")
        self.assertEqual(intake.region_for("America/Los_Angeles", "US"), "US West")
        self.assertEqual(intake.region_for("America/Toronto", "CA"), "Canada")
        self.assertEqual(intake.region_for("Europe/Budapest", "HU"), "Europe")
        self.assertEqual(intake.region_for("Asia/Tokyo", "JP"), "Elsewhere")

    def test_find_event_by_short_name_or_pattern(self):
        cat = [{"id": "sss", "name": "Silverball Super Showdown", "short": "SSS", "patterns": ["silverball super s"]}]
        self.assertEqual(intake.find_event("sss", cat)["id"], "sss")
        self.assertEqual(intake.find_event("Silverball Super Showdown 6", cat)["id"], "sss")
        self.assertIsNone(intake.find_event("Nope Fest", cat))

    def test_page_must_name_the_event(self):
        ev = {"id": "sss", "name": "Silverball Super Showdown", "short": "SSS", "patterns": ["silverball super s"]}
        self.assertTrue(intake.names_event("Welcome to the Silverball Super Showdown 6!", ev))
        self.assertFalse(intake.names_event("Python is a programming language", ev))

    def test_add_watch_keeps_layout_and_valid_json(self):
        import json
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "catalog.json"
        tmp.write_text('{\n  "events": [\n    {"id": "a", "name": "A", "watch": []},\n'
                       '    {"id": "b", "name": "B",\n     "watch": ["https://b.example/"]}\n  ]\n}\n', encoding="utf-8")
        old, intake.CATALOG = intake.CATALOG, tmp
        try:
            intake.add_watch("a", "https://a.example/")
            intake.add_watch("b", "https://b2.example/")
        finally:
            intake.CATALOG = old
        evs = {e["id"]: e["watch"] for e in json.loads(tmp.read_text(encoding="utf-8"))["events"]}
        self.assertEqual(evs, {"a": ["https://a.example/"], "b": ["https://b.example/", "https://b2.example/"]})
        self.assertEqual(len(tmp.read_text(encoding="utf-8").splitlines()), 7)

    def test_rejects_missing_ifpa_link_before_calling_the_api(self):
        with self.assertRaises(intake.Reject):
            intake.evaluate({"ifpa tournament from a past year": "my event is great"}, [])


if __name__ == "__main__":
    unittest.main()
