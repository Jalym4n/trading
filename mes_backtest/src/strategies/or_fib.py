"""The original spec (lucid-25k-backtest-spec.md section 1), as two registered arms.

Both arms share every rule in src.strategy.run_day; they differ ONLY in the
confluence filter, which is the A/B test the spec calls the most important
one in the project.
"""

from ..registry import register
from ..strategy import run_day


@register(
    "or_fib_confluence",
    hypothesis="After the first 5-min close outside the 09:30-09:45 opening range, a pullback to a fib of the "
               "impulse leg that coincides (within confluence_tol) with the prior session's POC/VAH/VAL "
               "resumes in the breakout direction often enough to pay after costs.",
    disproof="Net expectancy <= 0 on training, or positive in fewer than half the walk-forward test windows, "
             "or no better than or_fib_base (then the fib/volume story is decoration).",
)
def or_fib_confluence(day_bars, prior_levels, params):
    return run_day(day_bars, prior_levels, params, confluence=True)


@register(
    "or_fib_base",
    hypothesis="The same opening-range breakout and fib-pullback mechanics with NO volume-node filter: the "
               "control arm. Any edge here belongs to the opening-range mechanics, not to confluence.",
    disproof="Net expectancy <= 0 on training, or positive in fewer than half the walk-forward test windows.",
)
def or_fib_base(day_bars, prior_levels, params):
    return run_day(day_bars, prior_levels, params, confluence=False)
