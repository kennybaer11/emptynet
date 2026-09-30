"""One cached HTTP getter for every source.

A season of play-by-play is a thousand requests you do not want to make twice,
so every response is written to disk under the cache directory and re-read on
the next run. Delete the cache (or pass refresh=True) to go back to the wire.

Misses are cached too. Sheet ids come in blocks with unused stretches of
several hundred between them, and a gap the collector forgets is a gap it pays
for again on every run.
"""

import hashlib
import json
import logging
import os
import re
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

# Politeness delay between live requests, per host. A few hundred game pages
# in a row is enough for some of these sites to start refusing, so the ones
# that have done so get a wider gap.
DELAY = 0.4
DELAY_BY_HOST = {"www.penny-del.org": 1.2}

# A refusal in the middle of a season should cost a wait, not the run.
BACKOFF = (3, 10, 30, 90)
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

_last_call: dict[str, float] = {}


def _path(url: str, suffix: str) -> Path:
    host = url.split("/")[2].replace(":", "_")
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return CACHE_DIR / host / f"{digest}{suffix}"


def _missing(url: str) -> Path:
    """Marker for a URL the server has already told us does not exist."""
    return _path(url, ".missing")


def _mark_missing(url: str) -> None:
    path = _missing(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def forget_missing(url: str) -> None:
    """Undo a remembered 404.

    A miss above the end of a sequence is not a hole, it is a game that has
    not been played yet, and remembering it would hide that game for good.
    A caller that can tell the two apart says so here.
    """
    _missing(url).unlink(missing_ok=True)


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
    wanted = DELAY_BY_HOST.get(host, DELAY)
    gap = time.monotonic() - _last_call.get(host, 0.0)
    if gap < wanted:
        time.sleep(wanted - gap)
    _last_call[host] = time.monotonic()


def _get(url: str, accept: str | None, timeout: int, tolerate_404: bool,
         absent: tuple[int, ...] = (404,)):
    """A single response, retried through a refusal. None means "not there"."""
    headers = {**HEADERS, **({"Accept": accept} if accept else {})}
    last = None

    for attempt in range(len(BACKOFF) + 1):
        _throttle(url)
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = exc
        else:
            if r.status_code in absent and tolerate_404:
                if r.status_code == 404:
                    # Only a 404 is remembered. A stage that answers 403
                    # because it has not been played yet will be played later,
                    # and a cached miss would hide it for good.
                    _mark_missing(url)
                return None
            if r.status_code not in RETRY_STATUS:
                r.raise_for_status()
                _fix_encoding(r)
                return r
            last = requests.HTTPError(f"{r.status_code} for {url}", response=r)

        if attempt < len(BACKOFF):
            wait = BACKOFF[attempt]
            log.warning("%s - waiting %ds then retrying: %s", last, wait, url)
            time.sleep(wait)

    raise last


def get_text(url: str, refresh: bool = False, tolerate_404: bool = False,
             absent: tuple[int, ...] = (404,)) -> str | None:
    """Fetch a URL as text, through the cache. None when the page is missing.

    `absent` is which statuses mean "there is no such page". Not every site
    says 404: penny-del.org answers 403 for a section of a season that has
    not been played yet.
    """
    path = _path(url, ".txt")
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    if tolerate_404 and _missing(url).exists() and not refresh:
        return None

    r = _get(url, None, 60, tolerate_404, absent)
    if r is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    return r.text


def get_json(url: str, refresh: bool = False, tolerate_404: bool = False,
             absent: tuple[int, ...] = (404,)):
    """Fetch a URL and parse it as JSON, through the cache."""
    path = _path(url, ".json")
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("corrupt cache entry, refetching: %s", url)
    if tolerate_404 and _missing(url).exists() and not refresh:
        return None

    r = _get(url, "application/json", 60, tolerate_404, absent)
    if r is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    return r.json()


def get_distilled(url: str, distill, refresh: bool = False,
                  tolerate_404: bool = False,
                  absent: tuple[int, ...] = (404,)) -> str | None:
    """Fetch, reduce to what we actually keep, and cache only the reduction.

    Some game sheets are two megabytes of absolutely-positioned markup around
    forty kilobytes of text. Caching the markup would cost gigabytes for a few
    seasons, so `distill` runs before anything touches the disk.
    """
    path = _path(url, ".distilled.txt")
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    if tolerate_404 and _missing(url).exists() and not refresh:
        return None

    r = _get(url, None, 90, tolerate_404, absent)
    if r is None:
        return None
    text = distill(r.text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
