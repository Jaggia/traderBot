import logging
import os
import time
import glob as globmod
import databento as db
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def load_1m_csv(csv_path: str) -> pd.DataFrame:
    """Read a downloaded 1-min CSV, handling mixed DST offsets robustly."""
    df = pd.read_csv(csv_path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    return df


def aggregate_and_save_monthly(
    df_1m: pd.DataFrame, year: int, month: int, output_dir: str, symbol: str = "SYMBOL",
    eod_cutoff_time: str = "15:55"
) -> str:
    """Aggregate 1-min bars to 5-min and save as a monthly CSV."""
    from src.data.aggregator import aggregate_1m_to_5m

    df_5m = aggregate_1m_to_5m(df_1m, eod_cutoff_time=eod_cutoff_time)
    out_dir = os.path.join(output_dir, str(year))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{symbol}_5min_{year:04d}{month:02d}.csv")
    df_5m.to_csv(out_path)
    logger.info("%d 5-min bars → %s", len(df_5m), out_path)
    return out_path


def ensure_equity_data(
    output_dir: str,
    start: str,
    end: str,
    raw_cache_dir: str = "data/DataBento/equities/SYMBOL/1min",
    warmup_months: int = 3,
    symbol: str = "SYMBOL",
    eod_cutoff_time: str = "15:55"
) -> None:
    """Ensure all monthly 5-min CSVs exist and cover the full requested date range.

    For each month in [start - warmup, end] that is missing or whose last bar
    is more than 3 days before the expected end, downloads the 1-min bars from
    Databento and re-aggregates to 5-min. No-ops when data is already complete.
    """
    start_dt = (pd.Timestamp(start) - pd.DateOffset(months=warmup_months)).replace(day=1)
    end_dt = pd.Timestamp(end)

    current = start_dt
    while current <= end_dt:
        year, month = current.year, current.month
        path = os.path.join(output_dir, str(year), f"{symbol}_5min_{year:04d}{month:02d}.csv")

        # Determine the last day we expect this month's file to cover
        month_end = current + pd.DateOffset(months=1) - pd.Timedelta(days=1)
        expected_last = min(month_end, end_dt)

        needs_download = False
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            logger.info("Missing equity file: %s", path)
            needs_download = True
        else:
            try:
                df_check = pd.read_csv(path, index_col=0)
                df_check.index = pd.to_datetime(df_check.index, utc=True).tz_convert("America/New_York")
                last_bar = df_check.index[-1].date()
                # Stale if more than 3 days short of expected end (covers weekends + holidays)
                if last_bar < (expected_last - pd.Timedelta(days=3)).date():
                    logger.info(
                        "Stale equity file %s: last bar %s, expected up to %s — re-downloading",
                        os.path.basename(path), last_bar, expected_last.date(),
                    )
                    needs_download = True
            except Exception as exc:
                logger.warning("Could not read %s (%s) — re-downloading", path, exc)
                needs_download = True

        if needs_download:
            month_start_str = current.strftime("%Y-%m-%d")
            actual_end_str = expected_last.strftime("%Y-%m-%d")
            logger.info("Downloading equity data: %s to %s", month_start_str, actual_end_str)
            csv_path = download_databento_equities(
                symbol=symbol,
                start=month_start_str,
                end=actual_end_str,
                cache_dir=raw_cache_dir,
            )
            df_1m = load_1m_csv(csv_path)
            aggregate_and_save_monthly(df_1m, year, month, output_dir, symbol, eod_cutoff_time)

        current += pd.DateOffset(months=1)


class DatabentoOptionsLoader:
    def __init__(self, api_key: Optional[str], cache_dir: str = "data/options/SYMBOL/1min/",
                 underlying: str = "SYMBOL"):
        self.client = db.Historical(api_key) if api_key else None
        self.cache_dir = cache_dir
        self._underlying = underlying
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_cache_path(self, symbol: str) -> str:
        # Standardizing filename to avoid OS issues with OSI symbols
        safe_name = symbol.replace(" ", "_")
        return os.path.join(self.cache_dir, f"{safe_name}.csv")

    def get_contract_definition(self, ts: datetime, strike: float, expiry: datetime, option_type: str):
        """
        Uses the 'definition' schema to find the exact instrument_id/symbol.
        This is low-cost and high-efficiency.
        """
        option_type = option_type.upper()
        if option_type not in ("C", "P"):
            raise ValueError(f"option_type must be 'C' or 'P', got {option_type!r}")
        logger.info("Resolving symbol for %s %s expiring %s...", strike, option_type, expiry.date())

        # We look at definitions for the parent SYMBOL.OPT
        # Start/End window is small to minimize data weight
        data = self.client.timeseries.get_range(
            dataset="OPRA.PILLAR",
            symbols="SYMBOL.OPT",
            schema="definition",
            stype_in="parent",
            start=ts.date().isoformat(),
            end=(ts.date() + timedelta(days=1)).isoformat()
        )
        df = data.to_df()

        # Normalize put_call to single-char format ("C"/"P") regardless of Databento version
        if 'put_call' in df.columns:
            df['put_call'] = df['put_call'].astype(str).str.upper().str[0]
            valid = df['put_call'].isin({"C", "P"})
            if not valid.all():
                bad = df['put_call'][~valid].unique().tolist()
                logger.error("Unexpected put_call values after normalization: %s", bad)
                raise ValueError(f"Unexpected put_call values after normalization: {bad}")

        # Detect fixed-point (nanodollar) encoding: Databento stores strike as integer × 10^9
        strike_col = df['strike_price'].copy()
        if not strike_col.empty and strike_col.abs().max() > 100_000:
            logger.debug("Detected nanodollar strike encoding — scaling by 1e-9")
            strike_col = strike_col / 1e9

        # Filter for the specific strike and expiry
        match = df[
            ((strike_col - float(strike)).abs() < 0.01) &
            (df['expiration'].dt.date == expiry.date()) &
            (df['put_call'] == option_type)
        ]

        if match.empty:
            return None
        return match.iloc[0]['raw_symbol']

    def load_option_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Downloads 1-min OHLCV only for the requested symbol.
        Checks local cache first to save credits.
        """
        cache_path = self.get_cache_path(symbol)
        logger.debug("Cache path: %s", cache_path)

        if os.path.exists(cache_path):
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            # Ensure tz-aware index (round-trip through CSV can lose tz)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, utc=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
            elif str(df.index.tz) != "America/New_York":
                df.index = df.index.tz_convert("America/New_York")
            df = df[~df.index.duplicated(keep="first")]
            if df.empty:
                logger.info("Cache hit (empty) for %s — no market data available", symbol)
                return df
            cache_min, cache_max = df.index.min(), df.index.max()
            logger.debug("Cache exists: %s → %s", cache_min, cache_max)
            # Add a 3-day buffer to account for weekends/holidays: Databento
            # returns no data on non-trading days, so a request starting on
            # e.g. 2025-01-01 (holiday) will have cache_min = 2025-01-02.
            # Without the buffer the cache would be seen as incomplete.
            buf = pd.Timedelta(days=3)
            if (cache_min.date() <= (pd.Timestamp(start) + buf).date()
                    and cache_max.date() >= (pd.Timestamp(end) - buf).date()):
                logger.info("Cache hit for %s (%d rows)", symbol, len(df))
                return df
            if self.client is None:
                logger.info(
                    "Cache for %s does not fully cover %s → %s, but no Databento client is available; "
                    "using partial cache only",
                    symbol, start, end,
                )
                return df
            else:
                logger.info("Cache does not cover window (%s → %s) — downloading", start, end)
        else:
            if self.client is None:
                logger.info("No cache for %s and no Databento client available", symbol)
                return pd.DataFrame()
            logger.info("No cache for %s — downloading from Databento", symbol)

        logger.info("Requesting from Databento: %s | %s → %s", symbol, start, end)
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                data = self.client.timeseries.get_range(
                    dataset="OPRA.PILLAR",
                    symbols=symbol,
                    schema="ohlcv-1m",
                    stype_in="raw_symbol",
                    start=start.isoformat(),
                    end=end.isoformat()
                )
                df_new = data.to_df()
                df_new = df_new[~df_new.index.duplicated(keep="first")]
                # Normalize timestamps to America/New_York (Databento returns UTC)
                if not isinstance(df_new.index, pd.DatetimeIndex):
                    df_new.index = pd.to_datetime(df_new.index, utc=True)
                if df_new.index.tz is None:
                    df_new.index = df_new.index.tz_localize("UTC").tz_convert("America/New_York")
                else:
                    df_new.index = df_new.index.tz_convert("America/New_York")
                logger.info("Downloaded %d rows for %s — saving to cache", len(df_new), symbol)
                df_new.to_csv(cache_path)
                return df_new
            except Exception as e:
                if attempt < max_retries:
                    wait = 5 * attempt
                    logger.warning(
                        "Databento API error for %s (attempt %d/%d): %s — retrying in %ds",
                        symbol, attempt, max_retries, e, wait
                    )
                    time.sleep(wait)
                else:
                    logger.error("Databento API error for %s after %d attempts: %s", symbol, max_retries, e)
                    raise

# Example Usage:
# loader = DatabentoOptionsLoader(api_key="YOUR_KEY")
# osi = loader.get_contract_definition(ts=now, strike=450, expiry=friday, option_type='C')
# df = loader.load_option_bars(osi, start=entry_time, end=exit_time)


def download_databento_equities(
    symbol: str = "SYMBOL",
    start: str = "2025-08-01",
    end: str = "2026-02-14",
    cache_dir: str = "data/DataBento/equities/SYMBOL/1min",
) -> str:
    """Download 1-min OHLCV equity bars from Databento XNAS.ITCH and save as CSV.

    Returns the path to the saved CSV. Skips download if a cached file already
    covers the requested date range.  Uses ohlcv-1m because Databento doesn't
    offer ohlcv-5m for XNAS.ITCH — aggregate to 5-min with aggregator.py.
    """
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, f"{symbol}_1min_{start}_to_{end}.csv")

    if os.path.exists(out_path):
        try:
            df = pd.read_csv(out_path, parse_dates=["timestamp"], index_col="timestamp")
            if not df.empty:
                cache_min = df.index.min()
                cache_max = df.index.max()
                start_ts = pd.Timestamp(start, tz="America/New_York")
                end_ts = pd.Timestamp(end, tz="America/New_York")
                # Ensure tz-aware comparison
                if cache_min.tz is None:
                    cache_min = cache_min.tz_localize("America/New_York")
                if cache_max.tz is None:
                    cache_max = cache_max.tz_localize("America/New_York")
                # Add a 3-day buffer to account for weekends/holidays:
                # Databento returns no bars on non-trading days, so a
                # request starting on a holiday/weekend will have its
                # first bar on the next trading day — without the buffer
                # the cache would be considered incomplete every time.
                buf = pd.Timedelta(days=3)
                if cache_min <= start_ts + buf and cache_max >= end_ts - buf:
                    logger.info("Cache hit: %s (%d bars). Skipping download.", out_path, len(df))
                    return out_path
                logger.info(
                    "Cache %s does not cover %s → %s (has %s → %s) — re-downloading",
                    os.path.basename(out_path), start, end, cache_min.date(), cache_max.date(),
                )
        except Exception as exc:
            logger.warning("Could not validate cache %s (%s) — re-downloading", out_path, exc)

    api_key = os.getenv("DATA_BENTO_PW") or os.getenv("DATABENTO_API_KEY")
    if not api_key:
        raise RuntimeError("DATA_BENTO_PW env var not set")

    client = db.Historical(api_key)
    logger.info("Requesting %s ohlcv-1m from XNAS.ITCH (%s to %s)...", symbol, start, end)

    data = client.timeseries.get_range(
        dataset="XNAS.ITCH",
        symbols=symbol,
        schema="ohlcv-1m",
        start=start,
        end=end,
    )

    # Write raw Databento data to a temp CSV, then read back with pandas.
    # This avoids to_df() issues with ts_event grouper in some client versions.
    raw_path = out_path + ".raw.csv"
    data.to_csv(raw_path)
    df = pd.read_csv(raw_path)
    os.remove(raw_path)

    if df.empty:
        raise RuntimeError(f"Databento returned no data for {symbol} {start}→{end}")

    # Databento CSVs use ts_event as the bar timestamp
    ts_col = "ts_event" if "ts_event" in df.columns else df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col)
    df.index = df.index.tz_convert("America/New_York")
    df.index.name = "timestamp"

    # Filter to regular trading hours on weekdays
    df = df[df.index.dayofweek < 5]
    df = df.between_time("09:30", "16:00", inclusive="left")

    # Keep only OHLCV columns, rename to match Alpaca format
    ohlcv_cols = [c for c in df.columns if c in ("open", "high", "low", "close", "volume")]
    if not ohlcv_cols:
        logger.error("Available columns: %s", list(df.columns))
        raise RuntimeError("Could not find OHLCV columns in Databento output")
    df = df[ohlcv_cols]

    df.to_csv(out_path)
    logger.info("Saved %d bars to %s", len(df), out_path)
    return out_path


def load_databento_equities(
    cache_dir: str = "data/DataBento/equities/SYMBOL/5min",
    start: str = None,
    end: str = None,
) -> pd.DataFrame:
    """Load cached Databento equity CSVs — same return format as load_cached_csvs().

    Returns a DataFrame with a DatetimeIndex (EST tz-aware) and OHLCV columns.

    Expects organized structure: cache_dir/YYYY/SYMBOL_5min_YYYYMM.csv
    (produced by download_and_aggregate_databento.py).
    """
    all_files = sorted(globmod.glob(os.path.join(cache_dir, "*", "*.csv")))

    if not all_files:
        raise FileNotFoundError(f"No CSV files found in {cache_dir}/*/  — run download_and_aggregate_databento.py first")

    frames = []
    for f in all_files:
        df = pd.read_csv(f, index_col=0)
        
        # Check if index is already a datetime with timezone (from aggregated files)
        if not pd.api.types.is_datetime64_any_dtype(df.index):
            # Try to parse as timestamp column
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")
                df.index = pd.to_datetime(df.index, errors='coerce', utc=True)
            else:
                # Try to parse index
                df.index = pd.to_datetime(df.index, errors='coerce', utc=True)
        
        # Ensure it's a DatetimeIndex for timezone operations
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, errors='coerce', utc=True)
        
        # Set name if missing
        if df.index.name is None:
            df.index.name = "timestamp"
        
        # Skip files with invalid timestamps
        if df.index.isna().all():
            logger.warning("Skipped %s (all timestamps invalid)", os.path.basename(f))
            continue
        
        # Drop rows with NaN timestamps
        df = df[df.index.notna()]
        
        # Handle timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("America/New_York")
        elif str(df.index.tz) != "America/New_York":
            df.index = df.index.tz_convert("America/New_York")
        
        # Keep only OHLCV columns
        ohlcv_cols = [c for c in df.columns if c in ["open", "high", "low", "close", "volume"]]
        if ohlcv_cols:
            df = df[ohlcv_cols]
            frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No valid OHLCV data found in {cache_dir}")

    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]

    if start:
        start_ts = pd.Timestamp(start, tz="America/New_York")
        combined = combined[combined.index >= start_ts]
    if end:
        end_ts = pd.Timestamp(end, tz="America/New_York") + pd.Timedelta(days=1)
        combined = combined[combined.index < end_ts]

    logger.info("Loaded %d Databento equity bars from %d files", len(combined), len(all_files))
    return combined
