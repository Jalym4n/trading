"""Candle Range Theory on hourly ranges. Rules fixed 2026-11-12 before any run.

Bars: 5-min RTH, labels = bar OPEN (ET). Hour k = bars 12k .. 12k+11 (09:30-10:30, 10:30-11:30, ...).
  range     each completed hour: CRH = its high, CRL = its low. It is traded during the NEXT hour.
  sweep     a 5-min bar trades below CRL and closes back at or above it (long), or above CRH and
            closes back at or below it (short). A 5-min CLOSE outside the range before any sweep
            is a genuine breakout: that range is dropped. A bar that pierces both sides is ignored.
  shift     within the next crt_shift_bars (3) bars, a bar closes beyond the sweep bar's opposite
            end (above its high for a long). A close back outside the range first cancels.
  entry     at that close, on bars opening no later than 15:00.
  stop      one tick beyond the lowest low (highest high) from the sweep bar to the entry bar.
            Entry-to-stop above max_risk_points (30) -> not a trade.
  target    the opposite side of the range (CRH for a long); must be beyond the entry.
  exit      stop first if both are possible, from the bar after entry; open at 15:55 -> its close.
  limits    one attempt per range candle, max_trades_day (2), one position at a time.
"""

import numpy as np

from ..registry import register
from ..strategy import DayResult, Trade

DEFAULTS = {"crt_shift_bars": 3, "crt_last_entry": "15:00", "max_risk_points": 30.0, "max_trades_day": 2,
            "crt_tick": 0.25, "crt_bars_per_range": 12}


@register(
    "crt_hourly",
    hypothesis="A 5-minute sweep of the prior hour's high or low that closes back inside the hour's range, "
               "followed by a close through the sweep bar's other end, travels to the opposite side of that "
               "range often enough to pay costs.",
    disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
    defaults=DEFAULTS,
)
def crt_hourly(day_bars, prior_levels, params):
    p = {**DEFAULTS, **params}
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) < 78:
        return DayResult("short-day")
    o, h, l, c = (day_bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))   # noqa: E741
    n, B = len(c), p["crt_bars_per_range"]
    date = day_bars.index[0].strftime("%Y-%m-%d")
    nan = float("nan")
    trades, det = [], {"sweeps": 0, "breakouts": 0, "risk_skips": []}
    pos = sweep = None
    done_range = -1                                   # index of the last range candle already used
    for i in range(B, n):
        if pos is not None:
            side, sgn, entry, stop, target, ei, k = pos
            hit_stop = (l[i] <= stop) if sgn == 1 else (h[i] >= stop)
            hit_tgt = (h[i] >= target) if sgn == 1 else (l[i] <= target)
            if hit_stop:
                gap = (o[i] <= stop) if sgn == 1 else (o[i] >= stop)
                px, reason = (o[i], "stop_gap") if gap else (stop, "stop")
            elif hit_tgt:
                px, reason = target, "target"
            elif i == n - 1:
                px, reason = c[i], "time"
            else:
                continue
            trades.append(Trade(date, side, f"H{k}", target, nan, nan, labels[ei], entry, stop, target,
                                "CRH" if sgn == 1 else "CRL", labels[i], px, reason, False, abs(entry - stop), nan, nan,
                                "", nan, nan, ""))
            pos = None
            if len(trades) >= p["max_trades_day"]:
                break
            continue
        k = i // B - 1                                 # the range candle traded during this bar's hour
        if sweep is not None:                          # waiting for the structure shift
            sgn, crh, crl, si, trig, ext, sk = sweep
            ext = min(ext, l[i]) if sgn == 1 else max(ext, h[i])
            outside = c[i] < crl if sgn == 1 else c[i] > crh
            if outside or i - si > p["crt_shift_bars"]:
                sweep = None
            elif (c[i] > trig if sgn == 1 else c[i] < trig):
                entry = c[i]
                stop = ext - sgn * p["crt_tick"]
                target = crh if sgn == 1 else crl
                risk = sgn * (entry - stop)
                sweep = None
                if labels[i] > p["crt_last_entry"] or sgn * (target - entry) <= 0 or risk <= 0:
                    continue
                if risk > p["max_risk_points"] + 1e-9:
                    det["risk_skips"].append({"bar": labels[i], "zone": f"H{sk}", "entry": entry, "stop": stop,
                                              "risk_pts": risk, "max_risk_points": p["max_risk_points"]})
                    continue
                pos = ("long" if sgn == 1 else "short", sgn, entry, stop, target, i, sk)
                continue
            else:
                sweep = (sgn, crh, crl, si, trig, ext, sk)
                continue
        if k < 0 or k <= done_range:
            continue
        crh, crl = h[k * B:(k + 1) * B].max(), l[k * B:(k + 1) * B].min()
        below, above = l[i] < crl, h[i] > crh
        if c[i] > crh or c[i] < crl:                   # genuine breakout: drop this range
            done_range = k
            det["breakouts"] += 1
        elif below and above:
            done_range = k
        elif below:                                    # swept the low, closed back inside
            sweep, done_range = (1, crh, crl, i, h[i], l[i], k), k
            det["sweeps"] += 1
        elif above:
            sweep, done_range = (-1, crh, crl, i, l[i], h[i], k), k
            det["sweeps"] += 1
    if trades:
        return DayResult("traded", trades, det)
    if det["risk_skips"]:
        return DayResult("risk-too-large", [], det)
    return DayResult("no-sweep" if det["sweeps"] == 0 else "no-trigger", [], det)
