---
tags: [tutorial, monte-carlo, math, bootstrap-formulas, percentiles]
---
# Part 2: The Math — Bootstrap Resampling and the Formulas

[< Part 1: Intuition](01-intuition.md) | [Part 3: Quant Applications >](03-quant-applications.md)

---

## Bootstrap Resampling

"Bootstrap" is a statistics technique invented by Bradley Efron in 1979. The idea is deceptively simple: **treat your sample as if it were the population, and resample from it with replacement.**

### What "with replacement" means

You have a bag of 76 marbles (your trades). You draw one, record its value, *put it back*, and draw again. After 76 draws, you have a new sequence of 76 trades — but some original trades may appear multiple times, and others may not appear at all.

This is different from a *permutation* (shuffling without replacement), where every original trade appears exactly once. Bootstrap resampling can duplicate trades, which is important: it means a simulation might contain 3 copies of your best trade, or 5 copies of your worst.

### Why not just shuffle?

A permutation test (shuffling without replacement) would give you a narrower distribution because the total P&L of every permutation is identical — they all sum to $363. The only thing that varies is the *path* (drawdown, consecutive losses, etc.).

Bootstrap resampling gives a wider, more realistic distribution because different simulations have different total P&Ls. Some oversample winners and return $800; others oversample losers and return -$200. This captures both **sequencing risk** and **sampling uncertainty** — the possibility that your 76 trades aren't a perfectly representative sample of the strategy's true behavior.

### Formal definition

Given observed P&Ls:

```
X = {x_1, x_2, ..., x_n}     where n = number of trades
```

One bootstrap sample is:

```
X* = {x*_1, x*_2, ..., x*_n}  where each x*_i is drawn uniformly from X with replacement
```

The equity curve for that sample:

```
E*_0 = initial_capital
E*_k = E*_0 + sum(x*_1, ..., x*_k)   for k = 1, ..., n
```

Repeat B times (B = 1,000 simulations) to get B equity curves.

## The Law of Large Numbers

Why does this work? The law of large numbers (LLN) states that as B increases, sample statistics computed from the B simulations converge to the "true" values of the bootstrap distribution.

Formally: for any statistic `T(X*)` computed on a bootstrap sample, the average across B samples converges:

```
(1/B) * sum(T(X*_b)) → E[T(X*)]   as B → ∞
```

In practice, B = 1,000 is sufficient for stable estimates of means and percentiles. The *variance* of the percentile estimates decreases as O(1/B) — doubling B halves the estimation error.

## The Empirical Distribution

When you bootstrap, you're implicitly assuming that the empirical distribution of your trades *is* the true distribution. Each trade P&L has probability 1/n of being drawn.

This is a nonparametric assumption — you don't claim trades follow a normal distribution, a t-distribution, or any named distribution. You let the data speak for itself. This is particularly valuable for options P&Ls, which are decidedly **not** normally distributed (they tend to have a binary character: hit target or hit stop).

### Probability mass function

For your observed P&Ls `{x_1, ..., x_n}`, the empirical PMF is:

```
P(X = x_i) = (count of x_i in sample) / n
```

If you have 33 trades hitting +$42.47 and 39 trades hitting various loss amounts, the bootstrap respects these exact frequencies.

## Formulas for Each Metric

Each of these is computed per-simulation, giving you a distribution of B values.

### Total Return

```
return_pct_b = (E*_b,n / E*_b,0 - 1) * 100

where E*_b,n = final equity of simulation b
      E*_b,0 = initial capital
```

### Maximum Drawdown

```
running_max_k = max(E*_b,1, ..., E*_b,k)

dd_k = (E*_b,k - running_max_k) / running_max_k

max_dd_b = min(dd_1, ..., dd_n) * 100
```

Drawdown is always negative or zero. The "max drawdown" is the most negative value — the deepest peak-to-trough decline.

### Win Rate

```
win_rate_b = count(x*_i > 0 for i in 1..n) / n * 100
```

Because of resampling, each simulation can have a different win rate. If your actual win rate is 44.74% (34 of 76), individual simulations will cluster around ~45% but vary from roughly 30% to 60%.

