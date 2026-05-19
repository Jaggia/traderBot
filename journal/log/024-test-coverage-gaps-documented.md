---
tags: [testing, coverage, trade-logic, ema, visualize]
---
# 024 — Test Coverage Gaps Documented

**Date:** 2026-04-05

## What happened

Six test files existed in the codebase with no journal entry documenting their creation. They are catalogued here for completeness. All six were already passing as part of the test suite; this entry records what they cover and why they exist.

---

## `tests/backtest/test_trade_logic.py`

**Module:** `src/backtest/trade_logic.py`

This file covers the shared entry/exit decision layer used by both the backtest engine and the live engine. It was written alongside the intrabar options stop/limit feature (where the same bar's option high/low are checked against the threshold, mirroring the equity path). The bug-analysis report (`journal/log/bug-analysis-report.md`, issue #6) specifically called out this asymmetry between equity and options exit checking.

Notable test classes:

| Class | What it tests |
|---|---|
| `TestCheckExitEquityStopLoss` | Long/short intrabar stop fires at exact price; does not fire above |
| `TestCheckExitEquityProfitTarget` | Long/short intrabar limit fires when high/low breaches |
| `TestCheckExitEquityPriority` | Stop beats limit on a wide bar where both trigger (pessimistic bias by design) |
| `TestCheckExitEquityOppositeSignal` | Fires on reversed signal; disabled; same direction; zero signal |
| `TestCheckExitEquityEodClose` | Fires at exactly 15:55; does not fire at 15:50; disabled |
| `TestCheckExitOptions` | Delegates to `check_option_exit`; intrabar call/put stop and target via `get_option_price(field=...)` |
| `TestBuildEntryEquities` | Long/short stop-limit arithmetic; `signal=0` returns None |
| `TestBuildEntryOptions` | Delegates to `build_option_position` |
| `TestBarContextExitConfig` | Frozen dataclass invariants; `ExitResult` equality |

The `fill_price` assertions on `opposite_signal` and `eod_close` exits are intentionally omitted (a known gap flagged in the bug-analysis report, issue #33) — those tests check only `result.reason`.

---

## `tests/signals/test_ema_pipeline.py`

**Module:** `src/signals/ema_pipeline.py`

Covers System 2 (the EMA 233 intrabar-cross signal pipeline). The pipeline was documented in `journal/concepts/09-ema-233-signal-system.md` and introduced in log 012, but its direct unit tests landed separately.

Notable tests:

- `TestIdentify15mCloseBars` — verifies the internal helper that marks every 3rd 5-min bar as the close bar of its 15-min candle, and that the last bar of any series is always marked.
- `TestComputeIndicators` — checks that `ema_233`, `is_15m_close_bar`, `ema_entry_long`, and `ema_entry_short` columns are all added, and that the long/short offset arithmetic is exact.
- `TestGenerateSignals` — constructs synthetic bar sequences that force a single long or short intrabar cross; asserts signals only appear on 15-min close bars; verifies `entry_price_hint` is written correctly and equals `ema ± offset`.

---

## `tests/indicators/test_ema.py`

**Module:** `src/indicators/ema.py`

Unit tests for the `compute_ema()` function — the building block of System 2. Tests are deliberately minimal because EMA is a standard calculation; the goal is to pin the interface and catch obvious regressions.

Tests:

- Output length matches input; no NaN values (EWM with `adjust=False` starts immediately).
- Flat price series converges to that price exactly.
- Two different periods produce different series.
- Custom `column` argument works; nonexistent column raises `KeyError`.
- Default period of 233 produces correct-length output.

---

## `tests/main_runner/test_base_runner.py`

**Module:** `main_runner/base_runner.py`

Verifies the IS/OOS split path in `BaseBacktestRunner.run()`. This was a targeted regression guard for the timestamp-based split logic — a known source of boundary bugs (bug-analysis report, issues #4 and #5: inclusive-on-both-ends slicing, and fractional `oos_start` not aligning to bars).

The test instantiates a `_DummyRunner` subclass, monkeypatches all I/O (config loader, date validator, engine, metrics functions, plot functions, save functions), and injects a small 8-bar equity DataFrame. It then asserts:

- `compute_metrics` is called twice (once for IS, once for OOS).
- IS equity curve covers the correct bars; OOS equity curve covers the remainder.
- IS trade log contains only the trade with `entry_time` in the IS window; OOS trade log contains only the trade in the OOS window.
- A `results/` directory is created on disk.

---

## `tests/analysis/test_visualize.py`

**Module:** `src/analysis/visualize.py`

Smoke tests for the three chart functions: `plot_equity_curve`, `plot_drawdown`, and `plot_signals_on_price`. No pixel-level assertions are made — the tests assert only that a non-empty PNG file is written to `tmp_path`. The `Agg` matplotlib backend is set at import time to prevent any attempt to open a display.

Edge cases covered:

- `plot_equity_curve`: flat equity (zero variance); single data point; no `save_path` argument.
- `plot_drawdown`: monotonically increasing equity (zero drawdown area); equity with a known drawdown dip; no `save_path`.
- `plot_signals_on_price`: empty trade log (price-only chart); long-only trades; short-only trades.

---

## `tests/test_regression.py`

**Module:** `src/backtest/engine.py` (via full run)

A regression fixture test that pins a known-good 4-trade sequence and fails on any silent change to engine output. This was introduced after the accuracy overhaul in log 020 to guard against future regressions.

The scenario: 30 flat bars at $400, zero costs, 10 fixed contracts, 3 injected signals:

| Signal bar | Exit condition | Exit bar | PnL |
|---|---|---|---|
| Bar 5 → long | High=481 hits TP (20%) | Bar 7 | +$800 |
| Bar 12 → long | Low=319 hits SL (20%) | Bar 14 | −$800 |
| Bar 20 → long | Opposite signal | Bar 25 | $0 (+ short opens) |
| Bar 25 → short | Backtest end | Bar 29 | $0 |

The fixture CSV lives at `tests/fixtures/regression_trade_log.csv`. Regenerate with `REGEN=1 pytest tests/test_regression.py` after an intentional engine change.

---

## Why this entry

These files were discovered during a journal audit on 2026-04-05. They had no corresponding log entry despite all being live, passing test files. They are now recorded here so the journal accurately reflects test coverage history.
