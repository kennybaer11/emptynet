"""DEL (Germany), scraped from penny-del.org.

No API, but the event list on a game page is better than most APIs: it carries
the goalie in and out to the second, in German and in plain sight.

    57:59  [Straubing Tigers]  Torhueter aus dem Tor : Jakub Skarek (#31)
    58:42  [Eisbaeren Berlin]  Tor ins leere Tor von Eric Mik (#12)
    58:42  [Straubing Tigers]  Torhueter ins Tor : Jakub Skarek (#31)

Times are already cumulative across periods, so 58:42 is 3522 with no
arithmetic. The schedule is paged by month, one page per month per stage.
"""

import logging
import re
from collections import Counter

from ..fetch import get_text
from ..model import EmptyNetWindow, Game, Goal

log = logging.getLogger(__name__)

LEAGUE = "DEL"
SITE = "https://www.penny-del.org"

STAGES = {"regular": "hauptrunde", "playoffs": "playoffs"}
MONTHS = ("september", "oktober", "november", "dezember",
          "januar", "februar", "maerz", "april", "mai")

GOALIE_OUT = "Torhüter aus dem Tor"
GOALIE_IN = "Torhüter ins Tor"
EMPTY_NET_GOAL = "Tor ins leere Tor"

unknown_events = Counter()


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _season_slug(start_year: int) -> str:
    return f"saison-{start_year}-{str(start_year + 1)[2:]}"


def _flatten(raw_html: str) -> str:
    """HTML to a pipe-separated line, with image alt text kept inline."""
    t = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', r"[IMG:\1]", raw_html)
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\|{2,}", "|", t)
    t = re.sub(r"[ \t\n\r]+", " ", t)
    import html as _html
    return _html.unescape(t)


def season_games(start_year: int, stages=("regular", "playoffs")) -> list[dict]:
    games, seen = [], set()
    for stage in stages:
        slug = STAGES.get(stage)
        if not slug:
            continue
        for month in MONTHS:
            url = f"{SITE}/statistik/{_season_slug(start_year)}/{slug}/spielplan/monat/{month}"
            # A stage that has not been played yet answers 403, not 404.
            page = get_text(url, tolerate_404=True, absent=(403, 404))
            if not page:
                continue
            for href in re.findall(r"/statistik/spieldetails/[A-Za-z0-9_\-]+", page):
                game_id = href.rsplit("_", 1)[-1]
                if not game_id.isdigit() or game_id in seen:
                    continue
                if not _in_season(href, start_year):
                    continue      # the site-wide scoreboard strip, not this month
                seen.add(game_id)
                games.append({"game_id": game_id, "stage": stage, "path": href})
    log.info("DEL %s: %d games on the schedule", season_label(start_year), len(games))
    return games


def _in_season(path: str, start_year: int) -> bool:
    """Is this link's game inside the season we are scraping?

    Every page carries a scoreboard strip of the day's games across the top,
    whatever month or season the page itself is about. The date sits in the
    link, so it can say which links belong to the schedule below it.
    """
    m = re.search(r"/spieldetails/(\d{2})(\d{2})(\d{4})_", path)
    if not m:
        return False
    day, month, year = (int(x) for x in m.groups())
    season = year if month >= 8 else year - 1
    return season == start_year


def load_game(meta: dict, start_year: int) -> Game | None:
    url = SITE + meta["path"]
    page = get_text(url, tolerate_404=True)
    if not page:
        return None
    flat = _flatten(page)

    title = re.search(r"<title>(.*?)</title>", page, re.S)
    home, away, played = _teams_from(title.group(1) if title else "", meta["path"])
    if not home:
        log.warning("DEL %s: could not read the teams", meta["game_id"])
        return None

    events = _events(flat, home, away)
    if not events:
        return None       # not played yet, or the page carries no event list

    game = Game(
        league=LEAGUE,
        game_id=meta["game_id"],
        season=season_label(start_year),
        stage=meta["stage"],
        played_at=played,
        home_team=home,
        away_team=away,
        source_url=url,
    )

    open_at: dict[str, int] = {}
    idx = 0
    for t, side, text in events:
        if GOALIE_OUT in text:
            open_at.setdefault(side, t)
        elif GOALIE_IN in text:
            start = open_at.pop(side, None)
            if start is not None:
                game.windows.append(EmptyNetWindow(side, start, t))
        elif re.search(r"\bTor\b(?!hüter)", text):
            idx += 1
            game.goals.append(Goal(
                idx=idx,
                team_side=side,
                game_time=t,
                empty_net=EMPTY_NET_GOAL in text,
                scorer=_scorer(text),
            ))
        else:
            unknown_events[_event_kind(text)] += 1

    end = max((t for t, _, _ in events), default=3600)
    for side, start in open_at.items():
        game.windows.append(EmptyNetWindow(side, start, end))

    game.home_goals = sum(1 for g in game.goals if g.team_side == "home")
    game.away_goals = sum(1 for g in game.goals if g.team_side == "away")
    game.finished_in = "OT" if end > 3600 else "REG"
    return game


def _teams_from(title: str, path: str) -> tuple[str | None, str | None, str | None]:
    """'... - Eisbaeren Berlin gg. Straubing Tigers am 17.09.2026' -> the three fields."""
    m = re.search(r"-\s*([^-]+?)\s+gg\.\s+(.+?)\s+am\s+(\d{2})\.(\d{2})\.(\d{4})", title)
    if m:
        home, away, d, mo, y = m.groups()
        return home.strip(), away.strip(), f"{y}-{mo}-{d}"
    m = re.search(r"/(\d{2})(\d{2})(\d{4})_(.+?)_gg_(.+?)_\d+$", path)
    if m:
        d, mo, y, home, away = m.groups()
        pretty = lambda s: s.replace("-", " ").title()
        return pretty(home), pretty(away), f"{y}-{mo}-{d}"
    return None, None, None


PIPES = r"(?:\s*\|)+\s*"       # flattening leaves runs of pipes between cells
ROW = re.compile(
    rf"(\d{{1,3}}):(\d{{2}}){PIPES}\[IMG:([^\]]*)\]\s*(.*?)"
    rf"(?=\d{{1,3}}:\d{{2}}{PIPES}\[IMG:|$)",
    re.S,
)


def _events(flat: str, home: str, away: str) -> list[tuple[int, str, str]]:
    out = []
    for m in ROW.finditer(flat):
        minutes, seconds, team, text = m.groups()
        t = int(minutes) * 60 + int(seconds)
        team = team.strip()
        if team == home:
            side = "home"
        elif team == away:
            side = "away"
        else:
            continue                       # a sponsor or player photo, not an event row
        out.append((t, side, text))
    out.sort(key=lambda e: e[0])
    return out


def _scorer(text: str) -> str | None:
    m = re.search(r"Tor[^|]*? von ([^(|]+)", text)
    return m.group(1).strip() if m else None


def _event_kind(text: str) -> str:
    words = re.sub(r"[|\[\]]", " ", text).split()
    return " ".join(words[:3]) if words else "(empty)"
