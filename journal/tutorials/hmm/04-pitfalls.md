---
tags: [tutorial, hmm, pitfalls, look-ahead-bias, overfitting]
---
# Part 4: Pitfalls — Where HMMs Break Down

[< Part 3: Quant Applications](03-quant-applications.md) | [Part 5: Our Implementation >](05-our-implementation.md)

---

## Pitfall 1: Look-Ahead Bias in Training and Decoding

This is the most dangerous pitfall, and it is easy to fall into by accident.

**The problem:** Baum-Welch trains on a full historical sequence, using both past *and future* observations to estimate the regime at every bar (that's what the backward variable β does — it looks forward in time). If you train on your full backtest period and then use the resulting model to label the regime at each bar, those labels are contaminated by future information.

Put differently: the model "knew" at bar 100 that bars 200–300 were going to be choppy, so it might label bar 100 as a transition bar — but that information wasn't available at bar 100 in real time.

**The consequence:** Backtest performance looks great because the HMM filter appears to perfectly avoid bad regimes. In live trading, it performs much worse because the filter must work with only past data.

**How to avoid it:**

Option A — Use the Forward filter only (not Viterbi) during backtesting:
```python
# At each bar t, only use observations o_1, ..., o_t
state_probs_t = forward_filter(observations[:t+1], model)
regime_t = state_probs_t[-1]  # current bar's posterior from forward only
```

Option B — Walk-forward training:
1. Train the HMM on a lookback window ending at bar t (e.g., 252 trading days of 5-min bars = ~20k bars)
2. Apply it to bars t+1 through t+refit_window using the Forward filter
3. Retrain on the next window
4. Repeat

Option B is more realistic but computationally expensive (retraining every N bars). For a first implementation, Option A with a train/test split is safer and easier to validate.

---

## Pitfall 2: Regime Persistence Illusion

An HMM trained on data will assign high diagonal values to the transition matrix (A[i][i] is high) because most consecutive bars are in the same regime. This is correct statistically, but it creates a **persistence illusion** in backtesting:

- The model detects a regime change only *after* several bars of new-regime behavior
- By the time it's confident the regime has changed, the damage is already done (or the opportunity is already over)
- The model never says "we just entered choppy" — it says "we've been in choppy for 8 bars now"

**The practical impact:** Regime filters have latency. If your signal fires at the beginning of a regime transition, the filter may not yet have updated. You can't evaluate a regime filter based on how well it labels regimes in hindsight — you must measure how early it detects transitions in real time.

**How to measure latency:**
Compare Viterbi labels (hindsight-optimal) vs. Forward filter labels (real-time) on historical data. The average number of bars of delay before they agree is your filter's effective latency. If your momentum signal lasts ~20 bars on average and the filter has 10 bars of latency, the filter is nearly useless for that signal.

---

## Pitfall 3: Overfitting to the Training Regime

A 3-state HMM has many parameters:
- π: 3 values
- A: 9 values (3×3 matrix)
- B (Gaussian): 3 means + 3 covariance matrices

With a 4-feature observation vector, each covariance matrix is 4×4 = 16 values. Total parameters: ~90. If your training set has only 2,000 bars (about 2 months of 5-min data), you have 22 observations per parameter — borderline.

**The specific failure mode:** The model learns that a particular combination of SMI, W%R, and return characterizes "trending regime" in the training period. But the training period might have had a specific market structure (e.g., post-FOMC rally) that doesn't generalize. In the test period, the model confidently assigns the wrong regime label because it learned the wrong features.

**How to detect overfitting:**
- Train on Jan–Feb, validate on March. Check whether regime-labeled profit factors are similar in both periods.
- If PF is 2.1 in the training period's labeled "trending" regime and 0.9 in the validation period's labeled "trending" regime, the model is overfit.

**How to reduce overfitting:**
- Use fewer states (2 instead of 3)
- Use fewer features (1–2 instead of 4)
- Use `covariance_type="diag"` (fewer parameters per state)
- Train on more data (longer lookback window)

---

## Pitfall 4: Non-Stationarity

The most fundamental problem with HMMs (and most statistical models) applied to financial data:

**The model assumes the parameters (A, B, π) are constant over time.** In reality:
- Volatility regimes change across years (2020 COVID was unlike 2017–2019)
- Market microstructure changes (algos vs. humans, Reg NMS, options market-maker delta hedging)
- SYMBOL's behavior in a rising rate environment differs from a falling rate environment

When you train an HMM on 3 months of data and apply it to live trading, you're assuming the market's regime dynamics in those 3 months are still valid now. This is often reasonable in the short term (a few weeks), questionable in the medium term (a few months), and likely wrong in the long term.

**Symptoms of non-stationarity:**
- The model was performing well, then suddenly stopped working with no code changes
- Regime labels no longer match visual inspection of the chart
- The transition matrix has changed dramatically when you retrain

**How to handle it:**
- Retrain regularly (monthly or quarterly) — Part 5 covers this
- Use short lookback windows that weight recent data more heavily
- Monitor model log-likelihood on new data; if it degrades significantly, retrain

---

## Pitfall 5: The Label Assignment Problem

After training, your K states have no automatic interpretation. State 0 could be "trending" or "choppy" — the model doesn't know the difference. You have to label them yourself by examining the emission distributions.

**Common assignment strategy:** Sort states by mean return of the emission distribution. State with highest mean return = "Trending-Up." State with lowest mean = "Trending-Down." Middle state(s) = "Choppy/Neutral."

But this breaks down if:
- Two states have similar mean returns but different volatilities
- The market has only been trending down during the training period (one "trending down" state dominates)
- States swap identity between retrains (State 0 was "trending" in January, but after February retraining, State 0 is now "choppy")

**The state-swap problem** is especially nasty in live trading. If you retrain monthly and state identities swap, your gating logic silently inverts — you'd be trading in choppy and sitting out trends.

**How to handle it:** Always sort and relabel states after training based on a stable criterion (usually emission mean or emission volatility). Never refer to states by index; always refer to them by their inferred label.

---

## Pitfall 6: Calibration and Threshold Selection

The forward filter gives you a probability: `P(state = trending) = 0.73`. How do you turn that into a trading decision? "Trade if P > 0.65" is a threshold you chose. Why 0.65 and not 0.5 or 0.8?

If you optimize this threshold in-sample, you've introduced another layer of overfitting. The threshold that maximizes in-sample profit factor will not generalize.

**How to handle it:**
- Use a principled threshold (e.g., 0.5 = majority probability, or "only trade in the state with the highest probability")
- Or use the probability as a continuous weight for position sizing (avoids a hard threshold entirely)
- Never optimize the threshold on the same data you validate the HMM on

---

## Pitfall 7: HMMs Cannot Tell You About Future Regimes

This is sometimes misunderstood. The HMM tells you "the market is currently in regime X." It does not tell you "the market will be in regime X for the next N bars."

The transition matrix A encodes the *average* persistence of regimes. If A[0][0] = 0.90, a trending regime lasts an average of `1 / (1 - 0.90) = 10 bars`. But this is a statistical average — specific trends can end after 2 bars or persist for 100.

**The implication:** Don't enter a trade expecting the regime to persist just because the HMM says you're in it. Use the regime as a filter for entry, but keep your normal exit rules (stop loss, profit target, opposite signal) intact. The HMM does not replace exit logic.

---

## Pitfall 8: Small Sample Sizes for Options

This project runs backtests on a few months of data, generating ~50–100 closed trades per period. HMM training requires *bar-level* data (hundreds of 5-min bars per day × many days), but the validation of whether regime filtering actually helps requires *trade-level* data with enough trades per regime to be statistically meaningful.

If you have 80 total trades and 30 are labeled "choppy regime" entries, you need those 30 trades to show a clearly different profit factor from the 50 "trending" trades — but with 30 trades, your estimate of the profit factor is very noisy (see the Monte Carlo tutorial on sample size requirements).

**The honest minimum:** You need at least 30 trades per regime class to make statistically meaningful comparisons. With a 2-state model applied to 80 trades, that's borderline. With a 3-state model, it's probably insufficient. When in doubt, run Monte Carlo analysis on the regime-filtered subset to see whether the apparent improvement is statistically meaningful.

---

## Summary: What HMMs Are Not

| Claim | Reality |
|-------|---------|
| "The HMM predicts future regime" | It estimates current regime from past data only |
| "The HMM tells me whether to go long or short" | It filters conditions; your signal logic decides direction |
| "Regime labels from training are usable in backtesting" | Only if you use the Forward filter (no future data), not Viterbi |
| "Retraining monthly eliminates the non-stationarity problem" | It reduces it; it doesn't eliminate it |
| "A 3-state model is always better than a 2-state model" | More states = more parameters = higher overfitting risk on short histories |

---

**Key takeaway:** HMMs are powerful but fragile. The biggest failure mode is look-ahead bias — using Baum-Welch's backward smoothing in backtesting as if it were real-time. The second biggest is overfitting to a training regime that doesn't generalize. Both are avoidable with careful implementation.

[< Part 3: Quant Applications](03-quant-applications.md) | [Part 5: Our Implementation >](05-our-implementation.md)
