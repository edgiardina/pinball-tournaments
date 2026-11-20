"""Combine catalog + IFPA + verified state into the site: site/events.json, site/*.ics, site/index.html.

Source priority for an edition's dates: verified organizer page, then the IFPA calendar.
Registration info only ever comes from verified organizer pages.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"
PRODID = "-//pinball-tournaments//EN"
CAL_NAME = "Big Pinball Tournaments"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def history_stats() -> dict[str, dict]:
    path = DATA / "seed_candidates.csv"
    if not path.exists():
        return {}
    return {r["key"]: r for r in csv.DictReader(path.open(encoding="utf-8"))}


def ifpa_edition(tournaments: list[dict]) -> dict | None:
    """The next cluster of IFPA tournaments for an event (divisions within ~10 days of the first)."""
    ts = [t for t in tournaments if t.get("start")]
    if not ts:
        return None
    first = min(dt.date.fromisoformat(t["start"]) for t in ts)
    cluster = [t for t in ts if dt.date.fromisoformat(t["start"]) <= first + dt.timedelta(days=10)]
    return {
        "start": first.isoformat(),
        "end": max(t["end"] or t["start"] for t in cluster),
        "city": cluster[0].get("city"), "stateprov": cluster[0].get("stateprov"), "country": cluster[0].get("country"),
        "divisions": [{"name": t["name"], "ifpa_id": t["ifpa_id"], "start": t["start"]} for t in cluster],
    }


def site_label(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def build_events(today: dt.date) -> list[dict]:
    catalog = load(DATA / "catalog.json", {"events": []})["events"]
    upcoming = load(DATA / "ifpa_upcoming.json", {"events": {}})["events"]
    state = load(DATA / "state.json", {"events": {}})["events"]
    pages = load(DATA / "pages" / "index.json", {})
    hist = history_stats()
    out = []
    for ev in catalog:
        fields = state.get(ev["id"], {}).get("fields", {})
        val = lambda f: fields.get(f, {}).get("value")  # noqa: E731
        ifpa = ifpa_edition(upcoming.get(ev["id"], []))
        rows = [hist[k] for k in ev["keys"] if k in hist]
        past = None
        if rows:
            last = max(rows, key=lambda r: r["last_start"])
            past = {
                "peak_players": max(int(r["max_players"]) for r in rows),
                "wppr_tier": max(int(r["max_tier"]) for r in rows),
                "years": sorted({y for r in rows for y in r["years"].split()}),
                "last_start": last["last_start"],
                "city": last["city"], "stateprov": last["stateprov"], "country": last["country"],
            }

        start, end, date_source = val("event_start"), val("event_end"), None
        if start:
            end = end or start
            date_source = {"kind": "organizer", **_src(fields["event_start"])}
        elif ifpa:
            start, end = ifpa["start"], ifpa["end"]
            date_source = {"kind": "ifpa", "url": f'https://www.ifpapinball.com/tournaments/view.php?t={ifpa["divisions"][0]["ifpa_id"]}',
                           "label": "IFPA calendar"}
        if end and dt.date.fromisoformat(end) < today:
            start = end = date_source = None  # finished; wait for next year's info

        loc = ifpa or past or {}
        reg = {}
        for f in ("reg_opens", "reg_closes", "reg_status", "reg_method", "reg_url", "player_cap"):
            if f in fields:
                reg[f] = {"value": fields[f]["value"], **_src(fields[f])}
                if fields[f].get("tz"):
                    reg[f]["tz"] = fields[f]["tz"]
        watch = [{"url": u, "label": site_label(u), "status": pages.get(u, {}).get("status"),
                  "last_ok_at": pages.get(u, {}).get("last_ok_at")} for u in ev.get("watch", [])]
        out.append({
            "id": ev["id"], "name": ev["name"], "short": ev.get("short", ev["name"]), "region": ev["region"],
            "tz": ev["tz"], "start": start, "end": end, "date_source": date_source,
            "city": loc.get("city"), "stateprov": loc.get("stateprov"), "country": loc.get("country"),
            "divisions": (ifpa or {}).get("divisions", []) if start else [],
            "headline": ({"value": val("headline"), **_src(fields["headline"])} if val("headline") else None),
            "reg": reg, "past": past, "watch": watch, "note": ev.get("note"),
            "checked_at": state.get(ev["id"], {}).get("checked_at"),
        })
    out.sort(key=lambda e: (e["start"] is None, e["start"] or "", (e["past"] or {}).get("last_start", "")[5:] or "99"))
    return out


def _src(f: dict) -> dict:
    return {"quote": f.get("quote"), "url": f.get("url"), "label": site_label(f.get("url", "")),
            "verified_at": f.get("verified_at"), "previous": f.get("previous")}


# ---------------------------------------------------------------- ICS

def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def utc_stamp(value: str, tz: str) -> str:
    local = dt.datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(tz))
    return local.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def vevent(uid: str, summary: str, when: str, tz: str, desc: str, url: str | None, end: str | None = None,
           alarms: tuple[str, ...] = ()) -> list[str]:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", f"SUMMARY:{ics_escape(summary)}"]
    if "T" in when:
        lines += [f"DTSTART:{utc_stamp(when, tz)}",
                  f"DTEND:{utc_stamp((dt.datetime.fromisoformat(when) + dt.timedelta(minutes=30)).isoformat(), tz)}"]
    else:
        last = dt.date.fromisoformat(end or when) + dt.timedelta(days=1)
        lines += [f"DTSTART;VALUE=DATE:{when.replace('-', '')}", f"DTEND;VALUE=DATE:{last.strftime('%Y%m%d')}",
                  "TRANSP:TRANSPARENT"]
    lines.append(f"DESCRIPTION:{ics_escape(desc)}")
    if url:
        lines.append(f"URL:{url}")
    for trig in alarms:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{ics_escape(summary)}", f"TRIGGER:{trig}", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def calendar_items(e: dict) -> list[tuple[str, list[str]]]:
    """(kind, vevent lines) for one event. kind is 'event' or 'reg'."""
    items = []
    where = ", ".join(x for x in (e["city"], e["stateprov"], e["country"]) if x)
    if e["start"]:
        src = e["date_source"]
        desc = f'{e["name"]}\n{where}\nSource: {src["url"]}'
        if e["headline"]:
            desc += f'\nOrganizer says: "{e["headline"]["value"]}"'
        items.append(("event", vevent(f'{e["id"]}-event-{e["start"][:4]}@pinball-tournaments', e["name"], e["start"],
                                      e["tz"], desc, src["url"], end=e["end"])))
    for f, label, alarms in (("reg_opens", "registration opens", ("-P1D", "-PT1H")),
                             ("reg_closes", "registration closes", ("-P2D",))):
        r = e["reg"].get(f)
        if not r:
            continue
        year = (e["start"] or r["value"])[:4]
        desc = f'{e["name"]}: {label}.\n"{r["quote"]}"\nSource: {r["url"]}'
        if e["reg"].get("reg_url"):
            desc += f'\nRegister: {e["reg"]["reg_url"]["value"]}'
        timed = "T" in r["value"]
        link = e["reg"]["reg_url"]["value"] if e["reg"].get("reg_url") else r["url"]
        items.append(("reg", vevent(f'{e["id"]}-{f}-{year}@pinball-tournaments', f'{e["short"]} {label}', r["value"],
                                    r.get("tz") or e["tz"], desc, link, alarms=alarms if timed else ("-PT12H",))))
    return items


def write_ics(path: Path, name: str, events: list[dict], kinds: set[str]) -> None:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{ics_escape(name)}", "X-PUBLISHED-TTL:PT6H", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"]
    for e in events:
        for kind, ev in calendar_items(e):
            if kind in kinds:
                lines += ev
    lines.append("END:VCALENDAR")
    path.write_bytes(("\r\n".join(fold(ln) for ln in lines) + "\r\n").encode("utf-8"))


# ---------------------------------------------------------------- main

def main() -> None:
    today = dt.date.today()
    SITE.mkdir(exist_ok=True)
    events = build_events(today)
    regions = sorted({e["region"] for e in events})
    feeds = [{"id": "all", "name": "Everything", "file": "calendar.ics"},
             {"id": "registration", "name": "Registration dates only", "file": "registration.ics"}]
    write_ics(SITE / "calendar.ics", CAL_NAME, events, {"event", "reg"})
    write_ics(SITE / "registration.ics", f"{CAL_NAME}: registration", events, {"reg"})
    for r in regions:
        f = f"calendar-{slug(r)}.ics"
        write_ics(SITE / f, f"{CAL_NAME}: {r}", [e for e in events if e["region"] == r], {"event", "reg"})
        feeds.append({"id": slug(r), "name": r, "file": f})
    payload = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "regions": regions,
               "feeds": feeds, "events": events}
    (SITE / "events.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    template = (ROOT / "web" / "index.template.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    (SITE / "index.html").write_text(template.replace("/*__DATA__*/null", data), encoding="utf-8")
    dated = sum(1 for e in events if e["start"])
    print(f"built {len(events)} events ({dated} with dates), {len(feeds)} feeds -> {SITE}")


if __name__ == "__main__":
    main()
