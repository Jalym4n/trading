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
import re
import html
from dataclasses import dataclass, field

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue", "Cash",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
# ONE tag per category, first present wins: the same securities are often
# tagged twice (EVMN 2026-06-30: ShortTermInvestments 140,759k and
# HeldToMaturitySecuritiesCurrent 140,759k are one holding).
STI_TAGS = ["ShortTermInvestments", "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "HeldToMaturitySecuritiesCurrent"]
LTI_TAGS = ["LongTermInvestments", "MarketableSecuritiesNoncurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent", "HeldToMaturitySecuritiesNoncurrent"]
RESTRICTED_TAGS = ["RestrictedCashCurrent", "RestrictedCashNoncurrent", "RestrictedCash",
                   "RestrictedCashAndCashEquivalentsAtCarryingValue"]
OCF_TAG = "NetCashProvidedByUsedInOperatingActivities"
MIN_PLAUSIBLE_BURN = 250_000      # $/quarter; below this with >$10M cash the burn is an artifact
MIN_CASH_FOR_FLOOR = 10_000_000
MAX_PLAUSIBLE_RUNWAY_Q = 40       # quarters; runway is never emitted above this (hard ceiling 200)
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


def cash_instant(facts: dict) -> tuple[dict | None, str | None]:
    """Cash at the latest balance-sheet date. Some filers tag
    CashAndCashEquivalentsAtCarryingValue = 0 every quarter and report the real
    figure under us-gaap:Cash (MAIA, 2026-06-30: 0 vs 27,595,831). At that
    date the first tag in CASH_TAGS with a NON-ZERO value wins; all zero -> 0."""
    latest, _ = latest_instant(facts, CASH_TAGS)
    if not latest:
        return None, None
    first = None
    for tag in CASH_TAGS:
        val, _t = instant_at(facts, [tag], latest["end"])
        if val is None:
            continue
        hit = ({"end": latest["end"], "val": val}, tag)
        first = first or hit
        if val != 0:
            return hit
    return first


@dataclass
class Runway:
    cash_usd: float | None
    cash_asof: str | None
    cash_tags: str
    burn_q_usd: float | None          # positive number = cash consumed per quarter
    burn_status: str                  # burning / cash_positive / no_data / stale / burn_implausible
    quarters_used: list[dict]
    runway_q: float | None
    runway_needed_q: float | None
    funded_through: bool | None
    components: dict = field(default_factory=dict)


def compute_runway(facts: dict, months_out: float | None, today: dt.date,
                   n_quarters: int = 2, stale_days: int = 270) -> Runway:
    r = _compute_runway(facts, months_out, today, n_quarters, stale_days)
    r.components = cash_components(facts)
    return r


def cash_components(facts: dict) -> dict:
    """Liquid cash as a SUM of categories at one balance-sheet date (the date of
    the cash figure): cash and equivalents + short-term investments + long-term
    investments (included, flagged illiquid). Restricted cash is reported and
    excluded; when the only cash tag already includes restricted cash it is
    subtracted. One tag per category (first present) so a holding tagged twice
    is counted once. EVMN 2026-06-30: 60,034k + 140,759k + 87,214k = 287,999k."""
    cash_f, cash_tag = cash_instant(facts)
    empty = {"cash_and_equivalents": None, "short_term_investments": None, "long_term_investments": None,
             "restricted_cash": None, "total_liquid_cash": None, "cash_tags_found": [], "cash_as_of_date": None,
             "liquid_cash_ex_long_term": None}
    if not cash_f:
        return empty
    d = cash_f["end"]
    sti, sti_tag = instant_at(facts, STI_TAGS, d)
    lti, lti_tag = instant_at(facts, LTI_TAGS, d)
    restricted = 0.0
    r_tags = []
    for t in RESTRICTED_TAGS[:2]:
        v, tg = instant_at(facts, [t], d)
        if v:
            restricted += v
            r_tags.append(tg)
    if not r_tags:
        v, tg = instant_at(facts, RESTRICTED_TAGS[2:], d)
        if v:
            restricted, r_tags = v, [tg]
    ce = cash_f["val"]
    if cash_tag and "RestrictedCash" in cash_tag and restricted:
        ce -= restricted                      # the total tag includes restricted cash; take it out
    total = ce + (sti or 0.0) + (lti or 0.0)
    tags = [cash_tag] + [t for t in (sti_tag, lti_tag) if t]
    return {"cash_and_equivalents": ce, "short_term_investments": sti, "long_term_investments": lti,
            "restricted_cash": restricted or None, "total_liquid_cash": total, "cash_tags_found": tags,
            "cash_as_of_date": d, "liquid_cash_ex_long_term": ce + (sti or 0.0),
            "restricted_tags": r_tags}


def _compute_runway(facts: dict, months_out: float | None, today: dt.date,
                    n_quarters: int = 2, stale_days: int = 270) -> Runway:
    comp = cash_components(facts)
    cash, cash_asof = comp["total_liquid_cash"], comp["cash_as_of_date"]
    tags_used = comp["cash_tags_found"] or []

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
    # Sanity floors. A YTD-differencing artifact (one quarter -18.9M, the next
    # +18.9M: ZURA 2026) averages to a near-zero "burn" and a five-digit runway
    # that sorts to the top of any column. The quarters stay visible in
    # quarters_used; the ratio is withheld.
    if (burn < MIN_PLAUSIBLE_BURN and (cash or 0) > MIN_CASH_FOR_FLOOR) or \
            (runway is not None and runway > MAX_PLAUSIBLE_RUNWAY_Q):
        return Runway(cash, cash_asof, " + ".join(tags_used), None, "burn_implausible", recent,
                      None, needed, None)
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


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


_RATIO = re.compile(r"\b(?:1|one)[- ]for[- ](\d{1,4}|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                    r"fifteen|twenty|twenty[- ]five|thirty|forty|fifty)\b", re.I)
_EFFECTED = re.compile(r"\b(effective|effected|became effective|filed a certificate of amendment)\b", re.I)


def split_sentences_effected(text: str) -> list[str]:
    """Evidence that a reverse split was EFFECTED, from one document: a sentence
    giving a single 1-for-N ratio for the reverse split (ranges, proposals and
    board discretion excluded) AND a sentence saying the reverse split became
    effective / was effected. Returns [ratio sentence, effect sentence] or []."""
    t = re.sub(r"<[^>]+>", " ", text)
    t = re.sub(r"\s+", " ", html.unescape(t))
    # "5:00 p.m. Eastern Time on May 29, 2026" must stay one sentence
    t = re.sub(r"\b([ap])\.\s?m\.", r"\1m", t, flags=re.I)
    t = re.sub(r"\b(Inc|Corp|Co|Ltd|No|Nos|Mr|Ms|Dr|U\.S)\.", lambda m_: m_.group(1).replace(".", ""), t)
    sents = [x.strip() for x in re.split(r"(?<=[.;])\s+(?=[A-Z])", t)]
    rs = [x for x in sents if re.search(r"reverse (?:stock )?split", x, re.I)]
    # "Authorized Share Reduction" is a noun phrase in real 8-Ks; only the verb hedges
    hedge = re.compile(r"\b(to authorize|authorizing|propos\w*|may effect|discretion|ranging|range of|between|"
                       r"up to|not to exceed)\b", re.I)
    ratio = [x for x in rs if _RATIO.search(x) and not hedge.search(x)]
    effect = [x for x in rs if re.search(r"\b(became effective|was effected|effected (?:a|the)|is effective|"
                                          r"were automatically combined)\b", x, re.I) and not hedge.search(x)]
    return [ratio[0][:300], effect[0][:300]] if ratio and effect else []


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}
_DATE_RX = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                      r"\s+(\d{1,2}),\s*(\d{4})", re.I)


