"""The rule set (spec section 1) as a pure function: (day bars, prior levels) -> trades.

Deterministic, no I/O, no state between days. Bars are processed strictly in
time order; every decision at bar i uses bars <= i only. Costs (commission,
slippage) are NOT applied here; backtest.py applies them to the raw fills.

Timing on the 5-min grid (bars labelled by OPEN time, all from config.json):
  opening range   bars 09:30, 09:35, 09:40 (OR = their high / low)
  breakout        first bar opening 09:45..10:25 (so it CLOSES by 10:30) whose
                  close is outside [OR_low, OR_high]; none -> no trade
  leg             start = the broken OR boundary. The leg is CONFIRMED by the first
                  bar after the breakout bar whose colour is opposite (red for a
                  long leg: close < open; a doji is not opposite). Leg end = the
                  extreme from the breakout bar through the confirming bar
                  inclusive, all known at the confirming bar's close. No
                  confirmation before the window ends -> no trade.
  min leg         a confirmed leg shorter than min_leg_points -> "leg-too-small"
                  (both confluence modes, so the A/B differs only in the filter).
  fibs            fib_f = end - f * (end - start) for every f in fib_levels.
  zones           confluence ON: a zone is a volume node (POC / VAH / VAL) with a
                  fib in confluence_fib_levels within confluence_tol points. The
                  zone spans the node and that fib (the nearest one if several).
                  confluence OFF (the A/B test): every fib in
                  confluence_fib_levels is a zone by itself, node = the fib price.
                  Zones must lie strictly inside the leg (between start and end).
                  They are traded shallowest first.
  scanning        from the confirming bar on. For the active zone, a bar that
                  trades through the zone's stop (low <= stop for a long) kills it
                  and the next deeper zone becomes active (that same bar may touch
                  it). A bar whose low reaches the zone top (long) touches it.
  entry trigger   on or after the touch bar (and after the confirming bar), a bar
                  in the trade direction (close > open for a long) with its close
                  in the favourable third of its range: (close - low) >= 2/3 (high
                  - low). Entry at that bar's close. Last entry bar opens at 10:50
                  (closes 10:55); nothing enters at or after session_end.
  stop            node - stop_pad (long) / node + stop_pad (short).
  risk cap        a trigger whose entry-to-stop distance exceeds max_risk_points is
                  NOT a trade: it is logged in detail["risk_skips"] and the zone
                  stays live. A day whose only signals were too risky is
                  "risk-too-large".
  target          target_mode "nearest_node" (default): the nearest volume node beyond
                  the entry in the trade direction; none -> the leg end; leg end not
                  beyond entry -> no trade. target_mode "r_multiple": entry +/-
                  target_r x (entry-to-stop distance), on the tick grid.
  exit            evaluated from the bar AFTER entry. Stop and target in the same
                  bar -> STOP (pessimistic). A bar opening through the stop fills at
                  its open; the target is a limit and fills at the target price.
                  Still open at session_end -> exit at the close of the last bar.
  day rules       max_trades_day trades. After a stop-out the remaining (deeper)
                  zones stay live; the exit bar can touch one but not trigger. The day ends after a
                  target, a time exit, or max losses (= max_trades_day).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

NODES = ("POC", "VAH", "VAL")


@dataclass
class Zone:
    node_name: str          # POC / VAH / VAL, or FIB_0.618 with confluence off
    node: float
    fib: float
    fib_price: float
    top: float
    bottom: float
    stop: float


@dataclass
class Trade:
    date: str
    side: str
    zone: str
    node: float
    fib: float
    fib_price: float
    entry_bar: str            # bar open label; the fill is that bar's CLOSE
    entry: float
    stop: float
    target: float
    target_source: str
    exit_bar: str             # bar open label of the exit bar (time exit: the last bar, filled at its close)
    exit: float
    exit_reason: str          # target / stop / stop_gap / time
    same_bar_stop_and_target: bool
    risk_pts: float
    or_high: float
    or_low: float
    breakout_time: str
    leg_start: float
    leg_end: float
    leg_confirm_time: str


@dataclass
class DayResult:
    status: str               # one of DAY_CATEGORIES below (strategy-side ones)
    trades: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _t(s: str) -> dt.time:
    return dt.datetime.strptime(s, "%H:%M").time()


def _minus(t: dt.time, minutes: int) -> dt.time:
    return (dt.datetime.combine(dt.date.min, t) - dt.timedelta(minutes=minutes)).time()


def build_zones(side: str, start: float, end: float, levels: dict, p: dict, confluence: bool) -> list[Zone]:
    sgn = 1 if side == "long" else -1
    fibs = {f: end - f * (end - start) for f in p["confluence_fib_levels"]}
    lo, hi = min(start, end), max(start, end)
    zones = []
    if confluence:
        for name in NODES:
            node = levels.get(name.lower())
            if node is None or not np.isfinite(node) or not (lo < node < hi):
                continue
            near = [(abs(fp - node), f, fp) for f, fp in fibs.items() if abs(fp - node) <= p["confluence_tol"] + 1e-9]
            if not near:
                continue
            _, f, fp = min(near)
            zones.append(Zone(name, node, f, fp, max(node, fp), min(node, fp), node - sgn * p["stop_pad"]))
    else:
        for f, fp in fibs.items():
            if lo < fp < hi:
                zones.append(Zone(f"FIB_{f}", fp, f, fp, fp, fp, fp - sgn * p["stop_pad"]))
    # shallowest first: nearest the leg end
    zones.sort(key=lambda z: -sgn * (z.top if side == "long" else z.bottom))
    return zones


def _target(side: str, entry: float, levels: dict, leg_end: float) -> tuple[float | None, str]:
    nodes = [(levels.get(n.lower()), n) for n in NODES]
    nodes = [(v, n) for v, n in nodes if v is not None and np.isfinite(v)]
    if side == "long":
        beyond = sorted((v, n) for v, n in nodes if v > entry)
        if beyond:
            return beyond[0][0], beyond[0][1]
        return (leg_end, "leg_end") if leg_end > entry else (None, "none")
    beyond = sorted(((v, n) for v, n in nodes if v < entry), reverse=True)
    if beyond:
        return beyond[0][0], beyond[0][1]
    return (leg_end, "leg_end") if leg_end < entry else (None, "none")


def run_day(bars: pd.DataFrame, levels: dict, p: dict, confluence: bool = True) -> DayResult:
    """bars: one day's 5-min RTH bars (index = bar open time, market tz), columns open/high/low/close.
    levels: {"poc", "vah", "val"} from the prior session (NaN -> no trade)."""
    if any(levels.get(k) is None or not np.isfinite(levels.get(k)) for k in ("poc", "vah", "val")):
        return DayResult("no-levels")
    s0, s1, or_end = _t(p["session_start"]), _t(p["session_end"]), _t(p["or_end"])
    bar_min = int(pd.Timedelta(p["bar_size_strategy"]).total_seconds() // 60)
    w = bars[(bars.index.time >= s0) & (bars.index.time < s1)]
    expected = (dt.datetime.combine(dt.date.min, s1) - dt.datetime.combine(dt.date.min, s0)).seconds // 60 // bar_min
    if len(w) != expected:
        return DayResult("short-day", detail={"bars": len(w), "expected": expected})
    t = w.index.time
    o, h, l, c = (w[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = [x.strftime("%H:%M") for x in w.index]
    date = w.index[0].strftime("%Y-%m-%d")

    n_or = int((t < or_end).sum())
    or_high, or_low = h[:n_or].max(), l[:n_or].min()
    last_breakout_open = _minus(_t(p["breakout_deadline"]), bar_min)
    last_entry_open = _minus(s1, 2 * bar_min)
    det = {"or_high": or_high, "or_low": or_low}

    # ---- breakout: first close outside the OR, bar closing by the deadline
    b = next((i for i in range(n_or, len(w)) if t[i] <= last_breakout_open and (c[i] > or_high or c[i] < or_low)),
             None)
    if b is None:
        return DayResult("no-breakout", detail=det)
    side = "long" if c[b] > or_high else "short"
    sgn = 1 if side == "long" else -1
    start = or_high if side == "long" else or_low
    det.update({"side": side, "breakout_time": times[b], "leg_start": start})

    # ---- leg: confirmed by the first opposite-colour bar after the breakout bar
    k = next((i for i in range(b + 1, len(w)) if sgn * (c[i] - o[i]) < 0), None)
    if k is None:
        return DayResult("no-confirmation", detail=det)
    end = h[b:k + 1].max() if side == "long" else l[b:k + 1].min()
    det.update({"leg_end": end, "leg_confirm_time": times[k],
                "fibs": {f: end - f * (end - start) for f in p["fib_levels"]}})

    if abs(end - start) < p["min_leg_points"] - 1e-9:
        # fib precision is meaningless on a small leg: the three fibs sit within ~2 pts of each other
        det["leg_points"] = abs(end - start)
        return DayResult("leg-too-small", detail=det)
    zones = build_zones(side, start, end, levels, p, confluence)
    det["zones"] = [asdict(z) for z in zones]
    if not zones:
        return DayResult("no-zone", detail=det)

    trades, losses, zi, touched, i, no_entry_through = [], 0, 0, False, k, k
    while i < len(w) and zi < len(zones) and len(trades) < p["max_trades_day"]:
        z = zones[zi]
        # 1. invalidation: the bar trades through the active zone's stop
        if (side == "long" and l[i] <= z.stop) or (side == "short" and h[i] >= z.stop):
            zi, touched = zi + 1, False
            continue                                   # re-examine this bar against the next zone
        # 2. touch
        if (side == "long" and l[i] <= z.top) or (side == "short" and h[i] >= z.bottom):
            touched = True
        # 3. trigger (never on the confirming bar, never on or after the last entry bar)
        rng = h[i] - l[i]
        fav = (c[i] - l[i]) if side == "long" else (h[i] - c[i])
        if (touched and i > no_entry_through and t[i] <= last_entry_open and sgn * (c[i] - o[i]) > 0 and rng > 0
                and fav >= 2.0 / 3.0 * rng - 1e-9):
            entry = c[i]
            if p.get("target_mode", "nearest_node") == "r_multiple":
                # fixed reward:risk, rounded to the tick grid; always beyond the entry
                tk = p.get("target_tick", 0.25)
                target = round((entry + sgn * p["target_r"] * sgn * (entry - z.stop)) / tk) * tk
                tsrc = f"{p['target_r']:g}R"
            else:
                target, tsrc = _target(side, entry, levels, end)
            if target is None or sgn * (entry - z.stop) <= 0:
                zi, touched = zi + 1, False
                i += 1
                continue
            risk = sgn * (entry - z.stop)
            if risk > p["max_risk_points"] + 1e-9:
                # not takeable at the per-trade cap: logged, never counted as a trade; the zone stays
                # live, so a later trigger closer to the node can still qualify
                det.setdefault("risk_skips", []).append(
                    {"bar": times[i], "zone": z.node_name, "entry": entry, "stop": z.stop, "risk_pts": risk,
                     "max_risk_points": p["max_risk_points"]})
                i += 1
                continue
            # ---- manage the position from the next bar
            j, reason, px, both = i + 1, "time", c[-1], False
            while j < len(w):
                hit_stop = (l[j] <= z.stop) if side == "long" else (h[j] >= z.stop)
                hit_tgt = (h[j] >= target) if side == "long" else (l[j] <= target)
                if hit_stop:
                    gap = (o[j] <= z.stop) if side == "long" else (o[j] >= z.stop)
                    reason, px = ("stop_gap", o[j]) if gap else ("stop", z.stop)
                    both = bool(hit_tgt)
                    break
                if hit_tgt:
                    reason, px = "target", target
                    break
                j += 1
            exit_i = min(j, len(w) - 1)
            trades.append(Trade(date, side, z.node_name, z.node, z.fib, z.fib_price, times[i], entry, z.stop,
                                target, tsrc, times[exit_i], px, reason, both, abs(entry - z.stop), or_high, or_low,
                                times[b], start, end, times[k]))
            if reason in ("target", "time"):
                break
            losses += 1
            if losses >= p["max_trades_day"]:
                break
            # stopped out: this zone is spent. The exit bar is re-examined against the deeper zones
            # (it may touch one) but cannot itself be an entry bar.
            zi, touched, i, no_entry_through = zi + 1, False, exit_i, exit_i
            continue
        i += 1
    if trades:
        return DayResult("traded", trades, det)
    return DayResult("risk-too-large" if det.get("risk_skips") else "no-trigger", trades, det)


# one category per calendar day, in this precedence; every day lands in exactly one
DAY_CATEGORIES = ["roll-skip", "missing-bars", "no-levels", "short-day", "no-breakout", "no-confirmation",
                  "leg-too-small", "no-zone", "risk-too-large", "no-trigger", "traded"]


def day_category(roll_skip: bool, data_skip: bool, result: "DayResult | None") -> str:
    """Harness-side days (roll / missing bars) first, then the strategy's own status."""
    if roll_skip:
        return "roll-skip"
    if data_skip:
        return "missing-bars"
    return result.status


