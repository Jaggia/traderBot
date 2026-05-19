# IBKR Live Runner — Operational Guide

**Last updated:** 2026-05-12 by Hermes Agent

## Current Status (May 12, 2026)

### What's Working
- IBKR Gateway connection (port 4002, paper account)
- Equity tick streaming via `reqMktData`
- 5-min bar aggregation + signal pipeline (trigger_chain + ema_233)
- Signals fire correctly
- Log/tick/bar CSV output

### What's Blocked
- **Real-time equity data** — paid subscription is TWS-only, not API-enabled
- **Option quotes** — OPRA subscription is TWS-only, not API-enabled
- **After-hours paper trading** — not supported by IBKR

### Current Data Mode
- `reqMarketDataType(1)` (LIVE) — set in both `ibkr_streamer.py` and `ibkr_trader.py`
- This MAY work if IBKR support enables API access on subscriptions
- If it fails at market open, revert to type 3 (63-69s equity lag, no options)

## Quick Reference

### Start the Runner
```bash
cd ~/Desktop/projects/backTestingTraderBot
./venv_stonks/bin/python -u live_runner/run_live_ibkr.py
```

### Check Status
```bash
# Is it running?
ps aux | grep run_live_ibkr | grep -v grep

# Tail today's log
tail -f results/live/$(date +%Y-%m-%d)/$(date +%Y-%m-%d).log

# Check for errors
grep "ERROR\|Error 10089\|Error 10091\|Entry skipped" results/live/$(date +%Y-%m-%d)/$(date +%Y-%m-%d).log | tail -20

# Check for successful entries
grep "BUY\|SELL" results/live/$(date +%Y-%m-%d)/$(date +%Y-%m-%d).log
```

### Revert to Delayed Data (if type 1 fails)
```bash
# In src/live/ibkr_trader.py, line ~107:
#   Change: self._ib.reqMarketDataType(1)
#   To:     self._ib.reqMarketDataType(3)

# In src/live/ibkr_streamer.py, line ~152:
#   Change: ib.reqMarketDataType(1)
#   To:     ib.reqMarketDataType(3)
```

### Kill the Runner
```bash
pkill -f run_live_ibkr
```

## Prerequisites
1. **IB Gateway must be running** — launch manually: `"/home/ajaggia/Jts/ibgateway/1046/ibgateway"`
2. Login with paper account, confirm port 4002
3. **Read-Only API must be UNCHECKED** in Gateway settings

## Cron Jobs
| Job | Schedule | Purpose |
|---|---|---|
| Daily session launcher | 6:25 AM PT (May 13 one-shot) | Starts runner with type 1 |
| `81e518270714` | 9:00 AM PT, Mon-Fri | Regular daily launcher |
| `d659205bca4e` | 4:15 PM PT, Mon-Fri | End-of-day summary |

## Error Codes
| Code | Meaning | Fix |
|---|---|---|
| 10089 | Equity subscription not API-enabled | Support ticket |
| 10091 | Options subscription not API-enabled | Support ticket |
| 10168 | Delayed data not enabled | TWS setting or support ticket |
| 354 | Not subscribed | Need OPRA subscription |
| 162 | HMDS no data (paper quirk) | Non-fatal, uses seed data |
| 200 | Contract not found | Add `tradingClass` + `multiplier` |

## Key Files
- `live_runner/run_live_ibkr.py` — entry point
- `src/live/ibkr_streamer.py` — tick streaming + bar aggregation
- `src/live/ibkr_trader.py` — order routing + option quotes
- `src/live/live_engine.py` — signal processing + position management
- `config/strategy_params.yaml` — all strategy parameters
- `results/live/YYYY-MM-DD/YYYY-MM-DD.log` — daily session log
- `results/live/YYYY-MM-DD/live_ticks.csv` — tick-by-tick data
- `results/live/YYYY-MM-DD/live_bars_5m.csv` — 5-min OHLCV bars
- `results/live/YYYY-MM-DD/live_bars_1m.csv` — 1-min OHLCV bars
- `results/live/YYYY-MM-DD/HHMMSS/` — session-specific trade logs

## Support Ticket Status
- **Ticket 1** (May 6): Resolved equity streaming → Steve D. enabled API access
- **Ticket 2** (May 12): Sent — requesting API-enabled subscriptions for equity + OPRA options
  - Claims "no separate API subs" but after-hours test proved this wrong
  - Also requested after-hours paper trading capability
  - Awaiting response

## Hermes Skill
Full operational details, debugging history, and reference docs are in the Hermes skill:
- Skill: `ibkr-live-trading` (auto-loaded for IBKR-related tasks)
- Location: `~/.hermes/skills/trading/ibkr-live-trading/`
- 14 reference documents covering every issue encountered and resolved

## Architecture
```
IB Gateway (port 4002, paper)
    │
    ├── Streamer (clientId=1) ── reqMktData(SYMBOL) ──→ ticks ──→ 1min bars ──→ 5min bars
    │                                                                         │
    └── Trader (clientId=2) ── option quotes + order routing  ←── signals ──┘
         │
         ├── get_option_mid_price() ── reqMktData(snapshot=True) ──→ bid/ask
         ├── buy_option() ── MarketOrder("BUY")
         └── sell_option() ── MarketOrder("SELL")
```

## Ports
| Account | IB Gateway |
|---|---|
| Paper | 4002 |
| Live | 4001 |
