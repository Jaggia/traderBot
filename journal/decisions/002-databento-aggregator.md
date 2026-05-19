---
tags: [databento, aggregator, 1m-to-5m, validation, resampling]
---
# Decision: Databento 1-Min → 5-Min Aggregation

**Date:** 2026-02-15

## Why We Had to Build This

Databento only offers `ohlcv-1m` for equities on XNAS.ITCH. No native 5-min bars. Our strategy runs on 5-min bars. We had to aggregate.

## Aggregation Rules

Standard OHLCV resample: open=first, high=max, low=min, close=last, volume=sum. Implemented in `src/data/aggregator.py` → `aggregate_1m_to_5m()`.

## Validation: 100% Match Against Alpaca

Before spending Databento credits on live data, validated the aggregator against Alpaca (which has both 1-min and native 5-min). Result:

```
Column    Match%     MaxDiff   MeanDiff
open     100.00%     0.0000   0.000000
high     100.00%     0.0000   0.000000
low      100.00%     0.0000   0.000000
close    100.00%     0.0000   0.000000
volume   100.00%     0.0000   0.000000
```

6,552 bars matched (84 missing = 15:55 bars, filtered by design).

## Key Design Choices

- **`label="left", closed="left"`** — bar timestamp = start of window, matches Alpaca convention so `load_cached_csvs()` works unchanged
- **Filter to 09:30–15:55** not 09:30–16:00 — the 15:55 bar only covers 5 min before a 55-min halt; cleaner to drop it
- **Drop vwap** during aggregation — VWAP can't be naively averaged across bars; it's recomputed fresh each backtest from OHLCV
- **Monthly CSV output** — matches Alpaca naming convention so the same loader works for both sources

## Files

- `src/data/aggregator.py` — `aggregate_1m_to_5m()`
- `scripts_py/download_and_aggregate_databento.py` — download + aggregate in one step
- `scripts_py/validate_aggregator.py` — validation script (run before spending credits)
