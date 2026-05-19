"""
Tests for the options pricing path:
  - src/options/strike_selector.py: build_occ_symbol(), round_to_strike(),
    get_target_expiry(), select_strike()
  - BacktestEngine._get_option_price(): market-data lookup and hard-failure branches
  - src/options/option_pricer.py: black_scholes_price() edge cases

greeks.py + option_pricer.py are already covered in test_greeks.py.
"""
import os
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tests.conftest import MockStrategy
from src.options.strike_selector import (
    _is_nyse_holiday,
    build_occ_symbol,
    get_target_expiry,
    round_to_strike,
    select_strike,
)
from src.backtest.engine import BacktestEngine
from src.options.option_pricer import black_scholes_price, implied_vol


# ---------------------------------------------------------------------------
# black_scholes_price edge cases (Bug #14)
# ---------------------------------------------------------------------------

class TestBlackScholesSigmaGuard:
    """Verify that sigma <= 0 returns intrinsic value without crashing."""

    def test_sigma_zero_call_itm(self):
        """sigma=0 for ITM call → intrinsic value S - K."""
        price = black_scholes_price(S=410.0, K=400.0, T=0.1, sigma=0.0, option_type="C")
        assert price == pytest.approx(10.0)

    def test_sigma_zero_call_otm(self):
        """sigma=0 for OTM call → intrinsic value 0."""
        price = black_scholes_price(S=390.0, K=400.0, T=0.1, sigma=0.0, option_type="C")
        assert price == pytest.approx(0.0)

    def test_sigma_zero_put_itm(self):
        """sigma=0 for ITM put → intrinsic value K - S."""
        price = black_scholes_price(S=390.0, K=400.0, T=0.1, sigma=0.0, option_type="P")
        assert price == pytest.approx(10.0)

    def test_sigma_zero_put_otm(self):
        """sigma=0 for OTM put → intrinsic value 0."""
        price = black_scholes_price(S=410.0, K=400.0, T=0.1, sigma=0.0, option_type="P")
        assert price == pytest.approx(0.0)

    def test_sigma_negative_call(self):
        """sigma=-0.1 for call → intrinsic value without crashing."""
        price = black_scholes_price(S=410.0, K=400.0, T=0.1, sigma=-0.1, option_type="C")
        assert price == pytest.approx(10.0)

    def test_sigma_negative_put(self):
        """sigma=-0.1 for put → intrinsic value without crashing."""
        price = black_scholes_price(S=390.0, K=400.0, T=0.1, sigma=-0.1, option_type="P")
        assert price == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# implied_vol edge cases (Bug #15 / Bug #16)
# ---------------------------------------------------------------------------

class TestImpliedVolBisectionFailure:
    """Bug #15: bisection that cannot bracket market_price must raise ValueError."""

    def test_raises_when_market_price_above_bs_range(self):
        """A market_price above the highest BS price (hi_vol=5.0) cannot be bracketed.

        With S=410, K=400, T=0.1 and an absurdly high market price (e.g. 500.0 —
        far above any realistic BS call value), the bisection lo/hi bracket check
        must detect the failure and raise ValueError rather than returning a bogus IV.
        """
        with pytest.raises(ValueError, match="implied_vol: market price"):
            implied_vol(
                market_price=500.0,  # unreachable by any vol in [0.01, 5.0]
                S=410.0, K=400.0, T=0.1,
                option_type="C",
            )

    def test_raises_when_market_price_below_bs_range_but_above_intrinsic(self):
        """A market_price that is above intrinsic but below the lo_vol BS price.

        At very low sigma the BS price can still be above some market prices (e.g.
        a deeply OTM option priced below the lo_vol model floor).  Force the bracket
        to fail by using an artificially high lo that exceeds the market price.
        """
        with pytest.raises(ValueError, match="implied_vol: market price"):
            implied_vol(
                market_price=500.0,
                S=410.0, K=400.0, T=0.1,
                option_type="C",
                lo=0.01, hi=5.0,
            )


