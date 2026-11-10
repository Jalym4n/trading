"""Family A: late-day momentum (last half hour follows the day's move).

Rules (fixed before any data was looked at; times are bar OPEN labels, ET):
  signal   at the close of the 15:25 bar: R = close / prior session's last RTH
           bar close - 1 (the 15:55 bar; 12:55 after an early close).
  entry    at the OPEN of the 15:30 bar: long if R > 0, short if R < 0,
           no trade if R == 0 ("no-signal").
  stop     ldm_stop_points (10) from entry, live from the entry bar itself. A bar
           that opens through the stop fills at its open.
  exit     at the CLOSE of the 15:55 bar (a market exit: slippage applies). No target.
  skips    days without the full 15:25-15:55 bars (early closes) -> "short-day";
           no prior close -> "no-levels". Roll and missing-bar days are skipped
           by the harness.
"""

import numpy as np

from ..registry import register
from ..strategy import DayResult, Trade

SIGNAL_BAR, ENTRY_BAR, EXIT_BAR = "15:25", "15:30", "15:55"


@register(
    "late_day_momentum",
    hypothesis="The last half hour of the cash session continues the day's move (return from the prior close "
               "to 15:30), because option-dealer hedging and end-of-day fund rebalancing trade in the direction "
               "of the day's return; the continuation is large enough to pay 1 MES round trip.",
    disproof="Gross expectancy <= 0 on training, or net expectancy <= 0 with normal slippage.",
    defaults={"ldm_stop_points": 10.0},
)
def late_day_momentum(day_bars, prior_levels, params):
    pc = prior_levels.get("prior_close")
    if pc is None or not np.isfinite(pc):
        return DayResult("no-levels")
    labels = list(day_bars.index.strftime("%H:%M"))
    need = ["15:25", "15:30", "15:35", "15:40", "15:45", "15:50", "15:55"]
    if any(x not in labels for x in need):
        return DayResult("short-day")
    o, h, l, c = (day_bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    i_sig, i_in, i_out = labels.index(SIGNAL_BAR), labels.index(ENTRY_BAR), labels.index(EXIT_BAR)
    r = c[i_sig] / pc - 1.0
    det = {"prior_close": pc, "signal_close": c[i_sig], "day_return": r}
    if r == 0:
        return DayResult("no-signal", detail=det)
    side, sgn = ("long", 1) if r > 0 else ("short", -1)
    entry = o[i_in]
    stop = entry - sgn * params["ldm_stop_points"]
    reason, px, exit_i = "time", c[i_out], i_out
    for j in range(i_in, i_out + 1):
        if (side == "long" and l[j] <= stop) or (side == "short" and h[j] >= stop):
            gap = j > i_in and ((o[j] <= stop) if side == "long" else (o[j] >= stop))
            reason, px, exit_i = ("stop_gap", o[j], j) if gap else ("stop", stop, j)
            break
    nan = float("nan")
    t = Trade(day_bars.index[0].strftime("%Y-%m-%d"), side, "LDM", nan, nan, nan, ENTRY_BAR, entry, stop, nan, "none",
              labels[exit_i], px, reason, False, abs(entry - stop), nan, nan, "", nan, nan, "")
    return DayResult("traded", [t], det)
