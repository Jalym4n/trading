"""fetch_crypto checks against fake exchange responses (no network)."""

import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.error
import zipfile
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_crypto as fc  # noqa: E402
from fvg_lab import data  # noqa: E402

T0 = fc._epoch("2024-01-01")


def _bar(ts):
    p = 40_000 + (ts - T0) / 300
    return ts, p, p + 5, p - 5, p + 1, 1.5  # ts, o, h, l, c, v


def fake_coinbase(missing=(), calls=None):
    def get(url):
        q = parse_qs(urlparse(url).query)
        a, b = fc._epoch(q["start"][0][:-1]), fc._epoch(q["end"][0][:-1])
        if calls is not None:
            calls.append((a, b))
        rows = []
        for ts in range(a, b + 1, 300):  # coinbase end is inclusive
            if ts in missing:
                continue
            t, o, h, l, c, v = _bar(ts)
            rows.append([t, l, h, o, c, v])  # coinbase order: time, low, high, open, close, volume
        return json.dumps(rows[::-1]).encode()  # newest first
    return get


def test_coinbase_order_paging_and_partial_bar():
    calls = []
    fc._get = fake_coinbase(missing={T0 + 300 * 10}, calls=calls)
    fc.time.sleep = lambda s: None
    out = os.path.join(tempfile.mkdtemp(), "btc.csv")
    now = T0 + 300 * 1000 + 120  # mid-way through bar 1000: it must not be written
    rep = fc.download("coinbase", "BTC-USD", "5m", "2024-01-01", None, out, now=now)
    assert rep["bars"] == 999                 # bars 0..999 minus the one gap
    assert rep["missing_bars"] == 1
    assert len(calls) == 4                    # 1000 bars / 300 per request
    df = data.load_csv(out, tz="UTC")
    assert df.index[0].timestamp() == T0
    assert df.index[-1].timestamp() == T0 + 300 * 999   # last CLOSED bar
    _, o, h, l, c, v = _bar(T0)
    row = df.iloc[0]
    assert (row.open, row.high, row.low, row.close) == (o, h, l, c)  # columns not swapped
    df = data.add_session(df, "00:00", "24:00")
    assert len(df) == 999


def test_binance_ms_and_us_timestamps_header_and_checksum():
    def month_zip(y, m, unit, header):
        start = fc._epoch(f"{y}-{m:02d}-01")
        lines = ["open_time,open,high,low,close,volume,close_time"] if header else []
        for k in range(3):
            ts, o, h, l, c, v = _bar(start + 300 * k)
            lines.append(f"{ts * unit},{o},{h},{l},{c},{v},0")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x.csv", "\n".join(lines))
        return buf.getvalue()

    files = {(2024, 12): month_zip(2024, 12, 1000, False),        # milliseconds
             (2025, 1): month_zip(2025, 1, 1_000_000, True)}      # microseconds + header
    sums = {k: hashlib.sha256(b).hexdigest() for k, b in files.items()}  # as published

    def get(url):
        name = url.rsplit("/", 1)[1]
        ym = name.split("-5m-")[1][:7]
        key = (int(ym[:4]), int(ym[5:7]))
        if key not in files:
            raise urllib.error.HTTPError(url, 404, "nf", None, None)
        blob = files[key]
        if url.endswith(".CHECKSUM"):
            return f"{sums[key]}  {name}".encode()
        return blob

    fc._get = get
    out = os.path.join(tempfile.mkdtemp(), "b.csv")
    rep = fc.download("binance", "BTCUSDT", "5m", "2024-12-01", "2025-03-01", out,
                      now=fc._epoch("2025-03-01"))
    assert rep["bars"] == 6
    df = data.load_csv(out, tz="UTC")
    assert str(df.index[3]) == "2025-01-01 00:00:00+00:00"   # us decoded, not year 50000

    b = bytearray(files[(2024, 12)])
    b[len(b) // 2] ^= 0xFF                                   # corrupt one byte
    files[(2024, 12)] = bytes(b)
    try:
        fc.download("binance", "BTCUSDT", "5m", "2024-12-01", "2025-01-01", out,
                    now=fc._epoch("2025-03-01"))
        raise AssertionError("checksum mismatch not caught")
    except RuntimeError as e:
        assert "checksum" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
