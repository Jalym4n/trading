"""Ingest the Databento MES 1-minute file, build the front month, resample to 5-min RTH.

Every parameter comes from config.json at the project root (path overridable
with --config). Steps, in this order:

  1. Read every raw file in data/raw/ (.zip, .csv, .csv.zst), concatenate, sort.
  2. Parse ts_event as UTC and convert to America/New_York. This happens FIRST:
     no session, date or roll logic ever sees a UTC timestamp.
  3. Keep outrights only (calendar spreads such as MESH6-MESM6 are dropped).
  4. Front month by volume crossover, decided WITHOUT look-ahead: the front for
     session D+1 is the contract with the most volume in session D, and the
     series never steps back to an earlier expiry. A session is the Globex
     trading day, 18:00 ET the prior evening to 17:00 ET.
  5. Roll handling: no price adjustment of any kind. The roll day (the session
     whose volume triggers the switch, still traded in the old contract) and the
     first session of the new contract are marked roll_skip. Their bars stay in
     the files so the next session's profile can be built from the new contract.
  6. 5-min RTH bars: 1-min bars floored to 5 minutes, labelled by OPEN time,
     09:30 to 16:00 ET (13:00 on early-close days), NYSE holidays dropped.

Outputs (data/processed/):
  mes_1m.parquet      front-month 1-min bars, all hours (profile input)
  mes_5m_rth.parquet  5-min RTH bars (strategy input)
  sessions.parquet    one row per RTH day: contract, bar counts, half_day,
                      roll_skip, skip_reason, tradeable
  rolls.parquet       one row per roll: roll session, old, new, first new session

    python -m src.load              # build, then print the validation report
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MONTH_CODES = "FGHJKMNQUVXZ"
SESSION_SHIFT = pd.Timedelta(hours=6)       # 18:00 ET + 6h = 00:00 of the session date


# ----------------------------------------------------------------- config --
def load_config(path: str | Path | None = None) -> dict:
    return json.loads(Path(path or ROOT / "config.json").read_text())


# -------------------------------------------------------------------- read --
def read_raw(raw_dir: Path, expected_columns: list[str]) -> pd.DataFrame:
    files = sorted(p for p in raw_dir.iterdir() if p.name.endswith((".zip", ".csv", ".csv.zst")))
    if not files:
        raise FileNotFoundError(f"no .zip / .csv / .csv.zst files in {raw_dir}")
    frames = [pd.read_csv(p) for p in files]        # pandas infers zip / zstd compression
    df = pd.concat(frames, ignore_index=True)
    missing = [c for c in expected_columns if c not in df.columns]
    if missing:
        raise ValueError(f"raw data missing columns {missing}")
    return df


def to_market_tz(df: pd.DataFrame, source_tz: str, market_tz: str) -> pd.DataFrame:
    """ts_event (ISO 8601) -> tz-aware index in market_tz. The only place timestamps change zone."""
    ts = pd.to_datetime(df["ts_event"], utc=(source_tz == "UTC"))
    if source_tz != "UTC":
        ts = ts.dt.tz_localize(source_tz)
    out = df.drop(columns=["ts_event"]).set_index(ts.dt.tz_convert(market_tz).rename("ts"))
    return out.sort_index(kind="stable")


# --------------------------------------------------------------- contracts --
def is_outright(symbol: str, root: str) -> bool:
    return re.fullmatch(rf"{re.escape(root)}[{MONTH_CODES}]\d{{1,2}}", symbol) is not None


def expiry_key(symbol: str, root: str, first_year: int) -> tuple[int, int]:
    """'MESZ0' -> (2020, 12). A one-digit year is the first year >= first_year - 1 ending in it."""
    m = re.fullmatch(rf"{re.escape(root)}([{MONTH_CODES}])(\d{{1,2}})", symbol)
    if not m:
        raise ValueError(f"not an outright: {symbol}")
    code, yy = m.groups()
    if len(yy) == 2:
        year = 2000 + int(yy)
    else:
        year = first_year - first_year % 10 + int(yy)
        if year < first_year - 1:
            year += 10
    return year, MONTH_CODES.index(code) + 1


def session_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Globex trading date from WALL-CLOCK time: 18:00 ET on day D-1 belongs to session D."""
    return (index.tz_localize(None) + SESSION_SHIFT).normalize()


