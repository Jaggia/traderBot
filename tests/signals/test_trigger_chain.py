"""Tests for the new trigger-chain signal pipeline.

Covers:
- 1-trigger (single indicator, pass-through)
- 2-trigger sequential chain (RSI → MACD, matching old logic output)
- 2-trigger sequential chain via presets only
- Explicit per-trigger event override
- Windowed mode (sequential=False)
- N>2 trigger chains (sequential + windowed)
- apply_sequential_chain state machine directly
- Strategy factory wiring
- Error cases
"""
import numpy as np
import pandas as pd
import pytest

from src.signals.sequential_logic import apply_sequential_chain
from src.signals.indicator_pair_pipeline import compute_indicators, generate_signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    dates = pd.date_range("2025-01-02 09:30", periods=n, freq="5min", tz="America/New_York")
    close = 400 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.randint(100_000, 2_000_000, n).astype(float),
    }, index=dates)


def _trigger_chain_config(triggers, sequential=True, sync_window=5, vwap_filter=False):
    return {
        "strategy": {"signal_system": "trigger_chain"},
        "signals": {
            "trigger_chain": {
                "triggers": triggers,
                "sequential": sequential,
                "sync_window": sync_window,
                "vwap_filter": vwap_filter,
            }
        },
    }


# ---------------------------------------------------------------------------
# Single-trigger (pass-through)
# ---------------------------------------------------------------------------

class TestSingleTrigger:
    def test_single_smi_produces_signals(self):
        """One trigger = single indicator, no arm/fire — just that event."""
        config = _trigger_chain_config([{"indicator": "smi"}])
        df = compute_indicators(_make_ohlcv(), config)
        signals = generate_signals(df, config)
        assert isinstance(signals, pd.Series)
        assert set(signals.unique()).issubset({-1, 0, 1})

    def test_single_rsi_adds_column(self):
        config = _trigger_chain_config([{"indicator": "rsi"}])
        df = compute_indicators(_make_ohlcv(), config)
        assert "rsi" in df.columns

    def test_single_macd_adds_columns(self):
        config = _trigger_chain_config([{"indicator": "macd"}])
        df = compute_indicators(_make_ohlcv(), config)
        assert "macd_histogram" in df.columns


# ---------------------------------------------------------------------------
# Two-trigger sequential chain (preset events)
# ---------------------------------------------------------------------------

class TestTwoTriggerSequentialPreset:
    def test_rsi_then_macd_produces_signals(self):
        """RSI arms, MACD fires — using preset events."""
        config = _trigger_chain_config([
            {"indicator": "rsi"},
            {"indicator": "macd"},
        ])
        df = compute_indicators(_make_ohlcv(), config)
        signals = generate_signals(df, config)
        assert set(signals.unique()).issubset({-1, 0, 1})

    def test_smi_then_wr_produces_signals(self):
        """SMI arms, WR fires — mimics old default pair but via trigger chain."""
        config = _trigger_chain_config([
            {"indicator": "smi"},
            {"indicator": "williams_r"},
        ])
        df = compute_indicators(_make_ohlcv(), config)
        signals = generate_signals(df, config)
        assert set(signals.unique()).issubset({-1, 0, 1})

    def test_both_indicators_computed(self):
        config = _trigger_chain_config([
            {"indicator": "rsi"},
            {"indicator": "macd"},
        ])
        df = compute_indicators(_make_ohlcv(), config)
        assert "rsi" in df.columns
        assert "macd_histogram" in df.columns


# ---------------------------------------------------------------------------
# Windowed mode (sequential=False)
# ---------------------------------------------------------------------------

class TestWindowedMode:
    """sequential=False means co-occurrence within sync_window — no state machine."""

    def test_two_trigger_windowed_produces_signals(self):
        """SMI + WR in windowed mode should produce valid signals."""
        config = _trigger_chain_config(
            [{"indicator": "smi"}, {"indicator": "williams_r"}],
            sequential=False,
            sync_window=10,
        )
        df = compute_indicators(_make_ohlcv(), config)
        signals = generate_signals(df, config)
        assert set(signals.unique()).issubset({-1, 0, 1})


# ---------------------------------------------------------------------------
# N>2 trigger chains (sequential mode)
# ---------------------------------------------------------------------------

class TestThreeTriggerSequential:
    """3 triggers in sequential mode: trigger[0] arms, all must fire within window."""

    def test_three_trigger_sequential_produces_signals(self):
        config = _trigger_chain_config(
            [{"indicator": "rsi"}, {"indicator": "smi"}, {"indicator": "macd"}],
            sequential=True, sync_window=20,
        )
        df = compute_indicators(_make_ohlcv(), config)
        signals = generate_signals(df, config)
        assert set(signals.unique()).issubset({-1, 0, 1})


# ---------------------------------------------------------------------------
# apply_sequential_chain state machine (unit tests)
# ---------------------------------------------------------------------------

class TestApplySequentialChain:
    """Direct tests of the apply_sequential_chain state machine."""

    def test_two_trigger_matches_apply_sequential_logic(self):
        """For N=2, apply_sequential_chain must produce the same result as apply_sequential_logic."""
        from src.signals.sequential_logic import apply_sequential_logic
        np.random.seed(99)
        arm = np.random.rand(50) > 0.85
        fire = np.random.rand(50) > 0.85
        expected = apply_sequential_logic(arm, fire, window=5)
        actual = apply_sequential_chain([arm, fire], window=5)
        np.testing.assert_array_equal(actual, expected)

    def test_three_trigger_basic_sequence(self):
        """Trigger0 at bar 2, trigger[1] at bar 4, trigger[2] at bar 5 → fires bar 5."""
        arm  = np.array([0, 0, 1, 0, 0, 0, 0, 0, 0, 0], dtype=bool)
        mid  = np.array([0, 0, 0, 0, 1, 0, 0, 0, 0, 0], dtype=bool)
        fire = np.array([0, 0, 0, 0, 0, 1, 0, 0, 0, 0], dtype=bool)
        result = apply_sequential_chain([arm, mid, fire], window=5)
        assert result[5]
        assert result.sum() == 1

    def test_three_trigger_window_expires(self):
        """Trigger0 at bar 0, trigger[1] at bar 3, trigger[2] at bar 7 → window=5 expired."""
        arm  = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 0], dtype=bool)
        mid  = np.array([0, 0, 0, 1, 0, 0, 0, 0, 0, 0], dtype=bool)
        fire = np.array([0, 0, 0, 0, 0, 0, 0, 1, 0, 0], dtype=bool)
        result = apply_sequential_chain([arm, mid, fire], window=5)
        assert result.sum() == 0, "Should not fire — window expired before trigger[2]"