class TestImpliedVolAtExpiry:
    """Bug #16: implied_vol with T<=0 must return None and emit a warning."""

    def test_returns_none_when_t_is_zero(self, caplog):
        """At T=0 the function must return None — Black-Scholes is undefined."""
        import logging
        with caplog.at_level(logging.WARNING, logger="src.options.option_pricer"):
            result = implied_vol(
                market_price=5.0, S=410.0, K=400.0, T=0.0, option_type="C"
            )
        assert result is None

    def test_warning_logged_when_t_is_zero(self, caplog):
        """A WARNING must be emitted so callers know Greeks will be unreliable."""
        import logging
        with caplog.at_level(logging.WARNING, logger="src.options.option_pricer"):
            implied_vol(market_price=5.0, S=410.0, K=400.0, T=0.0, option_type="C")
        assert any("T<=0" in record.message for record in caplog.records)

    def test_returns_none_when_t_is_negative(self, caplog):
        """Negative T (past expiry) must also return None with a warning."""
        import logging
        with caplog.at_level(logging.WARNING, logger="src.options.option_pricer"):
            result = implied_vol(
                market_price=5.0, S=410.0, K=400.0, T=-0.01, option_type="C"
            )
        assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(n=5, base_price=400.0):
    idx = pd.date_range("2025-01-02 09:30", periods=n, freq="5min", tz="America/New_York")
    return pd.DataFrame({
        "open": base_price, "high": base_price * 1.001,
        "low": base_price * 0.999, "close": base_price, "volume": 1_000_000,
    }, index=idx)


def _minimal_config():
    return {
        "strategy": {"trade_mode": "equities", "initial_capital": 100_000},
        "exits": {
            "profit_target_pct": 20.0, "stop_loss_pct": 20.0,
            "eod_close": False, "opposite_signal": False,
        },
        "position": {"sizing_mode": "fixed", "contracts_per_trade": 1, "max_concurrent_positions": 1},
        "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
    }


def _make_engine() -> BacktestEngine:
    """Build a minimal BacktestEngine with mock strategy (no real indicators)."""
    df = _make_bars()
    signals = pd.Series(0, index=df.index)
    return BacktestEngine(config=_minimal_config(), equity_data=df,
                          strategy=MockStrategy(df, signals))


def _make_options_engine(config_overrides: dict | None = None) -> BacktestEngine:
    """Build a minimal options-mode BacktestEngine with mock strategy."""
    df = _make_bars()
    signals = pd.Series(0, index=df.index)
    config = {
        "strategy": {"trade_mode": "options", "initial_capital": 100_000},
        "exits": {
            "profit_target_pct": 20.0,
            "stop_loss_pct": 20.0,
            "eod_close": False,
            "opposite_signal": False,
        },
        "position": {"sizing_mode": "fixed", "contracts_per_trade": 1, "max_concurrent_positions": 1},
        "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
        "signals": {
            "smi_fast": {"period": 5, "smooth1": 3, "smooth2": 3},
            "smi_slow": {"period": 10, "smooth1": 3, "smooth2": 3},
            "williams_r": {"period": 7},
            "sync_window": 3,
            "vwap_filter": False,
            "pair_mode": "either",
            "armed_mode": False,
        },
        "options": {"target_dte": 7, "strike_selection": "ATM", "sigma": 0.25},
        "data": {"options_dir": "data/DataBento/options/SYMBOL/1min"},
    }
    if config_overrides:
        for key, value in config_overrides.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
    return BacktestEngine(config=config, equity_data=df,
                          strategy=MockStrategy(df, signals))


def _options_config(selection: str, target_dte: int = 7) -> dict:
    return {"options": {"strike_selection": selection, "target_dte": target_dte, "sigma": 0.25}}


# ---------------------------------------------------------------------------
# build_occ_symbol
# ---------------------------------------------------------------------------

class TestBuildOccSymbol:
    def test_call_format(self):
        """Verify full OCC symbol format for a call."""
        sym = build_occ_symbol("SYMBOL", datetime(2026, 2, 21), "C", 451.0)
        assert sym == "SYMBOL   260221C00451000"

    def test_put_format(self):
        """Verify full OCC symbol format for a put."""
        sym = build_occ_symbol("SYMBOL", datetime(2026, 2, 21), "P", 450.0)
        assert sym == "SYMBOL   260221P00450000"

    def test_fractional_strike_encoded(self):
        """$450.50 → strike_int=450500 encoded in the symbol."""
        sym = build_occ_symbol("SYMBOL", datetime(2026, 2, 21), "C", 450.5)
        assert "00450500" in sym

    def test_root_padded_to_six_chars(self):
        """Root is left-justified and padded to 6 characters."""
        sym = build_occ_symbol("SYMBOL", datetime(2026, 2, 21), "C", 400.0)
        assert sym[:6] == "SYMBOL   "


# ---------------------------------------------------------------------------
# round_to_strike
# ---------------------------------------------------------------------------

