---
tags: [tutorial, monte-carlo, pitfalls, limitations]
---
# Part 4: Pitfalls — Where Monte Carlo Breaks Down

[< Part 3: Quant Applications](03-quant-applications.md) | [Part 5: Our Implementation >](05-our-implementation.md)

---

Monte Carlo simulation is powerful, but it has blind spots. Understanding these limitations is more important than understanding the technique itself — a tool used beyond its valid range gives you false confidence, which is worse than no tool at all.

## Pitfall 1: Autocorrelation (Serial Dependence)

**The biggest limitation of trade-level bootstrap.**

Bootstrap resampling assumes that each trade is *independent* — that knowing the outcome of trade #5 tells you nothing about trade #6. This is often false:

- **Momentum regimes:** Markets trend. If you're trading a trend-following signal, winners tend to cluster (a winning trade is followed by more winners), and so do losers. Bootstrap destroys this structure.
- **Volatility clustering:** After a big move (win or loss), more big moves tend to follow. Bootstrap treats a $500 trade and a $5 trade as equally likely to appear anywhere in the sequence.
- **Mean reversion:** In some strategies, a loss makes the next trade more likely to win (the market reverted). Bootstrap loses this negative autocorrelation.

**Impact:** If your trades have positive autocorrelation (winners follow winners), bootstrap *understates* the severity of drawdowns because it breaks up the loss clusters. Conversely, if trades are negatively autocorrelated, bootstrap *overstates* drawdown risk.

**Mitigation:** Block bootstrap — instead of resampling individual trades, resample *blocks* of consecutive trades (e.g., 5 trades at a time). This preserves short-range autocorrelation. Our codebase uses individual trade bootstrap, which is appropriate for 0-DTE options where each trade opens and closes the same day, making autocorrelation minimal.

**How to check:** Compute the autocorrelation of your P&L series:

```python
import pandas as pd
pnl = pd.Series(trade_pnls)
print(pnl.autocorr(lag=1))  # Close to 0 = independence assumption is reasonable
```

If |autocorr| > 0.2, consider block bootstrap or at least note the limitation.

## Pitfall 2: Regime Changes

Bootstrap resampling assumes **stationarity** — that the underlying process generating trades doesn't change over time. In reality, market regimes shift:

- Your backtest spans Jan–Mar 2026. Jan was a low-vol grind higher. Feb was a choppy sideways mess. March was a sell-off.
- Trades from January have different characteristics than trades from March.
- Bootstrap treats them all as equally representative of "the strategy."

**The problem:** If the market enters a regime not represented in your sample (e.g., a crash worse than anything in your backtest), MC will never generate it. MC can only show you recombinations of what already happened — it cannot imagine scenarios beyond your data.

**Real-world example:** A strategy backtested over 2017–2019 (low vol) would produce an MC distribution that looks great. Then 2020 hits and the strategy experiences drawdowns that were in the 0th percentile of the MC distribution — "impossible" according to the simulation.

**Mitigation:**
- Use longer backtest windows that span multiple regimes
- Supplement MC with stress testing (manually inject extreme scenarios)
- Never treat MC percentiles as hard guarantees — treat P5 as "this is bad but worse is possible"

## Pitfall 3: Small Sample Sizes

Bootstrap resampling relies on the sample being *representative* of the true distribution. With too few trades, it isn't.

| Trades | Reliability |
|--------|-------------|
| < 10 | Essentially meaningless — the bootstrap just recombines too few data points |
| 10–30 | Directional guidance only — wide confidence intervals, unstable percentiles |
| 30–100 | Reasonable — P25/P75 are stable, P5/P95 are noisy |
| 100–500 | Good — robust percentile estimates |
| 500+ | Excellent — fine-grained distribution detail |

Our codebase enforces a minimum of 5 trades (below which MC is skipped), but really, you want 30+ trades for the output to be actionable.

**The problem with small samples:** If you have 15 trades and 2 of them are large outliers, the bootstrap will create simulations with 0, 1, 2, 3, or even 4 copies of those outliers. The resulting distribution will be extremely wide and dominated by the accident of whether those outliers get resampled.

**Mitigation:** Report the number of trades alongside MC results. Treat MC output from < 30 trades as exploratory, not decision-grade.

## Pitfall 4: Overfitting Interaction

MC does **not** detect overfitting. This is perhaps the most dangerous misconception.

If you optimized your SMI/Williams %R parameters until the backtest looked great, those 76 trades are the *result* of overfitting. Running MC on overfit trades just tells you about the ordering robustness of an overfit result. The MC output will look perfectly healthy — positive median, tight confidence interval — because the input trades are artificially good.

**The trap:**
1. Optimize parameters → get great backtest
2. Run MC → "P50 is positive, P25 PF > 1.0, looks robust!"
3. Deploy → strategy fails because the edge was curve-fit

