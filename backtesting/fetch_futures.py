"""Download CME index futures 1m bars from Databento and back-adjust the rolls.

Output: one CSV in the canonical fvg_lab format (timestamp = bar OPEN, UTC)
plus audit columns, ready for:

    df = data.load_csv("es_1m.csv")                 # America/New_York
    df5 = data.resample_ohlcv(df, "5min")
    rth = data.add_overnight_levels(df5)

Why back-adjust. `ES.v.0` is the front contract by volume, stitched with NO
adjustment. At each quarterly roll the price jumps by the old/new contract
spread (often 10 to 60 points), a jump nobody could trade. That jump
satisfies low[i] > high[i-2] and manufactures a phantom FVG four times a year.

How the jump is measured. NOT as (first new bar) - (last old bar): that mixes
the contract spread with a real one-minute move. Instead the incoming
contract is fetched by instrument_id for the 24h before the roll, and the
spread is new.close - old.close at the LAST MINUTE BOTH TRADED. Every bar
before the roll is shifted by that many points (difference adjustment), so
point distances, ATR in points and tick costs are unchanged, and prices stay
on the 0.25 tick grid. The most recent contract is never adjusted.

Cost control. The script quotes the price first (quotes are free) and stops
unless you pass --yes or confirm interactively. Downloads are cached per year
under .databento_cache/, so a rerun after a failure does not pay twice.

    pip install databento
    export DATABENTO_API_KEY=...          # never put the key in a file here
    python fetch_futures.py --root ES --start 2010-06-06 --out es_1m.csv
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".databento_cache")
OUT_COLS = ["open", "high", "low", "close", "volume"]


# ------------------------------------------------------------ pure logic ---
def find_rolls(df: pd.DataFrame) -> pd.DataFrame:
    """Every bar where instrument_id differs from the previous bar."""
    iid = df["instrument_id"].to_numpy()
    at = np.flatnonzero(iid[1:] != iid[:-1]) + 1
    return pd.DataFrame({
        "ts": df.index[at],
        "old_id": iid[at - 1],
        "new_id": iid[at],
        "old_last_close": df["close"].to_numpy()[at - 1],
        "new_first_open": df["open"].to_numpy()[at],
    })


def spread_at_overlap(old: pd.DataFrame, new: pd.DataFrame, before: pd.Timestamp):
    """new.close - old.close at the last minute strictly before `before` where
    both contracts printed. Returns (spread, ts) or (nan, None)."""
    o = old.loc[old.index < before, "close"]
    n = new.loc[new.index < before, "close"]
    common = o.index.intersection(n.index)
    if common.empty:
        return float("nan"), None
    t = common.max()
    return float(n.loc[t] - o.loc[t]), t


def back_adjust(df: pd.DataFrame, rolls: pd.DataFrame) -> pd.DataFrame:
    """Shift every bar before each roll by that roll's spread (points).

    rolls needs columns ts and spread. Adjustments accumulate backwards: a bar
    three rolls before the end carries the sum of all three spreads.
    """
    out = df.copy()
    adj = np.zeros(len(out))
    idx = out.index
    for ts, spread in zip(rolls["ts"], rolls["spread"]):
        if not np.isfinite(spread):
            raise ValueError(f"no overlap spread for roll at {ts}; refusing to guess")
        adj[idx < ts] += spread
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].to_numpy() + adj
    out["adj"] = adj
    return out


def roll_jump_report(raw: pd.DataFrame, adjusted: pd.DataFrame, rolls: pd.DataFrame) -> pd.DataFrame:
    """Open-vs-previous-close jump at each roll, before and after adjustment,
    against the typical jump at that same clock minute on non-roll days."""
    prev_raw = raw["close"].shift(1)
    prev_adj = adjusted["close"].shift(1)
    jump_raw = (raw["open"] - prev_raw).abs()
    jump_adj = (adjusted["open"] - prev_adj).abs()
    minute = raw.index.hour * 60 + raw.index.minute
    is_roll = raw.index.isin(rolls["ts"])
    typical = jump_raw[~is_roll].groupby(minute[~is_roll]).quantile(0.99)
    rows = []
    for ts in rolls["ts"]:
        m = ts.hour * 60 + ts.minute
        rows.append({"ts": ts, "raw_jump": jump_raw.loc[ts], "adj_jump": jump_adj.loc[ts],
                     "p99_same_minute": typical.get(m, np.nan)})
    return pd.DataFrame(rows)


def contracts_in_use(intervals: list[dict], first: pd.Timestamp, last: pd.Timestamp) -> list[str]:
    """Raw symbols an instrument_id carried while it was in OUR series.

    CME reuses instrument_ids over the years (id 118 was ESU4 in 2014 and an
    option, a spread and a swap at other times), so resolving an id over the
    whole download range returns unrelated contracts. Keep only the intervals
    [d0, d1) that overlap the dates this id actually appears in the file.
    """
    f, l = first.normalize().tz_localize(None), last.normalize().tz_localize(None)
    return sorted({iv["s"] for iv in intervals
                   if pd.Timestamp(iv["d0"]) <= l and pd.Timestamp(iv["d1"]) > f})


def is_outright(symbol: str, root: str) -> bool:
    """ESU4, ESZ25 yes; ESZ5-ESH6 (spread), ESU4 C5000 (option), NQU4 no."""
    import re
    return re.fullmatch(rf"{re.escape(root)}[FGHJKMNQUVXZ]\d{{1,2}}", symbol) is not None


# ------------------------------------------------------------- databento ---
def _client():
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        sys.exit("set DATABENTO_API_KEY first (environment variable, not a file).")
    try:
        import databento as db
    except ImportError:
        sys.exit("pip install databento")
    return db.Historical(key)


def _year_chunks(start: str, end: str):
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    t = s
    while t < e:
        t1 = min(pd.Timestamp(year=t.year + 1, month=1, day=1, tz="UTC"), e)
        yield t, t1
        t = t1


def _fetch_chunk(client, symbol, stype, t0, t1, cache_name):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, cache_name)
    if not os.path.exists(path):
        store = client.timeseries.get_range(dataset=DATASET, symbols=[symbol], stype_in=stype,
                                            schema=SCHEMA, start=t0, end=t1)
        store.to_file(path + ".part")
        os.replace(path + ".part", path)
    import databento as db
    df = db.DBNStore.from_file(path).to_df()
    return df[["instrument_id"] + OUT_COLS]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="ES", help="ES, NQ, YM, RTY, MES, MNQ ...")
    ap.add_argument("--roll", default="v", choices=["v", "n", "c"],
                    help="continuous rule: v=volume (default), n=open interest, c=calendar")
    ap.add_argument("--start", default="2010-06-06")
    ap.add_argument("--end", default=None, help="exclusive; default = latest available")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-cost", type=float, default=30.0, help="refuse above this many USD")
    ap.add_argument("--yes", action="store_true", help="skip the purchase confirmation")
    a = ap.parse_args(argv)

    client = _client()
    cont = f"{a.root}.{a.roll}.0"
    rng = client.metadata.get_dataset_range(dataset=DATASET)
    end = a.end or rng["schema"][SCHEMA]["end"]
    req = dict(dataset=DATASET, symbols=[cont], stype_in="continuous", schema=SCHEMA,
               start=a.start, end=end)

    # ---- 1. quote, confirm ----------------------------------------------
    cost = client.metadata.get_cost(**req)
    n = client.metadata.get_record_count(**req)
    print(f"{cont} {SCHEMA}  {a.start} -> {end}")
    print(f"quote: ${cost:,.2f} for {n:,} bars (full range; years already in "
          f"{os.path.basename(CACHE)}/ are read from disk, not downloaded again)")
    if cost > a.max_cost:
        sys.exit(f"quote exceeds --max-cost {a.max_cost}; raise it deliberately if intended.")
    if not a.yes:
        if input("type 'buy' to download and pay this: ").strip().lower() != "buy":
            sys.exit("not confirmed; nothing downloaded.")

    # ---- 2. download by year (cached) -----------------------------------
    parts = []
    for t0, t1 in _year_chunks(a.start, end):
        name = f"{cont}_{t0:%Y%m%d}_{t1:%Y%m%d}.dbn.zst"
        print(f"  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}", end="", flush=True)
        part = _fetch_chunk(client, cont, "continuous", t0, t1, name)
        print(f"  {len(part):,} bars")
        parts.append(part)
    raw = pd.concat(parts).sort_index()
    raw = raw[~raw.index.duplicated(keep="first")]

    # ---- 3. outrights only ----------------------------------------------
    ids = sorted(int(x) for x in raw["instrument_id"].unique())
    res = client.symbology.resolve(dataset=DATASET, symbols=[str(i) for i in ids],
                                   stype_in="instrument_id", stype_out="raw_symbol",
                                   start_date=a.start[:10], end_date=str(end)[:10])
    span = raw.groupby("instrument_id").apply(lambda g: (g.index[0], g.index[-1]))
    names = {int(k): contracts_in_use(v, *span[int(k)]) for k, v in res["result"].items()}
    bad = {i: s for i, s in names.items() if len(s) != 1 or not is_outright(s[0], a.root)}
    if bad:
        sys.exit(f"instrument_id(s) not resolving to exactly one {a.root} outright while "
                 f"in the series: {bad}. Refusing to continue.")
    print(f"{len(names)} contracts, all {a.root} outrights: "
          f"{names[ids[0]][0]} ... {names[ids[-1]][0]}" if ids else "")
    raw["contract"] = raw["instrument_id"].map(lambda i: "/".join(names.get(int(i), ["?"])))

    # ---- 4. measure each roll at a common minute ------------------------
    rolls = find_rolls(raw)
    spreads, at = [], []
    for r in rolls.itertuples():
        s, t = float("nan"), None
        for hours in (24, 96):  # 96h covers a roll that lands just after a weekend
            t0 = r.ts - pd.Timedelta(hours=hours)
            name = f"id{r.new_id}_{t0:%Y%m%d%H%M}_{r.ts:%Y%m%d%H%M}.dbn.zst"
            new = _fetch_chunk(client, str(r.new_id), "instrument_id", t0, r.ts, name)
            old = raw.loc[(raw.index >= t0) & (raw["instrument_id"] == r.old_id)]
            s, t = spread_at_overlap(old, new, r.ts)
            if t is not None:
                break
        spreads.append(s)
        at.append(t)
    rolls["spread"] = spreads
    rolls["measured_at"] = at
    rolls["old"] = rolls["old_id"].map(lambda i: "/".join(names.get(int(i), ["?"])))
    rolls["new"] = rolls["new_id"].map(lambda i: "/".join(names.get(int(i), ["?"])))

    years = (raw.index[-1] - raw.index[0]).days / 365.25
    per_year = len(rolls) / years if years >= 1 else float("nan")
    print(f"\n{len(rolls)} rolls" + (f" ({per_year:.1f}/yr; ~4/yr expected for quarterly "
                                       f"index futures)" if years >= 1 else ""))
    if years >= 1 and per_year > 6:
        print("  WARNING: more rolls than quarterly. The volume ranking may flip back "
              "and forth around expiry. Each flip is adjusted, but inspect the rolls file.")

    adjusted = back_adjust(raw, rolls)
    rep = roll_jump_report(raw, adjusted, rolls)
    rolls = rolls.merge(rep, on="ts")

    # ---- 5. checks and output -------------------------------------------
    if (adjusted[["open", "high", "low", "close"]] <= 0).any().any():
        sys.exit("back-adjustment produced non-positive prices; use a later --start.")
    off_grid = ((adjusted["close"] * 4) % 1 != 0).sum()
    worst = rolls.sort_values("adj_jump", ascending=False).head(3)
    print(f"roll jumps: raw median {rolls['raw_jump'].median():.2f} pts -> "
          f"adjusted median {rolls['adj_jump'].median():.2f} pts")
    print(f"adjusted roll jumps above the 99th pct of normal same-minute jumps: "
          f"{int((rolls['adj_jump'] > rolls['p99_same_minute']).sum())} of {len(rolls)}")
    print("largest adjusted roll jumps:")
    print(worst[["ts", "old", "new", "raw_jump", "adj_jump", "p99_same_minute"]].to_string(index=False))
    if off_grid and a.root in ("ES", "NQ", "MES", "MNQ"):
        print(f"  WARNING: {off_grid} closes off the 0.25 grid after adjustment")

    out = a.out or f"{a.root.lower()}_1m.csv"
    tmp = out + ".part"
    o = adjusted[OUT_COLS + ["contract", "adj"]].copy()
    o.index = o.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    o.index.name = "timestamp"
    o.to_csv(tmp)
    os.replace(tmp, out)
    rolls.to_csv(out.replace(".csv", "_rolls.csv"), index=False)

    try:
        cond = client.metadata.get_dataset_condition(dataset=DATASET, start_date=a.start[:10],
                                                     end_date=str(end)[:10])
        poor = [d["date"] for d in cond if d.get("condition") != "available"]
        print(f"\nDatabento days not fully 'available': {len(poor)}"
              + (f" e.g. {poor[:6]}" if poor else ""))
    except Exception as e:  # informational only
        print(f"(could not read dataset condition: {e})")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fvg_lab import data
    chk = data.load_csv(out)
    data.assert_no_roll_gaps(chk)            # raises if any roll survived adjustment
    data.assert_no_roll_gaps(data.resample_ohlcv(chk, "5min"))
    print(f"\nwrote {len(chk):,} bars to {out}  ({chk.index[0]} -> {chk.index[-1]})")
    print(f"rolls written to {out.replace('.csv', '_rolls.csv')}")
    print("data.load_csv + validate + assert_no_roll_gaps (1m and 5m): OK")


if __name__ == "__main__":
    main()
