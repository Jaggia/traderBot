---
tags: [runbook, backtest, data-sources, config]
---
# Runbook: Run a Backtest

## Quick Start

```bash
# Databento data (default, highest quality)
./scripts_bash/run_backtest_db.sh

# Alpaca data
./scripts_bash/run_backtest_alpaca.sh

# TradingView data
./scripts_bash/run_backtest_tv.sh
```

Edit `START_DATE` and `END_DATE` inside the `.sh` file before running, or pass as args:

```bash
/path/to/venv/python main_runner/run_backtest_db.py 2025-11-10 2026-02-13
```

Set `VENV_PYTHON` env var, or use `./venv/bin/python` by convention.

## With Monte Carlo

```bash
# Inline (runs MC automatically after backtest)
./scripts_bash/run_backtest_db.sh   # set RUN_MC=true inside the .sh first
# Or directly:
python main_runner/run_backtest_db.py 2025-11-10 2026-02-13 --mc
```

## Output Location

```
results/db/{Month-DD-YYYY}/{mode}/{timeframe}/
  backtest.csv       — trade log
  report.md          — performance summary
  equity_curve.png   — portfolio value over time
  drawdown.png       — underwater chart
  signals.png        — price chart with entry/exit markers
  config.yaml        — config snapshot at run time
  equity_data.csv    — equity curve data (for dashboard)
  price_data.csv     — close prices (for dashboard)
```

## Pre-Downloading Options Data (Optional but Recommended)

The pre-download script is **completely separate** from the backtest — you never have to run it. If you skip it, the backtest still works: on the first `_get_option_price()` call for each new contract, the engine downloads the full trading day from Databento on the fly and caches it. Subsequent bars (and future backtests) hit the cache.

The only reasons to pre-download are:
- **Speed**: avoids mid-loop network calls slowing the backtest
- **Visibility**: you see all download errors upfront, not buried in a long backtest run
- **Cost is identical**: Databento charges the same either way — same data downloaded

```bash
python scripts_py/download_options_databento.py 2025-11-10 2026-02-13
```

Run once per date range. Once cached, all backtests over that range cost nothing.

## Adjusting Parameters

All in `config/strategy_params.yaml` — no code changes needed. Key settings:

```yaml
strategy:
  trade_mode: "options"      # "equities", "options", "both"

options:
  target_dte: 0              # days to expiration
  strike_selection: "target_delta"
  target_delta: 0.50

exits:
  profit_target_pct: 20.0
  stop_loss_pct: 20.0
  opposite_signal: true
```
