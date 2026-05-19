---
tags: [runbook, databento, download, aggregate, options-cache]
---
# Runbook: Download & Prepare Databento Data

## Download Equity Bars (1-min → 5-min)

```bash
python scripts_py/download_and_aggregate_databento.py 2025-08-01 2026-02-28
# Uses defaults if no args (2025-08-01 to 2026-02-14)
python scripts_py/download_and_aggregate_databento.py
```

What it does: downloads 1-min OHLCV bars from XNAS.ITCH, aggregates to 5-min, saves as monthly CSVs.

**Output:**
- Raw 1-min: `data/DataBento/equities/SYMBOL/1min/SYMBOL_1min_{start}_to_{end}.csv`
- Aggregated 5-min: `data/DataBento/equities/SYMBOL/5min/YYYY/SYMBOL_5min_YYYYMM.csv`

**Requires:** `DATA_BENTO_PW` env var (already in `~/.zshrc`)

## Validate Aggregator (Before Spending Credits)

```bash
python scripts_py/validate_aggregator.py
```

Compares aggregated Alpaca 1-min vs native Alpaca 5-min. Should report 100% match across all OHLCV columns. Run this once to confirm the aggregator is correct before downloading Databento data.

## Pre-Download Options Contracts

Run before a backtest to pre-warm the options cache (avoids slow API calls during the backtest loop):

```bash
python scripts_py/download_options_databento.py 2025-11-10 2026-02-13
```

What it does: computes signals for the date range, identifies which contracts would be traded, downloads their full-day 1-min bars into the cache.

**Output:**
```
Options Pre-Download Summary
  Signal bars:       47
  Unique contracts:  31
  Downloaded (new):  28
  Cache hits:         3
```

Options cache: `data/DataBento/options/SYMBOL/1min/`

## Data Directory Layout

```
data/DataBento/
  equities/SYMBOL/
    1min/   SYMBOL_1min_{start}_to_{end}.csv   (raw download)
    5min/   YYYY/SYMBOL_5min_YYYYMM.csv         (aggregated, used by backtests)
  options/SYMBOL/
    1min/   {OCC_SYMBOL}.csv                  (per-contract cache)
```
