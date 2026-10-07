"""Parameterized FVG + first-candle strategy.

Every rule you might want to vary is a parameter, so that exploring the idea
space is explicit and countable rather than a series of hand edits you forget
you made. The engine calls decide() with data up to and including bar t and
executes the returned intent at the OPEN of bar t+1.

FVG (fair value gap), 3-bar definition:
    bullish at bar i:  low[i]  > high[i-2]   -> unfilled zone [high[i-2], low[i]]
    bearish at bar i:  high[i] < low[i-2]    -> unfilled zone [high[i],  low[i-2]]
The gap is only known at the CLOSE of bar i. Nothing here may reference i+1.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    # --- entry logic -------------------------------------------------------
    entry_mode: str = "fvg_retest"  # fvg_retest | first_candle_break | fvg_plus_break | amd
    first_candle_n: int = 1  # bars defining the opening range / "first candle"
    require_first_candle_bias: bool = True  # only take FVGs agreeing with OR direction
    min_gap_atr: float = 0.0  # ignore gaps smaller than this * ATR
    fvg_max_age: int = 20  # bars an unfilled FVG stays tradable
    entry_start_bar: int = 1  # no entries before this bar index in session
    entry_end_bar: int = 60  # no NEW entries after this bar index

    # --- exit logic --------------------------------------------------------
    stop_mode: str = "fvg_far_edge"  # fvg_far_edge | atr | opening_range | amd_extreme
    stop_atr_mult: float = 1.0
    stop_buffer_atr: float = 0.25  # push the stop this far beyond the structural level
    min_stop_atr: float = 0.25  # skip trades whose stop is tighter than this * ATR
    max_stop_atr: float = 10.0  # skip trades whose stop is absurdly wide
    target_r: float = 2.0  # take profit at this multiple of initial risk
    exit_at_session_end: bool = True
    max_trades_per_session: int = 1

    # --- AMD / power of 3 --------------------------------------------------
    # Only phases 1 and 2 are DETECTED. Phase 3 (distribution) is the trade,
    # not part of the signal: requiring the reversal to confirm the pattern
    # would define the setup by its own outcome.
    amd_cons_bars: int = 6  # bars forming the consolidation range
    amd_max_range_atr: float = 1.5  # range must be no wider than this x ATR
    amd_min_break_atr: float = 0.25  # how far past the range counts as a break
    amd_reclaim_bars: int = 3  # must close back inside within this many bars

    # --- inversion (iFVG) --------------------------------------------------
    invalidation_mode: str = "wick"  # wick = bar's extreme | close = bar must settle through
    invert_on_violation: bool = False  # iFVG: re-arm a failed zone in reverse
    max_inversions: int = 1  # how many times one zone may flip polarity

    # --- trend filter ------------------------------------------------------
    ema_period: int = 0  # 0 disables the filter entirely
    ema_mode: str = "with"  # with = trade with the trend | against = fade it

    # --- direction / misc --------------------------------------------------
    allow_long: bool = True
    allow_short: bool = True
    atr_period: int = 14

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Intent:
    """An order decided at close of bar t, to be filled at open of bar t+1."""

    direction: int  # +1 long, -1 short
    stop: float
    target: float
    reason: str = ""


class Bars:
    """Column-major view of the bar data.

    Everything the strategy and engine touch inside the loop is a plain numpy
    array. Row-wise pandas access (`df.iloc[i]`) is ~100x slower and makes a
    parameter sweep impractical.
    """

    __slots__ = (
        "open", "high", "low", "close", "session", "k", "nbars",
        "atr", "or_high", "or_low", "or_bias", "ema",
        "amd_hi", "amd_lo", "index", "n",
    )

    def __init__(self, df: pd.DataFrame, p: Params):
        out = precompute(df, p)
        self.open = out["open"].to_numpy(float)
        self.high = out["high"].to_numpy(float)
        self.low = out["low"].to_numpy(float)
        self.close = out["close"].to_numpy(float)
        self.session = out["session"].to_numpy()
        self.k = out["bar_in_session"].to_numpy(np.int64)
        self.nbars = out["bars_in_session"].to_numpy(np.int64)
        self.atr = out["atr"].to_numpy(float)
        self.or_high = out["or_high"].to_numpy(float)
        self.or_low = out["or_low"].to_numpy(float)
        self.or_bias = out["or_bias"].to_numpy(np.int64)
        self.ema = out["ema"].to_numpy(float)
        self.amd_hi = out["amd_hi"].to_numpy(float)
        self.amd_lo = out["amd_lo"].to_numpy(float)
        self.index = out.index
        self.n = len(out)


def precompute(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Add indicator columns. Every one is shifted so bar t sees only <= t."""
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # ATR uses bars strictly before t, so it is knowable at the open of t
    out["atr"] = tr.rolling(p.atr_period, min_periods=p.atr_period).mean().shift(1)

    g = out.groupby("session")
    n = p.first_candle_n
    # opening range built from the first n bars; only valid from bar n onward
    out["or_high"] = g["high"].transform(lambda s: s.iloc[:n].max())
    out["or_low"] = g["low"].transform(lambda s: s.iloc[:n].min())
    or_open = g["open"].transform(lambda s: s.iloc[0])
    or_close = g["close"].transform(lambda s: s.iloc[n - 1])
    out["or_bias"] = np.sign(or_close - or_open).astype(int)

    # AMD consolidation range, kept separate from the opening range so the two
    # window lengths can be swept independently.
    na_ = p.amd_cons_bars
    out["amd_hi"] = g["high"].transform(lambda s: s.iloc[:na_].max())
    out["amd_lo"] = g["low"].transform(lambda s: s.iloc[:na_].min())

    # EMA of closes, run continuously across sessions (the usual intraday
    # convention). No shift: the decision is made at the CLOSE of bar t, so
    # close[t] is already known and including it is not lookahead.
    if p.ema_period > 0:
        out["ema"] = out["close"].ewm(span=p.ema_period, adjust=False).mean()
        # the first `ema_period` bars are still converging from the seed value
        out.iloc[: p.ema_period, out.columns.get_loc("ema")] = np.nan
    else:
        out["ema"] = np.nan  # filter disabled
    return out


