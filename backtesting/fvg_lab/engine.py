"""Event-driven backtest engine.

Design rules, all of them about not cheating:
  1. A decision made on the CLOSE of bar t is filled at the OPEN of bar t+1.
  2. Costs are applied on every fill, in both directions, before any metric.
  3. When a bar's range contains both the stop and the target, the STOP is
     assumed to hit first. Without tick data you cannot know the order, and
     the pessimistic assumption is the only honest one.
  4. A bar that opens beyond the stop fills at the open, not at the stop.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .strategy import Bars, FVGStrategy, Params


@dataclass(frozen=True)
class Costs:
    tick_size: float = 0.25
    point_value: float = 50.0  # $ per 1.0 price move per unit (ES = 50)
    spread_ticks: float = 1.0  # full spread; half is paid per side
    slippage_ticks: float = 0.5  # per side, on top of spread
    commission_per_side: float = 1.25  # $ per unit per side

    @property
    def edge(self) -> float:
        """Adverse price offset paid on each fill, in price units."""
        return (self.spread_ticks / 2 + self.slippage_ticks) * self.tick_size

    def fill_price(self, price: float, direction: int, opening: bool) -> float:
        """Adverse fill. direction is +1 long / -1 short; opening or closing."""
        side = direction if opening else -direction
        return price + side * self.edge

    def double(self) -> "Costs":
        """Stress test. If the edge dies here, it was never there."""
        return Costs(
            self.tick_size,
            self.point_value,
            self.spread_ticks * 2,
            self.slippage_ticks * 2,
            self.commission_per_side * 2,
        )

    # ------------------------------------------------------------ presets --
    @staticmethod
    def spy_shares(commission_per_share: float = 0.0) -> "Costs":
        """SPY as shares. One 'point' is $1 of share price, one unit is 1 share.

        Penny spread, one tick of slippage. This is the cheapest expression of
        the signal and therefore the fairest test of whether the signal exists:
        if it cannot clear this cost model, nothing with wider spreads will.
        """
        return Costs(
            tick_size=0.01,
            point_value=1.0,
            spread_ticks=1.0,
            slippage_ticks=1.0,
            commission_per_side=commission_per_share,
        )

    @staticmethod
    def es() -> "Costs":
        """E-mini S&P futures."""
        return Costs(0.25, 50.0, 1.0, 0.5, 1.25)

    @staticmethod
    def mes() -> "Costs":
        """Micro E-mini S&P futures. 1/10th the size of ES."""
        return Costs(0.25, 5.0, 1.0, 0.5, 0.52)


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry: float
    exit: float
    stop: float
    target: float
    qty: float
    risk_points: float
    pnl_dollars: float
    r_multiple: float
    bars_held: int
    exit_reason: str
    entry_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def run(
    df: pd.DataFrame,
    params: Params,
    costs: Costs = Costs(),
    starting_equity: float = 25_000.0,
    risk_per_trade: float = 0.005,
    allow_fractional: bool = False,
    bars: Bars | None = None,
) -> dict:
    """Run one parameter set over one dataset. Returns trades + equity curve.

    Pass a prebuilt `bars` to reuse indicator computation across a sweep. Only
    valid for variants that share first_candle_n, atr_period and ema_period,
    since those are what precompute() depends on.
    """
    b = bars if bars is not None else Bars(df, params)
    strat = FVGStrategy(params)

    n = b.n
    o, h, l, c = b.open, b.high, b.low, b.close
    sess, kbar, nbars, idx = b.session, b.k, b.nbars, b.index

    equity = starting_equity
    eq_curve = np.empty(n)

    trades: list[Trade] = []
    pending = None  # Intent decided at close of previous bar
    pos = None  # dict describing the open position
    skipped_undersized = 0  # signals dropped because 1 unit exceeded the risk budget

    for i in range(n):
        # ---- 1. fill any order decided on the previous bar's close ----------
        if pending is not None and pos is None:
            d = pending.direction
            entry = costs.fill_price(o[i], d, opening=True)
            risk_pts = (entry - pending.stop) * d
            if risk_pts > 0:
                qty = (equity * risk_per_trade) / (risk_pts * costs.point_value)
                qty = qty if allow_fractional else np.floor(qty)
                if qty >= (0.0001 if allow_fractional else 1):
                    pos = dict(
                        d=d,
                        entry=entry,
                        stop=pending.stop,
                        target=pending.target,
                        qty=qty,
                        risk_pts=risk_pts,
                        t0=idx[i],
                        i0=i,
                        reason=pending.reason,
                    )
                    strat.note_trade_opened()
                else:
                    # the smallest tradable unit risks more than the budget
                    # allows. Not a filter -- an account-size constraint.
                    skipped_undersized += 1
            pending = None

        # ---- 2. manage an open position on THIS bar -------------------------
        if pos is not None:
            d, stop, tgt = pos["d"], pos["stop"], pos["target"]
            exit_px = None
            reason = ""

            # gap through the stop -> fill at the open
            if (d == 1 and o[i] <= stop) or (d == -1 and o[i] >= stop):
                exit_px, reason = o[i], "gap_stop"
            elif (d == 1 and o[i] >= tgt) or (d == -1 and o[i] <= tgt):
                exit_px, reason = o[i], "gap_target"
            else:
                hit_stop = (d == 1 and l[i] <= stop) or (d == -1 and h[i] >= stop)
                hit_tgt = (d == 1 and h[i] >= tgt) or (d == -1 and l[i] <= tgt)
                if hit_stop:  # pessimistic: stop wins ties
                    exit_px, reason = stop, "stop"
                elif hit_tgt:
                    exit_px, reason = tgt, "target"

            if exit_px is None and params.exit_at_session_end and kbar[i] == nbars[i] - 1:
                exit_px, reason = c[i], "session_end"

            if exit_px is not None:
                fill = costs.fill_price(exit_px, d, opening=False)
                gross = (fill - pos["entry"]) * d * pos["qty"] * costs.point_value
                comm = 2 * costs.commission_per_side * pos["qty"]
                pnl = gross - comm
                risk_dollars = pos["risk_pts"] * pos["qty"] * costs.point_value
                equity += pnl
                trades.append(
                    Trade(
                        entry_time=pos["t0"],
                        exit_time=idx[i],
                        direction=d,
                        entry=pos["entry"],
                        exit=fill,
                        stop=pos["stop"],
                        target=pos["target"],
                        qty=pos["qty"],
                        risk_points=pos["risk_pts"],
                        pnl_dollars=pnl,
                        r_multiple=pnl / risk_dollars if risk_dollars else 0.0,
                        bars_held=i - pos["i0"],
                        exit_reason=reason,
                        entry_reason=pos["reason"],
                    )
                )
                pos = None

        eq_curve[i] = equity

        # ---- 3. decide for the NEXT bar -------------------------------------
        if i + 1 < n and sess[i + 1] == sess[i]:
            pending = strat.decide(i, b, in_position=pos is not None)
        else:
            pending = None  # never carry an intent across a session boundary

    tdf = pd.DataFrame([t.to_dict() for t in trades])
    return {
        "trades": tdf,
        "equity": pd.Series(eq_curve, index=idx, name="equity"),
        "params": params.to_dict(),
        "starting_equity": starting_equity,
        "skipped_undersized": skipped_undersized,
    }
