---
tags: [tutorial, hmm, quant-applications, signal-gating, vol-filter]
---
# Part 3: Quant Applications — How Practitioners Use HMMs

[< Part 2: The Math](02-the-math.md) | [Part 4: Pitfalls >](04-pitfalls.md)

---

## The Core Use Case: Regime Detection

The dominant application in systematic trading is identifying market regimes — distinct statistical environments in which a strategy's behavior differs substantially.

This is not a minor refinement. Strategies that fail in backtesting often fail because they were trained and tested within a single regime. Strategies that work in backtesting but fail in live trading often fail because they entered a new regime. Regime awareness is the difference between "this signal has edge" and "this signal has edge *in this type of market*."

HMMs are the most principled tool for this because they account for:
1. Regime persistence (a trend lasts more than one bar)
2. Probabilistic inference (you're never 100% certain which regime you're in)
3. Transition structure (some regime changes are more likely than others)

---

## Application 1: Signal Gating

The most direct use in a momentum strategy like this one:

```
Compute regime probability → if P(trending regime) > threshold → allow signals
                           → if P(trending regime) < threshold → block signals
```

**Why it helps:** Your SMI + Williams %R signals are momentum signals. Momentum has positive expected value in trending regimes and near-zero (or negative) expected value in choppy, mean-reverting regimes. The HMM identifies which condition you're in.

Concretely: in a 2-state model (trending vs. choppy), you only fire the SMI/WR signal when `P(state=trending) > 0.65`. When the market is ambiguously in between, you wait.

This is strictly more sophisticated than the VWAP filter. VWAP checks whether price is above or below a single intraday reference level — it tells you about the *day's direction* but not about whether the market is moving *cleanly* or *noisily*. A choppy day can have price entirely above VWAP while SMI and WR generate multiple false signals. The HMM captures the noise structure directly.

---

## Application 2: Volatility Regime Filtering

A simpler but closely related use: separate high-volatility from low-volatility regimes.

In options trading (which this project does), volatility regime matters doubly:
- **Implied volatility (IV)** is directly linked to premium paid for options
- **Realized volatility (RV)** determines whether price actually reaches targets

A 2-state HMM trained on realized volatility alone can classify bars as:
- **Low-vol regime:** small, consistent bar moves. Directional signals have cleaner follow-through.
- **High-vol regime:** large, noisy bar moves. Directional signals are unreliable; options are expensive (high IV) while the direction is random (high RV).

The practical implication for this project: in a high-vol regime, either skip the trade entirely or switch from directional options (calls/puts) to a different strategy entirely.

You can estimate this without an HMM (e.g., ATR-based thresholds), but the HMM is better because it accounts for vol persistence — a single noisy bar doesn't trigger a regime change, but sustained elevated vol does.

---

## Application 3: Position Sizing

Instead of a binary gate (trade / don't trade), use the regime probability to scale position size continuously:

```python
base_contracts = config["position"]["contracts_per_trade"]

# P(regime = trending) from HMM forward filter
trending_prob = hmm_state_probs[t, TRENDING_STATE]

# Scale: 0 contracts in pure choppy, full size in pure trending
scaled_contracts = round(base_contracts * trending_prob)
```

This is softer than binary gating. When the model is 60% confident in a trending regime, you trade at 60% size. When it's 90% confident, you trade full size. When it's 40% (ambiguous), you either skip or go minimal.

This approach connects directly to Kelly criterion thinking: the optimal bet size is proportional to your edge. If your edge is weaker in ambiguous regimes, your position should be smaller.

---

## Application 4: Drawdown Budget Management

Run the HMM on your historical equity curve rather than price data. Train it on equity returns (daily or weekly P&L as a fraction of capital). States become:

- **Productive regime:** positive expected P&L, low drawdown
- **Drawdown regime:** negative expected P&L, rising max drawdown

If the model classifies you as entering a drawdown regime, reduce position size or stop trading entirely until you return to productive.

This is distinct from a simple drawdown stop (e.g., "stop trading if down 10%"). A drawdown stop reacts *after* the damage is done. An HMM-based filter reacts to the *statistical pattern* that precedes drawdowns — often several bars before the actual equity peak.

---

## Application 5: Walk-Forward Regime Labeling for Analytics

After a backtest, run the Viterbi decoder over the full bar sequence (using future data — this is allowed for analysis only, not for signal generation). Label each trade with the regime at entry:

```
Trade 1: entered in regime "Trending-Up"  → won
Trade 2: entered in regime "Choppy"       → lost
Trade 3: entered in regime "Choppy"       → lost
Trade 4: entered in regime "Trending-Up"  → won
```

Then compute performance metrics split by regime:

| Regime | Trades | Win Rate | Profit Factor |
|--------|--------|----------|---------------|
| Trending-Up | 34 | 58% | 1.72 |
| Trending-Down | 12 | 52% | 1.41 |
| Choppy | 30 | 32% | 0.71 |

If this table shows what you expect — choppy regime has sub-1.0 profit factor — you have a concrete justification for gating. And you know exactly how many trades you'd have filtered out.

This is a powerful diagnostic. It can transform "our strategy has a 1.34 profit factor" into "our strategy has a 2.1 profit factor in trending regimes, but those are only 60% of bars — the choppy 40% drags the average down to 1.34. Filter out choppy and you're looking at a different strategy."

---

## Application 6: Regime-Conditional Strategy Switching

The most ambitious use: train different strategies for different regimes and let the HMM select which one to run.

```
Regime = "Trending":     run SMI + Williams %R (momentum)
Regime = "Choppy":       run mean-reversion (fade moves at extremes)
Regime = "Trending-Down":run puts only, tighter stop, smaller size
```

This project's generic armed-mode system is well-suited to this. Different indicator pairs (RSI+MACD vs SMI+WR) have different behavior profiles — RSI+MACD might be better in ranging markets, SMI+WR better in trending markets. The HMM could switch between them.

In practice, this approach requires careful validation. Two strategies that each look good independently can produce chaotic behavior when the switching rule itself becomes a source of error.

---

## What HMMs Are NOT Used For in This Context

**Signal generation (directly).** An HMM tells you the regime; it doesn't tell you whether to go long or short within that regime. You still need your SMI/WR logic (or equivalent) to generate directional signals.

**Price prediction.** An HMM does not forecast future returns. It estimates the current hidden state, which has *different expected returns* than other states — but that's not a price target.

**Replacing Monte Carlo.** MC answers "how sensitive is my P&L to trade ordering?" HMM answers "was my strategy being applied in the right environment?" These are complementary, not substitutes. Run both.

---

## The Relationship to Existing Filters in This Codebase

| Filter | What it checks | Limitation |
|--------|---------------|------------|
| VWAP filter | Is price above/below day's volume-weighted average? | Day-level only; says nothing about market noise/trend clarity |
| Armed mode window | Did the second indicator fire within N bars of the first? | Looks at indicator timing, not market state |
| HMM regime filter | Is the market's statistical behavior pattern currently favorable for momentum? | Requires training, intro of look-ahead bias if not careful |

The VWAP filter and armed mode are about *signal quality* (do the indicators agree? is price directional intraday?). The HMM is about *market condition quality* (is the market in a state where momentum strategies work at all?). They operate at different levels and stack cleanly.

---

**Key takeaway:** HMMs are used in trading primarily as regime classifiers — gating signals, scaling position size, labeling historical trades for analysis, and informing drawdown management. In this project the most immediate value is Application 5 (backtesting analytics) followed by Application 1 (signal gating in live/backtest).

[< Part 2: The Math](02-the-math.md) | [Part 4: Pitfalls >](04-pitfalls.md)
