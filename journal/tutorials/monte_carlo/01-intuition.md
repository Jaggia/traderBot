---
tags: [tutorial, monte-carlo, intuition, thought-experiments]
---
# Part 1: Intuition — What Is Monte Carlo Simulation?

[< Index](00-index.md) | [Part 2: The Math >](02-the-math.md)

---

## The Name

Monte Carlo simulation is named after the Monte Carlo Casino in Monaco. The technique was formalized in the 1940s by Stanislaw Ulam and John von Neumann while working on nuclear physics at Los Alamos. Ulam was playing solitaire, got curious about the probability of winning, and realized that instead of computing the combinatorial math, he could just *simulate a thousand games and count*. That instinct — replace analytical math with repeated random simulation — is the entire idea.

## The Core Insight

**Your backtest result is a single data point.** You ran your strategy from January to March 2026 and got +$363 in P&L. But you didn't *have* to get $363. If the same 76 trades had happened in a different order — if the winners had clustered differently, if the losing streaks had fallen at different times — the final P&L would be different. Maybe $800. Maybe -$200. Maybe $1,500.

Monte Carlo simulation asks: **what is the *distribution* of outcomes I could have gotten from the same set of trades?**

## The Coin-Flip Experiment

Imagine you flip a fair coin 100 times. You expect 50 heads. But you won't always get exactly 50 — sometimes you'll get 47, sometimes 53, occasionally 42 or 58. Each sequence of 100 flips is one "realization" of the experiment.

Now imagine each flip is a trade. Heads = win $42 (your avg win), tails = lose $24 (your avg loss). After 100 trades, your P&L depends on:

1. **How many wins vs. losses** — this is the win rate (strategy edge)
2. **What order they came in** — this is sequencing luck

If you get 10 losses in a row early, your equity curve looks terrifying even if the final P&L is positive. If you get 10 wins in a row early, you feel like a genius. Same trades, different experience, different drawdown, different risk of ruin.

Monte Carlo simulation **shuffles the deck** thousands of times to show you the full range of possibilities.

## A Visual Metaphor

Think of your actual backtest as one path through a forest. Monte Carlo shows you the *entire forest* — all the paths you could have walked with the same set of trades. Some paths reach a higher summit, some dip into deeper valleys, but they all use the same collection of steps.

```
                         ╱ P95 — got very lucky
                       ╱
          ════════════╱═══ P75
        ╱           ╱
  ─────╱───────────╱────── P50 (median outcome)
      ╱           ╱
════╱═══════════╱═════════ P25
   ╱           ╱
  ╱           ╱ P5 — got very unlucky
 ╱
Start
```

Your actual result falls somewhere in this fan. Where it falls tells you about your sequencing luck.

## What MC Does NOT Tell You

This is critical and often misunderstood:

- MC does **not** tell you if your strategy will work in the future
- MC does **not** test your signal logic — it assumes the trades are a given
- MC does **not** model changing market conditions
- MC does **not** account for the order in which trades *should* occur (because it shuffles them)

What it *does* tell you:

- How sensitive your result is to trade ordering
- The range of drawdowns you should expect
- Whether your edge is robust or whether you got lucky
- Your estimated risk of ruin over many possible orderings

## The Two Flavors

In quant finance, there are two major flavors of Monte Carlo:

### 1. Path simulation (model-based)

Generate synthetic price paths using a stochastic process (geometric Brownian motion, Heston model, etc.). Used heavily in derivatives pricing — Black-Scholes itself can be solved via MC. This requires assumptions about the underlying data-generating process.

### 2. Bootstrap resampling (data-driven)

Take your actual observed data (trade P&Ls, returns, etc.) and resample it randomly. No model assumptions needed — the data *is* the distribution. This is what we do in this project.

**We use bootstrap resampling** because:
- We don't need to assume a return distribution (normal, log-normal, etc.)
- The actual trade P&Ls already capture the strategy's real behavior
- It directly answers the sequencing luck question
- It's simple to implement and fast to compute

## Why 1,000 Simulations?

The law of large numbers guarantees that as you run more simulations, your sample statistics (mean, percentiles) converge to the true values. In practice:

| Simulations | Stability |
|-------------|-----------|
| 100 | Rough — percentiles jump around |
| 500 | Reasonable — P50 is stable, P5/P95 still noisy |
| 1,000 | Good default — percentiles stable to ~1% |
| 10,000 | High precision — diminishing returns for most uses |

1,000 is the sweet spot: fast to compute (< 1 second for 76 trades) and precise enough for decision-making. If you need rock-solid P5/P95 estimates (for risk management), bump to 5,000–10,000.

## Putting It Together

Here's the full thought process:

1. You ran a backtest and got 76 trades with various P&Ls
2. You wonder: "Is my +$363 result typical, or did I get lucky/unlucky?"
3. You resample those 76 P&Ls randomly (with replacement) 1,000 times
4. Each resampling produces a different equity curve
5. You now have 1,000 equity curves — a distribution of possible outcomes
6. You compute metrics (return, drawdown, profit factor) for each
7. You see where your *actual* result sits within that distribution

If your actual profit factor is at the 80th percentile — 80% of random orderings did worse — you got lucky with sequencing. If it's at the 30th percentile, you got unlucky, and the strategy's true expected performance is likely better than what you observed.

---

**Key takeaway:** Monte Carlo simulation turns your single backtest result into a probability distribution, letting you separate strategy edge from sequencing luck.

[< Index](00-index.md) | [Part 2: The Math >](02-the-math.md)
