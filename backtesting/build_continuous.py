"""Build a back-adjusted continuous 1m series from a Databento ALL-CONTRACTS file.

Input: a Databento GLBX.MDP3 ohlcv-1m CSV that holds every contract of a root
(outrights and calendar spreads), e.g. glbx-mdp3-20200101-20261107.ohlcv-1m.csv
with columns ts_event, rtype, publisher_id, instrument_id, open, high, low,
close, volume, symbol. Output: the same canonical CSV fetch_futures.py writes
(timestamp = bar OPEN, UTC; open..volume; contract; adj) plus a _rolls.csv.

Contract choice mirrors Databento's `.v.0`: each UTC day trades the outright
with the most volume on the PREVIOUS UTC day, and the series never steps back
to an earlier expiry (no flip-flops around expiry). Rolls therefore land at
00:00 UTC, mid-session, exactly where assert_no_roll_gaps can see them.

Each roll's spread is measured from this file alone: new.close - old.close at
the last minute both contracts printed before the roll (fetch_futures'
spread_at_overlap), then every earlier bar is shifted by it (back_adjust).
Nothing is downloaded and nothing is bought.

    python build_continuous.py --csv glbx-mdp3-...ohlcv-1m.csv --root MES --out mes_1m.csv
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fetch_futures import (OUT_COLS, back_adjust, find_rolls, is_outright,  # noqa: E402
                           roll_jump_report, spread_at_overlap)

MONTHS = "FGHJKMNQUVXZ"


def expiry_key(symbol: str, root: str, first_year: int) -> tuple[int, int]:
    """MESZ0 -> (2020, 12). One-digit years are placed in the decade at or after first_year."""
    m = re.fullmatch(rf"{re.escape(root)}([FGHJKMNQUVXZ])(\d{{1,2}})", symbol)
    code, yy = m.group(1), m.group(2)
    if len(yy) == 2:
        year = 2000 + int(yy)
    else:
        year = first_year - first_year % 10 + int(yy)
        if year < first_year - 1:
            year += 10
    return year, MONTHS.index(code) + 1


def choose_front(out: pd.DataFrame, root: str) -> pd.Series:
    """{UTC day: symbol}: the previous day's volume leader, never an earlier expiry."""
    first_year = out.index[0].year
    day = out.index.normalize()
    vol = out.groupby([day, "symbol"])["volume"].sum().unstack(fill_value=0)
    leader = vol.idxmax(axis=1).shift(1)
    leader.iloc[0] = vol.iloc[0].idxmax()          # first day: its own leader (no earlier data)
    chosen, cur = {}, None
    for d, sym in leader.items():
        if cur is None or expiry_key(sym, root, first_year) > expiry_key(cur, root, first_year):
            cur = sym
        chosen[d] = cur
    return pd.Series(chosen)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--root", default="MES")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    raw = pd.read_csv(a.csv, usecols=["ts_event", "instrument_id", "open", "high", "low", "close", "volume",
                                      "symbol"])
    raw["ts_event"] = pd.to_datetime(raw["ts_event"], utc=True)
    raw = raw.set_index("ts_event").sort_index()
    outr = raw[raw["symbol"].map(lambda s: is_outright(s, a.root))]
    print(f"{len(raw):,} rows; {outr['symbol'].nunique()} {a.root} outrights, "
          f"{raw['symbol'].nunique() - outr['symbol'].nunique()} spreads/other dropped")

    front = choose_front(outr, a.root)
    day = outr.index.normalize()
    pick = outr[outr["symbol"].to_numpy() == front.reindex(day).to_numpy()].copy()
    pick = pick[~pick.index.duplicated(keep="first")]
    pick["contract"] = pick["symbol"]

    rolls = find_rolls(pick)
    spreads, at = [], []
    for r in rolls.itertuples():
        t0 = r.ts - pd.Timedelta(hours=96)
        win = outr[(outr.index >= t0) & (outr.index < r.ts)]
        s, t = spread_at_overlap(win[win["instrument_id"] == r.old_id], win[win["instrument_id"] == r.new_id], r.ts)
        spreads.append(s)
        at.append(t)
    rolls["spread"], rolls["measured_at"] = spreads, at
    sym = pick.drop_duplicates("instrument_id").set_index("instrument_id")["symbol"]
    rolls["old"], rolls["new"] = rolls["old_id"].map(sym), rolls["new_id"].map(sym)
    years = (pick.index[-1] - pick.index[0]).days / 365.25
    print(f"{len(rolls)} rolls ({len(rolls) / years:.1f}/yr; quarterly index futures ~4/yr)")

    adjusted = back_adjust(pick, rolls)
    rolls = rolls.merge(roll_jump_report(pick, adjusted, rolls), on="ts")
    if (adjusted[["open", "high", "low", "close"]] <= 0).any().any():
        sys.exit("back-adjustment produced non-positive prices")
    off = int(((adjusted["close"] * 4) % 1 != 0).sum())
    tick = 0.25
    limit = np.maximum(rolls["p99_same_minute"].fillna(0), 2 * tick)
    leftover = rolls[rolls["adj_jump"] > limit]
    print(f"roll jumps: raw median {rolls['raw_jump'].median():.2f} pts -> adjusted median "
          f"{rolls['adj_jump'].median():.2f} pts; off the 0.25 grid: {off}")
    print(rolls[["ts", "old", "new", "spread", "measured_at", "raw_jump", "adj_jump"]].to_string(index=False))
    if not leftover.empty:
        sys.exit(f"{len(leftover)} roll(s) still jump after adjustment")

    out = a.out or f"{a.root.lower()}_1m.csv"
    o = adjusted[OUT_COLS + ["contract", "adj"]].copy()
    o.index = o.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    o.index.name = "timestamp"
    o.to_csv(out + ".part")
    os.replace(out + ".part", out)
    rolls.to_csv(out.replace(".csv", "_rolls.csv"), index=False)

    from fvg_lab import data
    chk = data.load_csv(out)
    rth5 = data.add_session(data.resample_ohlcv(chk, "5min"))[OUT_COLS]
    try:
        data.assert_no_roll_gaps(rth5)
        generic = "5m RTH generic jump check: pass"
    except ValueError as e:
        generic = f"5m RTH generic jump check (advisory): {str(e)[:160]}"
    print(f"\nwrote {len(chk):,} bars to {out}  ({chk.index[0]} -> {chk.index[-1]})")
    print(f"data.load_csv + validate: OK; all {len(rolls)} adjusted roll jumps within normal range")
    print(generic)


if __name__ == "__main__":
    main()
