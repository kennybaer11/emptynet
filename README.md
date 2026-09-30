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

## How likely is a pull in the first place

The pull table holds the pulls that happened, not the situations where a team
could have pulled and did not. `probability.py` rebuilds that denominator from
the goal times - at a given moment every team is behind by some number of
goals, and either has its goalie in the net or does not.

```bash
python probability.py --league LIIGA --deficit 3
```

Liiga, teams trailing by exactly three goals:

| Left | Teams | Net empty now | Pull while still 3 down | Pull by the horn |
| ---: | ---: | ---: | ---: | ---: |
| 5:00 | 195 | 0.0% | 35.9% | 47.2% |
| 3:00 | 196 | 16.8% | 24.0% | 42.9% |
| 2:00 | 225 | 22.2% | 11.1% | 33.8% |
| 1:00 | 265 | 19.2% | 2.3% | 20.4% |

The last two columns are not the same question. A team three down at five
minutes often scores twice before it pulls, so "by the horn" counts pulls
taken at a one-goal deficit. Only the middle column answers what a team does
*while* three behind.

## What the seasons say

2023-24 to 2026-27 so far: 9,580 games, 7,682 tactical pulls. `left` is how
much regulation was left when the goalie went, `empty` how long the net
stayed that way.

| League | Pulls | left | empty | Scored | Conceded | Nothing |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| NHL | 3,701 | 2:03 | 60 s | 12.6% | 36.4% | 51.0% |
| Liiga | 1,350 | 2:02 | 68 s | 14.1% | 35.0% | 50.9% |
| U20 SM-sarja | 1,120 | 2:07 | 45 s | 15.6% | 37.1% | 47.3% |
| DEL | 1,024 | 2:03 | 49 s | 13.2% | 33.5% | 53.3% |
| EIHL | 487 | 1:47 | 76 s | 10.9% | 41.3% | 47.8% |

Five leagues, five unrelated feeds, five parsers written separately - and they
agree to within a few points. That agreement is the best evidence available
that the parsing is right, which is why the table is worth more than any one
league's number.

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
* **EIHL windows run longer than the rest, and some of that is the method.**
  Its sheet does not record the pull, so a window is reconstructed from
  minutes played and the goalie's return. A game with two separate pulls comes
  back as one long window, which also pushes its conceded rate up. The other
  four leagues state the pull outright and need no such reconstruction.
* **A league that renumbers its games from 1 each season must say so.** Liiga
  does, and storing its games under the league's own id silently overwrote
  each season with the next. Its ids are now qualified with the season; the
  collector checks every season's stored count against what it collected and
  complains if they differ.
