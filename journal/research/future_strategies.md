---
tags: [research, strategies, future-work]
---
# Quantitative Research: Momentum Strategy Alternatives to SMI + W%R

Based on the `quant-researcher` skill profile and a structural analysis of the **SMI + Williams %R** strategy in this codebase, here is a
deep dive into similar, technically sound momentum strategies.

## The Underlying Premise of SMI + W%R
The current system pairs a **smoothed, directional momentum oscillator** (SMI) with a **fast, hyper-responsive mean-reversion oscillator**
(Williams %R). 
* **SMI (Stochastic Momentum Index)** uses double exponential smoothing (EWM). It filters out market noise to confirm the broader momentum
regime, but introduces lag.
* **Williams %R** uses raw High/Low range logic without smoothing. It is highly erratic and provides precise, agile entries by identifying
short-term overbought/oversold extremes within that broader momentum shift.

To build technically sound alternatives, we must avoid **multicollinearity** (pairing two indicators that do the exact same math, like RSI
and MACD, which are both just variations of EMAs). We need pairs where one acts as the smoothed momentum filter, and the other acts as the
un-smoothed or statistically distinct timing trigger.

Here are 4 advanced quantitative strategies built on this exact dual-oscillator architecture:

---

### Strategy 1: The Blau/Chande System (TSI + StochRSI)
**Concept:** A direct spiritual successor to the SMI + W%R pairing, but utilizing different underlying mathematical constraints.
* **Filter (TSI - True Strength Index):** Like SMI, TSI was developed by William Blau and utilizes double-smoothing of price momentum.
However, TSI smooths the 1-period momentum rather than the distance to the midpoint, making it slightly more responsive to sharp price
velocity changes.
* **Trigger (StochRSI):** Applies the stochastic formula to RSI values rather than raw price. It forces RSI to swing wildly between 0 and
100, making it one of the most sensitive mean-reversion triggers available.

**Execution Logic:**
1. **Trend/Momentum:** TSI crosses its signal line (e.g., 25, 13, 7).
2. **Trigger:** StochRSI (14, 14, 3, 3) crosses out of extreme zones (e.g., crosses above 20 for a long entry) within a `sync_window` of the
TSI cross.

**References:**
* Blau, W. (1995). *Momentum, Direction, and Divergence*. John Wiley & Sons. (TSI)
* Chande, T., & Kroll, S. (1994). *The New Technical Trader*. John Wiley & Sons. (StochRSI)

---

### Strategy 2: Volume-Weighted Momentum (KVO + CCI)
**Concept:** If you want to improve upon SMI + W%R, add a third dimension: **Volume**. Price momentum without volume confirmation is
susceptible to false breakouts.
* **Filter (KVO - Klinger Volume Oscillator):** Uses High, Low, Close, and Volume to measure long-term money flow while remaining sensitive
to short-term fluctuations. It compares volume force to trend direction.
* **Trigger (CCI - Commodity Channel Index):** Measures price deviation from its statistical moving average. Unlike oscillators bound between
and 100, CCI is unbounded but practically oscillates between -100 and +100.

**Execution Logic:**
1. **Trend/Momentum:** KVO crosses above its signal line (typically a 13-period EMA), confirming volume is backing the upward momentum.
2. **Trigger:** CCI drops below -100 and crosses back above it, providing the precise "spring" entry.

**References:**
* Klinger, S. (1993). *The Klinger Oscillator*. Technical Analysis of Stocks & Commodities.
* Lambert, D. (1980). *Commodity Channel Index: Tools for Trading Cyclic Trends*. Commodities Magazine.

---

### Strategy 3: The Ehlers/Connors Gaussian Pullback (Fisher Transform + CRSI)
**Concept:** This is a highly mathematical approach suited for mean-reverting equity indices (like SYMBOL).
* **Filter (Fisher Transform):** Converts price data into a Gaussian normal distribution. This creates a nearly perfect square wave that
sharply defines turning points, completely removing the "wandering" behavior of traditional oscillators.
* **Trigger (Connors RSI - CRSI):** A composite indicator combining a 3-period RSI, a 2-period Up/Down Streak, and a 100-period Rate of
Change. It is specifically designed to find micro-pullbacks in established trends.

**Execution Logic:**
1. **Trend/Momentum:** Fisher Transform crosses its signal line.
2. **Trigger:** CRSI drops below 15 (for longs) or pops above 85 (for shorts).

**References:**
* Ehlers, J. F. (2004). *Cybernetic Analysis for Stocks and Futures*. John Wiley & Sons. (Fisher Transform)
* Connors, L. (2008). *Short Term Trading Strategies That Work*. TradingMarkets Publishing. (CRSI)

---

### Strategy 4: Pure Velocity (MACD Histogram Acceleration + ROC)
**Concept:** Stripping away bounded oscillators entirely to focus strictly on the *rate of acceleration*.
* **Filter (MACD Histogram):** Most traders use the MACD lines, but the *Histogram* (the difference between the MACD line and the Signal
line) is the true derivative of momentum. When the histogram expands, momentum is accelerating.
* **Trigger (ROC - Rate of Change):** The purest form of momentum. It is a completely un-smoothed percentage change between the current price
and the price $n$ periods ago. 

**Execution Logic:**
1. **Trend/Momentum:** MACD Histogram turns positive (or simply makes a higher high while below zero, indicating deceleration of a
downtrend).
2. **Trigger:** ROC (e.g., 10-period) crosses the zero line with high velocity.

**References:**
* Appel, G. (2005). *Technical Analysis: Power Tools for Active Investors*. Financial Times Prentice Hall. (MACD)

## Recommendation for this backtester (`backTestingTraderBot`):
Given the existing architecture with `indicator_pair_pipeline.py` and `sync_window` logic, **Strategy 1 (TSI + StochRSI)** would be the most
seamless drop-in replacement to test against the SMI + W%R benchmark, as they share the same normalized bounds and trigger mechanics. 
  Let me know if you would like me to help implement any of these indicators in src/indicators/ or adjust the signal pipeline!