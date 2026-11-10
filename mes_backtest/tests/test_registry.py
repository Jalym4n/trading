"""Registry contract, including the generic no-look-ahead probe over EVERY registered strategy."""
import pytest

from src import registry as R
from src.load import load_config
from src.strategy import DAY_CATEGORIES, DayResult
from synth import LEG, LV, OR, TO_TARGET, TRIGGER, day

P = dict(load_config()["params"], min_leg_points=0.0)


def test_registry_lists_and_resolves():
    assert {"or_fib_base", "or_fib_confluence"} <= set(R.names())
    s = R.get("or_fib_base")
    assert s.hypothesis and s.disproof and s.module == "src.strategies.or_fib"
    with pytest.raises(KeyError):
        R.get("does_not_exist")


def test_duplicate_name_is_refused():
    with pytest.raises(ValueError):
        R.register("or_fib_base")(lambda b, l, p: None)


@pytest.mark.parametrize("name", R.names())
def test_every_strategy_honours_the_contract(name):
    strat = R.get(name)
    bars = day(OR + LEG + [TRIGGER, TO_TARGET])
    r1 = strat(bars, LV, P)
    assert isinstance(r1, DayResult) and r1.status in DAY_CATEGORIES
    # deterministic
    r2 = strat(bars.copy(), dict(LV), dict(P))
    assert [t.__dict__ for t in r1.trades] == [t.__dict__ for t in r2.trades] and r1.status == r2.status
    # no look-ahead: rewriting every bar after the first entry bar must not change that entry
    if r1.trades:
        i = list(bars.index.strftime("%H:%M")).index(r1.trades[0].entry_bar)
        alt = bars.copy()
        alt.iloc[i + 1:] = [(50, 200, 40, 60)] * (len(alt) - i - 1)
        t0, t1 = r1.trades[0], strat(alt, LV, P).trades[0]
        for f in ("side", "zone", "entry_bar", "entry", "stop", "target", "leg_end", "leg_confirm_time"):
            assert getattr(t0, f) == getattr(t1, f), (name, f)
