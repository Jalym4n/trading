"""
Rank CME index micros for a single-instrument futures strategy.

The decisive metric is NOT dollar volatility and NOT friction in isolation.
It is friction expressed in R, i.e. friction divided by an ATR-scaled stop,
because that is the share of gross edge the instrument takes before you start.

Vol multiples vs SPX are long-run estimates and MUST be replaced with measured
ATR once real data is loaded. Everything else is contract spec or arithmetic.
"""
import math

VIX          = 15.72          # from the Oct 7 chart
BARS_RTH_5M  = 78
TRADING_DAYS = 252
COMM_RT      = 1.00           # round-turn commission per micro, discount broker

SPX_DAILY_SIGMA = VIX / math.sqrt(TRADING_DAYS) / 100   # 0.99%

INSTRUMENTS = [
    # name, index level, $/point, tick(points), vol multiple vs SPX
    ("MES  S&P 500",     7_800.0, 5.00, 0.25, 1.00),
    ("MNQ  Nasdaq 100", 31_160.0, 2.00, 0.25, 1.30),
    ("MYM  Dow 30",     51_185.0, 0.50, 1.00, 0.88),
    ("M2K  Russell 2k",  2_808.0, 5.00, 0.10, 1.30),
]

rows = []
for name, level, mult, tick_pts, volx in INSTRUMENTS:
    notional   = level * mult
    tick_val   = tick_pts * mult
    daily_sig  = level * SPX_DAILY_SIGMA * volx * mult      # $ per contract per day
    sig_5m     = daily_sig / math.sqrt(BARS_RTH_5M)         # $ per 5m bar (1 sigma)
    friction   = tick_val + COMM_RT                         # 1 tick spread + commission
    stop_1atr  = sig_5m                                     # stop scaled to the instrument
    drag_R     = friction / stop_1atr                       # <-- the decisive number
    rows.append(dict(name=name, notional=notional, tick_val=tick_val,
                     daily_sig=daily_sig, sig_5m=sig_5m, friction=friction,
                     stop=stop_1atr, drag_R=drag_R,
                     eff=daily_sig / friction))

print("CONTRACT ECONOMICS")
print(f"{'instrument':<17}{'notional':>10}{'tick$':>7}{'daily σ':>9}{'5m σ':>7}"
      f"{'frict':>7}{'σ/frict':>9}{'DRAG (R)':>10}")
print("-" * 76)
for r in sorted(rows, key=lambda x: x["drag_R"]):
    print(f"{r['name']:<17}{r['notional']:>9,.0f}${r['tick_val']:>6.2f}"
          f"{r['daily_sig']:>8.0f}${r['sig_5m']:>6.0f}${r['friction']:>6.2f}"
          f"{r['eff']:>9.0f}{r['drag_R']:>10.3f}")

print("\n\nWHAT THE DRAG COSTS YOU, by gross edge")
print(f"{'instrument':<17}" + "".join(f"{g:>12}" for g in
      ["gross .03", "gross .05", "gross .10", "gross .20"]))
print("-" * 65)
for r in sorted(rows, key=lambda x: x["drag_R"]):
    cells = ""
    for g in (0.03, 0.05, 0.10, 0.20):
        net = g - r["drag_R"]
        cells += f"{net:>+12.3f}"
    print(f"{r['name']:<17}{cells}")

print("\n\nSIZING")
print(f"{'instrument':<17}{'1 ctr risk':>12}{'% of $1,000':>13}{'ctrs for $200':>15}"
      f"{'granularity':>13}")
print("-" * 71)
for r in sorted(rows, key=lambda x: x["drag_R"]):
    n200 = 200.0 / r["stop"]
    print(f"{r['name']:<17}{r['stop']:>11.0f}${r['stop']/1000*100:>12.1f}%"
          f"{n200:>15.1f}{r['stop']:>12.0f}$")
