import logging

import pandas as pd

logger = logging.getLogger(__name__)


def compute_ema(df: pd.DataFrame, period: int = 233, column: str = "close") -> pd.Series:
    """Compute an Exponential Moving Average.

    Parameters
    ----------
    df : DataFrame with the specified column
    period : EMA lookback period (default 233)
    column : column name to compute EMA on (default 'close')

    Returns
    -------
    pd.Series : EMA values (first ``period - 1`` values are warming up)
    """
    if column not in df.columns:
        raise KeyError(f"Column {column!r} not found in DataFrame")
    return df[column].ewm(span=period, adjust=False).mean()
