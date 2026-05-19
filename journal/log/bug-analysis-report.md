---
tags: [bug-audit, severity, options, profit-factor, dashboard]
---
# Comprehensive Bug Analysis Report

**Date:** 2026-03-24
**Scope:** Full line-by-line review of `main_runner/`, `src/`, `tests/`, and `scripts_py/`

---

## Critical Bugs

### 1. Options position sizing uses underlying price instead of option premium
**File:** `src/backtest/engine.py`, line ~182
**Impact:** Massively oversized option positions when `sizing_mode=percent_of_equity`

`_compute_shares(bar.close)` uses the underlying equity price (e.g., ~$480 for SYMBOL) instead of the option premium (e.g., ~$5). With $100K equity and 50% sizing: `int(50000 / 480) = 104` contracts instead of `int(50000 / 5) = 10000`. The sizing is off by ~100x in the wrong direction -- positions are undersized relative to intended equity allocation but the notional exposure (`104 * 100 shares * $480 = ~$5M`) vastly exceeds the portfolio. Only affects `percent_of_equity` mode; `fixed` mode sidesteps this.

### 2. `round(float('inf'), 2)` crashes when all trades are winners
**File:** `src/analysis/metrics.py`, line ~80
**Impact:** `OverflowError` runtime crash

`compute_profit_factor()` returns `inf` when `gross_loss == 0`. The caller does `round(compute_profit_factor(...), 2)`, and `round(float('inf'), 2)` raises `OverflowError` in Python. This crashes `compute_metrics()` whenever a backtest has zero losing trades.

### 3. Dashboard `discover_runs()` never matches actual folder names
**File:** `scripts_py/dashboard.py`, lines 72-75
**Impact:** Dashboard is permanently empty

`discover_runs()` tries to parse folder names with `strptime(name, "%B-%d-%Y")`, but `base_runner.py` creates folders named like `February-10-2026_to_February-13-2026_run-February-27-2026_smi-then-wr`. This compound format never matches the simple `%B-%d-%Y` pattern, so every result folder is skipped.

---

## High-Severity Bugs

### 4. IS/OOS equity curves overlap at the split boundary
**File:** `main_runner/base_runner.py`, lines 230-231
**Impact:** One bar counted in both in-sample and out-of-sample metrics

```python
is_equity_curve = equity_curve[:oos_start]
oos_equity_curve = equity_curve[oos_start:]
```

Pandas DatetimeIndex slicing is inclusive on both ends, so the bar at `oos_start` appears in both curves. OOS metrics are slightly contaminated by IS data.

### 5. `oos_start` fractional timestamp may not align to any bar
**File:** `main_runner/base_runner.py`, lines 174-182
**Impact:** IS/OOS split point lands between bars, causing unpredictable slicing

`oos_start = trade_start + (end_ts - trade_start) * is_fraction` produces a timestamp like `11:47:30` that doesn't match any 5-min bar. The pandas slice behavior with non-matching timestamps is implementation-dependent.

### 6. Option exits don't use intrabar high/low (asymmetry with equities)
**File:** `src/backtest/trade_logic.py`, lines 92-101
**Impact:** Missed option stop/profit triggers within a bar

Equity exits check `bar.high`/`bar.low` for intrabar stop/limit hits. Option exits only check `pnl_pct` at bar close. An option could hit its stop intrabar, recover by close, and the exit never triggers. This creates inconsistent behavior between equity and options modes.

### 7. `get_indexer(method="nearest")` returns arbitrarily stale option prices
**File:** `src/backtest/engine.py`, line ~108
**Impact:** Option valuations based on prices from minutes/hours ago

When option data has gaps (no trades), `nearest` returns the closest available price with no staleness guard. A price from 30+ minutes ago could be used for mark-to-market or exit decisions.

### 8. Duplicate environment variable check (copy-paste bug)
**File:** `scripts_py/download_options_databento.py`, line 89
**Impact:** Missing fallback API key variable