class TestRoundToStrike:
    def test_rounds_up(self):
        assert round_to_strike(400.6) == 401.0

    def test_rounds_down(self):
        assert round_to_strike(400.4) == 400.0

    def test_already_on_tick(self):
        assert round_to_strike(400.0) == 400.0

    def test_half_rounds_to_nearest_even(self):
        """Python's round() uses banker's rounding; just assert result is on a $1 tick."""
        result = round_to_strike(400.5)
        assert result % 1.0 == 0.0


# ---------------------------------------------------------------------------
# get_target_expiry
# ---------------------------------------------------------------------------

class TestGetTargetExpiry:
    def test_target_already_friday_stays(self):
        """If target lands on a Friday (non-holiday), keep it."""
        # 2025-01-03 is Friday; +7 days = 2025-01-10 (Friday)
        result = get_target_expiry(datetime(2025, 1, 3), 7)
        assert result.weekday() == 4  # Friday
        assert result == datetime(2025, 1, 10, 16, 0, tzinfo=_NY)

    def test_target_on_wednesday_rolls_to_friday(self):
        """If target lands on Wednesday, advance to the following Friday."""
        # 2025-01-01 (Wednesday) +7 = 2025-01-08 (Wednesday) → 2025-01-10 (Friday)
        result = get_target_expiry(datetime(2025, 1, 1), 7)
        assert result.weekday() == 4
        assert result == datetime(2025, 1, 10, 16, 0, tzinfo=_NY)

    def test_good_friday_rolls_back_to_thursday(self):
        """Good Friday (NYSE holiday) should roll back to Thursday."""
        # Good Friday 2025 = April 18; start 2025-04-11 (Friday) +7 = Apr 18 → Apr 17
        result = get_target_expiry(datetime(2025, 4, 11), 7)
        assert result == datetime(2025, 4, 17, 16, 0, tzinfo=_NY)
        assert result.weekday() == 3  # Thursday

    def test_result_is_tz_aware_datetime(self):
        result = get_target_expiry(datetime(2025, 1, 3), 7)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_zero_dte_on_wednesday_returns_wednesday(self):
        """0-DTE: current_date is a Wednesday (trading day) → expiry is that same Wednesday at 16:00."""
        # 2025-01-08 is a Wednesday (not a holiday)
        result = get_target_expiry(datetime(2025, 1, 8), 0)
        assert result == datetime(2025, 1, 8, 16, 0, tzinfo=_NY)
        assert result.weekday() == 2  # Wednesday

    def test_zero_dte_on_saturday_advances_to_monday(self):
        """0-DTE: current_date is Saturday → expiry advances to next Monday (first trading day)."""
        # 2025-01-04 is a Saturday
        result = get_target_expiry(datetime(2025, 1, 4), 0)
        # Should advance past Saturday (5) and Sunday (6) to Monday (0)
        assert result.weekday() not in (5, 6)  # not a weekend
        assert result > datetime(2025, 1, 4, 16, 0, tzinfo=_NY)

    def test_zero_dte_on_friday_stays_friday(self):
        """0-DTE: current_date is Friday (non-holiday) → expiry is that same Friday at 16:00."""
        # 2025-01-03 is a Friday
        result = get_target_expiry(datetime(2025, 1, 3), 0)
        assert result == datetime(2025, 1, 3, 16, 0, tzinfo=_NY)
        assert result.weekday() == 4  # Friday


# ---------------------------------------------------------------------------
# select_strike
# ---------------------------------------------------------------------------

