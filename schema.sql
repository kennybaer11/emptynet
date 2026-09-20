-- emptynet: when a team pulls its goaltender, and what happens next.
--
-- Everything lives in its own schema, so this can share a database with
-- anything else without colliding. Times are seconds from the opening
-- faceoff: 3600 is the end of regulation, so "58:12" is 3492.

CREATE SCHEMA IF NOT EXISTS emptynet;

CREATE TABLE IF NOT EXISTS emptynet.game (
  league        TEXT    NOT NULL,   -- NHL, LIIGA, DEL, EIHL, U20SM
  game_id       TEXT    NOT NULL,   -- the league's own id, kept as text
  season        TEXT    NOT NULL,   -- '2024-25'
  stage         TEXT,               -- regular, playoffs, cup, ...
  played_at     TIMESTAMPTZ,
  home_team     TEXT    NOT NULL,
  away_team     TEXT    NOT NULL,
  home_goals    INTEGER,
  away_goals    INTEGER,
  finished_in   TEXT,               -- REG, OT, SO
  regulation_seconds INTEGER NOT NULL DEFAULT 3600,
  source_url    TEXT,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (league, game_id)
);

CREATE INDEX IF NOT EXISTS game_season_idx ON emptynet.game (league, season);

-- Every goal in the game, in order. Kept in full because the outcome of a
-- pull is derived from it, and because a stored derivation you cannot
-- re-check is a derivation you cannot trust.
CREATE TABLE IF NOT EXISTS emptynet.goal (
  league     TEXT    NOT NULL,
  game_id    TEXT    NOT NULL,
  idx        INTEGER NOT NULL,      -- order within the game, from 1
  team_side  TEXT    NOT NULL,      -- home | away - the side that scored
  period     INTEGER,
  game_time  INTEGER NOT NULL,      -- seconds from the opening faceoff
  strength   TEXT,                  -- EV, PP, SH; NULL when the feed is silent
  empty_net  BOOLEAN,               -- scored into an unattended net
  scorer     TEXT,
  PRIMARY KEY (league, game_id, idx),
  FOREIGN KEY (league, game_id) REFERENCES emptynet.game (league, game_id) ON DELETE CASCADE
);

-- One row per stretch of play with a net unattended.
--
-- kind separates the three reasons a net is empty:
--   trailing         - the tactical pull: behind, late in the third
--   delayed_penalty  - a few seconds with the whistle already up
--   other            - anything else, including a pull that came early
CREATE TABLE IF NOT EXISTS emptynet.pull (
  league        TEXT    NOT NULL,
  game_id       TEXT    NOT NULL,
  idx           INTEGER NOT NULL,
  team_side     TEXT    NOT NULL,   -- home | away - the side without a goalie
  period        INTEGER,
  start_time    INTEGER NOT NULL,   -- seconds from the opening faceoff
  end_time      INTEGER,            -- NULL: still empty at the final horn
  seconds_empty INTEGER,
  time_left     INTEGER,            -- regulation_seconds - start_time
  kind          TEXT    NOT NULL,
  score_diff    INTEGER,            -- puller's goals minus opponent's, at the pull
  goal_for      INTEGER,            -- time of the puller's first goal while empty
  goal_against  INTEGER,            -- time of the first goal conceded while empty
  outcome       TEXT    NOT NULL,   -- scored | conceded | nothing
  PRIMARY KEY (league, game_id, idx),
  FOREIGN KEY (league, game_id) REFERENCES emptynet.game (league, game_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS pull_kind_idx ON emptynet.pull (league, kind);

-- The question in one table: of the tactical pulls, how many turned into a
-- goal for the pulling team, and how many into one against.
CREATE OR REPLACE VIEW emptynet.v_trailing_pulls AS
SELECT
  p.league,
  g.season,
  g.stage,
  p.game_id,
  CASE WHEN p.team_side = 'home' THEN g.home_team ELSE g.away_team END AS team,
  CASE WHEN p.team_side = 'home' THEN g.away_team ELSE g.home_team END AS opponent,
  p.start_time,
  p.time_left,
  p.seconds_empty,
  p.score_diff,
  p.outcome,
  p.goal_for,
  p.goal_against
FROM emptynet.pull p
JOIN emptynet.game g USING (league, game_id)
WHERE p.kind = 'trailing';

CREATE OR REPLACE VIEW emptynet.v_pull_rates AS
SELECT
  league,
  season,
  count(*)                                             AS pulls,
  round(avg(time_left))                                AS avg_time_left,
  round(avg(seconds_empty))                            AS avg_seconds_empty,
  count(*) FILTER (WHERE outcome = 'scored')           AS scored,
  count(*) FILTER (WHERE outcome = 'conceded')         AS conceded,
  count(*) FILTER (WHERE outcome = 'nothing')          AS nothing,
  round(100.0 * count(*) FILTER (WHERE outcome = 'scored')   / count(*), 1) AS scored_pct,
  round(100.0 * count(*) FILTER (WHERE outcome = 'conceded') / count(*), 1) AS conceded_pct
FROM emptynet.v_trailing_pulls
GROUP BY league, season
ORDER BY league, season;
