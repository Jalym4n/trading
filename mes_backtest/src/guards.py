"""The anti-fooling-yourself layer (spec section 5): trial counter, holdout firewall,
regime split, scorecard. Enforced in code, not by discipline.

TRIAL LOG  results/trial_log.jsonl, append-only and hash-chained (each line
           carries the sha256 of the previous line). It is committed to git
           (un-ignored in .gitignore) because the count must never reset.
           Every harness run appends one line: strategy, effective params,
           instruments, window, git commit (+dirty flag), phase, results.
           trial_count() = distinct (strategy, params, instruments) tested; the
           reminder is printed next to every result.

HOLDOUT    2026-01-01 to 2026-11-06 (config validation.oos_holdout). backtest.run
           clamps every development window before it. The ONLY way in is
           run_final_holdout(cfg, strategy, confirm=True, reason="..."), which
             - refuses unless confirm is True and a reason is given,
             - refuses if results/holdout_ledger.jsonl already has ANY entry
               (one finalist, once; a second finalist needs a deliberate, visible
               edit of the ledger, which git will show),
             - prints a loud banner, runs with the private _allow_holdout flag,
             - writes the ledger line and a phase="HOLDOUT" trial-log line.
           Peeking and then tweaking burns the holdout; the ledger makes that a
           recorded fact instead of a memory.

REGIME     realized-vol terciles of each instrument's RTH close-to-close returns,
           rolling window config validation.regime.realized_vol_window_days,
           tercile cut points from DEVELOPMENT data only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest as B
from .load import ROOT, read_processed

TRIAL_LOG = ROOT / "results" / "trial_log.jsonl"
HOLDOUT_LEDGER = ROOT / "results" / "holdout_ledger.jsonl"
GENESIS = "0" * 64


# ------------------------------------------------------------- git state --
def git_state(root: Path = ROOT) -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "config.json"], cwd=root,
                               capture_output=True, text=True, check=True).stdout.strip()
        return {"commit": commit, "dirty": bool(dirty)}
    except Exception:  # noqa: BLE001  not a git checkout
        return {"commit": None, "dirty": None}


# ------------------------------------------------------------- trial log --
def trial_key(strategy: str, params: dict, instruments: list) -> str:
    blob = json.dumps({"strategy": strategy, "params": params, "instruments": sorted(instruments)}, sort_keys=True,
                      default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _line_hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()


def read_trials(path: Path = TRIAL_LOG) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def verify_chain(path: Path = TRIAL_LOG) -> None:
    """Raise if any line was edited or removed. Run before every append and on `harness log`."""
    prev = GENESIS
    for i, rec in enumerate(read_trials(path), 1):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if body.get("prev_hash") != prev or _line_hash(prev, body) != rec.get("hash"):
            raise RuntimeError(f"{path.name} line {i} breaks the hash chain: the trial log was edited or truncated. "
                               "It is append-only; restore it from git.")
        prev = rec["hash"]


def append_trial(record: dict, path: Path = TRIAL_LOG) -> dict:
    """Append one trial line. Never rewrites; verifies the chain first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    verify_chain(path)
    trials = read_trials(path)
    prev = trials[-1]["hash"] if trials else GENESIS
    body = {"trial_line": len(trials) + 1, "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            **record, "prev_hash": prev}
    body["hash"] = _line_hash(prev, body)
    with path.open("a") as f:                          # "a": append, never "w"
        f.write(json.dumps(body, sort_keys=True, default=str) + "\n")
    return body


def trial_count(path: Path = TRIAL_LOG) -> dict:
    trials = read_trials(path)
    keys = {t["trial_key"] for t in trials if t.get("phase") != "HOLDOUT"}
    return {"lines": len(trials), "distinct": len(keys), "strategies": len({t["strategy"] for t in trials})}


def reminder(path: Path = TRIAL_LOG, ceiling: int | None = None) -> str:
    n = trial_count(path)["distinct"]
    budget = f" Trial budget: {n} of {ceiling} used." if ceiling else ""
    return (f"{n} strategies/params tested so far. At p<0.05, expect ~{n * 0.05:.1f} false positives by chance. "
            "A winner here is a CANDIDATE, not a confirmed edge." + budget)


class TrialBudgetExhausted(RuntimeError):
    pass


def check_budget(cfg: dict, key: str, path: Path = TRIAL_LOG) -> None:
    """Refuse a NEW distinct trial once the ceiling is reached. Re-running a logged
    configuration (e.g. its walk-forward phase) is the same trial and is allowed."""
    ceiling = cfg["validation"]["trial_budget"]["ceiling"]
    keys = {t["trial_key"] for t in read_trials(path) if t.get("phase") != "HOLDOUT"}
    if key not in keys and len(keys) >= ceiling:
        raise TrialBudgetExhausted(
            f"trial budget exhausted: {len(keys)} of {ceiling} distinct trials used. "
            + cfg["validation"]["trial_budget"]["stopping_rule"])


# ------------------------------------------------------- holdout firewall --
def holdout_window(cfg: dict) -> tuple[str, str]:
    h = cfg["validation"]["oos_holdout"]
    return h["start"], h["end"]


def read_ledger(path: Path = HOLDOUT_LEDGER) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class HoldoutRefused(RuntimeError):
    pass


def run_final_holdout(cfg: dict, strategy, confirm: bool = False, reason: str = "", params: dict | None = None,
                      instruments: list | None = None, slippage_mult: float = 1.0, ledger: Path = HOLDOUT_LEDGER,
                      trial_log: Path = TRIAL_LOG, _print=print) -> dict:
    """The one road into 2026. See the module docstring for the rules."""
    name = getattr(strategy, "name", getattr(strategy, "__name__", "strategy"))
    if confirm is not True:
        raise HoldoutRefused("run_final_holdout needs confirm=True. The holdout is consumed ONCE, on ONE finalist, "
                             "after all searching is done. If you are still searching, do not call this.")
    if not reason or len(reason.strip()) < 20:
        raise HoldoutRefused("give a reason of at least 20 characters: why this strategy is the single finalist")
    used = read_ledger(ledger)
    if used:
        u = used[0]
        raise HoldoutRefused(f"the holdout was already consumed on {u['timestamp_utc']} by {u['strategy']!r} "
                             f"(commit {u['git']['commit']}). Re-running it, on this or any tweaked strategy, is no "
                             f"longer an out-of-sample test. Nothing was run. (ledger: {ledger})")
    s, e = holdout_window(cfg)
    banner = "=" * 78
    _print(f"\n{banner}\n  CONSUMING THE 2026 HOLDOUT  {s} -> {e}\n  strategy {name!r}\n  reason: {reason.strip()}\n"
           f"  This happens once. The result is final whatever it says.\n{banner}\n")
    res = B.run(cfg, strategy, instruments, s, e, params, slippage_mult, name, _allow_holdout=True)
    from . import stats as S
    stats = {}
    for inst in res.instruments:
        dl = res.day_log[res.day_log["instrument"] == inst]
        stats[inst] = S.summarize(res.trade_log[res.trade_log["instrument"] == inst], cfg, B.weeks_in(dl, cfg))
    if len(res.instruments) > 1:
        stats["POOLED"] = S.summarize(res.trade_log, cfg, B.weeks_in(res.day_log, cfg))
    rec = {"timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "strategy": name,
           "params": res.params, "instruments": res.instruments, "window": [s, e], "reason": reason.strip(),
           "git": git_state(), "trial_key": trial_key(name, res.params, res.instruments), "stats": stats}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    append_trial({"strategy": name, "params": res.params, "instruments": res.instruments, "window": [s, e],
                  "git": rec["git"], "phase": "HOLDOUT", "trial_key": rec["trial_key"],
                  "results": {"holdout": stats}, "note": reason.strip()}, trial_log)
    return {"result": res, "stats": stats, "ledger_entry": rec}


# ----------------------------------------------------------- regime split --
def regime_from_closes(close: pd.Series, window: int) -> pd.DataFrame:
    """close: daily RTH closes indexed by date (development days only). Returns
    [date, realized_vol, regime]: rolling std of log returns over `window` days,
    annualised, LAGGED one day (a day's tag uses only earlier closes), cut at terciles."""
    ret = np.log(close.astype(float)).diff()
    rv = ret.rolling(window).std().shift(1) * np.sqrt(252)
    lo, hi = rv.quantile([1 / 3, 2 / 3])
    tag = pd.cut(rv, [-np.inf, lo, hi, np.inf], labels=["low", "mid", "high"])
    return pd.DataFrame({"date": [pd.Timestamp(d).date().isoformat() for d in close.index],
                         "realized_vol": rv.to_numpy(), "regime": tag.astype(object).to_numpy()})


def regime_tags(cfg: dict, instruments: list[str]) -> pd.DataFrame:
    """[instrument, date, realized_vol, regime] for every development day of each instrument.
    Tercile cut points come from development data only (nothing from the holdout)."""
    win = cfg["validation"]["regime"]["realized_vol_window_days"]
    dev_end = pd.Timestamp(B.development_end(cfg))
    rows = []
    for inst in instruments:
        m5 = read_processed(inst)["m5"]
        close = m5.groupby("date")["close"].last()
        rows.append(regime_from_closes(close[close.index <= dev_end], win).assign(instrument=inst))
    return pd.concat(rows, ignore_index=True)[["instrument", "date", "realized_vol", "regime"]]


# --------------------------------------------------------------- scorecard --
def scorecard(cfg: dict, training: dict | None = None, walk_forward: dict | None = None, holdout: dict | None = None,
              training_doubled: dict | None = None) -> list[dict]:
    """Rows of {gate, rule, actual, verdict} against config viability_gates. A gate not yet
    measurable is 'n/a', never 'pass'. Pooled figures are used (the combined book)."""
    g = cfg["viability_gates"]
    target = cfg["lucid_25k"]["profit_target"]
    rows = []

    def fmt(v, f="{:.2f}"):
        return "n/a" if v is None else f.format(v)

    def add(gate, rule, actual, ok):
        rows.append({"gate": gate, "rule": rule, "actual": actual,
                     "verdict": "n/a" if ok is None else ("PASS" if ok else "FAIL")})

    # which stats block answers the frequency / drawdown / expectancy questions
    if walk_forward is not None:
        basis, label = walk_forward["test_pooled"], "walk-forward test windows pooled"
    elif training is not None:
        basis, label = training, "training window"
    else:
        basis, label = None, ""
    if basis is None or basis.get("n_trades", 0) == 0:
        add("GATE 1", f">= {g['gate1_min_trades_per_week']:g} trades/week", "0 trades", False)
        add("GATE 2", f"max DD < ${g['gate2_max_drawdown_usd']:,.0f} (wall ${g['gate2_hard_wall_usd']:,.0f})",
            "no trades", None)
        add("GATE 3", "net expectancy > 0, majority of test windows", "no trades", None)
    else:
        tpw = basis.get("trades_per_week")
        add("GATE 1", f">= {g['gate1_min_trades_per_week']:g} trades/week ({label})", f"{fmt(tpw)} /wk",
            None if tpw is None else tpw >= g["gate1_min_trades_per_week"])
        dd = basis.get("max_dd_usd")
        add("GATE 2", f"max DD < ${g['gate2_max_drawdown_usd']:,.0f} net, 1 contract ({label})", f"${fmt(dd)}",
            None if dd is None else dd < g["gate2_max_drawdown_usd"])
        exp = basis.get("expectancy_net_usd")
        if walk_forward is not None:
            k, n = walk_forward["positive_test_windows"], walk_forward["n_test_windows"]
            add("GATE 3", "net expectancy > 0 AND positive in a majority of test windows",
                f"${fmt(exp)}/trade; {k}/{n} windows positive", (exp or 0) > 0 and k > n / 2)
        else:
            # a non-positive training expectancy already fails the first condition; a positive one
            # still needs the walk-forward windows
            add("GATE 3", "net expectancy > 0 (majority-of-windows part needs walk-forward)",
                f"${fmt(exp)}/trade on training; windows n/a", False if (exp is not None and exp <= 0) else None)
    if holdout is not None:
        exp_h = holdout.get("expectancy_net_usd")
        add("GATE 4", "net expectancy > 0 on the 2026 holdout", f"${fmt(exp_h)}/trade, {holdout.get('n_trades')} trades",
            None if exp_h is None else exp_h > 0)
    else:
        add("GATE 4", "survives the 2026 holdout", "not run (one finalist, once, at the end)", None)
    if basis and basis.get("n_trades"):
        bd = basis.get("best_day_pct_of_target")
        add("SOFT", f"largest day < {g['soft_largest_day_max_pct_of_target']:g}% of the ${target:,} target",
            f"{fmt(bd, '{:.1f}')}%", None if bd is None else bd < g["soft_largest_day_max_pct_of_target"])
        ttt = basis.get("trades_to_target")
        add("SOFT", "trades to +$1,250 is weeks, not months (at >= 3/wk, ~40 trades = 3 months)",
            "never (expectancy <= 0)" if ttt is None else f"{ttt:.0f} trades", False if ttt is None else ttt <= 40)
        ws = basis.get("worst_streak_loss_usd")
        add("SOFT", f"worst losing streak < ${g['soft_worst_streak_times_risk_max_usd']:,.0f}",
            f"${fmt(-ws if ws is not None else None)} over {basis.get('max_consec_losses')} losses",
            None if ws is None else -ws < g["soft_worst_streak_times_risk_max_usd"])
        dbl = (walk_forward or {}).get("test_pooled_doubled_slippage") if walk_forward is not None else training_doubled
        e2 = dbl.get("expectancy_net_usd") if dbl else None
        add("SOFT", "net expectancy still > 0 with doubled slippage", f"${fmt(e2)}/trade",
            None if e2 is None else e2 > 0)
    return rows


def format_scorecard(rows: list[dict]) -> str:
    w = max(len(r["rule"]) for r in rows)
    lines = [f"  [{('x' if r['verdict'] == 'PASS' else ' ' if r['verdict'] == 'FAIL' else '?'):1}] {r['gate']:7} "
             f"{r['rule']:<{w}}  actual: {r['actual']:<40} {r['verdict']}" for r in rows]
    gates = [r for r in rows if r["gate"].startswith("GATE")]
    fails = [r["gate"] for r in gates if r["verdict"] == "FAIL"]
    pend = [r["gate"] for r in gates if r["verdict"] == "n/a"]
    verdict = ("NOT VIABLE: fails " + ", ".join(fails)) if fails else \
        ("all measured gates pass; pending: " + ", ".join(pend) if pend else "ALL FOUR GATES PASS")
    return "\n".join(lines + ["", f"  verdict: {verdict}"])