# --------------------------------------------------------------- hand check --
def trace(date: str, bars: pd.DataFrame, levels: dict, p: dict, confluence: bool = True) -> str:
    """Every bar of the window with the facts each rule reads, for a manual check."""
    r = run_day(bars, levels, p, confluence)
    s0, s1 = _t(p["session_start"]), _t(p["session_end"])
    w = bars[(bars.index.time >= s0) & (bars.index.time < s1)]
    d = r.detail
    out = [f"=== {date}  status: {r.status}  (confluence {'ON' if confluence else 'OFF'})",
           f"prior-session levels: VAL {levels['val']:g}  POC {levels['poc']:g}  VAH {levels['vah']:g}"]
    if "or_high" in d:
        out.append(f"OR (09:30-09:45): high {d['or_high']:g}, low {d['or_low']:g}")
    if "side" in d:
        out.append(f"breakout {d['breakout_time']} -> {d['side']}; leg start {d['leg_start']:g}")
    if "leg_end" in d:
        out.append(f"leg confirmed {d['leg_confirm_time']} (first opposite-colour bar); leg end {d['leg_end']:g}")
        out.append("fibs: " + ", ".join(f"{f}: {v:.2f}" for f, v in d["fibs"].items()))
        out.append("zones (shallowest first): " + ("; ".join(
            f"{z['node_name']} {z['node']:g} ~ fib {z['fib']} {z['fib_price']:.2f} -> zone {z['bottom']:.2f}-"
            f"{z['top']:.2f}, stop {z['stop']:g}" for z in d["zones"]) or "none"))
    out.append("bar     open     high      low    close  colour  close-in-range")
    for ts, b in w.iterrows():
        rng = b.high - b.low
        pos = (b.close - b.low) / rng if rng else float("nan")
        col = "green" if b.close > b.open else "red" if b.close < b.open else "doji"
        out.append(f"{ts:%H:%M}  {b.open:8.2f} {b.high:8.2f} {b.low:8.2f} {b.close:8.2f}  {col:6}  {pos:5.2f}")
    for rs in d.get("risk_skips", []):
        out.append(f"RISK-TOO-LARGE (not a trade): {rs['zone']} trigger on {rs['bar']}, entry {rs['entry']:g}, stop "
                   f"{rs['stop']:g}, risk {rs['risk_pts']:.2f} pts > {rs['max_risk_points']:g}")
    for t in r.trades:
        out.append(f"TRADE {t.side} {t.zone}: entry {t.entry:g} at close of {t.entry_bar}; stop {t.stop:g}; "
                   f"target {t.target:g} ({t.target_source}); exit {t.exit_reason} {t.exit:g} on {t.exit_bar}"
                   + (" [stop and target in one bar]" if t.same_bar_stop_and_target else ""))
    return "\n".join(out)


