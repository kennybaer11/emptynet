"""Liiga (Finland), from the public liiga.fi API.

Liiga records the answer directly. Each team in a game detail carries
`goalKeeperEvents`, and a pulled goalie shows up as

    {"playerId": 0, "emptyNet": 1, "beginTime": 3552, "endTime": 3600, ...}

with the times already in seconds from the opening faceoff. `timeOut` on the
same object is when that team took its timeout, which is usually the same
whistle the goalie left at.

Season numbers are the year the season ends: 2025 is 2024-25.
"""

import logging

from ..fetch import get_json
from ..model import EmptyNetWindow, Game, Goal

log = logging.getLogger(__name__)

LEAGUE = "LIIGA"
API = "https://liiga.fi/api/v2"

TOURNAMENTS = {"regular": "runkosarja", "playoffs": "playoffs"}

FINISHED = {
    "ENDED_DURING_REGULAR_GAME_TIME": "REG",
    "ENDED_DURING_EXTENDED_GAME_TIME": "OT",
    "ENDED_DURING_WINNING_SHOT_COMPETITION": "SO",
}


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def season_games(start_year: int, stages=("regular", "playoffs")) -> list[dict]:
    season = start_year + 1
    games = []
    for stage in stages:
        tournament = TOURNAMENTS.get(stage)
        if not tournament:
            continue
        url = f"{API}/games?tournament={tournament}&season={season}"
        for g in get_json(url) or []:
            if not g.get("ended"):
                continue
            games.append({
                "game_id": str(g["id"]),
                "stage": stage,
                "played_at": g.get("start"),
                "home_team": g["homeTeam"]["teamName"],
                "away_team": g["awayTeam"]["teamName"],
                "season": season,
            })
    log.info("LIIGA %s: %d finished games", season_label(start_year), len(games))
    return games


def load_game(meta: dict, start_year: int) -> Game | None:
    season = meta.get("season", start_year + 1)
    url = f"{API}/games/{season}/{meta['game_id']}"
    payload = get_json(url, tolerate_404=True)
    if not payload or "game" not in payload:
        log.warning("LIIGA %s: no game detail", meta["game_id"])
        return None
    g = payload["game"]

    game = Game(
        league=LEAGUE,
        game_id=meta["game_id"],
        season=season_label(start_year),
        stage=meta["stage"],
        played_at=g.get("start"),
        home_team=g["homeTeam"]["teamName"],
        away_team=g["awayTeam"]["teamName"],
        home_goals=g["homeTeam"].get("goals"),
        away_goals=g["awayTeam"].get("goals"),
        finished_in=FINISHED.get(g.get("finishedType")),
        source_url=url,
    )

    events = []
    for side in ("home", "away"):
        team = g[f"{side}Team"]
        for ge in team.get("goalEvents") or []:
            events.append((ge["gameTime"], side, ge))
        for ke in team.get("goalKeeperEvents") or []:
            if not ke.get("emptyNet"):
                continue
            game.windows.append(EmptyNetWindow(
                team_side=side,
                start_time=ke["beginTime"],
                end_time=ke.get("endTime"),
                period=ke.get("period"),
            ))

    for idx, (t, side, ge) in enumerate(sorted(events, key=lambda e: e[0]), start=1):
        types = set(ge.get("goalTypes") or [])
        game.goals.append(Goal(
            idx=idx,
            team_side=side,
            game_time=t,
            period=ge.get("period"),
            strength="PP" if types & {"YV", "YV2"} else "SH" if "AV" in types else "EV",
            empty_net="TM" in types,          # tyhjään maaliin
            scorer=_name(ge.get("scorerPlayer")),
        ))
    return game


def _name(player: dict | None) -> str | None:
    if not player:
        return None
    return " ".join(filter(None, (player.get("firstName"), player.get("lastName"))))
