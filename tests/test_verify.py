"""Traps the verifier must catch. Pages here are trimmed copies of real event sites."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from verify import Reject, check_claim, sanity  # noqa: E402

TODAY = dt.date(2026, 11, 19)
U = "https://example.org/"
PAGE = {U: """
2027 target dates are August 26-29, all details To Be Confirmed.
Pinhead package SOLD OUT.
General registration for remaining 80 spots will open on 6/20 @ Noon Pacific.
160 Tournament Tickets slots sold out on October 31st, 2027.
Weekend passes go on sale Fri., Feb. 26th at 6pm ET online
27.–30. Mai 2027
18.6. – 20.6.2027
Registration starts at: 2026.July 06. 20:00 CET
Get Tickets <https://buytickets.at/iepinball/2352180>
Limited to 150 players.
"""}


def claim(field, value, quote, tz=None):
    c = {"field": field, "value": value, "quote": quote, "url": U}
    if tz:
        c["tz"] = tz
    return c


class Accepts(unittest.TestCase):
    def ok(self, c):
        check_claim(c, PAGE, TODAY)

    def test_range_with_year(self):
        self.ok(claim("event_start", "2027-08-26", "2027 target dates are August 26-29"))
        self.ok(claim("event_end", "2027-08-29", "2027 target dates are August 26-29"))

    def test_german_month(self):
        self.ok(claim("event_start", "2027-05-27", "27.–30. Mai 2027"))

    def test_numeric_european_dates(self):
        self.ok(claim("event_end", "2027-06-20", "18.6. – 20.6.2027"))

    def test_near_date_without_year_and_time(self):
        self.ok(claim("reg_opens", "2027-02-26T18:00", "Weekend passes go on sale Fri., Feb. 26th at 6pm ET",
                      "America/New_York"))

    def test_dash_and_whitespace_normalization(self):
        self.ok(claim("event_start", "2027-05-27", "27.-30.   Mai 2027"))

    def test_url_and_cap(self):
        self.ok(claim("reg_url", "https://buytickets.at/iepinball/2352180", "Get Tickets"))
        self.ok(claim("player_cap", 150, "Limited to 150 players."))


class Rejects(unittest.TestCase):
    def bad(self, c, why):
        with self.assertRaises(Reject) as cm:
            check_claim(c, PAGE, TODAY)
        self.assertIn(why, str(cm.exception))

    def test_invented_quote(self):
        self.bad(claim("reg_opens", "2027-02-01", "Registration opens February 1, 2027"), "not found")

    def test_last_years_yearless_date(self):
        # "6/20" is almost certainly last year's info; guessing 2027 must not pass.
        self.bad(claim("reg_opens", "2027-06-20T12:00", "will open on 6/20 @ Noon Pacific", "America/Los_Angeles"),
                 "days out")

    def test_year_contradicts_quote(self):
        self.bad(claim("reg_opens", "2026-07-06T20:00", "2027 target dates are August 26-29"), "day 6")
        self.bad(claim("reg_closes", "2026-10-31", "sold out on October 31st, 2027"), "year 2026")

    def test_wrong_day(self):
        self.bad(claim("event_start", "2027-08-25", "2027 target dates are August 26-29"), "day 25")

    def test_wrong_month(self):
        self.bad(claim("event_start", "2027-06-27", "27.–30. Mai 2027"), "month 6")

    def test_wrong_time(self):
        self.bad(claim("reg_opens", "2027-02-26T19:00", "Weekend passes go on sale Fri., Feb. 26th at 6pm ET",
                       "America/New_York"), "time")

    def test_headline_must_be_verbatim(self):
        self.bad(claim("headline", "Registration is sold out", "Pinhead package SOLD OUT."), "quote itself")

    def test_url_not_on_page(self):
        self.bad(claim("reg_url", "https://evil.example/pay", "Get Tickets"), "not on page")

    def test_unknown_page(self):
        c = claim("player_cap", 150, "Limited to 150 players.")
        c["url"] = "https://other.example/"
        self.bad(c, "no saved page")


class Sanity(unittest.TestCase):
    def f(self, **kw):
        return {k: {"value": v} for k, v in kw.items()}

    def test_finished_edition_is_dropped(self):
        drop = sanity(self.f(event_start="2026-07-22", event_end="2026-07-26", reg_url="x", headline="h"), TODAY)
        self.assertEqual(set(drop), {"event_start", "event_end", "reg_url"})

    def test_reg_after_event_is_dropped(self):
        drop = sanity(self.f(event_start="2027-02-04", reg_opens="2027-03-01"), TODAY)
        self.assertEqual(drop, ["reg_opens"])

    def test_good_edition_kept(self):
        self.assertEqual(sanity(self.f(event_start="2027-02-04", event_end="2027-02-07", reg_opens="2026-08-29T10:00"),
                                TODAY), [])


if __name__ == "__main__":
    unittest.main()
