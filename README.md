# emptynet

When does an ice hockey team pull its goaltender, and what happens next.

For every game it can reach, emptynet records each stretch of play with a net
unattended: who pulled, at what second, how far behind they were, how long the
net stayed empty, and whether that stretch ended in a goal for them, a goal
against them, or nothing at all.

## Leagues and where the answer comes from

Every league states it differently, and the difference matters. Four of the
five time the pull to the second; the NHL times it to the play, which for a
pull taken at a whistle is the same thing.

| League | Source | Pull timing |
| --- | --- | --- |
| **NHL** | `api-web.nhle.com` play-by-play | `situationCode` on every play: `0651` is the away goalie off with six skaters out. Timed to the play, so to the whistle the goalie left at. |
| **Liiga** | `liiga.fi/api/v2` game detail | `goalKeeperEvents` with `emptyNet: 1`, `beginTime` and `endTime` already in seconds. Exact. |
| **DEL** | `penny-del.org` game page | The event list carries `Torhüter aus dem Tor` and `Torhüter ins Tor` with the clock. Exact. |
| **EIHL** | official game sheet on `eihlhq.co.uk` | The `Time / GKA / GKB` table at the foot of the sheet: a row where a column reads `- -` is a pull. Exact. |
| **U20 SM-sarja** | `tulospalvelu.leijonat.fi` game report | `GK_out` and `GK_in` entries in `GameLogsUpdate`, with `GameTime` in seconds, alongside `Timeout`. Exact. |

A "season" is named by the year it starts in: `2024` means 2024-25.

## Running it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env          # put your DATABASE_URL in it
python collect.py --leagues NHL,LIIGA,DEL,EIHL --seasons 2023,2024,2025
```

`--dry-run` parses and reports without writing. Every response is cached under
`./cache`, so a re-run costs nothing, an interrupted run resumes where it
stopped, and a parser change can be re-tested without touching the network.
Games already stored are skipped unless `--refresh` is passed.

## What lands in the database

Three tables in the `emptynet` schema, so this can share a database with an
unrelated project:

* **`game`** - one row per game, with the final score and how it finished.
* **`goal`** - every goal, with its time in seconds from the opening faceoff.
  Kept in full so the outcome of a pull can be re-checked rather than trusted.
* **`pull`** - one row per empty-net window: `start_time`, `end_time`,
  `seconds_empty`, `time_left` (regulation time remaining at the pull),
  `score_diff` (the puller's margin when the goalie left), and `outcome`, one
  of `scored`, `conceded`, `nothing`.

`pull.kind` separates the three reasons a net is empty, and only the first is
the thing this project is about:

* `trailing` - behind, with five minutes or less of regulation left.
* `delayed_penalty` - half a minute or less, with the whistle already up.
* `other` - anything else, including a pull earlier than five minutes out.

Two views do the counting: `v_trailing_pulls` lists the tactical pulls with
the teams named, and `v_pull_rates` reduces them to per-league, per-season
rates.

```sql
SELECT * FROM emptynet.v_pull_rates;
```

## Things to know before trusting a number

* **Both teams' pulls are counted separately.** A game where a team pulls,
  gets the goalie back, and pulls again is two rows, which is why a season can
  hold more trailing pulls than games.
* **Five minutes is a convention, not a fact.** `TRAILING_WINDOW` in
  `emptynet/model.py` decides what counts as tactical. It is deliberately
  wider than any real pull so that the classification is not quietly deciding
  the answer.
* **NHL pull times are play-accurate, not second-accurate.** `situationCode`
  changes at the next recorded event. For a pull taken at a whistle - nearly
  all of them - that is the same second; for one taken on the fly it can be a
  few seconds late.
* **A goal at the same second as the pull counts as before it.** That goal is
  usually the reason for the pull, so it belongs to the score the coach was
  looking at.
