import glob as globmod
import logging
import os
import calendar
from datetime import datetime

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
API_KEY = os.getenv("ALPACA_UN")
SECRET_KEY = os.getenv("ALPACA_PW")
BASE_DIR = "data/Alpaca/equities/SYMBOL"


def _get_client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(API_KEY, SECRET_KEY)


def download_bars(symbol: str, start_dt: datetime, end_dt: datetime,
                  tf_value: int = 5, tf_unit: TimeFrameUnit = TimeFrameUnit.Minute) -> pd.DataFrame | None:
    """Download OHLCV bars from Alpaca for any timeframe.

    Returns a cleaned DataFrame filtered to regular trading hours, or None if empty.
    """
    client = _get_client()
    request_params = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame(tf_value, tf_unit),
        start=start_dt,
        end=end_dt,
        adjustment="split",
    )

    bars = client.get_stock_bars(request_params)
    df = bars.df

    if df.empty:
        logger.info("No data for %s from %s to %s", symbol, start_dt.date(), end_dt.date())
        return None

    df = df.reset_index()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("America/New_York")
    df.set_index("timestamp", inplace=True)
    df = df[df.index.dayofweek < 5]
    df = df.between_time("09:30", "16:00", inclusive="left")
    return df


def _needs_update(folder: str, prefix: str, year: int, month: int) -> bool:
    """Check if a monthly CSV is missing or incomplete."""
    fname = f"{prefix}_{year:04d}{month:02d}.csv"
    path = os.path.join(folder, str(year), fname)
    if not os.path.exists(path):
        return True
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    if df.empty:
        return True
    last_date = df.index.max()
    # Guard: if the last date in the file belongs to a different year/month
    # (e.g. UTC→EST conversion pushed a Feb bar into Jan), treat as stale.
    if last_date.year != year or last_date.month != month:
        return True
    last_day = calendar.monthrange(year, month)[1]
    today = datetime.now()
    if year == today.year and month == today.month:
        expected_last_day = max(today.day - 1, 1)
        return last_date.day < expected_last_day
    return last_date.day < last_day - 2


def _download_month(symbol: str, year: int, month: int,
                    tf_label: str, tf_value: int, tf_unit: TimeFrameUnit):
    """Download one month of data and save to the correct directory."""
    folder = os.path.join(BASE_DIR, tf_label)
    prefix = f"{symbol}_{tf_label}"

    if not _needs_update(folder, prefix, year, month):
        logger.info("%s_%04d%02d.csv — up to date, skipping", prefix, year, month)
        return

    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1, 0, 0)

    today = datetime.now()
    if year == today.year and month == today.month:
        end = datetime(year, month, min(today.day, last_day), 23, 59)
    else:
        end = datetime(year, month, last_day, 23, 59)

    logger.info("Downloading %s_%04d%02d (%s to %s)...", prefix, year, month, start.date(), end.date())
    data = download_bars(symbol, start, end, tf_value, tf_unit)

    if data is not None and not data.empty:
        out_dir = os.path.join(folder, str(year))
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{prefix}_{year:04d}{month:02d}.csv")
        data.to_csv(out_path)
        logger.info("Saved %d bars to %s", len(data), out_path)
    else:
        logger.warning("No data returned for %d-%02d", year, month)


def update_to_present(symbol: str = "SYMBOL", start_year: int = None, start_month: int = None):
    """Scan local CSVs and download any missing/incomplete months up to today.

    If start_year/start_month are not given, it finds the latest existing file
    and starts from there.
    """
    today = datetime.now()

    if start_year is None or start_month is None:
        # Find the latest file across both timeframes to determine where to start
        all_csvs = sorted(globmod.glob(os.path.join(BASE_DIR, "5min", "**", "*.csv"), recursive=True))
        if all_csvs:
            latest = os.path.basename(all_csvs[-1])  # e.g. SYMBOL_5min_202601.csv
            date_part = latest.split("_")[-1].replace(".csv", "")
            start_year = int(date_part[:4])
            start_month = int(date_part[4:6])
        else:
            start_year = today.year
            start_month = 1

    # Build list of (year, month) pairs from start to present
    months = []
    y, m = start_year, start_month
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    for tf_label, tf_value, tf_unit in [
        ("1min", 1, TimeFrameUnit.Minute),
        ("5min", 5, TimeFrameUnit.Minute),
    ]:
        logger.info("Updating %s %s data", symbol, tf_label)
        for year, month in months:
            _download_month(symbol, year, month, tf_label, tf_value, tf_unit)

    logger.info("Data update complete.")


def load_cached_csvs(
    base_dir: str = "data/Alpaca/equities/SYMBOL/5min",
    start: str = None,
    end: str = None,
) -> pd.DataFrame:
    """Load cached CSVs from disk, optionally filtering to a date range.

    When start/end are provided (e.g. "2025-11-10"), only loads the monthly
    files that overlap with the requested range — much faster than loading all.
    """
    all_files = sorted(globmod.glob(os.path.join(base_dir, "**", "*.csv"), recursive=True))
    if not all_files:
        raise FileNotFoundError(f"No CSV files found in {base_dir}")

    # Pre-filter files by year/month from filename (e.g. SYMBOL_5min_202511.csv)
    if start or end:
        start_ym = int(start[:7].replace("-", "")) if start else 0
        end_ym = int(end[:7].replace("-", "")) if end else 999999
        filtered = []
        for f in all_files:
            # Extract YYYYMM from filename like SYMBOL_5min_202511.csv
            base = os.path.basename(f).replace(".csv", "")
            ym_str = base.split("_")[-1]  # "202511"
            if len(ym_str) == 6 and ym_str.isdigit():
                ym = int(ym_str)
                if ym >= start_ym and ym <= end_ym:
                    filtered.append(f)
            else:
                filtered.append(f)
        all_files = filtered

    frames = []
    for f in all_files:
        # Skip non-alpaca CSV files like tradingview_exact.csv
        basename = os.path.basename(f)
        if "tradingview" in basename.lower():
            continue
        
        df = pd.read_csv(f)
        # Check if timestamp is in columns; if not, try to reset index
        if "timestamp" not in df.columns:
            if df.index.name == "timestamp":
                df = df.reset_index()
            else:
                raise ValueError(f"No timestamp column in {f}")
        
        # Strip whitespace from timestamp strings before parsing
        df["timestamp"] = df["timestamp"].astype(str).str.strip()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("America/New_York")
        df.set_index("timestamp", inplace=True)
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No CSV files matched the date range {start} to {end}")

    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]

    # Row-level date filtering (file-level filter above only selects which months to load)
    if start:
        start_ts = pd.Timestamp(start, tz="America/New_York")
        combined = combined[combined.index >= start_ts]
    if end:
        end_ts = pd.Timestamp(end, tz="America/New_York") + pd.Timedelta(days=1)
        combined = combined[combined.index < end_ts]

    logger.info("Loaded %d bars from %d files", len(combined), len(all_files))
    return combined


# --- ENTRY POINT ---
# Run directly to update data: python -m src.data.alpaca_loader [--full YEAR]
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "--full":
        # Full year download: python -m src.data.alpaca_loader --full 2018
        year = int(sys.argv[2])
        for tf_label, tf_value, tf_unit in [
            ("1min", 1, TimeFrameUnit.Minute),
            ("5min", 5, TimeFrameUnit.Minute),
        ]:
            for month in range(1, 13):
                _download_month("SYMBOL", year, month, tf_label, tf_value, tf_unit)
    else:
        # Default: update to present day
        update_to_present()
