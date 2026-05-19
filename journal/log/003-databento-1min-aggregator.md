---
tags: [databento, aggregator, 1m-to-5m, validation]
---
# Journal Entry 003: Databento 1-Min → 5-Min Aggregator

**Date:** 2026-02-15

## The Problem

Databento only offers `ohlcv-1m` for equities on XNAS.ITCH — no native 5-min bars. We need 5-min bars for our backtesting framework.

## Solution

Built a 1-min → 5-min aggregator pipeline:

1. **`src/data/aggregator.py`** — `aggregate_1m_to_5m()` resamples with standard OHLCV rules (open: first, high: max, low: min, close: last, volume: sum), filters to 09:30–15:55, drops incomplete bars
2. **`scripts_py/download_and_aggregate_databento.py`** — Single script that downloads 1-min from Databento, aggregates to 5-min, saves as monthly CSVs matching Alpaca naming convention

## Validation

Before spending Databento credits, validated the aggregator against Alpaca data (we have both 1-min and native 5-min). Results: **100% match** across all OHLCV columns — zero diffs.

```
Column       Match%    MaxDiff   MeanDiff
open        100.00%     0.0000   0.000000
high        100.00%     0.0000   0.000000
low         100.00%     0.0000   0.000000
close       100.00%     0.0000   0.000000
volume      100.00%     0.0000   0.000000
```

Validated on Aug–Nov 2025 (6,552 bars matched out of 6,636 native — the 84 missing are the 15:55 bars that get filtered by `between_time("09:30", "15:55")` since 15:55+5min = 16:00 which is market close).

## Data Flow

```
Databento XNAS.ITCH (ohlcv-1m)
  → data/DataBento/equities/SYMBOL/1min/SYMBOL_1min_{start}_to_{end}.csv
  → aggregate_1m_to_5m()
  → data/DataBento/equities/SYMBOL/5min/YYYY/SYMBOL_5min_YYYYMM.csv
  → loadable by load_cached_csvs() or armed_mode_comparison.py
```

## Key Decisions

- **Resample with `label="left", closed="left"`** — matches how Alpaca timestamps their bars (bar timestamp = start of window)
- **Filter to 09:30–15:55** instead of 09:30–16:00 — the 15:55 bar would aggregate 15:55–16:00, but that's only 5 minutes of real trading; keeping it clean
- **Drop vwap column** during aggregation — VWAP can't be naively summed/averaged across bars
- **Monthly CSV output** matches Alpaca convention so `load_cached_csvs()` works unchanged