def _iso(m) -> str:
    return dt.date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()


def _ratio_text(sentence: str) -> str:
    """'1-for-11' / 'one-for-eleven' -> '1-for-11'."""
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty-five": 25, "twenty five": 25,
             "thirty": 30, "forty": 40, "fifty": 50}
    n = _RATIO.search(sentence).group(1).lower()
    return f"1-for-{int(n) if n.isdigit() else words.get(n, n)}"


def reverse_split_events(http, cik: int, today: dt.date, days: int = 1095) -> list[dict]:
    """Reverse splits EFFECTED in the last `days` (default 36 months), from the
    company's own 8-Ks, with effective date and ratio.

    A split needs a charter amendment, disclosed under 8-K Item 5.03. Each such
    8-K is read; it counts only if one sentence gives a single 1-for-N ratio for
    the reverse split and another says it became effective (ranges, proposals
    and board discretion excluded: a risk factor or a shareholder authorization
    is not a split). The effective date is the date in the effect sentence
    ("became effective as of 5:00 p.m. Eastern Time on May 29, 2026"), else the
    8-K filing date, labelled as such. Price feeds miss splits when a symbol's
    history restarts (ELOX relisted 2026-06-09). Raises on fetch failure so the
    caller records "unknown", never "none"."""
    since = today - dt.timedelta(days=days)
    sub = http.get_json(SUBMISSIONS_URL.format(cik=cik))
    r = sub.get("filings", {}).get("recent", {})
    out = []
    for acc, form, d, items, doc in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                                        r.get("items", []), r.get("primaryDocument", [])):
        if form not in ("8-K", "8-K/A") or "5.03" not in (items or "") or _d(d) < since or not doc:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        hits = split_sentences_effected(http.get_text(url))
        if not hits:
            continue
        ratio_sent, effect_sent = hits
        m = _DATE_RX.search(effect_sent) or _DATE_RX.search(ratio_sent)
        out.append({"effective_date": _iso(m) if m else d,
                    "date_basis": "stated effective date" if m else "8-K filing date (no effective date stated)",
                    "filing_date": d, "ratio": _ratio_text(ratio_sent), "source": f"8-K {acc}",
                    "evidence": effect_sent[:240]})
    return sorted(out, key=lambda e: e["effective_date"])


