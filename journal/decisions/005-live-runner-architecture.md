---
tags: [live-trading, databento, alpaca, architecture, warmup]
---
# Decision: Live Runner Architecture (Databento + Alpaca)

**Date:** 2026-02-28

## Why This Split

Databento has higher-quality market data (XNAS.ITCH, direct exchange feed) but no paper trading. Alpaca has paper trading but lower-quality data. The split lets us get the best of both: real data quality for signals, real execution infrastructure for orders.

## Why Not Polygon.io or IBKR?

See `decisions/006-data-provider-comparison.md` for the full breakdown. Short version:
- Polygon.io: clean API but still needs Alpaca for execution (adds cost without simplifying)
- IBKR: much cheaper ($4.50/month) and consolidates everything, but complex TWS API — better choice when moving to live trading

## Architecture Decisions

### Warmup from local cache, not Live API

200 bars of warmup are loaded from local Databento CSV files (zero credits). Databento Live streaming only starts after warmup. Credits are only spent during active market hours streaming.

### Signal pipeline reused unchanged

`compute_indicators()` and `generate_signals()` from the backtest are called on a rolling 300-bar buffer on every bar close. No indicator code duplication. Same config file, same parameters.

### Signal transition detection

Only enter on `0 → signal` transitions (new signal, not continuation of an existing one). Prevents re-entering after an exit if the signal stays active.

### V1 exits: opposite signal + EOD only

Options don't support bracket orders on Alpaca. Intrabar stop/target would require polling Alpaca's position P&L every ~30 seconds — deferred to V2. V1 matches backtest config: `opposite_signal: true`, EOD close at 15:50.

### OCC symbol passthrough

`select_strike()` generates the OCC symbol with space-padded root (`"SYMBOL   260228C00450000"`). Alpaca expects no spaces (`"SYMBOL260228C00450000"`). `_strip_occ()` handles the conversion.

## Known V1 Limitations

1. No intrabar stop/target — 0-DTE options can decay to near-zero with no protection
2. In-memory state only — process crash mid-position won't reconcile with Alpaca on restart
3. Market orders only — limit orders at mid-price would reduce slippage in production
4. Single symbol per run — multi-symbol support would require parallelising the stream subscription

## Files

- `src/live/databento_streamer.py` — XNAS.ITCH WebSocket, 1m→5m aggregation
- `src/live/alpaca_trader.py` — paper TradingClient wrapper
- `src/live/live_engine.py` — rolling buffer, position state machine
- `live_runner/run_live_db.py` — entry point
