# fvg_lab

A backtesting framework for FVG (fair value gap) and opening-range strategies,
built so that the default outcome is **killing your idea**, not confirming it.

```
pip install pandas numpy scipy
python run_example.py
```

The example runs on a synthetic random walk. There is nothing in that data to
find, so the output shows you what a correct null result looks like before you
ever point this at real prices.

---

## What the example prints, and why it matters

From the run on 350 sessions of random-walk data:

```
randomization test p_value      0.997      <- expectancy is worse than shuffled noise
best of 144 variants            -0.067 R   <- the best thing found in a 144-way search
deflated Sharpe vs 145 trials    0.000     <- want > 0.95
```

And the walk-forward, which is the part to internalise:

```
             is_expectancy_R   oos_expectancy_R
fold 4            +0.078            -0.107
fold 5            +0.159            -0.569
fold 6            +0.219            -0.042

in-sample -> OOS decay: +0.214 R
```

Three folds where parameter selection found something that looked good
in-sample, and all three lost money out-of-sample. That gap **is** overfitting,
measured. On real data you will see the same pattern unless you have a genuine
edge, and this is how you tell the difference.

---

## The workflow

1. **Write the rules down** as a `Params` object. Everything variable is a
   parameter so the search is countable.
2. **Run against `data.synth()` first.** Anything profitable on a random walk
   is a bug or a lucky draw.
3. **Split the holdout and leave it alone.** `data.split_holdout()` gives you
   dev and holdout. Open the holdout once, at the end. Twice and it is gone.
4. **Sweep on dev**, with a `TrialLog`. Every variant is logged permanently.
5. **Check the deflated Sharpe**, not the Sharpe. It discounts your result by
   how many variants you tried.
6. **Walk forward with purge and embargo.** Select in-sample, apply forward.
   Watch the decay.
7. **Double the costs.** `costs.double()`. If the edge dies, it was never there.
8. **Paper trade.** Then, only then, the holdout.

---

## Module map

| file | what it does |
|---|---|
| `data.py` | loading, validation, session tagging, synthetic null data, holdout split |
| `strategy.py` | `Params`, FVG detection, entry/exit rules, the `Bars` numpy view |
| `engine.py` | event-driven loop, cost model, position sizing, trade records |
| `metrics.py` | expectancy, PSR, **deflated Sharpe**, drawdown, randomization test |
| `trials.py` | trial log, parameter sweep, purged walk-forward |

---

## The strategy, as implemented

**FVG**, three-bar definition, detected at the close of bar `i`:

- bullish: `low[i] > high[i-2]` → unfilled zone `[high[i-2], low[i]]`
- bearish: `high[i] < low[i-2]` → unfilled zone `[high[i], low[i-2]]`

**First candle** is the opening range built from the first `first_candle_n`
bars of the session. It supplies a directional bias (did the OR close above
its open) and a breakout level.

**Entry modes:**

- `fvg_retest` — price trades back into a gap created on an earlier bar
- `first_candle_break` — close beyond the opening range
- `fvg_plus_break` — both conditions required

**Exits:** structural stop (far edge of the gap, or the OR, plus a buffer),
target at `target_r` multiples of initial risk, flat at the session close.

None of these are recommendations. They are a starting parameterization so you
can explore the idea space without hand-editing code and losing track of what
you tried.

---

## The anti-self-deception machinery

**No lookahead.** A decision made at the close of bar `t` fills at the **open
of bar `t+1`**. ATR is shifted so bar `t` only sees bars before it. Intents
never cross a session boundary.

**Pessimistic fills.** When a bar's range contains both the stop and the
target, the **stop is assumed to hit first**. With OHLC data you cannot know
the order, and optimism here is the single most common way an intraday
backtest fakes a positive result.

**Costs before metrics.** Half-spread plus slippage on every side, commission
per unit per side. `costs.double()` is the stress test.

**Trial counting.** `TrialLog` never resets. `deflated_sharpe()` uses the
cumulative count and the dispersion of Sharpes across trials, so the bar rises
every time you test another variant. This is the whole point — it turns "I
tried a lot of things" into a number that penalises you for it.

**Purge and embargo.** `purged_walk_forward` drops `embargo_days` of sessions
between train and test so volatility clustering cannot leak across the boundary.
Standard k-fold is invalid on time series and is not offered.

---

## Diagnostics that are not performance numbers

Two fields in `summarize()` exist to tell you the backtest is unreliable:

**`pct_same_bar_exit`** — trades that opened and closed inside one bar. These
are unresolvable with OHLC data. A high share means you are measuring your bar
size, not your idea. `min_stop_atr` rejects stops tighter than a typical bar's
range for exactly this reason; raise it, or go to finer bars.

**`skipped_undersized`** — signals that never became trades because one
contract risked more than the risk budget allowed. A large number means the
account is too small for the instrument. The example uses MES (`point_value=5`)
rather than ES (`50`) for this reason; on ES with a $25k account and a 0.5%
risk budget, nearly every signal is unfillable.

---

## Using real data

```python
from fvg_lab import data
df = data.add_session(data.load_csv("es_5min.csv"))   # timestamp,open,high,low,close,volume
dev, holdout = data.split_holdout(df, holdout_frac=0.30)
```

`load_csv` assumes the timestamp is the bar's **open** time. If your vendor
stamps bars with the close time, shift it before loading — getting this wrong
is a silent one-bar lookahead that will make almost anything look profitable.

`validate()` runs automatically and rejects unsorted indexes, duplicate
timestamps, and bars that violate OHLC bounds.

Set `Costs` to your actual instrument. The defaults are ES-shaped; the example
overrides to MES.

---

## Honest limits

- **Bar data cannot resolve intrabar sequence.** The stop-first rule is
  conservative but it is still an assumption. Tight-stop intraday strategies
  are the worst case for this, and that is most FVG strategies.
- **Synthetic data is a smoke test, not a benchmark.** It has no volatility
  clustering, no session effects, no real microstructure.
- **Sample size is the binding constraint.** Distinguishing a true 55% win rate
  from 50% takes on the order of 400 trades. A sweep over a few hundred trades
  per variant is exploration, not evidence.
- **This framework cannot tell you a strategy works.** It can only tell you
  when one doesn't. That asymmetry is deliberate and is the correct way round.
