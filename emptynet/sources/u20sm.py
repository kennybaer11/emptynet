"""U20 SM-sarja (Finland), from the Finnish association's GameCentre.

The league is not on the Liiga API - liiga.fi answers 403 for every tournament
that is not its own - but tulospalvelu.leijonat.fi, which runs the
association's competitions, has a JSON feed behind its own front end, and its
game report is the most explicit of the five:

    {"Type": "Timeout", "TeamId": 27404470, "GameTime": 3465}
    {"Type": "GK_out",  "TeamId": 27404470, "GameTime": 3475, ...}
    {"Type": "Goal",    "TeamId": 27404470, "GameTime": 3549, "GoalType": "YV IM"}
    {"Type": "GK_in",   "TeamId": 27404470, "GameTime": 3549, ...}

Seasons are numbered by the year they end: 2026 is 2025-26. Sub-series ids
change every season, so they are looked up rather than hard coded, and the
whole season's schedule comes back in a single call with gamedays=1.
"""

import logging

from ..fetch import get_json
from ..model import EmptyNetWindow, Game, Goal

log = logging.getLogger(__name__)

LEAGUE = "U20SM"
SITE = "https://tulospalvelu.leijonat.fi"
LEVEL_ID = 77          # U20 SM-sarja
DISTRICT_ID = 9

SUBSERIES = f"{SITE}/serie/helpers/getsubseries?season={{season}}&levelid={LEVEL_ID}&districtid={DISTRICT_ID}"
GAMES = (f"{SITE}/helpers/getgames?dwl=0&season={{season}}&subSerieId={{ssid}}"
         f"&teamid=0&districtid=0&gamedays=1&dog={{dog}}&levelid=-1")
REPORT = f"{SITE}/gamereport/getgamereportdata?gameid={{gid}}&season={{season}}"

FINISHED = {1: "REG", 2: "OT", 3: "SO"}


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _stage_of(subserie_name: str) -> str:
    name = subserie_name.lower()
    if "pudotuspeli" in name:
        return "playoffs"
    if "karsinta" in name:
        return "qualifiers"
    return "regular"


def season_games(start_year: int, stages=("regular", "playoffs")) -> list[dict]:
    season = start_year + 1
    subseries = get_json(SUBSERIES.format(season=season), tolerate_404=True) or []

    games = []
    for sub in subseries:
        stage = _stage_of(sub.get("subSerieName", ""))
        if stage not in stages:
            continue
        payload = get_json(
            GAMES.format(season=season, ssid=sub["subSerieId"], dog=f"{start_year}-09-01"),
            tolerate_404=True,
        ) or []
        for level in payload:
            for g in level.get("Games", []):
                if g.get("HomeGoals") is None or not g.get("GameStatus"):
                    continue                     # not played
                games.append({
                    "game_id": str(g["GameID"]),
                    "stage": stage,
                    "season": season,
                    "played_at": g.get("GameDateDB"),
                    "home_team": g.get("HomeTeamAbbrv"),
                    "away_team": g.get("AwayTeamAbbrv"),
                    "home_id": g.get("HomeTeam"),
                    "away_id": g.get("AwayTeam"),
                    "home_goals": g.get("HomeGoals"),
                    "away_goals": g.get("AwayGoals"),
                    "finished_type": g.get("FinishedType"),
                })
    log.info("U20SM %s: %d finished games", season_label(start_year), len(games))
    return games


def load_game(meta: dict, start_year: int) -> Game | None:
    season = meta.get("season", start_year + 1)
    url = REPORT.format(gid=meta["game_id"], season=season)
    payload = get_json(url, tolerate_404=True)
    if not payload or not payload.get("GameLogsUpdate"):
        return None

    side_of = {meta["home_id"]: "home", meta["away_id"]: "away"}
    game = Game(
        league=LEAGUE,
        game_id=meta["game_id"],
        season=season_label(start_year),
        stage=meta["stage"],
        played_at=meta.get("played_at"),
        home_team=meta["home_team"],
        away_team=meta["away_team"],
        home_goals=meta.get("home_goals"),
        away_goals=meta.get("away_goals"),
        finished_in=FINISHED.get(meta.get("finished_type")),
        source_url=url,
    )

    logs = sorted(payload["GameLogsUpdate"], key=lambda e: (e.get("GameTime") or 0))
    open_at: dict[str, int] = {}
    idx = 0

    for entry in logs:
        side = side_of.get(entry.get("TeamId"))
        if side is None:
            continue
        t = entry.get("GameTime")
        if t is None:
            continue
        kind = entry.get("Type")

        if kind == "GK_out":
            open_at.setdefault(side, t)
        elif kind in ("GK_in", "GK_change"):
            start = open_at.pop(side, None)
            if start is not None:
                game.windows.append(EmptyNetWindow(side, start, t, entry.get("Period")))
        elif kind == "Goal":
            types = set((entry.get("GoalType") or "").split())
            if "VL" in types:
                continue                     # a shootout winner, not a goal in play
            idx += 1
            game.goals.append(Goal(
                idx=idx,
                team_side=side,
                game_time=t,
                period=entry.get("Period"),
                strength="PP" if types & {"YV", "YV2"} else "SH" if "AV" in types else "EV",
                empty_net="TM" in types,
                scorer=entry.get("ScorerName"),
            ))

    end = max((e.get("GameTime") or 0 for e in logs), default=3600)
    for side, start in open_at.items():
        game.windows.append(EmptyNetWindow(side, start, end))
    return game
