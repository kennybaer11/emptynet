"""NHL, from the public api-web.nhle.com feed.

The gift here is `situationCode`, carried on every play. Four digits:

    1551  ->  away goalie in, away 5 skaters, home 5 skaters, home goalie in
    0651  ->  away goalie PULLED, away 6 skaters, home 5, home goalie in

So an empty net is simply a run of plays whose first or last digit is 0, and
the pull is timed to the first play that shows it. That is stoppage-accurate
rather than second-accurate, which for a tactical pull - which happens at a
whistle or on a change - is the same thing nearly every time.
"""

import logging
from datetime import date, timedelta

from ..fetch import get_json
from ..model import EmptyNetWindow, Game, Goal

log = logging.getLogger(__name__)

LEAGUE = "NHL"
API = "https://api-web.nhle.com/v1"

STAGE = {1: "preseason", 2: "regular", 3: "playoffs"}


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _time_to_seconds(period: int, clock: str) -> int:
    """'18:52' in period 3 -> 3532. Overtime keeps counting past 3600."""
    minutes, seconds = clock.split(":")
    return (period - 1) * 1200 + int(minutes) * 60 + int(seconds)


def season_games(start_year: int, stages=("regular", "playoffs")) -> list[dict]:
    """Every finished game of a season, walked a week at a time."""
    games, seen = [], set()
    cursor = date(start_year, 9, 1)
    stop = date(start_year + 1, 7, 15)

    while cursor <= stop:
        payload = get_json(f"{API}/schedule/{cursor.isoformat()}")
        for day in payload.get("gameWeek", []):
            for g in day.get("games", []):
                if g["id"] in seen:
                    continue
                seen.add(g["id"])
                stage = STAGE.get(g.get("gameType"), "other")
                if stage not in stages:
                    continue
                if g.get("gameState") not in ("OFF", "FINAL"):
                    continue
                games.append({
                    "game_id": str(g["id"]),
                    "stage": stage,
                    "played_at": g.get("startTimeUTC"),
                    "home_team": g["homeTeam"]["abbrev"],
                    "away_team": g["awayTeam"]["abbrev"],
                })
        nxt = payload.get("nextStartDate")
        cursor = date.fromisoformat(nxt) if nxt else cursor + timedelta(days=7)

    log.info("NHL %s: %d finished games", season_label(start_year), len(games))
    return games


def load_game(meta: dict, start_year: int) -> Game | None:
    url = f"{API}/gamecenter/{meta['game_id']}/play-by-play"
    pbp = get_json(url, tolerate_404=True)
    if not pbp or not pbp.get("plays"):
        log.warning("NHL %s: no play-by-play", meta["game_id"])
        return None

    home_id = pbp["homeTeam"]["id"]
    side_of = {home_id: "home", pbp["awayTeam"]["id"]: "away"}

    game = Game(
        league=LEAGUE,
        game_id=meta["game_id"],
        season=season_label(start_year),
        stage=meta["stage"],
        played_at=meta["played_at"],
        home_team=meta["home_team"],
        away_team=meta["away_team"],
        home_goals=pbp["homeTeam"].get("score"),
        away_goals=pbp["awayTeam"].get("score"),
        finished_in=_finished_in(pbp),
        source_url=url,
    )

    plays = sorted(pbp["plays"], key=lambda p: p.get("sortOrder", 0))

    idx = 0
    for p in plays:
        if p.get("typeDescKey") != "goal":
            continue
        idx += 1
        d = p.get("details", {})
        period = p["periodDescriptor"]["number"]
        game.goals.append(Goal(
            idx=idx,
            team_side=side_of.get(d.get("eventOwnerTeamId"), "home"),
            game_time=_time_to_seconds(period, p["timeInPeriod"]),
            period=period,
            strength=None,
            empty_net="goalieInNetId" not in d,
            scorer=str(d.get("scoringPlayerId") or ""),
        ))

    game.windows = _windows(plays)
    return game


def _finished_in(pbp: dict) -> str | None:
    last = pbp.get("periodDescriptor", {}).get("periodType")
    return {"REG": "REG", "OT": "OT", "SO": "SO"}.get(last)


def _windows(plays: list[dict]) -> list[EmptyNetWindow]:
    """Runs of plays where a situationCode shows a goalie off the ice."""
    open_at: dict[str, tuple[int, int]] = {}     # side -> (start_time, period)
    out: list[EmptyNetWindow] = []
    last_time = 0

    for p in plays:
        code = p.get("situationCode")
        if not code or len(code) != 4 or p.get("timeInPeriod") is None:
            continue
        period = p["periodDescriptor"]["number"]
        if p["periodDescriptor"].get("periodType") == "SO":
            break                                  # the shootout has no net to empty
        t = _time_to_seconds(period, p["timeInPeriod"])
        last_time = max(last_time, t)

        for side, digit in (("away", code[0]), ("home", code[3])):
            empty = digit == "0"
            if empty and side not in open_at:
                open_at[side] = (t, period)
            elif not empty and side in open_at:
                start, per = open_at.pop(side)
                out.append(EmptyNetWindow(side, start, t, per))

    for side, (start, per) in open_at.items():
        out.append(EmptyNetWindow(side, start, last_time or None, per))
    return out
