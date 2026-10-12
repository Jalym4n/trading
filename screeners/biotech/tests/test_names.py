"""Name normaliser and strict identity matching, on REAL sponsor names taken
from the ClinicalTrials.gov Stage 1 pull (2026-10-12) and real SEC strings."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.identity import TickerIndex, candidates_from_fts, resolve_fast  # noqa: E402
from biocat.names import normalise, parse_display_name  # noqa: E402

# (ClinicalTrials.gov sponsor as registered, expected key)
REAL_SPONSORS = [
    ("Vera Therapeutics, Inc.", "vera"),
    ("Verrica Pharmaceuticals Inc.", "verrica"),
    ("Sarepta Therapeutics, Inc.", "sarepta"),
    ("Xenon Pharmaceuticals Inc.", "xenon"),
    ("Vedanta Biosciences, Inc.", "vedanta"),
    ("Tango Therapeutics, Inc.", "tango"),
    ("Coherus Oncology, Inc.", "coherus oncology"),
    ("ImmunityBio, Inc.", "immunitybio"),
    ("Alkermes, Inc.", "alkermes"),
    ("Curis, Inc.", "curis"),
    ("Climb Bio, Inc.", "climb bio"),
    ("Cellectis S.A.", "cellectis"),
    ("TILT Biotherapeutics Ltd.", "tilt biotherapeutics"),
    ("Riboscience, LLC.", "riboscience"),
    ("Janssen Research & Development, LLC", "janssen research and development"),
    ("Merck Sharp & Dohme LLC", "merck sharp and dohme"),
    ("Otsuka Pharmaceutical Development & Commercialization, Inc.",
     "otsuka pharmaceutical development and commercialization"),
    ("Shin Poong Pharmaceutical Co. Ltd.", "shin poong"),
    ("Qilu Pharmaceutical Co., Ltd.", "qilu"),
    ("CSPC Megalith Biopharmaceutical Co.,Ltd.", "cspc megalith biopharmaceutical"),
    ("Innovent Biologics (Suzhou) Co. Ltd.", "innovent biologics"),
    ("Genrix (Shanghai) Biopharmaceutical Co., Ltd.", "genrix biopharmaceutical"),
    ("Brainfarma Industria Química e Farmacêutica S/A", "brainfarma industria quimica e farmaceutica"),
    ("Gan & Lee Pharmaceuticals.", "gan and lee"),
    ("Medipost Co Ltd.", "medipost"),
    ("Hoffmann-La Roche", "hoffmann la roche"),
    ("Eli Lilly and Company", "eli lilly"),
]


def test_real_sponsor_names():
    bad = [(raw, normalise(raw), want) for raw, want in REAL_SPONSORS if normalise(raw) != want]
    assert not bad, bad


def test_ctgov_and_sec_spellings_meet():
    pairs = [("Vera Therapeutics, Inc.", "VERA THERAPEUTICS, INC."),
             ("Apellis Pharmaceuticals, Inc.", "Apellis Pharmaceuticals, Inc."),
             ("Sarepta Therapeutics, Inc.", "SAREPTA THERAPEUTICS, INC."),
             ("Xenon Pharmaceuticals Inc.", "XENON PHARMACEUTICALS INC."),
             ("ImmunityBio, Inc.", "ImmunityBio, Inc.")]
    for a, b in pairs:
        assert normalise(a) == normalise(b), (a, b)


def test_distinct_companies_stay_distinct():
    # stripping descriptors must not merge different firms
    for a, b in [("Arcus Biosciences, Inc.", "Arcutis Biotherapeutics, Inc."),
                 ("Vera Therapeutics, Inc.", "Vera Bradley, Inc."),
                 ("Coherus Oncology, Inc.", "Coherus BioSciences, Inc."),
                 ("Vedanta Biosciences, Inc.", "Vedanta Resources Ltd")]:
        assert normalise(a) != normalise(b), (a, b)


def test_suffixes_only_stripped_from_the_end():
    assert normalise("Labstyle Innovations Ltd") == "labstyle innovations"
    assert normalise("Holdings Pharma Group Holdings plc") == "holdings"   # leading word kept
    assert normalise("The Medicines Company") == "medicines"


def test_parse_display_name():
    assert parse_display_name("Apellis Pharmaceuticals, Inc.  (APLS)  (CIK 0001492422)") == \
        {"name": "Apellis Pharmaceuticals, Inc.", "tickers": ["APLS"], "cik": 1492422}
    assert parse_display_name("BlackRock, Inc.  (CIK 0002012383)")["tickers"] == []
    multi = parse_display_name("Foo Corp  (FOO, FOOW)  (CIK 0000000123)")
    assert multi["tickers"] == ["FOO", "FOOW"]
    assert parse_display_name("garbled string") is None       # fails closed


def test_fts_ignores_holders_that_merely_mention_the_sponsor():
    # real response shape for q="Vera Therapeutics": holders file 13Gs naming the issuer
    hits = [{"_source": {"display_names": ["BlackRock, Inc.  (CIK 0002012383)",
                                           "Vera Therapeutics, Inc.  (VERA)  (CIK 0001831828)"]}},
            {"_source": {"display_names": ["FMR LLC  (CIK 0000315066)"]}}]
    c = candidates_from_fts(normalise("Vera Therapeutics, Inc."), hits)
    assert list(c) == [1831828] and c[1831828]["tickers"] == {"VERA"}


def test_ambiguous_key_is_rejected_not_guessed():
    idx = TickerIndex([{"cik_str": 1, "ticker": "ABCX", "title": "Abc Therapeutics Inc"},
                       {"cik_str": 2, "ticker": "ABCP", "title": "ABC Pharmaceuticals Corp"},
                       {"cik_str": 3, "ticker": "XYZ", "title": "XYZ Biosciences, Inc."}])
    r = resolve_fast("ABC Holdings Ltd", idx)
    assert r.status == "ambiguous" and r.ticker is None and r.cik is None
    ok = resolve_fast("Xyz Biosciences Inc", idx)
    assert ok.status == "resolved" and ok.ticker == "XYZ" and ok.cik == 3
    assert resolve_fast("Nothing Like It Inc", idx).status == "no_match"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
