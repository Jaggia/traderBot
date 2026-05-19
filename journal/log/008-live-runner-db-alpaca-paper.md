---
tags: [live-trading, databento, alpaca, occ, paper-trading]
---
# 008 — Live Runner: Databento Streaming + Alpaca Paper Options Trading

**Date:** 2026-02-28

---

## Why This Exists

The backtester runs on historical data. This live runner bridges the gap to real-world execution:

- **Databento** streams real-time 1-min equity bars (higher data quality than Alpaca's feed)
- **Alpaca paper trading** handles order execution (Databento has no paper trading)

The signal logic is identical to the backtest — same indicators, same config, same strike selection. The only difference is that instead of simulating fills, it submits real orders to Alpaca.

---

## How to Run on Monday (Market Hours)

### Prerequisites

Make sure these env vars are set in `~/.zshrc` (they already are):

```
DATA_BENTO_PW   — Databento Live API key
ALPACA_UN            — Alpaca paper account API key
ALPACA_PW            — Alpaca paper account secret key
```

Confirm options trading is enabled on your Alpaca paper account:
Alpaca Dashboard → Paper Account → Settings → Options (Level 3 ✓)

### Run it

```bash
cd /path/to/project
python live_runner/run_live_db.py
```

That's it. Run this **after 09:30 EST** on a market day.

### What happens at startup

1. Loads the last 200 5-min bars from the local Databento CSV cache (free — no credits spent)
2. Connects to Databento Live and starts streaming 1-min SYMBOL bars
3. Aggregates every 5 bars into one 5-min OHLCV bar (windows: :30–:34, :35–:39, ..., :55–:59)
4. On each 5-min bar close: recomputes SMI + Williams %R + VWAP, checks for signals
5. Signals trigger market orders on Alpaca paper

### Stopping

Press **Ctrl+C** — the runner catches it, closes any open position, cancels pending orders, and exits cleanly.

### EOD behaviour

Any open position is automatically closed at **15:50** (before the 16:00 close). You don't need to babysit it.

---

## Architecture

```
Databento Live WebSocket (1-min bars, XNAS.ITCH)
        │
        ▼
DatabentoStreamer  — assembles 5-min bars from 5× 1-min bars
        │  callback on each bar close
        ▼
LiveEngine  — rolling 300-bar buffer, recomputes indicators + signals
        │  signal detected / exit triggered
        ▼
AlpacaTrader  — submits market orders to paper-api.alpaca.markets
```

### New files

| File | Role |
|---|---|
| `src/live/databento_streamer.py` | WebSocket connection, 1m→5m aggregation, market hours filter |
| `src/live/alpaca_trader.py` | Alpaca paper TradingClient wrapper (buy, sell, cancel) |
| `src/live/live_engine.py` | Rolling buffer, indicator/signal recompute, position state machine |
| `live_runner/run_live_db.py` | Entry point — wires everything together, handles Ctrl+C |

---

## Signal Logic (identical to backtest)

Config values from `config/strategy_params.yaml` are used unchanged:

| Setting | Effect |
|---|---|
| `lookforward_mode` | Controls which indicator fires first vs confirms — `smi_then_wr`, `wr_then_smi`, or `either` |
| `armed_mode` | When true, only one signal per arm event |
| `sync_window` | Max bars between arm and fire |
| `vwap_filter` | When true, long only if close > VWAP; short only if close < VWAP |
| `strike_selection` | Configurable: ATM, ITM, OTM, or target-delta |
| `target_dte` | Days to expiration for options selection |

### Exit rules (V1)

- **Opposite signal:** if a new signal fires in the other direction, close and re-evaluate
- **EOD:** forced close at 15:50 regardless of P&L
- No intrabar stop/target yet — options don't have bracket orders on Alpaca (V2 enhancement)

---

## OCC Symbol Format

Every US equity option is identified by an **OCC symbol** (also called an OSI symbol). It's a standardised 21-character string:

```
SYMBOL   260228C00450000
│     │     │ │
│     │     │ └── Strike × 1000, zero-padded to 8 digits
│     │     │        $450.00 → 00450000
│     │     │        $452.50 → 00452500
│     │     └── Option type: C = Call, P = Put
│     └── Expiry: YYMMDD
│            Feb 28 2026 → 260228
└── Underlying root, left-padded to 6 characters with spaces
       SYMBOL → "SYMBOL   "
       AAPL → "AAPL  "
```

### Full example

`SYMBOL   260228C00450000` decodes as:
- **Underlying:** SYMBOL
- **Expiry:** Feb 28, 2026
- **Type:** Call
- **Strike:** $450.00

### Alpaca's format

Alpaca uses the same OCC standard but **without the space padding**:

```
SYMBOL260228C00450000   ← Alpaca API symbol (no spaces)
SYMBOL   260228C00450000  ← OCC canonical format (6-char root)
```

The codebase generates the padded version via `build_occ_symbol()` in `src/options/strike_selector.py`, then strips spaces before sending to Alpaca:

```python
def _strip_occ(occ_symbol: str) -> str:
    return occ_symbol.replace(" ", "")
# "SYMBOL   260228C00450000" → "SYMBOL260228C00450000"
```

### Strike encoding

Strike is encoded as `int(strike * 1000)`, zero-padded to 8 digits:

| Strike | Encoded | In symbol |
|---|---|---|
| $450.00 | 450000 | `00450000` |
| $452.50 | 452500 | `00452500` |
| $100.00 | 100000 | `00100000` |
| $1500.00 | 1500000 | `01500000` |

---

## Databento Credit Usage

The live runner only uses credits while streaming. XNAS.ITCH `ohlcv-1m` for a single symbol (SYMBOL) is one of the cheaper schemas. The historical warmup bars are loaded from the local CSV cache — zero credit cost.

Check your remaining balance at: [databento.com/portal](https://databento.com/portal)

---

## Known Limitations (V1)

1. **No intrabar stop/target** — exits only on opposite signal or EOD. A 0-DTE option can decay to near-zero with no protection. V2 would poll Alpaca's position P&L every 30s.
2. **Single symbol per run** — configured via the `symbol` parameter. Multi-symbol support would require parallelising the stream subscription.
3. **State is in-memory** — if the process crashes mid-position, it won't know on restart. V2 would reconcile with Alpaca's open positions on startup.
4. **Market orders** — used for simplicity. Limit orders at mid-price would reduce slippage in production.
