#!/usr/bin/env python3
"""SYMBOL Options & Equities Backtesting System — CLI Entry Point (TradingView Data)."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logging_config import setup_logging
from main_runner.base_runner import BaseBacktestRunner

logger = logging.getLogger(__name__)


class TradingViewRunner(BaseBacktestRunner):
    source_name = "tv"
    data_source = "tv"
    warmup_months = 0


if __name__ == "__main__":
    setup_logging()
    try:
        TradingViewRunner().run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error("%s", e, exc_info=True)
        sys.exit(1)
