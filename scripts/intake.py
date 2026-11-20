"""Turn an issue-form request into a catalog change, with no human in the loop.

Two forms, picked by INTAKE_KIND:
- add-event: a new event. Accepted when a past edition on IFPA has a WPPR value of at least
  MIN_VALUE (the same bar the hand-made catalog uses). Name and patterns come from IFPA, not from
  the issue text, so nobody can put arbitrary words on the site through the form.
- suggest-website: another page to watch for an event already on the list. Accepted when the page
  loads, the nightly check can read it, and its text names the event.

Reads the form body from ISSUE_BODY, writes the reply to INTAKE_REPLY (a file), and exits 0 when
data/catalog.json changed, 2 when the request was turned down.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, available_timezones

sys.path.insert(0, str(Path(__file__).parent))
import ifpa  # noqa: E402
from build_seeds import domain  # noqa: E402
from discover import load_catalog, match  # noqa: E402
from fetch_pages import fetch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"
MIN_VALUE = 75.0
MAX_WATCH = 4  # pages per event; keeps one event from soaking up the nightly run
# Pages the nightly check can't read: social sites, link shorteners, logins.
UNREADABLE = {"facebook.com", "fb.me", "instagram.com", "discord.gg", "discord.com", "x.com", "twitter.com",
              "bit.ly", "tinyurl.com", "docs.google.com", "linktr.ee"}
US_WEST = {"America/Los_Angeles", "America/Phoenix", "America/Denver", "America/Boise", "America/Anchorage", "Pacific/Honolulu"}
US_EAST = {"America/New_York", "America/Detroit", "America/Indiana/Indianapolis", "America/Kentucky/Louisville"}


class Reject(Exception):
    pass


def form_fields(body: str) -> dict[str, str]:
    # GitHub issue forms render as "### Label\n\nvalue" blocks.
    out = {}
    for block in re.split(r"^### ", body or "", flags=re.M)[1:]:
        label, _, value = block.partition("\n")
        value = value.strip()
        out[label.strip().lower()] = "" if value == "_No response_" else value
    return out


def region_for(tz: str, country: str) -> str:
    if country == "CA":
        return "Canada"
    if tz.startswith("Europe/"):
        return "Europe"
    if tz.startswith("Australia/") or tz == "Pacific/Auckland":
        return "Australia / NZ"
    if country == "US":
        return "US West" if tz in US_WEST else "US East" if tz in US_EAST else "US Central"
    return "Elsewhere"


def clean_name(name: str) -> str:
    # "THE 4TH ANNUAL ABILENE SHOWDOWN MAIN EVENT" -> "Abilene Showdown", "Silverball Super Showdown 5" -> "Silverball Super Showdown"
    # Only edition markers go: years, ordinals, "8 year", and a trailing number or Roman numeral.
    # Numbers inside the name stay ("Level 1 Matchplay").
    n = re.sub(r"\b(19|20)\d\d\b|\b\d+(st|nd|rd|th)\b|\b\d+[- ]years?\b|\b\d+\s*$|\b[IVX]+\b(?=\s*$)", " ", name, flags=re.I)
    n = re.sub(r"^\s*[IVXL]{2,}\s+", " ", n)  # "XII Polish Pinball Open"; 2+ letters so "I Love Pinball" survives
    n = re.sub(r"\s*[-:|]\s*(main|a|classics?|womens?|juniors?|fri|sat|sun)\b.*$", "", n, flags=re.I)
    n = re.sub(r"\b(main (event|tournament)|annual)\b", " ", n, flags=re.I)
    n = re.sub(r"^\s*the\b", " ", n, flags=re.I)
    n = " ".join(n.split()).strip(" -:|") or name.strip()
    return n.title() if n.isupper() else n


def name_pattern(short: str) -> str:
    # Allow up to two extra words between ours, so "Electric Bat 9 year Anniversary Bash"
    # still matches "Electric Bat Anniversary Bash".
    return r"\W+(?:\S+\s+){0,2}?".join(re.escape(w) for w in short.lower().split())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())[:24] or "event"


def check_site(site: str) -> str:
    site = site.strip()
    if urlparse(site).scheme not in ("http", "https") or not urlparse(site).hostname:
        raise Reject("The website must be a full link that starts with `https://`.")
    if domain(site) in UNREADABLE:
        raise Reject("The website must be the event's own page. Facebook, Discord and similar pages "
                     "can't be read by the nightly check.")
    return site


def find_event(text: str, catalog: list[dict]) -> dict | None:
    key = re.sub(r"[^a-z0-9]+", "", text.lower())
    for ev in catalog:
        if key and key in {re.sub(r"[^a-z0-9]+", "", x.lower()) for x in (ev["id"], ev["name"], ev.get("short", ""))}:
            return ev
    eid = match({"tournament_name": text}, catalog)
    return next((ev for ev in catalog if ev["id"] == eid), None)


def names_event(page: str, ev: dict) -> bool:
    low = page.lower()
    return (any(n.lower() in low for n in (ev["name"], ev.get("short", "")) if len(n) >= 4)
            or any(re.search(p, page, re.I) for p in ev["patterns"]))


def evaluate_website(fields: dict[str, str], catalog: list[dict]) -> tuple[dict, str]:
    ev = find_event(fields.get("event", ""), catalog)
    if not ev:
        raise Reject(f"I couldn't find an event called \"{fields.get('event', '')}\" on the list. Use the name "
                     "as the site shows it. To add a new event, use the \"Add an event\" form.")
    site = check_site(fields.get("website", ""))
    if site.rstrip("/") in {u.rstrip("/") for u in ev.get("watch", [])}:
        raise Reject(f"The nightly check already reads {site} for **{ev['short']}**.")
    if len(ev.get("watch", [])) >= MAX_WATCH:
        raise Reject(f"**{ev['short']}** already has {MAX_WATCH} pages on the nightly check.")
    status, text = fetch(site)
    if status != "ok":
        raise Reject(f"The nightly check couldn't read {site} ({status}). Some sites turn away automated readers.")
    if not names_event(text, ev):
        raise Reject(f"{site} doesn't mention **{ev['short']}** by name, so I can't tell it's the event's page.")
    return ev, site


def add_watch(eid: str, url: str) -> None:
    # Edit the event's "watch" list in place so the file's hand-written layout survives.
    text = CATALOG.read_text(encoding="utf-8")
    start = text.index(f'{{"id": "{eid}"')
    w = text.index('"watch": [', start) + len('"watch": [')
    end = text.index("]", w)
    inner = text[w:end].strip()
    text = text[:w] + (f"{inner}, {json.dumps(url)}" if inner else json.dumps(url)) + text[end:]
    json.loads(text)  # fail loudly if the edit broke the file
    CATALOG.write_text(text, encoding="utf-8")


def evaluate(fields: dict[str, str], catalog: list[dict]) -> dict:
    link = fields.get("ifpa tournament from a past year", "")
    m = re.search(r"ifpapinball\.com/tournaments/view\.php\?t=(\d+)", link) or re.fullmatch(r"\s*(\d{3,7})\s*", link)
    if not m:
        raise Reject("I couldn't find an IFPA tournament link. Paste a link like "
                     "`https://www.ifpapinball.com/tournaments/view.php?t=12345` from a past edition.")
    t = ifpa.get(f"tournament/{m.group(1)}")
    if not t.get("tournament_id"):
        raise Reject(f"IFPA has no tournament {m.group(1)}.")
    name = t["tournament_name"].strip()
    value = float(t.get("tournament_value") or 0)
    if value < MIN_VALUE:
        raise Reject(f"**{name}** had a WPPR value of {value:.1f}. This site lists events whose past editions "
                     f"reached {MIN_VALUE:.0f} or more, so it doesn't qualify yet. Submit it again after an edition "
                     f"that reaches {MIN_VALUE:.0f}.")
    if (dup := match({"tournament_name": name}, catalog)):
        raise Reject(f"**{name}** is already on the list (`{dup}`).")

    site = check_site(fields.get("event website", ""))

    tz = fields.get("time zone", "").strip()
    if tz == "Other":
        tz = fields.get("other time zone", "").strip()
    if tz not in available_timezones():
        raise Reject(f"`{tz}` isn't a time zone name I know. Use one like `America/Chicago`.")
    ZoneInfo(tz)

    short = clean_name(name)
    eid, n = slug(short), 2
    ids = {e["id"] for e in catalog}
    while eid in ids:
        eid, n = f"{slug(short)}{n}", n + 1
    return {
        "id": eid, "name": short, "short": short, "tz": tz,
        "region": region_for(tz, (t.get("country_code") or "").upper()),
        "keys": [], "patterns": [name_pattern(short)], "watch": [site],
        "_added": {"ifpa_id": int(t["tournament_id"]), "value": round(value, 1), "city": t.get("city")},
    }


def append(entry: dict) -> None:
    # Keep the file's hand-written one-event-per-few-lines layout instead of re-dumping it.
    text = CATALOG.read_text(encoding="utf-8")
    line = "    " + json.dumps({k: v for k, v in entry.items() if k != "_added"}, ensure_ascii=False)
    head, sep, tail = text.rpartition("\n  ]")
    if not sep:
        raise SystemExit("catalog.json layout changed; can't append")
    CATALOG.write_text(f"{head.rstrip()},\n{line}{sep}{tail}", encoding="utf-8")
    json.loads(CATALOG.read_text(encoding="utf-8"))  # fail loudly if the append broke the file


def main() -> int:
    reply = Path(os.environ.get("INTAKE_REPLY", "intake-reply.md"))
    fields, kind = form_fields(os.environ.get("ISSUE_BODY", "")), os.environ.get("INTAKE_KIND", "add-event")
    try:
        if kind == "suggest-website":
            ev, site = evaluate_website(fields, load_catalog())
        else:
            entry = evaluate(fields, load_catalog())
    except Reject as e:
        reply.write_text(f"Thanks for the suggestion. I didn't add it:\n\n{e}\n", encoding="utf-8")
        return 2
    if kind == "suggest-website":
        add_watch(ev["id"], site)
        reply.write_text(f"Thanks. The nightly check reads {site} for **{ev['short']}** from tonight.\n", encoding="utf-8")
        return 0
    append(entry)
    a = entry["_added"]
    reply.write_text(
        f"Added **{entry['name']}** ({entry['region']}). Its IFPA tournament "
        f"[{a['ifpa_id']}](https://www.ifpapinball.com/tournaments/view.php?t={a['ifpa_id']}) had a WPPR value "
        f"of {a['value']}.\n\nThe nightly check reads {entry['watch'][0]} from tonight. Dates show up on the site "
        f"once that page states them in plain text, with the year.\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
