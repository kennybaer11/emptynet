"""Postgres sink. Works against Neon or any other Postgres.

Everything is written into the `emptynet` schema, so this can share a database
with an unrelated project without either one noticing the other.
"""

import logging
import os
from pathlib import Path

import psycopg

from .model import Game, Pull

log = logging.getLogger(__name__)
SCHEMA_SQL = Path(__file__).resolve().parent.parent / "schema.sql"


def connect(dsn: str | None = None):
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set - copy .env.example to .env")
    return psycopg.connect(dsn)


def create_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()
    log.info("schema ready")


def save(conn, game: Game, pulls: list[Pull]) -> None:
    """Write one game and everything derived from it, replacing any earlier run."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO emptynet.game (league, game_id, season, stage, played_at,
                home_team, away_team, home_goals, away_goals, finished_in,
                regulation_seconds, source_url, fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (league, game_id) DO UPDATE SET
                season = EXCLUDED.season,
                stage = EXCLUDED.stage,
                played_at = EXCLUDED.played_at,
                home_team = EXCLUDED.home_team,
                away_team = EXCLUDED.away_team,
                home_goals = EXCLUDED.home_goals,
                away_goals = EXCLUDED.away_goals,
                finished_in = EXCLUDED.finished_in,
                regulation_seconds = EXCLUDED.regulation_seconds,
                source_url = EXCLUDED.source_url,
                fetched_at = now()
        """, (game.league, game.game_id, game.season, game.stage, game.played_at,
              game.home_team, game.away_team, game.home_goals, game.away_goals,
              game.finished_in, game.regulation_seconds, game.source_url))

        cur.execute("DELETE FROM emptynet.goal WHERE league=%s AND game_id=%s",
                    (game.league, game.game_id))
        cur.executemany("""
            INSERT INTO emptynet.goal (league, game_id, idx, team_side, period,
                game_time, strength, empty_net, scorer)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, [(game.league, game.game_id, g.idx, g.team_side, g.period,
               g.game_time, g.strength, g.empty_net, g.scorer) for g in game.goals])

        cur.execute("DELETE FROM emptynet.pull WHERE league=%s AND game_id=%s",
                    (game.league, game.game_id))
        cur.executemany("""
            INSERT INTO emptynet.pull (league, game_id, idx, team_side, period,
                start_time, end_time, seconds_empty, time_left, kind, score_diff,
                goal_for, goal_against, outcome)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, [(game.league, game.game_id, p.idx, p.team_side, p.period,
               p.start_time, p.end_time, p.seconds_empty, p.time_left, p.kind,
               p.score_diff, p.goal_for, p.goal_against, p.outcome) for p in pulls])
    conn.commit()


def known_game_ids(conn, league: str, season: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT game_id FROM emptynet.game WHERE league=%s AND season=%s",
                    (league, season))
        return {row[0] for row in cur.fetchall()}


def rates(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM emptynet.v_pull_rates")
        return cur.fetchall()
