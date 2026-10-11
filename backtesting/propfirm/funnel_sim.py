"""Full Topstep funnel: Combine -> Express Funded -> first payout."""
import numpy as np
from combine_sim import win_rate_for, START, MLL, DLL, TRADES_DAY

SUB, ACT, DAYS_M = 49.0, 149.0, 21

def stage(rng, e, risk, target, max_days, need_win_days=0, win_day_min=150.0,
          consistency=0.50, target_r=2.0):
    """Generic trailing-MLL stage. Returns (passed, days, best_day, profit)."""
    w = win_rate_for(e, target_r)
    bal, floor, frozen = START, START - MLL, False
    best_day, win_days = 0.0, 0
    for day in range(1, max_days + 1):
        pnl = 0.0
        for _ in range(TRADES_DAY):
            if pnl <= -DLL: break
            pnl += (target_r if rng.random() < w else -1.0) * risk
        bal += pnl
        if bal <= floor:
            return False, day, best_day, bal - START
        best_day = max(best_day, pnl)
        if pnl >= win_day_min: win_days += 1
        if not frozen:
            if bal >= START + MLL: floor, frozen = START, True
            else: floor = max(floor, bal - MLL)
        profit = bal - START
        if profit >= target and best_day <= consistency * profit \
           and win_days >= need_win_days:
            return True, day, best_day, profit
    return False, max_days, best_day, bal - START

def funnel(e, risk, n=20_000, seed=1):
    rng = np.random.default_rng(seed)
    passed_combine = paid = 0
    spend = np.zeros(n)
    for i in range(n):
        ok, d, _, _ = stage(rng, e, risk, 3_000.0, 400)
        spend[i] = np.ceil(d / DAYS_M) * SUB
        if not ok:
            continue
        passed_combine += 1
        spend[i] += ACT
        # Express Funded: survive the same trailing MLL, 5 winning days >= $150,
        # and reach a withdrawable profit. Modelled as $2,000 profit + 5 win days.
        ok2, d2, _, _ = stage(rng, e, risk, 2_000.0, 200,
                              need_win_days=5, consistency=0.60)
        spend[i] += np.ceil(d2 / DAYS_M) * 0.0   # funded stage has no monthly fee
        if ok2:
            paid += 1
    return dict(expectancy_R=e, risk=risk,
                combine=passed_combine / n, paid=paid / n,
                mean_spend=float(spend.mean()))

print(f"{'exp(R)':>7} {'risk':>6} {'pass combine':>13} {'ever paid':>10} {'E[spend]':>9} {'breakeven payout':>17}")
print("-" * 70)
for e in [-0.10, -0.05, 0.00, 0.03, 0.05, 0.10]:
    for risk in [100, 200]:
        r = funnel(e, risk)
        be = r['mean_spend'] / r['paid'] if r['paid'] > 0 else float('inf')
        be_s = "n/a" if r['paid'] == 0 else f"${be:,.0f}"
        print(f"{e:>7.2f} {risk:>5.0f}$ {r['combine']*100:>12.1f}% "
              f"{r['paid']*100:>9.1f}% {r['mean_spend']:>8.0f}$ {be_s:>17}")
