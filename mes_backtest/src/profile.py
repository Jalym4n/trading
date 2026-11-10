"""Prior-session volume profile: POC / VAH / VAL for every RTH trading day.

Input: data/processed/mes_1m.parquet and sessions.parquet (from src.load).
Output: data/processed/levels.parquet, one row per trading day D, built ONLY
from the 1-minute bars of the previous trading day's RTH session (09:30-16:00
ET, 13:00 on early-close days). Nothing from day D enters D's levels.

Definitions (parameters from config.json):
  bin       each 1-min bar's volume goes to the bin of its typical price
            (H+L+C)/3, rounded to the nearest multiple of profile_bin (1.0 pt):
            bin 5805 holds typical prices in [5804.5, 5805.5).
  POC       the bin with the most volume; a tie goes to the bin nearest the
            session's volume-weighted mean price, then the lower price.
  value area  start at the POC and repeatedly add the single neighbouring bin
            (just above or just below the current area) with more volume, the
            upper one on a tie, until the area holds >= value_area_pct (70%) of
            session volume. Empty bins inside the range count as zero volume.
  VAH / VAL the highest / lowest bin price in the value area.
Prices are bin prices (multiples of profile_bin), never adjusted across rolls.
A day whose prior session traded a different contract gets no levels; those
are exactly the first sessions of a new contract, already roll_skip.

    python -m src.profile        # build levels.parquet, print checks, plot one day
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from .load import ROOT, load_config


def volume_profile(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray,
                   bin_size: float, va_pct: float) -> dict:
    """POC / VAH / VAL and the binned histogram for one session's 1-min bars."""
    tp = (np.asarray(high) + np.asarray(low) + np.asarray(close)) / 3.0
    vol = np.asarray(volume, dtype=float)
    if len(tp) == 0 or vol.sum() <= 0:
        raise ValueError("empty session")
    k = np.floor(tp / bin_size + 0.5).astype(np.int64)            # round half up to the nearest bin
    lo = k.min()
    hist = np.bincount(k - lo, weights=vol)                          # contiguous, empty bins = 0
    prices = (np.arange(len(hist)) + lo) * bin_size
    vwap = float((tp * vol).sum() / vol.sum())
    top = np.flatnonzero(hist == hist.max())
    poc_i = int(min(top, key=lambda i: (abs(prices[i] - vwap), prices[i])))
    total, target = hist.sum(), va_pct * hist.sum()
    a = b = poc_i
    area = hist[poc_i]
    while area < target - 1e-9:
        up = hist[b + 1] if b + 1 < len(hist) else -1.0
        dn = hist[a - 1] if a - 1 >= 0 else -1.0
        if up >= dn:
            b += 1
            area += up
        else:
            a -= 1
            area += dn
    return {"poc": float(prices[poc_i]), "vah": float(prices[b]), "val": float(prices[a]),
            "volume": float(total), "va_share": float(area / total), "n_bins": int(len(hist)),
            "hist": pd.Series(hist, index=prices)}


def rth_bars(m1: pd.DataFrame, day: pd.Timestamp, rth_start: str, rth_end: str) -> pd.DataFrame:
    """1-min bars of one RTH session: [day rth_start, day rth_end) in market time."""
    tz = m1.index.tz
    t0 = pd.Timestamp(f"{day.date()} {rth_start}").tz_localize(tz)
    t1 = pd.Timestamp(f"{day.date()} {rth_end}").tz_localize(tz)
    i0, i1 = m1.index.searchsorted(t0, "left"), m1.index.searchsorted(t1, "left")   # index is sorted
    return m1.iloc[i0:i1]


