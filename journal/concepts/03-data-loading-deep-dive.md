---
tags: [data-loading, databento, alpaca, tradingview, aggregator, timezone]
---
# Deep Dive: Data Loading Pipeline

How raw market data becomes a clean OHLCV DataFrame ready for indicators. Covers three loaders (Databento, Alpaca, TradingView), the 1m→5m aggregator, options data caching, and timezone handling.

> Part of the [02-code-walkthrough.md](02-code-walkthrough.md) — covers Phase 2e.

---

## The Common Output Contract

All three loaders return the same shape:

```python
pd.DataFrame
    Index: DatetimeIndex (tz-aware, "America/New_York")
           name = "timestamp"
    Columns: open, high, low, close, volume (float64)
    Sorted by index, no duplicates
```

The engine doesn't know or care which loader produced the data. This contract is what makes the Template Method pattern in `BaseBacktestRunner` work.

---

## Data Source Branching

```
                          ┌─────────────────┐
                          │ BaseBacktestRunner │
                          │    .load_data()    │
                          └────────┬──────────┘
               ┌─────────────────┬┴──────────────────┐
               ▼                 ▼                    ▼
   ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────┐
   │ DatabentoRunner   │ │ AlpacaRunner     │ │ TradingViewRunner    │
   │ load_databento_   │ │ load_cached_csvs │ │ load_tradingview_csv │
   │ equities()        │ │ ()               │ │ ()                   │
   └──────┬───────────┘ └──────┬───────────┘ └──────┬───────────────┘
          │                    │                     │
   5min CSVs from disk   5min CSVs from disk    Raw CSV(s) from disk
   (organized by YYYY/)  (organized by YYYY/)   (PST timestamps)
          │                    │                     │
          └────────────────────┴─────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  pd.DataFrame       │
                    │  DatetimeIndex(EST)  │
                    │  OHLCV columns      │
                    └─────────────────────┘
```

---

## Databento Loader

**File:** `src/data/databento_loader.py`

### `ensure_equity_data()` — Auto-download (line 35)

Called from `DatabentoRunner.pre_load_check()` before `load_data()`.

```python
def ensure_equity_data(output_dir, start, end, raw_cache_dir, warmup_months=3, symbol="SYMBOL"):
```

1. Computes the full date range: `[start - warmup_months, end]`
2. Iterates month by month
3. For each month, checks if `{output_dir}/{YYYY}/SYMBOL_5min_YYYYMM.csv` exists and is fresh
4. "Stale" = last bar more than 3 days before expected month-end
5. If missing or stale: downloads 1-min bars → aggregates to 5-min → saves

The download uses `download_databento_equities()` (line 197):
```python
def download_databento_equities(symbol="SYMBOL", start, end, cache_dir):
    client = db.Historical(api_key)  # DATA_BENTO_PW env var
    data = client.timeseries.get_range(
        dataset="XNAS.ITCH",    # Direct exchange feed
        symbols=symbol,
        schema="ohlcv-1m",      # 1-min (only available schema for equities)
        start=start, end=end,
    )
```

Post-processing:
- Converts `ts_event` to UTC → EST
- Filters to weekdays and regular hours (09:30–16:00)
- Keeps only OHLCV columns
- Saves raw 1-min CSV to `data/DataBento/equities/SYMBOL/1min/`

The aggregation uses `_aggregate_and_save_monthly()` → `aggregate_1m_to_5m()`.

### `load_databento_equities()` — Load from disk (line 266)

```python
def load_databento_equities(cache_dir, start=None, end=None):
```

1. Tries organized structure first: `cache_dir/YYYY/*.csv`
2. Falls back to flat: `cache_dir/*.csv`
3. For each CSV:
   - Parses index as datetime, converts to EST
   - Keeps only OHLCV columns
4. Concatenates all frames, sorts, deduplicates
5. Filters to `[start, end]` if provided

### Why 1-min → 5-min?

Databento's `XNAS.ITCH` dataset only offers `ohlcv-1m` for equities (no native 5-min). The aggregator bridges this gap.

---

## Aggregator

**File:** `src/data/aggregator.py`

```python
def aggregate_1m_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    agg_map = {
        "open": "first",    # First open in the 5-min window
        "high": "max",      # Highest high
        "low": "min",       # Lowest low
        "close": "last",    # Last close
        "volume": "sum",    # Total volume
    }
    df_5m = df_work.resample("5min", label="left", closed="left").agg(agg_map)
    df_5m = df_5m.dropna(subset=["open", "high", "low", "close"])
    df_5m = df_5m.between_time("09:30", "15:55")
    df_5m = df_5m[df_5m.index.dayofweek < 5]
    return df_5m
```

