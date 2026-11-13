"""Four confluence strategies described by the user on 2026-11-12, mechanised with round defaults
fixed on 2026-11-13 BEFORE any run. Bars: 5-min RTH, labels = bar OPEN (ET). All share: entry at
the close of the trigger bar, no entry after 15:00, risk above max_risk_points (30) -> not a
trade, target = 2 x risk, stop first if both possible, flat at the 15:55 close, max 2 trades a day.

htf_sweep_choch_volume   A 5-min bar trades beyond the Asia (18:00-03:00), London (03:00-09:30) or
    prior-day high/low and closes back inside (sweep). Within 6 bars a bar closes beyond the highest
    high (lowest low) of the 3 bars before the sweep bar (change of character) on volume >= 1.5x
    the mean of the previous 20 bars. Enter with the reversal; stop one tick beyond the sweep extreme.

smt_divergence   MES against MNQ (the peer), bar by bar. MES trades below the lowest low of its
    previous 12 bars and closes back above it, while MNQ on the same bar does NOT trade below the
    lowest low of ITS previous 12 bars. Long MES; stop one tick below the bar low. Mirror for highs.

poc_ifvg_flip   A bullish fair value gap (low[j] > high[j-2]) lying within 2 points of the prior
    session POC is later closed through downward (a bar closes below the gap bottom AND below the
    POC): the gap is now an inverse FVG and the POC has flipped from support to resistance. Short on
    the first later bar that trades back up into the gap (high >= gap bottom) and closes below it,
    within 12 bars; stop one tick above the gap top. Mirror for bearish gaps closed through upward.

keltner_box_reversion   Daily box = prior close +/- the mean of the last 14 daily ranges. Keltner
    channel = EMA20 of closes +/- 2 x ATR20 on 5-min bars (today only). Stretch: a bar's low is
    below the daily box AND its close is below the lower Keltner band. Within 6 bars the 3-EMA of
    closes crosses above the 8-EMA: long; stop one tick below the lowest low since the stretch.
    Mirror above. (Target stays 2 x risk, like the others.)
"""

import numpy as np

from ..registry import register
from ..strategy import DayResult
from ._common import arrays, finite, make_trade, manage

BASE = {"max_risk_points": 30.0, "max_trades_day": 2, "uc_last_entry": "15:00", "uc_target_r": 2.0}


def _tick(p):
    return p.get("_tick_size", 0.25)


def _enter(trades, det, date, side, tag, labels, i, entry, stop, o, h, l, c, p, level=float("nan")):   # noqa: E741
    """Apply the shared entry rules; returns the bar index to resume scanning from, or None."""
    sgn = 1 if side == "long" else -1
    risk = sgn * (entry - stop)
    if labels[i] > p["uc_last_entry"] or risk <= 0:
        return None
    if risk > p["max_risk_points"] + 1e-9:
        det.setdefault("risk_skips", []).append({"bar": labels[i], "zone": tag, "entry": entry, "stop": stop,
                                                 "risk_pts": risk, "max_risk_points": p["max_risk_points"]})
        return None
    target = entry + sgn * p["uc_target_r"] * risk
    j, px, reason = manage(side, i, entry, stop, target, o, h, l, c)
    trades.append(make_trade(date, side, tag, labels, i, entry, stop, target, j, px, reason, level))
    return j


def _result(trades, det, n_setups):
    if trades:
        return DayResult("traded", trades, det)
    if det.get("risk_skips"):
        return DayResult("risk-too-large", [], det)
    return DayResult("no-trigger" if n_setups else "no-sweep", [], det)


# ------------------------------------------------------------------ 1 ----
@register("htf_sweep_choch_volume",
          hypothesis="A sweep of the Asia, London or prior-day high/low that closes back inside, followed within "
                     "30 minutes by a structure break on a volume spike, reverses far enough to pay 2R often "
                     "enough to beat costs.",
          disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
          defaults={**BASE, "need_overnight": True, "uc_choch_bars": 6, "uc_vol_mult": 1.5})
