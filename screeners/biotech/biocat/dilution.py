"""Pre-readout dilution facts and press-release price reactions.

All mechanical: listing tier and bid-price notices from filings, months of
runway left at the catalyst, the cash shortfall to catalyst + 6 months as a
share of market cap, and the 1-day price change after each press release.
"""

from __future__ import annotations

import datetime as dt
import html
import re

POSITIVE = re.compile(r"\b(positive|aligns?|achieves?|achieved|successful(?:ly)?|advances?|granted)\b", re.I)
DIVERGENCE_RETURN = -0.15
MARGIN_MONTHS = 2.0
READOUT_LAG_MONTHS = 3.0   # topline follows primary completion (database lock, analysis); a stated convention


def _text(http, url: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", http.get_text(url))))


def bid_price_notices(http, cik: int, submissions: dict, today: dt.date, days: int = 1095) -> list[str]:
    """8-K Item 3.01 (notice of delisting / failure to satisfy a listing rule) in the
    window whose text mentions the minimum bid price. Dates, newest last."""
    since = (today - dt.timedelta(days=days)).isoformat()
    r = submissions.get("filings", {}).get("recent", {})
    out = []
    for acc, form, d, items, doc in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                                        r.get("items", []), r.get("primaryDocument", [])):
        if form in ("8-K", "8-K/A") and "3.01" in (items or "") and d >= since and doc:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
            try:
                if re.search(r"bid price", _text(http, url), re.I):
                    out.append(d)
            except Exception:  # noqa: BLE001
                continue
    return sorted(out)


def _headline(t: str) -> str:
    # drop the exhibit wrapper: "2 tm..._ex99-1.htm EXHIBIT 99.1 Exhibit 99.1 Company Announces ..."
    lead = list(re.finditer(r"(?:EX-99\.?\d*|Exhibit\s+99\.?\d*)", t[:200], re.I))
    if lead:
        t = t[lead[-1].end():]
    cut = re.search(r"\(\s*(?:GLOBE NEWSWIRE|BUSINESS WIRE|PR ?Newswire|ACCESSWIRE|Nasdaq)\b|/PRNewswire/|"
                    r"\b[A-Z][A-Za-z. ]+,\s*(?:[A-Z][a-z.]+\s*)?(?:—|–|-)?\s*\(?(?:January|February|March|April|May|"
                    r"June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4}", t)
    head = t[:cut.start()] if cut and cut.start() > 20 else t[:200]
    return " ".join(head.split())[:220]


def press_releases(http, cik: int, submissions: dict, n: int = 8) -> list[dict]:
    """The last n 8-K exhibit-99 press releases: {date, accepted, headline, url}."""
    r = submissions.get("filings", {}).get("recent", {})
    out = []
    rows = zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
               r.get("acceptanceDateTime", [""] * len(r.get("form", []))))
    for acc, form, d, accepted in rows:
        if form not in ("8-K", "8-K/A"):
            continue
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
        try:
            ex = next((i["name"] for i in http.get_json(f"{base}/index.json")["directory"]["item"]
                       if re.search(r"ex-?99|dex99", i["name"], re.I) and i["name"].endswith(".htm")), None)
            if not ex:
                continue
            out.append({"date": d, "accepted": accepted, "headline": _headline(_text(http, f"{base}/{ex}")),
                        "url": f"{base}/{ex}"})
        except Exception:  # noqa: BLE001
            continue
        if len(out) >= n:
            break
    return out


def _reaction_day(accepted: str | None, filed: str) -> tuple[str, bool]:
    """The first session that could react: acceptance after 16:00 ET -> next day.
    EDGAR acceptanceDateTime is UTC; ET = UTC-4 (EDT) / -5 (EST)."""
    if not accepted:
        return filed, False
    t = dt.datetime.fromisoformat(accepted.replace("Z", "+00:00"))
    offset = 4 if 3 <= t.month <= 10 else 5
    et = t - dt.timedelta(hours=offset)
    return et.date().isoformat(), et.hour >= 16


def one_day_return(closes: list, accepted: str | None, filed: str) -> tuple[str | None, float | None]:
    """(reaction session, close / previous close - 1). None if the dates are not in the series."""
    if not closes:
        return None, None
    day, after_close = _reaction_day(accepted, filed)
    dates = [d for d, _ in closes]
    idx = next((i for i, d in enumerate(dates) if (d > day if after_close else d >= day)), None)
    if idx is None or idx == 0:
        return None, None
    return dates[idx], round(closes[idx][1] / closes[idx - 1][1] - 1, 4)


def divergence(prs: list[dict], closes: list) -> dict:
    for p in prs:
        p["session"], p["return_1d"] = one_day_return(closes, p.get("accepted"), p["date"])
    events = [{"date": p["date"], "session": p["session"], "headline": p["headline"], "return": p["return_1d"],
               "url": p["url"]}
              for p in prs if p["return_1d"] is not None and p["return_1d"] < DIVERGENCE_RETURN
              and POSITIVE.search(p["headline"] or "")]
    return {"press_releases": [{"date": p["date"], "headline": p["headline"], "return_1d": p["return_1d"]}
                               for p in prs],
            "headline_price_divergence": bool(events), "divergence_events": events}


def dilution_facts(price: float | None, exchange: str | None, bid_notices: list | None, split: dict,
                   runway_q: float | None, total_liquid_cash: float | None, cash_as_of: str | None,
                   months_out: float | None, market_cap: float | None, today: dt.date,
                   readout_lag_months: float = READOUT_LAG_MONTHS) -> dict:
    """runway left at the expected READOUT (primary completion + readout_lag_months)
    and the shortfall to readout + 6 months.
    monthly burn = total_liquid_cash / (runway_q x 3), i.e. the burn implied by the
    runway actually used (the lower of computed and company-stated)."""
    from .market import nasdaq_tier
    out = {"share_price": round(price, 4) if price else None,
           "sub_dollar": (price < 1.0) if price else None,
           "nasdaq_tier": nasdaq_tier(exchange),
           "prior_bid_price_deficiency": bool(bid_notices) if bid_notices is not None else None,
           "bid_price_notice_dates": "|".join(bid_notices or []) or None,
           "reverse_split_last_36mo": (split.get("months_since_reverse_split") is not None
                                       and split["months_since_reverse_split"] <= 36)
           if split.get("reverse_split_known") else None,
           "months_runway_minus_months_to_catalyst": None, "must_raise_before_catalyst": None,
           "dilution_at_current_price_pct": None, "runway_end_date": None,
           "readout_lag_months": readout_lag_months,
           "readout_est_date": (today + dt.timedelta(days=(months_out + readout_lag_months) * 30.4375)).isoformat()
           if months_out is not None else None}
    if runway_q is None or not cash_as_of or months_out is None:
        return out
    months_out = months_out + readout_lag_months
    since = (today - dt.date.fromisoformat(cash_as_of)).days / 30.4375
    left = runway_q * 3 - since
    out["runway_end_date"] = (dt.date.fromisoformat(cash_as_of) + dt.timedelta(days=runway_q * 91.25)).isoformat()
    out["months_runway_minus_months_to_catalyst"] = round(left - months_out, 1)
    out["must_raise_before_catalyst"] = left - months_out <= MARGIN_MONTHS
    if total_liquid_cash and runway_q > 0 and market_cap:
        monthly = total_liquid_cash / (runway_q * 3)
        need = max(0.0, monthly * (since + months_out + 6) - total_liquid_cash)
        out["dilution_at_current_price_pct"] = round(100 * need / market_cap, 1)
    return out
