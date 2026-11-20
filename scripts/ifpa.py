"""Tiny IFPA API client. Stdlib only so it runs anywhere (GitHub Actions, cloud routine, laptop).

The API key comes from IFPA_API_KEY. Locally we fall back to the IfpaMaui user-secrets file
so nobody has to copy the key around.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.ifpapinball.com/"
PAGE_SIZE = 50  # the search endpoint rejects large pages
IFPA_MAUI_SECRETS = Path(os.environ.get("APPDATA", "")) / "Microsoft/UserSecrets/dde96872-83d8-431f-9f14-fdd8d175bcd6/secrets.json"


def api_key() -> str:
    key = os.environ.get("IFPA_API_KEY")
    if key:
        return key
    if IFPA_MAUI_SECRETS.exists():
        return json.loads(IFPA_MAUI_SECRETS.read_text(encoding="utf-8-sig"))["AppSettings"]["IfpaApiKey"]
    raise SystemExit("Set IFPA_API_KEY")


def get(path: str, **params) -> dict:
    params["api_key"] = api_key()
    url = BASE + path + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": "pinball-tournaments"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 3:
                body = e.read()[:300].decode(errors="replace")
                raise RuntimeError(f"IFPA {path} -> {e.code}: {body}") from None
        except urllib.error.URLError:
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def search_all(**params) -> list[dict]:
    """Page through tournament/search. Note start_pos is 1-based; 0 is a 400."""
    out: list[dict] = []
    start = 1
    while True:
        r = get("tournament/search", total=PAGE_SIZE, start_pos=start, **params)
        page = r.get("tournaments") or []
        out += page
        start += len(page)
        if not page or start > int(r.get("total_results") or 0):
            return out


def clean_date(s: str | None) -> str | None:
    """IFPA sends '0000-00-00' for unset dates."""
    if not s or s.startswith("0000"):
        return None
    return s[:10]
