"""YTD -> quarterly conversion on a hand-built fixture. Every trap from the spec
appears here: cumulative YTD, duplicate periods with different `fy` tags, a
mislabelled `fy`, a restatement, a missing quarter, a non-calendar fiscal year,
and a cash-flow-positive company."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from biocat.financials import compute_runway, dedupe_periods, ytd_to_quarterly  # noqa: E402


def f(start, end, val, fy, fp, form="10-Q", filed="2024-01-01"):
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp, "form": form, "filed": filed}


# Calendar FY2024, operating cash flow in $ (negative = burn). True quarters:
#   Q1 -10M, Q2 -15M, Q3 -20M, Q4 -25M
FY2024 = [
    f("2024-01-01", "2024-03-31", -10e6, 2024, "Q1", filed="2024-05-10"),
    f("2024-01-01", "2024-06-30", -25e6, 2024, "Q2", filed="2024-08-10"),
    f("2024-01-01", "2024-09-30", -45e6, 2024, "Q3", filed="2024-11-10"),
    f("2024-01-01", "2024-12-31", -70e6, 2024, "FY", form="10-K", filed="2025-03-01"),
    # the same Q2 period repeated as a prior-year comparative in a 2025 10-Q,
    # tagged fy=2025 (the Soleno pattern): must not double count
    f("2024-01-01", "2024-06-30", -25e6, 2025, "Q2", filed="2025-08-10"),
    # the same Q3 period, tagged fy=2025 even though it is a 2024 period
    f("2024-01-01", "2024-09-30", -45e6, 2025, "Q3", filed="2025-11-10"),
]


def test_ytd_is_differenced_not_read_as_quarterly():
    q = ytd_to_quarterly(FY2024)
    assert [(x["end"], x["val"]) for x in q] == [
        ("2024-03-31", -10e6), ("2024-06-30", -15e6), ("2024-09-30", -20e6), ("2024-12-31", -25e6)]
    assert [x["derived_from"] for x in q] == ["direct", "diff", "diff", "diff"]
    # the trap: the raw Q3 10-Q figure is 9 months of burn, 2.25x the true quarter
    assert -45e6 / q[2]["val"] == 2.25


def test_duplicates_dedupe_on_start_end_and_latest_filing_wins():
    restated = FY2024 + [f("2024-01-01", "2024-06-30", -26e6, 2024, "Q2", filed="2025-09-01")]
    assert len(dedupe_periods(restated)) == 4
    q = {x["end"]: x["val"] for x in ytd_to_quarterly(restated)}
    assert q["2024-06-30"] == -16e6      # restated H1 -26 minus Q1 -10
    assert q["2024-09-30"] == -19e6      # Q3 YTD -45 minus restated H1 -26


def test_fy_tag_is_ignored():
    # every fy tag scrambled: result must not change
    scrambled = [{**x, "fy": 1999} for x in FY2024]
    assert ytd_to_quarterly(scrambled) == ytd_to_quarterly(FY2024)


def test_missing_quarter_yields_no_fake_quarter():
    fy25 = [f("2025-01-01", "2025-03-31", -30e6, 2025, "Q1"),
            f("2025-01-01", "2025-09-30", -100e6, 2025, "Q3")]     # Q2 10-Q absent
    q = ytd_to_quarterly(fy25)
    assert [(x["end"], x["val"]) for x in q] == [("2025-03-31", -30e6)]


def test_non_calendar_fiscal_year():
    fy = [f("2024-07-01", "2024-09-30", -5e6, 2025, "Q1"),
          f("2024-07-01", "2024-12-31", -12e6, 2025, "Q2"),
          f("2023-07-01", "2024-06-30", -40e6, 2024, "FY", form="10-K")]   # annual only
    q = ytd_to_quarterly(fy)
    assert [(x["end"], x["val"]) for x in q] == [("2024-09-30", -5e6), ("2024-12-31", -7e6)]


def _facts(ocf, cash_end="2024-12-31", cash=100e6, sti=20e6):
    return {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            {"end": "2024-09-30", "val": 130e6, "filed": "2024-11-10"},
            {"end": cash_end, "val": cash, "filed": "2025-03-01"}]}},
        "ShortTermInvestments": {"units": {"USD": [
            {"end": "2024-09-30", "val": 99e6, "filed": "2024-11-10"},
            {"end": cash_end, "val": sti, "filed": "2025-03-01"}]}},
    }}}


def test_runway_uses_quarterly_burn_and_same_date_investments():
    r = compute_runway(_facts(FY2024), months_out=6, today=dt.date(2025, 4, 1))
    assert r.burn_status == "burning"
    assert r.cash_usd == 120e6 and r.cash_asof == "2024-12-31"       # STI at the SAME date
    assert r.burn_q_usd == 22.5e6                                     # avg of Q3 -20 and Q4 -25
    assert r.runway_q == round(120e6 / 22.5e6, 2)                     # 5.33 quarters
    assert r.runway_needed_q == 4.0                                   # 6/3 + 2
    assert r.funded_through is True
    # the naive mistake (Q3 YTD read as one quarter) would show burn 45M not 22.5M
    # in that case runway would be 2.67 and the answer would flip: the test pins it


def test_cash_positive_is_not_infinite_runway():
    pos = [f("2024-01-01", "2024-03-31", 5e6, 2024, "Q1"),
           f("2024-01-01", "2024-06-30", 12e6, 2024, "Q2")]
    r = compute_runway(_facts(pos, cash_end="2024-06-30"), months_out=6, today=dt.date(2024, 9, 1))
    assert r.burn_status == "cash_positive"
    assert r.runway_q is None and r.burn_q_usd is None and r.funded_through is None


def test_stale_financials_do_not_produce_a_runway():
    r = compute_runway(_facts(FY2024), months_out=6, today=dt.date(2026, 1, 1))
    assert r.burn_status == "stale" and r.runway_q is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
