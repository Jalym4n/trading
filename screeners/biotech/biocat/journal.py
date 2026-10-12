"""Thesis journal: append-only JSONL, Brier scoring, calibration.

Events are only ever APPENDED. There is no edit command. Each line carries the
SHA-256 of the previous line, so an in-place edit anywhere breaks the chain and
every command refuses to run until it is explained. A scored entry cannot be
scored again or amended.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

REQUIRED = ["ticker", "catalyst", "catalyst_date", "claim", "market_believes", "why_i_disagree",
            "what_would_prove_me_wrong", "my_probability", "market_implied", "position_usd",
            "entry_price"]
TEXT_FIELDS = ["ticker", "catalyst", "claim", "market_believes", "why_i_disagree",
               "what_would_prove_me_wrong"]
MIN_SCORED = 50


class JournalError(ValueError):
    pass


def _hash(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def read_events(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    events, prev = [], "GENESIS"
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            e = json.loads(line)
            if e.get("prev") != prev:
                raise JournalError(f"{path} line {n}: hash chain broken (file was edited). Refusing.")
            prev = _hash(line)
            events.append(e)
    return events


def _append(path: str, event: dict) -> dict:
    events = read_events(path)
    prev = "GENESIS"
    if events:
        with open(path) as f:
            last = [l.rstrip("\n") for l in f if l.strip()][-1]
        prev = _hash(last)
    event = {**event, "prev": prev}
    line = json.dumps(event, sort_keys=True)
    with open(path, "a") as f:
        f.write(line + "\n")
    return event


def _prob(name: str, v) -> float:
    try:
        p = float(v)
    except (TypeError, ValueError):
        raise JournalError(f"{name} must be a number strictly between 0 and 1, got {v!r}")
    if not 0.0 < p < 1.0:
        raise JournalError(f"{name} must be strictly between 0 and 1 (0 and 1 are refused), got {p}")
    return p


def entries(path: str) -> dict[str, dict]:
    out = {}
    for e in read_events(path):
        if e["type"] == "add":
            out[e["id"]] = {**e["entry"], "id": e["id"], "added": e["ts"], "outcome": None}
        elif e["type"] == "score":
            out[e["id"]]["outcome"] = e["outcome"]
            out[e["id"]]["scored"] = e["ts"]
    return out


def add(path: str, entry: dict, now: dt.datetime | None = None) -> dict:
    missing = [k for k in REQUIRED if entry.get(k) in (None, "")]
    if missing:
        raise JournalError(f"refused: missing required field(s): {', '.join(missing)}")
    for k in TEXT_FIELDS:
        if not str(entry[k]).strip():
            raise JournalError(f"refused: {k} is blank")
    clean = {k: entry[k] for k in REQUIRED}
    clean["my_probability"] = _prob("my_probability", entry["my_probability"])
    clean["market_implied"] = _prob("market_implied", entry["market_implied"])
    try:
        dt.date.fromisoformat(str(entry["catalyst_date"]))
        clean["position_usd"] = float(entry["position_usd"])
        clean["entry_price"] = float(entry["entry_price"])
    except ValueError as e:
        raise JournalError(f"refused: {e}") from e
    clean["ticker"] = str(clean["ticker"]).upper()
    now = now or dt.datetime.now(dt.timezone.utc)
    eid = f"{clean['ticker']}-{now.strftime('%Y%m%d%H%M%S')}"
    if eid in entries(path):
        raise JournalError(f"refused: id {eid} exists")
    return _append(path, {"type": "add", "id": eid, "ts": now.isoformat(), "entry": clean})


def score(path: str, eid: str, outcome, now: dt.datetime | None = None) -> dict:
    if outcome not in (0, 1, "0", "1"):
        raise JournalError("outcome must be 1 (claim came true) or 0 (it did not)")
    es = entries(path)
    if eid not in es:
        raise JournalError(f"no entry {eid}")
    if es[eid]["outcome"] is not None:
        raise JournalError(f"{eid} is already scored ({es[eid]['outcome']}); scored entries are final")
    now = now or dt.datetime.now(dt.timezone.utc)
    return _append(path, {"type": "score", "id": eid, "outcome": int(outcome), "ts": now.isoformat()})


def report(path: str) -> str:
    es = list(entries(path).values())
    scored = [e for e in es if e["outcome"] is not None]
    lines = [f"entries: {len(es)} | open: {len(es) - len(scored)} | scored: {len(scored)}"]
    if not scored:
        return "\n".join(lines + [f"No scored predictions yet. {MIN_SCORED} needed before the result is readable."])
    ps = [e["my_probability"] for e in scored]
    os_ = [e["outcome"] for e in scored]
    mk = [e["market_implied"] for e in scored]
    n = len(scored)
    brier = sum((p - o) ** 2 for p, o in zip(ps, os_)) / n
    base = sum(os_) / n
    brier_ref = sum((base - o) ** 2 for o in os_) / n       # constant forecast at the observed rate
    brier_mkt = sum((p - o) ** 2 for p, o in zip(mk, os_)) / n
    skill = (1 - brier / brier_ref) if brier_ref > 0 else None
    lines += [
        f"Brier (mine):              {brier:.4f}   (0 = perfect, 0.25 = always saying 50%)",
        f"Brier (market-implied):    {brier_mkt:.4f}",
        f"Brier (constant {base:.2f}):    {brier_ref:.4f}",
        "Skill vs constant:         " + (f"{skill:+.3f}  (> 0 beats always forecasting the base rate)"
                                         if skill is not None else "undefined (all outcomes identical)"),
        "",
        "calibration    n   mean p   observed",
    ]
    for lo in [i / 10 for i in range(10)]:
        b = [(p, o) for p, o in zip(ps, os_) if lo <= p < lo + 0.1 or (lo == 0.9 and p == 1.0)]
        if b:
            lines.append(f"  {lo:.1f}-{lo + 0.1:.1f}   {len(b):>3}   {sum(p for p, _ in b) / len(b):.2f}     "
                         f"{sum(o for _, o in b) / len(b):.2f}")
    if n < MIN_SCORED:
        lines += ["", f"NOT READABLE: {n} scored, {MIN_SCORED - n} more needed to reach {MIN_SCORED}. "
                      "Below that, these numbers are noise."]
    return "\n".join(lines)
