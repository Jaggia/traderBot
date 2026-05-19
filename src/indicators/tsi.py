import pandas as pd
import numpy as np

from src.indicators.base import double_ema_smooth

def compute_tsi(df: pd.DataFrame, long_period: int = 25, short_period: int = 13, signal_period: int = 7) -> pd.DataFrame:
    """Compute the True Strength Index (TSI) and its Signal line.

    Parameters
    ----------
    df : DataFrame with 'close' column
    long_period : first EMA smoothing period
    short_period : second EMA smoothing period
    signal_period : EMA smoothing period for the signal line

    Returns
    -------
    pd.DataFrame : DataFrame with 'tsi' and 'tsi_signal' columns
    """
    diff = df["close"].diff()
    abs_diff = diff.abs()

    # Double smoothing of momentum
    smooth_diff = double_ema_smooth(diff, long_period, short_period)
    
    # Double smoothing of absolute momentum
    smooth_abs_diff = double_ema_smooth(abs_diff, long_period, short_period)

    tsi = 100.0 * (smooth_diff / smooth_abs_diff)
    tsi = tsi.replace([np.inf, -np.inf], np.nan)
    
    tsi_signal = tsi.ewm(span=signal_period, adjust=False).mean()
    
    return pd.DataFrame({"tsi": tsi, "tsi_signal": tsi_signal})
