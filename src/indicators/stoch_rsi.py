import pandas as pd
import numpy as np

from src.indicators.rsi import compute_rsi

def compute_stoch_rsi(df: pd.DataFrame, length: int = 14, smooth_k: int = 3, smooth_d: int = 3, rsi_period: int = 14) -> pd.DataFrame:
    """Compute the Stochastic RSI.

    Parameters
    ----------
    df : DataFrame with 'close' column
    length : lookback period for StochRSI min/max
    smooth_k : EMA smoothing period for %K
    smooth_d : EMA smoothing period for %D (signal line of %K)
    rsi_period : lookback period for the underlying RSI

    Returns
    -------
    pd.DataFrame : DataFrame with 'stoch_rsi_k' and 'stoch_rsi_d' columns
    """
    rsi = compute_rsi(df, period=rsi_period)
    
    # Rolling min and max of RSI
    rsi_low = rsi.rolling(window=length).min()
    rsi_high = rsi.rolling(window=length).max()
    
    # Raw Stochastic RSI (0 to 1)
    stoch_rsi_raw = (rsi - rsi_low) / (rsi_high - rsi_low)
    stoch_rsi_raw = stoch_rsi_raw.replace([np.inf, -np.inf], np.nan)
    
    # Scale to 0-100
    stoch_rsi_raw = stoch_rsi_raw * 100.0
    
    # Smooth %K
    stoch_rsi_k = stoch_rsi_raw.ewm(span=smooth_k, adjust=False).mean()
    
    # Smooth %D
    stoch_rsi_d = stoch_rsi_k.ewm(span=smooth_d, adjust=False).mean()
    
    return pd.DataFrame({"stoch_rsi_k": stoch_rsi_k, "stoch_rsi_d": stoch_rsi_d})
