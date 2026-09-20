"""One module per league. Each exposes `season_games()` and `load_game()`."""

from . import nhl, liiga, eihl, del_, u20sm   # noqa: F401

SOURCES = {
    "NHL": nhl,
    "LIIGA": liiga,
    "EIHL": eihl,
    "DEL": del_,
    "U20SM": u20sm,
}