`api_key = os.getenv("DATA_BENTO_PW") or os.getenv("DATA_BENTO_PW")` checks the same variable twice. The `or` branch is dead code. One should likely be a different variable name.

---

## Medium-Severity Bugs

### 9. Armed mode allows same-bar arm+fire
**File:** `src/signals/smi_wr_pipeline.py`, lines 43-51
**Impact:** Possible violation of temporal ordering intent

If both `arm_events[i]` and `fire_events[i]` are True on the same bar, the arm happens first, then the fire check sees `armed == True` and fires immediately. If armed mode's intent is strict temporal sequencing (arm *before* fire on separate bars), this is a logic error.

### 10. `--mc` flag in `sys.argv[1]` position breaks date validation
**File:** `main_runner/base_runner.py`, lines 100-111
**Impact:** Unhelpful error message when flags precede dates

The positional parsing grabs `sys.argv[1]` and `sys.argv[2]` as dates. Passing `--mc` first causes `pd.Timestamp("--mc")` to fail with a confusing error.

### 11. 0-DTE options closed immediately on expiry day
**File:** `src/options/exit_rules.py`, line 40
**Impact:** Options closed at first bar of expiry day instead of EOD

`pd.Timestamp(ts).date() >= pd.Timestamp(pos.expiry).date()` triggers as soon as the current date equals expiry. For 0-DTE options, the position is closed at the first bar, missing the entire trading day.

### 12. Profit factor inconsistency between metrics and Monte Carlo
**File:** `src/analysis/metrics.py` vs `src/analysis/monte_carlo.py`
**Impact:** Different profit factor values for the same trade sequence

`compute_profit_factor` uses `arr[arr < 0]` (strict), excluding breakeven trades from losses. Monte Carlo's `_compute_mc_metrics` uses `sim_pnls <= 0` (non-strict), including breakeven trades. Results diverge when there are zero-P&L trades.

### 13. Greeks rounding loses precision for far-OTM options
**File:** `src/options/greeks.py`, lines 141-147
**Impact:** `target_delta` strike selection treats all far-OTM candidates as identical

Delta rounded to 4 decimal places means deep OTM options with delta ~0.00003 all become 0.0. The `min(abs(delta) - target)` comparison in strike selection can't distinguish between them.

### 14. No guard for `sigma <= 0` in Black-Scholes pricer
**File:** `src/options/option_pricer.py`, line 36
**Impact:** `ZeroDivisionError` if sigma is zero/negative

`compute_greeks` guards against `sigma <= 0`, but `black_scholes_price` does not -- it calls `_d1d2` directly, which divides by `sigma * sqrt(T)`.

### 15. `_warmup_start()` drops day component
**File:** `main_runner/base_runner.py`, lines 114-122
**Impact:** Warm-up period rounded to month boundary

Returns `"2025-08"` instead of `"2025-08-10"` for a 3-month warm-up from `"2025-11-10"`. The warm-up always starts from the 1st of the month rather than the exact date.

### 16. `_needs_update` first-of-month boundary bug
**File:** `src/data/alpaca_loader.py`, lines 63-72
**Impact:** Stale data not refreshed on the 1st of each month

`today.day - 1 = 0` on the 1st, and `last_date.day` (28-31) is always `>= 0`, so the function incorrectly returns "up to date."

### 17. Databento `strike_price` float equality comparison
**File:** `src/data/databento_loader.py`, line 129
**Impact:** Contract definition lookup silently returns None

`df['strike_price'] == float(strike)` can fail due to floating-point representation. Additionally, Databento may return `strike_price` in fixed-point units (x10^9), making the comparison always fail.

### 18. Databento `put_call` column format uncertainty
**File:** `src/data/databento_loader.py`, line 131
**Impact:** Contract lookup returns None if format is "CALL"/"PUT" vs "C"/"P"

Code passes `option_type.upper()` (e.g., "C"), but Databento may return `"CALL"` or `"PUT"` depending on client version.

