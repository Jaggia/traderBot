---
tags: [monte-carlo, bootstrap, otm, itm, expiration]
---
# 006 — Monte Carlo Simulation

**Date:** 2026-02-28

## What Was Built

A trade-bootstrap Monte Carlo simulation layer on top of existing backtests. Takes closed trade P&Ls from `backtest.csv`, resamples them with replacement N times (default 1000), and produces a distribution of equity curves — revealing how much of a backtest result depends on trade sequencing vs. the strategy's actual edge.

## Method: Trade Bootstrap (Not Bar-Level)

The MC operates at the **trade level**, not the bar level:

1. Extract the list of closed trade P&Ls from `backtest.csv` (the trade list)
2. Resample with replacement 1000× → `(1000, n_trades)` matrix
3. Cumsum each row → equity curve per simulation
4. Compute percentiles across simulations

### Why not bar-level bootstrapping?

Bar-level would reshuffle price bars and re-run the full backtest engine, which is more comprehensive but has a fundamental problem: **price bars are autocorrelated**. Shuffling individual bars independently destroys serial dependence (trends, volatility clustering), producing nonsensical price series with huge gaps. The correct approach would be a **block bootstrap** (resample contiguous blocks to preserve local autocorrelation), which is significantly more complex.

Additionally, with only ~3 months of data, even bar-level bootstrapping still samples from the same narrow market regime — it doesn't generate genuinely new market conditions. For that you need walk-forward testing or more history.

**What each method answers:**
- Trade-level MC → "Is my result sensitive to lucky/unlucky trade ordering?"
- Bar-level block bootstrap → "Is my signal logic robust to different bar sequences?"
- Out-of-sample testing → "Does this work in a different market?"

## What the Percentiles Mean

After running 1000 simulations:
- **P25**: 25% of simulations did *worse* than this value
- **P50**: median outcome (half better, half worse)
- **P75**: 75% of simulations did *worse* than this value

If the actual result is near P50, sequencing luck is neutral. If it's near P75+, the actual result benefited from favorable trade ordering. If the P75 profit factor is still below 1.0, the strategy is structurally losing regardless of sequencing.

## Outputs (saved to `{results_dir}/monte_carlo/`)

- `mc_equity_fan.png` — fan chart: 5/25/50/75/95th percentile bands + actual path overlaid in orange
- `mc_distributions.png` — 2×2 histograms for total return, max drawdown, win rate, profit factor; vertical line = actual value
- `mc_report.md` — table: metric | P5 | P25 | P50 | P75 | P95 | Actual
- `mc_metrics.csv` — raw per-simulation values (1000 rows × 4 metric columns)

## Two Ways to Run

**Post-processor** (on any existing results folder):
```bash
./scripts_bash/run_mc.sh results/db/February-28-2026/options/5min
./scripts_bash/run_mc.sh results/db/February-28-2026/options/5min 2000  # custom N
python main_runner/run_monte_carlo.py <results_dir> [--n 1000] [--seed 42]
```

**Inline** (MC runs automatically after a backtest):
```bash
# Set RUN_MC=true in any run_backtest_*.sh, or pass --mc directly:
python main_runner/run_backtest_db.py 2025-11-10 2026-02-13 --mc
```

## Interpreting Results

The MC simulation reveals whether a backtest result depends on trade sequencing luck or reflects a genuine strategy edge. If the actual result sits near P50, sequencing is not a significant factor. If the P75 profit factor is still below 1.0, the strategy is structurally losing regardless of ordering.

The framework supports comparing different strike selection modes (ATM, OTM, ITM) and DTE configurations. Run MC on each variant to see which differences are statistically meaningful vs. noise from trade ordering.

## New Files

- `src/analysis/monte_carlo.py` — core module
- `main_runner/run_monte_carlo.py` — standalone CLI entry point
- `scripts_bash/run_mc.sh` — bash post-processor wrapper

## Modified Files

- `scripts_bash/run_backtest_{db,alpaca,tv}.sh` — added `RUN_MC=false` toggle
- `main_runner/run_backtest_{db,with_alpaca,tv}.py` — added `--mc` flag detection
- `CLAUDE.md` — documented Monte Carlo in Commands section
