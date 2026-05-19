---
tags: [options, cache, results-dir, dashboard, plotly]
---
# Journal Entry 005: Options Pre-Download Pipeline & Results Directory Restructure

**Date:** 2026-02-23

## Two Changes, One Theme

Both changes this session are about **organization** — one for how backtest outputs are stored, one for how options data gets fetched. Neither changes the core strategy logic.

---

## Part 1: Results Directory Restructure

### Before

Results were saved flat in `results/{source}/` with date-suffixed filenames:
```
results/db/backtest_db_20260216.csv
results/db/report_db_20260216.md
results/db/equity_curve_db_20260216.png
```

### After

Results now live in a nested directory tree:
```
results/{source}/{Month-DD-YYYY}/{mode}/{timeframe}/
  backtest.csv
  report.md
  equity_curve.png
  drawdown.png
  signals.png
  config.yaml
  equity_data.csv
  price_data.csv
```

Example: `results/db/February-23-2026/options/5min/backtest.csv`

### Why

**Mode separation**: a single run date can produce results for equities, options, and both simultaneously. The flat naming with suffixes got unwieldy. The nested structure makes it trivial to compare modes from the same run.

**Human-readable dates**: `February-23-2026` instead of `20260223`. Easier to navigate in Finder or terminal.

**Two new CSVs per run**:
- `equity_data.csv` — full equity curve with cash column, enables interactive Plotly charts in dashboard instead of static PNGs
- `price_data.csv` — just `close` prices for the trading period, used by the dashboard signals overlay

### Dashboard Impact

`discover_runs()` was updated to walk the new `{source}/{date}/{mode}/{timeframe}/` tree. The dashboard now supports multiple modes per run via a radio selector in the Overview view. Interactive Plotly charts (equity curve, drawdown, signals with entry/exit markers) are shown when CSV data is available, with PNG fallback for older runs.

---

## Part 2: Options Pre-Download Pipeline

### The Problem

In options mode, each bar with an open position calls `_get_option_price()`, which hits the Databento API if the contract isn't cached. With 0-DTE options and many signal bars across months of backtesting, this meant:
1. First backtest run: slow (many API calls)
2. Subsequent runs: fast (cache hits)

But the cache was contract-level. If you ran a new date range, you'd hit the API again for those contracts. And there was no easy way to pre-warm the cache before a long backtest.

### The Solution: `scripts_py/download_options_databento.py`

A standalone pre-download script that runs *before* the backtest:

```
python scripts_py/download_options_databento.py 2025-11-10 2026-02-13
```

It:
1. Loads equity data with 3-month warm-up (same as the backtest runner)
2. Computes indicators + signals on the full dataset
3. For each signal bar in the trading period, constructs the OCC symbol that *would* be traded
4. Downloads the full trading day of 1-min bars for that contract
5. Deduplicates by (symbol, date) — one download per contract per trading day

After running this, the backtest hits cache only — zero API calls during the actual backtest loop.

### Key Design: Full-Day Downloads

The engine already downloaded full trading days at the time of the first `_get_option_price()` call (to make entry + exit both hit cache). The pre-download script replicates that same logic, so cache keys match perfectly.

### Deduplication

A single signal bar maps to one contract. But if two signal bars on the same day happen to map to the same contract (e.g. two bullish signals → same call strike), you only need to download once. The script tracks `seen_symbols = {symbol → set of dates}` and skips duplicates.

### Output

```
============================================================
Options Pre-Download Summary
============================================================
  Signal bars:       47
  Unique contracts:  31
  Downloaded (new):  28
  Cache hits:        3
  Cache dir:         data/DataBento/options/SYMBOL/1min
============================================================
```

---

## What Didn't Change

- Signal generation logic (armed mode, lookforward, VWAP filter) — unchanged
- Options strike selection (`1_OTM`, OCC symbol construction) — unchanged
- Black-Scholes fallback — still there for contracts with no market data
- All three data source runners (db, alpaca, tv) — same interface, updated output paths
