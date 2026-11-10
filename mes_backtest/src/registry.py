"""Strategy registry: every hypothesis is a module that exposes ONE pure function.

    @register("my_strategy", hypothesis="...", disproof="...")
    def my_strategy(day_bars, prior_levels, params) -> DayResult: ...

Contract (the harness relies on every line of it):
  - pure and deterministic: same (day_bars, prior_levels, params) -> same DayResult,
    no I/O, no state across days, no randomness;
  - day_bars is ONE day's 5-min RTH bars (index = bar open time in market tz;
    open/high/low/close); prior_levels is {"poc", "vah", "val", "prior_close"}
    from the prior session (may be NaN; prior_close = its last RTH bar close);
    params is the merged config params dict;
  - bars are consumed in time order: a decision at bar i uses bars <= i only.
    tests/test_registry.py mutates every bar after an entry and asserts the
    entry is unchanged, for EVERY registered strategy;
  - prices are instrument points; the strategy never sees point values or costs;
  - returns src.strategy.DayResult (status in DAY_CATEGORIES, trades, detail).

Put the module in src/strategies/; the package imports every file there, so a
new strategy is registered by existing. Nothing else in the project changes.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Callable

_REGISTRY: dict[str, "Strategy"] = {}


@dataclass
class Strategy:
    name: str
    fn: Callable
    hypothesis: str = ""           # one falsifiable sentence: what edge, what mechanism
    disproof: str = ""             # the result that would disprove it, written BEFORE running
    defaults: dict = field(default_factory=dict)   # param overrides this strategy needs
    module: str = ""

    def __call__(self, day_bars, prior_levels, params):
        return self.fn(day_bars, prior_levels, params)


def register(name: str, hypothesis: str = "", disproof: str = "", defaults: dict | None = None):
    def deco(fn):
        if name in _REGISTRY and _REGISTRY[name].fn is not fn:
            raise ValueError(f"strategy name {name!r} is already registered by {_REGISTRY[name].module}")
        _REGISTRY[name] = Strategy(name, fn, hypothesis, disproof, dict(defaults or {}), fn.__module__)
        return fn
    return deco


def _discover() -> None:
    from . import strategies as pkg
    for m in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{m.name}")


def get(name: str) -> Strategy:
    _discover()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; registered: {sorted(_REGISTRY)}") from None


def names() -> list[str]:
    _discover()
    return sorted(_REGISTRY)


def all_strategies() -> dict[str, Strategy]:
    _discover()
    return dict(_REGISTRY)
