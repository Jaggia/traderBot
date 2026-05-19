"""Pytest bootstrap shared by the repo's test suite."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

import pandas as pd

from src.signals.strategy import SignalStrategy


class MockStrategy(SignalStrategy):
    """Shared test strategy that returns pre-built DataFrames and signals."""

    def __init__(self, df: pd.DataFrame, signals: pd.Series):
        self._df = df
        self._signals = signals

    def compute_indicators(self, df, config):
        return self._df

    def generate_signals(self, df, config):
        return self._signals
