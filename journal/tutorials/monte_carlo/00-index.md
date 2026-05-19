---
tags: [tutorial, monte-carlo, bootstrap, index]
---
# Monte Carlo Crash Course

A 5-part series on Monte Carlo simulation for backtesting and quantitative trading. Written for this project, but the concepts are universal.

---

## Reading Order

| Part | File | What You'll Learn |
|------|------|-------------------|
| 1 | [01-intuition.md](01-intuition.md) | What MC simulation actually is, the coin-flip thought experiment, why randomness matters, and the core insight: *your backtest is one sample from a distribution* |
| 2 | [02-the-math.md](02-the-math.md) | Bootstrap resampling with replacement, the law of large numbers, empirical vs. theoretical distributions, confidence intervals, and the formulas behind each metric |
| 3 | [03-quant-applications.md](03-quant-applications.md) | How quants use MC: equity curve stress testing, risk of ruin, position sizing validation, sequencing luck, drawdown profiling, and forward-looking expectation |
| 4 | [04-pitfalls.md](04-pitfalls.md) | Where MC breaks down: autocorrelation, regime changes, overfitting interaction, sample size requirements, fat tails, and the dangerous things MC *cannot* tell you |
| 5 | [05-our-implementation.md](05-our-implementation.md) | How this codebase implements MC: code walkthrough, output interpretation, worked example with real results, and practical decision-making from MC output |

---

## Prerequisites

- Basic probability (what's a distribution, what's a percentile)
- Familiarity with backtesting concepts (P&L, drawdown, win rate)
- No advanced math required — Part 2 goes deepest but builds from first principles

## One-Sentence Summary

Monte Carlo simulation answers: **"How much of my backtest result is skill vs. luck in trade ordering?"**
