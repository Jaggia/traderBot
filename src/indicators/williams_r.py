import logging

import pandas as pd

logger = logging.getLogger(__name__)


from src.indicators.base import rolling_high_low


def compute_williams_r(df: pd.DataFrame, period: int = 13) -> pd.Series:
    """Compute Williams %R.

    Williams %R measures the level of the close relative to the highest high
    over a lookback period. Values range from -100 to 0.

    Parameters
    ----------
    df : DataFrame with 'high', 'low', 'close' columns
    period : lookback window

    Returns
    -------
    pd.Series : Williams %R values (-100 to 0)
    """
    high_roll, low_roll = rolling_high_low(df, period)

    range_ = high_roll - low_roll
    # Avoid division by zero on flat/zero-range bars (e.g. halted market).
    # Replace 0 with NaN before dividing so inf is never produced.
    wr = -100.0 * (high_roll - df["close"]) / range_.replace(0, float("nan"))
    return wr


# ============================================================================
# EXPLANATION: Williams %R Purpose, Range, and Trading Application
# ============================================================================

# WILLIAMS %R FORMULA
# ===================
# Williams %R = -100 × (Highest High - Close) / (Highest High - Lowest Low)
#
# Breaking it down:
#   Numerator: (Highest High - Close)
#     = Gap between close and the 13-bar high
#     = How far the close is from the peak
#   Denominator: (Highest High - Lowest Low)
#     = Total range of the lookback period
#     = Normalizes the numerator to a relative scale
#   Result: Expressed as a percentage and inverted (hence the -100 multiplier)
#
# Example with period=5:
#   Prices: [85, 88, 90, 87, 86]  <- current close
#   High = 90, Low = 85, Close = 86
#   WR = -100 × (90 - 86) / (90 - 85) = -100 × 4/5 = -80
#
#   If Close were at the high:
#   WR = -100 × (90 - 90) / (90 - 85) = 0  (maximum, strongest position)
#
#   If Close were at the low:
#   WR = -100 × (90 - 85) / (90 - 85) = -100  (minimum, weakest position)

# RANGE INTERPRETATION (-100 to 0)
# ================================
# Williams %R oscillates between -100 and 0 (not -100 to +100 like SMI).
#
#   -0 (near 0, e.g., -10 to 0):   Close near the highest high
#                                   Strong momentum, potentially overbought
#   -50:                            Close at the midpoint of range
#                                   Neutral position
#   -100 (near -100, e.g., -90 to -100): Close near lowest low
#                                   Weak momentum, potentially oversold
#
# The negative scale takes time to interpret but is standard in Williams %R
# tradition to distinguish it from Stochastic %K (which ranges 0 to 100).

# WILLIAMS %R PURPOSE
# ===================
# Williams %R is a momentum/oscillator indicator that measures "How far is
# the current close from the recent high, relative to the recent range?"
#
# Key goals:
#   1. OVERBOUGHT/OVERSOLD DETECTION:
#      - %R > -20 (close to 0): Overbought; pullback/reversal likely
#      - %R < -80 (close to -100): Oversold; bounce/recovery likely
#      - %R between -50 and -20: Bullish (above midpoint)
#      - %R between -80 and -50: Bearish (below midpoint)
#
#   2. MOMENTUM CONFIRMATION: Extreme %R levels (-100 or 0) often coincide
#      with exhaustion; reversals frequently occur near extremes.
#
#   3. DIVERGENCES: When price makes a new high but Williams %R doesn't,
#      momentum is not confirming strength—potential reversal signal.
#
#   4. RANGE-BOUND TRADING: In sideways markets, %R cycles between -100 and 0,
#      making it useful for mean-reversion trades (sell at -20, buy at -80).
#
# Trading applications:
#   - Mean reversion: Buy oversold (-80), sell overbought (-20)
#   - Momentum confirmation: Use %R extremes to validate trend strength
#   - Divergences: Price new high but %R fails to confirm = bearish signal
#   - Entry timing: Wait for %R to reach extreme before entering contrarian trades
#
# Comparison to SMI:
#   - Both are momentum oscillators using rolling highs/lows
#   - Williams %R is simpler, unsmoothed, and more responsive to near-term moves
#   - SMI is double-smoothed, reducing noise but lagging slightly
#   - Williams %R better for quick mean-reversion; SMI better for trend confirmation
