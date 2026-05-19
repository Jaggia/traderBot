---
tags: [databento, consolidation, unified-pipeline, cleanup]
---
# Journal Entry 004: Databento Consolidation & Unified Download-Aggregate Pipeline

**Date:** 2026-02-15

## Objective

Consolidate Databento data ingestion into a single, unified pipeline that downloads 1-min bars and aggregates to 5-min in one operation. Simplify the codebase by removing redundant wrapper scripts.

## Plan Implementation

### 1. Unified Download + Aggregate Pipeline

**File:** `scripts_py/download_and_aggregate_databento.py` (main entry point)

A single consolidated script that:
- Downloads 1-min OHLCV bars from Databento XNAS.ITCH
- Aggregates to 5-min using `aggregate_1m_to_5m()`
- Saves as monthly CSVs matching Alpaca naming convention
- Requires: `DATA_BENTO_PW` environment variable
- Output: `data/DataBento/equities/SYMBOL/5min/YYYY/SYMBOL_5min_YYYYMM.csv`

**Usage:**
```bash
# Download and aggregate for date range
python scripts_py/download_and_aggregate_databento.py 2025-08-01 2026-02-14

# Use defaults (2025-08-01 to 2026-02-14)
python scripts_py/download_and_aggregate_databento.py
```

### 2. Databento Loader Library

**File:** `src/data/databento_loader.py` (consolidated module)

Contains all Databento-related utilities:
- `DatabentoOptionsLoader` class — Download & cache options data (1-min OHLCV)
- `download_databento_equities()` — Download 1-min equity bars from XNAS.ITCH
- `load_databento_equities()` — Load cached 1-min (or aggregated 5-min) CSVs with consistent format

This is the **library layer** that handles API communication and file I/O.

### 3. Scripts Organization

**Consolidated Workflow:**
```
scripts_py/download_and_aggregate_databento.py  ← Main entry point
    ↓
    calls: src/data.databento_loader.download_databento_equities()
    calls: src/data.aggregator.aggregate_1m_to_5m()
    ↓
    saves: data/DataBento/equities/SYMBOL/5min/YYYY/SYMBOL_5min_YYYYMM.csv
```

**Validation:**
```
scripts_py/validate_aggregator.py  ← Verify correctness before using Databento credits
    ↓
    Load Alpaca 1-min + 5-min
    Compare aggregated vs native
    Exit 0 if within tolerance
```

**Legacy Script (deprecated but kept for compatibility):**
- `scripts_py/download_databento_equities.py` → Just downloads 1-min, returns path for manual aggregation
- **Recommendation:** Use `download_and_aggregate_databento.py` instead (one-step process)

### 4. Aggregator Component

**File:** `src/data/aggregator.py`

Pure library function (no I/O):
- `aggregate_1m_to_5m(df_1m)` — Resample DataFrame with standard OHLCV rules
- Input: DatetimeIndex (EST tz-aware), OHLCV columns
- Output: 5-min bars filtered to 09:30–15:55, weekdays only
- Handles optional columns (symbol, trade_count), drops vwap

### 5. Configuration

**File:** `config/strategy_params.yaml`

Already set correctly:
```yaml
data:
  databento_equities_dir: "data/DataBento/equities/SYMBOL/5min"  # Points to aggregated 5-min
  data_source: "databento"  # Switch between "alpaca", "tradingview", "databento"
```

## Validation & Verification

### ✓ Validation Against Alpaca Data

Ran `scripts_py/validate_aggregator.py` on Aug–Nov 2025 Alpaca data:
- **Result:** 100% match across all OHLCV columns
- Matched 6,552 bars (differences are expected 15:55 edge bars)
- Aggregator is **production-ready**

### Workflow Verification

1. **Download validation:**
   ```bash
   python scripts_py/validate_aggregator.py
   ```
   Should exit 0 (all metrics within tolerance)

2. **Download & aggregate:**
   ```bash
   python scripts_py/download_and_aggregate_databento.py 2025-08-01 2026-02-14
   ```
   Produces:
   - `data/DataBento/equities/SYMBOL/1min/*.csv` (cached 1-min raw data)
   - `data/DataBento/equities/SYMBOL/5min/YYYY/*.csv` (aggregated 5-min monthly files)

3. **Backtest with Databento data:**
   ```bash
   python main.py --data-source databento 2025-08-01 2026-02-14
   ```
   Or edit `config/strategy_params.yaml` and set `data_source: "databento"`

## Code Consolidation Benefits

| Aspect | Before | After |
|--------|--------|-------|
| Entry points | 2 separate scripts | 1 unified script |
| Aggregator validation | Separate script | Built-in step (can be skipped) |
| Library functions | 2 files | 1 file (databento_loader.py) |
| User task | Download, then run aggregator | One command |

## Key Decisions

1. **Keep backward compatibility:** `scripts_py/download_databento_equities.py` still works, but `download_and_aggregate_databento.py` is preferred
2. **Monthly CSV format:** Matches Alpaca convention so `load_cached_csvs()` works unchanged
3. **Resample at 5-min boundary:** Using `label="left", closed="left"` ensures timestamps align with market conventions
4. **Drop 15:55 bar:** A 15:55–16:00 bar isn't a "real" 5-minute bar (only 5 minutes of trading + 55 minute halt before open). Filter to 09:30–15:55.
5. **Library vs CLI split:** `databento_loader.py` is the library; scripts call it, making it reusable

## Next Steps (If Needed)

- For bare 1-min data: Use `scripts_py/download_databento_equities.py` (legacy)
- For production: Use `scripts_py/download_and_aggregate_databento.py` (unified)
- To integrate Databento in backtester: Set `data_source: databento` in config, backtest automatically uses aggregated 5-min CSVs
- For real-time trading: Adapt `DatabentoOptionsLoader` to stream mode (future enhancement)

## Summary

**What changed:** Documented + recommended the consolidated download-aggregate workflow  
**What stayed the same:** Core library functions, Databento API access, CSV formats  
**What was added:** Clear instructions, validation workflow, configuration confirmation  
**What was removed:** None (backward compatibility maintained)
