---
tags: [strategy-pattern, engine, is-oos-split, refactor]
---
# 012 — Indicator Strategy Pattern, Engine Unification, IS/OOS Split

**Date:** 2026-03-13

## What We Did

Three architectural improvements landed together. None of them touched the signal logic or the backtest results — they clean up the internals and add the IS/OOS split capability that's been on the TODO list since the beginning.

---

## 1. Indicator Strategy Pattern

**Files changed:** `src/indicators/base.py` (new), `src/indicators/smi.py`, `src/indicators/williams_r.py`, `src/indicators/vwap.py`, `src/indicators/__init__.py`, `src/signals/smi_wr_generator.py`

Before, `compute_indicators()` in `smi_wr_generator.py` called `compute_smi()`, `compute_williams_r()`, and `compute_vwap()` directly by name — three hardcoded function calls and an if-statement for VWAP. Adding a new indicator required editing `compute_indicators()` itself.

Now:
- `src/indicators/base.py` defines the `Indicator` ABC with two abstract members: `compute(df) -> Series` and `column_name: str`.
- Each indicator module wraps its standalone function in a concrete class: `SMIIndicator`, `WilliamsRIndicator`, `VWAPIndicator`.
- `src/indicators/__init__.py` exports all three.
- `compute_indicators()` builds a list of `Indicator` objects from config, then runs a uniform loop: `df[indicator.column_name] = indicator.compute(df)`.

The standalone `compute_smi()`, `compute_williams_r()`, `compute_vwap()` functions are unchanged — the classes just delegate to them. So all existing indicator math and existing tests remain valid.

**Tests added:** `TestComputeIndicators` (8 tests) in `tests/test_signals.py` — columns present, no all-NaN series, VWAP absent/present, SMI range, W%R range, no input mutation.

---

## 2. Engine + LiveEngine Unification

**Files changed:** `src/backtest/engine.py`, `src/live/live_engine.py`

Journal 009 created `src/options/entry_logic.build_option_position()` and `src/options/exit_rules.check_option_exit()` as shared abstractions, and tested them in isolation. But both `BacktestEngine` and `LiveEngine` still had their own inline copies of that logic — ~25 lines of option entry (select_strike + compute_greeks + build Position) and ~30 lines of exit checking (pnl_pct + 5 if-elif chains).

This session wired them in:

**`BacktestEngine`:**
- Replaced the inline 25-line entry block with `build_option_position(signal, close, ts, contracts, config, get_price_fn=...)`.
- Replaced the inline 5-condition exit chain with `check_option_exit(pos, signal, ts, ...)`.
- The equities exit path (opposite_signal + eod_close) stays inline since `check_option_exit` is options-specific.

**`LiveEngine`:**
- Same substitutions. The entry block shrank from ~25 lines to 3 (call `build_option_position`, buy via trader, assign `self._position`). The exit block shrank from ~35 lines to 4 (call `check_option_exit`, close if reason).

The `get_price_fn` lambda bridges the two worlds: the shared entry builder is data-source agnostic; each engine injects its own price-fetching function. `BacktestEngine` injects `_get_option_price()` (Databento cache → BS fallback); `LiveEngine` injects its own `_get_option_price()` (Alpaca mid-price → BS fallback).

This also removed `strike_selector` and `greeks` imports from both engine files — they're now used only inside `build_option_position`.

---

## 3. IS/OOS Split Architecture

**Files changed:** `main_runner/base_runner.py`, `config/strategy_params.yaml`

Added `backtest.is_fraction` to the config (default `0.0`, meaning no split). When set to e.g. `0.7`, the first 70% of the trading period is in-sample; the last 30% is out-of-sample.

**How it works:**

1. `BaseBacktestRunner.run()` computes `oos_start = trade_start + (end_ts - trade_start) * is_fraction` and passes it to `BacktestEngine` as the new `oos_start` parameter.
2. `BacktestEngine.__init__` stores `oos_start` and resolves it to `self.oos_start_idx` during `run()` — the bar index where OOS begins. Callers can use this to slice the trade log if needed.
3. Back in the runner, the trade log and equity curve are split on `oos_start`:
   - `is_trade_log` / `is_equity_curve` — first fraction
   - `oos_trade_log` / `oos_equity_curve` — remainder
4. Metrics are printed for each half (IS first, labeled "not for evaluation"; OOS second, labeled "valid performance").
5. The primary output files (`backtest.csv`, `report.md`, charts) are always the **OOS** results — the files the dashboard reads are clean.
6. When a split is active, IS files are saved with `_IS` suffix: `backtest_IS.csv`, `report_IS.md`, `equity_curve_IS.png`, etc.

When `is_fraction = 0.0` (the default), `oos_start == trade_start`, the split code path is skipped entirely, and output is identical to before.

**Results directory naming change:** The folder format changed from `{Month-DD-YYYY}` (just the run date) to `{start}_to_{end}_run-{date}` (e.g. `February-24-2026_to_February-28-2026_run-February-27-2026`). This makes the folder self-documenting — you can see what date range was backtested without opening any files. CLAUDE.md, README.md, and journal runbook examples were updated to match.

---

## Side Fixes

- `DATA_BENTO_API_KEY` → `DATA_BENTO_PW` corrected in README (was stale from an earlier naming inconsistency)

---

## State After This Session

- `is_fraction: 0.0` in config — no-op by default; set to e.g. `0.7` to activate IS/OOS split
- Indicator Strategy Pattern in place; adding a new indicator = add a class, append to the list in `compute_indicators()`
- Both `BacktestEngine` and `LiveEngine` delegate to `build_option_position` / `check_option_exit` — single source of truth for options entry/exit logic
- TODOs closed: **In-sample / out-of-sample split architecture**, **Refactor indicators to Strategy Pattern (5A)**
