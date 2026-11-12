"""Variant V1 (EVENTS.md): percentile-rank test for E1 and E6 on the development period."""
import datetime as dt
import hashlib
import json
import os
import subprocess

import numpy as np
import pandas as pd

from .study import DATA, DEV_END, OUT, SIC, forward_excess


def main():
    panel = pd.read_parquet(os.path.join(OUT, "panel.parquet"))
    panel = panel[panel["date"] <= DEV_END]
    xbi = panel[panel["symbol"] == "XBI"].set_index("date")["adj_close"]
    ev = pd.read_csv(os.path.join(OUT, "event_table.csv"), dtype={"sic": str})
    ev = ev[(ev["period"] == "development") & (ev["small_cap"] == True) & ev["sic"].isin(SIC)  # noqa: E712
            & ev["event"].isin(["E1", "E6"]) & ~ev["near_8k"]]
    dist = {}
    for sym, g in panel[panel["symbol"].isin(set(ev["symbol"]))].groupby("symbol"):
        g = g.reset_index(drop=True)
        dist[sym] = forward_excess(g, xbi, 20, g["date"].iloc[-1] <= "2023-11-30")
    rows = []
    for e in ev.itertuples():
        ex = dist.get(e.symbol)
        if ex is None or e.entry_date not in ex.index or np.isnan(ex[e.entry_date]):
            continue
        valid = ex.dropna().to_numpy()
        x = ex[e.entry_date]
        pct = (np.sum(valid < x) + 0.5 * np.sum(valid == x)) / len(valid)
        rows.append({"event": e.event, "cik": e.cik, "year": e.entry_date[:4], "pct": pct, "n_placebo": len(valid)})
    t = pd.DataFrame(rows)
    results = {}
    for evn, a in t.groupby("event"):
        m = a.groupby("cik")["pct"].mean() - 0.5
        tstat = float(m.mean() / (m.std(ddof=1) / np.sqrt(len(m))))
        results[evn] = {"n_events": int(len(a)), "n_companies": int(len(m)),
                        "mean_percentile": round(float(a["pct"].mean()), 4),
                        "company_mean_percentile": round(float(m.mean()) + 0.5, 4), "t_company": round(tstat, 2),
                        "share_below_median": round(float((a["pct"] < 0.5).mean()), 3),
                        "by_year_mean_percentile": {y: round(float(g["pct"].mean()), 3) for y, g in a.groupby("year")},
                        "verdict": "PASS on development" if tstat <= -2 else "FAIL"}
        print(evn, json.dumps(results[evn], indent=1))
    path = os.path.join(DATA, "trial_log.jsonl")
    lines = [json.loads(x) for x in open(path) if x.strip()]
    prev = lines[-1]["hash"]
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    body = {"timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "trial": "V1 rank test (E1, E6)",
            "phase": "development", "commit": commit, "results": results, "prev_hash": prev}
    body["hash"] = hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode()).hexdigest()
    with open(path, "a") as f:
        f.write(json.dumps(body, sort_keys=True) + "\n")
    print("logged as trial 7 of 10")


if __name__ == "__main__":
    main()
