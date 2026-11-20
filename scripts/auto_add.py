"""Weekly: add big events that aren't in the catalog yet, with no human in the loop.

Refreshes data/seed_candidates.csv (build_seeds.py), then adds every festival that
- had a 75+ WPPR edition in at least MIN_YEARS different years (one-offs and launch parties don't),
- drew MIN_PLAYERS+ players at its biggest,
- had an edition in the last RECENT_DAYS days,
- isn't an invitational or league,
- and isn't already covered by a catalog event (same festival key, or a catalog pattern
  matches one of its tournament names).

That's stricter than the "Add an event" issue form (one 75+ edition), because there a person
vouches for the event and here nobody does. A 75-only bar would have added 93 events at once.
At most MAX_PER_RUN are added per run (--max N overrides). Pass --dry-run to only print.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_seeds  # noqa: E402
import intake  # noqa: E402
from discover import load_catalog, match  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RECENT_DAYS = 400
MIN_YEARS = 2
MIN_PLAYERS = 80
MAX_PER_RUN = 5

US_TZ = {
    **dict.fromkeys(["CA", "WA", "OR", "NV"], "America/Los_Angeles"),
    **dict.fromkeys(["CO", "UT", "NM", "ID", "MT", "WY"], "America/Denver"),
    "AZ": "America/Phoenix", "AK": "America/Anchorage", "HI": "Pacific/Honolulu",
    **dict.fromkeys(["TX", "OK", "KS", "NE", "SD", "ND", "MN", "IA", "MO", "AR", "LA", "MS", "AL", "WI", "IL", "TN"],
                    "America/Chicago"),
}
CA_TZ = {"BC": "America/Vancouver", "AB": "America/Edmonton", "SK": "America/Regina", "MB": "America/Winnipeg",
         "ON": "America/Toronto", "QC": "America/Toronto", "NS": "America/Halifax", "NB": "America/Halifax"}
AU_TZ = {"NSW": "Australia/Sydney", "ACT": "Australia/Sydney", "VIC": "Australia/Melbourne", "QLD": "Australia/Brisbane",
         "SA": "Australia/Adelaide", "WA": "Australia/Perth", "TAS": "Australia/Hobart", "NT": "Australia/Darwin"}
COUNTRY_TZ = {"GB": "Europe/London", "IE": "Europe/Dublin", "NL": "Europe/Amsterdam", "DE": "Europe/Berlin",
              "FR": "Europe/Paris", "BE": "Europe/Brussels", "AT": "Europe/Vienna", "CH": "Europe/Zurich",
              "IT": "Europe/Rome", "ES": "Europe/Madrid", "SE": "Europe/Stockholm", "NO": "Europe/Oslo",
              "DK": "Europe/Copenhagen", "FI": "Europe/Helsinki", "PL": "Europe/Warsaw", "CZ": "Europe/Prague",
              "HU": "Europe/Budapest", "SI": "Europe/Ljubljana", "HR": "Europe/Zagreb", "PT": "Europe/Lisbon",
              "NZ": "Pacific/Auckland", "JP": "Asia/Tokyo"}


def guess_tz(country: str, state: str) -> str | None:
    country, state = (country or "").upper(), (state or "").upper()
    if country == "US":
        return US_TZ.get(state, "America/New_York")
    if country == "CA":
        return CA_TZ.get(state)
    if country == "AU":
        return AU_TZ.get(state)
    return COUNTRY_TZ.get(country)


def candidates(rows: list[dict], catalog: list[dict], today: dt.date) -> list[dict]:
    covered = {k for ev in catalog for k in ev.get("keys", [])}
    out = []
    for r in rows:
        if r["invitational"] == "True" or r["key"] in covered:
            continue
        if int(r["n_years"]) < MIN_YEARS or int(r["max_players"]) < MIN_PLAYERS:
            continue
        if (today - dt.date.fromisoformat(r["last_start"])).days > RECENT_DAYS:
            continue
        if any(match({"tournament_name": n}, catalog) for n in r["all_names"].split(" / ")):
            continue
        tz = guess_tz(r["country"], r["stateprov"])
        if not tz:
            print(f"skip (no time zone for {r['country']}/{r['stateprov']}): {r['name']}")
            continue
        out.append({**r, "tz": tz})
    return out


def entry_for(r: dict, taken: set[str]) -> dict:
    short = intake.clean_name(r["name"])
    eid, n = intake.slug(short), 2
    while eid in taken:
        eid, n = f"{intake.slug(short)}{n}", n + 1
    site = r["website"]
    readable = site.startswith(("http://", "https://")) and build_seeds.domain(site) not in build_seeds.SHARED_HOSTS \
        and build_seeds.domain(site) not in intake.UNREADABLE
    return {"id": eid, "name": short, "short": short, "tz": r["tz"], "region": intake.region_for(r["tz"], r["country"]),
            "keys": [r["key"]], "patterns": [intake.name_pattern(short)], "watch": [site] if readable else []}


def main() -> int:
    dry = "--dry-run" in sys.argv
    limit = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else MAX_PER_RUN
    if "--skip-seeds" not in sys.argv:
        build_seeds.main()
    with (ROOT / "data" / "seed_candidates.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    catalog = load_catalog()
    taken = {e["id"] for e in catalog}
    added = []
    ranked = sorted(candidates(rows, catalog, dt.date.today()), key=lambda r: -int(r["max_players"]))
    for r in ranked:
        if len(added) >= limit:
            print(f"stopping at {limit}; the rest wait for the next run")
            break
        e = entry_for(r, taken)
        # Two festivals can clean to the same name; the second one would just duplicate the first.
        if any(re.search(p, e["name"], re.I) for a in added for p in a["patterns"]):
            continue
        taken.add(e["id"])
        added.append(e)
        print(f"{'would add' if dry else 'add'} {e['id']:24} {e['region']:14} {r['last_start']}  tier {r['max_tier']:>3}  "
              f"{r['max_players']:>4}p  {e['name']}  {e['watch'][0] if e['watch'] else '(no website)'}")
        if not dry:
            intake.append(e)
    print(f"{len(added)} new events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
