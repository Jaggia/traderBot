import pandas as pd
import numpy as np

from src.signals.indicator_pair_pipeline import compute_indicators, generate_signals
from src.signals.strategy import IndicatorPairStrategy, create_strategy


def _base_df(n=20):
    idx = pd.date_range("2026-01-05 09:30", periods=n, freq="5min", tz="America/New_York")
    return pd.DataFrame(
        {
            "open": 400.0,
            "high": 401.0,
            "low": 399.0,
            "close": 400.0,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def test_strategy_factory_creates_indicator_pair_strategy():
    strategy = create_strategy(
        {
            "strategy": {"signal_system": "trigger_chain"},
            "signals": {
                "triggers": [
                    {"indicator": "tsi"},
                    {"indicator": "stoch_rsi"},
                ],
            },
        }
    )
    assert isinstance(strategy, IndicatorPairStrategy)


def test_trigger_chain_compute_indicators_supports_tsi_stoch_rsi():
    idx = pd.date_range("2026-01-05 09:30", periods=200, freq="5min", tz="America/New_York")
    prices = pd.Series(range(200), index=idx, dtype=float) + 400.0
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices + 1.0,
            "low": prices - 1.0,
            "close": prices,
            "volume": 1_000_000.0,
        },
        index=idx,
    )
    config = {
        "signals": {
            "trigger_chain": {
                "triggers": [
                    {"indicator": "tsi"},
                    {"indicator": "stoch_rsi"},
                ],
                "sequential": True,
                "sync_window": 5,
                "vwap_filter": False,
            }
        }
    }

    result = compute_indicators(df, config)

    assert "tsi" in result.columns
    assert "tsi_signal" in result.columns
    assert "stoch_rsi_k" in result.columns
    assert "stoch_rsi_d" in result.columns


def test_trigger_chain_generates_expected_tsi_stoch_rsi_signal():
    config = {
        "signals": {
            "trigger_chain": {
                "triggers": [
                    {"indicator": "tsi"},
                    {"indicator": "stoch_rsi"},
                ],
                "sequential": True,
                "sync_window": 5,
                "vwap_filter": False,
            }
        }
    }
    df = _base_df(30)
    
    # Fill in indicator columns directly to avoid compute_indicators dependency for this logic test
    df["tsi"] = 0.0
    df["tsi_signal"] = 0.0
    df["stoch_rsi_k"] = 50.0
    df["stoch_rsi_d"] = 50.0

    # TSI crossover at bar 4 (tsi > tsi_signal)
    df.iloc[4, df.columns.get_loc("tsi")] = -5
    df.iloc[5, df.columns.get_loc("tsi")] = 5
    df.iloc[4, df.columns.get_loc("tsi_signal")] = 0
    df.iloc[5, df.columns.get_loc("tsi_signal")] = 0
    
    # StochRSI crossover at bar 7 (stoch_rsi_k > 20)
    df.iloc[6, df.columns.get_loc("stoch_rsi_k")] = 15
    df.iloc[7, df.columns.get_loc("stoch_rsi_k")] = 25

    signals = generate_signals(df, config)
    
    # Signal should fire at bar 7 because it's within the sync_window of bar 5
    assert signals.iloc[7] == 1
    # Check that no other signals fired prematurely
    assert signals.iloc[5] == 0
    assert signals.iloc[6] == 0
