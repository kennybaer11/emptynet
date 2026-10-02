#!/usr/bin/env python
"""How likely is a team to pull its goalie, given how far behind it is?

    python probability.py --league LIIGA
    python probability.py --league NHL --deficit 3
    python probability.py --league LIIGA --team SaiPa --deficit 3

The pull table alone cannot answer this: it holds the pulls that happened,
not the situations where a team could have pulled and did not. That
denominator is rebuilt here from the goal times - at a given moment in the
third period, every team is behind by some number of goals, and either has its
goalie in the net or does not.
"""

import argparse
import logging
import math
from collections import defaultdict

from dotenv import load_dotenv

from emptynet import db

log = logging.getLogger("probability")

# Moments to look at, as seconds of regulation still to play.
MARKS = (300, 180, 120, 60, 30)

Z = 1.96          # 95%


def wilson(hits: int, n: int) -> tuple[float, float]:
    """95% interval for a rate, the way that behaves at small n.

    One club's own three-goal deficits number in the dozens, not the hundreds,
    and a bare percentage over fifteen games invites conclusions the data
    cannot carry. The interval is the point of the exercise, not decoration.
    """
    if not n:
        return 0.0, 0.0
    p = hits / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = Z / denom * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return max(0.0, centre - half), min(1.0, centre + half)


def load(conn, league: str, seasons: list[str] | None):
    where = "WHERE league = %s" + (" AND season = ANY(%s)" if seasons else "")
    args = (league, seasons) if seasons else (league,)

    games, goals, pulls = {}, defaultdict(list), defaultdict(list)
    with conn.cursor() as cur:
        cur.execute(f"SELECT game_id, season, regulation_seconds, home_team, away_team"
                    f" FROM emptynet.game {where}", args)
        for game_id, season, regulation, home, away in cur.fetchall():
            games[game_id] = {"season": season, "regulation": regulation,
                              "home": home, "away": away}

        cur.execute(f"SELECT game_id, team_side, game_time FROM emptynet.goal {where}", args)
        for game_id, side, t in cur.fetchall():
            goals[game_id].append((t, side))

        cur.execute(
            f"SELECT game_id, team_side, start_time, end_time FROM emptynet.pull {where}"
            " AND kind = 'trailing'", args)
        for game_id, side, start, end in cur.fetchall():
            pulls[game_id].append((side, start, end))
    return games, goals, pulls


def deficit_at(game_goals, side: str, t: int) -> int:
    """Goals behind at time t. Positive means behind."""
    mine = sum(1 for gt, s in game_goals if s == side and gt <= t)
    theirs = sum(1 for gt, s in game_goals if s != side and gt <= t)
    return theirs - mine


def tally(games, goals, pulls, team: str | None = None, sides=None):
    """For each mark and deficit: how many teams were there, and what they did.

    With `team` given, only that club's own side of each game is counted.
    With `sides` given - a mapping of game id to the sides to count - only
    those are, which is how a question about one goaltender is asked.
    """
    counts = defaultdict(lambda: {"teams": 0, "already": 0, "by_horn": 0,
                                  "still_behind": 0})

    for game_id, game in games.items():
        regulation = game["regulation"]
        game_goals = goals.get(game_id, [])
        game_pulls = pulls.get(game_id, [])

        for side in ("home", "away"):
            if team and game[side].casefold() != team.casefold():
                continue
            if sides is not None and side not in sides.get(game_id, ()):
                continue
            mine = [(s, a, b) for s, a, b in game_pulls if s == side]
            for mark in MARKS:
                t = regulation - mark
                behind = deficit_at(game_goals, side, t)
                if behind <= 0:
                    continue
                key = (mark, min(behind, 4))
                row = counts[key]
                row["teams"] += 1
                if any(a <= t <= (b if b is not None else regulation) for _, a, b in mine):
                    row["already"] += 1
                    row["by_horn"] += 1
                elif any(a > t for _, a, b in mine):
                    row["by_horn"] += 1

                # Pulling later is not the same as pulling at this deficit: a
                # team three down at five minutes often scores twice first and
                # pulls at one. Only a pull taken while still this far behind
                # answers "do they pull when they are this far behind".
                if any(a >= t and deficit_at(game_goals, side, a) == behind
                       for _, a, b in mine):
                    row["still_behind"] += 1
    return counts


