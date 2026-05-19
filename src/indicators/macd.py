import logging

import pandas as pd

logger = logging.getLogger(__name__)


def compute_macd(
    df: pd.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Compute MACD line, signal line, and histogram.

    Uses standard EMA-based MACD as implemented in most charting platforms
    (TradingView default: 12, 26, 9).

    Parameters
    ----------
    df            : DataFrame with a 'close' column
    fast_period   : period for the fast EMA (default 12)
    slow_period   : period for the slow EMA (default 26)
    signal_period : period for the signal EMA (default 9)

    Returns
    -------
    pd.DataFrame with columns:
        macd_line      : fast EMA − slow EMA
        macd_signal    : EMA of macd_line
        macd_histogram : macd_line − macd_signal
    """
    ema_fast = df["close"].ewm(span=fast_period, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow_period, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=signal_period, adjust=False).mean()
    macd_histogram = macd_line - macd_signal

    return pd.DataFrame(
        {
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
        },
        index=df.index,
    )
