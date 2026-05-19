import pandas as pd


def rolling_high_low(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
    """Compute rolling highest high and lowest low over a period.

    Parameters
    ----------
    df : DataFrame with 'high' and 'low' columns
    period : lookback window size

    Returns
    -------
    (high_roll, low_roll) : tuple of pd.Series
    """
    high_roll = df["high"].rolling(window=period).max()
    low_roll = df["low"].rolling(window=period).min()
    return high_roll, low_roll


def double_ema_smooth(series: pd.Series, span1: int, span2: int) -> pd.Series:
    """Apply double EMA smoothing to a series.

    Parameters
    ----------
    series : pd.Series to smooth
    span1 : first EMA smoothing period
    span2 : second EMA smoothing period

    Returns
    -------
    pd.Series : double-smoothed series
    """
    return (
        series.ewm(span=span1, adjust=False)
        .mean()
        .ewm(span=span2, adjust=False)
        .mean()
    )