class TestSelectStrike:
    def _select(self, underlying: float, selection: str, option_type: str = "C") -> dict:
        config = _options_config(selection)
        return select_strike(underlying, datetime(2025, 1, 2, 10, 0), option_type, config)

    def test_atm_equals_rounded_price(self):
        result = self._select(400.3, "ATM")
        assert result["strike"] == 400.0

    def test_1_itm_call_one_below_atm(self):
        """1_ITM call: strike = ATM − 1 (lower strike = ITM for calls)."""
        result = self._select(400.0, "1_ITM", "C")
        assert result["strike"] == 399.0

    def test_1_otm_call_one_above_atm(self):
        """1_OTM call: strike = ATM + 1 (higher strike = OTM for calls)."""
        result = self._select(400.0, "1_OTM", "C")
        assert result["strike"] == 401.0

    def test_2_itm_call_two_below_atm(self):
        result = self._select(400.0, "2_ITM", "C")
        assert result["strike"] == 398.0

    def test_2_otm_call_two_above_atm(self):
        result = self._select(400.0, "2_OTM", "C")
        assert result["strike"] == 402.0

    def test_result_has_required_keys(self):
        result = self._select(400.0, "ATM")
        assert "strike" in result
        assert "expiry" in result
        assert "raw_symbol" in result

    def test_raw_symbol_is_occ_formatted(self):
        """raw_symbol starts with SYMBOL and embeds the correct option_type character."""
        result = self._select(400.0, "ATM", "C")
        assert result["raw_symbol"].startswith("SYMBOL")
        assert "C" in result["raw_symbol"]

    def test_target_delta_selects_near_atm(self):
        """target_delta=0.50 on a 30-DTE call should select a strike close to ATM."""
        config = {
            "options": {
                "strike_selection": "target_delta",
                "target_dte": 30,
                "target_delta": 0.50,
                "sigma": 0.25,
            }
        }
        result = select_strike(400.0, datetime(2025, 1, 2, 10, 0), "C", config)
        assert abs(result["strike"] - 400.0) <= 3.0

    def test_expiry_is_a_friday_or_thursday(self):
        """Expiry must be a Friday (or Thursday when Friday is a holiday)."""
        result = self._select(400.0, "ATM")
        assert result["expiry"].weekday() in (3, 4)  # Thursday or Friday

    # --- Put-side coverage (mirrors call tests above) ---

    def test_1_itm_put_one_above_atm(self):
        """1_ITM put: strike = ATM + 1 (higher strike = ITM for puts)."""
        result = self._select(400.0, "1_ITM", "P")
        assert result["strike"] == 401.0

    def test_1_otm_put_one_below_atm(self):
        """1_OTM put: strike = ATM − 1 (lower strike = OTM for puts)."""
        result = self._select(400.0, "1_OTM", "P")
        assert result["strike"] == 399.0

    def test_2_itm_put_two_above_atm(self):
        result = self._select(400.0, "2_ITM", "P")
        assert result["strike"] == 402.0

    def test_2_otm_put_two_below_atm(self):
        result = self._select(400.0, "2_OTM", "P")
        assert result["strike"] == 398.0


# ---------------------------------------------------------------------------
# BacktestEngine._get_option_price: branching
# ---------------------------------------------------------------------------

class TestGetOptionPrice:
    """Verify historical option pricing succeeds with data and fails hard without it."""

    def test_no_raw_symbol_raises(self):
        """raw_symbol=None must fail instead of fabricating a price."""
        engine = _make_engine()
        with pytest.raises(RuntimeError, match="no raw_symbol"):
            engine._get_option_price(
                400.0, 400.0, 7 / 365, "C", None, datetime(2025, 1, 2, 10, 0)
            )

    def test_no_options_loader_raises(self):
        """Calling option pricing without an options loader must fail immediately."""
        engine = _make_engine()
        with pytest.raises(RuntimeError, match="no options loader"):
            engine._get_option_price(
                400.0, 400.0, 7 / 365, "C", "SYMBOL   250110C00400000",
                datetime(2025, 1, 2, 10, 0)
            )

    def test_empty_market_data_raises(self):
        """Empty option data must fail instead of falling back to model pricing."""
        engine = _make_engine()
        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = pd.DataFrame()
        engine._options_loader = mock_loader

        with pytest.raises(RuntimeError, match="no market data returned"):
            engine._get_option_price(
                400.0, 400.0, 7 / 365, "C", "SYMBOL   250110C00400000",
                datetime(2025, 1, 2, 10, 0)
            )

    def test_market_data_returns_latest_bar_at_or_before_timestamp(self):
        """Historical option pricing must use the last known bar, never a future bar."""
        engine = _make_engine()

        market_df = pd.DataFrame(
            {"close": [7.25, 9.50]},
            index=pd.to_datetime(
                ["2025-01-02 10:00:00", "2025-01-02 10:03:00"]
            ).tz_localize("America/New_York"),
        )

        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = market_df
        engine._options_loader = mock_loader

        price = engine._get_option_price(
            400.0,
            400.0,
            7 / 365,
            "C",
            "SYMBOL   250110C00400000",
            pd.Timestamp("2025-01-02 10:02:00", tz="America/New_York"),
        )
        assert price == pytest.approx(7.25)

    def test_cache_only_mode_uses_cached_option_bars_without_api_key(self, tmp_path, monkeypatch):
        """A populated local option cache should be usable if a dummy API key is provided."""
        raw_symbol = "SYMBOL   250110C00400000"
        cache_path = tmp_path / f"{raw_symbol.replace(' ', '_')}.csv"

        cached = pd.DataFrame(
            {"close": [6.75]},
            index=pd.to_datetime(["2025-01-02 10:00:00"]).tz_localize("America/New_York"),
        )
        cached.to_csv(cache_path)

        # Provide a dummy API key to bypass the validation that prevents silent failures.
        monkeypatch.setenv("DATABENTO_API_KEY", "dummy-key-for-cache")

        engine = _make_options_engine({"data": {"options_dir": str(tmp_path)}})
        price = engine._get_option_price(
            400.0,
            400.0,
            7 / 365,
            "C",
            raw_symbol,
            pd.Timestamp("2025-01-02 10:00:00", tz="America/New_York"),
        )

        assert price == pytest.approx(6.75)

    def test_raises_when_no_prior_option_bar_exists(self):
        """If the earliest cached/loaded bar is in the future, pricing must fail."""
        engine = _make_engine()
        future_only = pd.DataFrame(
            {"close": [9.50]},
            index=pd.to_datetime(["2025-01-02 10:03:00"]).tz_localize("America/New_York"),
        )

        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = future_only
        engine._options_loader = mock_loader

        with pytest.raises(RuntimeError, match="no historical bar at or before"):
            engine._get_option_price(
                400.0,
                400.0,
                7 / 365,
                "C",
                "SYMBOL   250110C00400000",
                pd.Timestamp("2025-01-02 10:02:00", tz="America/New_York"),
            )

    def test_returns_none_and_warns_when_option_bar_is_stale(self, caplog):
        """Stale bars (gap > 25 min) must return None and log a WARNING instead of raising."""
        import logging
        engine = _make_engine()
        stale_df = pd.DataFrame(
            {"close": [7.25]},
            index=pd.to_datetime(["2025-01-02 09:00:00"]).tz_localize("America/New_York"),
        )

        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = stale_df
        engine._options_loader = mock_loader

        with caplog.at_level(logging.WARNING, logger="src.backtest.engine"):
            result = engine._get_option_price(
                400.0,
                400.0,
                7 / 365,
                "C",
                "SYMBOL   250110C00400000",
                pd.Timestamp("2025-01-02 10:02:00", tz="America/New_York"),
            )

        assert result is None, f"Expected None for stale price, got {result}"
        assert any("stale" in r.message.lower() for r in caplog.records), (
            "Expected a WARNING mentioning 'stale' in log records"
        )


