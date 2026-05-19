---
tags: [monte-carlo, bootstrap, trade-level, percentiles, otm]
---
# Decision: Monte Carlo Simulation Method

**Date:** 2026-02-28

## Trade-Level Bootstrap, Not Bar-Level

The MC operates at the **trade level**: extract closed trade P&Ls, resample with replacement N times, cumsum each row into equity curves, compute percentiles.

### Why not bar-level bootstrapping?

Bar-level would reshuffle price bars and re-run the full backtest engine. More comprehensive in theory, but has a fundamental problem: **price bars are autocorrelated**. Shuffling individual bars independently destroys serial dependence (trends, volatility clustering), producing nonsensical price series with huge gaps.

The correct bar-level approach would be a **block bootstrap** (resample contiguous blocks to preserve local autocorrelation) — significantly more complex and not warranted given we only have ~3 months of data. Even block bootstrapping would still sample from the same narrow market regime; it doesn't generate genuinely new market conditions.

### What each method actually answers

| Method | Question answered |
|---|---|
| Trade-level MC | Is my result sensitive to lucky/unlucky trade ordering? |
| Bar-level block bootstrap | Is my signal logic robust to different bar sequences? |
| Out-of-sample testing | Does this work in a different market? |

Trade-level is the right tool for our current stage: we have enough trades to ask whether sequencing matters, but not enough history to justify bar resampling.

## What the Percentiles Mean

- **P25**: 25% of simulations did *worse* than this
- **P50**: median outcome (half better, half worse)
- **P75**: 75% of simulations did *worse* than this

If the actual result is near P50, sequencing luck is neutral. Near P75+, the actual result benefited from favorable trade ordering.

## Using MC for Strike Selection Decisions

MC is particularly useful for comparing strike selections (e.g. OTM vs ITM) — run the same date range under each config and compare P50/P25/P75 profit factor distributions rather than single-run results. A single run can be dominated by sequencing luck; MC shows where the true edge sits across orderings.

**Key diagnostic:** a high percentage of trades exiting via expiration worthless (visible in the Exit Reasons table in `report.md`) is a more important signal than OTM vs ITM comparison. If most options expire worthless regardless of strike, the problem is signal timing or DTE selection, not strike distance.

## OTM vs ITM at 0 DTE: Theoretical Take

**OTM** — cheaper, more leverage, explosive % gain if the move is big and fast. Brutal theta on 0-DTE if it isn't.

**ITM** — higher delta, move translates more directly into P&L, intrinsic value as a floor. Less explosive upside but more forgiveness.

OTM can edge ITM when signals are accurate because gamma explosion more than compensates for higher theta risk. The difference is small in most regimes — MC distributions from real runs will show whether the edge is consistent or sequencing-dependent.

## Files

- `src/analysis/monte_carlo.py` — core module
- `main_runner/run_monte_carlo.py` — standalone CLI
- `scripts_bash/run_mc.sh` — bash wrapper