### 19. Engine opens 0-contract positions
**File:** `src/backtest/engine.py`, lines 182-196
**Impact:** Zero-notional positions clutter trade log, skew metrics

No guard against `contracts == 0` after `_compute_shares`. A 0-contract position has zero P&L but still gets logged, potentially inflating win rate or trade count.

### 20. No validation that option `entry_price > 0`
**File:** `src/options/entry_logic.py`, line ~40
**Impact:** Positions opened with zero/negative entry price cause division by zero in `pnl_pct`

`build_option_position` always returns a Position. If Black-Scholes returns 0 or negative, the position has a bad entry price that causes `(current - entry) / entry` to fail.

### 21. Options cache timezone inconsistency on reload
**File:** `src/data/databento_loader.py`, lines 147-154
**Impact:** Date boundary misalignment causing unnecessary re-downloads or stale data

Saved CSV timestamps may lose timezone info on round-trip. UTC midnight vs EST midnight can shift date boundaries by a day.

---

## Low-Severity Bugs

### 22. Breakeven trades counted as losses
**File:** `src/analysis/metrics.py`, line 71
`trade_log[trade_log["pnl"] <= 0]` counts PnL=0 as losses, inflating loss count.

### 23. No Sharpe ratio for backtests under ~2 months
**File:** `src/analysis/metrics.py`, line 16
Monthly resampling returns `< 2` periods for short backtests, triggering early return of 0.0.

### 24. Mid-series NaN silently suppresses crossovers
**File:** `src/signals/smi_wr_pipeline.py`, lines 11-18
If SMI produces a mid-series NaN (from `range_smooth=0`), crossover detection silently swallows the event.

### 25. `between_time("09:30", "16:00")` includes the 16:00 bar
**Files:** `src/data/alpaca_loader.py` line 50, `src/data/databento_loader.py` line 252
Inclusive upper bound may include an after-hours bar.

### 26. Relative paths assume CWD is project root
**File:** `main_runner/base_runner.py`, lines 28-30, 65-66, 279
Config and results paths are relative. Running from a different directory breaks everything.

### 27. `ensure_equity_data` silently skipped with only one date arg
**File:** `main_runner/run_backtest_db.py`, lines 27-36
Only runs when both start and end args are provided.

### 28. `sys.exit(0)` on insufficient MC trades masks failure
**File:** `main_runner/run_monte_carlo.py`, line 56
Exits with success code when there are too few trades for Monte Carlo.

### 29. Inline `--mc` path has no minimum-trade guard
**File:** `main_runner/base_runner.py`, lines 318-320
The standalone runner requires 5+ trades; the inline path does not.

---

## Test Suite Issues