def main(argv=None) -> None:
    import argparse
    from .load import load_config, read_processed
    ap = argparse.ArgumentParser(description="trace one or more days for a hand check")
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--instrument", default=None, help="default: first of config params.instruments")
    ap.add_argument("--no-confluence", action="store_true")
    ap.add_argument("--pick", action="store_true", help="pick a long, a short and a no-trade day from 2020-2021")
    a = ap.parse_args(argv)
    cfg = load_config()
    p = cfg["params"]
    inst = a.instrument or p["instruments"][0]
    d_ = read_processed(inst)
    m5, lv, sess = d_["m5"], d_["levels"], d_["sessions"]
    dates = list(a.dates)
    if a.pick:
        # development years only: nothing from a walk-forward test year or the holdout is looked at
        pool = sess[(sess["tradeable"]) & (sess.index.year <= 2021)].index
        want = {"long": None, "short": None, "no-zone": None}
        for d in pool:
            if d not in lv.index:
                continue
            r = run_day(m5[m5["date"] == d], lv.loc[d, ["poc", "vah", "val"]].to_dict(), p, not a.no_confluence)
            key = r.trades[0].side if r.trades else r.status
            if key in want and want[key] is None:
                want[key] = d.strftime("%Y-%m-%d")
            if all(want.values()):
                break
        dates += [v for v in want.values() if v]
    for d in dates:
        ts = pd.Timestamp(d)
        print(f"[{inst}]")
        print(trace(d, m5[m5["date"] == ts], lv.loc[ts, ["poc", "vah", "val"]].to_dict(), p, not a.no_confluence))
        print()


if __name__ == "__main__":
    main()
