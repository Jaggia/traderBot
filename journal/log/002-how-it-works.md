---
tags: [system-overview, indicators, smi, williams-r, vwap, data-sources]
---
# Journal Entry 002: How It Works

**Date:** 2026-02-15

## The Big Picture

This is an options & equities strategy backtester. The thesis: combine two technical indicators — one for trend (SMI) and one for momentum (Williams %R) — and only enter when both agree within a time window. Backtest it against historical data to see if it actually works.

```
Historical Data → Indicators → Signal Logic → Backtest Engine → Performance Report
```

## The Two Indicators

### Stochastic Momentum Index (SMI)

A trend-following oscillator. We compute two SMI lines — fast (5,8,8) and slow (13,8,8). When the fast SMI crosses above the slow SMI, it's a bullish trend shift. Cross below = bearish.

Think of it as: "Which direction is the momentum of the momentum pointing?"

### Williams %R

A bounded momentum oscillator (-100 to 0). Below -80 is oversold, above -20 is overbought. We care about threshold *crossings*: when W%R crosses above -80, it's leaving oversold territory (bullish). Below -20 = leaving overbought (bearish).

Think of it as: "Has price been beaten down enough that buyers are stepping back in?"

### Why Both?

Either indicator alone produces too many false signals. SMI can cross in choppy markets with no follow-through. W%R can touch oversold in a strong downtrend that keeps going lower. Together, they require *convergence* — the trend must shift AND momentum must confirm, within a configurable window.

## Signal Modes

### Lookforward Mode: Who Goes First?

The `lookforward_mode` setting controls the chronological sequence:

- **`wr_then_smi`**: W%R crosses the threshold first, then SMI must cross within `sync_window` bars to confirm. The signal fires on the SMI bar (the confirming event). This is the "momentum first, trend confirms" pattern.

- **`smi_then_wr`**: SMI crosses first, then W%R must cross to confirm. Signal fires on the W%R bar. This is "trend first, momentum confirms."

Empirically, `wr_then_smi` produced better results on our TV data (+1.30% vs +0.45%).

### Armed vs Non-Armed

**Non-armed** (default): Uses a rolling lookback window. If W%R fired within the last N bars and SMI fires now, you get a signal. Simple, vectorized, fast. But if SMI fires twice in that window, you get two signals from a single W%R event.

**Armed**: Stateful. The first indicator *arms* the system. The second indicator *fires and disarms* it. One arm = maximum one signal. Requires a bar-by-bar loop (can't be vectorized because whether a signal fires depends on whether a prior signal already consumed the arm).

```
Armed mode timeline:
  Bar 100: W%R crosses -80     → system ARMED
  Bar 103: SMI crosses up      → SIGNAL FIRES, system DISARMED
  Bar 105: SMI crosses up again → nothing (disarmed, needs fresh W%R)
  Bar 110: W%R crosses -80     → system RE-ARMED
  ...
```

## The VWAP Filter

Optional trend guard. VWAP (Volume-Weighted Average Price) acts as an intraday support/resistance level. When enabled:
- Long signals only fire when `close > VWAP` (bullish context)
- Short signals only fire when `close < VWAP` (bearish context)

In armed mode, VWAP is checked at **fire time** (entry), not arm time. Rationale: VWAP moves intraday, and you want the trend guard reflecting conditions when you actually enter the trade, not when the arming indicator triggered potentially 20 bars earlier.

## The Backtest Engine

Indicators are pre-computed on the full dataset. Then the engine iterates bar-by-bar over numpy arrays:

1. **Check exits first** — for each open position, check if the current bar's high/low hit the profit target or stop loss. Also check for opposite signals, EOD close, and expiration.

2. **Check entries** — if the signal column is +1 (long) or -1 (short) and position limits allow, enter a new trade.

3. **Record equity** — update the portfolio equity curve on every bar.

Exit levels (TP/SL) are set as fixed percentages at entry time, TradingView `strategy.exit()` style. They're checked against intrabar high/low, not just close.

## Position Sizing

Two modes:
- **`percent_of_equity`**: Each trade uses N% of current equity (default 50%)
- **`fixed`**: Fixed number of contracts per trade

With `max_concurrent_positions: 1`, only one trade is open at a time. Opposite signals close the current position before opening the new one.

## Options Support

The engine supports equities, options, or both simultaneously. Options mode:
- Selects strikes using configurable logic (ATM, 1_ITM, 1_OTM, etc.)
- Prices options using Databento market data when available, Black-Scholes as fallback
- Tracks Greeks (delta, theta, gamma) throughout the position
- Uses 100x multiplier (standard contract size)

## Data Sources

Two data paths exist because we discovered they produce different results:

**TradingView**: Export CSVs from TradingView charts. PST timestamps are converted to EST. No warm-up period needed since TV exports include enough history. This is the **source of truth** — it matches on-chart visual analysis and produces profitable backtests.

**Alpaca**: Downloaded via API, cached as monthly CSVs. Requires a 3-month warm-up period before the trading window for indicators to stabilize. Produces different OHLC values than TradingView due to bar aggregation differences — backtests consistently underperform TV data.

## Output

Each backtest run produces (in `results/YYYYMMDD/`):
- **Trade log CSV** — every entry/exit with timestamps, prices, P&L
- **Markdown report** — performance summary with all metrics
- **Equity curve PNG** — portfolio value over time
- **Drawdown PNG** — underwater chart showing peak-to-trough declines
- **Signals overlay PNG** — price chart with entry/exit markers

## The Pine Script

A parallel implementation lives in `scripts_py/latest_smi_wPr_vwap.pine` for running directly on TradingView charts. The Pine Script and Python engine are kept in sync — same signal logic, same naming conventions, same parameter semantics. The Pine Script includes a dashboard overlay showing current mode, armed state, and filter status.

## Tech Stack

- **pandas/numpy** for data manipulation and vectorized indicator computation
- **matplotlib** for chart generation
- **PyYAML** for configuration
- **pytest** for testing
- All config in `config/strategy_params.yaml` — no code changes needed to adjust parameters