def by_deficit(conn, league: str, seasons: list[str] | None, team: str | None = None):
    """The pulls that did happen, grouped by how far behind the team was."""
    where = "WHERE p.league = %s AND p.kind = 'trailing'"
    args = [league]
    if seasons:
        where += " AND g.season = ANY(%s)"
        args.append(seasons)
    if team:
        where += (" AND ((p.team_side = 'home' AND g.home_team = %s)"
                  " OR (p.team_side = 'away' AND g.away_team = %s))")
        args += [team, team]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT LEAST(-p.score_diff, 4) AS behind,
                   count(*),
                   round(avg(p.time_left)),
                   round(avg(p.seconds_empty)),
                   round(100.0 * count(*) FILTER (WHERE p.outcome = 'scored') / count(*), 1),
                   round(100.0 * count(*) FILTER (WHERE p.outcome = 'conceded') / count(*), 1)
            FROM emptynet.pull p
            JOIN emptynet.game g USING (league, game_id)
            {where}
            GROUP BY 1 ORDER BY 1
        """, args)
        return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", default="LIIGA")
    ap.add_argument("--team", default=None, help="one club, by the name in the data")
    ap.add_argument("--seasons", default=None, help="comma separated, e.g. 2024-25,2025-26")
    ap.add_argument("--deficit", type=int, default=None, help="report only this deficit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_dotenv()
    conn = db.connect()
    seasons = args.seasons.split(",") if args.seasons else None
    games, goals, pulls = load(conn, args.league.upper(), seasons)
    conn.close()

    if args.team:
        known = {g[side] for g in games.values() for side in ("home", "away")}
        match = [k for k in known if k.casefold() == args.team.casefold()]
        if not match:
            log.error("no club called %r in %s - the names are: %s",
                      args.team, args.league.upper(), ", ".join(sorted(known)))
            return 2
        args.team = match[0]
        games = {k: v for k, v in games.items()
                 if args.team in (v["home"], v["away"])}

    print(f"{args.league.upper()}{' - ' + args.team if args.team else ''}:"
          f" {len(games)} games" + (f", seasons {args.seasons}" if seasons else ""))

    counts = tally(games, goals, pulls, args.team)
    shown = [args.deficit] if args.deficit else [1, 2, 3, 4]

    print()
    print(f"{'behind':>6}  {'left':>5}  {'teams':>6}  {'net empty':>10}"
          f"  {'pulls still this far back':>25}  {'95% range':>14}"
          f"  {'by the horn':>12}")
    for deficit in shown:
        for mark in MARKS:
            row = counts[(mark, deficit)]
            if not row["teams"]:
                continue
            label = f"{deficit}+" if deficit == 4 else str(deficit)
            n = row["teams"]
            lo, hi = wilson(row["still_behind"], n)
            print(f"{label:>6}  {mark // 60}:{mark % 60:02d}  {n:>6}"
                  f"  {100 * row['already'] / n:>9.1f}%"
                  f"  {100 * row['still_behind'] / n:>24.1f}%"
                  f"  {100 * lo:>5.0f}-{100 * hi:<3.0f}%"
                  f"  {100 * row['by_horn'] / n:>11.1f}%")
        print()

    conn = db.connect()
    rows = by_deficit(conn, args.league.upper(), seasons, args.team)
    conn.close()
    print("the pulls that did happen, by how far behind the team was")
    print(f"{'behind':>6}  {'pulls':>6}  {'left':>6}  {'empty':>6}  {'scored':>7}  {'conceded':>8}")
    for behind, n, left, empty, scored, conceded in rows:
        label = f"{behind}+" if behind == 4 else str(behind)
        print(f"{label:>6}  {n:>6}  {int(left) // 60}:{int(left) % 60:02d}"
              f"  {int(empty):>4}s  {scored:>6}%  {conceded:>7}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
