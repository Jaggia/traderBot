"""Compatibility wrapper for the generic non-EMA indicator-pair pipeline."""

from src.signals.indicator_pair_pipeline import compute_indicators, generate_signals

__all__ = ["compute_indicators", "generate_signals"]
