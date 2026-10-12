"""Stage 2: sponsor name -> (ticker, CIK). Strict: ambiguity is a rejection.

Fast path   company_tickers.json, indexed by normalised name. Treated as a CACHE,
            not a universe: active filers with tickers are missing from it.
Fallback    EDGAR full-text search for the exact sponsor phrase; candidates come
            from `_source.display_names` ("Name  (TICK)  (CIK 0001234567)").
            Hits include 13G/13D filers that merely MENTION the company
            (BlackRock, FMR), so only display names whose normalised name equals
            the sponsor's normalised name are considered.

Accept only when exactly one CIK survives and it has a ticker. Otherwise record
why it was rejected; nothing is guessed.

SIC gate    An independent second check on every accepted match: the filer's SIC
            code (data.sec.gov/submissions) must be a healthcare code. Stripping
            descriptors makes "Thryv Therapeutics" and "Thryv Holdings" (SaaS)
            share the key "thryv"; the name alone cannot tell them apart. Failure
            to fetch the SIC is a rejection ("sic_unverified"), not a pass.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .names import normalise, parse_display_name

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# drugs, biologics, diagnostics, medical devices, drug/device wholesale,
# health services, commercial biological research
HEALTHCARE_SIC = ({2833, 2834, 2835, 2836, 3826, 3841, 3842, 3843, 3844, 3845, 3851,
                   5047, 5122, 8731, 8733, 8734} | set(range(8000, 8100)))


@dataclass
class Resolution:
    sponsor: str
    key: str
    status: str                 # resolved / ambiguous / no_match / no_ticker / empty_key / skipped_budget
                                # / sic_outside_healthcare / sic_unverified
    cik: int | None = None
    ticker: str | None = None
    sec_name: str | None = None
    path: str | None = None     # tickers_json / fts
    detail: str = ""
    sic: int | None = None
    sic_desc: str | None = None


class TickerIndex:
    def __init__(self, rows: list[dict]):
        self.by_key: dict[str, set[int]] = defaultdict(set)
        self.by_cik: dict[int, dict] = {}
        for r in rows:
            cik = int(r["cik_str"])
            self.by_key[normalise(r["title"])].add(cik)
            self.by_cik.setdefault(cik, {"title": r["title"], "tickers": []})["tickers"].append(r["ticker"])

    @classmethod
    def load(cls, http) -> "TickerIndex":
        d = http.get_json(TICKERS_URL)
        return cls(list(d.values()))


def resolve_fast(sponsor: str, idx: TickerIndex) -> Resolution:
    key = normalise(sponsor)
    if not key:
        return Resolution(sponsor, key, "empty_key")
    ciks = idx.by_key.get(key, set())
    if len(ciks) == 1:
        cik = next(iter(ciks))
        info = idx.by_cik[cik]
        return Resolution(sponsor, key, "resolved", cik, info["tickers"][0], info["title"],
                          "tickers_json", detail=f"tickers={info['tickers']}")
    if len(ciks) > 1:
        return Resolution(sponsor, key, "ambiguous", path="tickers_json",
                          detail=f"{len(ciks)} CIKs share key '{key}': {sorted(ciks)}")
    return Resolution(sponsor, key, "no_match", path="tickers_json")


def candidates_from_fts(sponsor_key: str, hits: list[dict]) -> dict[int, dict]:
    """CIK -> {name, tickers} for display names that normalise to the sponsor key."""
    out: dict[int, dict] = {}
    for h in hits:
        for dn in h.get("_source", {}).get("display_names", []) or []:
            p = parse_display_name(dn)
            if p and normalise(p["name"]) == sponsor_key:
                cur = out.setdefault(p["cik"], {"name": p["name"], "tickers": set()})
                cur["tickers"].update(p["tickers"])
    return out


def resolve_fts(sponsor: str, http) -> Resolution:
    key = normalise(sponsor)
    if not key:
        return Resolution(sponsor, key, "empty_key")
    d = http.get_json(FTS_URL, {"q": f'"{sponsor}"'})
    cands = candidates_from_fts(key, d.get("hits", {}).get("hits", []))
    if not cands:
        return Resolution(sponsor, key, "no_match", path="fts")
    if len(cands) > 1:
        return Resolution(sponsor, key, "ambiguous", path="fts",
                          detail="; ".join(f"{c['name']} CIK {k}" for k, c in cands.items()))
    cik, c = next(iter(cands.items()))
    tickers = sorted(c["tickers"])
    if not tickers:
        return Resolution(sponsor, key, "no_ticker", cik, None, c["name"], "fts",
                          detail="SEC filer with no listed ticker")
    if len(tickers) > 1:
        return Resolution(sponsor, key, "ambiguous", cik, None, c["name"], "fts",
                          detail=f"multiple tickers {tickers}")
    return Resolution(sponsor, key, "resolved", cik, tickers[0], c["name"], "fts")


def resolve_all(sponsors_by_count: list[tuple[str, int]], http, idx: TickerIndex,
                max_fts: int, log=print) -> list[Resolution]:
    """sponsors_by_count must already be sorted FEWEST trials first."""
    out, fts_used = [], 0
    for sponsor, _n in sponsors_by_count:
        r = resolve_fast(sponsor, idx)
        if r.status == "no_match":
            if fts_used >= max_fts:
                r = Resolution(sponsor, r.key, "skipped_budget",
                               detail=f"FTS budget of {max_fts} lookups used")
            else:
                fts_used += 1
                try:
                    r = resolve_fts(sponsor, http)
                except Exception as e:  # a lookup failure is a miss, never a guess
                    r = Resolution(sponsor, r.key, "no_match", path="fts", detail=f"error: {e}")
        out.append(r)
    n_ok = sum(r.status == "resolved" for r in out)
    skipped = sum(r.status == "skipped_budget" for r in out)
    log(f"stage 2: {n_ok}/{len(out)} sponsors resolved ({100 * n_ok / max(len(out), 1):.0f}%), "
        f"{fts_used} FTS lookups" + (f", {skipped} NOT LOOKED UP (budget)" if skipped else ""))
    return out


def sic_gate(res: list[Resolution], http, log=print) -> list[Resolution]:
    """Demote resolved matches whose SEC filer is not a healthcare SIC. In place."""
    bad = unver = 0
    for r in res:
        if r.status != "resolved":
            continue
        try:
            sub = http.get_json(SUBMISSIONS_URL.format(cik=r.cik))
            r.sic = int(sub.get("sic") or 0) or None
            r.sic_desc = sub.get("sicDescription")
        except Exception as e:  # noqa: BLE001  unverified is a rejection
            r.status, r.detail = "sic_unverified", f"submissions fetch failed: {e}"
            unver += 1
            continue
        if r.sic not in HEALTHCARE_SIC:
            r.status = "sic_outside_healthcare"
            r.detail = f"SIC {r.sic} {r.sic_desc}; {r.detail}".rstrip("; ")
            bad += 1
    n_ok = sum(r.status == "resolved" for r in res)
    log(f"stage 2 SIC gate: {bad} rejected outside healthcare SIC, {unver} unverified; "
        f"{n_ok}/{len(res)} sponsors resolved ({100 * n_ok / max(len(res), 1):.0f}%)")
    return res