def choose_front(session_volume: pd.DataFrame, root: str, first_year: int) -> tuple[pd.Series, pd.DataFrame]:
    """session_volume: index = session date (sorted), columns = outright symbols, values = volume.

    front[D0] = the leader of the first session (no earlier data exists).
    front[D+1] = the leader of session D among contracts expiring no earlier than front[D].
    Uses only volume already traded, so no session's contract depends on its own or later volume.
    Returns (front by session, rolls) where each roll row is
    {roll_session, old, new, first_new_session}."""
    sessions = list(session_volume.index)
    key = {s: expiry_key(s, root, first_year) for s in session_volume.columns}
    front = {sessions[0]: session_volume.iloc[0].idxmax()}
    rolls = []
    for prev, nxt in zip(sessions[:-1], sessions[1:]):
        cur = front[prev]
        vol = session_volume.loc[prev]
        eligible = vol[[s for s in vol.index if key[s] >= key[cur]]]
        leader = eligible.idxmax() if eligible.max() > 0 else cur
        if leader != cur and vol[leader] > vol[cur]:
            rolls.append({"roll_session": prev, "old": cur, "new": leader, "first_new_session": nxt})
            front[nxt] = leader
        else:
            front[nxt] = cur
    return pd.Series(front, name="contract"), pd.DataFrame(rolls)


# ---------------------------------------------------------------- calendar --
def _observed(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1) if d.weekday() == 5 else d + dt.timedelta(days=1) if d.weekday() == 6 else d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    return dt.date(year, month, (h + l_ - 7 * m + 114) % 31 + 1)


SPECIAL_CLOSURES = {dt.date(2025, 1, 9)}     # national day of mourning, President Carter


def nyse_holidays(year: int) -> set[dt.date]:
    """Full-day NYSE closures. New Year's Day on a Saturday is not observed on the Friday."""
    jan1 = dt.date(year, 1, 1)
    days = {
        jan1 + dt.timedelta(days=1) if jan1.weekday() == 6 else jan1,
        _nth_weekday(year, 1, 0, 3),                 # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                 # Presidents' Day
        _easter(year) - dt.timedelta(days=2),        # Good Friday
        _last_weekday(year, 5, 0),                   # Memorial Day
        _observed(dt.date(year, 7, 4)),              # Independence Day
        _nth_weekday(year, 9, 0, 1),                 # Labor Day
        _nth_weekday(year, 11, 3, 4),                # Thanksgiving
        _observed(dt.date(year, 12, 25)),            # Christmas
    }
    if year >= 2022:
        days.add(_observed(dt.date(year, 6, 19)))   # Juneteenth
    days |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return {d for d in days if d.weekday() < 5}


def nyse_half_days(year: int) -> set[dt.date]:
    """13:00 ET closes: day after Thanksgiving; July 3 and Dec 24 when they are
    weekdays and not themselves holidays."""
    hol = nyse_holidays(year)
    days = {_nth_weekday(year, 11, 3, 4) + dt.timedelta(days=1)}
    for d in (dt.date(year, 7, 3), dt.date(year, 12, 24)):
        if d.weekday() < 5 and d not in hol and _observed(dt.date(year, d.month, d.day + 1)) != d:
            days.add(d)
    return days


# ---------------------------------------------------------------- resample --
def _hhmm(s: str) -> dt.time:
    return dt.datetime.strptime(s, "%H:%M").time()


