# Lucid $25k Viability Target — the bar every strategy must clear

This file defines the OUTCOME a mechanical strategy's backtest must produce to be
worth funding on a Lucid Flex $25k evaluation. Use it two ways:

1. As a pass/fail checklist to judge any backtest result.
2. As a brief to hand another model (Fable): "design a mechanical, falsifiable
   strategy whose honest backtest hits these targets," then test its proposals in
   the harness — never trust them untested.

All numbers assume MES/MNQ micros, 1 contract, on the Lucid Flex $25k:

* Profit target: $1,250
* Max loss limit (MLL): $1,000 total room, EOD trailing
* Consistency: best day <= 50% of total profit (eval)
* Payout: 5 profitable days before first payout
* VERIFY these on Lucid's help center before funding — terms change.

======================================================================
THE HARD GATES (fail any one = not viable, no exceptions)

GATE 1 — FREQUENCY
>= 3 trades per week on average (ideally 1-2 per day). Why: you need ~5
profitable days to get paid, and enough trades that a normal losing streak
doesn't strand you for weeks. 10 trades/year is an automatic fail — it can't
clear the eval in any reasonable time and can't produce a measurable edge.
Measure: total trades / weeks in sample.

GATE 2 — MAX DRAWDOWN vs THE $1,000 WALL
Peak-to-trough drawdown (net of costs, 1 contract) must stay under ~$600. Why:
the MLL is $1,000 of TOTAL room. A strategy whose normal variance eats $600
leaves almost no margin for a bad run before liquidation. If max DD in the
backtest approaches or exceeds $1,000, the account dies before the edge pays
off — full stop, regardless of long-run expectancy.
Measure: max drawdown in $ on the equity curve, net of costs.

GATE 3 — POSITIVE NET EXPECTANCY, ACROSS WINDOWS
Expectancy per trade > 0 AFTER commission + slippage, and positive in a
MAJORITY of walk-forward test windows (not one lucky blob). Why: an edge that
only shows up gross, or in a single year, isn't an edge.
Measure: (win% x avgWin) - (loss% x avgLoss), net, per walk-forward window.

GATE 4 — SURVIVES THE HOLDOUT
Still positive on the 2026 holdout (run once, at the end, on one finalist).
Why: this is the only test on data the search never touched. If it dies here,
every earlier positive was overfitting.

======================================================================
THE SOFT TARGETS (shape of a result that actually gets paid)

CONSISTENCY (Lucid's 50% rule)
No single day should contribute > 40% of total profit (aim under 40 to leave
margin under the 50% rule). A strategy that makes its money in 2 huge days
FAILS the eval even if total P&L hits the target. Steady beats spiky here.
Measure: largest single-day P&L / total profit needed to pass.

PATH TO $1,250 IN REASONABLE TIME
At the strategy's net expectancy and frequency, reaching +$1,250 should take a
few weeks, not many months. Estimate: trades-to-target = 1250 / (net
expectancy per trade). If that's hundreds of trades, the eval takes too long
to be practical.

WIN-RATE / R:R THAT YOU CAN STOMACH
Any combination with positive expectancy works mathematically, but on a $1,000
buffer, long losing streaks are dangerous. Prefer either:
- higher win rate (>=55%) with ~1:1, OR
- ~45% win rate with >=1.8:1,
and in BOTH cases, max consecutive losses in the backtest must fit under the
drawdown wall at your risk size. Count the worst streak and multiply the
per-trade risk — it must stay under ~$600.

COST ROBUSTNESS
Net expectancy must survive doubled slippage. If halving the edge by assuming
worse fills kills it, it's fee-sized and won't survive live execution.

======================================================================
PASS/FAIL SCORECARD (fill this in for every strategy)

[ ] GATE 1  Frequency >= 3 trades/week ............ actual: ____ /wk
[ ] GATE 2  Max DD < ~$600 (hard wall $1,000) ..... actual: $____
[ ] GATE 3  Net expectancy > 0, majority windows .. actual: ____ ($/trade)
[ ] GATE 4  Survives 2026 holdout ................. actual: ____
[ ] SOFT    Largest day < 40% of target ........... actual: ____ %
[ ] SOFT    Trades-to-$1,250 is weeks not months .. actual: ____ trades
[ ] SOFT    Worst streak x risk < ~$600 ........... actual: $____
[ ] SOFT    Survives doubled slippage ............. actual: ____

ALL FOUR GATES must pass. Soft targets shape confidence and real-world payout
odds. A result that clears the gates but fails consistency may pass the
backtest and still fail the live eval.

======================================================================
BRIEF FOR FABLE (hand it the outcome, ask for the path)

Paste this to Fable:

"Design a MECHANICAL, fully-specified, falsifiable intraday strategy for MES
and MNQ futures (1-min data, RTH, cash-session focus) whose honest backtest
could plausibly hit ALL of the following on a Lucid $25k account, 1 contract:

    - At least 3 trades per week (frequency is a hard requirement).
    - Max drawdown under $600, net of commission and slippage. The account has
      only $1,000 of total room on an end-of-day trailing drawdown.
    - Positive expectancy per trade AFTER costs, holding up across multiple
      yearly test windows, not just one.
    - No single day contributing more than ~40% of total profit (a 50%
      consistency rule applies).
    - A realistic path to +$1,250 in a few weeks of trading.

Give me EXACT, unambiguous rules: entry condition, stop, target, session
window, any filters — specified precisely enough that two people would code
it identically, with no discretion and no look-ahead (every decision uses
only data available at that bar). Explain the MECHANISM: who is on the other
side of the trade and why the edge would persist. Do NOT optimize parameters
to past data; give round, defensible defaults. I will backtest your proposal
in my own harness with walk-forward validation and an untouched holdout — so
propose a hypothesis I can test, and tell me what result would DISPROVE it."

Then: code its proposal as a strategy module, register it, run training +
walk-forward, log it to the trial counter. If it clears the gates on those,
AND you've picked it as your single finalist, run the holdout once.

NEVER fund a Lucid account on a strategy that hasn't cleared all four gates
on data the search never touched. A confident "no strategy clears these" is a
real answer and saves the eval fee.
