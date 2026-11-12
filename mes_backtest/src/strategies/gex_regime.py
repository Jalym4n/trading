"""Dealer-gamma regime filters on two existing strategies. Rules fixed 2026-11-12 before any run.

Mechanism (published: dealers who are long gamma trade against moves and damp them; dealers who
are short gamma trade with moves and amplify them):
  high gamma  yesterday's SPX GEX above its trailing 252-day median -> ranges hold, sweeps fail
              -> take the hourly candle-range reversal (crt_hourly) only on these days.
  low gamma   yesterday's GEX below that median -> moves extend -> take the noise-band trend
              (noise_band_trend) only on these days.
GEX comes from the previous day, the median from the 252 days before it: known before the open.
Nothing else about either strategy changes.
"""

import numpy as np

from ..registry import register
from ..strategy import DayResult
from .crt_hourly import DEFAULTS as CRT_DEFAULTS, crt_hourly
from .noise_band_trend import noise_band_trend

NBT_DEFAULTS = {"nbt_stop_points": 30.0, "noise_lookback_days": 14,
                "slippage_applies_to": ["entry", "stop", "stop_gap", "time", "band"]}


def _regime(prior_levels):
    g, m = prior_levels.get("gex_prev"), prior_levels.get("gex_median")
    if g is None or m is None or not (np.isfinite(g) and np.isfinite(m)):
        return None
    return "high" if g > m else "low"


@register(
    "crt_hourly_high_gamma",
    hypothesis="Hourly candle-range sweeps reverse to the other side of the range more reliably when dealers "
               "are long gamma (yesterday's SPX GEX above its 252-day median), enough to pay costs.",
    disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
    defaults=CRT_DEFAULTS,
)
def crt_hourly_high_gamma(day_bars, prior_levels, params):
    r = _regime(prior_levels)
    if r is None:
        return DayResult("no-levels")
    return crt_hourly(day_bars, prior_levels, params) if r == "high" else DayResult("no-bias")


@register(
    "noise_band_low_gamma",
    hypothesis="Moves outside the intraday noise band extend further when dealers are short or low gamma "
               "(yesterday's SPX GEX below its 252-day median): the trend rule's edge is concentrated there.",
    disproof="Gross expectancy <= 0 on training, or net expectancy <= 0.",
    defaults=NBT_DEFAULTS,
)
def noise_band_low_gamma(day_bars, prior_levels, params):
    r = _regime(prior_levels)
    if r is None:
        return DayResult("no-levels")
    return noise_band_trend(day_bars, prior_levels, params) if r == "low" else DayResult("no-bias")
