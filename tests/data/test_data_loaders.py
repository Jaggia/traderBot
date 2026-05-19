"""
Tests for the three data loaders:
  - src/data/alpaca_loader.py   → load_cached_csvs()
  - src/data/tradingview_loader.py → load_tradingview_csv()
  - src/data/databento_loader.py  → load_databento_equities() (equities path only)

Also covers:
  - src/data/databento_loader.py  → DatabentoOptionsLoader (cache hit/miss, retries)
  - src/data/alpaca_loader.py     → _needs_update(), download_bars(), update_to_present()

All tests use synthetic CSV fixtures built via the tmp_path pytest fixture.
No real data files or external API calls are made.

Coverage:
  - Each loader returns a DataFrame with open, high, low, close, volume columns
  - The index is a DatetimeIndex with America/New_York timezone
  - TradingView loader converts PST timestamps to EST
  - Date-range filtering works correctly (start / end bounds)
  - FileNotFoundError is raised when no data is found
  - Duplicates in the source CSVs are dropped (keep first)
"""
import os
import textwrap
from datetime import datetime

import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from src.data.alpaca_loader import load_cached_csvs, download_bars, _needs_update, update_to_present
from src.data.tradingview_loader import load_tradingview_csv
from src.data.databento_loader import load_databento_equities, DatabentoOptionsLoader


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

OHLCV_COLS = {"open", "high", "low", "close", "volume"}


def _assert_ohlcv(df: pd.DataFrame) -> None:
    """Assert that df has the required OHLCV columns."""
    missing = OHLCV_COLS - set(df.columns)
    assert not missing, f"Missing OHLCV columns: {missing}"


def _assert_est_index(df: pd.DataFrame) -> None:
    """Assert that the index is a tz-aware DatetimeIndex in America/New_York."""
    assert isinstance(df.index, pd.DatetimeIndex), "Index is not a DatetimeIndex"
    assert df.index.tz is not None, "Index has no timezone"
    # Both 'America/New_York' and pytz variants resolve to the same zone
    assert str(df.index.tz) in ("America/New_York", "US/Eastern"), (
        f"Unexpected timezone: {df.index.tz}"
    )


# ---------------------------------------------------------------------------
# Alpaca loader — load_cached_csvs()
# ---------------------------------------------------------------------------

def _write_alpaca_csv(path: str, rows: list[dict]) -> None:
    """Write a minimal Alpaca-style CSV (UTC timestamps, OHLCV columns)."""
    df = pd.DataFrame(rows)
    # Alpaca CSVs store timestamps as UTC-aware ISO strings
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize("UTC").astype(str)
    df.to_csv(path, index=False)


