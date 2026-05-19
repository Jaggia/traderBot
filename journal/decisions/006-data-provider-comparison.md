---
tags: [data-providers, databento, polygon, ibkr, comparison]
---
# Decision: Data Provider Comparison — Databento vs Polygon.io vs IBKR

**Date:** 2026-02-28

## The Question

When moving toward live/paper trading, which data + execution stack makes the most sense? Three realistic options were evaluated.

## Option 1: Current Stack (Databento + Alpaca) ✓ Already built

| | |
|---|---|
| Data | Databento XNAS.ITCH — direct exchange feed, highest quality |
| Execution | Alpaca paper trading — free, Level 3 options approved |
| Monthly cost | Databento credits (usage-based) + $0 Alpaca |
| Code complexity | Done |

**Verdict:** Best choice for paper trading. Already implemented.

## Option 2: Polygon.io + Alpaca

| | |
|---|---|
| Data | Polygon.io (now rebranded to Massive.com) — WebSocket streaming |
| Execution | Still Alpaca — no change |
| Monthly cost | ~$29–79/month for real-time stocks + options |
| Code complexity | Low — swap `DatabentoStreamer` for a `PolygonStreamer` (~50 lines) |

**Verdict:** Cleaner API than Databento, but adds a third monthly cost without eliminating any complexity (still need Alpaca). Not worth switching from the current working setup.

## Option 3: Interactive Brokers (IBKR)

| | |
|---|---|
| Data | TWS/IB Gateway — real-time equity + options streaming |
| Execution | IBKR paper account — built-in, no separate broker needed |
| Monthly cost | **$4.50/month** non-professional (waived if commissions > $5/month) |
| Code complexity | High — TWS must run locally as middleware; event-driven callback API; `ib_insync` helps but still significant lift |

**Verdict:** Dramatically cheaper and consolidates everything into one account. The right long-term destination when moving to live trading. The TWS API complexity is a one-time investment.

## Side-by-Side

| | Databento + Alpaca | Polygon + Alpaca | IBKR |
|---|---|---|---|
| Monthly cost | Credits + $0 | ~$29–79 + $0 | ~$4.50 |
| Code to build | Done | ~50 lines swap | Full new live engine |
| Paper trading | Alpaca (separate) | Alpaca (separate) | Built-in |
| TWS/Gateway needed | No | No | Yes (always running) |
| Options data quality | OPRA via DB | OPRA via Polygon | OPRA via IBKR |

## Recommendation (Updated 2026-04-05)

### Backtesting Strategy Development
**Use Databento for all historical backtests.** 

Rationale:
- XNAS.ITCH direct exchange feed (highest quality)
- Full options market data (IBKR cannot provide this)
- Bulk download efficient (~$0.50/month for streaming 1-min)
- No rate limits on historical queries

### Live Trading (Paper & Forward-Testing)
**Use IBKR for all live execution.**

Rationale:
- Zero API cost for paper trading (IB Gateway socket, no rate limits)
- Same data feed you'll see on live (reduces surprise mismatch)
- IB Gateway is stable middleware (ib_insync is proven)
- Tested working 2026-04-05 (socket connection, warmup aggregation, streaming confirmed)

### Data Source Consolidation: NOT FEASIBLE
IBKR cannot fully replace Databento for backtesting due to:
- **API rate limits:** Historical 1-min requests throttled to ~1/min; downloading years of data takes weeks
- **Missing options data:** IBKR doesn't provide historical option OHLCV or Greeks
- **Bulk export:** No option to download all data at once (must request bar-by-bar)

### Final Architecture (Locked In)
```
Backtest (strategy development)    → Databento XNAS.ITCH
Live paper trading (forward-test)  → IBKR (IB Gateway)
Warmup bars (live startup)         → Databento cache (free)
```

**Trade-off:** Backtest uses XNAS.ITCH (best quality) but live trades on IBKR's routed feed (what you actually see). This is expected and acceptable — validates whether edge is robust across data sources. Any professional strategy accepts backtest ≠ live (that's what forward-testing proves).

**Next phase:** Once IBKR live trading is proven (after Monday), build IBKR-only historical loader to backtest on same data you'll trade with (enables true apples-to-apples validation).