### 30. Test documents a bug instead of testing correct behavior
**File:** `tests/backtest/test_engine.py`, lines 573-588
`test_percent_of_equity_sizing_zero_shares_no_entry` encodes the 0-contract bug (issue #19) as expected behavior. If someone fixes the bug, this test incorrectly fails.

### 31. Weak assertions throughout test_portfolio.py
**File:** `tests/backtest/test_portfolio.py`
- Line 85: `assert equity > 0` (should check exact value)
- Line 56: `assert cash < 100_000` (should check exact value)
- No test for close_position P&L correctness
- No test for short equity position cash flows
- No test for options position cash flows

### 32. Test IDs swapped for short option scenarios
**File:** `tests/validation/test_pnl_vs_lambdaclass.py`, lines 83-84, 93-97
`"short_profit"` labels a scenario with expected PnL of -2000 (a loss), and `"short_loss"` labels +2000 (a profit). IDs are inverted.

### 33. Missing fill_price assertions in exit tests
**File:** `tests/backtest/test_trade_logic.py`, lines 135, 165
`test_opposite_signal_exit` and `test_eod_close` only check `result.reason`, not `result.fill_price`.

### 34. Dead code in live engine test
**File:** `tests/live/test_live_engine.py`, lines 414-419
A `with` block creating patches ends with `pass` -- creates mocks but never calls the engine.

### 35. No edge case coverage for short positions, options cash flows, `can_open(slots=2)`
Multiple test files have no coverage for these code paths.

---

## Coding Standard Violations

### 36. `print()` instead of logging in scripts
**Files:** `scripts_py/armed_mode_comparison.py`, `scripts_py/validate_aggregator.py`
Both use `print()` throughout, no `setup_logging()`, no `try/except` wrapper.

### 37. Private function imports across modules
**Files:** `scripts_py/download_and_aggregate_databento.py` (line 28), `scripts_py/download_options_databento.py` (line 137)
Importing `_`-prefixed functions from other modules creates fragile coupling.

### 38. No CLI argument validation in download scripts
**File:** `scripts_py/download_and_aggregate_databento.py`, lines 37-38
Dates from `sys.argv` used without validation.

### 39. EOD close followed by immediate re-entry on same bar
**File:** `src/backtest/engine.py`, lines 174-196
**Impact:** Position opened at 15:55 that sits overnight

The engine loop processes exits first (step 1), then entries (step 2). If a position is closed by `eod_close` at 15:55 and a signal exists on that same bar, a new position opens immediately. For `opposite_signal` reversal this is arguably intended, but for EOD close it's a clear bug -- the engine closes because it's end of day, then immediately reopens.

### 40. `pnl_pct` in trade log excludes transaction costs while `pnl` includes them
**File:** `src/backtest/portfolio.py`, lines 93-125
**Impact:** Inconsistent P&L reporting

The `pnl` field (line ~115) includes transaction costs, but `pnl_pct` calls `position.pnl_pct()` which does NOT include transaction costs. Any analysis using `pnl_pct` overstates performance relative to the dollar `pnl`.

### 41. `load_cached_csvs` never applies row-level date filtering
**File:** `src/data/alpaca_loader.py`, lines 162-177
**Impact:** Returns extra data outside requested date range

The function accepts `start`/`end` params and filters which CSV *files* to load by filename YYYYMM, but never filters the actual *rows* within those files. Requesting `start="2025-11-15"` returns all of November (from Nov 1). The Databento and TradingView loaders both apply post-load date filtering; Alpaca does not. This means warm-up periods get extra unintended data.

### 42. Timezone comparison uses string vs object -- always evaluates True
**File:** `src/data/databento_loader.py`, line ~322
**Impact:** Redundant `tz_convert` call on every load

`df.index.tz != "America/New_York"` compares a `pytz.timezone` object to a string, which is never equal regardless of the actual timezone. The `tz_convert` branch always executes. In practice it's a no-op when the data is already EST, but the logic is wrong. Should be `str(df.index.tz) != "America/New_York"`.

### 43. `tz_localize` on already tz-aware TradingView data crashes
**File:** `src/data/tradingview_loader.py`, lines 10-15
**Impact:** `TypeError` if CSV contains tz-aware datetime strings

If the CSV's datetime column has timezone offsets (e.g., `"2025-01-15 09:30:00-08:00"`), `pd.to_datetime()` produces tz-aware timestamps. Calling `.dt.tz_localize("America/Los_Angeles")` on tz-aware data raises `TypeError: Cannot localize tz-aware Timestamp`. Should check `dt.tz is None` first.

---

## Design Concerns (Not Bugs, But Worth Noting)

- **Stop loss pessimistic bias:** On wide-range bars where both stop and limit are hit, stop always wins (checked first). Without tick data this is unavoidable, but creates a consistently pessimistic bias.
- **`get_target_expiry` Saturday landing:** If `target_dte` lands on Saturday, it jumps to the *next* Friday (6 days later), potentially extending DTE beyond intent.
- **Holiday rollback only goes back one day:** If Friday expiry is a holiday and Thursday is too (extremely rare), the rollback to Thursday lands on another holiday.
