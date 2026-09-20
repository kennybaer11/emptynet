"""U20 SM-sarja (Finland) - not collecting yet.

What is established:

  * The league is run by the Finnish Ice Hockey Association, not by Liiga, so
    the liiga.fi API that serves Liiga does not serve it - every tournament
    code outside Liiga's own comes back 403.
  * Its results live in the association's GameCentre at
    tulospalvelu.leijonat.fi, reached from leijonat.fi/sarjat/u20-sm-sarja.
    The season/level/stage keys for 2025-26 are
    season=2026, lid=77, did=9, stgid=3360 and stgid=6590.
  * The game list on those pages is rendered behind a consent gate, so the
    per-game URL pattern still has to be read out of a live browser session
    before a scraper can be written.

What is still unknown, and decides whether this league can be collected at
all: whether the association's game sheet records goalie in and out times the
way the DEL event list and the EIHL sheet do. If it only records goals and an
empty-net flag, this league can answer "did an empty-net goal happen" but not
"when did they pull".
"""

import logging

log = logging.getLogger(__name__)

LEAGUE = "U20SM"
SERIE = ("https://tulospalvelu.leijonat.fi/serie/"
         "?season={season}&lid=77&did=9&stgid=3360&lang=fi&page=ottelut")


def season_games(start_year: int, stages=("regular",)) -> list[dict]:
    log.warning("U20SM: no collector yet - see the module docstring")
    return []


def load_game(meta: dict, start_year: int):
    return None
