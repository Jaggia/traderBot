import pandas as pd
import numpy as np
import pytest

from src.signals.indicator_pair_pipeline import compute_indicators, generate_signals


@pytest.fixture
def base_config():
    return {
        "signals": {
            "smi_fast": {"period": 5, "smooth1": 8, "smooth2": 8},
            "smi_slow": {"period": 13, "smooth1": 8, "smooth2": 8},
            "williams_r": {"period": 13},
            "sync_window": 5,
            "pair_mode": "either",
            "vwap_filter": False,
        }
    }


def _make_df_with_indicators(n=20):
    """Create a DataFrame with pre-set indicator columns for testing signal logic."""
    dates = pd.date_range("2023-01-03 09:30", periods=n, freq="5min", tz="America/New_York")
    df = pd.DataFrame({
        "open": 400.0, "high": 401.0, "low": 399.0, "close": 400.0, "volume": 1e6,
        "smi_fast": 0.0, "smi_slow": 0.0, "williams_r": -50.0,
    }, index=dates)
    return df


class TestSignalGeneration:
    def test_no_signal_when_flat(self, base_config):
        df = _make_df_with_indicators()
        signals = generate_signals(df, base_config)
        assert (signals == 0).all()

    def test_long_signal_on_sync(self, base_config):
        """SMI cross up at bar 5, W%R cross up at bar 7 — within 5-bar window → long."""
        df = _make_df_with_indicators(20)
        # SMI fast crosses above slow at bar 5
        df.iloc[4, df.columns.get_loc("smi_fast")] = -5
        df.iloc[5, df.columns.get_loc("smi_fast")] = 5
        df.iloc[4, df.columns.get_loc("smi_slow")] = 0
        df.iloc[5, df.columns.get_loc("smi_slow")] = 0
        # W%R crosses above -80 at bar 7
        df.iloc[6, df.columns.get_loc("williams_r")] = -85
        df.iloc[7, df.columns.get_loc("williams_r")] = -75
        # Keep SMI fast > slow through bar 7 so window still valid
        for i in range(6, 10):
            df.iloc[i, df.columns.get_loc("smi_fast")] = 5
            df.iloc[i, df.columns.get_loc("smi_slow")] = 0

        signals = generate_signals(df, base_config)
        assert signals.iloc[7] == 1

    def test_short_signal_on_sync(self, base_config):
        """SMI cross down + W%R cross below -20 within window → short."""
        df = _make_df_with_indicators(20)
        # SMI fast crosses below slow at bar 5
        df.iloc[4, df.columns.get_loc("smi_fast")] = 5
        df.iloc[5, df.columns.get_loc("smi_fast")] = -5
        df.iloc[4, df.columns.get_loc("smi_slow")] = 0
        df.iloc[5, df.columns.get_loc("smi_slow")] = 0
        # W%R crosses below -20 at bar 7
        df.iloc[6, df.columns.get_loc("williams_r")] = -15
        df.iloc[7, df.columns.get_loc("williams_r")] = -25
        for i in range(6, 10):
            df.iloc[i, df.columns.get_loc("smi_fast")] = -5
            df.iloc[i, df.columns.get_loc("smi_slow")] = 0

        signals = generate_signals(df, base_config)
        assert signals.iloc[7] == -1

    def test_no_signal_outside_window(self, base_config):
        """If triggers are more than sync_window bars apart, no signal."""
        base_config["signals"]["sync_window"] = 2
        df = _make_df_with_indicators(20)
        # SMI cross up at bar 3
        df.iloc[2, df.columns.get_loc("smi_fast")] = -5
        df.iloc[3, df.columns.get_loc("smi_fast")] = 5
        df.iloc[2, df.columns.get_loc("smi_slow")] = 0
        df.iloc[3, df.columns.get_loc("smi_slow")] = 0
        # W%R cross up at bar 10 (way outside window of 2)
        df.iloc[9, df.columns.get_loc("williams_r")] = -85
        df.iloc[10, df.columns.get_loc("williams_r")] = -75

        signals = generate_signals(df, base_config)
        assert signals.iloc[10] == 0

    def test_vwap_filter_blocks_long(self, base_config):
        """Long signal is blocked on every bar where close < VWAP, not just bar 5.

        This sets up TWO valid long triggers (bars 5 and 12) with VWAP always above
        close. Both must be suppressed to confirm the filter applies globally, not
        just at the specific bar checked in the original single-point assertion.
        """
        base_config["signals"]["vwap_filter"] = True
        df = _make_df_with_indicators(20)
        df["vwap_indicator"] = 405.0  # all closes are 400 → always below VWAP

        # First long trigger: WR cross at bar 4, SMI cross at bar 5
        df.iloc[4, df.columns.get_loc("smi_fast")]   = -5
        df.iloc[5, df.columns.get_loc("smi_fast")]   = 5
        df.iloc[4, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[5, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[4, df.columns.get_loc("williams_r")] = -85
        df.iloc[5, df.columns.get_loc("williams_r")] = -75

        # Second long trigger: WR cross at bar 11, SMI cross at bar 12
        df.iloc[11, df.columns.get_loc("smi_fast")]   = -5
        df.iloc[12, df.columns.get_loc("smi_fast")]   = 5
        df.iloc[11, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[12, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[11, df.columns.get_loc("williams_r")] = -85
        df.iloc[12, df.columns.get_loc("williams_r")] = -75

        signals = generate_signals(df, base_config)

        # Both triggers must be blocked — close (400) < vwap (405) on every bar
        assert signals.iloc[5] == 0,  "Bar 5 long trigger should be blocked by VWAP filter"
        assert signals.iloc[12] == 0, "Bar 12 long trigger should be blocked by VWAP filter"

        # Confirm no long signals fire anywhere in the dataset
        assert (signals == 1).sum() == 0, "VWAP filter should suppress all long signals"

    def test_vwap_filter_allows_long_above_vwap(self, base_config):
        """Long signal fires when close > VWAP (filter passes it through)."""
        base_config["signals"]["vwap_filter"] = True
        df = _make_df_with_indicators(20)
        df["vwap_indicator"] = 395.0  # price (400) is ABOVE VWAP → filter passes

        # Valid long trigger at bar 5
        df.iloc[4, df.columns.get_loc("smi_fast")]   = -5
        df.iloc[5, df.columns.get_loc("smi_fast")]   = 5
        df.iloc[4, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[5, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[4, df.columns.get_loc("williams_r")] = -85
        df.iloc[5, df.columns.get_loc("williams_r")] = -75

        signals = generate_signals(df, base_config)
        assert signals.iloc[5] == 1, "Bar 5 long trigger should NOT be blocked when close > VWAP"

    def test_vwap_filter_blocks_short_above_vwap(self, base_config):
        """Short signal blocked when close > VWAP (close=400, VWAP=395)."""
        base_config["signals"]["vwap_filter"] = True
        df = _make_df_with_indicators(20)
        df["vwap_indicator"] = 395.0  # close (400) > VWAP (395) → short blocked

        # Valid short trigger at bar 5: SMI cross down + W%R cross below -20
        df.iloc[4, df.columns.get_loc("smi_fast")]   = 5
        df.iloc[5, df.columns.get_loc("smi_fast")]   = -5
        df.iloc[4, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[5, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[4, df.columns.get_loc("williams_r")] = -15
        df.iloc[5, df.columns.get_loc("williams_r")] = -25
        for i in range(6, 10):
            df.iloc[i, df.columns.get_loc("smi_fast")] = -5
            df.iloc[i, df.columns.get_loc("smi_slow")] = 0

        signals = generate_signals(df, base_config)
        assert (signals == -1).sum() == 0, "VWAP filter should block all short signals when close > VWAP"

    def test_vwap_filter_allows_short_below_vwap(self, base_config):
        """Short signal fires when close < VWAP (close=400, VWAP=405)."""
        base_config["signals"]["vwap_filter"] = True
        df = _make_df_with_indicators(20)
        df["vwap_indicator"] = 405.0  # close (400) < VWAP (405) → short allowed

        # Valid short trigger at bar 5
        df.iloc[4, df.columns.get_loc("smi_fast")]   = 5
        df.iloc[5, df.columns.get_loc("smi_fast")]   = -5
        df.iloc[4, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[5, df.columns.get_loc("smi_slow")]   = 0
        df.iloc[4, df.columns.get_loc("williams_r")] = -15
        df.iloc[5, df.columns.get_loc("williams_r")] = -25
        for i in range(6, 10):
            df.iloc[i, df.columns.get_loc("smi_fast")] = -5
            df.iloc[i, df.columns.get_loc("smi_slow")] = 0

        signals = generate_signals(df, base_config)
        assert signals.iloc[5] == -1, "Short trigger should fire when close < VWAP"

    def test_vwap_filter_missing_column_raises(self, base_config):
        """vwap_filter=True without 'vwap_indicator' column raises a helpful ValueError."""
        base_config["signals"]["vwap_filter"] = True
        df = _make_df_with_indicators(20)
        # No vwap_indicator column — should raise with a descriptive message
        with pytest.raises(ValueError, match="vwap_indicator"):
            generate_signals(df, base_config)


# ---------------------------------------------------------------------------
# compute_indicators() tests
# ---------------------------------------------------------------------------

def _make_ohlcv(n=100):
    """Synthetic OHLCV DataFrame with enough bars for indicator warm-up."""
    np.random.seed(0)
    dates = pd.date_range("2025-01-02 09:30", periods=n, freq="5min", tz="America/New_York")
    close = 400 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": high, "low": low, "close": close,
        "volume": np.random.randint(100_000, 2_000_000, n).astype(float),
    }, index=dates)


class TestComputeIndicators:
    def test_indicator_columns_present(self, base_config):
        """smi_fast, smi_slow, and williams_r columns are added to the DataFrame."""
        df = compute_indicators(_make_ohlcv(), base_config)
        assert "smi_fast" in df.columns
        assert "smi_slow" in df.columns
        assert "williams_r" in df.columns

    def test_ohlcv_columns_preserved(self, base_config):
        """Original OHLCV columns are not dropped or modified."""
        df = compute_indicators(_make_ohlcv(), base_config)
        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns

    def test_no_all_nan_indicator(self, base_config):
        """No indicator column should be entirely NaN (warm-up should leave valid values)."""
        df = compute_indicators(_make_ohlcv(), base_config)
        for col in ("smi_fast", "smi_slow", "williams_r"):
            assert df[col].notna().any(), f"{col} is all-NaN"

    def test_vwap_absent_when_filter_off(self, base_config):
        """vwap_indicator should NOT be added when vwap_filter is False."""
        df = compute_indicators(_make_ohlcv(), base_config)
        assert "vwap_indicator" not in df.columns

    def test_vwap_present_when_filter_on(self, base_config):
        """vwap_indicator should be added when vwap_filter is True."""
        base_config["signals"]["vwap_filter"] = True
        df = compute_indicators(_make_ohlcv(), base_config)
        assert "vwap_indicator" in df.columns
        assert df["vwap_indicator"].notna().any()

    def test_smi_values_in_range(self, base_config):
        """SMI valid values should be roughly within [-100, 100]."""
        df = compute_indicators(_make_ohlcv(), base_config)
        valid = df["smi_fast"].dropna()
        assert valid.max() <= 110
        assert valid.min() >= -110

    def test_williams_r_values_in_range(self, base_config):
        """Williams %R valid values must be in [-100, 0]."""
        df = compute_indicators(_make_ohlcv(), base_config)
        valid = df["williams_r"].dropna()
        assert valid.max() <= 0.01
        assert valid.min() >= -100.01

    def test_returns_copy_not_mutating_input(self, base_config):
        """compute_indicators() should not modify the original DataFrame."""
        original = _make_ohlcv()
        cols_before = set(original.columns)
        compute_indicators(original, base_config)
        assert set(original.columns) == cols_before


def _armed_config(pair_mode="indicator_1_then_indicator_2", armed_mode=True, sync_window=5):
    return {
        "signals": {
            "smi_fast": {"period": 5, "smooth1": 8, "smooth2": 8},
            "smi_slow": {"period": 13, "smooth1": 8, "smooth2": 8},
            "williams_r": {"period": 13},
            "sync_window": sync_window,
            "pair_mode": pair_mode,
            "armed_mode": armed_mode,
            "vwap_filter": False,
        }
    }


def _setup_smi_cross_up(df, bar):
    """Set SMI fast crossing above slow at the given bar index."""
    df.iloc[bar - 1, df.columns.get_loc("smi_fast")] = -5
    df.iloc[bar, df.columns.get_loc("smi_fast")] = 5
    df.iloc[bar - 1, df.columns.get_loc("smi_slow")] = 0
    df.iloc[bar, df.columns.get_loc("smi_slow")] = 0


def _setup_wr_cross_up(df, bar):
    """Set W%R crossing above -80 at the given bar index."""
    df.iloc[bar - 1, df.columns.get_loc("williams_r")] = -85
    df.iloc[bar, df.columns.get_loc("williams_r")] = -75


class TestArmedMode:
    def test_armed_mode_disarms_after_fire(self):
        """Arm at bar 5 (SMI), fire at bar 7 (W%R), second W%R at bar 8 → no signal."""
        df = _make_df_with_indicators(30)
        config = _armed_config("indicator_1_then_indicator_2", armed_mode=True, sync_window=5)

        # SMI cross up at bar 5 (arms)
        _setup_smi_cross_up(df, 5)
        # W%R cross up at bar 7 (fires + disarms)
        _setup_wr_cross_up(df, 7)
        # W%R cross up again at bar 8 (should NOT fire — disarmed)
        _setup_wr_cross_up(df, 9)

        signals = generate_signals(df, config)
        assert signals.iloc[7] == 1, "First fire should produce a signal"
        assert signals.iloc[9] == 0, "Second fire should NOT produce a signal (disarmed)"

    def test_armed_mode_rearms(self):
        """Arm at bar 5, fire at bar 7, re-arm at bar 12, fire at bar 14 → two signals."""
        df = _make_df_with_indicators(30)
        config = _armed_config("indicator_1_then_indicator_2", armed_mode=True, sync_window=5)

        # First arm + fire
        _setup_smi_cross_up(df, 5)
        _setup_wr_cross_up(df, 7)
        # Re-arm + fire
        _setup_smi_cross_up(df, 12)
        _setup_wr_cross_up(df, 14)

        signals = generate_signals(df, config)
        assert signals.iloc[7] == 1, "First signal should fire"
        assert signals.iloc[14] == 1, "Second signal should fire after re-arm"

    def test_armed_mode_window_expiry(self):
        """Arm at bar 3, fire at bar 15 (outside window=5) → no signal."""
        df = _make_df_with_indicators(30)
        config = _armed_config("indicator_1_then_indicator_2", armed_mode=True, sync_window=5)

        _setup_smi_cross_up(df, 3)
        _setup_wr_cross_up(df, 15)

        signals = generate_signals(df, config)
        assert signals.iloc[15] == 0, "Fire outside window should not produce a signal"

    def test_non_armed_fires_multiple(self):
        """Same setup as disarm test but armed_mode=false → both W%R crosses fire."""
        df = _make_df_with_indicators(30)
        config = _armed_config("indicator_1_then_indicator_2", armed_mode=False, sync_window=5)

        # SMI cross up at bar 5
        _setup_smi_cross_up(df, 5)
        # Keep SMI fast > slow so the window stays active
        for i in range(6, 12):
            df.iloc[i, df.columns.get_loc("smi_fast")] = 5
            df.iloc[i, df.columns.get_loc("smi_slow")] = 0
        # W%R cross up at bar 7
        _setup_wr_cross_up(df, 7)
        # W%R cross up at bar 9
        _setup_wr_cross_up(df, 9)

        signals = generate_signals(df, config)
        assert signals.iloc[7] == 1, "First fire should produce a signal"
        assert signals.iloc[9] == 1, "Second fire should also produce a signal (non-armed)"
