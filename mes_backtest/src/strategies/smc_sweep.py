"""Liquidity sweep -> break of structure -> order block / fair value gap in discount ("SMC" / ICT
composite). Rules fixed 2026-11-12 before any run; every number is a round default, none fitted.

Bars: 5-min RTH. Decisions use closed bars only; times are bar OPEN labels (ET).
  daily bias   prior session close above the mean of the 20 prior session closes -> longs only;
               below -> shorts only. (Described for longs; shorts mirror.)
  liquidity    lows that stops rest under: the prior session's low, and every confirmed swing
               low of today. Swing low = a bar whose low is below the lows of the 3 bars before
               it and not above the lows of the 3 bars after it (known 3 bars later).
  sweep        a bar trades below an unswept liquidity low L and closes back above L, or
               closes below it and the NEXT bar closes back above L.
  BOS          within 12 bars after the sweep, a bar closes above the most recent swing high
               confirmed before the sweep. No such swing high, or no break in 12 bars -> no setup.
               A lower low before the break extends the sweep low.
  leg          sweep low -> highest high through the BOS bar. Equilibrium = the 50% level.
  zones        fair value gaps inside the leg (low[j] > high[j-2]: the gap between them) and the
               order block (the last down-close bar at or before the sweep-low bar, up to 6
               bars back: its low..high). Only the part of a zone at or below equilibrium
               (discount) is used. No zone reaching into discount -> no trade.
  entry        resting limit at the highest discount zone top, for 12 bars after the BOS bar,
               on bars opening no later than 15:00. A bar opening below the limit fills at its open.
  stop         one tick below the sweep low. Entry-to-stop above max_risk_points -> not a trade.
  target       the leg high. Stop wins if both are possible; on the fill bar only the stop is
               checked. Still open at 15:55 -> exit at that bar's close.
  limits       2 trades a day; each setup needs a fresh sweep.
"""

import numpy as np

from ..registry import register
from ..strategy import DayResult, Trade

DEFAULTS = {"smc_swing_n": 3, "smc_bos_bars": 12, "smc_fill_bars": 12, "smc_ob_lookback": 6, "smc_bias_sma": 20,
            "smc_last_entry": "15:00", "max_risk_points": 30.0, "max_trades_day": 2, "smc_tick": 0.25}


