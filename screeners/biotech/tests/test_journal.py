"""Thesis journal: refusals, finality of scores, tamper detection, Brier."""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat import journal as J  # noqa: E402

BASE = dict(ticker="vera", catalyst="Ph3 readout", catalyst_date="2027-03-01", claim="primary endpoint met",
            market_believes="coin flip", why_i_disagree="mechanism and Ph2 effect size",
            what_would_prove_me_wrong="DSMB stops for futility", my_probability="0.62",
            market_implied="0.45", position_usd="0", entry_price="29.88")


def _t(i):
    return dt.datetime(2026, 10, 12, 9, 0, i, tzinfo=dt.timezone.utc)


def _path():
    return os.path.join(tempfile.mkdtemp(), "j.jsonl")


def _refused(fn, *a):
    try:
        fn(*a)
    except J.JournalError:
        return True
    return False


def test_incomplete_and_extreme_entries_refused():
    p = _path()
    for k in J.REQUIRED:
        assert _refused(J.add, p, {**BASE, k: ""}), k
    for bad in ("0", "1", "1.2", "-0.1", "abc", "62"):
        assert _refused(J.add, p, {**BASE, "my_probability": bad}), bad
        assert _refused(J.add, p, {**BASE, "market_implied": bad}), bad
    assert _refused(J.add, p, {**BASE, "catalyst_date": "March 2027"})
    assert not os.path.exists(p) or J.entries(p) == {}


def test_scored_entries_are_final():
    p = _path()
    e = J.add(p, BASE, now=_t(1))
    J.score(p, e["id"], 1, now=_t(2))
    assert _refused(J.score, p, e["id"], 0, _t(3))
    assert _refused(J.score, p, "NOPE-1", 1, _t(3))
    assert _refused(J.score, p, e["id"], 2, _t(3))
    assert J.entries(p)[e["id"]]["outcome"] == 1


def test_editing_the_file_is_detected():
    p = _path()
    J.add(p, BASE, now=_t(1))
    J.add(p, {**BASE, "ticker": "xyz"}, now=_t(2))
    lines = open(p).read().splitlines()
    lines[0] = lines[0].replace("0.62", "0.99")
    open(p, "w").write("\n".join(lines) + "\n")
    assert _refused(J.entries, p)


def test_brier_skill_and_readability_gate():
    p = _path()
    probs, outs = [0.8, 0.7, 0.3, 0.6], [1, 1, 0, 0]
    for i, (pr, o) in enumerate(zip(probs, outs)):
        e = J.add(p, {**BASE, "ticker": f"t{i}", "my_probability": pr}, now=_t(10 + i))
        J.score(p, e["id"], o, now=_t(20 + i))
    rep = J.report(p)
    brier = sum((a - b) ** 2 for a, b in zip(probs, outs)) / 4          # 0.145
    assert f"{brier:.4f}" in rep
    assert "+0.420" in rep            # 1 - 0.145 / 0.25 (constant forecast at base rate 0.5)
    assert "46 more needed" in rep and "NOT READABLE" in rep


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
