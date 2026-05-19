import logging

import pandas as pd

logger = logging.getLogger(__name__)


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute the Relative Strength Index (RSI).

    Uses Wilder's smoothing (equivalent to EWM with alpha=1/period,
    which matches TradingView's default RSI implementation).

    Parameters
    ----------
    df     : DataFrame with a 'close' column
    period : lookback period (default 14)

    Returns
    -------
    pd.Series : RSI values (0–100); first ``period`` bars are NaN during warm-up
    """
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Wilder smoothing: alpha = 1/period  (adjust=False → recursive formula)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # First bar of delta is always NaN; propagate that as warm-up NaN
    rsi.iloc[0] = float("nan")
    return rsi
