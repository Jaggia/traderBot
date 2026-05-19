---
tags: [runbook, live-trading, paper-trading, eod]
---
# Runbook: Run Live Paper Trading

Two runners available: **Databento + Alpaca** (original) or **IBKR** (tested 2026-04-05). This guide covers both.

## Prerequisites (All Runners)

- Market must be open (Mon–Fri, 09:30–16:00 EST)
- 200 warmup bars pre-cached locally (loaded from `data/DataBento/equities/SYMBOL/5min/`)

## Option A: Databento + Alpaca (Original)

### Prerequisites
- Alpaca paper account with Level 3 options enabled
- These env vars in `~/.zshrc`:
  - `DATA_BENTO_PW` — Databento Live API key
  - `ALPACA_UN` — Alpaca paper username/key
  - `ALPACA_PW` — Alpaca paper secret

### Run It
```bash
cd /path/to/project
python live_runner/run_live_db.py
# Or: ./scripts_bash/run_live.sh (fix shebang if needed)
```

## Option B: IBKR (New — Tested 2026-04-05)

### Prerequisites
- IB Gateway installed + running (or Trader Workstation)
- Logged in with **paper account credentials**
- API socket enabled: **Settings → API → Enable Active X and Socket Clients**
  - Socket port: **4002** (IB Gateway paper) or **7497** (TWS paper)
  - ✗ Uncheck "Read-Only API" (need write access for orders)
- No env vars needed (local socket connection only)

### Run It
```bash
cd /path/to/project
python live_runner/run_live_ibkr.py
# Or: ./scripts_bash/run_live_ibkr.sh (zsh shebang)
```

---

## What Happens (Both Runners)

Start **around 09:25 EST** (5 min before open) to pre-load the first aggregation window.

## What Happens (Both Runners)

1. Loads last 200 5-min bars from local Databento cache (free — no API credits spent)
2. Connects to data source (Databento XNAS.ITCH or IBKR via IB Gateway)
3. Streams 1-min bars, aggregates 5× 1-min → 1× 5-min bar
4. On each 5-min bar close: recomputes SMI + W%R + VWAP, checks for signals
5. Signal detected → places order (Alpaca market order or IBKR market order)
6. Exit on opposite signal or EOD (auto-close at 15:55)
7. Saves live data to CSV for later analysis (see **Live Data Output** below)

## Stopping

Press **Ctrl+C** — the runner catches it, closes any open position, cancels pending orders, exits cleanly.

## EOD

Any open position is automatically closed at **15:50 EST**. You don't need to babysit it after starting.

## Checking Orders

**Alpaca:** Log in to [Alpaca paper dashboard](https://app.alpaca.markets) → Orders / Positions

**IBKR:** Log in to [IB Gateway / TWS] → Accounts & Positions

## Live Data Output

All streaming bars and trades are automatically saved to CSV for analysis:

```
results/live/
└── 2026-04-07_093000/          # Session timestamp
    ├── live_bars.csv           # All 5-min bars received (OHLCV + timestamp)
    └── live_trades.csv         # Closed trades (entry/exit, P&L, reason)
```

### Use Cases

1. **Compare backtest vs live:** Overlay IBKR live prices vs Databento backtest to measure slippage
2. **Re-backtest on live data:** Use `live_bars.csv` to run a backtest on exact traded bars, validating signal timing
3. **Audit trail:** Full record of all fills for compliance

### Example Analysis

```python
import pandas as pd

# Load live bars
bars = pd.read_csv("results/live/2026-04-07_093000/live_bars.csv", index_col=0, parse_dates=True)
# → Index: timestamp, Columns: open, high, low, close, volume

# Load trades
trades = pd.read_csv("results/live/2026-04-07_093000/live_trades.csv")
# → Columns: entry_time, exit_time, entry_price, exit_price, pnl, pnl_pct, reason, ...
```

## Features

- **Intrabar polling:** Daemon thread checks option mid-price every 30s, exits immediately on stop/target breach
- **Crash recovery:** On startup, `reconcile_positions()` queries broker to resume any orphaned positions
- **Market orders only** (appropriate for paper trading)
- **Live data logging:** All bars and trades saved to CSV automatically

## Files

### Databento + Alpaca
- `live_runner/run_live_db.py` — entry point
- `src/live/databento_streamer.py` — Databento XNAS.ITCH streamer
- `src/live/alpaca_trader.py` — Alpaca order placement

### IBKR
- `live_runner/run_live_ibkr.py` — entry point
- `src/live/ibkr_streamer.py` — IB Gateway bar streamer
- `src/live/ibkr_trader.py` — IBKR order placement

### Common
- `src/live/live_engine.py` — signal generation + position logic (broker-agnostic)
- `src/live/broker_protocol.py` — abstract broker interface
