"""Step 2: which sponsors were SEC-reporting companies during the price window, dead ones included.

Source: EDGAR quarterly form indexes (every filing, every filer, by quarter). A company
that filed a 10-K / 10-Q / 20-F / 40-F in 2018Q2-2025Q4 was public then, whatever happened
to it later. Sponsor names join to filer names by EXACT normalised equality (biocat.names),
never fuzzy. Output: regsig_out/listed_sponsors.csv, one row per (sponsor, CIK) match.

    BIOCAT_UA="name email" python -m regsig.listed
"""
import csv
import gzip
import io
import os

from biocat.names import normalise
from biocat.net import Http

OUT = os.path.join(os.path.dirname(__file__), "..", "regsig_out")
FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A", "20-F/A", "10-KT"}
QUARTERS = [(y, q) for y in range(2018, 2026) for q in (1, 2, 3, 4) if (y, q) >= (2018, 2)]


def parse_form_idx(text: str):
    """company.idx is fixed-width, form.idx too; master.idx is pipe-delimited: CIK|Name|Form|Date|File."""
    for line in text.splitlines():
        p = line.split("|")
        if len(p) == 5 and p[0].isdigit():
            yield int(p[0]), p[1].strip(), p[2].strip(), p[3].strip()


def main():
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24 * 365)
    filers = {}                                   # cik -> {names, first, last, forms}
    for y, q in QUARTERS:
        url = f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/master.idx"
        text = http.get_text(url)
        n = 0
        for cik, name, form, date in parse_form_idx(text):
            if form not in FORMS:
                continue
            f = filers.setdefault(cik, {"names": set(), "first": date, "last": date, "forms": set()})
            f["names"].add(name)
            f["first"], f["last"] = min(f["first"], date), max(f["last"], date)
            f["forms"].add(form.split("/")[0])
            n += 1
        print(f"{y}Q{q}: {n} periodic filings; {len(filers)} filers so far", flush=True)
    by_key = {}
    for cik, f in filers.items():
        for nm in f["names"]:
            k = normalise(nm)
            if k:
                by_key.setdefault(k, set()).add(cik)
    sponsors = list(csv.DictReader(open(os.path.join(OUT, "sponsors.csv"))))
    rows, matched_trials, ambiguous = [], 0, 0
    for s in sponsors:
        ciks = by_key.get(normalise(s["sponsor"]), set())
        if len(ciks) > 1:
            ambiguous += 1
        for cik in sorted(ciks):
            f = filers[cik]
            rows.append({"sponsor": s["sponsor"], "n_trials": s["n_trials"], "cik": cik,
                         "sec_names": " | ".join(sorted(f["names"])), "first_periodic": f["first"],
                         "last_periodic": f["last"], "forms": "|".join(sorted(f["forms"])),
                         "n_cik_matches": len(ciks)})
        if ciks:
            matched_trials += int(s["n_trials"])
    with open(os.path.join(OUT, "listed_sponsors.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n_sp = len({r["sponsor"] for r in rows})
    print(f"{len(filers)} periodic filers 2018Q2-2025Q4; {n_sp} of {len(sponsors)} sponsors matched "
          f"({ambiguous} to more than one CIK); their trials: {matched_trials}")


if __name__ == "__main__":
    main()