def rth_5m(front_1m: pd.DataFrame, rth_start: str, rth_end: str, half_day_end: str,
           half_days: set[dt.date], holidays: set[dt.date], bar: str) -> pd.DataFrame:
    """5-min RTH bars from front-month 1-min bars, labelled by bar OPEN time."""
    idx = front_1m.index
    t = idx.time
    d = idx.date
    start, end, half_end = _hhmm(rth_start), _hhmm(rth_end), _hhmm(half_day_end)
    day_end = np.where(np.isin(d, list(half_days)), half_end, end)
    keep = (t >= start) & (t < day_end) & ~np.isin(d, list(holidays))
    rth = front_1m[keep]
    g = rth.groupby(rth.index.floor(bar))
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(),
                        "n_1m": g["open"].size(), "contract": g["contract"].first()})
    out.index.name = "ts"
    out["date"] = out.index.tz_localize(None).normalize()
    return out


# ------------------------------------------------------------------- build --
def build(cfg: dict, root: Path = ROOT) -> dict:
    ds, p, roll_cfg = cfg["data_source"], cfg["params"], cfg["contract_roll"]
    raw = read_raw(root / ds["raw_dir"], ds["expected_columns"])
    n_raw = len(raw)
    bars = to_market_tz(raw, ds["source_tz"], p["market_tz"])                     # step 2: FIRST
    outr = bars[bars["symbol"].map(lambda s: is_outright(s, ds["symbol"]))].copy()
    outr["session_date"] = session_dates(outr.index)

    first_year = outr.index[0].year
    vol = outr.groupby(["session_date", "symbol"])["volume"].sum().unstack(fill_value=0).sort_index()
    front, rolls = choose_front(vol, ds["symbol"], first_year)
    front_1m = outr[outr["symbol"].to_numpy() == front.reindex(outr["session_date"]).to_numpy()]
    front_1m = front_1m.rename(columns={"symbol": "contract"})
    dup = int(front_1m.index.duplicated().sum())
    if dup:
        raise ValueError(f"{dup} duplicate timestamps in the front-month series")

    years = range(front_1m.index[0].year, front_1m.index[-1].year + 1)
    holidays = set().union(*(nyse_holidays(y) for y in years))
    half_days = set().union(*(nyse_half_days(y) for y in years))
    rth_end = p["rth_full"][1]
    m5 = rth_5m(front_1m, p["rth_full"][0], rth_end, p["rth_half_day_end"], half_days, holidays,
                p["bar_size_strategy"])

    # one row per RTH day
    rth1 = front_1m[(front_1m.index.time >= _hhmm(p["rth_full"][0])) & (front_1m.index.time < _hhmm(rth_end))
                    & ~np.isin(front_1m.index.date, list(holidays))]
    rth1 = rth1[~(np.isin(rth1.index.date, list(half_days)) & (rth1.index.time >= _hhmm(p["rth_half_day_end"])))]
    g5 = m5.groupby("date")
    ts5 = m5.index.to_series(index=m5["date"]).groupby(level=0)
    sess = pd.DataFrame({"contract": g5["contract"].first(), "n_5m": g5.size(),
                         "first_bar": ts5.first().dt.strftime("%H:%M %Z"),
                         "last_bar": ts5.last().dt.strftime("%H:%M %Z"),
                         "first_bar_utc": ts5.first().dt.tz_convert("UTC").dt.strftime("%H:%M UTC")})
    sess["n_1m_rth"] = rth1.groupby(rth1.index.tz_localize(None).normalize()).size().reindex(sess.index)
    sess["half_day"] = [d.date() in half_days for d in sess.index]
    skip = {}
    for r in rolls.itertuples():
        skip[pd.Timestamp(r.roll_session)] = f"roll day ({r.old} -> {r.new})"
        skip[pd.Timestamp(r.first_new_session)] = f"first session of {r.new}"
    sess["roll_skip"] = [d in skip for d in sess.index]
    sess["skip_reason"] = [skip.get(d) for d in sess.index]
    sess["tradeable"] = ~sess["roll_skip"]
    sess.index.name = "date"

    out = root / "data" / "processed"
    out.mkdir(parents=True, exist_ok=True)
    front_1m.to_parquet(out / "mes_1m.parquet")
    m5.to_parquet(out / "mes_5m_rth.parquet")
    sess.to_parquet(out / "sessions.parquet")
    rolls.to_parquet(out / "rolls.parquet")
    return {"n_raw": n_raw, "n_outright": len(outr), "front_1m": front_1m, "m5": m5, "sessions": sess,
            "rolls": rolls, "holidays": holidays, "half_days": half_days, "cfg": cfg}


