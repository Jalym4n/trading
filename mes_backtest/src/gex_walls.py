"""Daily gamma "walls" from ES option open interest (Databento GLBX.MDP3 statistics + definition,
quarterly ES options only: the purchase does not include weeklies, 0DTE or SPX options, which
hold much of the near-dated gamma. That is a stated limit of this test).

Rule, fixed 2026-11-13 before the statistics file was read:
  For RTH day D use only open-interest records PUBLISHED before 09:30 ET on D (latest per option,
  none older than 7 days). F = the prior RTH close. sigma = annualised 20-day realised volatility
  of daily closes up to D-1, floored at 10%. For every option expiring on or after D, with strike
  within 10% of F:   weight = open interest x Black-76 gamma(F, K, sigma, T),  T = days to expiry / 365
  (at least 1 day). Sum weights by strike across expiries.
      call wall = the strike ABOVE F with the largest call weight
      put wall  = the strike BELOW F with the largest put weight

    python -m src.gex_walls extract     # statistics.csv.zst -> open_interest.parquet (stat_type 9)
    python -m src.gex_walls build       # -> data/external/es_gex_walls.parquet
"""
import sys

import numpy as np
import pandas as pd

from .load import ROOT, read_processed

RAW = ROOT / "data" / "options_raw"
OUT = ROOT / "data" / "external" / "es_gex_walls.parquet"
OPEN_INTEREST = 9


def extract() -> None:
    import zstandard
    parts, n = [], 0
    with open(RAW / "es-opt-statistics.csv.zst", "rb") as f:
        stream = zstandard.ZstdDecompressor().stream_reader(f)
        for ch in pd.read_csv(stream, usecols=["ts_event", "quantity", "stat_type", "symbol"], chunksize=5_000_000):
            n += len(ch)
            parts.append(ch[ch["stat_type"] == OPEN_INTEREST].drop(columns="stat_type"))
    oi = pd.concat(parts, ignore_index=True)
    oi["ts_event"] = pd.to_datetime(oi["ts_event"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    oi.to_parquet(RAW / "open_interest.parquet")
    print(f"{n:,} statistics rows -> {len(oi):,} open-interest rows, {oi['ts_event'].min()} .. {oi['ts_event'].max()}")


def walls_for_day(snap: pd.DataFrame, f: float, sigma: float, day: pd.Timestamp) -> tuple[float, float]:
    """snap: one row per option: strike_price, instrument_class, expiration, quantity."""
    s = snap[(snap["expiration"] >= day) & (snap["strike_price"].between(0.9 * f, 1.1 * f)) & (snap["quantity"] > 0)]
    if s.empty:
        return float("nan"), float("nan")
    t = np.maximum((s["expiration"] - day).dt.days.to_numpy(float), 1.0) / 365.0
    k = s["strike_price"].to_numpy(float)
    d1 = (np.log(f / k) + 0.5 * sigma * sigma * t) / (sigma * np.sqrt(t))
    gamma = np.exp(-0.5 * d1 * d1) / (np.sqrt(2 * np.pi) * f * sigma * np.sqrt(t))
    w = pd.DataFrame({"k": k, "cls": s["instrument_class"].to_numpy(), "w": s["quantity"].to_numpy(float) * gamma})
    calls = w[(w["cls"] == "C") & (w["k"] > f)].groupby("k")["w"].sum()
    puts = w[(w["cls"] == "P") & (w["k"] < f)].groupby("k")["w"].sum()
    return (float(calls.idxmax()) if len(calls) else float("nan"),
            float(puts.idxmax()) if len(puts) else float("nan"))


def build(instrument: str = "MES") -> pd.DataFrame:
    oi = pd.read_parquet(RAW / "open_interest.parquet").sort_values("ts_event")
    defs = pd.read_parquet(RAW / "defs.parquet").drop_duplicates("raw_symbol", keep="last")
    oi = oi.merge(defs, left_on="symbol", right_on="raw_symbol", how="inner")
    m5 = read_processed(instrument)["m5"]
    close = m5.groupby("date")["close"].last()
    sigma = (np.log(close).diff().rolling(20).std() * np.sqrt(252)).clip(lower=0.10)
    prior_close, prior_sigma = close.shift(1), sigma.shift(1)
    ts = oi["ts_event"].to_numpy()
    rows = []
    for day in close.index:
        f, sg = prior_close.get(day), prior_sigma.get(day)
        if not (np.isfinite(f) and np.isfinite(sg)):
            continue
        cut = day + pd.Timedelta(hours=9, minutes=30)
        lo, hi = np.searchsorted(ts, np.datetime64(cut - pd.Timedelta(days=7))), np.searchsorted(ts, np.datetime64(cut))
        snap = oi.iloc[lo:hi].drop_duplicates("symbol", keep="last")
        cw, pw = walls_for_day(snap, float(f), float(sg), day)
        rows.append({"date": day, "call_wall": cw, "put_wall": pw, "ref": f, "n_options": len(snap)})
    out = pd.DataFrame(rows).set_index("date")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT)
    print(out.describe().T.to_string())
    print(f"days with both walls: {out[['call_wall', 'put_wall']].notna().all(axis=1).sum()} of {len(out)}")
    return out


if __name__ == "__main__":
    {"extract": extract, "build": build}[sys.argv[1]]()
