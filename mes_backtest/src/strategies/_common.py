"""Shared bracket management for strategies: stop first, target from the bar after entry."""
import numpy as np

from ..strategy import Trade

NAN = float("nan")


def manage(side, i, entry, stop, target, o, h, l, c):          # noqa: E741
    """(exit_index, exit_price, reason) for a position entered at the CLOSE of bar i."""
    sgn = 1 if side == "long" else -1
    for j in range(i + 1, len(c)):
        hit_stop = (l[j] <= stop) if sgn == 1 else (h[j] >= stop)
        hit_tgt = (h[j] >= target) if sgn == 1 else (l[j] <= target)
        if hit_stop:
            gap = (o[j] <= stop) if sgn == 1 else (o[j] >= stop)
            return (j, o[j], "stop_gap") if gap else (j, stop, "stop")
        if hit_tgt:
            return j, target, "target"
    return len(c) - 1, c[-1], "time"


def make_trade(date, side, tag, labels, i, entry, stop, target, exit_i, px, reason, level=NAN):
    return Trade(date, side, tag, level, NAN, NAN, labels[i], entry, stop, target, "fixed", labels[exit_i], px, reason,
                 False, abs(entry - stop), NAN, NAN, "", NAN, NAN, "")


def arrays(day_bars):
    return tuple(day_bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))


def finite(*xs):
    return all(x is not None and np.isfinite(x) for x in xs)
