---
tags: [tutorial, monte-carlo, implementation, code-walkthrough]
---
# Part 5: Our Implementation — Code Walkthrough and Worked Example

[< Part 4: Pitfalls](04-pitfalls.md) | [Index](00-index.md)

---

This part bridges the theory from Parts 1–4 to the actual code in this project. We'll walk through the implementation, trace a worked example, and show how to make decisions from the output.

## File Map

| File | Role |
|------|------|
| `src/analysis/monte_carlo.py` | Core MC logic: bootstrap, metrics, plots, report |
| `main_runner/run_monte_carlo.py` | Standalone CLI entry point |
| `main_runner/run_backtest_db.py` | Inline MC via `--mc` flag |
| `tests/analysis/test_monte_carlo.py` | 57 unit tests covering all functions |

## How to Run It

**After a backtest (standalone):**

```bash
# Default: 1,000 simulations, seed 42
python main_runner/run_monte_carlo.py \
  results/db/{start}_to_{end}_run-{date}/options/5min

# Custom simulation count
python main_runner/run_monte_carlo.py \
  results/db/{start}_to_{end}_run-{date}/options/5min \
  --n 5000 --seed 123
```

**Inline with backtest:**

```bash
python main_runner/run_backtest_db.py 2026-01-02 2026-03-30 --mc
```

**Output directory:**

```
{results_dir}/monte_carlo/
  mc_equity_fan.png       ← percentile band fan chart
  mc_distributions.png    ← 3x2 histogram grid
  mc_report.md            ← percentile table + interpretation
  mc_metrics.csv          ← raw data (1,000 rows x 7 metrics)
```

## Code Walkthrough

### Step 1: Load and Validate

```python
# monte_carlo.py — run_monte_carlo()
trade_log = pd.read_csv(f"{results_dir}/backtest.csv")
pnl_array = trade_log["pnl"].to_numpy(dtype=float)
```

Reads the CSV that the backtest engine produced. Extracts just the P&L column as a numpy array — nothing else is needed for the simulation.

**Guard rail:** If fewer than 5 trades, the function logs a warning and returns early. This prevents meaningless MC output from tiny samples (see Part 4, Pitfall 3).

### Step 2: Bootstrap Resampling

```python
# _simulate_equity_curves()
rng = np.random.default_rng(seed)
n_trades = len(pnl_array)
samples = rng.choice(pnl_array, size=(n_sim, n_trades), replace=True)
return initial_capital + np.cumsum(samples, axis=1)
```

This is the entire bootstrap in 3 lines:

1. **`rng.choice(..., replace=True)`** — Draws `n_sim * n_trades` values from `pnl_array` with replacement. Shape: `(1000, 76)`. Each row is one simulation's trade sequence.

2. **`np.cumsum(samples, axis=1)`** — Running sum along each row. Converts P&Ls to cumulative equity changes.

3. **`initial_capital + ...`** — Shifts to absolute equity levels.

Result: a `(1000, 76)` matrix where `equity_curves[b, k]` is the portfolio value of simulation `b` after trade `k`.

**Why `default_rng(seed)`?** Reproducibility. Same seed = same 1,000 simulations. This means you can re-run the analysis and get identical results, which is essential for debugging and comparison.

### Step 3: Compute Per-Simulation Metrics

```python
# _compute_mc_metrics()
sim_pnls = np.diff(
    np.hstack([np.full((n_sim, 1), initial_capital), equity_curves]),
    axis=1
)
```

Recovers per-trade P&Ls from equity levels (needed for win rate, profit factor). The `hstack` prepends initial capital so `diff` gives the correct first trade's P&L.

Then for each metric:

```python
# Total return
final_equity = equity_curves[:, -1]
total_return_pct = (final_equity / initial_capital - 1) * 100

# Max drawdown
running_max = np.maximum.accumulate(equity_curves, axis=1)
drawdown = (equity_curves - running_max) / running_max
max_dd = drawdown.min(axis=1) * 100

# Win rate
win_rate = (sim_pnls > 0).sum(axis=1) / n_trades * 100

# Profit factor
gross_profit = np.where(sim_pnls > 0, sim_pnls, 0).sum(axis=1)
gross_loss = np.abs(np.where(sim_pnls < 0, sim_pnls, 0).sum(axis=1))
profit_factor = gross_profit / gross_loss  # inf where gross_loss = 0
```

All operations are vectorized across the 1,000 simulations — no Python loops. The entire computation for 1,000 simulations of 76 trades takes ~2ms.

### Step 4: Max Consecutive Losses

This one can't be fully vectorized — it requires a sequential scan per simulation:

```python
# _max_consecutive_losses()
loss_mask = (sim_pnls < 0).astype(int)  # 1 where loss, 0 where win
current = np.zeros(n_sim, dtype=int)
best = np.zeros(n_sim, dtype=int)
for k in range(n_trades):
    current = np.where(loss_mask[:, k], current + 1, 0)
    best = np.maximum(best, current)
```

