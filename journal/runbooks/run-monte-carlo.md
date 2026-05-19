---
tags: [runbook, monte-carlo, percentiles]
---
# Runbook: Run Monte Carlo Simulation

Bootstraps trade P&Ls to show how much of a backtest result depends on sequencing luck vs actual edge.

## On an Existing Results Folder

```bash
./scripts_bash/run_mc.sh results/db/February-28-2026/options/5min
./scripts_bash/run_mc.sh results/db/February-28-2026/options/5min 2000  # custom N

# Or directly:
python main_runner/run_monte_carlo.py results/db/February-28-2026/options/5min
python main_runner/run_monte_carlo.py results/db/February-28-2026/options/5min --n 2000 --seed 42
```

## Inline (Immediately After a Backtest)

```bash
# Set RUN_MC=true in the .sh file, or pass --mc:
python main_runner/run_backtest_db.py 2025-11-10 2026-02-13 --mc
```

## Output

Saved to `{results_dir}/monte_carlo/`:

| File | Contents |
|---|---|
| `mc_equity_fan.png` | Fan chart: P5/P25/P50/P75/P95 bands + actual path in orange |
| `mc_distributions.png` | Histograms for total return, max drawdown, win rate, profit factor |
| `mc_report.md` | Table: metric × percentile, actual vs simulated |
| `mc_metrics.csv` | Raw per-simulation values (N rows × 4 metric columns) |

## Reading the Results

- **Actual near P50** → sequencing luck is neutral; result reflects real edge (or lack of it)
- **Actual near P75+** → you got lucky trade ordering; edge may be weaker than it looks
- **P75 profit factor < 1.0** → strategy is structurally losing regardless of luck
- **P25 profit factor > 1.0** → strategy has robust positive edge even in bad-luck scenarios

For the method rationale (why trade-level, not bar-level), see `decisions/004-monte-carlo-method.md`.
