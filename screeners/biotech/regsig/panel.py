"""Step 5: price panel for the mapped symbols, split-adjusted with SEC-confirmed splits only.

  bars      the Databento Nasdaq daily file, mapped symbols + benchmarks (XBI, IBB, SPY);
            bars on non-trading days (weekends, holidays: a few odd lots) are dropped. A
            trading day is a date on which at least 3,000 symbols printed.
  splits    a candidate is an overnight jump (previous close -> open AND previous close ->
            close) within 8% of an integer ratio N >= 2 (reverse split) or 1/N (forward split).
            It is CONFIRMED if the company filed an 8-K with Item 5.03 or 3.03 in the 45 days
            before to 10 days after; an unconfirmed RISE of 3x or more is also treated as a
            reverse split (foreign filers have no 8-K). Those are adjusted, at the integer
            ratio (the true ratio can differ by one step: the day's own move is mixed in).
            Unconfirmed 2x rises and all unconfirmed falls are NOT adjusted (real doublings and
            crashes) and go to split_suspects.csv; event windows containing one are flagged.
Outputs (regsig_out/): panel.parquet [symbol, date, close, adj_close, volume, dollar_vol],
splits.csv, split_suspects.csv.  No returns are computed here.

    BIOCAT_UA="name email" python -m regsig.panel
"""
import csv
import datetime as dt
import os

import numpy as np
import pandas as pd

from biocat.net import Http

OUT = os.path.join(os.path.dirname(__file__), "..", "regsig_out")
BENCH = ["XBI", "IBB", "SPY"]
TOL = 0.08


def split_candidates(g: pd.DataFrame) -> list[dict]:
    """g: one symbol's bars sorted by date (open, close)."""
    pc = g["close"].shift(1)
    rc, ro = (g["close"] / pc).to_numpy(), (g["open"] / pc).to_numpy()
    out = []
    for i in np.flatnonzero((rc > 1.8) | (rc < 0.56)):
        r = rc[i]
        n = round(r) if r > 1 else round(1 / r)
        ratio = float(n) if r > 1 else 1.0 / n
        if n >= 2 and abs(r / ratio - 1) <= TOL and abs(ro[i] / ratio - 1) <= 1.5 * TOL:
            out.append({"date": g["date"].iloc[i], "ratio": ratio, "kind": f"1-for-{n}" if r > 1 else f"{n}-for-1",
                        "close_ratio": round(float(r), 4)})
    return out


def main():
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24 * 365)
    tm = [r for r in csv.DictReader(open(os.path.join(OUT, "ticker_map.csv"))) if r["has_bars_in_window"] == "True"]
    cik_of = {}
    for r in tm:
        cik_of.setdefault(r["symbol"], set()).add(int(r["cik"]))
    want = set(cik_of) | set(BENCH)
    px = pd.read_csv(os.path.join(OUT, "prices", "xnas-itch-20180501-20251231.ohlcv-1d.csv.zst"),
                     usecols=["ts_event", "open", "close", "volume", "symbol"])
    px["date"] = px["ts_event"].str[:10]
    per_day = px.groupby("date")["symbol"].size()
    trading_days = set(per_day[per_day >= 3000].index)
    print(f"{len(per_day)} dates in file; {len(trading_days)} trading days; dropped "
          f"{int((~px['date'].isin(trading_days)).sum()):,} bars on non-trading days")
    px = px[px["date"].isin(trading_days) & px["symbol"].isin(want)].drop(columns=["ts_event"])
    px = px.sort_values(["symbol", "date"]).reset_index(drop=True)

    k8 = {}                                           # cik -> dates of 8-Ks with Item 5.03 / 3.03
    for cik in sorted({c for s in cik_of.values() for c in s}):
        try:
            rec = http.get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")["filings"]["recent"]
        except Exception:  # noqa: BLE001
            continue
        k8[cik] = [d for d, f, it in zip(rec.get("filingDate", []), rec.get("form", []), rec.get("items", []))
                   if f.startswith("8-K") and ("5.03" in (it or "") or "3.03" in (it or ""))]
    splits, suspects = [], []
    factor = np.ones(len(px))
    for sym, g in px.groupby("symbol", sort=False):
        if sym in BENCH:
            continue
        for c in split_candidates(g):
            d = dt.date.fromisoformat(c["date"])
            lo, hi = (d - dt.timedelta(days=45)).isoformat(), (d + dt.timedelta(days=10)).isoformat()
            hit = [x for cik in cik_of[sym] for x in k8.get(cik, []) if lo <= x <= hi]
            row = {"symbol": sym, **c, "sec_8k": "|".join(sorted(hit))}
            # an unconfirmed overnight rise of 3x or more is a reverse split at a filer with no 8-K
            # (foreign issuers file 6-Ks: ACB, AFMD, AKTX); a real stock does not triple to an exact
            # integer overnight. An unconfirmed 2x rise, or any unconfirmed FALL, is left alone:
            # doublings and -50% / -80% crashes on trial news are real.
            if not hit and c["ratio"] >= 3:
                row["sec_8k"] = "unconfirmed: ratio >= 3 treated as a reverse split"
                hit = True
            if hit:
                splits.append(row)
                idx = g.index[g["date"] < c["date"]]
                factor[idx] *= c["ratio"]                # bars before the split move to the new share basis
            else:
                suspects.append(row)
    px["adj_close"] = px["close"] * factor
    px["dollar_vol"] = px["close"] * px["volume"]
    px[["symbol", "date", "close", "adj_close", "volume", "dollar_vol"]].to_parquet(os.path.join(OUT, "panel.parquet"))
    for name, rows in (("splits.csv", splits), ("split_suspects.csv", suspects)):
        with open(os.path.join(OUT, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["symbol", "date", "kind", "ratio", "close_ratio", "sec_8k"])
            w.writeheader()
            w.writerows(rows)
    rev = sum(r["ratio"] > 1 for r in splits)
    print(f"panel: {len(px):,} bars, {px['symbol'].nunique()} symbols; confirmed splits {len(splits)} "
          f"({rev} reverse, {len(splits) - rev} forward); unconfirmed split-like jumps {len(suspects)}")


if __name__ == "__main__":
    main()
