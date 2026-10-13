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
from dataclasses import dataclass

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue", "Cash",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
STI_TAGS = ["ShortTermInvestments", "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
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


def compute_runway(facts: dict, months_out: float | None, today: dt.date,
                   n_quarters: int = 2, stale_days: int = 270) -> Runway:
    cash_f, cash_tag = cash_instant(facts)
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
    sents = [x.strip() for x in re.split(r"(?<=[.;])\s+(?=[A-Z])", t)]
    rs = [x for x in sents if re.search(r"reverse (?:stock )?split", x, re.I)]
    # "Authorized Share Reduction" is a noun phrase in real 8-Ks; only the verb hedges
    hedge = re.compile(r"\b(to authorize|authorizing|propos\w*|may effect|discretion|ranging|range of|between|"
                       r"up to|not to exceed)\b", re.I)
    ratio = [x for x in rs if _RATIO.search(x) and not hedge.search(x)]
    effect = [x for x in rs if re.search(r"\b(became effective|was effected|effected (?:a|the)|is effective|"
                                          r"were automatically combined)\b", x, re.I) and not hedge.search(x)]
    return [ratio[0][:300], effect[0][:300]] if ratio and effect else []


def sec_reverse_splits(http, cik: int, today: dt.date, days: int = 730) -> list[str]:
    """Reverse splits EFFECTED in the window, from the company's own 8-Ks.

    A split needs a charter amendment, disclosed under 8-K Item 5.03. Each such
    8-K in the window is read; it counts only if one sentence names the reverse
    split, a 1-for-N ratio and effect language (see split_sentences_effected).
    Price feeds miss splits when a symbol's history restarts (ELOX: OTC history
    dropped at its 2026-06 Nasdaq listing, 1-for-11 split 2026-05-29 not in the
    feed). Raises on fetch failure so the caller records "unknown", never "none".
    """
    since = today - dt.timedelta(days=days)
    sub = http.get_json(SUBMISSIONS_URL.format(cik=cik))
    r = sub.get("filings", {}).get("recent", {})
    found = []
    for acc, form, d, items, doc in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                                        r.get("items", []), r.get("primaryDocument", [])):
        if form not in ("8-K", "8-K/A") or "5.03" not in (items or "") or _d(d) < since or not doc:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        hits = split_sentences_effected(http.get_text(url))
        if hits:
            m = _RATIO.search(hits[0])
            found.append(f"{d} 8-K {m.group(0)}")
    return sorted(found)


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
