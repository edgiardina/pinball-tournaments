"""Fetch each catalog event's watch pages and save them as plain text.

The text files in data/pages/ are the single source of truth for extraction: Claude reads them,
and verify.py checks every quote against them. Links are kept inline as "text <url>" so a
registration URL can be verified too.

data/pages/index.json records, per URL: file, sha256 of the text, status, fetched_at, and
changed_at (when the text last differed), so the routine only re-reads pages that changed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "data" / "pages"
UA = "Mozilla/5.0 (compatible; pinball-tournaments-bot/1.0; +https://github.com/edgiardina/pinball-tournaments)"
BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "h1", "h2",
         "h3", "h4", "h5", "h6", "dt", "dd", "blockquote", "hr", "main", "nav", "aside", "td", "th", "form"}
SKIP = {"script", "style", "noscript", "svg", "template", "head"}


class TextExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.out: list[str] = []
        self.skip_depth = 0
        self.href_stack: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip_depth += 1
        elif tag in BLOCK:
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            ok = href and not href.startswith(("#", "javascript:", "mailto:", "tel:"))
            self.href_stack.append(urljoin(self.base, href) if ok else None)

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in BLOCK:
            self.out.append("\n")
        if tag == "a" and self.href_stack:
            href = self.href_stack.pop()
            if href:
                self.out.append(f" <{href}> ")

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(data)


EMBEDDED_HEADER = "[Text from the page's embedded data. The page draws it with JavaScript.]"
MIN_VISIBLE_WORDS = 40


def embedded_strings(raw: str) -> list[str]:
    """Human text inside script data, for sites (Canva, some builders) whose HTML has none.

    Canva nests JSON inside a JS string, so a string can be escaped two or three times deep.
    Keep anything that reads like copy: a digit or at least four words, and no code/URL bits.
    """
    seen, out = set(), []
    for m in re.finditer(r'"((?:[^"\\]|\\.){12,})"', raw):
        s = m.group(1)
        for _ in range(3):
            try:
                s2 = json.loads(f'"{s}"')
            except ValueError:
                break
            if s2 == s:
                break
            s = s2
        s = s.strip()
        if (s in seen or re.search(r"https?://|[{};=<>]|^_|\.(js|css)\b", s)
                or not re.search(r"[A-Za-z]{3,} [A-Za-z]{2,}", s)
                or not (re.search(r"\d", s) or len(s.split()) >= 4)):
            continue
        seen.add(s)
        out.append(s)
    return out


def html_to_text(raw: str, base_url: str) -> str:
    p = TextExtractor(base_url)
    p.feed(raw)
    text = html.unescape("".join(p.out)).replace("\xa0", " ")
    if len(text.split()) < MIN_VISIBLE_WORDS and (extra := embedded_strings(raw)):
        text += "\n\n" + EMBEDDED_HEADER + "\n\n" + "\n\n".join(extra)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in text.split("\n")]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip() + "\n"


def page_file(event_id: str, n: int) -> str:
    return f"{event_id}--{n}.txt"


def fetch(url: str) -> tuple[str, str]:
    """Returns (status, text). status is 'ok', 'blocked', 'http-NNN' or 'error'."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                                               "Accept-Language": "en;q=0.9,*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            raw = r.read(5_000_000).decode(charset, errors="replace")
            final = r.geturl()
    except urllib.error.HTTPError as e:
        body = e.read(20000).decode(errors="replace")
        if e.code in (403, 503) and ("Just a moment" in body or "cf-chl" in body or "challenge" in body.lower()):
            return "blocked", ""
        return f"http-{e.code}", ""
    except Exception as e:  # DNS, TLS, timeouts: all just "couldn't read it tonight"
        return "error", f"{type(e).__name__}: {e}"
    if "Just a moment..." in raw and "cf-chl" in raw:
        return "blocked", ""
    return "ok", html_to_text(raw, final)


def main(only: list[str] | None = None) -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))["events"]
    idx_path = PAGES / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for ev in catalog:
        if only and ev["id"] not in only:
            continue
        for n, url in enumerate(ev.get("watch", [])):
            status, text = fetch(url)
            entry = index.get(url, {})
            entry.update({"event_id": ev["id"], "file": page_file(ev["id"], n), "status": status, "fetched_at": now})
            if status == "ok":
                sha = hashlib.sha256(text.encode()).hexdigest()
                if sha != entry.get("sha256"):
                    entry["changed_at"] = now
                    entry["sha256"] = sha
                    (PAGES / entry["file"]).write_text(text, encoding="utf-8")
                entry["last_ok_at"] = now
            else:
                entry["error"] = text[:200] if status == "error" else None
            index[url] = entry
            print(f'{status:10} {ev["id"]:14} {url}')
    idx_path.write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