Key decisions:
- **`label="left", closed="left"`** — the 09:30 bar contains 09:30–09:34 (5 bars)
- **`between_time("09:30", "15:55")`** — drops bars outside market hours (resample can create edge bars at 16:00)
- **Weekdays only** — `dayofweek < 5`
- **Drop incomplete bars** — NaN from partial windows at session boundaries
- Validated to produce 100% match against native Alpaca 5-min data (see `journal/log/003`)

Optional columns `symbol` and `trade_count` are carried forward; `vwap` is dropped (can't be naively aggregated).

---

## Alpaca Loader

**File:** `src/data/alpaca_loader.py`

### `load_cached_csvs()` (line 144)

```python
def load_cached_csvs(base_dir, start=None, end=None):
```

1. Globs `base_dir/**/*.csv` recursively
2. Pre-filters files by YYYYMM from filename (avoids loading irrelevant months)
3. For each CSV:
   - Parses `timestamp` column → UTC → EST
   - Skips files with "tradingview" in the name
4. Concatenates, sorts, deduplicates

### `download_bars()` — Alpaca API download (line 24)

```python
def download_bars(symbol, start_dt, end_dt, tf_value=5, tf_unit=Minute):
    client = StockHistoricalDataClient(API_KEY, SECRET_KEY)  # ALPACA_UN, ALPACA_PW
    bars = client.get_stock_bars(request_params)
```

- Uses `adjustment="split"` for split-adjusted prices
- Converts to EST, filters to weekdays + regular hours

### `update_to_present()` — Incremental download

Scans existing CSVs, finds the latest month, downloads only missing/incomplete months. Saves both 1-min and 5-min natively (Alpaca supports both schemas).

---

## TradingView Loader

**File:** `src/data/tradingview_loader.py`

### `load_tradingview_csv()` (line 22)

```python
def load_tradingview_csv(path, start=None, end=None):
```

1. Accepts directory (globs `*.csv`) or single file path
2. For each CSV, calls `_parse_tv_csv()`:
   ```python
   df["datetime"] = df["datetime"].dt.tz_localize("America/Los_Angeles").dt.tz_convert("America/New_York")
   ```
3. Concatenates, deduplicates (keeps last), filters by date

**Key difference from other loaders:** TradingView exports timestamps in PST/PDT. The loader localizes to `America/Los_Angeles` then converts to `America/New_York`. This handles DST transitions correctly — both coasts change clocks on the same dates.

**No warm-up needed:** TradingView exports already contain enough history (they're typically broad date-range exports), so `TradingViewRunner.warmup_months = 0`.

---

## Options Data (Databento)

**File:** `src/data/databento_loader.py:97`

```python
class DatabentoOptionsLoader:
    def __init__(self, api_key, cache_dir="data/options/SYMBOL/1min/"):
```

Used during the engine hot loop (Phase 4) when pricing options.

### `load_option_bars(symbol, start, end)` (line 138)

1. Checks local CSV cache: `{cache_dir}/{symbol}.csv` (spaces replaced with `_`)
2. If cached and covers the requested date range → return immediately
3. Otherwise downloads 1-min OHLCV from Databento's `OPRA.PILLAR` dataset
4. Retries up to 3 times with exponential backoff on failure
5. Saves to cache for future use

### Pre-downloading options

To avoid API calls during a backtest, run:
```bash
python scripts_py/download_options_databento.py <start> <end>
```

This computes signals, identifies which contracts would be traded, and pre-downloads their full-day 1-min bars.

---

## Loader Comparison Summary

| Aspect | Databento | Alpaca | TradingView |
|--------|-----------|--------|-------------|
| Source | XNAS.ITCH (exchange feed) | Alpaca broker feed | Manual CSV export |
| Native timeframe | 1-min only | 1-min + 5-min | 5-min |
| Aggregation needed | Yes (1m→5m) | No | No |
| Timezone in files | UTC | UTC | PST/PDT |
| Auto-download | `ensure_equity_data()` | `update_to_present()` | Manual |
| Warmup months | 3 | 3 | 0 |
| Options data | Yes (via `DatabentoOptionsLoader`) | No | No |
| Data quality | Highest (direct exchange) | Lower (bar aggregation diffs) | High (chart-validated) |
