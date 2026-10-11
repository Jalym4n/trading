"""
Monte Carlo of the Topstep $50K Trading Combine.

Models the actual rule stack:
  - trailing Maximum Loss Limit (end-of-day only, ratchets on EOD highs,
    freezes at the starting balance once EOD equity reaches start + MLL)
  - daily loss limit (halts the day, does not end the Combine)
  - consistency rule (best single day <= 50% of total profit at pass time)
  - profit target

Trade model: fixed-R outcomes, 2R target / 1R stop, matching the
fvg_lab Params default of target_r = 2.0.
"""
import numpy as np

START        = 50_000.0
TARGET       = 3_000.0     # profit target
MLL          = 2_000.0     # trailing max loss limit
DLL          = 1_000.0     # daily loss limit
CONSISTENCY  = 0.50        # best day <= 50% of total profit
TRADES_DAY   = 3
MAX_DAYS     = 500         # give up after ~24 months
SUB_MONTHLY  = 49.0
DAYS_MONTH   = 21
ACTIVATION   = 149.0


def win_rate_for(expectancy_R, target_r=2.0):
    """2R win / 1R loss: e = w*target_r - (1-w) -> w = (e+1)/(target_r+1)"""
    return (expectancy_R + 1.0) / (target_r + 1.0)


def run_one(rng, expectancy_R, risk_dollars, target_r=2.0):
    w = win_rate_for(expectancy_R, target_r)
    bal      = START
    floor    = START - MLL
    frozen   = False
    best_day = 0.0
    for day in range(1, MAX_DAYS + 1):
        day_pnl = 0.0
        for _ in range(TRADES_DAY):
            if day_pnl <= -DLL:
                break                                   # daily limit halts the day
            r = target_r if rng.random() < w else -1.0
            day_pnl += r * risk_dollars
        bal += day_pnl
        if bal <= floor:
            return False, day, bal                      # MLL breach = permanent fail
        best_day = max(best_day, day_pnl)
        # end-of-day trail
        if not frozen:
            if bal >= START + MLL:
                floor, frozen = START, True
            else:
                floor = max(floor, bal - MLL)
        profit = bal - START
        if profit >= TARGET and best_day <= CONSISTENCY * profit:
            return True, day, bal
    return False, MAX_DAYS, bal                         # timed out


def sweep(expectancies, risks, n=20_000, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for e in expectancies:
        for risk in risks:
            passes, days_pass, days_fail = 0, [], []
            for _ in range(n):
                ok, d, _ = run_one(rng, e, risk)
                if ok:
                    passes += 1; days_pass.append(d)
                else:
                    days_fail.append(d)
            rate = passes / n
            med_pass = float(np.median(days_pass)) if days_pass else float("nan")
            # expected subscription spend across ALL attempts
            all_days = days_pass + days_fail
            months   = np.ceil(np.array(all_days) / DAYS_MONTH)
            spend    = months * SUB_MONTHLY + (np.array(
                           [ACTIVATION]*len(days_pass) + [0.0]*len(days_fail)))
            rows.append(dict(expectancy_R=e, risk=risk, pass_rate=rate,
                             med_days_to_pass=med_pass,
                             mean_spend=float(spend.mean())))
    return rows


if __name__ == "__main__":
    exps  = [0.00, 0.03, 0.05, 0.10, 0.20]
    risks = [50, 100, 200, 400]
    rows = sweep(exps, risks)
    print(f"{'exp(R)':>7} {'risk/trade':>11} {'pass%':>7} {'med days':>9} {'E[spend]':>9}")
    print("-" * 48)
    for r in rows:
        md = "   -" if np.isnan(r['med_days_to_pass']) else f"{r['med_days_to_pass']:.0f}"
        print(f"{r['expectancy_R']:>7.2f} {r['risk']:>10.0f}$ {r['pass_rate']*100:>6.1f}% "
              f"{md:>9} {r['mean_spend']:>8.0f}$")
