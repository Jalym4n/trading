"""Stage 3: cash, burn and runway from SEC XBRL; shelf and ATM from EDGAR FTS.

GOTCHA 1: 10-Q cash-flow facts are YEAR-TO-DATE. Q1 = 3 months, Q2 = 6 months,
Q3 = 9 months, and the 10-K holds the 12-month total. Quarterly = this YTD minus
the previous YTD of the same fiscal year. Reading a 9-month figure as one quarter
understates burn ~3x and makes every runway look safe.
  * the same (start, end) period appears in several filings, sometimes with
    different `fy` tags: dedupe on (start, end), keep the latest-filed value
  * `fy` is unreliable (a 2024 period tagged fy2025 happens): never group by
    `fy`. YTD facts of one fiscal year share the SAME `start` date, so group by
    `start`. For calendar-year filers this is the calendar year of `start`; it
    also handles non-calendar fiscal years correctly.
  * a missing step (Q1 and Q3 present, Q2 absent) yields NO quarterly value for
    that gap, rather than a 6-month difference mislabelled as one quarter.

GOTCHA 4: a company whose recent operating cash flow is positive is not
"infinite runway". It gets status cash_positive and runway None, so it can never
float to the top of a sorted column.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
STI_TAGS = ["ShortTermInvestments", "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
OCF_TAG = "NetCashProvidedByUsedInOperatingActivities"
QUARTER_DAYS = (75, 105)          # one fiscal quarter, allowing 52/53-week calendars


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


# ------------------------------------------------------- YTD -> quarterly --
def dedupe_periods(facts: list[dict]) -> list[dict]:
    """One value per (start, end): the most recently filed one."""
    best: dict[tuple[str, str], dict] = {}
    for f in facts:
        if "start" not in f or "end" not in f:
            continue
        k = (f["start"], f["end"])
        if k not in best or f.get("filed", "") > best[k].get("filed", ""):
            best[k] = f
    return list(best.values())


def ytd_to_quarterly(facts: list[dict]) -> list[dict]:
    """Quarterly values from cumulative YTD duration facts.

    Returns [{start, end, val, derived_from}] sorted by end. `derived_from` is
    'direct' when the fact itself spans one quarter (a Q1 YTD, or a standalone
    quarter some filers report) or 'diff' for a difference of consecutive YTD
    values.
    """
    facts = dedupe_periods(facts)
    by_start: dict[str, list[dict]] = {}
    for f in facts:
        by_start.setdefault(f["start"], []).append(f)
    out = []
    for start, group in by_start.items():
        group.sort(key=lambda f: f["end"])
        s = _d(start)
        # keep only one value per YTD length (3, 6, 9, 12 months)
        prev = None
        for f in group:
            e = _d(f["end"])
            if prev is None:
                span = (e - s).days
                if QUARTER_DAYS[0] <= span <= QUARTER_DAYS[1]:
                    out.append({"start": start, "end": f["end"], "val": f["val"], "derived_from": "direct"})
                prev = f
                continue
            step = (e - _d(prev["end"])).days
            if QUARTER_DAYS[0] <= step <= QUARTER_DAYS[1]:
                q_start = (_d(prev["end"]) + dt.timedelta(days=1)).isoformat()
                out.append({"start": q_start, "end": f["end"], "val": f["val"] - prev["val"],
                            "derived_from": "diff"})
            prev = f
    # a quarter reported directly can coincide with a derived one for the same
    # end date; the directly reported value wins
    uniq: dict[str, dict] = {}
    for q in sorted(out, key=lambda q: (q["end"], q["derived_from"] != "direct")):
        uniq.setdefault(q["end"], q)
    return sorted(uniq.values(), key=lambda q: q["end"])


# ------------------------------------------------------------- balance --
def _units(facts: dict, tag: str) -> list[dict]:
    return facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])


def latest_instant(facts: dict, tags: list[str]) -> tuple[dict | None, str | None]:
    """Latest point-in-time value across fallback tags (first tag wins ties)."""
    best, best_tag = None, None
    for tag in tags:
        for f in _units(facts, tag):
            if "start" in f:          # instants have no start
                continue
            if best is None or f["end"] > best["end"]:
                best, best_tag = f, tag
    return best, best_tag


def instant_at(facts: dict, tags: list[str], end: str) -> tuple[float | None, str | None]:
    for tag in tags:
        vals = [f for f in _units(facts, tag) if "start" not in f and f["end"] == end]
        if vals:
            return max(vals, key=lambda f: f.get("filed", ""))["val"], tag
    return None, None


@dataclass
class Runway:
    cash_usd: float | None
    cash_asof: str | None
    cash_tags: str
    burn_q_usd: float | None          # positive number = cash consumed per quarter
    burn_status: str                  # burning / cash_positive / no_data / stale
    quarters_used: list[dict]
    runway_q: float | None
    runway_needed_q: float | None
    funded_through: bool | None


def compute_runway(facts: dict, months_out: float | None, today: dt.date,
                   n_quarters: int = 2, stale_days: int = 270) -> Runway:
    cash_f, cash_tag = latest_instant(facts, CASH_TAGS)
    cash = cash_asof = None
    tags_used = []
    if cash_f:
        cash_asof = cash_f["end"]
        sti, sti_tag = instant_at(facts, STI_TAGS, cash_asof)   # SAME date as the cash figure
        cash = cash_f["val"] + (sti or 0.0)
        tags_used = [cash_tag] + ([sti_tag] if sti_tag else ["(no short-term investments at this date)"])

    qs = ytd_to_quarterly(_units(facts, OCF_TAG))
    recent = qs[-n_quarters:]
    needed = round(months_out / 3 + 2, 2) if months_out is not None else None
    if len(recent) < n_quarters:
        return Runway(cash, cash_asof, " + ".join(tags_used), None, "no_data", recent, None, needed, None)
    if (today - _d(recent[-1]["end"])).days > stale_days:
        return Runway(cash, cash_asof, " + ".join(tags_used), None, "stale", recent, None, needed, None)

    avg_ocf = sum(q["val"] for q in recent) / len(recent)
    if avg_ocf >= 0:
        return Runway(cash, cash_asof, " + ".join(tags_used), None, "cash_positive", recent,
                      None, needed, None)
    burn = -avg_ocf
    runway = round(cash / burn, 2) if cash is not None else None
    funded = (runway >= needed) if (runway is not None and needed is not None) else None
    return Runway(cash, cash_asof, " + ".join(tags_used), burn, "burning", recent, runway, needed, funded)


# --------------------------------------------------------- shelf / ATM --
def fts_count(http, phrase: str, cik: int, since: dt.date, max_pages: int = 10) -> tuple[int, list[dict]]:
    """Filings mentioning `phrase` filed on/after `since`, by this CIK.

    GOTCHA 3: `ciks` must be 10-digit zero-padded and must NOT be combined with
    `forms` (that combination silently returns 0 hits). Query by CIK alone and
    filter dates client-side.
    """
    hits, start = [], 0
    for _ in range(max_pages):
        d = http.get_json(FTS_URL, {"q": f'"{phrase}"', "ciks": f"{cik:010d}", "from": str(start)})
        page = d.get("hits", {}).get("hits", [])
        hits.extend(page)
        total = d.get("hits", {}).get("total", {}).get("value", 0)
        start += len(page)
        if not page or start >= total:
            break
    keep = []
    seen = set()
    for h in hits:
        s = h.get("_source", {})
        adsh = s.get("adsh") or h.get("_id", "").split(":")[0]
        if adsh in seen:
            continue
        if s.get("file_date") and _d(s["file_date"]) >= since:
            seen.add(adsh)
            keep.append({"form": s.get("form"), "file_date": s.get("file_date"), "adsh": adsh})
    return len(keep), sorted(keep, key=lambda x: x["file_date"], reverse=True)


def dilution_facts(http, cik: int, today: dt.date, lookback_days: int = 3 * 365) -> dict:
    """Shelf AND ATM both present within the lookback (a shelf is effective 3 years)."""
    since = today - dt.timedelta(days=lookback_days)
    n_shelf, shelf = fts_count(http, "shelf registration", cik, since)
    n_atm, atm = fts_count(http, "at-the-market", cik, since)
    return {"shelf_count": n_shelf, "atm_count": n_atm,
            "dilution_risk": bool(n_shelf and n_atm),     # fact: both present, nothing more
            "shelf_filings": shelf[:10], "atm_filings": atm[:10],
            "lookback_since": since.isoformat()}


def shares_outstanding(facts: dict) -> tuple[float | None, str | None]:
    rows = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}) \
        .get("units", {}).get("shares", [])
    if not rows:
        return None, None
    end = max(r["end"] for r in rows)
    at_end = [r for r in rows if r["end"] == end]
    latest_filed = max(r.get("filed", "") for r in at_end)
    # several share classes are several rows in the SAME filing: sum them
    same_filing = {r.get("accn"): None for r in at_end if r.get("filed", "") == latest_filed}
    accn = next(iter(same_filing))
    total = sum(r["val"] for r in at_end if r.get("accn") == accn)
    return float(total), end
