---
tags: [ibkr, live-trading, csv-logging, databento-comparison]
---
# Session 023: IBKR Sunday Prep, Live Data Logging, and Data Source Consolidation Decision

**Date:** 2026-04-05  
**Context:** Sunday before Monday market open (9:30 AM ET). User tested IBKR live trading locally, added CSV data logging, and decided on final data source architecture.

---

## IBKR Live Trading Test ✅

Ran `./scripts_bash/run_live_ibkr.sh` locally and confirmed the live runner works end-to-end:

1. **Shell script fix:** Script sources Oh My Zsh plugins inside bash, causing syntax errors. Changed shebang to `#!/bin/zsh` to resolve (or just run Python directly, bypassing the shell script).

2. **Connection success:** IB Gateway socket (127.0.0.1:4002) connected successfully:
   - Trader client (clientId=2) connected
   - Streamer client (clientId=1) connected
   - API connection ready, both farms reachable

3. **Warmup bars loaded:** 200 historical bars loaded from Databento cache (2026-03-27 → 2026-04-01). Confirmed warm-up period is calculated correctly (today - 90 days, last 200 bars).

4. **Streaming initiated:** `IBKRStreamer` requested 1-min historical data with `keepUpToDate=True`, successfully aggregated to 5-min, ready to emit bars on close.

5. **Graceful shutdown:** Pressed Ctrl+C, engine force-closed any pending position, connection disconnected cleanly.

**Result:** Live runner is production-ready for Monday 9:30 AM ET.

---

## Live Data Logging Feature (New) ✨

Added automatic CSV logging to capture all bars and trades during a live session.

### Implementation

Modified `src/live/live_engine.py`:
- `_setup_data_logging()` creates `results/live/{YYYY-MM-DD_HHMMSS}/` directory on initialization
- `on_bar()` calls `_save_bar()` to append each 5-min bar to `live_bars.csv` (incremental writes)
- `get_closed_trades()` saves the closed trade log to `live_trades.csv` at session end

### Output Structure

```
results/live/
└── 2026-04-07_093000/          # Session timestamp (UTC or local)
    ├── live_bars.csv           # All streamed 5-min bars (OHLCV + timestamp)
    └── live_trades.csv         # Closed trade log (entry/exit details, P&L)
```

### Use Cases

1. **Compare live vs backtest:** Overlay IBKR live prices against Databento backtest to measure slippage and data differences.
2. **Re-backtest on live data:** Use `live_bars.csv` to run a backtest on the exact bars you traded, validating signal timing.
3. **Audit trail:** Full record of all fills for compliance and analysis.

### Design Notes

- Incremental writes (each bar appended immediately) ensure data persists even if the session crashes mid-trade.
- CSV headers written once on first bar; subsequent bars appended without headers.
- Trade CSV generated on `get_closed_trades()` call (end of session), allowing summary logging.

---

## Data Source Consolidation: Databento vs IBKR ❌ (Not Feasible)

### The Question

Can IBKR fully replace Databento, allowing a single data source for both backtesting and live trading?

### Investigation

Checked IBKR's historical data API capabilities:

1. **Equities history:** IBKR can provide 1-min bars via `reqHistoricalData()`, but with hard rate limits:
   - Individual bar-by-bar requests, no bulk export
   - Throttled to ~1 request per minute (typical broker policy)
   - Downloading years of 1-min data would take weeks

2. **Options history:** IBKR does **not** offer historical options market data in a usable form:
   - No public API for historical option Greeks, IV, or OHLCV
   - Cannot backtest options on IBKR alone
   - Databento is the only viable source for options backtesting

3. **Data quality:** IBKR's feed is aggregated through the broker (good, but different from XNAS.ITCH direct feed)

4. **Cost at scale:** Rate limits would require weeks of development + potential IP throttling; not practical.

### Final Decision: **Databento (Backtest) + IBKR (Live)**

| Use Case | Data Source | Rationale |
|----------|-------------|-----------|
| **Backtesting** | Databento XNAS.ITCH | Highest quality direct exchange feed; full options data; bulk download efficient |
| **Live trading** | IBKR (paper/live) | Live price feed you're actually trading on; zero API cost for paper; socket-based (no rate limits) |
| **Live warmup bars** | Databento cache | Free from local CSV (no credits spent) |

### Trade-Off: Data Mismatch

Backtests use Databento (XNAS.ITCH direct) but live trades execute on IBKR's routed feed. This causes:
- Different OHLCV bars between backtest and live (microsecond aggregation differences, routing)
- Signal firing at slightly different times
- Different fill prices (normal for any live strategy; forward-testing on small size validates the edge)

This **is expected and acceptable** — any professional trader accepts backtest ≠ live trade. The goal is to develop on the same platform (IBKR) for live forward-testing after Monday.

---

## Monday Readiness Checklist ✅

- [x] IB Gateway installed, running, logged in with paper credentials
- [x] API socket enabled (port 4002, localhost only, read-write access)
- [x] Warmup bars cached locally (Databento, free, ~90 days)
- [x] Live runner tested (IB Gateway connection works)
- [x] CSV data logging enabled (live bars + trades will be saved)
- [x] Signal pipeline ready (same SMI+WR system as backtests)
- [x] Shell script fixed (zsh shebang)

**Start time:** 9:25 AM ET (5 min before NYSE open) to allow bar 1 aggregation buffer.

---

## Lessons & Notes

1. **Databento is worth the cost** (~$0.50/month) for backtesting alone. Options data and bulk download efficiency make it irreplaceable for strategy development.

2. **IBKR paper trading is free and robust** — socket API is stable, no rate limits, direct integration via IB Gateway makes it ideal for live forward-testing.

3. **Live data logging is a game-changer** — captures the ground truth of what you actually traded, enabling rigorous backtest vs live analysis.

4. **Data source diversity is a feature, not a bug** — testing on different data sources (Databento, Alpaca, TradingView, IBKR) reveals whether edges are robust or data-specific.

---

## Commits

- `7d5743a` — feat: add live bar and trade data logging to CSV

---

## Next Steps

- Monday 9:25 AM: Launch live runner
- 9:30–16:00: Monitor live trading, watch for signals
- 16:00+: Analyze CSV output, compare to backtest
