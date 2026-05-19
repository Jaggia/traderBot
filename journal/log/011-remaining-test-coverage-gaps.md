---
tags: [testing, coverage, databento-options, logging-config]
---
# 011 — Remaining Test Coverage Gaps

**Date:** 2026-03-13

## What We Did

Closed the four remaining test coverage gaps identified in the previous sessions. The suite went from 291 → 314 tests, all passing.

---

## Gap 1: `src/backtest/engine.py` — Options Mode

**File:** `tests/test_engine.py` — new `TestEngineOptionsMode` class (8 tests)

The engine's options path (`build_option_position` → mark-to-market → `check_option_exit`) was never exercised through `engine.run()`. The equities path already had thorough coverage; options had none.

**Approach:**
- Added `_opts_config()` helper (mirrors existing `_config()` but with `trade_mode: "options"`)
- Added `_make_option_pos()` to build a synthetic `Position` with known `entry_price=5.0`
- Added `_run_options()` using `contextlib.ExitStack` to stack patches:
  - `build_option_position` → `side_effect` that returns the correct-direction mock Position
  - `BacktestEngine._get_option_price` → `return_value=fixed_option_price` (optional; omitted for the BS-fallback test)

**Tests:**
- `test_options_entry_buy_signal` / `test_options_entry_sell_signal` — verifies call/put entry fields
- `test_options_profit_target_exit` — `_get_option_price` returns 7.5 (entry=5.0, tp=50%) → `profit_target`
- `test_options_stop_loss_exit` — returns 2.4 (sl=50%) → `stop_loss`
- `test_options_eod_close_exit` — bars start at 15:30, entry at 15:40 → `eod_close` at 15:55
- `test_options_opposite_signal_exit` — long then -1 signal → `opposite_signal`
- `test_options_pnl_multiplier` — entry=5.0, exit=7.5, 1 contract → pnl=$250 (100× multiplier verified)
- `test_bs_fallback_count_increments` — no `DATA_BENTO_PW` → `_bs_fallback_count > 0` after run

---

## Gap 2: `src/data/databento_loader.py` — DatabentoOptionsLoader

**File:** `tests/test_data_loaders.py` — new `TestDatabentoOptionsLoader` class (5 tests)

`DatabentoOptionsLoader.load_option_bars()` and `get_contract_definition()` had no tests. The equities path was already covered.

**Approach:** `patch("src.data.databento_loader.db")` at the module level replaces the `databento` import with a `MagicMock`. Combined with `patch("src.data.databento_loader.time.sleep")` in retry tests to avoid real delays.

Added `_write_options_cache_csv()` helper: writes a CSV with a DatetimeIndex that `pd.read_csv(path, index_col=0, parse_dates=True)` can read back correctly.

**Tests:**
- `test_cache_hit_skips_api_call` — pre-write cache CSV covering the window → `timeseries.get_range.assert_not_called()`
- `test_cache_miss_downloads_and_saves` — no cache → API called, CSV written to `cache_dir`
- `test_retry_succeeds_on_third_attempt` — side_effect raises twice then succeeds → result returned, `call_count == 3`
- `test_retry_exhausted_raises` — raises 3 times → `pytest.raises(Exception)`
- `test_get_contract_definition_returns_raw_symbol` — mock df with matching strike/expiry/put_call → correct symbol returned

---

## Gap 3: `src/data/alpaca_loader.py` — Download Functions

**File:** `tests/test_data_loaders.py` — new `TestAlpacaDownloadFunctions` class (7 tests)

`_needs_update()`, `download_bars()`, and `update_to_present()` had no tests. `load_cached_csvs()` was already covered.

**Approach:**
- `_needs_update()` is pure filesystem — use `tmp_path` directly, no mocks. Write minimal CSVs with `_write_alpaca_month_csv()`.
- `download_bars()`: patch `_get_client` + `StockBarsRequest`. The mock `bars.df` is a DataFrame with a UTC DatetimeIndex that survives `reset_index()` → `tz_convert` → `between_time()`.
- `update_to_present()`: patch `_download_month` (and `globmod.glob` for the no-files variant).

**Key detail for RTH filter test:** Mock DataFrame has 3 bars at UTC 13:00 (→08:00 EST), 14:30 (→09:30 EST), 21:05 (→16:05 EST). After `between_time("09:30", "16:00")` only the 09:30 bar survives.

**Tests:**
- `test_needs_update_missing_file_returns_true`
- `test_needs_update_complete_past_month_returns_false` — Jan 2024, last_date.day=30 ≥ 29 → False
- `test_needs_update_incomplete_past_month_returns_true` — last_date.day=5 < 29 → True
- `test_download_bars_returns_ohlcv` — mock client → OHLCV columns present
- `test_download_bars_filters_to_rth` — pre/post-market bars removed
- `test_update_to_present_calls_download_month` — at least 2 calls (1 month × 2 timeframes)
- `test_update_to_present_no_existing_files_uses_current_year` — `glob` mocked empty → still calls `_download_month`

---

## Gap 4: `src/utils/logging_config.py`

**File:** `tests/test_logging_config.py` — new file (3 tests)

`setup_logging()` had zero test coverage.

**Complication:** pytest's log-capture plugin installs `LogCaptureHandler`s on the root logger between `setup_method` and the test body. Clearing handlers in `setup_method` is not enough — pytest re-adds its handlers before the test runs.

**Solution:** `patch.object(logging.root, "handlers", [])` replaces the instance's handler list for the duration of the `with` block. `setup_logging()` sees an empty list → adds its StreamHandler → assertion runs inside the same block.

`setup_method`/`teardown_method` only save/restore the root logger *level* (unaffected by pytest's log capture).

**Tests:**
- `test_adds_handler` — with empty handler list → `len >= 1` after `setup_logging()`
- `test_idempotent` — two calls → count stays the same
- `test_custom_level_applied` — `setup_logging(logging.DEBUG)` → `root.level == DEBUG`

---

## Final State

- **314 tests, 0 failures**
- `engine.py` options path: fully exercised end-to-end through `run()`
- `DatabentoOptionsLoader`: cache, retries, and contract resolution covered
- `alpaca_loader` download functions: filesystem checks + API mocking covered
- `logging_config.py`: handler addition, idempotency, and level-setting covered
