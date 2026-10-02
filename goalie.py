#!/usr/bin/env python
"""How often is one goaltender's net the one that gets emptied?

    python goalie.py --name "Pyry Lammi"

The database records that a net was empty, not whose it was, so this reads the
cached Liiga game details, where the goaltenders are named. Who was in net at
the end of regulation comes from three things in that payload, in order of
authority:

  * goalKeeperChanges - a swap during the game names both goalies and the time
  * a goalKeeperEvents entry with emptyNet 0 - the goalie who came back
  * the roster's line 1, which is the starter in 670 of the 680 games where
    the other two agree on an answer to check it against

The counting itself is probability.py's, so a goaltender's numbers and his
club's are produced by the same code.
"""

import argparse
import glob
import json
import logging
from pathlib import Path

from probability import MARKS, tally, wilson

log = logging.getLogger("goalie")
CACHE = Path("cache/liiga.fi")


def game_details():
    for path in glob.glob(str(CACHE / "*.json")):
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(payload, dict) and "game" in payload:
            yield payload


def goalie_at_end(team: dict, roster: list[dict]) -> int | None:
    """The player id of whoever was in net when regulation ran out."""
    changes = team.get("goalKeeperChanges") or []
    if changes:
        return max(changes, key=lambda c: c["time"])["nextGoalie"]

    for event in team.get("goalKeeperEvents") or []:
        if not event.get("emptyNet") and event.get("playerId"):
            return event["playerId"]

    starters = [p for p in roster
                if p.get("role") == "GOALIE" and not p.get("removed")
                and p.get("line") == 1]
    return starters[0]["id"] if len(starters) == 1 else None


def collect(name: str):
    """Everything tally() needs, restricted to the sides this goalie played."""
    wanted = name.casefold()
    games, goals, pulls, sides = {}, {}, {}, {}
    club = set()

    for payload in game_details():
        g = payload["game"]
        game_id = str(g["id"])
        season = g.get("season")
        key = f"{season}-{g['id']}"

        for side, roster_key in (("home", "homeTeamPlayers"),
                                 ("away", "awayTeamPlayers")):
            roster = payload.get(roster_key) or []
            mine = [p for p in roster
                    if f"{p.get('firstName','')} {p.get('lastName','')}".casefold() == wanted]
            if not mine:
                continue
            if goalie_at_end(g[f"{side}Team"], roster) != mine[0]["id"]:
                continue

            club.add(g[f"{side}Team"]["teamName"])
            games[key] = {"season": season, "regulation": 3600,
                          "home": g["homeTeam"]["teamName"],
                          "away": g["awayTeam"]["teamName"]}
            goals[key] = [(ev["gameTime"], s)
                          for s in ("home", "away")
                          for ev in (g[f"{s}Team"].get("goalEvents") or [])]
            pulls[key] = [(s, ev["beginTime"], ev.get("endTime"))
                          for s in ("home", "away")
                          for ev in (g[f"{s}Team"].get("goalKeeperEvents") or [])
                          if ev.get("emptyNet")]
            sides.setdefault(key, set()).add(side)
    return games, goals, pulls, sides, club


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help='e.g. "Pyry Lammi"')
    ap.add_argument("--deficit", type=int, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    games, goals, pulls, sides, club = collect(args.name)
    if not games:
        log.error("no games found with %s in net at the end of regulation", args.name)
        return 2
    print(f"{args.name} ({', '.join(sorted(club))}): in net at the end of "
          f"regulation in {len(games)} games")

    counts = tally(games, goals, pulls, sides=sides)
    shown = [args.deficit] if args.deficit else [1, 2, 3, 4]
    print()
    print(f"{'behind':>6}  {'left':>5}  {'games':>6}  {'net empty':>10}"
          f"  {'pulled while this far back':>27}  {'95% range':>12}")
    for deficit in shown:
        for mark in MARKS:
            row = counts[(mark, deficit)]
            if not row["teams"]:
                continue
            n = row["teams"]
            lo, hi = wilson(row["still_behind"], n)
            label = f"{deficit}+" if deficit == 4 else str(deficit)
            print(f"{label:>6}  {mark // 60}:{mark % 60:02d}  {n:>6}"
                  f"  {100 * row['already'] / n:>9.1f}%"
                  f"  {100 * row['still_behind'] / n:>26.1f}%"
                  f"  {100 * lo:>4.0f}-{100 * hi:<3.0f}%")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
