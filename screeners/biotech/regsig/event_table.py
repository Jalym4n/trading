"""Step 6: one row per registry event with the stock it maps to. NO RETURNS are computed.

For each event dated 2018-05-01 .. 2025-12-31 (public posting date):
  symbol        the sponsor's symbol with bars on both sides of the event date; if several,
                the one with the most dollar volume over the prior 20 bars
  entry_date    first trading day AFTER the event date (returns will start at its close)
  market_cap    latest cover-page share count on or before the event (dei, <= 400 days old)
                x the unadjusted close on that count's date, carried to the event date by the
                split-adjusted price change. Unknown when no count exists.
  near_8k       the company filed any 8-K within 3 calendar days of the event date
  period        development (.. 2023-12-31) or holdout (2024-01-01 .. 2025-12-31)
Output: regsig_out/event_table.csv and a count table by event type.

    BIOCAT_UA="name email" python -m regsig.event_table
"""
import bisect
import csv
import datetime as dt
import json
import os

import pandas as pd

from biocat.net import Http

OUT = os.path.join(os.path.dirname(__file__), "..", "regsig_out")
DEV_END, HOLD_END, START = "2023-12-31", "2025-12-31", "2018-05-01"
SMALL_CAP = 1_000_000_000


def share_counts(http, cik: int) -> list[tuple[str, float]]:
    try:
        f = http.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    except Exception:  # noqa: BLE001
        return []
    u = f.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {})
    rows = sorted({(x["end"], float(x["val"])) for v in u.values() for x in v if x.get("val")})
    return rows


def main():
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24 * 365)
    panel = pd.read_parquet(os.path.join(OUT, "panel.parquet"))
    by_sym = {s: g.reset_index(drop=True) for s, g in panel.groupby("symbol")}
    days = sorted(panel["date"].unique())
    tm = [r for r in csv.DictReader(open(os.path.join(OUT, "ticker_map.csv"))) if r["has_bars_in_window"] == "True"]
    by_sponsor = {}
    for r in tm:
        by_sponsor.setdefault(r["sponsor"], []).append(r)
    ciks = sorted({int(r["cik"]) for r in tm})
    shares, k8 = {}, {}
    for i, cik in enumerate(ciks, 1):
        shares[cik] = share_counts(http, cik)
        try:
            rec = http.get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")["filings"]["recent"]
            k8[cik] = sorted(d for d, f in zip(rec.get("filingDate", []), rec.get("form", [])) if f.startswith("8-K"))
        except Exception:  # noqa: BLE001
            k8[cik] = []
        if i % 200 == 0:
            print(f"{i}/{len(ciks)} companies", flush=True)
    suspects = {}
    for r in csv.DictReader(open(os.path.join(OUT, "split_suspects.csv"))):
        suspects.setdefault(r["symbol"], []).append(r["date"])

    rows, drop = [], {"before 2018-05 or after 2025": 0, "sponsor has no mapped symbol": 0, "no bars around the event date": 0}
    for line in open(os.path.join(OUT, "events.jsonl")):
        e = json.loads(line)
        d = e["event_date"]
        if not (START <= d <= HOLD_END):
            drop["before 2018-05 or after 2025"] += 1
            continue
        cands = by_sponsor.get(e["sponsor"])
        if not cands:
            drop["sponsor has no mapped symbol"] += 1
            continue
        best = None
        for c in cands:
            g = by_sym.get(c["symbol"])
            if g is None or not (c["valid_from"] <= d <= c["valid_to"]):
                continue
            dates = g["date"].tolist()
            j = bisect.bisect_right(dates, d)            # first bar strictly after the event date
            if j == 0 or j >= len(dates):
                continue
            # the stock must be trading around the event: a bar within 10 calendar days on each side
            if (dt.date.fromisoformat(d) - dt.date.fromisoformat(dates[j - 1])).days > 10 or \
                    (dt.date.fromisoformat(dates[j]) - dt.date.fromisoformat(d)).days > 10:
                continue
            dv = float(g["dollar_vol"].iloc[max(0, j - 20):j].median())
            if best is None or dv > best[0]:
                best = (dv, c, g, j)
        if best is None:
            drop["no bars around the event date"] += 1
            continue
        dv, c, g, j = best
        cik = int(c["cik"])
        cap = None
        sc = [x for x in shares.get(cik, []) if x[0] <= d and (dt.date.fromisoformat(d) - dt.date.fromisoformat(x[0])).days <= 400]
        if sc:
            s_date, n_sh = sc[-1]
            k = bisect.bisect_right(g["date"].tolist(), s_date) - 1
            if k >= 0:
                cap = n_sh * g["close"].iloc[k] * (g["adj_close"].iloc[j - 1] / g["adj_close"].iloc[k])
        near = any(abs((dt.date.fromisoformat(x) - dt.date.fromisoformat(d)).days) <= 3 for x in k8.get(cik, [])
                   if d[:4] in (x[:4], str(int(x[:4]) + 1), str(int(x[:4]) - 1)))
        rows.append({
            "event": e["event"], "event_date": d, "entry_date": g["date"].iloc[j], "symbol": c["symbol"], "cik": cik,
            "sponsor": e["sponsor"], "nct_id": e["nct_id"], "phase": e["phase"], "sic": c["sic"],
            "period": "development" if d <= DEV_END else "holdout",
            "market_cap": round(cap) if cap else None,
            "small_cap": (cap < SMALL_CAP) if cap else None,
            "median_dollar_vol_20d": round(dv), "near_8k": near,
            "bars_after_entry": len(g) - j - 1, "still_filing": c["still_filing"],
            "suspect_split_within_60d": any(g["date"].iloc[j] <= s <= g["date"].iloc[min(j + 60, len(g) - 1)]
                                            for s in suspects.get(c["symbol"], [])),
            "detail": json.dumps({k: v for k, v in e.items() if k in ("from", "to", "months", "ratio", "dropped",
                                                                      "status_before", "to_type")})})
    with open(os.path.join(OUT, "event_table.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    t = pd.DataFrame(rows)
    print(f"events in file: {len(rows) + sum(drop.values())}; dropped: {drop}; kept {len(rows)}")
    t["cap"] = t["small_cap"].map({True: "small (<$1B)", False: "large", None: "unknown"})
    print(t.groupby(["period", "cap"]).size().unstack(fill_value=0).to_string())
    sm = t[t["small_cap"] == True]                                                        # noqa: E712
    out = pd.DataFrame({
        "dev small-cap": sm[sm.period == "development"].groupby("event").size(),
        "dev, no 8-K nearby": sm[(sm.period == "development") & ~sm.near_8k].groupby("event").size(),
        "dev distinct companies": sm[sm.period == "development"].groupby("event")["cik"].nunique(),
        "holdout small-cap": sm[sm.period == "holdout"].groupby("event").size(),
    }).fillna(0).astype(int)
    print(out.to_string())
    print("pharma/biotech SIC share of small-cap events:", round(float(sm["sic"].isin(["2834", "2836", "2835", "2833", "8731"]).mean()), 3))


if __name__ == "__main__":
    main()
