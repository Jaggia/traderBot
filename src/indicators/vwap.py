import logging

import pandas as pd

logger = logging.getLogger(__name__)


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Compute VWAP with daily reset.

    Requires 'high', 'low', 'close', 'volume' columns and a DatetimeIndex.

    Parameters
    ----------
    df : DataFrame with OHLCV data and DatetimeIndex

    Returns
    -------
    pd.Series : VWAP values, resetting at the start of each trading day
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_volume = typical_price * df["volume"]

    # Group by calendar date for daily reset
    dates = df.index.date
    cum_tp_vol = tp_volume.groupby(dates).cumsum()
    cum_vol = df["volume"].groupby(dates).cumsum()

    zero_vol_count = (df["volume"] == 0).sum()
    if zero_vol_count > 0:
        logger.warning("compute_vwap: %d zero-volume bar(s) detected — will be forward-filled", zero_vol_count)

    vwap = cum_tp_vol / cum_vol
    vwap = vwap.replace([float("inf"), float("-inf")], float("nan"))
    # Forward-fill within each day so zero-volume bars inherit the prior VWAP
    vwap = vwap.groupby(dates).transform(lambda s: s.ffill())
    return vwap


# ============================================================================
# EXPLANATION: VWAP Purpose, Typical Price, and Daily Reset
# ============================================================================

# TYPICAL PRICE
# =============
# Typical Price = (High + Low + Close) / 3.0
#
# This represents an average fair price for the bar instead of using just
# the close. By incorporating high and low, it captures the entire range of
# trading activity within that bar, providing a more balanced reference point
# than close alone.
#
# Example:
#   High=100, Low=98, Close=99 -> Typical Price = 99.0
#   High=100, Low=90, Close=95 -> Typical Price = 95.0  (wide range, lower avg)

# VOLUME WEIGHTING
# ================
# tp_volume = typical_price * volume captures the "weight" of each bar.
# Bars with higher volume get more influence on the final VWAP, which reflects
# institutional order flow: larger trades are more reliable price discovery.
#
# Cumulative calculations:
#   cum_tp_vol: Running sum of (typical_price × volume)
#   cum_vol: Running sum of volume
#   VWAP = cum_tp_vol / cum_vol  (weighted average price)

# DAILY RESET
# ===========
# groupby(dates) resets the cumulative sums at each new calendar day.
# This means VWAP starts fresh at market open (09:30) and accumulates
# throughout the trading day.
#
# Without daily reset, VWAP would gradually converge to a long-term average,
# losing relevance as an intraday reference. Daily reset ensures VWAP
# reflects the current day's volume-weighted behavior.

# VWAP PURPOSE
# ============
# VWAP answers: "At what price has the bulk of today's volume traded?"
#
# Key goals:
#   1. FAIR VALUE: Identify the volume-weighted fair price for the day.
#      Institutions often use VWAP as a benchmark for execution quality.
#
#   2. SUPPORT/RESISTANCE: VWAP acts as dynamic intraday S/R.
#      - Price above VWAP: Buyers in control (strength)
#      - Price below VWAP: Sellers in control (weakness)
#
#   3. MEAN REVERSION: Prices that deviate far from VWAP tend to revert.
#      Sharp spikes up/down often pull back toward VWAP.
#
#   4. VOLUME CONTEXT: Combines price AND volume, making it more meaningful
#      than simple price levels alone.
#
# Trading applications:
#   - Entry signals: Buy bounces off VWAP, sell rejections at VWAP
#   - Trend confirmation: Sustained price above VWAP = bullish
#   - Exit targets: Partial profits at VWAP after a move away
#   - Swing traders often scale in/out using VWAP pullbacks
#
# Advantage:
#   VWAP is harder to manipulate than simple moving averages because it
#   requires large volume to shift, making it a reliable institutional reference.
