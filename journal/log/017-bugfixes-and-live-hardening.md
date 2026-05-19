---
tags: [bugfixes, live-engine, intrabar, recovery, pnl]
---
# 017 — Bug Fixes, P&L Validation, and Live Hardening

**Date:** 2026-03-23

## Context

Merged the `claude/backtest-runner-setup-xyhao` branch into main (7 prior bug fixes: options P&L inversion, W%R NaN, can_open race, warm-up equity curve, etc.). Ran a 4-agent parallel bug sweep across the entire codebase, then hardened the live engine for paper trading.

## Bug Sweep Results

Launched 4 parallel agents scanning: backtest engine/portfolio, signals/indicators, data/options/analytics, and entry points/config. After verifying each finding:

### Real bugs found and fixed

1. **`dte_years()` truncated intraday precision** (`src/options/utils.py:6`) — HIGH. Used `.days` (integer) which returned 0 for < 24 hours remaining. Options priced at intrinsic only on last trading day. Fixed: `.total_seconds() / (365 * 86400)`. Also updated `strike_selector.py` to use the shared utility instead of an inline `.days` calculation.

2. **`check_exit()` bypassed `update_price()`** (`src/backtest/trade_logic.py:69,71`) — MEDIUM. Direct assignment to `pos.current_price` left `high_water` frozen at entry price for the life of each position. Fixed: calls `pos.update_price()` now.

3. **`_compute_shares()` unguarded division** (`src/backtest/engine.py:122`) — LOW. Zero price would crash with `ZeroDivisionError`. Added `price <= 0` guard.

### False positives rejected

Strike selection inversion (math verified correct), double warmup in Databento runner (both compute same range independently), unsafe config `.get()` (KeyError is correct behavior), 15:55 cutoff (intentional), Sharpe=0 for short backtests (display choice).

## Backtest Re-runs

Re-ran existing DB backtests with bug fixes applied. The inverted short P&L bug (item 1 from prior session) caused short trades to appear profitable when they weren't — correcting it changed absolute P&L materially and brought win rate closer to true performance. The `dte_years` fix had no impact on 0-DTE positions (same-day open/close) but matters for multi-day holds crossing midnight into expiry day.

## Live Engine Hardening

Two features added to prepare for live paper trading:

### Intrabar stop/target polling

While a position is open, a daemon thread polls the option mid-price from Alpaca every 30 seconds. If P&L breaches stop loss or profit target, closes immediately — doesn't wait for the next 5-min bar close. Critical for 0-DTE options where theta decay can blow past a 20% stop in minutes.

- Threading lock prevents races between bar-close and intrabar exit paths
- New exit reasons: `intrabar_stop`, `intrabar_target`
- Poll interval configurable via `LiveEngine(poll_interval=30)`

### Startup position reconciliation

On engine startup, queries Alpaca for any open SYMBOL option positions left from a previous crash. Reconstructs a `Position` object and resumes tracking + polling. Prevents duplicate entries on restart.

- New: `AlpacaTrader.get_option_positions()` and `parse_occ_symbol()`
- New: `LiveEngine.reconcile_positions()` called in `run_live_db.py` before streaming starts

## P&L Validation Test Update

Updated `test_pnl_vs_lambdaclass.py` to match the always-long options model from commit 4b40332. The test previously used `(entry - exit)` for shorts; now uses `(exit - entry)` universally. Removed stale pre-bugfix result directories.

## Test Count

660 tests, all passing (was 469 at last journal entry):
- +17 new live tests (intrabar polling: 6, reconciliation: 3, OCC parsing: 4, position filtering: 4)
- +2 dte_years intraday precision tests
- Plus prior additions from merged branch (validation suite, edge-case tests)
