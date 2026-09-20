"""EIHL (UK), from the league's own official game sheets.

eliteleague.co.uk renders each game server-side and links a game sheet on
eihlhq.co.uk. That sheet is the IIHF standard form, and its last table is
exactly the question this project asks:

    Time    GKA   GKB
    00:00    1     1
    58:12    1    - -      <- the visitor pulled here
    60:00   - -   - -      <- the horn, not a pull

Sheet ids run in date order across seasons, with unused blocks of a few
hundred in between, so a season's id range is found by scanning forward in
strides. Each sheet is ~1.8 MB of positioned markup around ~45 KB of text, so
only the text is cached.
"""

import logging
import re
from datetime import date

from ..fetch import get_distilled
from ..model import EmptyNetWindow, Game, Goal

log = logging.getLogger(__name__)

LEAGUE = "EIHL"
SHEET = "https://eihlhq.co.uk/pdf/print/de-html/{id}"

# Ids are not contiguous - whole blocks of several hundred are unused - so
# every lookup steps forward past holes, and a season boundary is found by a
# coarse forward scan rather than a bisection that a hole would derail.
HOLE_SEARCH = 64
COARSE_STRIDE = 64


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _distill(raw_html: str) -> str:
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", raw_html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\|{2,}", "|", t)
    t = re.sub(r"[ \t\n\r]+", " ", t)
    import html as _html
    return _html.unescape(t)


def _sheet(sheet_id: int) -> str | None:
    return get_distilled(SHEET.format(id=sheet_id), _distill, tolerate_404=True)


def _header(text: str) -> dict | None:
    if "Official Gamesheet" not in text:
        return None
    comp = re.search(r"Competition\|\s*\|(.*?)\|", text)
    day = re.search(r"Date\|\s*\|(\d{2})\.(\d{2})\.(\d{4})\|", text)
    teams = re.search(r"Home\|\s*\|(.*?)\|.*?Visitor\|\s*\|(.*?)\|", text, re.S)
    if not (comp and day and teams):
        return None
    d, m, y = day.groups()
    return {
        "competition": comp.group(1).strip(),
        "played_at": f"{y}-{m}-{d}",
        "date": date(int(y), int(m), int(d)),
        "home_team": teams.group(1).strip().strip("|").strip(),
        "away_team": teams.group(2).strip().strip("|").strip(),
    }


def _stage(competition: str) -> str:
    c = competition.lower()
    if "cup" in c:
        return "cup"
    if "play" in c:
        return "playoffs"
    return "regular"


def _probe(sheet_id: int, budget: int = HOLE_SEARCH) -> dict | None:
    """Header of the first sheet at or after `sheet_id`, stepping past holes."""
    for candidate in range(sheet_id, sheet_id + budget):
        text = _sheet(candidate)
        if not text:
            continue
        head = _header(text)
        if head:
            head["sheet_id"] = candidate
            return head
    return None


def _first_id_on_or_after(target: date, lo: int, hi: int) -> int:
    """Lowest sheet id whose game is on or after `target`.

    Sheet ids run in date order, so a forward scan in strides brackets the
    boundary and a walk over the last stride pins it down.
    """
    before = lo
    cursor = lo
    while cursor <= hi:
        head = _probe(cursor, budget=COARSE_STRIDE)
        if head is None:
            cursor += COARSE_STRIDE
            continue
        if head["date"] >= target:
            break
        before = head["sheet_id"]
        cursor = head["sheet_id"] + COARSE_STRIDE

    for candidate in range(before + 1, min(cursor + COARSE_STRIDE, hi) + 1):
        text = _sheet(candidate)
        if not text:
            continue
        head = _header(text)
        if head and head["date"] >= target:
            return candidate
    return hi


def season_games(start_year: int, stages=("regular", "playoffs", "cup"),
                 id_range: tuple[int, int] = (1, 6000)) -> list[dict]:
    lo, hi = id_range
    first = _first_id_on_or_after(date(start_year, 8, 1), lo, hi)
    last = _first_id_on_or_after(date(start_year + 1, 8, 1), first, hi)
    log.info("EIHL %s: sheet ids %d..%d", season_label(start_year), first, last)

    games = []
    for sheet_id in range(first, last):
        text = _sheet(sheet_id)
        if not text:
            continue
        head = _header(text)
        if not head:
            continue
        stage = _stage(head["competition"])
        if stage not in stages:
            continue
        games.append({"game_id": str(sheet_id), "stage": stage, **head})
    log.info("EIHL %s: %d game sheets", season_label(start_year), len(games))
    return games


def load_game(meta: dict, start_year: int) -> Game | None:
    text = _sheet(int(meta["game_id"]))
    if not text:
        return None

    game = Game(
        league=LEAGUE,
        game_id=meta["game_id"],
        season=season_label(start_year),
        stage=meta["stage"],
        played_at=meta.get("played_at"),
        home_team=meta["home_team"],
        away_team=meta["away_team"],
        source_url=SHEET.format(id=meta["game_id"]),
    )

    times = _goal_times(text)
    goals = [(t, "home") for t in times[0]] + [(t, "away") for t in times[1]]
    for idx, (t, side) in enumerate(sorted(goals), start=1):
        game.goals.append(Goal(idx=idx, team_side=side, game_time=t))

    game.home_goals = len(times[0])
    game.away_goals = len(times[1])
    game.windows = _windows(text)
    game.finished_in = "OT" if any(t > 3600 for t, _ in goals) else "REG"
    return game


def _seconds(token: str) -> int:
    minutes, seconds = token.split(":")
    return int(minutes) * 60 + int(seconds)


def _goal_times(text: str) -> tuple[list[int], list[int]]:
    """Times from the two Goals tables: home first, visitor second.

    The sheet is emitted twice over, so only the first pair is read.
    """
    blocks = [m.end() for m in re.finditer(r"\|Goals\|", text)][:2]
    out: list[list[int]] = [[], []]
    for i, start in enumerate(blocks):
        block = text[start:start + 4000].split("|Penalties|")[0]
        header = block.find("|N6|")
        rows = block[header:] if header >= 0 else block
        out[i] = [_seconds(t) for t in re.findall(r"\b(\d{1,3}:\d{2})\b", rows)]
    return out[0], out[1]


def _windows(text: str) -> list[EmptyNetWindow]:
    m = re.search(r"\|Time\|\s*\|GKA\|\s*\|GKB\|(.*?)\|Referee", text, re.S)
    if not m:
        return []

    tokens = [t.strip() for t in m.group(1).split("|")]
    rows: list[tuple[int, str, str]] = []
    i = 0
    while i < len(tokens):
        if re.fullmatch(r"\d{1,3}:\d{2}", tokens[i]):
            rest = [t for t in tokens[i + 1:] if t][:2]
            if len(rest) == 2:
                rows.append((_seconds(tokens[i]), rest[0], rest[1]))
            i += 1
        else:
            i += 1
    if not rows:
        return []

    rows.sort(key=lambda r: r[0])
    end = rows[-1][0]

    def empty(cell: str) -> bool:
        return not any(ch.isdigit() for ch in cell)

    out: list[EmptyNetWindow] = []
    for side, column in (("home", 1), ("away", 2)):
        open_at = None
        for t, *cells in rows:
            is_empty = empty(cells[column - 1])
            if is_empty and open_at is None and t < end:
                open_at = t
            elif not is_empty and open_at is not None:
                out.append(EmptyNetWindow(side, open_at, t))
                open_at = None
        if open_at is not None:
            out.append(EmptyNetWindow(side, open_at, end))
    return out
