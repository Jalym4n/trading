"""Download free BTC/ETH OHLCV history into the canonical fvg_lab CSV format.

Output columns: timestamp,open,high,low,close,volume
timestamp is the bar's OPEN time in UTC (ISO 8601), which is what
data.load_csv() assumes. Load it with:

    df = data.add_session(data.load_csv("btc_5m.csv", tz="UTC"), "00:00", "24:00")

Sources (no API key needed):
  coinbase  BTC-USD / ETH-USD, Coinbase Exchange public candles API.
            USD-quoted, available in Canada. 300 bars per request, so 5m
            since 2017 is ~3,500 requests (roughly 20-30 min).
  binance   BTCUSDT / ETHUSDT, monthly archive files from data.binance.vision.
            Fast (one file per month), SHA-256 verified, complete months only.
            USDT-quoted. Binance does not serve Ontario, but the historical
            archive is still fine as research data.

Rules this script enforces:
  * The current, still-forming bar is never written (it would be a partial
    bar whose high/low can still change, a quiet form of lookahead).
  * Missing bars are REPORTED, never filled. A forward-filled flat bar is a
    fabricated price; inventing data is worse than a gap.

Usage:
  python fetch_crypto.py --source coinbase --symbol BTC-USD --interval 5m --start 2017-01-01 --out btc_5m.csv
  python fetch_crypto.py --source binance  --symbol BTCUSDT --interval 5m --start 2018-01-01 --out btc_5m_binance.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
COINBASE_GRANULARITIES = {60, 300, 900, 3600, 21600, 86400}
COINBASE_URL = "https://api.exchange.coinbase.com/products/{sym}/candles"
BINANCE_URL = "https://data.binance.vision/data/spot/monthly/klines/{sym}/{iv}/{sym}-{iv}-{y}-{m:02d}.zip"
HEADER = ["timestamp", "open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------- http -----
def _get(url: str, retries: int = 5) -> bytes:
    """GET with backoff on rate limits and server errors. Tests replace this."""
    req = urllib.request.Request(url, headers={"User-Agent": "fvg_lab-fetch/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


# ------------------------------------------------------------ coinbase -----
def fetch_coinbase(symbol: str, step: int, start: int, end: int, pause: float = 0.2):
    """Yield (open_ts, o, h, l, c, v) ascending. Coinbase rows are
    [time, low, high, open, close, volume], newest first, time = bar open."""
    if step not in COINBASE_GRANULARITIES:
        raise ValueError(f"coinbase supports {sorted(COINBASE_GRANULARITIES)} seconds")
    chunk = 300 * step
    t = start
    while t < end:
        t1 = min(t + chunk, end)
        url = (COINBASE_URL.format(sym=symbol)
               + f"?granularity={step}&start={_iso(t)}&end={_iso(t1 - step)}")
        rows = json.loads(_get(url))
        if isinstance(rows, dict):  # error payload, e.g. {"message": "NotFound"}
            raise RuntimeError(f"coinbase error: {rows}")
        for r in sorted(rows, key=lambda r: r[0]):
            ts = int(r[0])
            if t <= ts < t1:
                yield ts, r[3], r[2], r[1], r[4], r[5]
        t = t1
        if pause:
            time.sleep(pause)


# ------------------------------------------------------------- binance -----
def _binance_month(symbol: str, iv: str, y: int, m: int):
    url = BINANCE_URL.format(sym=symbol, iv=iv, y=y, m=m)
    try:
        blob = _get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # month not published (before listing, or not yet complete)
        raise
    want = _get(url + ".CHECKSUM").decode().split()[0]
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise RuntimeError(f"checksum mismatch for {url}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode()
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():
            continue  # header line, present in some files
        raw = int(row[0])
        # open_time is milliseconds, microseconds in files from 2025 on
        ts = raw // 1_000_000 if raw > 10**14 else raw // 1000
        out.append((ts, row[1], row[2], row[3], row[4], row[5]))
    return out


def fetch_binance(symbol: str, iv: str, start: int, end: int):
    d = datetime.fromtimestamp(start, tz=timezone.utc)
    y, m = d.year, d.month
    stop = datetime.fromtimestamp(end - 1, tz=timezone.utc)  # end is exclusive
    while (y, m) <= (stop.year, stop.month):
        rows = _binance_month(symbol, iv, y, m)
        if rows is None:
            print(f"  {y}-{m:02d}: not published, skipped", file=sys.stderr)
        else:
            for r in rows:
                if start <= r[0] < end:
                    yield r
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


# ---------------------------------------------------------------- main -----
def gap_report(stamps: list[int], step: int) -> tuple[int, list[tuple[int, int]]]:
    """Missing-bar count and the largest gaps as (after_ts, bars_missing)."""
    gaps = [(a, (b - a) // step - 1) for a, b in zip(stamps, stamps[1:]) if b - a > step]
    return sum(g for _, g in gaps), sorted(gaps, key=lambda g: -g[1])[:5]


def download(source: str, symbol: str, interval: str, start: str, end: str | None,
             out: str, now: int | None = None) -> dict:
    step = INTERVALS[interval]
    now = int(time.time()) if now is None else now
    t0 = _epoch(start) // step * step
    # last bar we may write must have CLOSED: open + step <= now
    t_end = min(_epoch(end) if end else now, now) // step * step
    if source == "coinbase":
        rows = fetch_coinbase(symbol, step, t0, t_end)
    elif source == "binance":
        rows = fetch_binance(symbol, interval, t0, t_end)
    else:
        raise ValueError(source)

    seen: dict[int, tuple] = {}
    for r in rows:
        if r[0] + step <= now and r[0] % step == 0:
            seen.setdefault(r[0], r)  # first copy wins, duplicates dropped
    stamps = sorted(seen)
    if not stamps:
        raise RuntimeError("no bars returned; check symbol and dates")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".part"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for ts in stamps:
            _, o, h, l, c, v = seen[ts]
            w.writerow([_iso(ts), o, h, l, c, v])
    os.replace(tmp, out)

    missing, worst = gap_report(stamps, step)
    expected = (stamps[-1] - stamps[0]) // step + 1
    return {
        "bars": len(stamps),
        "first": _iso(stamps[0]),
        "last": _iso(stamps[-1]),
        "missing_bars": missing,
        "missing_pct": 100 * missing / expected,
        "largest_gaps": [(_iso(a), n) for a, n in worst],
        "out": out,
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["coinbase", "binance"], default="coinbase")
    ap.add_argument("--symbol", default=None, help="BTC-USD (coinbase) or BTCUSDT (binance)")
    ap.add_argument("--interval", choices=list(INTERVALS), default="5m")
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--end", default=None, help="exclusive, default now")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    sym = a.symbol or ("BTC-USD" if a.source == "coinbase" else "BTCUSDT")
    out = a.out or f"{sym.lower().replace('-', '')}_{a.interval}_{a.source}.csv"

    print(f"fetching {sym} {a.interval} from {a.source}, {a.start} -> {a.end or 'now'}")
    rep = download(a.source, sym, a.interval, a.start, a.end, out)
    print(f"wrote {rep['bars']:,} bars to {rep['out']}  ({rep['first']} -> {rep['last']})")
    print(f"missing bars: {rep['missing_bars']:,} ({rep['missing_pct']:.3f}%), NOT filled")
    for ts, n in rep["largest_gaps"]:
        print(f"  gap after {ts}: {n} bars")

    # final check through the framework's own validator
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fvg_lab import data
    df = data.load_csv(out, tz="UTC")
    print(f"data.load_csv + validate: OK, {len(df):,} rows")


if __name__ == "__main__":
    main()
