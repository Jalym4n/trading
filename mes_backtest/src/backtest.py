"""Run one strategy over a list of instruments and a date range; apply per-instrument costs.

Phase 3 plumbing. The SAME strategy callable runs on every instrument; nothing
instrument-specific lives in the strategy. Costs, point values and tick sizes
come from config instrument_specs[<instrument>]. Every output row carries an
`instrument` column, and summaries are produced per instrument AND pooled.

  strategy          callable(day_bars, prior_levels, params) -> DayResult (src.strategy)
  run(...)          -> RunResult(trade_log, day_log, window, instruments)
  apply_costs(...)  gross and net P&L for one trade, in points and dollars
  counts(...)       per-instrument + POOLED day categories and trade frequency

Costs (config params + instrument_specs):
  slippage          slippage_ticks x tick_size per fill, on the entry and on any
                    market exit (stop, stop_gap, time). A target is a limit order
                    and fills at the target price. `slippage_mult` scales it
                    (2.0 = the doubled-slippage robustness test).
  commission        commission_rt_usd per round turn.
  R                 r_net = net_usd / planned risk (risk_pts x point value).

Development window: run() never includes the 2026 holdout
(config validation.oos_holdout). `end` is clamped to the day before it and a
`start` inside it raises. The only exception is the private `_allow_holdout`
flag, reserved for guards.run_final_holdout (phase 4). P&L statistics are
stats.py's job (phase 5); this module's CLI prints counts only.

    python -m src.backtest --strategy confluence_off --start 2020-01-01 --end 2021-12-31
    python -m src.backtest --strategy confluence_on --set min_leg_points=0
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import asdict, dataclass

import pandas as pd

from .load import ROOT, load_config, read_processed
from .strategy import DAY_CATEGORIES, DayResult, day_category, run_day

# Phase 4 replaces this with the registry; the two A/B arms are the only strategies for now.
STRATEGIES = {
    "confluence_on": lambda bars, levels, p: run_day(bars, levels, p, confluence=True),
    "confluence_off": lambda bars, levels, p: run_day(bars, levels, p, confluence=False),
}
NAN_LEVELS = {"poc": float("nan"), "vah": float("nan"), "val": float("nan")}


@dataclass
class RunResult:
    strategy: str
    instruments: list
    window: tuple                 # (start, end) actually used, ISO dates
    params: dict
    trade_log: pd.DataFrame       # one row per trade, with costs
    day_log: pd.DataFrame         # one row per instrument-day, with its category


# ------------------------------------------------------------------ config --
def instrument_spec(cfg: dict, instrument: str) -> dict:
    try:
        return cfg["instrument_specs"][instrument]
    except KeyError:
        raise KeyError(f"{instrument} is not in config instrument_specs ({list(cfg['instrument_specs'])})") from None


def development_end(cfg: dict) -> dt.date:
    """The last date development code may see: the day before the holdout starts."""
    return dt.date.fromisoformat(cfg["validation"]["oos_holdout"]["start"]) - dt.timedelta(days=1)


def clamp_window(cfg: dict, start: str | None, end: str | None, _allow_holdout: bool = False) -> tuple[str, str, str | None]:
    """(start, end, note). Without _allow_holdout the window can never touch the holdout."""
    dev_end = development_end(cfg)
    s = dt.date.fromisoformat(start) if start else dt.date.fromisoformat(cfg["data_source"]["start"])
    e = dt.date.fromisoformat(end) if end else dev_end
    note = None
    if not _allow_holdout:
        if s > dev_end:
            raise ValueError(f"start {s} is inside the holdout (from {dev_end + dt.timedelta(days=1)}); "
                             "development code never runs there")
        if e > dev_end:
            note = f"end clamped from {e} to {dev_end}: the holdout is only reachable via run_final_holdout"
            e = dev_end
    if s > e:
        raise ValueError(f"empty window {s} > {e}")
    return s.isoformat(), e.isoformat(), note


# ------------------------------------------------------------------- costs --
def apply_costs(trade: dict, spec: dict, params: dict, slippage_mult: float = 1.0) -> dict:
    sgn = 1 if trade["side"] == "long" else -1
    pv = spec["point_value_usd"]
    slip = params["slippage_ticks"] * spec["tick_size"] * slippage_mult
    slip_pts = slip * (1 + (trade["exit_reason"] in params["slippage_applies_to"]))     # entry + market exit
    gross_pts = sgn * (trade["exit"] - trade["entry"])
    net_pts = gross_pts - slip_pts
    gross_usd = gross_pts * pv
    net_usd = net_pts * pv - spec["commission_rt_usd"]
    risk_usd = trade["risk_pts"] * pv
    return {**trade, "point_value_usd": pv, "gross_pts": gross_pts, "slippage_pts": slip_pts, "net_pts": net_pts,
            "gross_usd": round(gross_usd, 4), "commission_usd": spec["commission_rt_usd"],
            "net_usd": round(net_usd, 4), "risk_usd": risk_usd,
            "r_gross": gross_pts / trade["risk_pts"] if trade["risk_pts"] else float("nan"),
            "r_net": net_usd / risk_usd if risk_usd else float("nan")}


# --------------------------------------------------------------------- run --
def run(cfg: dict, strategy, instruments: list | None = None, start: str | None = None, end: str | None = None,
        params: dict | None = None, slippage_mult: float = 1.0, name: str = "strategy",
        _allow_holdout: bool = False) -> RunResult:
    p = dict(cfg["params"], **(params or {}))
    instruments = list(instruments or p["instruments"])
    s, e, note = clamp_window(cfg, start, end, _allow_holdout)
    if note:
        print(f"NOTE: {note}")
    s_ts, e_ts = pd.Timestamp(s), pd.Timestamp(e)
    trades, days = [], []
    for inst in instruments:
        spec = instrument_spec(cfg, inst)
        d = read_processed(inst)
        m5, sess = d["m5"], d["sessions"]
        lv = d.get("levels")
        if lv is None:
            raise FileNotFoundError(f"{inst} has no levels: run  python -m src.profile --instrument {inst}")
        sess = sess[(sess.index >= s_ts) & (sess.index <= e_ts)]
        for day, row in sess.iterrows():
            res: DayResult | None = None
            if not (row["roll_skip"] or row["data_skip"]):
                levels = lv.loc[day, ["poc", "vah", "val"]].to_dict() if day in lv.index else NAN_LEVELS
                res = strategy(m5[m5["date"] == day], levels, p)
            cat = day_category(bool(row["roll_skip"]), bool(row["data_skip"]), res)
            days.append({"instrument": inst, "date": day.date().isoformat(), "contract": row["contract"],
                         "category": cat, "n_trades": len(res.trades) if res else 0,
                         "n_risk_skips": len(res.detail.get("risk_skips", [])) if res else 0,
                         "skip_reason": row.get("skip_reason")})
            for t in (res.trades if res else []):
                trades.append(apply_costs({"instrument": inst, "contract": row["contract"], **asdict(t)}, spec, p,
                                          slippage_mult))
    cols = ["instrument", "contract", "date", "side", "zone", "node", "fib", "fib_price", "entry_bar", "entry", "stop",
            "target", "target_source", "exit_bar", "exit", "exit_reason", "same_bar_stop_and_target", "risk_pts",
            "or_high", "or_low", "breakout_time", "leg_start", "leg_end", "leg_confirm_time", "point_value_usd",
            "gross_pts", "slippage_pts", "net_pts", "gross_usd", "commission_usd", "net_usd", "risk_usd", "r_gross",
            "r_net"]
    tl = pd.DataFrame(trades, columns=cols)
    return RunResult(name, instruments, (s, e), p, tl, pd.DataFrame(days))


def weeks_in(day_log: pd.DataFrame, cfg: dict) -> float:
    """Calendar weeks covered by the instrument-days in the log (trading days x 0.2)."""
    return day_log.groupby("instrument")["date"].nunique().max() * cfg["viability_gates"]["weeks_per_trading_day"]


def counts(result: RunResult, cfg: dict) -> pd.DataFrame:
    """Per instrument + POOLED: days by category, trades, trades per week (Gate 1 input).
    Pooled trades/week counts every instrument's trades over the same calendar weeks."""
    rows = []
    groups = [(i, result.day_log[result.day_log["instrument"] == i]) for i in result.instruments]
    if len(result.instruments) > 1:
        groups.append(("POOLED", result.day_log))
    for name, dl in groups:
        wk = weeks_in(dl, cfg) if len(dl) else float("nan")
        n_tr = int(dl["n_trades"].sum())
        row = {"instrument": name, "days": len(dl), "trades": n_tr,
               "trades_per_week": round(n_tr / wk, 2) if wk else float("nan"),
               "risk_skips": int(dl["n_risk_skips"].sum())}
        row.update({c: int((dl["category"] == c).sum()) for c in DAY_CATEGORIES})
        rows.append(row)
    return pd.DataFrame(rows).set_index("instrument")


