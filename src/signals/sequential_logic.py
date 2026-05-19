"""Generic sequential trigger state machine and event detection primitives.

Any signal pipeline can import these to build sequential-logic
without depending on specific indicators.

Usage
-----
from src.signals.sequential_logic import (
    apply_sequential_logic,
    within_window,
    crossover,
    crossunder,
    series_crossover,
    series_crossunder,
)

trigger0_events = crossunder(rsi, threshold=30)    # RSI dips below 30
trigger1_events = crossover(macd_hist, threshold=0) # MACD histogram turns positive

long_signal  = pd.Series(
    apply_sequential_logic(trigger0_events.values, trigger1_events.values, window=5),
    index=df.index,
)
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Event detectors (vectorized, return boolean Series)
# ---------------------------------------------------------------------------

def crossover(series: pd.Series, threshold: float) -> pd.Series:
    """True on bars where *series* crosses **above** *threshold*.

    Bar i fires when series[i] > threshold AND series[i-1] <= threshold.
    """
    return (series > threshold) & (series.shift(1) <= threshold)


def crossunder(series: pd.Series, threshold: float) -> pd.Series:
    """True on bars where *series* crosses **below** *threshold*.

    Bar i fires when series[i] < threshold AND series[i-1] >= threshold.
    """
    return (series < threshold) & (series.shift(1) >= threshold)


def series_crossover(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """True on bars where *fast* crosses **above** *slow* (two-line crossover)."""
    return (fast > slow) & (fast.shift(1) <= slow.shift(1))


def series_crossunder(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """True on bars where *fast* crosses **below** *slow* (two-line crossunder)."""
    return (fast < slow) & (fast.shift(1) >= slow.shift(1))


# ---------------------------------------------------------------------------
# Rolling-window helper (non-sequential path)
# ---------------------------------------------------------------------------

def within_window(trigger: pd.Series, window: int) -> pd.Series:
    """True if *trigger* fired within the last *window* bars (inclusive of current).

    Uses rolling(window=window + 1) to match TradingView's ``ta.barssince(x) <= window``
    which covers the current bar plus *window* prior bars (window + 1 total bars).
    This is intentional — a window of 5 means "fired within the last 5 bars including
    this one", i.e. bars 0 through 5 (6 bars total).
    """
    return trigger.rolling(window=window + 1, min_periods=1).max().astype(bool)


# ---------------------------------------------------------------------------
# Sequential state machine
# ---------------------------------------------------------------------------

def apply_sequential_logic(
    trigger0_events: np.ndarray,
    trigger1_events: np.ndarray,
    window: int,
) -> np.ndarray:
    """Bar-by-bar sequential mode: trigger[0] arms the system; trigger[1] fires + resets.

    Rules
    -----
    - A *trigger[0] event* sets the system to armed and records the trigger bar.
    - Only one signal fires per trigger cycle (subsequent fire events are ignored until
      the system re-arms).
    - If the *window* expires (``current_bar - trigger_bar > window``) before a
      fire event, the arm is discarded silently.
    - A new trigger[0] event always resets the trigger bar (re-arming mid-window is allowed).

    Parameters
    ----------
    trigger0_events : boolean numpy array — True on bars that arm the system
    trigger1_events : boolean numpy array — True on bars that fire the signal
    window          : maximum bars between trigger[0] and trigger[1] (exclusive: > window expires)

    Returns
    -------
    numpy.ndarray of bool — True on bars where a signal fires
    """
    n = len(trigger0_events)
    signal = np.zeros(n, dtype=bool)
    is_armed = False
    trigger0_bar = -window - 1  # ensures no false fire before first arm

    for i in range(n):
        # New trigger[0] event always refreshes armed state (re-arm resets window)
        if trigger0_events[i]:
            is_armed = True
            trigger0_bar = i
        # Expire arm if window elapsed without a fire
        if is_armed and (i - trigger0_bar) > window:
            is_armed = False
        # Fire if armed and trigger[1] event coincides
        if trigger1_events[i] and is_armed:
            signal[i] = True
            is_armed = False  # reset after single fire

    return signal


def apply_sequential_chain(
    trigger_arrays: list[np.ndarray],
    window: int,
) -> np.ndarray:
    """N-trigger sequential chain: trigger[0] arms, triggers[1..N-1] must all fire
    within *window* bars of the original trigger[0] bar.

    Rules
    -----
    - trigger[0] arms the system and records the trigger[0] bar.
    - The chain advances through triggers sequentially: once trigger[k] fires,
      the system waits for trigger[k+1].
    - All triggers must fire within ``window`` bars of trigger[0]'s bar.
      If the window expires at any stage, the chain resets.
    - A new trigger[0] event always resets the chain and window.
    - Only one signal fires per cycle (the final trigger in the chain).

    For N=2, this is equivalent to ``apply_sequential_logic``.

    Parameters
    ----------
    trigger_arrays : list of boolean numpy arrays, length >= 2
    window         : max bars from trigger[0] to final trigger fire

    Returns
    -------
    numpy.ndarray of bool — True on bars where the full chain completes
    """
    n = len(trigger_arrays[0])
    num_triggers = len(trigger_arrays)
    signal = np.zeros(n, dtype=bool)

    trigger0_bar = -window - 1
    stage = 0  # 0 = waiting for trigger0, 1..N-1 = waiting for that trigger

    for i in range(n):
        # trigger[0] always resets the chain
        if trigger_arrays[0][i]:
            trigger0_bar = i
            stage = 1  # now waiting for trigger[1]

        # Check window expiry
        if stage > 0 and (i - trigger0_bar) > window:
            stage = 0  # chain expired

        # Advance through remaining triggers
        if stage > 0 and stage < num_triggers and trigger_arrays[stage][i]:
            if stage == num_triggers - 1:
                # Final trigger fires — signal!
                signal[i] = True
                stage = 0  # reset after fire
            else:
                stage += 1  # advance to next trigger

    return signal
