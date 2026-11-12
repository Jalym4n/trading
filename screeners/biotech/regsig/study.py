"""Step 7: event study on the DEVELOPMENT period only. Rules: EVENTS.md "Analysis rules".

The holdout is never touched here: only events whose whole window ends by DEV_END are used,
and company placebo returns are computed from development-period bars only.

    python -m regsig.study
"""
import datetime as dt
import hashlib
import json
import os
import subprocess

import numpy as np
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "..", "regsig_out")
DATA = os.path.join(os.path.dirname(__file__), "..", "regsig_data")
DEV_END = "2023-12-31"
SIC = {"2833", "2834", "2835", "2836", "8731"}
PREDICTED = {"E1": -1, "E2": +1, "E3": -1, "E4": -1, "E5": 0, "E6": -1}
MIN_EVENTS = 100
HORIZONS = (20, 60)


def forward_excess(g: pd.DataFrame, xbi: pd.Series, h: int, stopped: bool) -> pd.Series:
    """One symbol's development bars -> excess return for every entry bar i, index = entry date.
    Return runs close[i] -> close[i+h]. If fewer than h later bars exist: when the stock stopped
    trading inside development (`stopped`), its last close is the exit; otherwise the window is
    cut by the development boundary and the entry is NaN (not usable). XBI over the same dates."""
    c, d = g["adj_close"].to_numpy(float), g["date"].to_numpy()
    n = len(c)
    i = np.arange(n)
    j = np.minimum(i + h, n - 1)
    ex = c[j] / c - 1.0 - (xbi.reindex(d[j]).to_numpy() / xbi.reindex(d).to_numpy() - 1.0)
    short = (j - i) < h
    ex[short & (not stopped)] = np.nan
    ex[j == i] = np.nan
    return pd.Series(ex, index=d)


def clustered(x: pd.Series, cluster: pd.Series) -> dict:
    m = x.groupby(cluster).mean()
    n = len(m)
    t = float(m.mean() / (m.std(ddof=1) / np.sqrt(n))) if n > 2 and m.std(ddof=1) > 0 else float("nan")
    return {"n_events": int(len(x)), "n_companies": int(n), "mean_pct": round(100 * float(x.mean()), 2),
            "company_mean_pct": round(100 * float(m.mean()), 2), "median_pct": round(100 * float(x.median()), 2),
            "t_company": round(t, 2)}


def main():
    panel = pd.read_parquet(os.path.join(OUT, "panel.parquet"))
    panel = panel[panel["date"] <= DEV_END]                       # development bars only
    xbi = panel[panel["symbol"] == "XBI"].set_index("date")["adj_close"]
    ev = pd.read_csv(os.path.join(OUT, "event_table.csv"), dtype={"sic": str})
    ev = ev[(ev["period"] == "development") & (ev["small_cap"] == True) & ev["sic"].isin(SIC)]   # noqa: E712
    by_sym = {s: g.reset_index(drop=True) for s, g in panel.groupby("symbol") if s in set(ev["symbol"])}
    fwd, placebo = {}, {}
    for sym, g in by_sym.items():
        stopped = g["date"].iloc[-1] <= "2023-11-30"           # last development bar well before the boundary
        fwd[sym] = {h: forward_excess(g, xbi, h, stopped) for h in HORIZONS}
        placebo[sym] = {h: float(np.nanmean(fwd[sym][h])) if fwd[sym][h].notna().any() else np.nan for h in HORIZONS}
    rows = []
    for e in ev.itertuples():
        f = fwd.get(e.symbol)
        if f is None or e.entry_date not in f[20].index:
            continue
        r = {"event": e.event, "symbol": e.symbol, "cik": e.cik, "entry_date": e.entry_date, "near_8k": bool(e.near_8k),
             "suspect": bool(e.suspect_split_within_60d), "year": e.entry_date[:4]}
        for h in HORIZONS:
            x = float(f[h][e.entry_date])
            r[f"ex{h}"], r[f"ab{h}"] = x, x - placebo[e.symbol][h]
        if not np.isnan(r["ab20"]):
            rows.append(r)
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(OUT, "study_dev_events.csv"), index=False)
    results = {}
    print(f"development events with a complete 20-day window: {len(t)}; companies {t['cik'].nunique()}\n")
    for evn in ["E1", "E2", "E3", "E4", "E5", "E6"]:
        a = t[t["event"] == evn]
        clean = a[~a["near_8k"]]
        res = {"predicted_sign": PREDICTED[evn], "n_all": len(a), "n_no_8k": len(clean)}
        if len(a) < MIN_EVENTS:
            res["verdict"] = f"NOT TESTABLE ({len(a)} events < {MIN_EVENTS})"
        else:
            res["primary_20d_no8k_abnormal"] = clustered(clean["ab20"], clean["cik"])
            for name, frame, col in (("60d_no8k_abnormal", clean, "ab60"), ("20d_all_abnormal", a, "ab20"),
                                     ("60d_all_abnormal", a, "ab60"), ("20d_no8k_raw_excess", clean, "ex20"),
                                     ("20d_no8k_abnormal_no_suspect", clean[~clean["suspect"]], "ab20")):
                fr = frame.dropna(subset=[col])
                res[name] = clustered(fr[col], fr["cik"])
            p = res["primary_20d_no8k_abnormal"]
            sign_ok = PREDICTED[evn] == 0 or np.sign(p["company_mean_pct"]) == PREDICTED[evn]
            res["by_year_20d_no8k_abnormal_pct"] = {y: round(100 * float(g["ab20"].mean()), 2)
                                                    for y, g in clean.groupby("year")}
            res["verdict"] = ("PASS on development" if abs(p["t_company"]) >= 2 and sign_ok else
                              "FAIL: wrong sign" if abs(p["t_company"]) >= 2 else "FAIL: not distinguishable from zero")
        results[evn] = res
        print(evn, json.dumps(res, indent=1))
    # trial log (tracked in git), one line per event type
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "trial_log.jsonl")
    prev = "0" * 64
    if os.path.exists(path):
        lines = [json.loads(x) for x in open(path) if x.strip()]
        prev = lines[-1]["hash"] if lines else prev
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = None
    with open(path, "a") as f:
        for evn, res in results.items():
            body = {"timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "trial": evn,
                    "phase": "development", "commit": commit, "results": res, "prev_hash": prev}
            body["hash"] = hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode()).hexdigest()
            prev = body["hash"]
            f.write(json.dumps(body, sort_keys=True) + "\n")
    print(f"\nlogged {len(results)} trials -> regsig_data/trial_log.jsonl (budget 10)")


if __name__ == "__main__":
    main()
