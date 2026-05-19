"""Load and convert TradingView CSV data from PST to EST."""
import glob as globmod
import os
import pandas as pd


def _parse_tv_csv(file_path: str) -> pd.DataFrame:
    """Read a single TradingView CSV and return an EST-indexed OHLCV DataFrame."""
    df = pd.read_csv(file_path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    if df["datetime"].dt.tz is None:
        df["datetime"] = df["datetime"].dt.tz_localize("America/Los_Angeles")
    df["datetime"] = df["datetime"].dt.tz_convert("America/New_York")
    df.set_index("datetime", inplace=True)
    df.index.name = "timestamp"
    cols = ["open", "high", "low", "close", "volume"]
    return df[[c for c in cols if c in df.columns]]


def load_tradingview_csv(
    path: str = "data/TV/equities/SYMBOL/5min",
    start: str = None,
    end: str = None,
) -> pd.DataFrame:
    """Load TradingView CSVs from a directory (or single file) and return EST OHLCV.

    Accepts either a directory (globs all *.csv files and concatenates) or a
    direct path to a single CSV file. Timestamps are converted from PST/PDT to
    EST/EDT. Duplicates are dropped and the result is sorted by time.

    Parameters
    ----------
    path : str
        Directory containing TradingView CSV exports, or path to a single CSV.
    start, end : str, optional
        YYYY-MM-DD date filters (inclusive on both ends).
    """
    if os.path.isdir(path):
        files = sorted(globmod.glob(os.path.join(path, "*.csv")))
        if not files:
            raise FileNotFoundError(f"No CSV files found in {path}")
        frames = [_parse_tv_csv(f) for f in files]
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
    elif os.path.isfile(path):
        df = _parse_tv_csv(path)
    else:
        raise FileNotFoundError(f"TradingView data not found at {path}")

    if start:
        df = df[df.index >= pd.Timestamp(start, tz="America/New_York")]
    if end:
        df = df[df.index < pd.Timestamp(end, tz="America/New_York") + pd.Timedelta(days=1)]

    return df