def cover_step_down(facts: dict | None, around: str | None = None, max_ratio: float = 0.67) -> str | None:
    """Corroboration from dei:EntityCommonStockSharesOutstanding: consecutive
    cover-page counts where the later is < max_ratio of the earlier. A reverse
    split shrinks the count even when an offering lands in the same quarter
    (ELOX 7,573,935 on 2026-05-01 -> 4,036,459 on 2026-08-07, x0.53). When
    `around` (a split date) is given, the step spanning it is reported.
    None if fewer than two cover counts exist."""
    rows = ((facts or {}).get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
            .get("units", {}).get("shares", []))
    pts = sorted({r["end"]: r["val"] for r in rows}.items())
    if len(pts) < 2:
        return None
    steps = [(a, b) for a, b in zip(pts, pts[1:]) if a[1] and b[1] / a[1] < max_ratio]
    if around:
        spanning = [(a, b) for a, b in steps if a[0] <= around <= b[0]]
        steps = spanning or []
        if not steps:
            return f"no cover-count step-down spanning {around}"
    if not steps:
        return "no cover-count step-down"
    a, b = steps[-1]
    return f"{a[0]} {a[1]:,.0f} -> {b[0]} {b[1]:,.0f} (x{b[1] / a[1]:.2f})"


def dilution_facts(http, cik: int, today: dt.date, lookback_days: int = 3 * 365) -> dict:
    """Counts of filings mentioning a shelf / an ATM within the lookback, and the
    age of the newest ATM mention. Counts only: a shelf-AND-ATM boolean fired on
    94% of run-one rows and was removed. runway_q vs runway_needed_q is the
    funding comparison."""
    since = today - dt.timedelta(days=lookback_days)
    n_shelf, shelf = fts_count(http, "shelf registration", cik, since)
    n_atm, atm = fts_count(http, "at-the-market", cik, since)
    latest_atm = max((h["file_date"] for h in atm), default=None)
    return {"shelf_count": n_shelf, "atm_count": n_atm,
            "latest_atm_filing": latest_atm,
            "days_since_latest_atm_filing": (today - _d(latest_atm)).days if latest_atm else None,
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


# ------------------------------------------------------------------ debt --
DEBT_TAGS = ["LongTermDebtNoncurrent", "LongTermDebtCurrent", "ConvertibleNotesPayable",
             "ConvertibleNotesPayableCurrent", "OperatingLeaseLiabilityNoncurrent"]
CONVERTIBLE_TAGS = ["ConvertibleNotesPayable", "ConvertibleNotesPayableCurrent", "ConvertibleDebtNoncurrent",
                    "ConvertibleDebtCurrent", "ConvertibleLongTermNotesPayable", "ConvertibleSubordinatedDebt"]


@dataclass
class Debt:
    total_debt_usd: float | None     # None = no debt tag at all (unknown), 0.0 = tags present, all zero
    debt_asof: str | None
    components: dict                 # tag -> value at debt_asof
    has_convertible: bool | None


def debt_facts(facts: dict, prefer_date: str | None = None) -> Debt:
    """Sum of DEBT_TAGS at one balance-sheet date (prefer the cash date, else
    the latest date any of them carries). Components are reported so a tag
    that double-counts another (a convertible inside LongTermDebt) is visible.
    has_convertible: any CONVERTIBLE_TAGS value > 0 at that date."""
    dates = {f["end"] for t in DEBT_TAGS + CONVERTIBLE_TAGS for f in _units(facts, t) if "start" not in f}
    if not dates:
        return Debt(None, None, {}, None)
    asof = prefer_date if prefer_date in dates else max(dates)
    comp = {}
    for t in DEBT_TAGS:
        v, _tag = instant_at(facts, [t], asof)
        if v is not None:
            comp[t] = v
    conv = False
    for t in CONVERTIBLE_TAGS:
        v, _tag = instant_at(facts, [t], asof)
        conv = conv or bool(v and v > 0)
    total = float(sum(comp.values())) if comp else None
    return Debt(total, asof, comp, conv)


# --------------------------------------------------- pre-funded warrants --
PREFUNDED_NAME = re.compile(r"pre-?funded|penny", re.I)
PREFUNDED_MAX_STRIKE = 0.15     # $/share; pre-funded strikes are nominal ($0.0001 to $0.01, $0.11 after a 1:11 split)
FINANCIAL_PRIMARY = ("10-Q", "10-K", "10-Q/A", "10-K/A")


@dataclass
class Prefunded:
    shares: float | None        # None = not determinable; 0.0 = classes reconciled, none pre-funded
    asof: str | None
    status: str                 # reconciled / reconciled_plus_named / named / none / not_determinable /
                                # no_instance / fetch_failed
    detail: str                 # per-class counts and strikes, or why not determinable
    source: str | None          # filing accession


def _instance_url(http, cik: int, submissions: dict) -> tuple[str | None, str | None]:
    r = submissions.get("filings", {}).get("recent", {})
    for acc, form in zip(r.get("accessionNumber", []), r.get("form", [])):
        if form in FINANCIAL_PRIMARY:
            base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
            items = http.get_json(f"{base}/index.json")["directory"]["item"]
            names = [i["name"] for i in items]
            inst = [n for n in names if n.endswith("_htm.xml")] or \
                   [n for n in names if n.endswith(".xml") and not re.search(r"(_cal|_def|_lab|_pre|FilingSummary)", n)]
            return (f"{base}/{inst[0]}" if inst else None), acc
    return None, None


def prefunded_from_instance(xml_text: str) -> Prefunded:
    """Pre-funded warrant shares at the filing's balance-sheet date
    (dei:DocumentPeriodEndDate), from one XBRL instance.

    A class is pre-funded if its member name says pre-funded / penny, or, on a
    class axis that reconciles to the total, its strike is <= PREFUNDED_MAX_STRIKE.
      1. If the single-member ClassOfWarrantOrRightOutstanding facts on one
         axis (or on all axes together) sum to the non-dimensional total, that
         is the class breakdown. Facts elsewhere are events or duplicates
         (ELOX 10-Q 2026-06-30: an exercise of 600,000 and the June 3,025,000
         also sit on PlanNameAxis).
      2. Pre-funded classes found there -> status reconciled.
      3. Pre-funded-NAMED classes at the balance date that are not in the
         reconciled breakdown are reported outside the total (IRD: total 19.6M
         excludes 16.0M pre-funded) and are added -> named / reconciled_plus_named.
      4. Pre-funded named only at other dates (an issuance event) -> None,
         not_determinable. No pre-funded class anywhere -> 0, none.
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as e:
        return Prefunded(None, None, "fetch_failed", f"instance parse error: {e}", None)
    local = lambda t: t.split("}")[-1]  # noqa: E731
    short = lambda m: m.split(":")[-1]  # noqa: E731
    ctx, period_end = {}, None
    for c in root.iter():
        if local(c.tag) == "context":
            mem = tuple((m.get("dimension"), (m.text or "").strip()) for m in c.iter()
                        if local(m.tag) == "explicitMember")
            when = [e.text for e in c.iter() if local(e.tag) in ("instant", "endDate")]
            ctx[c.get("id")] = (mem, when[-1].strip() if when else None)
        elif local(c.tag) == "DocumentPeriodEndDate" and c.text:
            period_end = c.text.strip()
    outst, strike = [], {}
    for e in root:
        t = local(e.tag)
        if t not in ("ClassOfWarrantOrRightOutstanding", "ClassOfWarrantOrRightExercisePriceOfWarrantsOrRights1"):
            continue
        mem, when = ctx.get(e.get("contextRef"), ((), None))
        try:
            v = float(e.text)
        except (TypeError, ValueError):
            continue
        if t == "ClassOfWarrantOrRightOutstanding":
            outst.append((mem, when, v))
        elif len(mem) == 1:
            strike.setdefault(mem[0], v)
    if not outst:
        return Prefunded(0.0, period_end, "none", "no ClassOfWarrantOrRightOutstanding in the filing", None)
    d = period_end or max(w for _, w, _ in outst if w)
    named_any = [(m, w, v) for m, w, v in outst if any(PREFUNDED_NAME.search(x) for _, x in m)]
    total = next((v for m, w, v in outst if not m and w == d), None)
    single = [(m[0], v) for m, w, v in outst if w == d and len(m) == 1]

    def is_pf(member, axis_name, by_strike):
        return bool(PREFUNDED_NAME.search(member)) or \
            (by_strike and strike.get((axis_name, member), 99) <= PREFUNDED_MAX_STRIKE)

    recon = None
    if total:
        groups = {}
        for (axis, mem), v in single:
            groups.setdefault(axis, {})[mem] = v
        candidates = list(groups.items()) + [("(all axes)", {m: v for g in groups.values() for m, v in g.items()})]
        for axis, members in candidates:
            if abs(sum(members.values()) - total) <= 0.005 * total:
                recon = (axis, members)
                break
    parts, status = [], None
    if recon:
        axis, members = recon
        pf = {m: v for m, v in members.items()
              if is_pf(m, axis, True) or (axis == "(all axes)" and PREFUNDED_NAME.search(m))}
        parts += [(m, v) for m, v in pf.items()]
        status = "reconciled" if pf else None
        in_recon = set(members)
    else:
        in_recon = set()
    # pre-funded-named classes at the balance date outside the reconciled breakdown
    outside = {}
    for (axis, mem), v in single:
        if PREFUNDED_NAME.search(mem) and mem not in in_recon and not (recon and status == "reconciled"):
            outside.setdefault(mem, v)          # same member on two axes counts once
    if outside:
        parts += list(outside.items())
        status = "reconciled_plus_named" if status == "reconciled" else "named"
    detail_tot = f"total {total:,.0f}" if total else "no total"
    if parts:
        n = float(sum(v for _, v in parts))
        det = f"{detail_tot} at {d}; pre-funded: " + "; ".join(
            f"{short(m)}={v:,.0f}@{next((s_ for (a, mm), s_ in strike.items() if mm == m), 'n/a')}" for m, v in parts)
        return Prefunded(n, d, status, det, None)
    if named_any:
        dates = sorted({w for _, w, _ in named_any if w})
        return Prefunded(None, d, "not_determinable",
                         f"pre-funded warrants tagged only at {', '.join(dates)}, not at the balance date {d}", None)
    return Prefunded(0.0, d, "none", f"{detail_tot} at {d}; no class named pre-funded"
                     + (f"; reconciled on {short(recon[0])}" if recon else ""), None)


def prefunded_warrants(http, cik: int, submissions: dict | None) -> Prefunded:
    if not submissions:
        return Prefunded(None, None, "fetch_failed", "no submissions record", None)
    try:
        url, acc = _instance_url(http, cik, submissions)
        if not url:
            return Prefunded(None, None, "no_instance",
                             "no 10-Q/10-K XBRL instance (20-F/40-F filer, or no periodic report yet)", acc)
        p = prefunded_from_instance(http.get_text(url))
    except Exception as e:  # noqa: BLE001
        return Prefunded(None, None, "fetch_failed", f"{e}", None)
    p.source = acc
    return p


# ------------------------------------------ pre-funded warrants: text path --
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE = rf"{_MONTH}\s+\d{{1,2}},\s*\d{{4}}"
_NUM = r"\d{1,3}(?:,\d{3})+|\d{4,}"
_PF = r"pre-?\s?funded"
# (kind, regex, verified?)  every pattern states a CURRENT BALANCE, not an issuance
_TEXT_PATTERNS = [
    # cover page: "This number does not include 37,814,754 shares of common stock issuable upon the
    # exercise of pre-funded warrants outstanding as of August 6, 2026" (PALI 10-Q)
    ("cover_note", re.compile(rf"does not include\s+({_NUM})\s+shares[^.]{{0,160}}?{_PF}\s+(?:common stock\s+)?"
                              rf"warrants?\s+outstanding\s+as of\s+({_DATE})", re.I), True),
    # "As of June 30, 2026, 37,814,754 of the October 2025 Offering Pre-funded Common Stock Warrants
    # remained unexercised" (PALI 10-Q)
    ("as_of_balance", re.compile(rf"As of\s+({_DATE})\s*,?\s*(?:there were\s+)?({_NUM})\b[^.]{{0,120}}?{_PF}"
                                 rf"[^.]{{0,60}}?warrants?[^.]{{0,40}}?\b(?:remained|were|are|was)\s+"
                                 rf"(?:issued and\s+)?(?:outstanding|unexercised)", re.I), True),
    # "pre-funded warrants to purchase 3,000,031 shares ... remained outstanding as of June 30, 2026"
    ("balance_as_of", re.compile(rf"{_PF}\s+warrants?\s+(?:to purchase\s+)?(?:an aggregate of\s+)?(?:up to\s+)?"
                                 rf"({_NUM})\s+shares[^.]{{0,80}}?\b(?:remained|were|are)\s+(?:outstanding|unexercised)"
                                 rf"\s+as of\s+({_DATE})", re.I), True),
    # reserved-shares / outstanding-securities table row, current column first:
    # "Pre-funded warrants to purchase common stock — 7,060,000" (ACRV 10-Q: 0 now, 7,060,000 prior)
    ("table_row", re.compile(rf"{_PF}\s+warrants?(?:\s+to purchase(?: shares of)? common stock)?(?:\s+outstanding)?"
                             rf"\s+(—|-|{_NUM})\s+(?:—|-|{_NUM})(?!\s*\$)", re.I), True),
    # EPS denominator: "weighted average of common stock to be issued upon exercise of pre-funded
    # warrants 3,000,031" (NKTX 10-Q). An AVERAGE for the period, not a balance -> unverified
    ("eps_weighted", re.compile(rf"weighted[- ]average[^.]{{0,90}}?{_PF}\s+warrants?\s+({_NUM})", re.I), False),
]


def prefunded_from_text(text: str) -> dict:
    """Outstanding pre-funded warrants stated in a 10-Q/10-K's own words.

    Only sentences that state a CURRENT balance count; issuance and exercise
    sentences are ignored (ACRV issued 7,060,000 and its table shows 0 today).
    Returns {count, kind, verified, evidence, mentions}; count None when the
    filing mentions pre-funded warrants but states no balance."""
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or "")))
    mentions = len(re.findall(rf"{_PF}\s+(?:common stock\s+)?warrants?", t, re.I))
    for kind, rx, verified in _TEXT_PATTERNS:
        m = next((m for m in rx.finditer(t)
                  # a "table row" inside an EPS denominator is a period average, not a balance
                  if not (kind == "table_row" and re.search(r"weighted[- ]average", t[max(0, m.start() - 160):m.start()],
                                                            re.I))), None)
        if not m:
            continue
        raw = m.group(2) if kind == "as_of_balance" else m.group(1)
        count = 0.0 if raw in ("—", "-") else float(raw.replace(",", ""))
        return {"count": count, "kind": kind, "verified": verified, "mentions": mentions,
                "evidence": t[max(0, m.start() - 40):m.end() + 40].strip()[:400]}
    return {"count": None, "kind": None, "verified": False, "mentions": mentions, "evidence": None}


@dataclass
class ShareCount:
    pre_funded: float | None
    source: str                # human-readable: where each number came from
    confidence: str            # verified / unverified
    detail: str


def share_count(http, cik: int, submissions: dict | None, xbrl: "Prefunded") -> ShareCount:
    """Combine the XBRL class breakdown with the filing text.

    verified   XBRL pre-funded classes at the balance date; or a text sentence
               stating the current balance; or no pre-funded class in XBRL AND
               no mention of pre-funded warrants anywhere in the filing text.
    unverified everything else: the text mentions pre-funded warrants but states
               no balance (VTVT 10-Q: "included in the weighted average number of
               shares outstanding", no count), an EPS weighted average, or no
               instance/text could be read. Count None unless a number was stated."""
    txt = None
    if submissions:
        r = submissions.get("filings", {}).get("recent", {})
        for acc, form, doc in zip(r.get("accessionNumber", []), r.get("form", []), r.get("primaryDocument", [])):
            if form in FINANCIAL_PRIMARY and doc:
                try:
                    txt = prefunded_from_text(http.get_text(
                        f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"))
                    txt["filing"] = f"{form} {acc}"
                except Exception as e:  # noqa: BLE001
                    txt = {"count": None, "kind": None, "verified": False, "mentions": None,
                           "evidence": f"text fetch failed: {e}", "filing": f"{form} {acc}"}
                break
    tx = txt or {"count": None, "kind": None, "verified": False, "mentions": None, "evidence": None, "filing": None}
    if xbrl.status in ("reconciled", "named", "reconciled_plus_named"):
        note = ""
        if tx["count"] is not None and tx["verified"] and xbrl.shares and \
                abs(tx["count"] - xbrl.shares) > 0.01 * xbrl.shares:
            note = f"; TEXT DISAGREES: {tx['kind']} states {tx['count']:,.0f}"
        return ShareCount(xbrl.shares, f"XBRL warrant classes at {xbrl.asof} ({xbrl.status})", "verified",
                          xbrl.detail + note)
    if tx["count"] is not None and tx["verified"]:
        return ShareCount(tx["count"], f"filing text ({tx['kind']}, {tx['filing']})", "verified", tx["evidence"])
    if tx["count"] is not None:
        return ShareCount(tx["count"], f"filing text ({tx['kind']}: a period average, not a balance; {tx['filing']})",
                          "unverified", tx["evidence"])
    if xbrl.status == "none" and tx["mentions"] == 0:
        return ShareCount(0.0, f"no pre-funded class in XBRL and no mention in {tx['filing']}", "verified",
                          xbrl.detail)
    why = (f"{tx['mentions']} mentions of pre-funded warrants in {tx['filing']} but no stated balance"
           if tx["mentions"] else f"XBRL {xbrl.status}; filing text not readable")
    return ShareCount(None, why, "unverified", f"{xbrl.status}: {xbrl.detail}")


# ------------------------------------------------- stated runway cross-check --
_Q = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1": 1, "2": 2, "3": 3, "4": 4}
_RUNWAY_RX = re.compile(
    r"[^.]{0,260}?\b(?:fund|finance|support|meet)\b[^.]{0,160}?\b(?:operations?|operating|expenses|plan|"
    r"requirements|needs|runway)\b[^.]{0,160}?\b(at least\s+)?(into|through|until|to|beyond)\s+(?:at least\s+)?"
    r"(?:the\s+)?(?P<period>(?:(?:first|second|third|fourth)\s+(?:quarter|half)\s+of\s+(?:calendar\s+|fiscal\s+)?)?"
    r"(?:Q[1-4]\s+|[12]H\s+|mid-?\s?|early\s+|late\s+|the\s+end\s+of\s+|end\s+of\s+|"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+)?20[2-4]\d)",
    re.I)
_TWELVE = re.compile(r"(?:sufficient|enough|adequate)[^.]{0,200}?(?:at least\s+)?(?:the\s+)?next\s+(?:twelve|12)\s+"
                     r"months", re.I)


def _period_window(period: str, prep: str, at_least: bool) -> tuple[dt.date, dt.date | None]:
    p = period.lower()
    y = int(re.search(r"(20\d\d)", p).group(1))
    m = re.search(r"(first|second|third|fourth)\s+quarter|q([1-4])", p)
    h_ = re.search(r"(first|second)\s+half|([12])h\b", p)
    mon = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)", p)
    if m:
        q = _Q[(m.group(1) or m.group(2))]
        lo, hi = dt.date(y, 3 * q - 2, 1), dt.date(y, 3 * q, 28)
    elif h_:
        half = _Q[(h_.group(1) or h_.group(2))]
        lo, hi = (dt.date(y, 1, 1), dt.date(y, 6, 30)) if half == 1 else (dt.date(y, 7, 1), dt.date(y, 12, 31))
    elif "mid" in p:
        lo, hi = dt.date(y, 4, 1), dt.date(y, 9, 30)
    elif "early" in p:
        lo, hi = dt.date(y, 1, 1), dt.date(y, 4, 30)
    elif "late" in p:
        lo, hi = dt.date(y, 9, 1), dt.date(y, 12, 31)
    elif "end of" in p:
        lo, hi = dt.date(y, 10, 1), dt.date(y, 12, 31)
    elif mon:
        mm = _MONTHS[mon.group(1)]
        lo, hi = dt.date(y, mm, 1), dt.date(y, mm, 28)
    else:
        lo, hi = dt.date(y, 1, 1), dt.date(y, 12, 31)
    if prep.lower() in ("through", "until", "to"):
        lo, hi = hi, hi + dt.timedelta(days=90)     # funded to the END of the period
    if at_least or prep.lower() == "beyond":
        hi = None                                    # a floor only
    return lo, hi


def stated_runway(text: str, filed: dt.date | None = None) -> dict | None:
    """The company's own runway statement: {sentence, lo, hi} where [lo, hi] is
    the date window it implies ('into the third quarter of 2027' -> Jul 1 to
    Sep 28 2027; 'through November 2027' -> end of November + 90 days; 'at least'
    -> no upper bound). A dated statement wins over 'the next twelve months'."""
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or "")))
    for m in _RUNWAY_RX.finditer(t):
        s = m.group(0)
        if re.search(r"remediat|material weakness|internal control", s, re.I):
            continue
        lo, hi = _period_window(m.group("period"), m.group(2), bool(m.group(1)))
        return {"sentence": s.strip()[-300:], "lo": lo, "hi": hi}
    m = _TWELVE.search(t)
    if m and filed:
        return {"sentence": m.group(0).strip()[-300:], "lo": filed + dt.timedelta(days=365), "hi": None}
    return None


def runway_statement(http, cik: int, submissions: dict | None) -> dict | None:
    """From the latest 10-Q/10-K and the latest earnings release (8-K Item 2.02,
    exhibit 99.x): the most specific dated statement, with its source URL."""
    if not submissions:
        return None
    r = submissions.get("filings", {}).get("recent", {})
    rows = list(zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                    r.get("items", []), r.get("primaryDocument", [])))
    found = []
    q = next((x for x in rows if x[1] in FINANCIAL_PRIMARY and x[4]), None)
    if q:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{q[0].replace('-', '')}/{q[4]}"
        s = stated_runway(http.get_text(url), dt.date.fromisoformat(q[2]))
        if s:
            found.append({**s, "source": f"{q[1]} {q[2]}", "url": url})
    e = next((x for x in rows if x[1] == "8-K" and "2.02" in (x[3] or "")), None)
    if e:
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{e[0].replace('-', '')}"
        try:
            ex = next((i["name"] for i in http.get_json(f"{base}/index.json")["directory"]["item"]
                       if re.search(r"ex-?99|dex99", i["name"], re.I) and i["name"].endswith(".htm")), None)
            if ex:
                s = stated_runway(http.get_text(f"{base}/{ex}"), dt.date.fromisoformat(e[2]))
                if s:
                    found.append({**s, "source": f"8-K 2.02 press release {e[2]}", "url": f"{base}/{ex}"})
        except Exception:  # noqa: BLE001
            pass
    dated = [f for f in found if f["hi"] is not None or "twelve" not in f["sentence"].lower()]
    return (dated or found or [None])[0]


def runway_crosscheck(runway_q: float | None, cash_asof: str | None, stmt: dict | None) -> dict:
    """computed runway end = cash_asof + runway_q x 91.25 days, against the
    statement's window. discrepancy in months: 0 inside the window, negative
    if the computed end falls before it, positive after it."""
    out = {"stated_runway": stmt["sentence"] if stmt else None,
           "stated_runway_window": None, "stated_runway_source": stmt.get("url") if stmt else None,
           "computed_runway_end": None, "runway_discrepancy_months": None}
    if not stmt:
        return out
    out["stated_runway_window"] = f"{stmt['lo']} to {stmt['hi'] or 'open'}"
    if runway_q is None or not cash_asof:
        return out
    end = dt.date.fromisoformat(cash_asof) + dt.timedelta(days=runway_q * 91.25)
    out["computed_runway_end"] = end.isoformat()
    if end < stmt["lo"]:
        out["runway_discrepancy_months"] = round((end - stmt["lo"]).days / 30.4375, 1)
    elif stmt["hi"] and end > stmt["hi"]:
        out["runway_discrepancy_months"] = round((end - stmt["hi"]).days / 30.4375, 1)
    else:
        out["runway_discrepancy_months"] = 0.0
    return out
