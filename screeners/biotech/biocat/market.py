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
NASDAQ_SUMMARY = "https://api.nasdaq.com/api/quote/{t}/summary"


@dataclass
class Market:
    price: float | None = None
    price_date: str | None = None
    avg_dollar_vol: float | None = None
    reverse_splits_24m: list[str] | None = None   # None = unknown (no split feed)
    source: str | None = None
    errors: list[str] = field(default_factory=list)
    history_start: str | None = None


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
    first = dt.datetime.fromtimestamp(rows[0][0], dt.timezone.utc).date()
    # history shorter than the window cannot show "no split": unknown unless one was seen
    covered = first <= cutoff + dt.timedelta(days=14)
    return Market(price=float(rows[-1][1]),
                  price_date=dt.datetime.fromtimestamp(rows[-1][0], dt.timezone.utc).date().isoformat(),
                  avg_dollar_vol=sum(c * v for _, c, v in last) / len(last),
                  reverse_splits_24m=sorted(rev) if (rev or covered) else None, source="yahoo",
                  history_start=first.isoformat())


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


def nasdaq_market_cap(http, ticker: str) -> float | None:
    """Nasdaq summary 'Market Cap' (unofficial). Used ONLY when SEC XBRL has no
    dei share count (typically 20-F filers), so ADS ratios are not guessed."""
    d = http.get_json(NASDAQ_SUMMARY.format(t=ticker), {"assetclass": "stocks"})
    v = ((((d or {}).get("data") or {}).get("summaryData") or {}).get("MarketCap") or {}).get("value")
    try:
        return _num(v) if v else None
    except ValueError:
        return None


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


def feed_split_events(feed: list | None) -> list[dict] | None:
    """Price-feed split strings ('2025-07-25 1.0:10.0') -> events. None stays None (unknown)."""
    if feed is None:
        return None
    out = []
    for f in feed:
        d, ratio = f.split()[0], f.split()[1]
        num, den = (float(x) for x in ratio.split(":"))
        out.append({"effective_date": d, "date_basis": "price-feed split date", "filing_date": None,
                    "ratio": f"1-for-{den / num:g}", "source": "price feed", "evidence": f})
    return out


def latest_reverse_split(feed_events: list | None, sec_events: list | None, today: dt.date,
                         same_split_days: int = 15) -> dict:
    """Most recent reverse split from either source. Events within
    same_split_days of each other are one split; the SEC record is kept and the
    other source noted as confirming. reverse_split_date None with
    reverse_split_known False when neither source could be read."""
    known = feed_events is not None or sec_events is not None
    evs = sorted((sec_events or []) + (feed_events or []), key=lambda e: e["effective_date"])
    if not evs:
        return {"reverse_split_date": None, "reverse_split_ratio": None, "months_since_reverse_split": None,
                "reverse_split_source": None if known else "unknown: no feed history and no SEC check",
                "reverse_split_known": known, "_split_events": []}
    last = evs[-1]
    near = [e for e in evs if abs((dt.date.fromisoformat(e["effective_date"])
                                   - dt.date.fromisoformat(last["effective_date"])).days) <= same_split_days]
    sec = next((e for e in reversed(near) if e["source"] != "price feed"), None)
    pick = sec or last
    other = [e["source"] for e in near if e is not pick]
    d = dt.date.fromisoformat(pick["effective_date"])
    months = round((today - d).days / 30.4375, 1)
    return {"reverse_split_date": pick["effective_date"], "reverse_split_ratio": pick["ratio"],
            "months_since_reverse_split": months,
            "reverse_split_source": f"{pick['source']} ({pick['date_basis']})"
                                    + (f"; confirmed by {', '.join(other)}" if other else ""),
            "reverse_split_known": True, "_split_events": evs}


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
