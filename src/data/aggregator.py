import logging

import pandas as pd

logger = logging.getLogger(__name__)


def aggregate_to_Nmin(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Resample any-frequency OHLCV to N-minute bars.

    Unlike ``aggregate_1m_to_5m``, no time-of-day or weekday filters are
    applied — the input is assumed to be pre-filtered.  Intended for internal
    signal-layer resampling (e.g. 5-min → 15-min).

    Parameters
    ----------
    df : DataFrame with DatetimeIndex and OHLCV columns
    n : target bar width in minutes

    Returns
    -------
    pd.DataFrame : resampled OHLCV bars (incomplete/NaN bars dropped)
    """
    agg_map = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    cols = [c for c in df.columns if c in agg_map]
    return (
        df[cols]
        .resample(f"{n}min", label="left", closed="left")
        .agg({c: agg_map[c] for c in cols})
        .dropna(subset=["open", "high", "low", "close"])
    )


def aggregate_1m_to_5m(df_1m: pd.DataFrame, eod_cutoff_time: str = "15:55") -> pd.DataFrame:
    """Resample 1-minute OHLCV bars to 5-minute bars.

    Parameters
    ----------
    df_1m : pd.DataFrame
        DataFrame with a DatetimeIndex (EST tz-aware) and at minimum
        open, high, low, close, volume columns.  May also contain
        symbol, trade_count, vwap — symbol and trade_count are carried
        forward; vwap is dropped (can't be naively aggregated).
    eod_cutoff_time : str
        The final bar time to include (HH:MM).

    Returns
    -------
    pd.DataFrame
        5-minute bars in the same format, filtered to 09:30–cutoff.
    """
    agg_map = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }

    # Carry forward optional columns
    if "symbol" in df_1m.columns:
        agg_map["symbol"] = "first"
    if "trade_count" in df_1m.columns:
        agg_map["trade_count"] = "sum"

    # Drop vwap before resampling — can't aggregate naively
    cols_to_use = [c for c in df_1m.columns if c in agg_map]
    df_work = df_1m[cols_to_use]

    df_5m = df_work.resample("5min", label="left", closed="left").agg(
        {c: agg_map[c] for c in cols_to_use}
    )

    # Drop incomplete bars (NaN from partial windows)
    df_5m = df_5m.dropna(subset=["open", "high", "low", "close"])

    # Filter to regular trading hours (resample can create edge bars)
    df_5m = df_5m.between_time("09:30", eod_cutoff_time)

    # Keep only weekdays
    df_5m = df_5m[df_5m.index.dayofweek < 5]

    return df_5m
