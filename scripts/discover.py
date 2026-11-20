"""Nightly: pull upcoming IFPA tournaments and match them to catalog events.

Writes data/ifpa_upcoming.json: for each catalog event, the upcoming IFPA tournaments that belong
to it (dates, names, website). Matching is by name pattern (see match()).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ifpa  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MONTHS_AHEAD = 18


def load_catalog() -> list[dict]:
    return json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))["events"]


def match(t: dict, catalog: list[dict]) -> str | None:
    # Name patterns only. Website domains are shared by a venue's weekly events (Starfighters
    # Monthly lives on the Starfighters SPF domain), so they can't identify a festival on their own.
    name = t["tournament_name"].strip()
    for ev in catalog:
        if any(re.search(p, name, re.I) for p in ev["patterns"]):
            return ev["id"]
    return None


def summarize(t: dict) -> dict:
    return {
        "ifpa_id": int(t["tournament_id"]),
        "name": t["tournament_name"].strip(),
        "division": (t.get("event_name") or "").strip() or None,
        "start": ifpa.clean_date(t.get("event_start_date")),
        "end": ifpa.clean_date(t.get("event_end_date")) or ifpa.clean_date(t.get("event_start_date")),
        "city": t.get("city"), "stateprov": t.get("stateprov"), "country": t.get("country_code"),
        "website": (t.get("website") or "").strip() or None,
        "prereg_flag_date": ifpa.clean_date(t.get("preregistration_date")),
    }


def main() -> None:
    catalog = load_catalog()
    today = dt.date.today()
    end = today + dt.timedelta(days=31 * MONTHS_AHEAD)
    upcoming = ifpa.search_all(start_date=today.isoformat(), end_date=end.isoformat())
    print(f"{len(upcoming)} upcoming IFPA tournaments")

    by_event: dict[str, list[dict]] = {ev["id"]: [] for ev in catalog}
    for t in upcoming:
        eid = match(t, catalog)
        if eid:
            by_event[eid].append(summarize(t))
    for v in by_event.values():
        v.sort(key=lambda x: (x["start"] or "", x["name"]))

    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "events": by_event}
    (ROOT / "data" / "ifpa_upcoming.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    hits = {k: len(v) for k, v in by_event.items() if v}
    print(f"matched {sum(hits.values())} tournaments to {len(hits)} events: {hits}")


if __name__ == "__main__":
    main()
