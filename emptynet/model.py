"""The shapes every source produces, and the one piece of reasoning they share.

A source's whole job is to say: here is the game, here are its goals, and here
are the stretches when a net stood unattended. Turning those stretches into
"they pulled at 58:12 down one and conceded eleven seconds later" is the same
arithmetic in every league, so it lives here.
"""

from dataclasses import dataclass, field

# A pull counts as tactical when the team is behind with no more than this
# much regulation left. Five minutes is generous on purpose: the earliest
# serious pulls in these leagues sit around 3:00, and a wide window means the
# classification is not quietly deciding what we are trying to measure.
TRAILING_WINDOW = 300

# A net left empty for no longer than this, outside the trailing window, is a
# delayed penalty - the goalie skating off with the whistle already up.
DELAYED_PENALTY_MAX = 30


@dataclass
class Goal:
    idx: int
    team_side: str            # home | away
    game_time: int            # seconds from the opening faceoff
    period: int | None = None
    strength: str | None = None
    empty_net: bool | None = None
    scorer: str | None = None


@dataclass
class EmptyNetWindow:
    """Raw output of a source: this side had no goalie from here to here."""
    team_side: str
    start_time: int
    end_time: int | None      # None: still empty when the game ended
    period: int | None = None


@dataclass
class Game:
    league: str
    game_id: str
    season: str
    home_team: str
    away_team: str
    stage: str | None = None
    played_at: str | None = None
    home_goals: int | None = None
    away_goals: int | None = None
    finished_in: str | None = None      # REG | OT | SO
    regulation_seconds: int = 3600
    source_url: str | None = None
    goals: list[Goal] = field(default_factory=list)
    windows: list[EmptyNetWindow] = field(default_factory=list)


@dataclass
class Pull:
    idx: int
    team_side: str
    start_time: int
    end_time: int | None
    seconds_empty: int | None
    time_left: int
    kind: str                 # trailing | delayed_penalty | other
    score_diff: int | None
    goal_for: int | None
    goal_against: int | None
    outcome: str              # scored | conceded | nothing
    period: int | None = None


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def classify(game: Game) -> list[Pull]:
    """Turn a game's empty-net windows into pull rows with their outcomes."""
    pulls: list[Pull] = []
    goals = sorted(game.goals, key=lambda g: g.game_time)

    for i, w in enumerate(sorted(game.windows, key=lambda w: w.start_time), start=1):
        end = w.end_time
        seconds_empty = None if end is None else max(0, end - w.start_time)
        time_left = game.regulation_seconds - w.start_time

        # Score as it stood when the goalie left. A goal struck at the same
        # second as the pull is the reason for the pull, so it counts.
        for_before = sum(1 for g in goals
                         if g.team_side == w.team_side and g.game_time <= w.start_time)
        against_before = sum(1 for g in goals
                             if g.team_side != w.team_side and g.game_time <= w.start_time)
        score_diff = for_before - against_before

        if score_diff < 0 and 0 <= time_left <= TRAILING_WINDOW:
            kind = "trailing"
        elif seconds_empty is not None and seconds_empty <= DELAYED_PENALTY_MAX:
            kind = "delayed_penalty"
        else:
            kind = "other"

        def first_goal(side: str) -> int | None:
            for g in goals:
                if g.team_side != side:
                    continue
                if g.game_time <= w.start_time:
                    continue
                if end is not None and g.game_time > end:
                    continue
                return g.game_time
            return None

        goal_for = first_goal(w.team_side)
        goal_against = first_goal(_other(w.team_side))

        if goal_for is not None and (goal_against is None or goal_for <= goal_against):
            outcome = "scored"
        elif goal_against is not None:
            outcome = "conceded"
        else:
            outcome = "nothing"

        pulls.append(Pull(
            idx=i,
            team_side=w.team_side,
            start_time=w.start_time,
            end_time=end,
            seconds_empty=seconds_empty,
            time_left=time_left,
            kind=kind,
            score_diff=score_diff,
            goal_for=goal_for,
            goal_against=goal_against,
            outcome=outcome,
            period=w.period,
        ))
    return pulls