def prior_session_levels(m1: pd.DataFrame, sessions: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Levels for every trading day D from the previous trading day P's RTH bars."""
    p = cfg["params"]
    rows = []
    days = list(sessions.index)
    for prev, day in zip(days[:-1], days[1:]):
        end = p["rth_half_day_end"] if sessions.at[prev, "half_day"] else p["rth_full"][1]
        bars = rth_bars(m1, prev, p["rth_full"][0], end)
        row = {"date": day, "prior_date": prev, "contract": sessions.at[day, "contract"],
               "prior_contract": sessions.at[prev, "contract"], "prior_half_day": bool(sessions.at[prev, "half_day"]),
               "prior_n_1m": len(bars), "prior_last_bar": bars.index.max() if len(bars) else pd.NaT,
               "poc": np.nan, "vah": np.nan, "val": np.nan, "prior_volume": np.nan, "va_share": np.nan,
               "n_bins": 0, "levels_reason": None}
        if row["prior_contract"] != row["contract"]:
            row["levels_reason"] = f"prior session traded {row['prior_contract']}, not {row['contract']}"
        elif bars.empty:
            row["levels_reason"] = "no prior RTH bars"
        else:
            vp = volume_profile(bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy(),
                                bars["volume"].to_numpy(), p["profile_bin"], p["value_area_pct"])
            row.update({k: vp[k] for k in ("poc", "vah", "val", "va_share", "n_bins")})
            row["prior_volume"] = vp["volume"]
        rows.append(row)
    out = pd.DataFrame(rows).set_index("date")
    # no look-ahead: every input bar is strictly before day D's session start
    starts = pd.DatetimeIndex([pd.Timestamp(f"{d.date()} {p['session_start']}") for d in out.index]).tz_localize(m1.index.tz)
    leak = out["prior_last_bar"].notna() & (out["prior_last_bar"] >= starts)
    if leak.any():
        raise AssertionError(f"look-ahead: {int(leak.sum())} days use bars at or after their own session start")
    return out


def plot_day(m1: pd.DataFrame, sessions: pd.DataFrame, levels: pd.DataFrame, day: str, cfg: dict,
             path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p = cfg["params"]
    r = levels.loc[day]
    end = p["rth_half_day_end"] if r["prior_half_day"] else p["rth_full"][1]
    bars = rth_bars(m1, r["prior_date"], p["rth_full"][0], end)
    vp = volume_profile(bars["high"], bars["low"], bars["close"], bars["volume"], p["profile_bin"],
                        p["value_area_pct"])
    h = vp["hist"]
    inside = (h.index >= vp["val"]) & (h.index <= vp["vah"])
    fig, ax = plt.subplots(figsize=(6, 9))
    ax.barh(h.index, h.values, height=p["profile_bin"] * 0.9, color=np.where(inside, "#4C78A8", "#BBBBBB"))
    for name, y, c in (("POC", vp["poc"], "#E45756"), ("VAH", vp["vah"], "#54A24B"), ("VAL", vp["val"], "#54A24B")):
        ax.axhline(y, color=c, lw=1)
        ax.text(h.max() * 1.01, y, f"{name} {y:g}", va="center", fontsize=8, color=c)
    ax.set_title(f"{r['prior_date'].date()} RTH profile ({r['contract']}), levels for {day}\n"
                 f"value area {vp['va_share']:.1%} of {vp['volume']:,.0f} contracts")
    ax.set_xlabel("volume (contracts)")
    ax.set_ylabel("price (1-pt bins)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def build(cfg: dict, root: Path = ROOT) -> pd.DataFrame:
    proc = root / "data" / "processed"
    m1 = pd.read_parquet(proc / "mes_1m.parquet")
    sessions = pd.read_parquet(proc / "sessions.parquet")
    levels = prior_session_levels(m1, sessions, cfg)
    levels.to_parquet(proc / "levels.parquet")
    return levels


def validate(levels: pd.DataFrame, sessions: pd.DataFrame, cfg: dict, n_sample: int = 8, seed: int = 11) -> str:
    va = cfg["params"]["value_area_pct"]
    have = levels.dropna(subset=["poc"])
    none = levels[levels["poc"].isna()]
    lines = [f"days with levels: {len(have):,} of {len(levels):,}; without: {len(none)}"]
    not_roll = none[~sessions.loc[none.index, "roll_skip"].to_numpy()]
    lines.append(f"days without levels that are NOT roll_skip days: {len(not_roll)}")
    if len(not_roll):
        lines.append(not_roll[["prior_date", "levels_reason"]].to_string())
    lines.append(f"VAL <= POC <= VAH on all days: {bool(((have['val'] <= have['poc']) & (have['poc'] <= have['vah'])).all())}")
    s = have["va_share"]
    lines.append(f"value-area share: min {s.min():.3f}, median {s.median():.3f}, max {s.max():.3f} "
                 f"(target {va:.2f}; every day >= target: {bool((s >= va - 1e-9).all())})")
    w = have["vah"] - have["val"]
    lines.append(f"value-area width (pts): median {w.median():.0f}, 5th pct {w.quantile(.05):.0f}, "
                 f"95th pct {w.quantile(.95):.0f}")
    lines.append(f"prior sessions on an early-close day: {int(have['prior_half_day'].sum())}")
    lines.append("")
    lines.append(f"{n_sample} random days (seed {seed}):")
    smp = have.sample(n=n_sample, random_state=seed).sort_index()
    lines.append(smp[["prior_date", "contract", "prior_n_1m", "val", "poc", "vah", "va_share", "prior_volume"]]
                 .assign(prior_date=lambda x: x["prior_date"].dt.date).to_string())
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=None)
    ap.add_argument("--plot-day", default="2024-03-14")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    levels = build(cfg)
    sessions = pd.read_parquet(ROOT / "data" / "processed" / "sessions.parquet")
    print(validate(levels, sessions, cfg))
    m1 = pd.read_parquet(ROOT / "data" / "processed" / "mes_1m.parquet")
    out = ROOT / "results" / f"profile_{a.plot_day}.png"
    out.parent.mkdir(exist_ok=True)
    plot_day(m1, sessions, levels, a.plot_day, cfg, out)
    print(f"\nprofile plot: {out}")


if __name__ == "__main__":
    main()
