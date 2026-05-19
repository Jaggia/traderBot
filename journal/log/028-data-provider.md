---
tags: [data-provider, protocol, ports-adapters, refactor, runners]
---
# 028 — Data Provider Module

**Date:** 2026-05-01

## What

Unified the three data loaders (Alpaca, TradingView, Databento) behind a single `DataProviderProtocol` interface, following the same `@runtime_checkable` Protocol pattern used by `broker_protocol.py`.

## Why

The three loaders (`alpaca_loader`, `tradingview_loader`, `databento_loader`) all return the same DataFrame schema (OHLCV, tz-aware DatetimeIndex), but had no shared interface. Each runner duplicated loader-calling logic with subtle differences (TV skips end-date trimming, DB needs a pre-download check). This violated DRY and made adding new data sources harder than necessary.

## What Changed

### New file: `src/data/provider.py`

- `DataProviderProtocol` — runtime-checkable Protocol with 4 methods:
  - `load_equity_data(start, end)` — load OHLCV bars
  - `ensure_data(start, end, warmup_months)` — pre-download/cache (no-op for Alpaca/TV)
  - `get_source_name()` — returns short identifier
  - `should_trim_end()` — TV returns `False` (loader already filters), others `True`
- Three private concrete providers: `_DatabentoProvider`, `_AlpacaProvider`, `_TradingViewProvider`
- `create_provider(config)` factory — reads `config["data"]["data_source"]`, supports aliases (`"db"` → `"databento"`, `"tradingview"` → `"tv"`)

### Simplified runners

- `BaseBacktestRunner.load_data()` is now concrete (not abstract) — delegates to provider
- `pre_load_check()` delegates to `provider.ensure_data()`
- `trim_end_date()` checks `provider.should_trim_end()`
- All three concrete runners (`run_backtest_db.py`, `run_backtest_with_alpaca.py`, `run_backtest_tv.py`) reduced to just `source_name` + `warmup_months` class attributes — no more loader imports or method overrides

### Tests

- `tests/data/test_provider.py` — 19 tests: protocol compliance, factory dispatch, loader delegation (mocked), should_trim_end, ensure_data behavior
- Updated `tests/main_runner/test_base_runner.py` — monkeypatched `create_provider` and `count_trials` in test harnesses

### What didn't change

- Existing loaders are untouched — providers are thin wrappers
- `DatabentoOptionsLoader` stays separate (used by `BacktestEngine`, not runners)
- All 536 core tests pass

## Design Decisions

1. **Protocol over ABC**: Matches `broker_protocol.py` pattern. Concrete classes don't inherit — duck typing with `isinstance()` support.
2. **Private concretes**: Only the Protocol and factory are public. Callers never import `_DatabentoProvider` directly.
3. **`should_trim_end()` on protocol**: Captures the TV loader's existing behavior (already filters end dates internally) as a provider property rather than a runner override.