# ---------------------------------------------------------------- validate --
def validate(res: dict, n_sample: int = 10, seed: int = 7) -> str:
    sess, rolls, m5 = res["sessions"], res["rolls"], res["m5"]
    full = sess[~sess["half_day"]]
    lines = [f"raw rows {res['n_raw']:,}; outright rows {res['n_outright']:,}; "
             f"front-month 1-min rows {len(res['front_1m']):,}",
             f"RTH days {len(sess):,} ({sess.index[0].date()} to {sess.index[-1].date()}); "
             f"5-min RTH bars {len(m5):,}", ""]
    sample = sess.sample(n=min(n_sample, len(sess)), random_state=seed).sort_index()
    lines.append(f"{n_sample} random days (seed {seed}):")
    lines.append(sample[["contract", "n_1m_rth", "n_5m", "first_bar", "last_bar", "half_day", "roll_skip"]]
                 .to_string())
    lines.append("")
    ok78 = int((full["n_5m"] == 78).sum())
    ok390 = int((full["n_1m_rth"] == 390).sum())
    lines.append(f"full days: {len(full):,}; with 78 five-min bars: {ok78:,}; with 390 one-min bars: {ok390:,}")
    bad = full[(full["n_5m"] != 78) | (full["n_1m_rth"] != 390)]
    if len(bad):
        lines.append("full days NOT at 78 / 390 (listed, not dropped; March 2020 = market-wide "
                     "circuit-breaker halts, real missing minutes):")
        lines.append(bad[["contract", "n_1m_rth", "n_5m", "first_bar", "last_bar"]].to_string())
    half = sess[sess["half_day"]]
    lines.append(f"early-close days (13:00 ET): {len(half)}; 5-min bar counts {sorted(half['n_5m'].unique())}")
    not930 = sess[~sess["first_bar"].str.startswith("09:30")]
    lines.append(f"first bar at 09:30 ET: {len(sess) - len(not930):,} of {len(sess):,} days")
    if len(not930):
        lines.append(not930[["contract", "first_bar", "n_5m"]].to_string())
    tz = sorted({b[-3:] for b in sess["first_bar"]})
    lines.append(f"time zone labels on first bars: {tz} (EST in winter, EDT in summer)")
    utc = sess.groupby(sess["first_bar"].str[-3:])["first_bar_utc"].unique()
    lines.append("09:30 ET in UTC, by zone (proves the conversion follows DST): "
                 + "; ".join(f"{z}: {list(v)}" for z, v in utc.items()))
    around = sess.loc["2024-03-06":"2024-03-13", ["first_bar", "first_bar_utc"]]
    lines.append("across the 2024-03-10 DST change:")
    lines.append(around.to_string())
    lines.append("")
    yrs = (sess.index[-1] - sess.index[0]).days / 365.25
    lines.append(f"rolls: {len(rolls)} ({len(rolls) / yrs:.1f}/yr); RTH days skipped: "
                 f"{int(sess['roll_skip'].sum())} ({sess['roll_skip'].sum() / yrs:.1f}/yr)")
    lines.append(rolls.assign(roll_session=rolls["roll_session"].dt.date,
                              first_new_session=rolls["first_new_session"].dt.date).to_string(index=False))
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    res = build(load_config(a.config))
    print(validate(res))


if __name__ == "__main__":
    main()
