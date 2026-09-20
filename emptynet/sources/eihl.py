"""EIHL (UK), from the league's own official game sheets.

eliteleague.co.uk renders each game server-side and links a game sheet on
eihlhq.co.uk, in the IIHF standard form. Unlike the other four leagues, it
never states the pull outright, so it is reconstructed from two tables that
each hold half the answer:

    No. A   Min     GA      No. B   Min     GA
    34      60:00   1       31      59:12   2        <- 48 seconds unattended

    Time    GKA   GKB
    00:00    34    31
    59:36    34    31                                <- 31 back in the net
    60:00   - -   - -                                <- the horn, not a pull

Minutes played give how long the net stood empty; the late row in the goalie
timeline gives when the goalie came back. Together they place the window at
58:48-59:36. With no late row the net stayed empty to the final horn, which
fixes the window just as well. Two separate pulls in one game would merge
into one - rare enough to accept, and visible as an unusually long window.

Sheet ids run in date order across seasons, with unused blocks of a few
hundred in between, so a season's id range is found by bisecting over the ids
that exist. Each sheet is ~1.8 MB of positioned markup around ~45 KB of text,
so only the text is cached.
"""

import logging
import re
import string
from datetime import date

from ..fetch import get_distilled
from ..model import EmptyNetWindow, Game, Goal

log = logging.getLogger(__name__)

LEAGUE = "EIHL"
SHEET = "https://eihlhq.co.uk/pdf/print/de-html/{id}"

# Ids are not contiguous: whole blocks of several hundred are unused. Every
# lookup therefore steps forward to the next id that exists, with a budget
# wider than any observed gap, so a bisection cannot mistake a hole for the
# end of the sequence.
HOLE_SEARCH = 512

# A goalie coming back after a pull does it in the last seconds of the game.
# A row in the timeline further out than this is a change of netminder, not a
# return, and must not be read as the end of an empty net.
RETURN_WINDOW = 150

# Ordinary whitespace only, so that str.strip leaves the non-breaking space
# alone - in this sheet it is a cell, not padding.
SPACE = string.whitespace


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

    Sheet ids run in date order, so this is a bisection - but over the ids
    that exist, which is why each step probes forward from its midpoint.
    """
    best = hi
    while lo <= hi:
        mid = (lo + hi) // 2
        head = _probe(mid, budget=HOLE_SEARCH)
        if head is None:
            hi = mid - 1                       # nothing left above: all hole
        elif head["date"] >= target:
            best = head["sheet_id"]
            hi = mid - 1
        else:
            lo = head["sheet_id"] + 1
    return best


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

    minutes, conceded = _goalie_table(text)
    scored = _reconcile(_goal_times(text), conceded, _game_length(minutes))
    goals = [(t, "home") for t in scored[0]] + [(t, "away") for t in scored[1]]
    for idx, (t, side) in enumerate(sorted(goals), start=1):
        game.goals.append(Goal(idx=idx, team_side=side, game_time=t))

    game.home_goals = len(scored[0])
    game.away_goals = len(scored[1])
    game.windows = _windows(text, minutes)
    game.finished_in = "OT" if any(t > 3600 for t, _ in goals) else "REG"
    return game


def _reconcile(times: tuple[list[int], list[int]], conceded: dict[str, int],
               length: int) -> tuple[list[int], list[int]]:
    """Drop the shootout winner, which the Goals table lists as a goal.

    Two things make a goal one too many for the goalie table to account for: a
    shootout winner, credited in the score but conceded by nobody, and a goal
    into an empty net, which no goalie was there to concede. Only the first
    should go, and the clock separates them - a shootout winner is logged at
    the horn, an empty-net goal before it.
    """
    out = []
    for side, mine in (("home", times[0]), ("away", times[1])):
        expected = conceded.get("away" if side == "home" else "home")
        if expected is not None and mine and len(mine) == expected + 1                 and mine[-1] >= length:
            mine = mine[:-1]
        out.append(mine)
    return out[0], out[1]


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


def _windows(text: str, minutes: dict[str, int]) -> list[EmptyNetWindow]:
    if not minutes:
        return []
    rows = _gk_rows(text)
    length = _game_length(minutes)

    out: list[EmptyNetWindow] = []
    for side in ("home", "away"):
        empty = length - minutes.get(side, length)
        if empty <= 0:
            continue
        back = _returned_at(rows, side, length)
        end = back if (back is not None and empty <= back
                       and _is_return(back, length)) else length
        out.append(EmptyNetWindow(side, end - empty, end))
    return out


def _goalie_table(text: str) -> tuple[dict[str, int], dict[str, int]]:
    """Minutes played and goals conceded, per side, over that side's netminders.

    The table runs No. A, Min, GA, No. B, Min, GA across, six cells to a row.
    Blank cells are non-breaking spaces and are kept, because dropping them
    would shift every later cell into the wrong column.
    """
    m = re.search(r"\|No\. A\|(.*?)\|Local start time", text, re.S)
    if not m:
        return {}, {}
    cells = [c.strip(SPACE) for c in m.group(1).split("|")]
    cells = [c for c in cells if c and c not in ("Min", "GA", "No. B")]

    minutes = {"home": 0, "away": 0}
    conceded = {"home": 0, "away": 0}
    seen = False
    for i in range(0, len(cells) - 5, 6):
        row = cells[i:i + 6]
        for side, minute, against in (("home", row[1], row[2]),
                                      ("away", row[4], row[5])):
            if re.fullmatch(r"\d{1,3}:\d{2}", minute):
                minutes[side] += _seconds(minute)
                seen = True
            if against.isdigit():
                conceded[side] += int(against)
    return (minutes, conceded) if seen else ({}, {})


def _gk_rows(text: str) -> list[tuple[int, str, str]]:
    """The goalie timeline, as (time, GKA cell, GKB cell)."""
    m = re.search(r"\|Time\|\s*\|GKA\|\s*\|GKB\|(.*?)\|Referee", text, re.S)
    if not m:
        return []
    tokens = [t.strip() for t in m.group(1).split("|")]
    rows = []
    for i, token in enumerate(tokens):
        if not re.fullmatch(r"\d{1,3}:\d{2}", token):
            continue
        rest = [t for t in tokens[i + 1:] if t][:2]
        if len(rest) == 2:
            rows.append((_seconds(token), rest[0], rest[1]))
    rows.sort(key=lambda r: r[0])
    return rows


def _game_length(minutes: dict[str, int]) -> int:
    """How long the game ran, in seconds - 3600 unless it went to overtime.

    The goalies' own minutes are the clock. The timeline's last row is not:
    after a shootout it reads past the end of overtime, which would make both
    nets look unattended for the length of the shootout.
    """
    return max([3600, *minutes.values()])


def _is_return(when: int, length: int) -> bool:
    """A goalie back in the net near a horn is a return; earlier is a change.

    Both horns count: a team that ties the game with the net empty gets its
    goalie back for an overtime that ends minutes later, and measuring only
    against the end of the game would throw that pull away.
    """
    return (length - when <= RETURN_WINDOW
            or (when <= 3600 and 3600 - when <= RETURN_WINDOW))


def _returned_at(rows: list[tuple[int, str, str]], side: str, length: int) -> int | None:
    """Last time this side had a numbered goalie put back, before the horn."""
    column = 1 if side == "home" else 2
    for t, *cells in reversed(rows):
        if t >= length:
            continue
        if any(ch.isdigit() for ch in cells[column - 1]) and t > 0:
            return t
    return None