def htf_sweep_choch_volume(day_bars, prior_levels, params):
    p = {**BASE, "uc_choch_bars": 6, "uc_vol_mult": 1.5, **params}
    keys = ("asia_high", "asia_low", "london_high", "london_low", "prior_high", "prior_low")
    if not finite(*[prior_levels.get(k) for k in keys]):
        return DayResult("no-levels")
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) < 78:
        return DayResult("short-day")
    o, h, l, c = arrays(day_bars)                                                # noqa: E741
    v = day_bars["volume"].to_numpy(float)
    highs = sorted({prior_levels[k] for k in keys if k.endswith("high")})
    lows = sorted({prior_levels[k] for k in keys if k.endswith("low")})
    date, trades, det, setups = day_bars.index[0].strftime("%Y-%m-%d"), [], {}, 0
    pending, i = None, 20
    while i < len(c) and len(trades) < p["max_trades_day"]:
        if pending is not None:
            side, si, ext, trig = pending
            sgn = 1 if side == "long" else -1
            ext = min(ext, l[i]) if sgn == 1 else max(ext, h[i])
            if i - si > p["uc_choch_bars"]:
                pending = None
            elif sgn * (c[i] - trig) > 0 and v[i] >= p["uc_vol_mult"] * v[i - 20:i].mean():
                pending = None
                j = _enter(trades, det, date, side, "HTF", labels, i, c[i], ext - sgn * _tick(p), o, h, l, c, p)
                if j is not None:
                    i = j + 1
                    continue
            else:
                pending = (side, si, ext, trig)
        if pending is None:
            lo_hit = [x for x in lows if l[i] < x <= c[i]]
            hi_hit = [x for x in highs if h[i] > x >= c[i]]
            if lo_hit and not hi_hit:
                pending, setups = ("long", i, l[i], h[i - 3:i].max()), setups + 1
                lows = [x for x in lows if x < l[i]]
            elif hi_hit and not lo_hit:
                pending, setups = ("short", i, h[i], l[i - 3:i].min()), setups + 1
                highs = [x for x in highs if x > h[i]]
        i += 1
    return _result(trades, det, setups)


# ------------------------------------------------------------------ 2 ----
@register("smt_divergence",
          hypothesis="When MES sweeps its one-hour low or high and closes back while MNQ fails to make the "
                     "matching new extreme on the same bar, the MES move is a trap and reverses by 2R often "
                     "enough to beat costs.",
          disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
          defaults={**BASE, "peer_instrument": "MNQ", "uc_lookback": 12})
def smt_divergence(day_bars, prior_levels, params):
    p = {**BASE, "uc_lookback": 12, **params}
    peer = prior_levels.get("peer_bars")
    labels = list(day_bars.index.strftime("%H:%M"))
    if peer is None or len(peer) != len(day_bars) or len(labels) < 78 or list(peer.index) != list(day_bars.index):
        return DayResult("no-levels" if peer is None else "short-day")
    o, h, l, c = arrays(day_bars)                                                # noqa: E741
    ph, pl = peer["high"].to_numpy(float), peer["low"].to_numpy(float)
    n = p["uc_lookback"]
    date, trades, det, setups = day_bars.index[0].strftime("%Y-%m-%d"), [], {}, 0
    i = n
    while i < len(c) and len(trades) < p["max_trades_day"]:
        lo, hi = l[i - n:i].min(), h[i - n:i].max()
        plo, phi = pl[i - n:i].min(), ph[i - n:i].max()
        side = None
        if l[i] < lo <= c[i] and pl[i] > plo and not h[i] > hi:
            side, stop = "long", l[i] - _tick(p)
        elif h[i] > hi >= c[i] and ph[i] < phi and not l[i] < lo:
            side, stop = "short", h[i] + _tick(p)
        if side:
            setups += 1
            j = _enter(trades, det, date, side, "SMT", labels, i, c[i], stop, o, h, l, c, p)
            if j is not None:
                i = j + 1
                continue
        i += 1
    return _result(trades, det, setups)


# ------------------------------------------------------------------ 3 ----
@register("poc_ifvg_flip",
          hypothesis="A fair value gap at the prior session POC that is closed through becomes an inverse gap "
                     "and flips the POC from support to resistance (or the reverse); the first retest of the "
                     "gap rejects by 2R often enough to beat costs.",
          disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
          defaults={**BASE, "uc_poc_tol": 2.0, "uc_retest_bars": 12})
