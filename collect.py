#!/usr/bin/env python
"""Collect goalie pulls.

    python collect.py --leagues NHL,LIIGA --seasons 2023,2024,2025
    python collect.py --leagues DEL --seasons 2025 --dry-run

Seasons are named by the year they start in: 2024 is 2024-25. Every response
is cached under ./cache, so a second run costs nothing and a run that dies
halfway resumes where it stopped. Games already in the database are skipped
unless --refresh is given.
"""

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

from emptynet import db
from emptynet.model import classify
from emptynet.sources import SOURCES

log = logging.getLogger("collect")


def collect_season(conn, league: str, start_year: int, stages, dry_run: bool,
                   refresh: bool) -> dict:
    source = SOURCES[league]
    label = source.season_label(start_year)
    metas = source.season_games(start_year, stages=stages)

    already = set() if (dry_run or refresh) else db.known_game_ids(conn, league, label)
    todo = [m for m in metas if m["game_id"] not in already]
    log.info("%s %s: %d games, %d already stored", league, label, len(metas), len(already))

    counts = {"games": 0, "pulls": 0, "trailing": 0, "scored": 0, "conceded": 0,
              "failed": 0}
    started = time.monotonic()

    for i, meta in enumerate(todo, start=1):
        try:
            game = source.load_game(meta, start_year)
        except Exception:                      # one bad game must not end the run
            log.exception("%s %s: load failed", league, meta["game_id"])
            counts["failed"] += 1
            continue
        if game is None:
            counts["failed"] += 1
            continue

        pulls = classify(game)
        counts["games"] += 1
        counts["pulls"] += len(pulls)
        for p in pulls:
            if p.kind != "trailing":
                continue
            counts["trailing"] += 1
            if p.outcome == "scored":
                counts["scored"] += 1
            elif p.outcome == "conceded":
                counts["conceded"] += 1

        if not dry_run:
            db.save(conn, game, pulls)

        if i % 50 == 0 or i == len(todo):
            rate = i / max(time.monotonic() - started, 1e-9)
            log.info("%s %s: %d/%d games (%.1f/s), %d trailing pulls",
                     league, label, i, len(todo), rate, counts["trailing"])

    if conn is not None and not dry_run:
        stored = len(db.known_game_ids(conn, league, label))
        if stored < counts["games"]:
            # A league that numbers its games from 1 every season will quietly
            # overwrite the previous one unless the source qualifies the id.
            log.error("%s %s: collected %d games but only %d are stored - "
                      "game ids are colliding", league, label, counts["games"], stored)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leagues", default="NHL,LIIGA,DEL,EIHL",
                    help="comma separated: " + ",".join(SOURCES))
    ap.add_argument("--seasons", default="2023,2024,2025",
                    help="comma separated start years: 2024 means 2024-25")
    ap.add_argument("--stages", default="regular,playoffs,cup")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, write nothing")
    ap.add_argument("--refresh", action="store_true",
                    help="re-parse games already in the database")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv()

    leagues = [x.strip().upper() for x in args.leagues.split(",") if x.strip()]
    unknown = [x for x in leagues if x not in SOURCES]
    if unknown:
        log.error("unknown league(s): %s", ", ".join(unknown))
        return 2
    seasons = [int(x) for x in args.seasons.split(",") if x.strip()]
    stages = tuple(x.strip() for x in args.stages.split(",") if x.strip())

    conn = None
    if not args.dry_run:
        conn = db.connect()
        db.create_schema(conn)

    totals = {}
    for league in leagues:
        for year in seasons:
            counts = collect_season(conn, league, year, stages, args.dry_run,
                                    args.refresh)
            totals[(league, year)] = counts

    print()
    print(f"{'league':<7} {'season':<9} {'games':>6} {'pulls':>6} {'trailing':>9}"
          f" {'scored':>7} {'conceded':>9} {'failed':>7}")
    for (league, year), c in totals.items():
        label = SOURCES[league].season_label(year)
        print(f"{league:<7} {label:<9} {c['games']:>6} {c['pulls']:>6}"
              f" {c['trailing']:>9} {c['scored']:>7} {c['conceded']:>9}"
              f" {c['failed']:>7}")

    if conn is not None:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
