"""Family B: intraday trend outside a volatility "noise band" (after Zarattini, Aziz & Barbon 2024).

Rules (fixed before any data was looked at; bar labels are OPEN times, ET):
  band     sigma(bar) = mean over the 14 previous trading days of
           |bar close / that day's 09:30 open - 1| (prior_levels["noise_sigma"]).
           upper(bar) = max(today's 09:30 open, prior close) x (1 + sigma(bar))
           lower(bar) = min(today's 09:30 open, prior close) x (1 - sigma(bar))
  checks   at the CLOSE of the bars 09:55, 10:25, ..., 15:25 (clock 10:00 ... 15:30).
  exit     at a check, a long whose close is <= upper (short: >= lower) is closed at that
           close ("band"). Still open at the end -> close of the 15:55 bar ("time").
  entry    at a check, when flat: close > upper -> long, close < lower -> short, at that
           close. A check can exit and enter (only possible as a flip to the other side).
  stop     nbt_stop_points (30 = the $150 per-trade cap on 1 MES) from entry, live from
           the bar after entry; a bar opening through it fills at its open.
  skips    days without all 78 RTH bars (early closes) -> "short-day"; no prior close or
           no 14-day band yet -> "no-levels"; never outside the band -> "no-signal".
All exits are market orders: slippage applies to every one of them.
"""

import numpy as np

from ..registry import register
from ..strategy import DayResult, Trade

CHECKS = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (25, 55)][1:-1]      # 09:55 ... 15:25
LAST = "15:55"


@register(
    "noise_band_trend",
    hypothesis="When the index has moved further from the open than its 14-day average for that time of "
               "day, the move tends to continue into the close (trend days driven by hedging and momentum "
               "flows), by enough points to pay costs on 1 MES.",
    disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
    defaults={"nbt_stop_points": 30.0, "noise_lookback_days": 14,
              "slippage_applies_to": ["entry", "stop", "stop_gap", "time", "band"]},
)
def noise_band_trend(day_bars, prior_levels, params):
    pc, sig = prior_levels.get("prior_close"), prior_levels.get("noise_sigma") or {}
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) != 78 or labels[0] != "09:30" or labels[-1] != LAST:
        return DayResult("short-day")
    if pc is None or not np.isfinite(pc) or any(not np.isfinite(sig.get(k, np.nan)) for k in CHECKS):
        return DayResult("no-levels")
    o, h, l, c = (day_bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    hi_ref, lo_ref = max(o[0], pc), min(o[0], pc)
    date = day_bars.index[0].strftime("%Y-%m-%d")
    if params.get("nbt_stop_usd"):
        # the same DOLLAR stop on every instrument, rounded down to the tick
        tick = params["_tick_size"]
        stop_pts = int(params["nbt_stop_usd"] / params["_point_value_usd"] / tick + 1e-9) * tick
    else:
        stop_pts = params["nbt_stop_points"]
    nan = float("nan")
    trades, pos = [], None                      # pos = (side, sgn, entry, stop, entry_label)

    def close_out(i, px, reason):
        side, sgn, entry, stop, lab = pos
        trades.append(Trade(date, side, "NBT", nan, nan, nan, lab, entry, stop, nan, "none", labels[i], px, reason,
                            False, stop_pts, nan, nan, "", nan, nan, ""))

    for i, lab in enumerate(labels):
        if pos is not None:                     # stop first, on every bar after the entry bar
            side, sgn, entry, stop, _ = pos
            if (side == "long" and l[i] <= stop) or (side == "short" and h[i] >= stop):
                gap = (o[i] <= stop) if side == "long" else (o[i] >= stop)
                close_out(i, o[i] if gap else stop, "stop_gap" if gap else "stop")
                pos = None
        if lab == LAST:
            if pos is not None:
                close_out(i, c[i], "time")
                pos = None
            break
        if lab not in CHECKS:
            continue
        upper, lower = hi_ref * (1 + sig[lab]), lo_ref * (1 - sig[lab])
        if pos is not None:
            side = pos[0]
            if (side == "long" and c[i] <= upper) or (side == "short" and c[i] >= lower):
                close_out(i, c[i], "band")
                pos = None
        if pos is None:
            if c[i] > upper:
                pos = ("long", 1, c[i], c[i] - stop_pts, lab)
            elif c[i] < lower:
                pos = ("short", -1, c[i], c[i] + stop_pts, lab)
    return DayResult("traded" if trades else "no-signal", trades, {"open": o[0], "prior_close": pc})


@register(
    "noise_band_trend_usd",
    hypothesis="The noise-band trend effect is a property of intraday markets, not of MES 2020-2025: the "
               "UNCHANGED rule, with its stop expressed as the same $150 per contract, is positive after "
               "costs on other liquid futures (Nasdaq, gold, euro).",
    disproof="Pooled walk-forward net expectancy <= 0, or t < 3 on the pooled test trades, or fewer than a "
             "majority of windows positive.",
    defaults={"nbt_stop_usd": 150.0, "noise_lookback_days": 14,
              "slippage_applies_to": ["entry", "stop", "stop_gap", "time", "band"]},
)
def noise_band_trend_usd(day_bars, prior_levels, params):
    return noise_band_trend(day_bars, prior_levels, params)