def poc_ifvg_flip(day_bars, prior_levels, params):
    p = {**BASE, "uc_poc_tol": 2.0, "uc_retest_bars": 12, **params}
    poc = prior_levels.get("poc")
    if not finite(poc):
        return DayResult("no-levels")
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) < 78:
        return DayResult("short-day")
    o, h, l, c = arrays(day_bars)                                                # noqa: E741
    date, trades, det, setups = day_bars.index[0].strftime("%Y-%m-%d"), [], {}, 0
    gaps, inverted = [], []                    # gaps: (kind, bottom, top); inverted: (side, bottom, top, bar)
    i = 2
    while i < len(c) and len(trades) < p["max_trades_day"]:
        # retest of an inverse gap (decided on bar i, gaps inverted on earlier bars only)
        done = False
        for z in list(inverted):
            side, bot, top, zi = z
            if i - zi > p["uc_retest_bars"]:
                inverted.remove(z)
                continue
            if side == "short" and h[i] >= bot and c[i] < bot:
                inverted.remove(z)
                j = _enter(trades, det, date, "short", "iFVG", labels, i, c[i], top + _tick(p), o, h, l, c, p, poc)
            elif side == "long" and l[i] <= top and c[i] > top:
                inverted.remove(z)
                j = _enter(trades, det, date, "long", "iFVG", labels, i, c[i], bot - _tick(p), o, h, l, c, p, poc)
            else:
                continue
            if j is not None:
                i, done = j + 1, True
            break
        if done:
            continue
        # inversion: a close through a gap near the POC that also puts price on the other side of the POC
        for g in list(gaps):
            kind, bot, top = g
            if kind == "bull" and c[i] < bot and c[i] < poc:
                gaps.remove(g)
                inverted.append(("short", bot, top, i))
                setups += 1
            elif kind == "bear" and c[i] > top and c[i] > poc:
                gaps.remove(g)
                inverted.append(("long", bot, top, i))
                setups += 1
        # new gaps formed by bar i, kept only if they sit at the POC
        if l[i] > h[i - 2] and h[i - 2] - p["uc_poc_tol"] <= poc <= l[i] + p["uc_poc_tol"]:
            gaps.append(("bull", h[i - 2], l[i]))
        if h[i] < l[i - 2] and h[i] - p["uc_poc_tol"] <= poc <= l[i - 2] + p["uc_poc_tol"]:
            gaps.append(("bear", h[i], l[i - 2]))
        i += 1
    return _result(trades, det, setups)


# ------------------------------------------------------------------ 4 ----
def _ema(x, n):
    out, a = np.empty(len(x)), 2.0 / (n + 1)
    out[0] = x[0]
    for k in range(1, len(x)):
        out[k] = a * x[k] + (1 - a) * out[k - 1]
    return out


@register("keltner_box_reversion",
          hypothesis="A push outside both the daily expected-range box and the 5-minute Keltner channel that is "
                     "followed by a 3/8 EMA cross back snaps back by 2R often enough to beat costs.",
          disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
          defaults={**BASE, "uc_cross_bars": 6, "uc_kc_mult": 2.0})
def keltner_box_reversion(day_bars, prior_levels, params):
    p = {**BASE, "uc_cross_bars": 6, "uc_kc_mult": 2.0, **params}
    pc, atr_d = prior_levels.get("prior_close"), prior_levels.get("daily_atr")
    if not finite(pc, atr_d):
        return DayResult("no-levels")
    labels = list(day_bars.index.strftime("%H:%M"))
    if len(labels) < 78:
        return DayResult("short-day")
    o, h, l, c = arrays(day_bars)                                                # noqa: E741
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    mid, atr = _ema(c, 20), _ema(tr, 20)
    up, dn = mid + p["uc_kc_mult"] * atr, mid - p["uc_kc_mult"] * atr
    e3, e8 = _ema(c, 3), _ema(c, 8)
    box_hi, box_lo = pc + atr_d, pc - atr_d
    date, trades, det, setups = day_bars.index[0].strftime("%Y-%m-%d"), [], {}, 0
    pending, i = None, 20                       # the channel needs 20 bars of today before it is used
    while i < len(c) and len(trades) < p["max_trades_day"]:
        if l[i] < box_lo and c[i] < dn[i]:
            pending, setups = ("long", i, l[i]), setups + 1
        elif h[i] > box_hi and c[i] > up[i]:
            pending, setups = ("short", i, h[i]), setups + 1
        elif pending is not None:
            side, si, ext = pending
            sgn = 1 if side == "long" else -1
            ext = min(ext, l[i]) if sgn == 1 else max(ext, h[i])
            if i - si > p["uc_cross_bars"]:
                pending = None
            elif sgn * (e3[i] - e8[i]) > 0 and sgn * (e3[i - 1] - e8[i - 1]) <= 0:
                pending = None
                j = _enter(trades, det, date, side, "KC", labels, i, c[i], ext - sgn * _tick(p), o, h, l, c, p)
                if j is not None:
                    i = j + 1
                    continue
            else:
                pending = (side, si, ext)
        i += 1
    return _result(trades, det, setups)
