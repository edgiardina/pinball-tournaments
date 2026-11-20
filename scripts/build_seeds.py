"""Find the big recurring events from IFPA history. Run about once a year.

Pulls every completed tournament worth >= MIN_VALUE WPPR in the last few years, groups divisions
into festivals by website domain, and writes data/seed_candidates.csv for a human to skim. The
catalog (data/catalog.json) is the curated result; this script never edits it.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import ifpa  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TIERS = (75, 100, 200, 300)
YEARS_BACK = 3

# Hosts that many unrelated events share, so the domain alone can't identify a festival.
SHARED_HOSTS = {
    "facebook.com", "fb.me", "eventbrite.com", "app.matchplay.events", "matchplay.events", "matchplay.live",
    "svenskaflippersallskapet.com", "pinballevents.de", "flippermarkt.de", "docs.google.com", "bit.ly",
    "tinyurl.com", "sites.google.com", "aussiearcade.com", "ifpapinball.com", "instagram.com", "flipjuke.fr",
    "forum.flippery.org.pl", "pinballrepublic.com", "bnepac.org", "district82.com",
}
INVITATIONAL = re.compile(
    r"(^ifpa world pinball championship|pin-masters|series final|cs finals?\b|ukcs|gcs final|nlcs|hcs final|\bfcs\b"
    r"|swecs|invitational|championship series|ifpa european pinball championship)", re.I)


def domain(url: str | None) -> str:
    host = (urlparse((url or "").strip()).hostname or "").lower().removeprefix("www.")
    if host.endswith(".eventbrite.com"):
        # 2026midatlanticpinballopen.eventbrite.com -> midatlanticpinballopen@eb
        host = re.sub(r"^\d{4}", "", host.split(".")[0]) + "@eb"
    return host


def short_name(name: str) -> str:
    n = re.sub(r"\b(19|20)\d\d\b|\b\d+(st|nd|rd|th)?\b|\b[ivx]+\b", " ", name.lower())
    return " ".join(re.sub(r"[^a-z ]", " ", n).split()[:3])


def festival_key(t: dict) -> str:
    host = domain(t.get("website"))
    if host and host not in SHARED_HOSTS:
        return host
    return f'{t.get("city") or ""}|{short_name(t["tournament_name"])}'


def main() -> None:
    today = dt.date.today()
    start = today.replace(year=today.year - YEARS_BACK)
    tier: dict[int, int] = {}
    tournaments: dict[int, dict] = {}
    for mp in TIERS:
        rows = ifpa.search_all(start_date=start.isoformat(), end_date=today.isoformat(), min_points=mp,
                               point_filter="Y", only_with_results="Y")
        print(f"min_points {mp}: {len(rows)}")
        for t in rows:
            tid = int(t["tournament_id"])
            tournaments[tid] = t
            tier[tid] = mp

    fests: dict[str, list[dict]] = collections.defaultdict(list)
    for tid, t in tournaments.items():
        if (t.get("event_type") or "").lower() == "league":
            continue
        fests[festival_key(t)].append(t)

    rows = []
    for key, ts in fests.items():
        years = sorted({t["event_start_date"][:4] for t in ts})
        last = max(ts, key=lambda t: t["event_start_date"])
        last_year = [t for t in ts if t["event_start_date"][:4] == last["event_start_date"][:4]]
        rows.append({
            "key": key,
            "name": max(last_year, key=lambda t: int(t["player_count"]))["tournament_name"].strip(),
            "city": last.get("city"), "stateprov": last.get("stateprov"), "country": last.get("country_code"),
            "years": " ".join(years), "n_years": len(years),
            "max_tier": max(tier[int(t["tournament_id"])] for t in ts),
            "max_players": max(int(t["player_count"]) for t in ts),
            "divisions_last": len(last_year),
            "last_start": last["event_start_date"][:10],
            "website": (last.get("website") or "").strip(),
            "invitational": all(INVITATIONAL.search(t["tournament_name"]) for t in ts),
            "all_names": " / ".join(sorted({t["tournament_name"].strip() for t in ts}))[:400],
        })
    rows.sort(key=lambda r: (r["invitational"], r["n_years"] < 2, -r["max_tier"], -r["max_players"]))
    out = ROOT / "data" / "seed_candidates.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    recurring = [r for r in rows if r["n_years"] >= 2 and not r["invitational"]]
    print(f"{len(rows)} festivals, {len(recurring)} recurring public -> {out}")


if __name__ == "__main__":
    main()
