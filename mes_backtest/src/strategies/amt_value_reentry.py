"""Auction-market "80% rule": open outside the prior value area, accepted back inside -> trade to
the far side, with a session-VWAP filter. The mechanical core of an AMT / TPO / VWAP framework
(the user's "Orochi" description; its footprint, delta and Elliott-wave parts are not testable on
1-minute bars and are not here). Rules fixed 2026-11-12 before any run.

Bars: 5-min RTH, labels = bar OPEN (ET). VAH / VAL = prior session's 70% volume value area.
  setup       today's 09:30 open is above VAH (short setup) or below VAL (long setup).
  acceptance  two CONSECUTIVE 30-minute periods (09:30-10:00, 10:00-10:30, ...) whose last 5-min
              close is inside [VAL, VAH].
  VWAP        session VWAP from 09:30 (5-min typical price x volume). At the entry bar the close
              must be below VWAP for a short, above it for a long; otherwise no trade that day.
  entry       at the close of the second accepted period, if that period ends by 13:00.
  target      the opposite value-area edge (VAL for shorts, VAH for longs).
  stop        amt_stop_pad (2) points beyond the edge price came back through. Entry-to-stop
              above max_risk_points (30) -> not a trade. Target must be beyond the entry.
  exit        stop first if both are possible; from the bar after entry. Open at 15:55 -> its close.
  limit       one trade a day.
"""

import numpy as np

from ..registry import register
from ..strategy import DayResult, Trade

DEFAULTS = {"amt_stop_pad": 2.0, "amt_last_entry_close": "13:00", "max_risk_points": 30.0}
PERIOD_ENDS = [f"{h:02d}:{m:02d}" for h in range(9, 13) for m in (25, 55)][1:]      # 09:55 ... 12:55


@register(
    "amt_value_reentry",
    hypothesis="When the market opens outside the prior session's value area and is accepted back inside it "
               "for two 30-minute periods, it tends to rotate to the other side of the value area (the '80% "
               "rule'); taken on the VWAP side, that rotation pays costs.",
    disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
    defaults=DEFAULTS,
)
def amt_value_reentry(day_bars, prior_levels, params):
    p = {**DEFAULTS, **params}
    vah, val = prior_levels.get("vah"), prior_levels.get("val")
    if vah is None or val is None or not (np.isfinite(vah) and np.isfinite(val)) or vah <= val:
        return DayResult("no-levels")
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) < 78:
        return DayResult("short-day")
    o, h, l, c = (day_bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    v = day_bars["volume"].to_numpy(float) if "volume" in day_bars else np.ones(len(c))
    if o[0] > vah:
        side, sgn = "short", -1
    elif o[0] < val:
        side, sgn = "long", 1
    else:
        return DayResult("no-signal", detail={"open": o[0], "vah": vah, "val": val})
    vwap = np.cumsum((h + l + c) / 3.0 * v) / np.maximum(np.cumsum(v), 1e-9)
    det = {"side": side, "open": o[0], "vah": vah, "val": val}
    run, entry_i = 0, None
    for lab in PERIOD_ENDS:
        if lab not in labels:
            break
        i = labels.index(lab)
        run = run + 1 if val <= c[i] <= vah else 0
        if run >= 2:
            entry_i = i
            break
    if entry_i is None or labels[entry_i] > "12:55":
        return DayResult("no-trigger", detail=det)
    i = entry_i
    entry = c[i]
    if sgn * (entry - vwap[i]) <= 0:
        return DayResult("no-zone", detail={**det, "vwap": float(vwap[i]), "entry": entry})     # wrong side of VWAP
    edge_in, target = (vah, val) if side == "short" else (val, vah)
    stop = edge_in - sgn * p["amt_stop_pad"]
    risk = sgn * (entry - stop)
    if sgn * (target - entry) <= 0 or risk <= 0:
        return DayResult("no-trigger", detail=det)
    if risk > p["max_risk_points"] + 1e-9:
        det["risk_skips"] = [{"bar": labels[i], "zone": "VA", "entry": entry, "stop": stop, "risk_pts": risk,
                              "max_risk_points": p["max_risk_points"]}]
        return DayResult("risk-too-large", detail=det)
    reason, px, exit_i = "time", c[-1], len(c) - 1
    for j in range(i + 1, len(c)):
        hit_stop = (l[j] <= stop) if side == "long" else (h[j] >= stop)
        hit_tgt = (h[j] >= target) if side == "long" else (l[j] <= target)
        if hit_stop:
            gap = (o[j] <= stop) if side == "long" else (o[j] >= stop)
            reason, px, exit_i = ("stop_gap", o[j], j) if gap else ("stop", stop, j)
            break
        if hit_tgt:
            reason, px, exit_i = "target", target, j
            break
    nan = float("nan")
    t = Trade(day_bars.index[0].strftime("%Y-%m-%d"), side, "VA", edge_in, nan, nan, labels[i], entry, stop, target,
              "VAL" if side == "short" else "VAH", labels[exit_i], px, reason, False, risk, nan, nan, "", nan, nan, "")
    return DayResult("traded", [t], det)
