"""LIVE end-to-end check on ELOX (Eloxx Pharmaceuticals, CIK 1035354).

Runs the same company_info() path the screen uses, then checks the screen's
own outputs. Run AFTER `python -m biocat screen --out biocat_out`:
    BIOCAT_UA="Name email" python3 tests/live_elox.py
Expected (verified against the filings 2026-10-13):
  pre_funded_warrants 7,383,919     10-Q 2026-06-30 XBRL warrant classes; S-1 2026-09-11
  true_market_cap     ~$159M        (4,036,459 + 7,383,919) x price, not ~$55M
  reverse split       2026-05-29, 1-for-11   8-K 2026-06-01, Item 5.03
  row EXCLUDED from candidates.csv by the 24-month reverse-split rule
"""
import csv
import datetime as dt
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from biocat import market as M  # noqa: E402
from biocat.cli import company_info  # noqa: E402
from biocat.net import Http  # noqa: E402

OUT = os.path.join(HERE, "..", "biocat_out")

if __name__ == "__main__":
    today = dt.date.today()
    http = Http(cache_dir=os.path.join(OUT, ".cache"), ttl_hours=24)
    info = company_info(http, 1035354, "ELOX", today)
    price, sc, split = info["mkt"].price, info["sc"], info["split"]
    print(f"price {price}  common {info['shares']:,.0f}  pre-funded {sc.pre_funded:,.0f} ({sc.source})")
    print(f"reported {info['mcap_reported'] / 1e6:.1f}M  true {info['mcap'] / 1e6:.1f}M  "
          f"confidence {info['confidence']}")
    print(f"split {split['reverse_split_date']} {split['reverse_split_ratio']} "
          f"{split['months_since_reverse_split']} months ago; {split['reverse_split_source']}; "
          f"cover check: {split['reverse_split_cover_check']}")
    keep, reasons = M.size_filter(info["mcap"], info["mkt"].avg_dollar_vol, info["splits_24m"])
    print("size filter:", "KEEP" if keep else "DROP", reasons)

    assert sc.pre_funded == 7_383_919, sc
    assert info["confidence"] == "verified"
    ratio = info["mcap"] / info["mcap_reported"]
    assert abs(ratio - 11_420_378 / 4_036_459) < 0.01, ratio          # 2.83x, price-independent
    assert 120e6 < info["mcap"] < 220e6, info["mcap"]                 # ~$159M at $13.89; moves with price
    assert split["reverse_split_date"] == "2026-05-29" and split["reverse_split_ratio"] == "1-for-11", split
    assert not keep and any("reverse split" in x for x in reasons), reasons

    cand = os.path.join(OUT, "candidates.csv")
    if os.path.exists(cand):
        rows = list(csv.DictReader(open(cand)))
        assert not [r for r in rows if r["ticker"] == "ELOX"], "ELOX is still in candidates.csv"
        dropped = [r for r in csv.DictReader(open(os.path.join(OUT, "dropped.csv"))) if r["ticker"] == "ELOX"]
        assert dropped and dropped[0]["reverse_split_date"] == "2026-05-29", dropped
        assert dropped[0]["reverse_split_ratio"] == "1-for-11" and dropped[0]["pre_funded_warrants"] == "7383919.0"
        print(f"outputs: ELOX absent from candidates.csv ({len(rows)} rows); in dropped.csv with "
              f"true_market_cap={dropped[0]['true_market_cap']}: {dropped[0]['_filter_notes']}")
    print("ok live ELOX")
