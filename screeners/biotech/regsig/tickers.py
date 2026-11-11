"""Step 4: sponsor -> SEC entity -> trading symbol, valid for a date range, dead companies included.

For every matched CIK:
  - SEC submissions JSON: SIC code, current tickers (empty for most dead companies);
  - if no current ticker: dei:TradingSymbol from the XBRL of its latest and earliest
    10-K / 10-Q in the window (two filings, so a ticker change shows up);
  - the symbol must have bars in the price file inside the company's reporting life
    (first periodic filing - 730 days .. last periodic filing + 270 days, or the end of the
    data for companies still filing in 2025H2). Bars outside that window are not this
    company's (a dead company's ticker is reused).
Output: regsig_out/ticker_map.csv, one row per (sponsor, cik, symbol).

    BIOCAT_UA="name email" python -m regsig.tickers
"""
import csv
import datetime as dt
import os
import re

from biocat.net import Http

OUT = os.path.join(os.path.dirname(__file__), "..", "regsig_out")
SUB = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
PERIODIC = ("10-K", "10-Q", "20-F", "40-F")
_SYM = re.compile(r"TradingSymbol[^>]*>\s*([A-Za-z][A-Za-z.\-]{0,6})\s*<")
END_OF_DATA = "2025-12-31"


def trading_symbol(http, cik: int, acc: str) -> str | None:
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    try:
        names = [i["name"] for i in http.get_json(f"{base}/index.json")["directory"]["item"]]
    except Exception:  # noqa: BLE001
        return None
    inst = [n for n in names if n.endswith("_htm.xml")] or \
           [n for n in names if n.endswith(".xml") and not re.search(r"(_cal|_def|_lab|_pre|FilingSummary)", n)]
    for n in inst[:2]:
        try:
            m = _SYM.search(http.get_text(f"{base}/{n}"))
        except Exception:  # noqa: BLE001
            continue
        if m:
            return m.group(1).upper()
    return None


def main():
    import pandas as pd
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24 * 365)
    summ_path = os.path.join(OUT, "prices", "symbol_summary.csv")
    if not os.path.exists(summ_path):
        px = pd.read_csv(os.path.join(OUT, "prices", "xnas-itch-20180501-20251231.ohlcv-1d.csv.zst"),
                         usecols=["ts_event", "symbol"])
        px["d"] = px["ts_event"].str[:10]
        px.groupby("symbol")["d"].agg(["min", "max", "size"]).to_csv(summ_path)
    summ = {r["symbol"]: r for r in csv.DictReader(open(summ_path))}
    listed = [r for r in csv.DictReader(open(os.path.join(OUT, "listed_sponsors.csv"))) if int(r["n_trials"]) <= 100]
    rows, n = [], 0
    for r in listed:
        cik = int(r["cik"])
        n += 1
        try:
            sub = http.get_json(SUB.format(cik=cik))
        except Exception as e:  # noqa: BLE001
            rows.append({**r, "symbol": None, "source": f"submissions error: {str(e)[:80]}"})
            continue
        rec = sub.get("filings", {}).get("recent", {})
        per = [(d, a) for d, f, a in zip(rec.get("filingDate", []), rec.get("form", []), rec.get("accessionNumber", []))
               if f in PERIODIC and "2018-01-01" <= d <= END_OF_DATA]
        syms = [(t.upper(), "submissions.tickers") for t in sub.get("tickers") or []]
        if not syms and per:
            per.sort()
            for d, a in {per[-1], per[0]}:
                s = trading_symbol(http, cik, a)
                if s and s not in [x for x, _ in syms]:
                    syms.append((s, f"dei:TradingSymbol in {a} ({d})"))
        alive = r["last_periodic"] >= "2025-07-01"
        lo = (dt.date.fromisoformat(r["first_periodic"]) - dt.timedelta(days=730)).isoformat()
        hi = END_OF_DATA if alive else (dt.date.fromisoformat(r["last_periodic"]) + dt.timedelta(days=270)).isoformat()
        base = {**r, "sic": sub.get("sic"), "sic_desc": sub.get("sicDescription"),
                "exchanges": "|".join(x or "" for x in sub.get("exchanges") or []), "still_filing": alive, "valid_from": lo,
                "valid_to": hi}
        if not syms:
            rows.append({**base, "symbol": None, "source": "no ticker found"})
        for s, src in syms:
            b = summ.get(s) or summ.get(s.replace("-", ".")) or {}
            in_window = bool(b) and b["min"] <= hi and b["max"] >= lo
            rows.append({**base, "symbol": s, "source": src, "bars_first": b.get("min"), "bars_last": b.get("max"),
                         "n_bars": b.get("size"), "has_bars_in_window": in_window})
        if n % 100 == 0:
            print(f"{n}/{len(listed)} entities", flush=True)
    cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("sponsor", "cik", "symbol"), k))
    with open(os.path.join(OUT, "ticker_map.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    ok = [r for r in rows if r.get("has_bars_in_window")]
    print(f"{len(listed)} entities; rows {len(rows)}; with a symbol {sum(bool(r.get('symbol')) for r in rows)}; "
          f"with bars in window {len(ok)}; sponsors covered {len({r['sponsor'] for r in ok})}; "
          f"dead with bars {sum(not r['still_filing'] for r in ok)}")


if __name__ == "__main__":
    main()