# ---------------------------------------------------------------------------
# M-3: NYSE holiday observance rules (nearest_workday)
# ---------------------------------------------------------------------------

class TestNYSEHolidayObservance:
    """Verify that observed holiday dates are recognised as NYSE closures."""

    def test_new_years_observed_friday(self):
        """Jan 1 2022 is a Saturday → observed on Dec 31 2021 (Friday)."""
        assert _is_nyse_holiday(datetime(2021, 12, 31)) is True

    def test_christmas_observed_friday(self):
        """Dec 25 2021 is a Saturday → observed on Dec 24 2021 (Friday)."""
        assert _is_nyse_holiday(datetime(2021, 12, 24)) is True

    def test_independence_day_observed_monday(self):
        """Jul 4 2021 is a Sunday → observed on Jul 5 2021 (Monday)."""
        assert _is_nyse_holiday(datetime(2021, 7, 5)) is True


# ---------------------------------------------------------------------------
# M-6: 0-DTE expiry selection
# ---------------------------------------------------------------------------

class TestZeroDteExpiry:
    """Verify that target_dte=0 returns today (or next trading day), not the next Friday."""

    def test_zero_dte_returns_same_day_on_weekday(self):
        """0-DTE on a regular weekday (Monday) should return that same day at 16:00."""
        # Jan 5 2026 is a Monday
        result = get_target_expiry(datetime(2026, 1, 5), 0)
        assert result == datetime(2026, 1, 5, 16, 0, tzinfo=_NY)

    def test_zero_dte_advances_past_weekend(self):
        """0-DTE on a Saturday should advance to Monday at 16:00."""
        # Jan 3 2026 is a Saturday → next weekday is Jan 5 2026 (Monday)
        result = get_target_expiry(datetime(2026, 1, 3), 0)
        assert result == datetime(2026, 1, 5, 16, 0, tzinfo=_NY)

    def test_nonzero_dte_still_rolls_to_friday(self):
        """Non-zero DTE path is unchanged — result must be a Friday (or Thursday for holiday)."""
        # Jan 5 2026 (Monday) + 7 days = Jan 12 (Monday) → rolls to Friday Jan 9
        result = get_target_expiry(datetime(2026, 1, 5), 7)
        assert result.weekday() == 4  # Friday