### Profit Factor

```
gross_profit_b = sum(x*_i for x*_i > 0)
gross_loss_b   = |sum(x*_i for x*_i < 0)|

pf_b = gross_profit_b / gross_loss_b
```

If a simulation has zero losses (all resampled trades are winners), profit factor is infinity. This is handled by capping in visualizations.

### Calmar Ratio

```
calmar_b = return_pct_b / |max_dd_b|
```

A return-to-risk measure. High Calmar = you earned a lot relative to your worst drawdown. Infinity if max drawdown is zero (only happens if equity never declines).

### Maximum Consecutive Losses

```
For simulation b, scan through x*_b,1, ..., x*_b,n:
  If x*_b,k < 0: current_streak += 1
  Else:           current_streak = 0
  max_streak_b = max(max_streak_b, current_streak)
```

This is a sequential metric — order matters, which is exactly why MC is useful for it.

### Risk of Ruin

```
ruined_b = 1 if min(E*_b,1, ..., E*_b,n) < ruin_floor * initial_capital
           0 otherwise

risk_of_ruin = mean(ruined_1, ..., ruined_B) * 100
```

Default ruin floor is 50% (you've lost half your capital at any point in the simulation). This is a more conservative measure than just looking at final equity — you might finish positive but have gone through a -55% drawdown along the way.

## Percentile Ranks

Once you have B values of any metric, your actual result's "percentile rank" tells you where it sits:

```
pct_rank(actual, distribution) = count(distribution <= actual) / len(distribution) * 100
```

- Rank = 50: your result is the median outcome
- Rank = 85: 85% of simulations did worse than you (you got lucky)
- Rank = 15: only 15% did worse (you got unlucky)

### Interpreting profit factor rank

| Rank | Interpretation |
|------|----------------|
| >= 75th | Lucky sequencing — your ordering was favorable |
| 25th–75th | Neutral — result is typical for this trade set |
| <= 25th | Unlucky — different ordering would likely have performed better |

## Confidence Intervals via Percentiles

The 5th and 95th percentiles of the bootstrap distribution form a **90% confidence interval** for the metric. This means: under the assumption that your trades are representative, 90% of possible orderings would produce a result between P5 and P95.

```
90% CI for return = [P5(return), P95(return)]
90% CI for max DD = [P5(max_dd), P95(max_dd)]
```

The width of this interval tells you about **strategy stability**:

- **Narrow CI** (e.g., return ranges from 0.1% to 0.4%): results are insensitive to ordering — the strategy has a consistent, small edge
- **Wide CI** (e.g., return ranges from -3% to +5%): results are highly path-dependent — a few large trades dominate

## The Central Limit Theorem Connection

Even though individual trade P&Ls may not be normally distributed, the CLT guarantees that the *sum* of many trades (i.e., total P&L) approaches a normal distribution as n increases. This is why the histogram of total returns across simulations often looks bell-shaped even when the underlying trades are skewed.

However, with small n (say, 20 trades), the CLT approximation is poor, and the bootstrap distribution may look nothing like a bell curve. This is actually a strength of MC: it gives you the *actual* distribution shape without forcing a normal assumption.

## Vectorized Computation

A naive implementation would loop over B simulations. The numpy-vectorized approach is orders of magnitude faster:

```python
# Naive (slow):
for b in range(1000):
    sample = rng.choice(pnl_array, size=n_trades, replace=True)
    equity = initial_capital + np.cumsum(sample)

# Vectorized (fast):
samples = rng.choice(pnl_array, size=(1000, n_trades), replace=True)  # (B, n) matrix
equity_curves = initial_capital + np.cumsum(samples, axis=1)           # (B, n) matrix
```

The vectorized version draws all samples in one call and computes all equity curves simultaneously. For 1,000 simulations of 76 trades, this takes ~1ms vs. ~100ms for the loop.

---

**Key takeaway:** Bootstrap resampling is a nonparametric method that uses your actual trade data as the distribution. Each metric is computed per-simulation, giving you a full distribution rather than a single point estimate.

[< Part 1: Intuition](01-intuition.md) | [Part 3: Quant Applications >](03-quant-applications.md)