MC validates **trade ordering robustness**, not **signal validity**. You need walk-forward analysis, out-of-sample testing, or cross-validation to detect overfitting. MC is a complement, not a substitute.

## Pitfall 5: Fat Tails and Extreme Events

Options P&Ls often have fat tails — a few extreme wins or losses that are much larger than the typical trade. Bootstrap handles this better than parametric methods (it doesn't assume normality), but there's still a problem:

**Your sample may not contain the true tail.** If your worst trade was -$200 but the strategy is capable of producing a -$2,000 trade (e.g., a gap-through on a stop), no amount of bootstrapping will generate that scenario. MC can only recombine observed values — it cannot extrapolate.

**Impact:** Risk of ruin estimates are biased *downward* because the simulation can't produce losses larger than the worst observed trade.

**Mitigation:**
- Augment the P&L array with hypothetical extreme scenarios (stress testing)
- Use EVT (Extreme Value Theory) to model the tail separately
- Recognize that MC gives a *lower bound* on risk of ruin, not an exact estimate

## Pitfall 6: Independence of Trade Size

Our bootstrap resamples trade P&Ls uniformly. But in practice, trade size might vary:

- Early trades (when equity is higher) might be larger than late trades (after drawdown reduces sizing)
- A percent-of-equity sizing scheme means losing trades reduce position size, creating a natural de-risking effect

Bootstrap destroys this relationship — a large early trade can appear at the end of a simulation when equity is low, or vice versa. If your sizing is fixed (like our 1-contract-per-trade), this isn't an issue. But for percent-of-equity sizing, the MC distribution will be wider than reality because it doesn't model the position-sizing feedback loop.

## Pitfall 7: Ignoring Costs and Slippage Variation

The bootstrap resamples *net* P&Ls (after costs). But in live trading, slippage and costs can vary:

- A trade that was profitable by $5 in backtesting might be unprofitable after real slippage
- Liquidity conditions change — wide spreads during volatility events increase costs

MC doesn't model variable costs. It treats the backtest cost assumptions as fixed.

**Mitigation:** Run backtests with conservative cost assumptions before MC analysis. If the strategy is still profitable with 2x estimated slippage, MC results are more trustworthy.

## Pitfall 8: The "It Looks Good" Trap

The fan chart and histograms are visually compelling. A tight fan chart with the actual path in the middle *looks* robust. But visual appearance can mask problems:

- A fan chart with 76 trades may look tight simply because there aren't enough trades for large divergence
- The histograms may be normal-looking because of the CLT, even if the underlying distribution is pathological
- "Risk of ruin: 0%" feels reassuring but just means no simulation hit the floor — with a conservative ruin threshold or more simulations, it might not be zero

**Mitigation:** Always read the numbers, not just the pictures. Focus on the percentile table in the report, especially P5 values — they're the stress-test numbers.

## Pitfall 9: Conflating Bootstrap Distribution with Forecast Distribution

The MC distribution answers: "Given these specific trades, what are the possible orderings?" It does **not** answer: "What will happen in the next 76 trades?"

The bootstrap distribution is a statement about the past data, not a prediction about the future. Future trades will have different P&Ls, different win rates, different characteristics. The MC distribution is only predictive to the extent that past trades are representative of future ones — which is the stationarity assumption from Pitfall 2.

## Pitfall 10: Computational False Precision

Running 10,000 simulations gives you percentiles to many decimal places. This precision is artificial — the true uncertainty comes from the input data (76 trades), not the number of simulations. Don't report "P50 return = 0.2347%" as if that fourth decimal digit means something.

**Rule of thumb:** Report percentiles rounded to 1 decimal place for percentages, whole dollars for P&L. The input data doesn't support more precision than that.

---

## Summary: What MC Can and Cannot Do

| MC CAN tell you | MC CANNOT tell you |
|---|---|
| How sensitive results are to trade ordering | Whether your signal will work in the future |
| The distribution of possible drawdowns | How bad drawdowns could get beyond observed data |
| Whether your specific result was lucky/unlucky | Whether the strategy is overfit |
| Risk of ruin under resampled orderings | True risk of ruin (which includes unseen scenarios) |
| Confidence intervals for metrics | Guarantees about metric values |

**The golden rule:** Monte Carlo is a necessary but not sufficient condition for strategy confidence. Use it alongside walk-forward testing, out-of-sample validation, and common sense.

---

**Key takeaway:** The most important thing about MC is knowing where it breaks. Bootstrap assumes independence, stationarity, and that your sample is representative. When those assumptions hold, MC is invaluable. When they don't, it gives you overconfidence — which is the most dangerous thing in trading.

[< Part 3: Quant Applications](03-quant-applications.md) | [Part 5: Our Implementation >](05-our-implementation.md)
