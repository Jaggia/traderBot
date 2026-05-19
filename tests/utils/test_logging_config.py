"""
Tests for src/utils/logging_config.py — setup_logging().

Pure stdlib. Handler-list tests use patch.object to inject a fresh empty list so
pytest's LogCaptureHandlers (added by the log-capture plugin) don't interfere.
"""
import logging

import pytest
from unittest.mock import patch

from src.utils.logging_config import setup_logging


class TestSetupLogging:
    """Verifies that setup_logging() configures the root logger correctly."""

    def setup_method(self):
        """Save the root logger level so we can restore it after each test."""
        self._saved_level = logging.root.level

    def teardown_method(self):
        """Restore the root logger level to its pre-test value."""
        logging.root.setLevel(self._saved_level)

    def test_adds_handler(self):
        """setup_logging() adds a handler when root logger starts empty."""
        with patch.object(logging.root, "handlers", []):
            assert len(logging.root.handlers) == 0
            setup_logging()
            assert len(logging.root.handlers) >= 1

    def test_idempotent(self):
        """Calling setup_logging() twice does not add a second handler."""
        fresh_handlers: list = []
        with patch.object(logging.root, "handlers", fresh_handlers):
            setup_logging()
            n = len(fresh_handlers)
            setup_logging()
            assert len(fresh_handlers) == n

    def test_custom_level_applied(self):
        """setup_logging(logging.DEBUG) sets root logger level to DEBUG."""
        setup_logging(logging.DEBUG)
        assert logging.root.level == logging.DEBUG