class TestAlpacaLoadCachedCsvs:
    """Tests for alpaca_loader.load_cached_csvs()."""

    def test_returns_ohlcv_columns(self, tmp_path):
        """Loader returns a DataFrame containing all five OHLCV columns."""
        csv_path = tmp_path / "SYMBOL_5min_202501.csv"
        _write_alpaca_csv(str(csv_path), [
            {"timestamp": "2025-01-02 14:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path))
        _assert_ohlcv(df)

    def test_index_is_est_datetime(self, tmp_path):
        """Index is a DatetimeIndex with America/New_York timezone."""
        csv_path = tmp_path / "SYMBOL_5min_202501.csv"
        _write_alpaca_csv(str(csv_path), [
            {"timestamp": "2025-01-02 14:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path))
        _assert_est_index(df)

    def test_utc_timestamps_converted_to_est(self, tmp_path):
        """A UTC bar at 19:30 UTC (14:30 EST) appears as 14:30 in the index."""
        csv_path = tmp_path / "SYMBOL_5min_202501.csv"
        _write_alpaca_csv(str(csv_path), [
            {"timestamp": "2025-01-02 19:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path))
        ts = df.index[0]
        assert (ts.hour, ts.minute) == (14, 30)

    def test_multiple_files_concatenated_and_sorted(self, tmp_path):
        """Bars from two monthly CSVs are concatenated and sorted by time."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_alpaca_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 14:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
        ])
        _write_alpaca_csv(str(year_dir / "SYMBOL_5min_202502.csv"), [
            {"timestamp": "2025-02-03 14:35:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path))
        assert len(df) == 2
        assert df.index.is_monotonic_increasing

    def test_duplicates_dropped(self, tmp_path):
        """Duplicate timestamps in the CSV appear only once in the output."""
        csv_path = tmp_path / "SYMBOL_5min_202501.csv"
        _write_alpaca_csv(str(csv_path), [
            {"timestamp": "2025-01-02 14:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
            {"timestamp": "2025-01-02 14:30:00", "open": 492.0, "high": 493.0,
             "low": 491.0, "close": 492.5, "volume": 2000},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path))
        assert len(df) == 1
        assert df["open"].iloc[0] == pytest.approx(492.0)  # last row wins (keep="last")

    def test_start_filter(self, tmp_path):
        """start filters at monthly filename level: files before the start month are excluded.

        Two monthly files: 202501 and 202502. start="2025-02-01" → only 202502 loaded.
        """
        _write_alpaca_csv(str(tmp_path / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 14:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
        ])
        _write_alpaca_csv(str(tmp_path / "SYMBOL_5min_202502.csv"), [
            {"timestamp": "2025-02-03 14:30:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path), start="2025-02-01")
        assert len(df) == 1
        assert df.index[0].month == 2

    def test_end_filter(self, tmp_path):
        """end filters at monthly filename level: files after the end month are excluded.

        Two monthly files: 202501 and 202502. end="2025-01-31" → only 202501 loaded.
        """
        _write_alpaca_csv(str(tmp_path / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 14:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
        ])
        _write_alpaca_csv(str(tmp_path / "SYMBOL_5min_202502.csv"), [
            {"timestamp": "2025-02-03 14:30:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path), end="2025-01-31")
        assert len(df) == 1
        assert df.index[0].month == 1

    def test_row_level_start_filter(self, tmp_path):
        """Row-level start filtering: rows before start date are excluded even within a loaded month.

        One monthly file for Nov 2025 with rows on Nov 1, 10, 15, and 20.
        Requesting start='2025-11-15' must return only rows from Nov 15 onward.
        """
        csv_path = tmp_path / "SYMBOL_5min_202511.csv"
        _write_alpaca_csv(str(csv_path), [
            {"timestamp": "2025-11-01 14:30:00", "open": 480.0, "high": 481.0,
             "low": 479.0, "close": 480.5, "volume": 1000},
            {"timestamp": "2025-11-10 14:30:00", "open": 482.0, "high": 483.0,
             "low": 481.0, "close": 482.5, "volume": 1100},
            {"timestamp": "2025-11-15 14:30:00", "open": 484.0, "high": 485.0,
             "low": 483.0, "close": 484.5, "volume": 1200},
            {"timestamp": "2025-11-20 14:30:00", "open": 486.0, "high": 487.0,
             "low": 485.0, "close": 486.5, "volume": 1300},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path), start="2025-11-15")
        assert len(df) == 2, f"Expected 2 rows (Nov 15 and 20), got {len(df)}"
        assert df.index[0].day == 15
        assert df.index[1].day == 20

    def test_row_level_end_filter(self, tmp_path):
        """Row-level end filtering: rows after end date are excluded even within a loaded month."""
        csv_path = tmp_path / "SYMBOL_5min_202511.csv"
        _write_alpaca_csv(str(csv_path), [
            {"timestamp": "2025-11-01 14:30:00", "open": 480.0, "high": 481.0,
             "low": 479.0, "close": 480.5, "volume": 1000},
            {"timestamp": "2025-11-10 14:30:00", "open": 482.0, "high": 483.0,
             "low": 481.0, "close": 482.5, "volume": 1100},
            {"timestamp": "2025-11-15 14:30:00", "open": 484.0, "high": 485.0,
             "low": 483.0, "close": 484.5, "volume": 1200},
            {"timestamp": "2025-11-20 14:30:00", "open": 486.0, "high": 487.0,
             "low": 485.0, "close": 486.5, "volume": 1300},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path), end="2025-11-10")
        assert len(df) == 2, f"Expected 2 rows (Nov 1 and 10), got {len(df)}"
        assert df.index[0].day == 1
        assert df.index[1].day == 10

    def test_file_not_found_raises(self, tmp_path):
        """FileNotFoundError is raised when the directory contains no CSVs."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_cached_csvs(base_dir=str(empty_dir))

    def test_ohlcv_values_preserved(self, tmp_path):
        """OHLCV numeric values survive the load round-trip."""
        csv_path = tmp_path / "SYMBOL_5min_202501.csv"
        _write_alpaca_csv(str(csv_path), [
            {"timestamp": "2025-01-02 14:30:00", "open": 490.12, "high": 491.34,
             "low": 489.56, "close": 490.78, "volume": 12345},
        ])
        df = load_cached_csvs(base_dir=str(tmp_path))
        assert df["open"].iloc[0] == pytest.approx(490.12)
        assert df["high"].iloc[0] == pytest.approx(491.34)
        assert df["low"].iloc[0] == pytest.approx(489.56)
        assert df["close"].iloc[0] == pytest.approx(490.78)
        assert df["volume"].iloc[0] == pytest.approx(12345.0)


# ---------------------------------------------------------------------------
# TradingView loader — load_tradingview_csv()
# ---------------------------------------------------------------------------

def _write_tv_csv(path: str, rows: list[dict]) -> None:
    """Write a TradingView-style CSV (naive PST timestamps, datetime column)."""
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)


class TestTradingViewLoader:
    """Tests for tradingview_loader.load_tradingview_csv()."""

    def test_returns_ohlcv_columns(self, tmp_path):
        """Loader returns a DataFrame containing all five OHLCV columns."""
        csv_path = tmp_path / "tradingview_exact.csv"
        _write_tv_csv(str(csv_path), [
            {"datetime": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_tradingview_csv(path=str(csv_path))
        _assert_ohlcv(df)

    def test_index_is_est_datetime(self, tmp_path):
        """Index is a DatetimeIndex with America/New_York timezone."""
        csv_path = tmp_path / "tradingview_exact.csv"
        _write_tv_csv(str(csv_path), [
            {"datetime": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_tradingview_csv(path=str(csv_path))
        _assert_est_index(df)

    def test_pst_converted_to_est(self, tmp_path):
        """A naive PST timestamp at 09:30 appears as 12:30 EST in the output.

        America/Los_Angeles is UTC-8 in winter, America/New_York is UTC-5,
        so the EST offset is +3 hours.
        """
        csv_path = tmp_path / "tradingview_exact.csv"
        _write_tv_csv(str(csv_path), [
            {"datetime": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_tradingview_csv(path=str(csv_path))
        ts = df.index[0]
        # PST (UTC-8) + 3h = EST (UTC-5) → 09:30 PST = 12:30 EST
        assert (ts.hour, ts.minute) == (12, 30)

    def test_pst_to_est_summer_dst(self, tmp_path):
        """During DST: PDT (UTC-7) → EDT (UTC-4), offset is still +3 hours."""
        csv_path = tmp_path / "tradingview_exact.csv"
        # July 2025 — both coasts on summer time
        _write_tv_csv(str(csv_path), [
            {"datetime": "2025-07-01 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_tradingview_csv(path=str(csv_path))
        ts = df.index[0]
        # PDT (UTC-7) + 3h = EDT (UTC-4) → 09:30 PDT = 12:30 EDT
        assert (ts.hour, ts.minute) == (12, 30)

    def test_index_name_is_timestamp(self, tmp_path):
        """The index name is 'timestamp' after loading."""
        csv_path = tmp_path / "tradingview_exact.csv"
        _write_tv_csv(str(csv_path), [
            {"datetime": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_tradingview_csv(path=str(csv_path))
        assert df.index.name == "timestamp"

    def test_start_filter(self, tmp_path):
        """Bars before 'start' are excluded."""
        csv_path = tmp_path / "tradingview_exact.csv"
        _write_tv_csv(str(csv_path), [
            # PST 09:30 on Jan 2 → EST 12:30 Jan 2
            {"datetime": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
            # PST 09:30 on Jan 3 → EST 12:30 Jan 3
            {"datetime": "2025-01-03 09:30:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_tradingview_csv(path=str(csv_path), start="2025-01-03")
        assert len(df) == 1
        assert df.index[0].date() == pd.Timestamp("2025-01-03").date()

    def test_end_filter(self, tmp_path):
        """Bars after 'end' are excluded."""
        csv_path = tmp_path / "tradingview_exact.csv"
        _write_tv_csv(str(csv_path), [
            {"datetime": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
            {"datetime": "2025-01-03 09:30:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_tradingview_csv(path=str(csv_path), end="2025-01-02")
        assert len(df) == 1
        assert df.index[0].date() == pd.Timestamp("2025-01-02").date()

    def test_file_not_found_raises(self, tmp_path):
        """FileNotFoundError is raised when the file does not exist."""
        with pytest.raises(FileNotFoundError):
            load_tradingview_csv(path=str(tmp_path / "no_such_file.csv"))

    def test_ohlcv_values_preserved(self, tmp_path):
        """OHLCV numeric values survive the load round-trip."""
        csv_path = tmp_path / "tradingview_exact.csv"
        _write_tv_csv(str(csv_path), [
            {"datetime": "2025-01-02 09:30:00", "open": 490.12, "high": 491.34,
             "low": 489.56, "close": 490.78, "volume": 12345},
        ])
        df = load_tradingview_csv(path=str(csv_path))
        assert df["open"].iloc[0] == pytest.approx(490.12)
        assert df["high"].iloc[0] == pytest.approx(491.34)
        assert df["low"].iloc[0] == pytest.approx(489.56)
        assert df["close"].iloc[0] == pytest.approx(490.78)
        assert df["volume"].iloc[0] == pytest.approx(12345.0)

    def test_multiple_rows_all_converted(self, tmp_path):
        """All rows in the CSV are timezone-converted, not just the first."""
        csv_path = tmp_path / "tradingview_exact.csv"
        rows = [
            {"datetime": f"2025-01-0{d} 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000}
            for d in range(2, 5)  # Jan 2, 3, 4
        ]
        _write_tv_csv(str(csv_path), rows)
        df = load_tradingview_csv(path=str(csv_path))
        assert len(df) == 3
        for ts in df.index:
            # All should be 12:30 EST (09:30 PST + 3h)
            assert (ts.hour, ts.minute) == (12, 30)


# ---------------------------------------------------------------------------
# Databento equities loader — load_databento_equities()
# ---------------------------------------------------------------------------

def _write_databento_csv(path: str, rows: list[dict], tz_aware: bool = True) -> None:
    """Write a Databento-style aggregated equity CSV.

    The aggregated monthly CSVs produced by download_and_aggregate_databento.py
    store the index as EST-aware ISO timestamps and OHLCV columns.
    """
    df = pd.DataFrame(rows)
    if tz_aware:
        df["timestamp"] = (
            pd.to_datetime(df["timestamp"])
            .dt.tz_localize("America/New_York")
        )
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    df.to_csv(path)


class TestDatabentoLoadEquities:
    """Tests for databento_loader.load_databento_equities() (equities CSV path)."""

    def test_returns_ohlcv_columns(self, tmp_path):
        """Loader returns a DataFrame containing all five OHLCV columns."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_databento_equities(cache_dir=str(tmp_path))
        _assert_ohlcv(df)

    def test_index_is_est_datetime(self, tmp_path):
        """Index is a DatetimeIndex with America/New_York timezone."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_databento_equities(cache_dir=str(tmp_path))
        _assert_est_index(df)

    def test_multiple_files_concatenated_and_sorted(self, tmp_path):
        """Bars from two monthly CSVs are concatenated and sorted by time."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
        ])
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202502.csv"), [
            {"timestamp": "2025-02-03 09:35:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_databento_equities(cache_dir=str(tmp_path))
        assert len(df) == 2
        assert df.index.is_monotonic_increasing

    def test_duplicates_dropped(self, tmp_path):
        """Duplicate timestamps in source CSVs appear only once in the output."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
            {"timestamp": "2025-01-02 09:30:00", "open": 492.0, "high": 493.0,
             "low": 491.0, "close": 492.5, "volume": 2000},
        ])
        df = load_databento_equities(cache_dir=str(tmp_path))
        assert len(df) == 1
        assert df["open"].iloc[0] == pytest.approx(490.0)

    def test_start_filter(self, tmp_path):
        """Bars before 'start' are excluded."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
            {"timestamp": "2025-01-03 09:30:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_databento_equities(cache_dir=str(tmp_path), start="2025-01-03")
        assert len(df) == 1
        assert df.index[0].date() == pd.Timestamp("2025-01-03").date()

    def test_end_filter(self, tmp_path):
        """Bars after 'end' are excluded."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 1000},
            {"timestamp": "2025-01-03 09:30:00", "open": 495.0, "high": 496.0,
             "low": 494.0, "close": 495.5, "volume": 2000},
        ])
        df = load_databento_equities(cache_dir=str(tmp_path), end="2025-01-02")
        assert len(df) == 1
        assert df.index[0].date() == pd.Timestamp("2025-01-02").date()

    def test_file_not_found_raises(self, tmp_path):
        """FileNotFoundError is raised when no CSV files exist in cache_dir."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_databento_equities(cache_dir=str(empty_dir))

    def test_ohlcv_values_preserved(self, tmp_path):
        """OHLCV numeric values survive the load round-trip."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        _write_databento_csv(str(year_dir / "SYMBOL_5min_202501.csv"), [
            {"timestamp": "2025-01-02 09:30:00", "open": 490.12, "high": 491.34,
             "low": 489.56, "close": 490.78, "volume": 12345},
        ])
        df = load_databento_equities(cache_dir=str(tmp_path))
        assert df["open"].iloc[0] == pytest.approx(490.12)
        assert df["high"].iloc[0] == pytest.approx(491.34)
        assert df["low"].iloc[0] == pytest.approx(489.56)
        assert df["close"].iloc[0] == pytest.approx(490.78)
        assert df["volume"].iloc[0] == pytest.approx(12345.0)

    def test_naive_timestamps_localized_to_est(self, tmp_path):
        """CSVs with naive (tz-unaware) timestamps are localized to EST."""
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        # Write with tz_aware=False to simulate naive timestamps in the CSV
        _write_databento_csv(
            str(year_dir / "SYMBOL_5min_202501.csv"),
            [{"timestamp": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
              "low": 489.0, "close": 490.5, "volume": 10000}],
            tz_aware=False,
        )
        df = load_databento_equities(cache_dir=str(tmp_path))
        _assert_est_index(df)


# ---------------------------------------------------------------------------
# DatabentoOptionsLoader — cache hit/miss, retry, contract definition
# ---------------------------------------------------------------------------

def _write_options_cache_csv(path: str, timestamps: list) -> None:
    """Write an options cache CSV with a DatetimeIndex (as saved by load_option_bars)."""
    df = pd.DataFrame(
        {"open": [5.0] * len(timestamps), "close": [5.0] * len(timestamps)},
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )
    df.to_csv(path)


class TestDatabentoOptionsLoader:
    """Tests for DatabentoOptionsLoader.load_option_bars() and get_contract_definition()."""

    def _make_loader(self, mock_db, cache_dir: str) -> DatabentoOptionsLoader:
        """Instantiate loader with a mocked databento client."""
        mock_db.Historical.return_value = MagicMock()
        return DatabentoOptionsLoader(api_key="dummy_key", cache_dir=cache_dir)

    def test_cache_hit_skips_api_call(self, tmp_path):
        """Cache covering the requested window → no API call made."""
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir)
        symbol = "TEST_SYM"
        cache_path = os.path.join(cache_dir, f"{symbol}.csv")
        _write_options_cache_csv(cache_path, [
            "2025-01-02 09:30:00",
            "2025-01-02 16:00:00",
        ])

        with patch("src.data.databento_loader.db") as mock_db:
            loader = self._make_loader(mock_db, cache_dir)
            start = datetime(2025, 1, 2, 9, 30)
            end = datetime(2025, 1, 2, 16, 0)
            df = loader.load_option_bars(symbol, start, end)

        assert not df.empty
        loader.client.timeseries.get_range.assert_not_called()

    def test_cache_miss_downloads_and_saves(self, tmp_path):
        """No cache file → API called and result saved to cache path."""
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir)
        symbol = "MISS_SYM"

        mock_df = pd.DataFrame(
            {"open": [5.0], "close": [5.0]},
            index=pd.DatetimeIndex(["2025-01-02 09:30:00"], name="ts"),
        )

        with patch("src.data.databento_loader.db") as mock_db:
            loader = self._make_loader(mock_db, cache_dir)
            loader.client.timeseries.get_range.return_value.to_df.return_value = mock_df
            result = loader.load_option_bars(
                symbol, datetime(2025, 1, 2, 9, 30), datetime(2025, 1, 2, 16, 0)
            )

        assert not result.empty
        cache_path = os.path.join(cache_dir, f"{symbol}.csv")
        assert os.path.exists(cache_path)

    def test_retry_succeeds_on_third_attempt(self, tmp_path):
        """API fails twice then succeeds → data returned, no exception raised."""
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir)
        symbol = "RETRY_SYM"

        mock_df = pd.DataFrame(
            {"open": [5.0]},
            index=pd.DatetimeIndex(["2025-01-02 09:30:00"], name="ts"),
        )

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("transient API error")
            result = MagicMock()
            result.to_df.return_value = mock_df
            return result

        with (
            patch("src.data.databento_loader.db") as mock_db,
            patch("src.data.databento_loader.time.sleep"),
        ):
            loader = self._make_loader(mock_db, cache_dir)
            loader.client.timeseries.get_range.side_effect = side_effect
            result = loader.load_option_bars(
                symbol, datetime(2025, 1, 2), datetime(2025, 1, 2)
            )

        assert not result.empty
        assert call_count[0] == 3

    def test_retry_exhausted_raises(self, tmp_path):
        """API fails on all 3 attempts → exception propagated to caller."""
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir)

        with (
            patch("src.data.databento_loader.db") as mock_db,
            patch("src.data.databento_loader.time.sleep"),
        ):
            loader = self._make_loader(mock_db, cache_dir)
            loader.client.timeseries.get_range.side_effect = Exception("API down")
            with pytest.raises(Exception, match="API down"):
                loader.load_option_bars("FAIL", datetime(2025, 1, 2), datetime(2025, 1, 2))

    def test_get_contract_definition_returns_raw_symbol(self, tmp_path):
        """get_contract_definition returns the raw_symbol from the matching row."""
        mock_definition_df = pd.DataFrame({
            "strike_price": [450.0],
            "expiration": pd.to_datetime(["2025-01-10"]),
            "put_call": ["C"],
            "raw_symbol": ["SYMBOL   250110C00450000"],
        })

        with patch("src.data.databento_loader.db") as mock_db:
            loader = self._make_loader(mock_db, str(tmp_path))
            loader.client.timeseries.get_range.return_value.to_df.return_value = (
                mock_definition_df
            )
            result = loader.get_contract_definition(
                ts=datetime(2025, 1, 2, 9, 30),
                strike=450.0,
                expiry=datetime(2025, 1, 10),
                option_type="C",
            )

        assert result == "SYMBOL   250110C00450000"

    def test_get_contract_definition_raises_on_unexpected_put_call(self, tmp_path):
        """get_contract_definition raises ValueError when put_call normalizes to
        something other than 'C' or 'P' (e.g. Databento returns 'UNKNOWN')."""
        mock_definition_df = pd.DataFrame({
            "strike_price": [450.0],
            "expiration": pd.to_datetime(["2025-01-10"]),
            "put_call": ["UNKNOWN"],
            "raw_symbol": ["SYMBOL   250110C00450000"],
        })

        with patch("src.data.databento_loader.db") as mock_db:
            loader = self._make_loader(mock_db, str(tmp_path))
            loader.client.timeseries.get_range.return_value.to_df.return_value = (
                mock_definition_df
            )
            with pytest.raises(ValueError, match="Unexpected put_call values after normalization"):
                loader.get_contract_definition(
                    ts=datetime(2025, 1, 2, 9, 30),
                    strike=450.0,
                    expiry=datetime(2025, 1, 10),
                    option_type="C",
                )

    def test_get_contract_definition_raises_on_empty_put_call(self, tmp_path):
        """get_contract_definition raises ValueError when put_call normalizes to
        an empty string (e.g. Databento returns '')."""
        mock_definition_df = pd.DataFrame({
            "strike_price": [450.0],
            "expiration": pd.to_datetime(["2025-01-10"]),
            "put_call": [""],
            "raw_symbol": ["SYMBOL   250110C00450000"],
        })

        with patch("src.data.databento_loader.db") as mock_db:
            loader = self._make_loader(mock_db, str(tmp_path))
            loader.client.timeseries.get_range.return_value.to_df.return_value = (
                mock_definition_df
            )
            with pytest.raises(ValueError, match="Unexpected put_call values after normalization"):
                loader.get_contract_definition(
                    ts=datetime(2025, 1, 2, 9, 30),
                    strike=450.0,
                    expiry=datetime(2025, 1, 10),
                    option_type="C",
                )


# ---------------------------------------------------------------------------
# Alpaca loader — _needs_update(), download_bars(), update_to_present()
# ---------------------------------------------------------------------------

def _write_alpaca_month_csv(path: str, last_day: int, year: int, month: int) -> None:
    """Write a minimal monthly CSV for _needs_update testing."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = pd.Timestamp(f"{year}-{month:02d}-{last_day:02d} 14:30:00", tz="America/New_York")
    df = pd.DataFrame(
        {"open": [490.0], "close": [490.5]},
        index=pd.DatetimeIndex([ts], name="timestamp"),
    )
    df.to_csv(path)


class TestAlpacaDownloadFunctions:
    """Tests for _needs_update(), download_bars(), and update_to_present()."""

    # --- _needs_update ---

    def test_needs_update_missing_file_returns_true(self, tmp_path):
        """Returns True when the expected CSV does not exist."""
        assert _needs_update(str(tmp_path), "SYMBOL_5min", 2024, 1) is True

    def test_needs_update_complete_past_month_returns_false(self, tmp_path):
        """Returns False when the CSV covers a complete past month."""
        # Jan 2024: last_day=31, threshold=31-2=29; last_date.day=30 >= 29 → False
        folder = str(tmp_path)
        prefix = "SYMBOL_5min"
        year, month = 2024, 1
        path = os.path.join(folder, str(year), f"{prefix}_{year:04d}{month:02d}.csv")
        _write_alpaca_month_csv(path, last_day=30, year=year, month=month)
        assert _needs_update(folder, prefix, year, month) is False

    def test_needs_update_incomplete_past_month_returns_true(self, tmp_path):
        """Returns True when the CSV is missing most of a past month."""
        # Jan 2024: last_day=31, threshold=29; last_date.day=5 < 29 → True
        folder = str(tmp_path)
        prefix = "SYMBOL_5min"
        year, month = 2024, 1
        path = os.path.join(folder, str(year), f"{prefix}_{year:04d}{month:02d}.csv")
        _write_alpaca_month_csv(path, last_day=5, year=year, month=month)
        assert _needs_update(folder, prefix, year, month) is True

    def test_needs_update_wrong_month_in_csv_returns_true(self, tmp_path):
        """Returns True when the CSV file for Feb contains only Jan data.

        This can happen when UTC→EST conversion shifts a bar's date back by one
        day (e.g. a Feb-01 00:30 UTC bar becomes Jan-31 19:30 EST).  Without the
        year/month guard the old code compared last_date.day (31) against
        last_day_feb - 2 (26) and incorrectly concluded the file was complete.
        """
        folder = str(tmp_path)
        prefix = "SYMBOL_5min"
        year, month = 2025, 2  # We're checking February
        path = os.path.join(folder, str(year), f"{prefix}_{year:04d}{month:02d}.csv")
        # But the CSV's latest timestamp is still in January (wrong month)
        _write_alpaca_month_csv(path, last_day=31, year=2025, month=1)
        assert _needs_update(folder, prefix, year, month) is True

    # --- download_bars ---

    def test_download_bars_returns_ohlcv(self):
        """download_bars returns a DataFrame with OHLCV columns on success."""
        mock_df = pd.DataFrame(
            {"symbol": ["SYMBOL"], "open": [490.0], "high": [491.0],
             "low": [489.0], "close": [490.5], "volume": [10000]},
            index=pd.DatetimeIndex(
                [pd.Timestamp("2025-01-02 14:30:00", tz="UTC")], name="timestamp"
            ),
        )

        with (
            patch("src.data.alpaca_loader._get_client") as mock_get_client,
            patch("src.data.alpaca_loader.StockBarsRequest"),
        ):
            mock_get_client.return_value.get_stock_bars.return_value.df = mock_df
            result = download_bars("SYMBOL", datetime(2025, 1, 2), datetime(2025, 1, 3))

        assert result is not None
        _assert_ohlcv(result)

    def test_download_bars_filters_to_rth(self):
        """download_bars excludes pre-market and post-market bars."""
        mock_df = pd.DataFrame(
            {"symbol": ["SYMBOL", "SYMBOL", "SYMBOL"],
             "open": [490.0, 491.0, 492.0],
             "high": [490.5, 491.5, 492.5],
             "low": [489.5, 490.5, 491.5],
             "close": [490.2, 491.2, 492.2],
             "volume": [10000, 11000, 12000]},
            index=pd.DatetimeIndex([
                pd.Timestamp("2025-01-02 13:00:00", tz="UTC"),  # 08:00 EST — pre-market
                pd.Timestamp("2025-01-02 14:30:00", tz="UTC"),  # 09:30 EST — RTH
                pd.Timestamp("2025-01-02 21:05:00", tz="UTC"),  # 16:05 EST — post-market
            ], name="timestamp"),
        )

        with (
            patch("src.data.alpaca_loader._get_client") as mock_get_client,
            patch("src.data.alpaca_loader.StockBarsRequest"),
        ):
            mock_get_client.return_value.get_stock_bars.return_value.df = mock_df
            result = download_bars("SYMBOL", datetime(2025, 1, 2), datetime(2025, 1, 3))

        assert result is not None
        # Only 09:30 EST bar survives between_time("09:30", "16:00")
        assert len(result) == 1
        assert result.index[0].hour == 9
        assert result.index[0].minute == 30

    # --- update_to_present ---

    def test_update_to_present_calls_download_month(self):
        """update_to_present calls _download_month for each month×timeframe pair."""
        with patch("src.data.alpaca_loader._download_month") as mock_download:
            # Start from a fixed past month so we know at least 1 full month runs
            update_to_present(symbol="SYMBOL", start_year=2025, start_month=1)

        # 2 timeframes (1min, 5min) × N months — at minimum 2 calls
        assert mock_download.call_count >= 2

    def test_update_to_present_no_existing_files_uses_current_year(self):
        """With no existing files and no start args, begins from current year January."""
        with (
            patch("src.data.alpaca_loader.globmod.glob", return_value=[]),
            patch("src.data.alpaca_loader._download_month") as mock_download,
        ):
            update_to_present(symbol="SYMBOL")

        # Should call _download_month at least once (current month × 2 timeframes)
        assert mock_download.call_count >= 2


# ---------------------------------------------------------------------------
# TradingView loader — tz-aware timestamps (Bug #43)
# ---------------------------------------------------------------------------

class TestTradingViewLoaderTzAware:
    """Verify _parse_tv_csv handles CSVs with tz-aware datetime strings."""

    def _write_tz_aware_csv(self, path, rows):
        """Write a CSV where the datetime column already contains tz offset strings."""
        df = pd.DataFrame(rows)
        df.to_csv(str(path), index=False)

    def test_tz_aware_timestamps_do_not_raise(self, tmp_path):
        """CSV with tz-aware datetime strings (e.g. -08:00) must not raise TypeError."""
        csv_path = tmp_path / "tv_tz_aware.csv"
        self._write_tz_aware_csv(csv_path, [
            {"datetime": "2025-01-02 09:30:00-08:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        # Should not raise TypeError: Already tz-aware
        df = load_tradingview_csv(path=str(csv_path))
        assert len(df) == 1

    def test_tz_aware_timestamps_converted_to_est(self, tmp_path):
        """CSV with -08:00 offset (PST) is correctly converted to America/New_York."""
        csv_path = tmp_path / "tv_tz_aware_est.csv"
        self._write_tz_aware_csv(csv_path, [
            # 09:30 PST (UTC-8) = 12:30 EST (UTC-5)
            {"datetime": "2025-01-02 09:30:00-08:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        df = load_tradingview_csv(path=str(csv_path))
        ts = df.index[0]
        assert str(ts.tzinfo) == "America/New_York"
        assert (ts.hour, ts.minute) == (12, 30)

    def test_naive_and_tz_aware_both_produce_est(self, tmp_path):
        """Naive PST and tz-aware PST timestamps both produce EST results."""
        naive_path = tmp_path / "naive.csv"
        aware_path = tmp_path / "aware.csv"

        _write_tv_csv(str(naive_path), [
            {"datetime": "2025-01-02 09:30:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])
        self._write_tz_aware_csv(aware_path, [
            {"datetime": "2025-01-02 09:30:00-08:00", "open": 490.0, "high": 491.0,
             "low": 489.0, "close": 490.5, "volume": 10000},
        ])

        df_naive = load_tradingview_csv(path=str(naive_path))
        df_aware = load_tradingview_csv(path=str(aware_path))

        # Both should resolve to the same EST time
        assert (df_naive.index[0].hour, df_naive.index[0].minute) == (12, 30)
        assert (df_aware.index[0].hour, df_aware.index[0].minute) == (12, 30)


# ---------------------------------------------------------------------------
# Bug C-2: load_option_bars() fresh-download UTC → EST normalization
# ---------------------------------------------------------------------------

class TestLoadOptionBarsFreshDownloadTimezone:
    """Ensure load_option_bars() returns America/New_York index on first download (C-2).

    Before the fix, df_new was returned directly from data.to_df() which uses
    UTC, while the cache-read branch converted to America/New_York.  This caused
    a 4-5 hour mismatch between first and subsequent calls.
    """

    def _make_loader(self, mock_db, cache_dir: str) -> DatabentoOptionsLoader:
        mock_db.Historical.return_value = MagicMock()
        return DatabentoOptionsLoader(api_key="dummy_key", cache_dir=cache_dir)

    def test_fresh_download_index_is_est(self, tmp_path):
        """Fresh download (no cache) returns an America/New_York DatetimeIndex."""
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir)
        symbol = "FRESH_SYM"

        # Simulate Databento returning a UTC-indexed DataFrame (as the real client does)
        utc_ts = pd.Timestamp("2025-01-02 14:30:00", tz="UTC")
        mock_df = pd.DataFrame(
            {"open": [5.0], "close": [5.0]},
            index=pd.DatetimeIndex([utc_ts], name="ts"),
        )

        with patch("src.data.databento_loader.db") as mock_db:
            loader = self._make_loader(mock_db, cache_dir)
            loader.client.timeseries.get_range.return_value.to_df.return_value = mock_df
            result = loader.load_option_bars(
                symbol, datetime(2025, 1, 2, 9, 30), datetime(2025, 1, 2, 16, 0)
            )

        assert isinstance(result.index, pd.DatetimeIndex)
        assert result.index.tz is not None
        assert str(result.index.tz) in ("America/New_York", "US/Eastern"), (
            f"Expected America/New_York, got {result.index.tz}"
        )
        # 14:30 UTC = 09:30 EST
        assert result.index[0].hour == 9
        assert result.index[0].minute == 30

    def test_fresh_download_and_cache_read_return_same_timezone(self, tmp_path):
        """First download and cache re-read produce the same timezone."""
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir)
        symbol = "SAME_TZ_SYM"

        utc_ts = pd.Timestamp("2025-01-02 14:30:00", tz="UTC")
        mock_df = pd.DataFrame(
            {"open": [5.0], "close": [5.0]},
            index=pd.DatetimeIndex([utc_ts], name="ts"),
        )

        # First call — downloads and caches
        with patch("src.data.databento_loader.db") as mock_db:
            loader = self._make_loader(mock_db, cache_dir)
            loader.client.timeseries.get_range.return_value.to_df.return_value = mock_df
            result_first = loader.load_option_bars(
                symbol, datetime(2025, 1, 2, 9, 30), datetime(2025, 1, 2, 16, 0)
            )

        # Second call — served from cache
        with patch("src.data.databento_loader.db") as mock_db:
            loader2 = self._make_loader(mock_db, cache_dir)
            result_second = loader2.load_option_bars(
                symbol, datetime(2025, 1, 2, 9, 30), datetime(2025, 1, 2, 16, 0)
            )

        assert str(result_first.index.tz) == str(result_second.index.tz)
        assert result_first.index[0].hour == result_second.index[0].hour
        assert result_first.index[0].minute == result_second.index[0].minute


# ---------------------------------------------------------------------------
# Bug M-7: _warmup_start() end-of-month clamping
# ---------------------------------------------------------------------------

class TestWarmupStart:
    """Ensure _warmup_start() handles end-of-month dates correctly (M-7).

    The old implementation used manual month arithmetic and produced invalid
    dates like 2025-02-31.  The fix uses pd.DateOffset which clamps correctly.
    """

    def test_warmup_start_imports(self):
        """_warmup_start is importable from main_runner.base_runner."""
        from main_runner.base_runner import _warmup_start
        assert callable(_warmup_start)

    def test_normal_mid_month_date(self):
        """Standard case: 2025-06-15 minus 3 months → 2025-03-15."""
        from main_runner.base_runner import _warmup_start
        result = _warmup_start("2025-06-15", 3)
        assert result == "2025-03-15"

    def test_end_of_month_clamped(self):
        """2025-05-31 minus 3 months clamps to 2025-02-28 (not invalid 2025-02-31)."""
        from main_runner.base_runner import _warmup_start
        result = _warmup_start("2025-05-31", 3)
        assert result == "2025-02-28"

    def test_january_rolls_back_to_previous_year(self):
        """2025-01-15 minus 3 months → 2024-10-15 (crosses year boundary)."""
        from main_runner.base_runner import _warmup_start
        result = _warmup_start("2025-01-15", 3)
        assert result == "2024-10-15"

    def test_march_31_minus_1_month_clamps(self):
        """2025-03-31 minus 1 month clamps to 2025-02-28."""
        from main_runner.base_runner import _warmup_start
        result = _warmup_start("2025-03-31", 1)
        assert result == "2025-02-28"

    def test_returns_string(self):
        """Return value is always a YYYY-MM-DD string."""
        from main_runner.base_runner import _warmup_start
        result = _warmup_start("2025-06-01", 3)
        assert isinstance(result, str)
        # Validate it parses back cleanly
        pd.Timestamp(result)
