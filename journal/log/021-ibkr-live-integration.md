---
tags: [ibkr, live-trading, ibkr-streamer, ibkr-trader, broker-protocol]
---
# 021 — IBKR Live Data Streaming + Paper Trading Integration

**Date:** 2026-04-02
**Branch:** `claude/live-data-paper-trading-8h5y8`

## What We Did

Added full Interactive Brokers (IBKR) support as an alternative live data source and paper broker,
alongside the existing Databento + Alpaca stack. The codebase now has two live runners: the original
`run_live_db.py` (Databento → Alpaca) and the new `run_live_ibkr.py` (IBKR → IBKR paper).

## Why IBKR

The journal decision doc `006-data-provider-comparison.md` already called IBKR the right choice
when moving to real money: consolidated data + execution, lower cost (~$4.50/month for data,
potentially waived if commissions exceed $5), and built-in paper trading. The architecture was
already designed to support this — LiveEngine is fully broker-agnostic — so it was a clean
plug-in rather than a refactor.

## Files Changed

### Modified
- **`requirements.txt`** — added `ib_insync>=0.9.86`
- **`src/live/live_engine.py`** — fill-confirmation polling now calls
  `self._trader.get_order_status(order_id)` instead of reaching into
  `self._trader._client` (Alpaca-specific internals). This makes the engine
  truly broker-agnostic.
- **`src/live/alpaca_trader.py`** — added `get_order_status()` method to satisfy
  the updated interface. Internally wraps `_client.get_order_by_id()`.
- **`config/strategy_params.yaml`** — added IBKR connection params under `live:`:
  `ibkr_host`, `ibkr_port` (default 4002 = IB Gateway paper), and two client IDs.

### Created
- **`src/live/ibkr_streamer.py`** — streams 1-min SYMBOL bars from IB Gateway via
  `ib_insync.IB.reqHistoricalData(keepUpToDate=True)`, aggregates to 5-min, and
  calls `on_bar_close`. Mirrors DatabentoStreamer including the exponential-backoff
  reconnection policy (5 attempts, 5/10/20/40/60 s), stale-connection timeout (120 s),
  and stale-bar reset at window boundaries.
- **`src/live/ibkr_trader.py`** — places market orders and fetches option quotes
  via `ib_insync`. Implements the identical public interface as AlpacaTrader:
  `get_option_mid_price`, `buy_option`, `sell_option`, `get_order_status`,
  `get_option_positions`, `cancel_all_orders`.
- **`live_runner/run_live_ibkr.py`** — entry point. Reads IBKR config from YAML,
  loads warmup bars from local Databento cache (free), wires IBKRTrader + IBKRStreamer
  to LiveEngine, streams until Ctrl+C, then prints EOD summary.
- **`scripts_bash/run_live_ibkr.sh`** — shell wrapper mirroring `run_live.sh`.
- **`tests/live/test_ibkr_streamer.py`** — 26 tests covering aggregation, market
  hours filtering, stale-bar reset, emitted Series format, and reconnection logic.
- **`tests/live/test_ibkr_trader.py`** — 24 tests covering OCC parsing, quote
  fetching, order placement, order status lookup, position parsing, and order cancellation.

## Key Design Decisions

**Broker-agnostic engine fix:** The fill-confirmation polling in `live_engine.py` previously
called `self._trader._client.get_order_by_id()` — leaking Alpaca internals into the engine.
This was refactored to a public `get_order_status(order_id) -> str` method on the trader.
Both AlpacaTrader and IBKRTrader implement it. The engine no longer knows which broker it's
talking to.

**Two separate IB client IDs:** IB Gateway rejects duplicate connections. The streamer uses
`client_id=1` and the trader uses `client_id=2` (configurable via YAML). This is a common
`ib_insync` pattern for apps that need both a data feed and an order channel simultaneously.

**avgCost division:** IBKR reports option `avgCost` per share (not per contract). Dividing by
100 gives the per-contract price, which matches AlpacaTrader's convention and what LiveEngine
expects.

**entry_iv=None on reconcile:** IBKR doesn't store the implied vol at which you entered.
`get_option_positions()` returns `entry_iv=None`, so on a crash recovery the intrabar BS
repricing falls back to config `sigma` (0.25). This matches what the original Alpaca reconcile
path does.

**Warmup still from Databento cache:** Even when running the IBKR live runner, warmup bars are
loaded from the local Databento CSV cache (no API cost). IBKR historical data could be used
instead, but the existing cache is already there, free, and accurate enough for indicator warmup.

## How to Run

**Prerequisites:**
1. IB Gateway installed, logged in with paper account
2. Settings → API → Enable socket, port 4002, localhost only, Read-Only API OFF

**Start the live runner:**
```bash
./scripts_bash/run_live_ibkr.sh
# or directly:
python live_runner/run_live_ibkr.py
```

**Expected log output:**
```
Warmup: 200 bars (2025-12-... → 2026-03-...)
IBKRTrader connected at 127.0.0.1:4002 (clientId=2)
IBKRStreamer connected to IB Gateway at 127.0.0.1:4002 (clientId=1)
IBKRStreamer: streaming 1-min SYMBOL bars (keepUpToDate=True)
5-min bar closed: 09:34 O=... H=... L=... C=...
ENTERED C 480.0 exp=2026-04-01 entry_price=1.23 ...
INTRABAR TARGET — C 480.0 mid=1.48 pnl=20.3% ...
Session complete. 1 trade(s) closed.
```

## Test Count

New tests: 50 (26 streamer + 24 trader). Existing suite remains at 738/738.
