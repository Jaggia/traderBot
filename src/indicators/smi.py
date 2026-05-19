import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


from src.indicators.base import double_ema_smooth, rolling_high_low


def compute_smi(df: pd.DataFrame, period: int = 13, smooth1: int = 8, smooth2: int = 8) -> pd.Series:
    """Compute the Stochastic Momentum Index (SMI).

    SMI measures where the close is relative to the midpoint of the
    high-low range, double-smoothed with EMAs.

    Parameters
    ----------
    df : DataFrame with 'high', 'low', 'close' columns
    period : lookback for highest-high / lowest-low
    smooth1 : first EMA smoothing period
    smooth2 : second EMA smoothing period

    Returns
    -------
    pd.Series : SMI values (typically oscillates -100 to +100)
    """
    high_roll, low_roll = rolling_high_low(df, period)

    midpoint = (high_roll + low_roll) / 2.0
    diff = df["close"] - midpoint           # distance from midpoint
    range_ = high_roll - low_roll           # full range

    # Double EMA smoothing on numerator and denominator
    diff_smooth = double_ema_smooth(diff, smooth1, smooth2)
    range_smooth = double_ema_smooth(range_, smooth1, smooth2)

    smi = 100.0 * (diff_smooth / (range_smooth / 2.0))
    smi = smi.replace([np.inf, -np.inf], np.nan)
    return smi


# ============================================================================
# EXPLANATION: rolling(), ewm(), and SMI Purpose
# ============================================================================

# ROLLING WINDOW
# ==============
# rolling(window=period) creates a sliding window of the specified size that
# moves one row at a time through the data. For each position, it performs a
# calculation on all values within that window.
#
# Example with rolling(window=3).max():
#   prices = [10, 15, 12, 18, 14, 20]
#   result  = [NaN, NaN, 15, 18, 18, 20]  (first 2 are NaN due to insufficient data)
#
# The rolling window is fundamental to finding local highs/lows. In SMI,
# rolling(period) identifies the highest and lowest prices within the recent
# lookback period, establishing context for the current price.

# EXPONENTIALLY WEIGHTED MOVING AVERAGE (EWM)
# ============================================
# .ewm(span=N, adjust=False).mean() computes an EMA that gives exponentially
# more weight to recent values and less to older ones. The 'span' parameter
# controls how far back the "effective" lookback goes.
#
# Comparison to simple moving average:
#   SMA: All values in window get equal weight (1/N each)
#   EMA: Recent values weighted ~2/(span+1), older values decay exponentially
#
# adjust=False means:
#   - Calculate using the recursive/online formula (faster, standard in trading)
#   - Recent data points dominate the result immediately
#   - No recalculation of past values based on future data
#
# In SMI, we apply EWM twice ("double-smoothed"):
#   1st EMA: Reduces noise, preserves major trends
#   2nd EMA: Further smooths, reduces whipsaw, creates more stable signal
# This two-stage smoothing prevents the indicator from reacting to every
# minor price fluctuation while still remaining responsive.

# STOCHASTIC MOMENTUM INDEX (SMI) PURPOSE
# ========================================
# SMI is a momentum oscillator that answers: "How strong is the current move
# relative to recent volatility?"
#
# Key goals:
#   1. LOCATION: Determine where price closes relative to the high-low range.
#      (Distance from midpoint tells us if we're closer to highs or lows)
#
#   2. NORMALIZED: Express this as a percentage (-100 to +100) for consistency
#      across different price ranges and volatility regimes.
#
#   3. MOMENTUM: Measure speed/strength of price movement using double smoothing.
#      Smoothing removes noise while preserving genuine directional momentum.
#
#   4. OVERBOUGHT/OVERSOLD SIGNALS:
#      - SMI > +50: Pull toward highs (potential overbought)
#      - SMI < -50: Pulled toward lows (potential oversold)
#      - SMI near 0: Price near midpoint (neutral/equilibrium)
#
# Trading application:
#   - Divergences: When SMI and price move in opposite directions, momentum
#     may be exhausting and a reversal could be imminent.
#   - Crossovers: SMI crossing 0 can signal momentum phase changes.
#   - Extremes: Values beyond +50/-50 often precede pullbacks or reversals.
#
# Advantage over raw stochastics:
#   Double smoothing reduces false signals from small price noise while
#   keeping the indicator responsive to genuine momentum changes. This makes
#   it more suitable for swing trading and reduces "whipsaw" trades.