def _parse_set(items: list[str] | None) -> dict:
    out = {}
    for kv in items or []:
        k, v = kv.split("=", 1)
        try:
            out[k] = int(v) if v.lstrip("-").isdigit() else float(v)
        except ValueError:
            out[k] = {"true": True, "false": False}.get(v.lower(), v)
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strategy", default="confluence_off", choices=sorted(STRATEGIES))
    ap.add_argument("--instrument", action="append")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--set", action="append", metavar="PARAM=VALUE", help="override a config param (repeatable)")
    ap.add_argument("--slippage-mult", type=float, default=1.0)
    a = ap.parse_args(argv)
    cfg = load_config()
    res = run(cfg, STRATEGIES[a.strategy], a.instrument, a.start, a.end, _parse_set(a.set), a.slippage_mult,
              a.strategy)
    print(f"{a.strategy}  {res.window[0]} -> {res.window[1]}  instruments {res.instruments}  "
          f"overrides {_parse_set(a.set) or 'none'}")
    print(counts(res, cfg).T.to_string())
    out = ROOT / "results" / f"trade_log_{a.strategy}_{res.window[0]}_{res.window[1]}.csv"
    out.parent.mkdir(exist_ok=True)
    res.trade_log.to_csv(out, index=False)
    print(f"\ntrade log ({len(res.trade_log)} rows, costs applied, no statistics printed here): {out}")


if __name__ == "__main__":
    main()
