---
tags: [metrics, refactor, sharpe, psr, vwap, indicators]
---
# 027 — Indicator Refactor, Advanced Analytics, and VWAP Validation

**Date:** 2026-04-12

## What We Did

This session delivered several improvements from the `TODO_WIP.md` across architecture, analytics, and testing.

---

## 1. Indicator Architecture Refactor

**Files changed:** `src/indicators/base.py` (new), `src/indicators/smi.py`, `src/indicators/williams_r.py`

Consolidated duplicated mathematical primitives into a central `base.py` module to improve reusability and maintainability.

- **`rolling_high_low(df, period)`**: Extracted from `smi.py` and `williams_r.py`. Centralises the common operation of finding local extremes.
- **`double_ema_smooth(series, span1, span2)`**: Extracted from `smi.py`. Provides a generic primitive for indicators requiring multi-stage smoothing (e.g., SMI, TSI).
- Updated both `compute_smi()` and `compute_williams_r()` to use these shared helpers.

---

## 2. Advanced Analytics: Trade-level Sharpe & PSR

**Files changed:** `src/analysis/metrics.py`

Expanded the performance reporting with more sophisticated quant metrics that are robust to episodic trading strategies.

- **Trade-level Sharpe Ratio**: Added `trade_sharpe` using the formula `(mean(pnl_pct) / std(pnl_pct)) * sqrt(trades_per_year)`. Unlike the daily/monthly Sharpe, this is "honest" for strategies that are not always invested.
- **Probabilistic Sharpe Ratio (PSR)**: Implemented López de Prado's PSR. This metric estimates the probability that the true Sharpe Ratio is above a benchmark (0.0), correcting for sample size (number of trades), skewness, and kurtosis.
- **Statistical Helpers**: Added pure-NumPy implementations of `_skewness()`, `_kurtosis()`, and `_norm_cdf()` (via `math.erf`) to support PSR without adding a heavy `scipy` dependency.
- **Report Updates**: The markdown report now includes "Trades per Year", "Trade-level Sharpe", and "Probabilistic Sharpe (PSR)". The existing Sharpe Ratio was relabeled to clarify it assumes an "always-invested" profile.

---

## 3. Architecture & Time Utils

**Files changed:** `src/utils/time_utils.py` (new), `src/backtest/engine.py`, `src/analysis/metrics.py`

- **Market Hours Window**: Extracted the logic for calculating market open/close boundaries (09:30–16:00 EST) into `get_market_hours_window()` in `src/utils/time_utils.py`.
- **Monthly Returns Helper**: Extracted the TradingView-aligned resampling logic into `_compute_monthly_returns()` within `metrics.py`.
- Updated `BacktestEngine._get_option_price()` to use the new time utility.

---

## 4. VWAP Validation

**Files added:** `tests/indicators/test_vwap_manual.py`

Added a high-confidence characterization test for the VWAP indicator.

- **Manual Verification**: Uses a hand-calculated 5-bar sequence spanning two days.
- **Edge Case Coverage**: Specifically asserts on daily reset behavior and the forward-filling of zero-volume bars (halted trading).
- **Result**: Verified 100% accuracy against manual calculations using the project's virtual environment.

---

## State After This Session

- Indicator primitives are DRY and reusable in `src/indicators/base.py`.
- Backtest reports provide deeper statistical confidence via PSR and trade-level SR.
- Codebase is cleaner with unified time-window and return-calculation helpers.
- VWAP implementation is empirically verified.
