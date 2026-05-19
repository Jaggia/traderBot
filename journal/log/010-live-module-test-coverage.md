---
tags: [testing, live-module, alpaca-trader, databento-streamer, mock]
---
# 010 — Live Module Test Coverage

**Date:** 2026-03-12

---

## Context

A coverage audit identified that `src/live/` had zero test coverage. The three modules — `alpaca_trader.py`, `databento_streamer.py`, and `live_engine.py` — contain the entire live trading stack and are completely untested despite being the path that touches real money (paper, but still).

This session adds 52 tests across three new test files that cover the logic without requiring any real API connections.

---

## What Was Added

### `tests/test_alpaca_trader.py` (17 tests)

Tests `AlpacaTrader` with the Alpaca SDK (`TradingClient`, `OptionHistoricalDataClient`) fully mocked via `unittest.mock.patch`. Key coverage:

- `_strip_occ()` — OCC root padding `"SYMBOL   260228C00450000"` → `"SYMBOL260228C00450000"` (Alpaca expects no spaces)
- `get_option_mid_price()` — returns `(bid + ask) / 2` when both are positive; returns `None` when either is zero; returns `None` on any exception from the data client
- `buy_option()` / `sell_option()` — correct `OrderSide`, correct `qty`, OCC symbol stripped before submission
- `get_positions()` — delegates to `get_all_positions()`
- `cancel_all_orders()` — delegates to `cancel_orders()`

### `tests/test_databento_streamer.py` (18 tests)

Tests `DatabentoStreamer._handle()` and `_emit()` directly (bypasses `run()` which needs a live WebSocket). `databento` is injected into `sys.modules` as a `MagicMock` at import time. Key coverage:

- **Price scale** — Databento fixed-point integers (1 unit = 1e-9 dollars) are correctly divided by 1e9 to recover dollar prices
- **Market hours filtering** — bars at 09:25 and 16:00 are silently dropped; bars at 09:30 and 15:00 are accepted
- **Accumulation without premature callback** — 4 bars accumulate but no callback fires until `minute % 5 == 4`
- **Window boundary** — callback fires exactly once per 5-min window; `_pending` is cleared after emit
- **Two windows** — callback fires twice when two complete windows are delivered
- **OHLCV aggregation rules** — `open` = first bar's open, `high` = max of highs, `low` = min of lows, `close` = last bar's close, `volume` = sum
- **Output shape** — emitted object is a `pd.Series`; its `.name` (index label) is the first bar's timestamp

### `tests/test_live_engine.py` (17 tests)

Tests `LiveEngine` with `AlpacaTrader` fully mocked and `compute_indicators` / `generate_signals` / `build_option_position` / `check_option_exit` patched via `patch`. This isolates the engine's orchestration logic from indicator and options-pricing details. Key coverage:

- **No signal → no position** — `on_bar()` with signal=0 does not call `buy_option()`
- **Buy signal → position opened** — `on_bar()` with signal=+1 calls `buy_option()` once and sets `_position`
- **Sell signal → put position** — signal=-1 opens a put
- **No double entry** — a second signal while a position is open is ignored
- **Late-day entry gate** — no entry after 15:45
- **Exit conditions** — profit target, stop loss, EOD close, opposite signal each close the position and record the correct reason in the trade log
- **`force_close()`** — closes any open position and always calls `cancel_all_orders()`; trade log captures the forced-close reason
- **`get_closed_trades()`** — all 16 required fields present; P&L math: `(exit - entry) * contracts * 100`; multiple trade cycles accumulate correctly

---

## Implementation Notes

### Timezone gotcha

`_check_exits()` computes `pd.Timestamp(pos.expiry) - ts` where `ts` comes from the bar's tz-aware `DatetimeIndex`. The `Position.expiry` field must also be tz-aware for this subtraction to work. Test helper `_make_position()` sets `expiry=pd.Timestamp("2026-01-16", tz="America/New_York")`.

### Opposite-signal re-entry

After `_check_exits` closes a position on a bar with signal=-1, `_check_entry` is immediately called on the same bar with `signal=-1` and `_position=None` — which would open a new put. Tests that verify the exit reason must either use a timestamp after 15:45 (where entry is gated) or mock `build_option_position` to prevent the subsequent re-entry from confusing assertions.

---

## Suite Result

```
291 passed, 12 warnings in 3.11s
```

The 12 warnings are pre-existing `RuntimeWarning: divide by zero` from `monte_carlo.py` (expected for zero gross-loss scenarios in the MC tests).

---

## Files Changed

- `tests/test_alpaca_trader.py` — new (17 tests)
- `tests/test_databento_streamer.py` — new (18 tests)
- `tests/test_live_engine.py` — new (17 tests)
- `TODO.md` — checked three new live-module testing items
- `journal/docs/_state.md` — updated Testing section
- `journal/INDEX.md` — this entry added to Dev Log table