The loop iterates over trade index (76 iterations), but within each iteration, all 1,000 simulations are processed in parallel via numpy. This is O(n_trades) in the loop, O(n_sim) in each vectorized operation.

### Step 5: Risk of Ruin

```python
ruin_floor = ruin_floor_pct * initial_capital  # default: 50% of initial
ruined = (equity_curves.min(axis=1) < ruin_floor).astype(float)
```

For each simulation, checks if the equity *ever* dipped below the floor. Note: this checks the minimum equity along the entire path, not just the final value. A simulation that dropped to 40% of initial and recovered to 120% is still marked as "ruined."

### Step 6: Visualization

**Fan chart (`mc_equity_fan.png`):**

```python
percentiles = np.percentile(equity_curves, [5, 25, 50, 75, 95], axis=0)
```

Computes percentiles *at each trade index* across all 1,000 simulations, then:

- P5–P95 band: light blue fill (the full range excluding extremes)
- P25–P75 band: darker blue fill (the "typical" range)
- P50 line: the median path
- Actual path: orange line (your real backtest result)

**Distribution histograms (`mc_distributions.png`):**

3x2 grid of histograms for: total return, max drawdown, win rate, profit factor, Calmar ratio, max consecutive losses. Each includes a vertical orange line showing your actual value.

### Step 7: Report Generation

The markdown report (`mc_report.md`) contains a percentile table:

```
| Metric             | P5    | P25   | P50   | P75    | P95    | Actual | Rank |
|--------------------|-------|-------|-------|--------|--------|--------|------|
| Total Return (%)   | -1.2  | -0.1  | 0.3   | 0.7    | 1.5    | 0.24   | 45th |
| Max Drawdown (%)   | -2.1  | -1.0  | -0.5  | -0.3   | -0.1   | -0.15  | 72nd |
| Profit Factor      | 0.85  | 0.97  | 1.12  | 1.30   | 1.65   | 1.34   | 78th |
```

*(Numbers are illustrative — run MC on your actual results to see real values.)*

The interpretation section uses the profit factor rank to classify your result:

- **Rank >= 75th:** "Actual profit factor ranks above 75% of simulations — trade sequencing was favorable."
- **Rank 25th–75th:** "Actual profit factor is in the typical range — sequencing luck was neutral."
- **Rank <= 25th:** "Actual profit factor ranks below 25% of simulations — sequencing was unfavorable; the strategy's expected performance may be better."

## Worked Example: Interpreting Real Output

Let's trace through interpreting MC output for our 2026 YTD backtest.

**Input facts:**
- 76 trades, 34 wins / 42 losses
- Avg win: $42.47, avg loss: -$25.74
- Total P&L: +$363
- Profit factor: 1.34
- Max drawdown: -0.15%

**What to look for in the MC output:**

1. **Is the median return positive?** Check P50 of total return. If it's > 0, the strategy has positive expected value under resampling — the edge is real, not just lucky ordering.

2. **Is the actual profit factor within the IQR?** If your 1.34 ranks between P25 and P75, your ordering was neutral. If it's above P75, you got lucky — expect lower PF going forward.

3. **What's the P5 max drawdown?** This is your stress-test number. If P5 max DD is -3%, you should budget for 3% drawdowns even though you only experienced 0.15%.

4. **Risk of ruin?** With 1-contract sizing on $150k capital, ruin risk should be ~0% (the max possible loss per trade is small relative to capital). If it's not zero, something is wrong with your sizing.

5. **How wide is the return CI?** P5 return to P95 return. If it spans -2% to +3%, there's meaningful uncertainty. If it spans -0.5% to +1%, the strategy is consistent across orderings.

## Decision Framework

After running MC, apply this checklist:

```
1. P50 return > 0?               → Strategy has expected positive value
2. P25 profit factor > 1.0?      → Edge survives in 75% of orderings
3. Risk of ruin < 5%?            → Acceptable tail risk
4. Actual PF rank between 25-75? → Result is not just lucky ordering
5. P5 max DD within risk budget? → Worst-case drawdown is tolerable
```

If all five: deploy with confidence (subject to walk-forward and OOS validation).
If 1–4 pass but 5 fails: reduce position size until P5 DD is within budget.
If 2 fails: the strategy's edge is fragile — reconsider signal logic.
If 1 fails: the strategy loses money more often than not — reject.

## Relationship to Other Project Docs

- **Decision rationale:** `journal/decisions/004-monte-carlo-method.md` explains *why* we chose trade-level bootstrap over bar-level
- **Existing reference:** `journal/concepts/06-analytics-deep-dive.md` has a condensed technical summary of MC alongside other analytics
- **Running MC:** `journal/runbooks/run-monte-carlo.md` has the step-by-step operational guide

---

**Key takeaway:** The implementation is intentionally simple — 3 lines of numpy for the core bootstrap, ~100 lines for metrics, the rest is visualization and reporting. The simplicity is a feature: there's no model to misconfigure, no parameters to overfit. The data speaks for itself.

[< Part 4: Pitfalls](04-pitfalls.md) | [Index](00-index.md)