class FVGStrategy:
    """Stateful, bar-by-bar. Reset at each session boundary.

    Active FVGs are held as four parallel lists rather than objects; at a few
    thousand decisions per sweep variant the allocation cost is real.
    """

    def __init__(self, params: Params):
        self.p = params
        self._bar: list[int] = []
        self._dir: list[int] = []
        self._lo: list[float] = []
        self._hi: list[float] = []
        self._flips: list[int] = []  # times this zone has inverted
        # AMD state
        self._amd_armed = False
        self._amd_dir = 0
        self._amd_bar = -1
        self._amd_ext = float("nan")  # furthest point the fake move reached
        self._amd_used = False
        self._amd_stop_level = float("nan")
        self._session = None
        self._trades_this_session = 0

    # -- session bookkeeping -------------------------------------------------
    def _maybe_reset(self, session) -> None:
        if session != self._session:
            self._session = session
            self._bar, self._dir, self._lo, self._hi = [], [], [], []
            self._flips = []
            self._amd_armed = False
            self._amd_dir = 0
            self._amd_bar = -1
            self._amd_ext = float("nan")
            self._amd_used = False
            self._trades_this_session = 0

    def note_trade_opened(self) -> None:
        self._trades_this_session += 1

    # -- AMD / power of 3 ----------------------------------------------------
    def _amd(self, i: int, b: Bars) -> tuple[int, str]:
        """Detect consolidation then manipulation. Returns (direction, reason).

        Observable at bar i and nothing later:
          phase 1  the first amd_cons_bars of the session formed a range no
                   wider than amd_max_range_atr x ATR
          phase 2  price pushed amd_min_break_atr x ATR beyond that range and
                   then CLOSED back inside within amd_reclaim_bars

        The returned direction is the opposite of the break. That is the
        hypothesis under test, not a confirmed phase: nothing here checks
        whether price actually went that way.
        """
        p = self.p
        k = int(b.k[i])
        atr = b.atr[i]
        if self._amd_used or k <= p.amd_cons_bars:
            return 0, ""

        hi, lo = b.amd_hi[i], b.amd_lo[i]
        if not (np.isfinite(hi) and np.isfinite(lo)):
            return 0, ""
        if (hi - lo) > p.amd_max_range_atr * atr:
            return 0, ""  # the range was never tight, so no accumulation leg

        thresh = p.amd_min_break_atr * atr
        hi_i, lo_i, close_i = b.high[i], b.low[i], b.close[i]

        if not self._amd_armed:
            if hi_i > hi + thresh:
                self._amd_armed, self._amd_dir, self._amd_bar = True, 1, i
                self._amd_ext = hi_i
            elif lo_i < lo - thresh:
                self._amd_armed, self._amd_dir, self._amd_bar = True, -1, i
                self._amd_ext = lo_i
            # fall through: a single bar can break and reclaim within itself

        if not self._amd_armed:
            return 0, ""

        # how far the fake move ran, which is where the stop belongs
        self._amd_ext = (
            max(self._amd_ext, hi_i) if self._amd_dir == 1 else min(self._amd_ext, lo_i)
        )

        if i - self._amd_bar > p.amd_reclaim_bars:
            self._amd_armed = False  # never came back: a real breakout
            return 0, ""

        reclaimed = (close_i < hi) if self._amd_dir == 1 else (close_i > lo)
        if not reclaimed:
            return 0, ""

        self._amd_armed = False
        self._amd_used = True  # one manipulation per session
        self._amd_stop_level = self._amd_ext
        d = -self._amd_dir
        return d, f"amd_{'up' if d == 1 else 'dn'}"

    # -- FVG maintenance -----------------------------------------------------
    def _update_fvgs(self, i: int, b: Bars) -> None:
        p = self.p
        k = b.k[i]
        atr = b.atr[i]
        lo_i, hi_i, close_i = b.low[i], b.high[i], b.close[i]

        # detect a new gap formed by bars (t-2, t-1, t) within this session
        if k >= 2 and atr > 0 and np.isfinite(atr):
            h2, l2 = b.high[i - 2], b.low[i - 2]
            thresh = p.min_gap_atr * atr
            if lo_i > h2:
                if lo_i - h2 >= thresh:
                    self._bar.append(i); self._dir.append(1)
                    self._lo.append(h2); self._hi.append(lo_i)
                    self._flips.append(0)
            elif hi_i < l2:
                if l2 - hi_i >= thresh:
                    self._bar.append(i); self._dir.append(-1)
                    self._lo.append(hi_i); self._hi.append(l2)
                    self._flips.append(0)

        # Expire by age, then handle zones price has traded fully through.
        #
        # invalidation_mode picks what counts as "through":
        #   wick  -- the bar's extreme pierced the far edge (loose, fires early)
        #   close -- the bar had to SETTLE beyond it (strict, fires later,
        #            fewer inversions, the more common convention)
        #
        # invert_on_violation turns a violation into an iFVG: the zone keeps
        # its price boundaries but flips polarity, on the theory that a level
        # which failed as support now acts as resistance. The age clock
        # restarts, and because the new birth bar is i, the entry loop's
        # `_bar[j] >= i` guard stops it trading on the same bar it flipped.
        if self._bar:
            probe_dn = lo_i if p.invalidation_mode == "wick" else close_i
            probe_up = hi_i if p.invalidation_mode == "wick" else close_i
            kb, kd, kl, kh, kf = [], [], [], [], []
            for j in range(len(self._bar)):
                if i - self._bar[j] > p.fvg_max_age:
                    continue
                d = self._dir[j]
                violated = (probe_dn < self._lo[j]) if d == 1 else (probe_up > self._hi[j])
                if violated:
                    if not p.invert_on_violation or self._flips[j] >= p.max_inversions:
                        continue  # drop it, as before
                    kb.append(i); kd.append(-d)
                    kl.append(self._lo[j]); kh.append(self._hi[j])
                    kf.append(self._flips[j] + 1)
                    continue
                kb.append(self._bar[j]); kd.append(d)
                kl.append(self._lo[j]); kh.append(self._hi[j])
                kf.append(self._flips[j])
            self._bar, self._dir, self._lo, self._hi, self._flips = kb, kd, kl, kh, kf

    # -- the decision --------------------------------------------------------
    def decide(self, i: int, b: Bars, in_position: bool) -> Intent | None:
        self._maybe_reset(b.session[i])
        self._update_fvgs(i, b)

        p = self.p
        k = int(b.k[i])
        atr = b.atr[i]

        if in_position or self._trades_this_session >= p.max_trades_per_session:
            return None
        if k < max(p.entry_start_bar, p.first_candle_n) or k > p.entry_end_bar:
            return None
        if not np.isfinite(atr) or atr <= 0:
            return None

        bias = int(b.or_bias[i])
        close_i, hi_i, lo_i = b.close[i], b.high[i], b.low[i]
        broke_up = close_i > b.or_high[i]
        broke_dn = close_i < b.or_low[i]

        direction = 0
        chosen = -1
        reason = ""

        # Trend filter. Computed once per bar so it costs nothing inside the
        # FVG loop. ema_dir is +1 when price is above the EMA, -1 below, and 0
        # when the filter is off or the EMA has not converged yet.
        ema_dir = 0
        if p.ema_period > 0:
            e = b.ema[i]
            if not np.isfinite(e):
                return None  # not enough history to judge trend
            ema_dir = 1 if close_i > e else -1
            if p.ema_mode == "against":
                ema_dir = -ema_dir

        if p.entry_mode in ("fvg_retest", "fvg_plus_break"):
            # price must have traded back INTO a gap created on an earlier bar
            for j in range(len(self._bar)):
                if self._bar[j] >= i:
                    continue
                if not (lo_i <= self._hi[j] and hi_i >= self._lo[j]):
                    continue
                d = self._dir[j]
                if p.require_first_candle_bias and bias != 0 and d != bias:
                    continue
                if ema_dir != 0 and d != ema_dir:
                    continue
                if p.entry_mode == "fvg_plus_break":
                    if d == 1 and not broke_up:
                        continue
                    if d == -1 and not broke_dn:
                        continue
                direction, chosen = d, j
                kind = "ifvg" if self._flips[j] > 0 else "fvg"
                reason = f"{kind}_retest age={i - self._bar[j]}"
                break

        elif p.entry_mode == "first_candle_break":
            if broke_up:
                direction, reason = 1, "or_break_up"
            elif broke_dn:
                direction, reason = -1, "or_break_dn"
            if ema_dir != 0 and direction != ema_dir:
                direction, reason = 0, ""

        elif p.entry_mode == "amd":
            direction, reason = self._amd(i, b)
            if ema_dir != 0 and direction != ema_dir:
                direction, reason = 0, ""

        if direction == 0:
            return None
        if direction == 1 and not p.allow_long:
            return None
        if direction == -1 and not p.allow_short:
            return None

        ref = close_i  # stop is sized off the last known price
        if p.stop_mode == "fvg_far_edge" and chosen >= 0:
            level = self._lo[chosen] if direction == 1 else self._hi[chosen]
        elif p.stop_mode == "opening_range":
            level = b.or_low[i] if direction == 1 else b.or_high[i]
        elif p.stop_mode == "amd_extreme" and np.isfinite(self._amd_stop_level):
            level = self._amd_stop_level  # beyond the extreme of the fake move
        else:
            level = ref - direction * p.stop_atr_mult * atr
        # a structural stop sits just BEYOND the level, not exactly on it
        stop = level - direction * p.stop_buffer_atr * atr

        risk = (ref - stop) * direction
        # A stop tighter than a typical bar's range cannot be resolved with bar
        # data: the pessimistic stop-first rule fires on nearly every trade and
        # the backtest measures your bar size, not your idea. Reject those.
        if risk < p.min_stop_atr * atr:
            return None
        if risk > p.max_stop_atr * atr:
            return None
        target = ref + direction * p.target_r * risk
        return Intent(direction=direction, stop=float(stop), target=float(target), reason=reason)
