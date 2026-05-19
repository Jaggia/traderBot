---
tags: [architecture, system-overview, data-flow, modules]
---
# Concept: System Overview

Options & equities backtesting framework. Generates trading signals using pluggable signal systems (SMI + Williams %R, EMA 233 intrabar cross, MACD, ADX/DMI, or any custom two-indicator armed-mode config), manages positions with options Greeks tracking, and produces performance analytics.

> For a step-by-step code trace through every function and file, see [02-code-walkthrough.md](02-code-walkthrough.md).

## Data Flow

```
Data Loading → Indicator Calculation → Signal Generation → Backtest Engine → Analytics
```

## The Two Modes

**Backtesting** (`main_runner/`) — runs over historical data, produces performance reports in `results/`.

**Live trading** (`live_runner/`) — runs the same signal logic on live 5-minute bars and places paper orders through a broker adapter. Two runners:
- `run_live_db.py` — Databento streamer + Alpaca paper trading
- `run_live_ibkr.py` — IBKR streamer + IBKR paper trading (IB Gateway/TWS socket)

## Key Modules

| Module | Role |
|---|---|
| `src/data/` | Loaders for Alpaca, TradingView, Databento (equities + options) |
| `src/indicators/` | SMI, Williams %R, VWAP, EMA, RSI, MACD — vectorized; `base.py` provides primitives |
| `src/signals/strategy.py` | `SignalStrategy` ABC + `create_strategy(config)` dispatcher |
| `src/signals/indicator_pair_pipeline.py` | Unified pipeline for System 1 (Pair), System 2 (EMA), and System 3 (Chain) |
| `src/backtest/engine.py` | Bar-by-bar loop over numpy arrays; entry/exit/P&L |
| `src/backtest/portfolio.py` | Cash, open positions, closed trade log, equity curve |
| `src/options/` | Strike selection, OCC symbol construction, IV backsolve, Greeks snapshot |
| `src/analysis/` | Metrics (Sharpe, PSR, drawdown), visualizations, Monte Carlo |
| `src/live/` | Databento + IBKR streamers, Alpaca + IBKR traders, broker-agnostic live engine |

## Backtest Engine Design

- Indicators pre-computed on the full dataset (vectorized, fast)
- Engine iterates bar-by-bar over pre-extracted **numpy arrays** (avoids pandas overhead in the hot loop)
- Signals fire on bar close; entries fill on the **next bar's open**
- Equity exits use fixed stop/limit levels checked against intrabar high/low
- Options backtests use observed option market data only; missing or stale option data is a hard failure
- Portfolio accounting includes commissions and slippage from `config/strategy_params.yaml`
- Options P&L uses the standard 100x contract multiplier
- All timestamps are EST/EDT

## Configuration

Everything in `config/strategy_params.yaml`. No code changes needed to adjust strategy parameters.

## Data Sources

| Source | Quality | Notes |
|---|---|---|
| Databento | Highest | XNAS.ITCH direct exchange feed; 1-min only, aggregated to 5-min |
| TradingView | High | Source of truth for visual chart alignment; PST→EST conversion |
| Alpaca | Lower | Produces different OHLC values due to bar aggregation differences |

## Output Structure

```
results/{db,alpaca,tv}/{Month-DD-YYYY}/{mode}/{timeframe}/
  backtest.csv, report.md, equity_curve.png, drawdown.png,
  signals.png, config.yaml, equity_data.csv, price_data.csv
  monte_carlo/  (if MC was run)
```
