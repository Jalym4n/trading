"""gex_wall_fade: fade the first rejection of the day's call wall or put wall.

Rule, fixed 2026-11-13 before the walls were computed (walls: src/gex_walls.py). 5-min RTH bars.
  Short: a bar trades up to the call wall (high >= wall) and closes below it. Enter at that close,
         stop one tick above the bar high.
  Long:  a bar trades down to the put wall (low <= wall) and closes above it; stop one tick below.
  Shared with the other 2026-11-13 set-ups: target 2 x risk, risk above 30 points is not a trade,
  no entry after 15:00, flat at the 15:55 close, stop first if both possible, at most 2 trades a
  day and one per wall.
"""
from ..registry import register
from ..strategy import DayResult
from ._common import arrays, finite
from .user_confluences import BASE, _enter, _result, _tick


@register("gex_wall_fade",
          hypothesis="Dealer hedging at the strikes holding the most option gamma makes the first touch of the "
                     "call wall or put wall reject by 2R often enough to beat costs.",
          disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
          defaults={**BASE, "need_gex_walls": True})
def gex_wall_fade(day_bars, prior_levels, params):
    p = {**BASE, **params}
    cw, pw = prior_levels.get("call_wall"), prior_levels.get("put_wall")
    if not finite(cw, pw):
        return DayResult("no-levels")
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) < 78:
        return DayResult("short-day")
    o, h, l, c = arrays(day_bars)                                                # noqa: E741
    date, trades, det, setups = day_bars.index[0].strftime("%Y-%m-%d"), [], {}, 0
    live = {"call": True, "put": True}
    i = 0
    while i < len(c) and len(trades) < p["max_trades_day"]:
        side = None
        if live["call"] and h[i] >= cw > c[i]:
            side, stop, wall, live["call"] = "short", h[i] + _tick(p), cw, False
        elif live["put"] and l[i] <= pw < c[i]:
            side, stop, wall, live["put"] = "long", l[i] - _tick(p), pw, False
        if side:
            setups += 1
            j = _enter(trades, det, date, side, "WALL", labels, i, c[i], stop, o, h, l, c, p, wall)
            if j is not None:
                i = j + 1
                continue
        i += 1
    return _result(trades, det, setups)
