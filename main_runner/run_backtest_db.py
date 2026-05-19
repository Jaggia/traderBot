#!/usr/bin/env python3
"""SYMBOL Options & Equities Backtesting System — CLI Entry Point (Databento Data)."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logging_config import setup_logging
from main_runner.base_runner import BaseBacktestRunner

logger = logging.getLogger(__name__)


class DatabentoRunner(BaseBacktestRunner):
    source_name = "db"
    data_source = "databento"
    warmup_months = 3


if __name__ == "__main__":
    setup_logging()
    try:
        DatabentoRunner().run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error("%s", e, exc_info=True)
        sys.exit(1)
