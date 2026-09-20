"""One cached HTTP getter for every source.

A season of play-by-play is a thousand requests you do not want to make twice,
so every response is written to disk under the cache directory and re-read on
the next run. Delete the cache (or pass refresh=True) to go back to the wire.
"""

import hashlib
import json
import re
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("EMPTYNET_CACHE") or
                 Path(__file__).resolve().parent.parent / "cache")

HEADERS = {
    "User-Agent": "emptynet/0.1 (hockey goalie-pull research; contact via github)",
    "Accept-Language": "en,fi;q=0.8,de;q=0.8",
}

# Politeness delay between live requests, per host.
DELAY = 0.4
_last_call = {}


def _path(url: str, suffix: str) -> Path:
    host = url.split("/")[2].replace(":", "_")
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return CACHE_DIR / host / f"{digest}{suffix}"


def _fix_encoding(response) -> None:
    """Believe the document, not requests' fallback.

    With no charset in the Content-Type header requests falls back to
    ISO-8859-1, which turns every umlaut on penny-del.org into a question
    mark. Every source here declares UTF-8 in the markup instead.
    """
    if "charset=" in response.headers.get("Content-Type", "").lower():
        return
    declared = re.search(rb'charset=[\"\']?([\w\-]+)', response.content[:2048], re.I)
    response.encoding = declared.group(1).decode() if declared else "utf-8"


def _throttle(url: str) -> None:
    host = url.split("/")[2]
    gap = time.monotonic() - _last_call.get(host, 0.0)
    if gap < DELAY:
        time.sleep(DELAY - gap)
    _last_call[host] = time.monotonic()


def get_text(url: str, refresh: bool = False, tolerate_404: bool = False) -> str | None:
    """Fetch a URL as text, through the cache. None when the page is missing."""
    path = _path(url, ".txt")
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")

    _throttle(url)
    log.debug("GET %s", url)
    r = requests.get(url, headers=HEADERS, timeout=60)
    if r.status_code == 404 and tolerate_404:
        return None
    r.raise_for_status()
    _fix_encoding(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    return r.text


def get_json(url: str, refresh: bool = False, tolerate_404: bool = False):
    """Fetch a URL and parse it as JSON, through the cache."""
    path = _path(url, ".json")
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("corrupt cache entry, refetching: %s", url)

    _throttle(url)
    log.debug("GET %s", url)
    r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=60)
    if r.status_code == 404 and tolerate_404:
        return None
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    return r.json()


def get_distilled(url, distill, refresh: bool = False, tolerate_404: bool = False) -> str | None:
    """Fetch, reduce to what we actually keep, and cache only the reduction.

    Some game sheets are two megabytes of absolutely-positioned markup around
    forty kilobytes of text. Caching the markup would cost gigabytes for a few
    seasons, so `distill` runs before anything touches the disk.
    """
    path = _path(url, ".distilled.txt")
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")

    _throttle(url)
    log.debug("GET %s", url)
    r = requests.get(url, headers=HEADERS, timeout=90)
    if r.status_code == 404 and tolerate_404:
        return None
    r.raise_for_status()
    _fix_encoding(r)
    text = distill(r.text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
