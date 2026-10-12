"""Stage 4: price, dollar volume, reverse splits. Free, UNOFFICIAL sources.

Primary   Yahoo Finance chart endpoint (close, volume, split events). No key,
          not an official API: it can change or rate-limit without notice.
Fallback  Nasdaq quote API (close, volume; no split history).

A feed failure never drops a candidate: fields come back None and the filter
step keeps the name (spec: "never drop a name because of a data-source failure").
Market cap = latest close x dei:EntityCommonStockSharesOutstanding (from XBRL).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
NASDAQ = "https://api.nasdaq.com/api/quote/{t}/historical"


@dataclass
class Market:
    price: float | None = None
    price_date: str | None = None
    avg_dollar_vol: float | None = None
    reverse_splits_24m: list[str] | None = None   # None = unknown (no split feed)
    source: str | None = None
    errors: list[str] = field(default_factory=list)


def _from_yahoo(http, ticker: str, today: dt.date, days: int) -> Market:
    d = http.get_json(YAHOO.format(t=ticker), {"range": "2y", "interval": "1d", "events": "split"})
    res = (d.get("chart", {}).get("result") or [None])[0]
    if not res:
        raise RuntimeError(f"yahoo: {d.get('chart', {}).get('error')}")
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    rows = [(t, c, v) for t, c, v in zip(ts, q.get("close", []), q.get("volume", []))
            if c is not None and v is not None]
    if not rows:
        raise RuntimeError("yahoo: no price rows")
    last = rows[-days:]
    cutoff = today - dt.timedelta(days=730)
    rev = []
    for s in (res.get("events", {}).get("splits") or {}).values():
        when = dt.datetime.fromtimestamp(s["date"], dt.timezone.utc).date()
        if when >= cutoff and s.get("numerator", 1) < s.get("denominator", 1):
            rev.append(f"{when.isoformat()} {s['numerator']}:{s['denominator']}")
    return Market(price=float(rows[-1][1]),
                  price_date=dt.datetime.fromtimestamp(rows[-1][0], dt.timezone.utc).date().isoformat(),
                  avg_dollar_vol=sum(c * v for _, c, v in last) / len(last),
                  reverse_splits_24m=sorted(rev), source="yahoo")


def _num(s: str) -> float:
    return float(str(s).replace("$", "").replace(",", ""))


def _from_nasdaq(http, ticker: str, today: dt.date, days: int) -> Market:
    d = http.get_json(NASDAQ.format(t=ticker), {
        "assetclass": "stocks", "fromdate": (today - dt.timedelta(days=60)).isoformat(),
        "todate": today.isoformat(), "limit": "60"})
    rows = (((d or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    if not rows:
        raise RuntimeError("nasdaq: no rows")
    rows = rows[:days]                       # newest first
    dv = [_num(r["close"]) * _num(r["volume"]) for r in rows]
    m, dd, y = rows[0]["date"].split("/")
    return Market(price=_num(rows[0]["close"]), price_date=f"{y}-{m}-{dd}",
                  avg_dollar_vol=sum(dv) / len(dv), reverse_splits_24m=None, source="nasdaq")


def fetch_market(http, ticker: str, today: dt.date, days: int = 30) -> Market:
    errors = []
    for fn in (_from_yahoo, _from_nasdaq):
        try:
            m = fn(http, ticker, today, days)
            m.errors = errors
            return m
        except Exception as e:  # noqa: BLE001 - any feed failure means "unknown", not "drop"
            errors.append(f"{fn.__name__}: {e}")
    return Market(errors=errors)


def size_filter(market_cap: float | None, adv: float | None, rev_splits: list | None,
                cap_lo: float = 50e6, cap_hi: float = 400e6, min_adv: float = 300e3) -> tuple[bool, list[str]]:
    """Drop only on a KNOWN value outside the range. Unknown passes, flagged."""
    reasons, unknown = [], []
    if market_cap is None:
        unknown.append("market_cap")
    elif not (cap_lo <= market_cap <= cap_hi):
        reasons.append(f"market_cap {market_cap / 1e6:.0f}M outside {cap_lo / 1e6:.0f}-{cap_hi / 1e6:.0f}M")
    if adv is None:
        unknown.append("avg_dollar_vol")
    elif adv < min_adv:
        reasons.append(f"avg_dollar_vol {adv / 1e3:.0f}k < {min_adv / 1e3:.0f}k")
    if rev_splits is None:
        unknown.append("reverse_split")
    elif rev_splits:
        reasons.append(f"reverse split in 24m: {rev_splits}")
    return (not reasons), reasons + [f"unknown:{u}" for u in unknown]