@register(
    "smc_sweep_bos",
    hypothesis="After a stop-run below an obvious low that is immediately reclaimed, and a close through the "
               "prior swing high, price that returns to an order block or fair value gap in the lower half of "
               "the new leg continues to the leg high often enough to pay costs, when traded with the daily bias.",
    disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
    defaults=DEFAULTS,
)
def smc_sweep_bos(day_bars, prior_levels, params):
    p = {**DEFAULTS, **params}
    sma, pc = prior_levels.get("daily_sma"), prior_levels.get("prior_close")
    ph, pl = prior_levels.get("prior_high"), prior_levels.get("prior_low")
    if any(v is None or not np.isfinite(v) for v in (sma, pc, ph, pl)):
        return DayResult("no-levels")
    if pc == sma:
        return DayResult("no-bias")
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) < 78:
        return DayResult("short-day")
    sgn = 1 if pc > sma else -1
    side = "long" if sgn == 1 else "short"
    # mirror shorts into "long space": work on -price with high/low swapped, flip back at the end
    o = sgn * day_bars["open"].to_numpy(float)
    c = sgn * day_bars["close"].to_numpy(float)
    h = sgn * (day_bars["high"] if sgn == 1 else day_bars["low"]).to_numpy(float)
    l = sgn * (day_bars["low"] if sgn == 1 else day_bars["high"]).to_numpy(float)   # noqa: E741
    n, N = len(labels), p["smc_swing_n"]
    date = day_bars.index[0].strftime("%Y-%m-%d")
    liq = [sgn * pl if sgn == 1 else sgn * ph]          # unswept liquidity lows (long space)
    swing_highs = []                                    # (bar index, price), confirmed
    trades, state, det = [], "scan", {"side": side, "sweeps": 0, "bos": 0, "risk_skips": []}
    setup = pos = None
    i = 0
    while i < n:
        # --- swings confirmed at the close of bar i (pivot = i - N)
        q = i - N
        if q - N >= 0:
            if l[q] < l[q - N:q].min() and l[q] <= l[q + 1:i + 1].min():
                liq.append(l[q])
            if h[q] > h[q - N:q].max() and h[q] >= h[q + 1:i + 1].max():
                swing_highs.append((q, h[q]))
        if state == "pos":
            entry, stop, target, fill_i = pos
            if l[i] <= stop:
                px, reason = (o[i], "stop_gap") if (i > fill_i and o[i] <= stop) else (stop, "stop")
            elif i > fill_i and h[i] >= target:
                px, reason = target, "target"
            elif i == n - 1:
                px, reason = c[i], "time"
            else:
                i += 1
                continue
            trades.append(Trade(date, side, setup["zone"], sgn * setup["level"], float("nan"), float("nan"),
                                labels[fill_i], sgn * entry, sgn * stop, sgn * target, "leg_high", labels[i], sgn * px,
                                reason, False, entry - stop, float("nan"), float("nan"), labels[setup["bos_i"]],
                                sgn * setup["low"], sgn * target, labels[setup["sweep_i"]]))
            state, setup, pos = "scan", None, None
            if len(trades) >= p["max_trades_day"]:
                break
            i += 1
            continue
        if state == "scan":
            hit = [L for L in liq if (l[i] < L and c[i] > L) or (i > 0 and c[i - 1] <= L and l[i - 1] < L and c[i] > L)]
            if hit:
                lo = min(l[i], l[i - 1]) if i > 0 and any(c[i - 1] <= L for L in hit) else l[i]
                lo_i = i if l[i] <= lo else i - 1
                liq = [L for L in liq if L < lo]                       # everything pierced is spent
                prior_sh = [x for x in swing_highs if x[0] < lo_i]
                det["sweeps"] += 1
                if prior_sh:
                    setup = {"sweep_i": i, "low": lo, "low_i": lo_i, "ref_high": prior_sh[-1][1], "level": max(hit)}
                    state = "bos"
            i += 1
            continue
        if state == "bos":
            if l[i] < setup["low"]:
                setup["low"], setup["low_i"] = l[i], i
                liq = [L for L in liq if L < l[i]]
            if c[i] > setup["ref_high"]:
                leg_high = h[setup["low_i"]:i + 1].max()
                eq = (setup["low"] + leg_high) / 2.0
                zones = []
                for j in range(setup["low_i"] + 2, i + 1):              # fair value gaps inside the leg
                    if l[j] > h[j - 2]:
                        zones.append(("FVG", h[j - 2], l[j]))
                for m in range(setup["low_i"], max(-1, setup["low_i"] - p["smc_ob_lookback"]), -1):
                    if c[m] < o[m]:                                     # order block: last down-close bar
                        zones.append(("OB", l[m], h[m]))
                        break
                cands = [(min(top, eq), name) for name, bot, top in zones if bot < eq]
                det["bos"] += 1
                if not cands:
                    state, setup = "scan", None
                    det["no_zone"] = det.get("no_zone", 0) + 1
                else:
                    lvl, name = max(cands)
                    lvl = np.floor(lvl / p["smc_tick"]) * p["smc_tick"]
                    setup.update({"bos_i": i, "leg_high": leg_high, "entry": lvl, "zone": name,
                                  "stop": setup["low"] - p["smc_tick"]})
                    state = "fill"
            elif i - setup["sweep_i"] >= p["smc_bos_bars"]:
                state, setup = "scan", None
            i += 1
            continue
        if state == "fill":
            if i - setup["bos_i"] > p["smc_fill_bars"] or labels[i] > p["smc_last_entry"]:
                state, setup = "scan", None
                continue                                               # re-examine this bar as a scan bar
            if l[i] <= setup["entry"]:
                entry = min(setup["entry"], o[i])
                risk = entry - setup["stop"]
                if risk <= 0 or entry >= setup["leg_high"]:
                    state, setup = "scan", None
                elif risk > p["max_risk_points"] + 1e-9:
                    det["risk_skips"].append({"bar": labels[i], "zone": setup["zone"], "entry": sgn * entry,
                                              "stop": sgn * setup["stop"], "risk_pts": risk,
                                              "max_risk_points": p["max_risk_points"]})
                    state, setup = "scan", None
                else:
                    pos, state = (entry, setup["stop"], setup["leg_high"], i), "pos"
                    continue                                           # the fill bar can also stop the trade
            i += 1
            continue
    if trades:
        return DayResult("traded", trades, det)
    if det["risk_skips"]:
        return DayResult("risk-too-large", [], det)
    if det["sweeps"] == 0:
        return DayResult("no-sweep", [], det)
    if det["bos"] == 0:
        return DayResult("no-bos", [], det)
    return DayResult("no-zone" if det.get("no_zone") else "no-trigger", [], det)
