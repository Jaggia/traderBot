---
tags: [testing, entry-logic, exit-rules, options, unit-tests]
---
# 009 — Test Coverage: entry_logic and exit_rules

**Date:** 2026-03-12

## What happened

Added `tests/test_entry_exit_logic.py` — 29 tests covering the two shared options modules that were previously untested: `src/options/entry_logic.py` and `src/options/exit_rules.py`. These are the last modules in `src/options/` to get explicit unit coverage.

## Why these two

Both modules are shared entry points used by the backtest engine **and** the live runner. A bug in `check_option_exit` or `build_option_position` would silently affect every trade in both contexts, so having explicit tests for every exit condition and every position attribute matters more than for a module that's only used in one place.

## What's covered

### `check_option_exit` — pure function, no mocks needed

The function checks five exit conditions in order; each class targets one condition in isolation:

| Test class | Condition |
|---|---|
| `TestCheckOptionExitStopLoss` | `pnl_pct <= -stop_loss_pct` (at threshold, below, above) |
| `TestCheckOptionExitProfitTarget` | `pnl_pct >= profit_target_pct` (at threshold, above, below) |
| `TestCheckOptionExitPriority` | stop_loss wins when both stop and target would fire at pct=0 |
| `TestCheckOptionExitOppositeSignal` | enabled+reversed fires; disabled skips; same direction skips; signal=0 skips |
| `TestCheckOptionExitEodClose` | 15:55 fires; 15:59 fires; disabled skips; 15:54 skips |
| `TestCheckOptionExitExpiration` | on expiry date fires; past expiry fires; before expiry skips |
| `TestCheckOptionExitZeroEntryPrice` | zero entry_price falls back to `pnl_pct=0.0`, no crash |

### `build_option_position` — mock-injected tests

`select_strike` is patched; a `MagicMock` is injected for `get_price_fn`. Tests verify the returned `Position` object:

| Test class | Assertion |
|---|---|
| `TestBuildOptionPositionOptionType` | `signal=+1` → `option_type="C"`; `signal=-1` → `option_type="P"` |
| `TestBuildOptionPositionEntryPrice` | `entry_price` equals whatever `get_price_fn` returns; fn called exactly once |
| `TestBuildOptionPositionGreeks` | delta/gamma/theta/vega are non-None and non-NaN; call delta > 0; put delta < 0 |
| `TestBuildOptionPositionContracts` | `contracts` propagates correctly for 1, 3, 10 |
| `TestBuildOptionPositionTradeMode` | `trade_mode == "options"` |

## Result

All 29 tests pass. `src/options/` now has coverage across: `position.py`, `greeks.py`, `strike_selector.py` (indirectly via the backtest tests), `entry_logic.py`, and `exit_rules.py`.
