"""Name normaliser and strict identity matching, on REAL sponsor names taken
from the ClinicalTrials.gov Stage 1 pull (2026-10-12) and real SEC strings."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.identity import Resolution, TickerIndex, candidates_from_fts, resolve_fast, sic_gate  # noqa: E402
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


def test_sec_state_tags_are_stripped_but_legal_forms_survive():
    assert normalise("VERTEX PHARMACEUTICALS INC / MA") == "vertex"
    assert normalise("TuHURA Biosciences, Inc./NV") == "tuhura"
    assert normalise("Ascendis Pharma A/S") == "ascendis pharma a s"
    assert normalise("Brainfarma Industria Química e Farmacêutica S/A") == \
        "brainfarma industria quimica e farmaceutica"


def test_vertex_regression_is_now_ambiguous():
    # live run 2026-10-12: "Vertex Pharmaceuticals Incorporated" resolved to VERX
    # (Vertex, Inc., tax software) because VRTX's SEC title carries "/ MA"
    idx = TickerIndex([{"cik_str": 875320, "ticker": "VRTX", "title": "VERTEX PHARMACEUTICALS INC / MA"},
                       {"cik_str": 1806837, "ticker": "VERX", "title": "Vertex, Inc."}])
    r = resolve_fast("Vertex Pharmaceuticals Incorporated", idx)
    assert r.status == "ambiguous" and r.ticker is None


class _FakeHttp:
    def __init__(self, sics):
        self.sics = sics

    def get_json(self, url, params=None, use_cache=True):
        cik = int(url.rsplit("CIK", 1)[1].split(".")[0])
        if cik not in self.sics:
            raise OSError("HTTP 500")
        sic, desc = self.sics[cik]
        return {"sic": sic, "sicDescription": desc}


def test_sic_gate_rejects_name_collisions_outside_healthcare():
    # real false joins from the same run: Thryv Therapeutics -> Thryv Holdings (SIC 7370s),
    # Polaris Group -> Polaris Inc. (vehicles); Starton Holdings is pharma and stays
    res = [Resolution("Thryv Therapeutics", "thryv", "resolved", 1556739, "THRY", "Thryv Holdings, Inc."),
           Resolution("Polaris Group", "polaris", "resolved", 931015, "PII", "Polaris Inc."),
           Resolution("Starton Therapeutics, Inc", "starton", "resolved", 2, "STA", "Starton Holdings, Inc."),
           Resolution("Unreachable Bio", "unreachable bio", "resolved", 3, "UNR", "Unreachable Bio Inc"),
           Resolution("Some Sponsor", "some sponsor", "no_match")]
    http = _FakeHttp({1556739: ("7374", "Services-Computer Processing"),
                      931015: ("3711", "Motor Vehicles & Passenger Car Bodies"),
                      2: ("2834", "Pharmaceutical Preparations")})
    sic_gate(res, http, log=lambda m: None)
    assert [r.status for r in res] == ["sic_outside_healthcare", "sic_outside_healthcare", "resolved",
                                       "sic_unverified", "no_match"]
    assert res[2].sic == 2834


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
