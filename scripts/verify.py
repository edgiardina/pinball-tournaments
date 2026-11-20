"""Check Claude's extracted claims against the saved page text, then merge the good ones into state.

A claim is only accepted when code can prove it from the page:
  * the quote appears verbatim (whitespace/dash/quote-mark normalized) in the saved text of its URL,
  * dates and times in the value are visibly present in the quote (day, month, year if stated, hour),
  * the result is sane (edition hasn't ended, reg opens before the event, and so on).

Anything that fails is dropped and logged; the last good value stays. Nothing here needs a human.

Claims live in data/claims/<event_id>.json:
{
  "event_id": "yegpin",
  "claims": [
    {"field": "event_start", "value": "2027-05-13", "quote": "...", "url": "https://..."},
    {"field": "reg_opens", "value": "2027-02-01T12:00", "tz": "America/Edmonton", "quote": "...", "url": "..."}
  ]
}
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DATE_FIELDS = {"event_start", "event_end", "reg_opens", "reg_closes"}
ENUMS = {
    "reg_status": {"not_open", "open", "full", "waitlist", "closed"},
    "reg_method": {"first_come", "lottery", "qualifier", "invite", "walk_up"},
}
TEXT_FIELDS = {"headline"}  # shown verbatim; value must equal the quote
URL_FIELDS = {"reg_url"}
INT_FIELDS = {"player_cap"}
ALL_FIELDS = DATE_FIELDS | set(ENUMS) | TEXT_FIELDS | URL_FIELDS | INT_FIELDS
NO_YEAR_WINDOW_DAYS = 120

# Month names we accept as evidence, by month number. English plus the languages our events use.
MONTHS = {
    1: ["january", "jan", "januar", "januari", "janvier", "janv", "enero", "gennaio", "tammikuu", "január", "stycznia", "styczeń"],
    2: ["february", "feb", "februar", "februari", "février", "fevrier", "févr", "febrero", "febbraio", "helmikuu", "február", "lutego", "luty"],
    3: ["march", "mar", "märz", "maerz", "mars", "maart", "marzo", "maaliskuu", "március", "marca", "marzec"],
    4: ["april", "apr", "avril", "abril", "aprile", "huhtikuu", "április", "kwietnia", "kwiecień"],
    5: ["may", "mai", "maj", "mei", "mayo", "maggio", "toukokuu", "május", "maja"],
    6: ["june", "jun", "juni", "juin", "junio", "giugno", "kesäkuu", "június", "czerwca", "czerwiec"],
    7: ["july", "jul", "juli", "juillet", "julio", "luglio", "heinäkuu", "július", "lipca", "lipiec"],
    8: ["august", "aug", "augusti", "août", "aout", "augustus", "agosto", "elokuu", "augusztus", "sierpnia", "sierpień"],
    9: ["september", "sep", "sept", "septembre", "septiembre", "settembre", "syyskuu", "szeptember", "września", "wrzesień"],
    10: ["october", "oct", "oktober", "okt", "octobre", "octubre", "ottobre", "lokakuu", "október", "października", "październik"],
    11: ["november", "nov", "novembre", "noviembre", "marraskuu", "listopada", "listopad"],
    12: ["december", "dec", "dezember", "dez", "decembre", "décembre", "diciembre", "dicembre", "joulukuu", "grudnia", "grudzień"],
}


def norm(s: str) -> str:
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def words(s: str) -> set[str]:
    return set(re.findall(r"[^\W\d_]+", s.lower()))


def has_day(q: str, day: int) -> bool:
    return re.search(rf"(?<!\d)0?{day}(?!\d)", q) is not None


def has_month(q: str, month: int) -> bool:
    w = words(q)
    if any(name in w for name in MONTHS[month]):
        return True
    # Numeric forms: 13.05. / 13/5 / 5/13 / 2027-05-13
    return re.search(rf"(?<!\d)0?{month}(?!\d)", re.sub(r"(?<!\d)(19|20)\d\d(?!\d)", " ", q)) is not None and \
        re.search(r"\d{1,2}\s*[./-]\s*\d{1,2}", q) is not None


def has_hour(q: str, hour: int, minute: int) -> bool:
    if hour == 12 and minute == 0 and re.search(r"\bnoon\b", q):
        return True
    if hour == 0 and minute == 0 and re.search(r"\bmidnight\b", q):
        return True
    h12 = hour % 12 or 12
    pats = [rf"(?<!\d)0?{hour}[:.h]{minute:02d}", rf"(?<!\d){hour}\s*(uhr|h\b)"]
    ampm = "p" if hour >= 12 else "a"
    pats += [rf"(?<!\d){h12}(:{minute:02d})?\s*{ampm}\.?m?\b"]
    return any(re.search(p, q) for p in pats)


class Reject(Exception):
    pass


def parse_when(value: str) -> tuple[dt.date, tuple[int, int] | None]:
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}))?", value or "")
    if not m:
        raise Reject(f"bad date format {value!r}")
    d = dt.date.fromisoformat(m.group(1))
    return d, ((int(m.group(2)), int(m.group(3))) if m.group(2) else None)


def check_claim(c: dict, pages: dict[str, str], today: dt.date) -> dict:
    field, value, quote, url = c.get("field"), c.get("value"), c.get("quote") or "", c.get("url")
    if field not in ALL_FIELDS:
        raise Reject(f"unknown field {field!r}")
    if url not in pages:
        raise Reject(f"no saved page for {url!r}")
    q = norm(quote)
    if len(q) < 4 or len(q) > 600:
        raise Reject("quote length")
    if q not in norm(pages[url]):
        raise Reject("quote not found on page")

    if field in DATE_FIELDS:
        d, hm = parse_when(value)
        if not has_day(q, d.day):
            raise Reject(f"day {d.day} not in quote")
        if not has_month(q, d.month):
            raise Reject(f"month {d.month} not in quote")
        years = {int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", q)}
        if years and d.year not in years:
            raise Reject(f"year {d.year} not in quote years {sorted(years)}")
        # A year-less date ("opens 6/20 @ noon") is usually left over from last year's page. Only trust
        # one that's close, since organizers name the year when they post far ahead.
        if not years and not (today - dt.timedelta(days=60) <= d <= today + dt.timedelta(days=NO_YEAR_WINDOW_DAYS)):
            raise Reject(f"no year in quote and date is more than {NO_YEAR_WINDOW_DAYS} days out")
        if hm and not has_hour(q, *hm):
            raise Reject(f"time {hm[0]:02d}:{hm[1]:02d} not in quote")
        if hm:
            ZoneInfo(c.get("tz") or "UTC")  # raises on a bad tz name
    elif field in ENUMS:
        if value not in ENUMS[field]:
            raise Reject(f"{field} must be one of {sorted(ENUMS[field])}")
    elif field in TEXT_FIELDS:
        if norm(value or "") != q:
            raise Reject("headline must be the quote itself")
    elif field in URL_FIELDS:
        if not re.match(r"https?://", value or "") or value not in pages[url]:
            raise Reject("reg_url not on page")
    elif field in INT_FIELDS:
        if not isinstance(value, int) or str(value) not in re.sub(r"[,.\s](?=\d{3})", "", q):
            raise Reject("player_cap not in quote")
    return {k: c[k] for k in ("field", "value", "quote", "url", "tz") if c.get(k) is not None}


def sanity(fields: dict[str, dict], today: dt.date) -> list[str]:
    """Cross-field checks on the merged result. Returns names of fields to drop."""
    drop = []
    get = lambda f: parse_when(fields[f]["value"])[0] if f in fields else None  # noqa: E731
    start, end = get("event_start"), get("event_end")
    if start and end and end < start:
        drop += ["event_end"]
        end = None
    last_day = end or start
    if last_day and last_day < today:
        # Page still shows the edition that already happened: none of it is useful now.
        return [f for f in fields if f != "headline"]
    if start and get("reg_opens") and get("reg_opens") > start:
        drop.append("reg_opens")
    if last_day and get("reg_closes") and get("reg_closes") > last_day:
        drop.append("reg_closes")
    return drop


def load_pages() -> dict[str, str]:
    index = json.loads((DATA / "pages" / "index.json").read_text(encoding="utf-8"))
    out = {}
    for url, e in index.items():
        f = DATA / "pages" / e["file"]
        if f.exists():
            out[url] = f.read_text(encoding="utf-8")
    return out


def main() -> int:
    today = dt.date.today()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    pages = load_pages()
    index = json.loads((DATA / "pages" / "index.json").read_text(encoding="utf-8"))
    readable = {u for u, e in index.items() if e.get("status") == "ok"}
    state_path = DATA / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"events": {}}
    rejected_total = 0
    for path in sorted((DATA / "claims").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        eid = doc["event_id"]
        st = state["events"].setdefault(eid, {"fields": {}})
        accepted, rejected = {}, []
        for c in doc.get("claims", []):
            try:
                good = check_claim(c, pages, today)
                accepted[good["field"]] = good
            except (Reject, ValueError, KeyError) as e:
                rejected.append({"field": c.get("field"), "value": c.get("value"), "reason": str(e)})
        # A claims file is a full snapshot of what the pages say now. Old values survive only when
        # their page couldn't be read this time; otherwise "registration open" would outlive a sellout.
        kept = {f: g for f, g in st["fields"].items() if f not in accepted and g.get("url") not in readable}
        merged = {**kept, **{f: {**st["fields"].get(f, {}), **g} for f, g in accepted.items()}}
        for f in sanity(merged, today):
            if f in accepted:
                rejected.append({"field": f, "value": accepted[f]["value"], "reason": "failed cross-field sanity"})
            merged.pop(f, None)
        for f, g in merged.items():
            old = st["fields"].get(f)
            if f in accepted:
                g["verified_at"] = now
                if not old:
                    g["first_seen_at"] = now
                elif old.get("value") != g["value"]:
                    g["first_seen_at"] = now
                    g["previous"] = {"value": old.get("value"), "replaced_at": now}
        st["fields"] = merged
        st["checked_at"] = doc.get("checked_at") or now
        st["rejected"] = rejected
        rejected_total += len(rejected)
        print(f"{eid:16} accepted {len(accepted)}  rejected {len(rejected)}" +
              "".join(f"\n    x {r['field']}={r['value']!r}: {r['reason']}" for r in rejected))
    state["verified_at"] = now
    state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
