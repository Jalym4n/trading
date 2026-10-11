#!/usr/bin/env python3
"""Verify NQ session coverage before committing to a multi-year download.

Pulls ONE day of ohlcv-1m and answers three questions with data rather than
assumption:

  1. Is this 23-hour Globex coverage or RTH only?
  2. Does the continuous symbol resolve to an outright contract, or can a
     calendar spread leak in?
  3. Is the overnight window (18:00 to 09:29 ET) actually populated?

Question 3 is the one that matters. The overnight range IS the strategy. A
feed that only carries 09:30 to 16:00 makes the whole approach unrunnable, and
you want to know that for a fraction of a cent, not after a multi-year pull.

    pip install databento pandas
    export DATABENTO_API_KEY=db-xxxxxxxxxxxx
    python check_coverage.py
    python check_coverage.py --date 2026-10-07 --symbol ES
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
TZ = "America/New_York"


def analyze(df: pd.DataFrame, contracts: list[str]) -> bool:
    """Return True if the frame looks like full 23h Globex coverage."""
    ok = True
    print(f"\n{len(df):,} one-minute bars returned")
    print(f"first  {df.index[0]}")
    print(f"last   {df.index[-1]}\n")

    # ---- 1. bars per hour, ET -------------------------------------------
    per_hour = df.groupby(df.index.hour).size().reindex(range(24), fill_value=0)
    print("bars per hour (ET).  60 = a complete hour.")
    print("hour  bars  " + "-" * 40)
    for h, n in per_hour.items():
        bar = "#" * int(n / 60 * 38)
        tag = ""
        if h == 17:
            tag = "  <- CME daily halt, empty is CORRECT"
        elif h == 16:
            tag = "  post-close: must be EXCLUDED from the next ONH/ONL"
        elif 9 <= h < 16:
            tag = "  RTH"
        print(f"{h:>4}  {n:>4}  {bar}{tag}")

    live_hours = int((per_hour > 0).sum())
    print(f"\nhours with data: {live_hours}")

    # ---- 2. the verdict --------------------------------------------------
    overnight = df[(df.index.hour >= 18) | (df.index.hour < 9) |
                   ((df.index.hour == 9) & (df.index.minute < 30))]
    print(f"bars in the 18:00-09:29 overnight window: {len(overnight):,}")

    if live_hours >= 23:
        print("\nVERDICT: full 23-hour Globex coverage. The overnight range is "
              "computable. Proceed.")
    elif len(overnight) == 0:
        print("\nVERDICT: RTH ONLY. No overnight bars at all. This feed cannot "
              "support the overnight-range strategy. Stop here.")
        ok = False
    else:
        print(f"\nVERDICT: PARTIAL ({live_hours} hours). Some overnight data but "
              f"not a full session. Inspect before trusting it.")
        ok = False

    # ---- 3. spread contamination ----------------------------------------
    # The instrument list for NQ contains calendar spreads such as NQZ6-NQH7,
    # whose 'price' is a spread differential, not an index level. One of those
    # in the series would be catastrophic and silent.
    # The 'symbol' column only echoes the continuous name requested (NQ.v.0),
    # so checking it for '-' proves nothing. Resolve instrument_id instead.
    if contracts:
        syms = contracts
        spreads = [s for s in syms if "-" in str(s)]
        print(f"\ncontracts resolved: {syms}")
        if spreads:
            print(f"WARNING: calendar spread(s) present: {spreads}")
            print("  Their prices are differentials, not index levels. Filter "
                  "any symbol containing '-' before using this data.")
            ok = False
        else:
            print("no calendar spreads: continuous symbology resolved to "
                  "outrights only, as intended.")
    else:
        print("\ncould not resolve instrument_id to a contract; spread "
              "contamination NOT checked.")
        ok = False

    # ---- 4. sanity on the prices ----------------------------------------
    print(f"\nprice range this day: {df['low'].min():,.2f} to {df['high'].max():,.2f}")
    print(f"total volume: {int(df['volume'].sum()):,}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NQ")
    ap.add_argument("--roll", default="v", choices=["v", "n", "c"])
    ap.add_argument("--date", default=None,
                    help="a recent WEDNESDAY works best (full session, no "
                         "holiday effects). Default: last Wednesday.")
    args = ap.parse_args()

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("set DATABENTO_API_KEY first.", file=sys.stderr)
        return 1
    try:
        import databento as db
    except ImportError:
        print("pip install databento", file=sys.stderr)
        return 1

    if args.date:
        day = pd.Timestamp(args.date)
    else:
        today = pd.Timestamp.now("UTC").normalize().tz_localize(None)
        day = today - pd.Timedelta(days=(today.dayofweek - 2) % 7 + 7)

    # widen by a day either side so the 18:00 prior-evening open is included
    start = (day - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    end = (day + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    cont = f"{args.symbol}.{args.roll}.0"

    client = db.Historical(key)
    req = dict(dataset=DATASET, symbols=[cont], stype_in="continuous",
               schema=SCHEMA, start=start, end=end)

    cost = client.metadata.get_cost(**req)
    print(f"{cont}  {start} to {end}  schema={SCHEMA}")
    print(f"cost for this probe: ${cost:,.6f}")
    if cost > 1.0:
        print("unexpectedly expensive for one day. Check the schema is "
              "ohlcv-1m and not trades or mbo.", file=sys.stderr)
        return 1

    store = client.timeseries.get_range(**req)
    df = store.to_df(tz=TZ)
    if df.empty:
        print("empty response. Check the symbol root and that the date is a "
              "trading day.", file=sys.stderr)
        return 1

    ids = [str(int(x)) for x in df["instrument_id"].unique()]
    res = client.symbology.resolve(dataset=DATASET, symbols=ids,
                                   stype_in="instrument_id", stype_out="raw_symbol",
                                   start_date=start, end_date=end)
    contracts = sorted({m["s"] for v in res["result"].values() for m in v})
    # restrict to the single trade date so the hour histogram is clean
    # 18:00 prior evening to 17:00: the full Globex day, post-close included
    sel = df[(df.index >= day.tz_localize(TZ) - pd.Timedelta(hours=6)) &
             (df.index < day.tz_localize(TZ) + pd.Timedelta(hours=17))]
    ok = analyze(sel if len(sel) > 600 else df, contracts)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
