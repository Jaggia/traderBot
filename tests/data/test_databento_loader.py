"""Tests for databento_loader cache date range validation (BUG-017)."""

import os
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime

import pandas as pd
import pytest

from src.data.databento_loader import download_databento_equities


def _make_sample_csv(path: str, start: str, end: str, n_bars: int = 10) -> None:
    """Write a CSV that mimics what download_databento_equities produces.

    The function writes with `df.to_csv(path)` where the index is a
    tz-aware DatetimeIndex named "timestamp".  When read back with
    parse_dates=["timestamp"], index_col="timestamp" the index should
    be parsed as datetime.
    """
    idx = pd.date_range(start, end, periods=n_bars, tz="America/New_York")
    df = pd.DataFrame({
        "open": [100.0] * n_bars,
        "high": [101.0] * n_bars,
        "low": [99.0] * n_bars,
        "close": [100.5] * n_bars,
        "volume": [1000] * n_bars,
    }, index=idx)
    df.index.name = "timestamp"
    df.to_csv(path)


class TestCacheDateRangeValidation:
    """BUG-017: cache must validate date range, not just file existence."""

    def test_cache_hit_when_date_range_fully_covered(self, tmp_path):
        """Cached file covering the full requested range should be reused."""
        cache_dir = str(tmp_path)
        _make_sample_csv(
            os.path.join(cache_dir, "SYMBOL_1min_2025-01-01_to_2025-01-31.csv"),
            start="2025-01-01", end="2025-01-31", n_bars=20,
        )

        result = download_databento_equities(
            symbol="SYMBOL", start="2025-01-01", end="2025-01-31", cache_dir=cache_dir,
        )

        assert result.endswith("SYMBOL_1min_2025-01-01_to_2025-01-31.csv")

    def test_cache_invalidated_when_date_range_too_short(self, tmp_path):
        """Cached file with a shorter date range than requested must NOT be reused.

        This is the core BUG-017 regression test: previously the loader
        only checked that the file existed and was non-empty, so a stale /
        incomplete cache could be returned for any wider request.
        """
        cache_dir = str(tmp_path)
        # Cached file only covers Jan 1–10
        _make_sample_csv(
            os.path.join(cache_dir, "SYMBOL_1min_2025-01-01_to_2025-01-31.csv"),
            start="2025-01-01", end="2025-01-10", n_bars=10,
        )

        # Request Jan 1–31 — the cache is insufficient
        with patch("src.data.databento_loader.db.Historical") as mock_hist_cls:
            mock_client = MagicMock()
            mock_hist_cls.return_value = mock_client

            mock_data = MagicMock()
            # When the loader calls mock_data.to_csv(raw_path), write a proper CSV
            raw_written = {}
            def _write_raw(raw_path):
                _make_sample_csv(raw_path, "2025-01-01", "2025-01-31", 20)
                raw_written[raw_path] = True
            mock_data.to_csv.side_effect = _write_raw
            mock_client.timeseries.get_range.return_value = mock_data

            with patch.dict(os.environ, {"DATA_BENTO_PW": "fake-key"}):
                download_databento_equities(
                    symbol="SYMBOL", start="2025-01-01", end="2025-01-31", cache_dir=cache_dir,
                )

            # The API must have been called — cache was rejected
            mock_client.timeseries.get_range.assert_called_once()

    def test_cache_invalidated_when_start_date_later(self, tmp_path):
        """Cached file starting after the requested start must NOT be reused."""
        cache_dir = str(tmp_path)
        # Cached file covers Jan 15–31
        _make_sample_csv(
            os.path.join(cache_dir, "SYMBOL_1min_2025-01-01_to_2025-01-31.csv"),
            start="2025-01-15", end="2025-01-31", n_bars=15,
        )

        with patch("src.data.databento_loader.db.Historical") as mock_hist_cls:
            mock_client = MagicMock()
            mock_hist_cls.return_value = mock_client
            mock_data = MagicMock()
            mock_data.to_csv.side_effect = lambda p: _make_sample_csv(p, "2025-01-01", "2025-01-31", 20)
            mock_client.timeseries.get_range.return_value = mock_data

            with patch.dict(os.environ, {"DATA_BENTO_PW": "fake-key"}):
                download_databento_equities(
                    symbol="SYMBOL", start="2025-01-01", end="2025-01-31", cache_dir=cache_dir,
                )

            mock_client.timeseries.get_range.assert_called_once()

    def test_cache_used_when_range_wider_than_requested(self, tmp_path):
        """Cached file with a wider date range than requested SHOULD be reused."""
        cache_dir = str(tmp_path)
        # Cached file covers Jan 1 – Feb 28 (wider than needed)
        _make_sample_csv(
            os.path.join(cache_dir, "SYMBOL_1min_2025-01-01_to_2025-01-31.csv"),
            start="2025-01-01", end="2025-02-28", n_bars=40,
        )

        result = download_databento_equities(
            symbol="SYMBOL", start="2025-01-01", end="2025-01-31", cache_dir=cache_dir,
        )

        assert result.endswith("SYMBOL_1min_2025-01-01_to_2025-01-31.csv")

    def test_empty_cache_triggers_download(self, tmp_path):
        """An existing but empty cache file must trigger a re-download."""
        cache_dir = str(tmp_path)
        cache_path = os.path.join(cache_dir, "SYMBOL_1min_2025-01-01_to_2025-01-31.csv")
        # Write empty CSV with proper header
        pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                     index=pd.DatetimeIndex([], name="timestamp")).to_csv(cache_path)

        with patch("src.data.databento_loader.db.Historical") as mock_hist_cls:
            mock_client = MagicMock()
            mock_hist_cls.return_value = mock_client
            mock_data = MagicMock()
            mock_data.to_csv.side_effect = lambda p: _make_sample_csv(p, "2025-01-01", "2025-01-31", 20)
            mock_client.timeseries.get_range.return_value = mock_data

            with patch.dict(os.environ, {"DATA_BENTO_PW": "fake-key"}):
                download_databento_equities(
                    symbol="SYMBOL", start="2025-01-01", end="2025-01-31", cache_dir=cache_dir,
                )

            mock_client.timeseries.get_range.assert_called_once()

    def test_corrupt_cache_triggers_download(self, tmp_path):
        """A corrupt/unreadable cache file must trigger a re-download."""
        cache_dir = str(tmp_path)
        cache_path = os.path.join(cache_dir, "SYMBOL_1min_2025-01-01_to_2025-01-31.csv")
        with open(cache_path, "w") as f:
            f.write("this is not a valid csv!!!\n\x00\x00\x00")

        with patch("src.data.databento_loader.db.Historical") as mock_hist_cls:
            mock_client = MagicMock()
            mock_hist_cls.return_value = mock_client
            mock_data = MagicMock()
            mock_data.to_csv.side_effect = lambda p: _make_sample_csv(p, "2025-01-01", "2025-01-31", 20)
            mock_client.timeseries.get_range.return_value = mock_data

            with patch.dict(os.environ, {"DATA_BENTO_PW": "fake-key"}):
                download_databento_equities(
                    symbol="SYMBOL", start="2025-01-01", end="2025-01-31", cache_dir=cache_dir,
                )

            mock_client.timeseries.get_range.assert_called_once()
