---
tags: [bug-audit, options, live-engine, streamer, eod, warmup]
---
# 019 — Comprehensive Bug Audit and Critical Fixes

**Date:** 2026-03-25

## What Happened

Ran a full line-by-line bug audit across all 70+ Python files in the codebase (src/, main_runner/, scripts_py/, tests/). 9 parallel audit agents each scanned a domain independently, then the top-priority bugs were fixed with 4 parallel fix agents. All 714 tests pass.

---

## Bugs Fixed (7 total)

### C-1 — Options intrabar stop/limit inverted for puts (`src/backtest/trade_logic.py`)

**Root cause:** The intrabar stop-loss check used `bar.low` (worst-case underlying) and profit-target used `bar.high` (best-case underlying) for ALL options. This is only correct for calls. For puts, the underlying rising (`bar.high`) hurts the put, and the underlying falling (`bar.low`) helps it — the opposite of calls.

**Fix:** Branch on `pos.option_type`. For calls: stop uses `bar.low`, target uses `bar.high`. For puts: stop uses `bar.high`, target uses `bar.low`. Variable names and comments updated to be explicit. 4 new put-side intrabar tests added.

### C-2 — Options loader returns UTC on first download, EST on cache hits (`src/data/databento_loader.py`)

**Root cause:** The fresh-download branch returned `data.to_df()` directly (Databento timestamps are UTC). The cached-data branch carefully converted to `America/New_York`. First call returned UTC timestamps; all subsequent calls returned EST — a 4-5 hour mismatch on the first run.

**Fix:** Apply the same three-branch timezone normalization (non-DatetimeIndex → parse UTC → convert; tz-naive → localize UTC → convert; tz-aware → convert) to `df_new` before saving to cache and before returning. 2 new timezone round-trip tests added.

### C-3 — Live streamer no reconnection or heartbeat (`src/live/databento_streamer.py`)

**Root cause:** `for record in client:` with no error handling. Connection drop = silent stop.

**Fix:** Extracted streaming body to `_run_once()`. Added stale-connection detection (configurable timeout, default 120s via `last_received` tracking). `run()` is now an outer reconnection loop with exponential backoff (5/10/20/40/60s, max 5 retries), logging `WARNING` on each retry and `ERROR` + raising after exhaustion. `KeyboardInterrupt` exits cleanly. 5 new reconnection tests added.

### C-4/C-5 — Live engine: no fill verification + zombie positions + thread bugs (`src/live/live_engine.py`)

**Root cause:** Three interlocked issues:
1. `buy_option()` return immediately set `self._position` without verifying the order filled.
2. If `sell_option()` raised, the position was never cleared (zombie state).
3. `_stop_poll()` called `self._poll_thread.join()` which crashed when called from within the poll thread itself (via `_poll_check` → `_close` → `_stop_poll`). Also `_start_poll()` had no guard against creating a second thread.

**Fix:**
1. After `buy_option()`, poll `_client.get_order_by_id()` up to 3 times (2s apart, via `_FILL_POLL_ATTEMPTS`/`_FILL_POLL_WAIT` constants). Only set `self._position` if `order.status == "filled"`. Log `ERROR` and skip if not confirmed.
2. Wrapped `sell_option()` in `try/except`. On failure: log `ERROR` with traceback, set `self._sell_failed = True`, record `"sell_failed": True` in trade log, then clear `self._position` anyway to prevent infinite loops.
3. Added `if threading.current_thread() is self._poll_thread: return` guard in `_stop_poll()` before joining. Added `if self._poll_thread is not None and self._poll_thread.is_alive(): return` guard in `_start_poll()`.
4. All 82 live tests updated to pre-configure `filled_order.status = "filled"` in `_make_engine()`. 11 new tests across `TestFillConfirmation`, `TestSellFailure`, `TestPollThreadGuards`.

### M-1 — EOD entry bypass: signal on last bar opens overnight position (`src/backtest/engine.py`)

**Root cause:** `not eod_closed_this_bar` only blocked entries if an existing position was just EOD-closed. If no position was open at 15:55, a signal still opened a new position that held overnight despite `eod_close: true`.

**Fix:** Added `is_eod_bar = exit_config.eod_close and (bar.hour > 15 or (bar.hour == 15 and bar.minute >= 55))` check. Entry guard becomes `bar.signal != 0 and not eod_closed_this_bar and not is_eod_bar`. 2 new tests: one with `eod_close=True` (no entry), one with `eod_close=False` (entry allowed).

### M-2 — No cash check before opening positions: implicit margin (`src/backtest/portfolio.py`)

**Root cause:** `open_position()` deducted cash without checking if it was sufficient. Cash could go negative, enabling unrealistic implicit margin.

**Fix:** For long equity and options entries, compute `required = notional + txn_cost` and raise `ValueError` if `self.cash < required`. Short equity is unchanged (short sale correctly adds cash). Engine wraps `open_position()` in `try/except ValueError` and logs a warning + skips entry. 4 new tests in `TestPortfolioInsufficientFunds`.

### M-7 — `_warmup_start` crashes on end-of-month start dates (`main_runner/base_runner.py`)

**Root cause:** Manual month subtraction produced invalid date strings (e.g., "2025-02-31") for start dates near end of month.

**Fix:** Replaced the manual loop with `pd.Timestamp(start_arg) - pd.DateOffset(months=warmup_months)`, which handles end-of-month clamping correctly (e.g., May 31 − 3 months → Feb 28). 6 new tests covering normal case, multiple clamp scenarios, and year boundary.

---

## Bugs Catalogued But Not Fixed (Remaining)

The audit also identified these lower-priority items left for future sessions:

**Medium:**
- `strike_selector.py` — fixed-date NYSE holidays lack `observance=nearest_workday` (New Year's, July 4, Christmas on weekends)
- `strike_selector.py` — `target_dte=0` always rolls to nearest Friday; doesn't support true daily 0-DTE (SYMBOL has daily expirations)
- `vwap.py` — division by zero on zero-volume bars; `inf` propagates silently through VWAP filter
- `smi_wr_generator.py` — VWAP excluded from mid-series NaN/inf diagnostic warning
- `base_runner.py` — IS/OOS equity curve split uses `engine.oos_start_idx` (index into full data) to slice trimmed equity curve — misaligned when `is_fraction > 0`
- `live_engine.py` — hardcoded `"options"` trade mode; ignores `trade_mode` config

**Low:** ~15 items including equity curve baseline point, `get_equity_df()` empty crash, silent empty backtest on bad date range, Sortino guard inconsistency, various live engine minor issues.

**Test suite:** loose SMI/WR bounds, tz-naive fixtures, deprecated `DatetimeIndex.append()`, 3 medium coverage gaps.

---

## Test Count

714 tests passing (up from 660). Added 54 net new tests:
- 4 put intrabar stop/limit (backtest)
- 6 `_warmup_start` end-of-month clamp (main_runner)
- 2 options loader timezone round-trip (data)
- 2 EOD entry guard (engine)
- 4 insufficient-funds portfolio (portfolio)
- 5 streamer reconnection (live)
- 11 fill confirmation + zombie position + thread guards (live engine)
- 11 updated existing live tests to work with new fill-verification mocks
