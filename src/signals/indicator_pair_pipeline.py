"""Unified signal pipeline for both EMA and non-EMA strategies.

This module centralizes all signal composition behind one pipeline. It supports
four config styles:

1. Default ``signals`` SMI + Williams %R config (default ``signal_system=indicator_pair``)
2. New generic ``signals`` pair config (``indicator_1`` / ``indicator_2``)
3. Legacy ``armed_mode`` config (explicit arm/fire events; ``signal_system=armed_mode``)
4. EMA-233 intrabar-cross system (``signal_system=ema_233``)

EMA-233 is integrated via internal resampling and intrabar-cross detection,
preserving its unique requirement for 15-min resampling and price-hint levels.
"""
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.constants import WR_OVERBOUGHT, WR_OVERSOLD
from src.indicators import (
    compute_ema,
    compute_macd,
    compute_rsi,
    compute_smi,
    compute_stoch_rsi,
    compute_tsi,
    compute_vwap,
    compute_williams_r,
)
from src.data.aggregator import aggregate_to_Nmin
from src.signals.sequential_logic import (
    apply_sequential_chain,
    apply_sequential_logic,
    crossover,
    crossunder,
    series_crossover,
    series_crossunder,
    within_window,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventSpec:
    indicator: str
    column: str
    event_type: str
    threshold: float | None = None
    column_b: str | None = None


@dataclass(frozen=True)
class TriggerSpec:
    """One step in a trigger chain: an indicator and its long/short events."""
    indicator: str
    long: EventSpec
    short: EventSpec


@dataclass(frozen=True)
class PipelineConfig:
    config_section: dict
    trigger_specs: list[TriggerSpec]
    is_sequential: bool
    sync_window: int
    vwap_filter: bool
    bidirectional: bool = False  # "either" mode: try both orderings, OR results
    resample_tf: int | None = None
    entry_price_hint: bool = False


def intrabar_crossover(curr_val: pd.Series, threshold: pd.Series | float, prev_val: pd.Series) -> pd.Series:
    """True if current value (e.g. high) crosses above threshold after prior close was below."""
    return (curr_val > threshold) & (prev_val < threshold)


def intrabar_crossunder(curr_val: pd.Series, threshold: pd.Series | float, prev_val: pd.Series) -> pd.Series:
    """True if current value (e.g. low) crosses below threshold after prior close was above."""
    return (curr_val < threshold) & (prev_val > threshold)


_EVENT_DETECTORS = {
    "crossover": crossover,
    "crossunder": crossunder,
    "series_crossover": series_crossover,
    "series_crossunder": series_crossunder,
    "intrabar_crossover": intrabar_crossover,
    "intrabar_crossunder": intrabar_crossunder,
}


def _get_signal_system(config: dict) -> str:
    return config.get("strategy", {}).get("signal_system", "indicator_pair")


def _normalize_pipeline_config(config: dict) -> PipelineConfig:
    signal_system = _get_signal_system(config)
    sig_cfg = config.get("signals", {})

    if signal_system == "ema_233":
        return _normalize_ema_233_config(config)

    # New canonical path: triggers list or trigger_chain block in signals section
    if (
        signal_system in ("trigger_chain", "indicator_pair")
        or "triggers" in sig_cfg
        or "trigger_chain" in sig_cfg
    ):
        return _normalize_trigger_chain_config(sig_cfg)

    # Fallback to trigger_chain for any other signal_system if config is present
    return _normalize_trigger_chain_config(sig_cfg)


def _normalize_trigger_chain_config(sig_cfg: dict) -> PipelineConfig:
    """Build PipelineConfig from the new ``signals.triggers`` list.

    Each trigger entry is a dict with at minimum ``indicator``.  Events are
    derived from presets by default, but can be overridden per-trigger via
    ``event``, ``column``, ``column_b``, ``threshold``, ``threshold_short``.

    Supports optional nesting under a ``trigger_chain`` key.

    Example YAML::

        signals:
          trigger_chain:
            triggers:
              - indicator: rsi
              - indicator: macd
            sequential: true
            sync_window: 5
    """
    # Handle optional nesting under 'trigger_chain' key
    t_cfg = (
        sig_cfg.get("trigger_chain", sig_cfg)
        if isinstance(sig_cfg.get("trigger_chain"), dict)
        else sig_cfg
    )

    trigger_defs = t_cfg.get("triggers")
    if trigger_defs is None:
        # Fallback to sig_cfg directly
        trigger_defs = sig_cfg.get("triggers", [])

    if not trigger_defs:
        raise ValueError(
            "trigger_chain signal_system requires signals.triggers list "
            "or signals.trigger_chain.triggers"
        )

    specs: list[TriggerSpec] = []
    for tdef in trigger_defs:
        indicator = tdef.get("indicator")
        if not indicator:
            raise ValueError("Each trigger must have an 'indicator' key")

        if "event" in tdef or "column" in tdef:
            # Explicit event override
            col = tdef["column"]
            event = tdef["event"]
            threshold = tdef.get("threshold")
            threshold_short = tdef.get("threshold_short")
            col_b = tdef.get("column_b")

            if threshold_short is None and threshold is not None:
                if float(threshold) != 0.0:
                    raise ValueError(
                        f"threshold_short must be specified explicitly when threshold "
                        f"is non-zero (got threshold={threshold}). Auto-negation only "
                        f"works for zero-centered indicators."
                    )
                threshold_short = -float(threshold)
            if threshold is not None:
                threshold = float(threshold)
            if threshold_short is not None:
                threshold_short = float(threshold_short)

            long_evt = EventSpec(indicator, col, event, threshold, col_b)
            short_evt = EventSpec(
                indicator, col, _invert_event(event), threshold_short, col_b,
            )
        else:
            # Use preset
            long_evt, short_evt = _preset_events(indicator)

        specs.append(TriggerSpec(indicator=indicator, long=long_evt, short=short_evt))

    # Check for 'sequential' or legacy 'armed_mode' in config
    # Default is sequential=True (armed mode). Set sequential: false for
    # co-occurrence / windowed mode where triggers need not be ordered.
    is_seq = t_cfg.get("sequential")
    if is_seq is None:
        is_seq = t_cfg.get("armed_mode")
    if is_seq is None:
        is_seq = sig_cfg.get("sequential")
    if is_seq is None:
        is_seq = sig_cfg.get("armed_mode", True)

    return PipelineConfig(
        config_section=sig_cfg,
        trigger_specs=specs,
        is_sequential=bool(is_seq),
        sync_window=int(t_cfg.get("sync_window", sig_cfg.get("sync_window", 5))),
        vwap_filter=bool(t_cfg.get("vwap_filter", sig_cfg.get("vwap_filter", False))),
    )


def _normalize_ema_233_config(config: dict) -> PipelineConfig:
    ema_cfg = config.get("signals_ema", {})
    sig_cfg = config.get("signals", {})

    resample_target = int(ema_cfg.get("base_timeframe_min", 15))
    actual_resample = resample_target if resample_target != 5 else None

    return PipelineConfig(
        config_section=ema_cfg,
        trigger_specs=[
            TriggerSpec(
                indicator="ema",
                long=EventSpec("ema", "high", "intrabar_crossover", column_b="ema_233"),
                short=EventSpec("ema", "low", "intrabar_crossunder", column_b="ema_233"),
            ),
        ],
        is_sequential=False,
        sync_window=0,
        vwap_filter=bool(ema_cfg.get("vwap_filter", False)),
        resample_tf=actual_resample,
        entry_price_hint=True,
    )


def _invert_event(event_type: str) -> str:
    _INVERSION_MAP = {
        "crossover": "crossunder",
        "crossunder": "crossover",
        "series_crossover": "series_crossunder",
        "series_crossunder": "series_crossover",
        "intrabar_crossover": "intrabar_crossunder",
        "intrabar_crossunder": "intrabar_crossover",
    }
    if event_type not in _INVERSION_MAP:
        raise ValueError(
            f"Unknown event type {event_type!r}. Cannot invert. "
            f"Valid types: {', '.join(sorted(_INVERSION_MAP))}"
        )
    return _INVERSION_MAP[event_type]


def _preset_events(indicator: str) -> tuple[EventSpec, EventSpec]:
    indicator = indicator.lower()
    if indicator == "smi":
        return (
            EventSpec("smi", "smi_fast", "series_crossover", column_b="smi_slow"),
            EventSpec("smi", "smi_fast", "series_crossunder", column_b="smi_slow"),
        )
    if indicator == "williams_r":
        return (
            EventSpec("williams_r", "williams_r", "crossover", WR_OVERSOLD),
            EventSpec("williams_r", "williams_r", "crossunder", WR_OVERBOUGHT),
        )
    if indicator == "rsi":
        return (
            EventSpec("rsi", "rsi", "crossover", 30.0),
            EventSpec("rsi", "rsi", "crossunder", 70.0),
        )
    if indicator == "macd":
        return (
            EventSpec("macd", "macd_histogram", "crossover", 0.0),
            EventSpec("macd", "macd_histogram", "crossunder", 0.0),
        )
    if indicator == "vwap":
        return (
            EventSpec("vwap", "close", "series_crossover", column_b="vwap_indicator"),
            EventSpec("vwap", "close", "series_crossunder", column_b="vwap_indicator"),
        )
    if indicator == "ema":
        return (
            EventSpec("ema", "close", "series_crossover", column_b="ema"),
            EventSpec("ema", "close", "series_crossunder", column_b="ema"),
        )
    if indicator == "tsi":
        return (
            EventSpec("tsi", "tsi", "series_crossover", column_b="tsi_signal"),
            EventSpec("tsi", "tsi", "series_crossunder", column_b="tsi_signal"),
        )
    if indicator == "stoch_rsi":
        return (
            EventSpec("stoch_rsi", "stoch_rsi_k", "crossover", 20.0),
            EventSpec("stoch_rsi", "stoch_rsi_k", "crossunder", 80.0),
        )
    raise ValueError(
        f"Unknown indicator preset {indicator!r}. Supported: ema, macd, rsi, smi, stoch_rsi, tsi, vwap, williams_r"
    )


def _read_period_cfg(cfg: dict, key: str, default: dict) -> dict:
    value = cfg.get(key)
    if isinstance(value, dict):
        return {
            "period": value.get("period", default["period"]),
            "smooth1": value.get("smooth1", default.get("smooth1")),
            "smooth2": value.get("smooth2", default.get("smooth2")),
        }
    return default.copy()


def _add_rsi(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    period = int(cfg.get("rsi", {}).get("period", 14))
    df["rsi"] = compute_rsi(df, period=period)
    return df


def _add_macd(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    nested = cfg.get("macd", {})
    fast = int(nested.get("fast_period", 12))
    slow = int(nested.get("slow_period", 26))
    signal = int(nested.get("signal_period", 9))
    macd_df = compute_macd(df, fast_period=fast, slow_period=slow, signal_period=signal)
    df["macd_line"] = macd_df["macd_line"]
    df["macd_signal"] = macd_df["macd_signal"]
    df["macd_histogram"] = macd_df["macd_histogram"]
    return df


def _add_smi(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    fast_cfg = _read_period_cfg(cfg, "smi_fast", {"period": 5, "smooth1": 3, "smooth2": 3})
    slow_cfg = _read_period_cfg(cfg, "smi_slow", {"period": 13, "smooth1": 5, "smooth2": 5})
    df["smi_fast"] = compute_smi(
        df,
        period=int(fast_cfg["period"]),
        smooth1=int(fast_cfg["smooth1"]),
        smooth2=int(fast_cfg["smooth2"]),
    )
    df["smi_slow"] = compute_smi(
        df,
        period=int(slow_cfg["period"]),
        smooth1=int(slow_cfg["smooth1"]),
        smooth2=int(slow_cfg["smooth2"]),
    )
    return df


def _add_williams_r(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    period = int(cfg.get("williams_r", {}).get("period", 14))
    df["williams_r"] = compute_williams_r(df, period=period)
    return df


def _add_vwap(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    del cfg
    df["vwap_indicator"] = compute_vwap(df)
    return df


def _add_ema(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    period = int(cfg.get("ema", {}).get("period", 200))
    df["ema"] = compute_ema(df, period=period)
    return df


def _add_ema_233(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    period = int(cfg.get("ema_period", 233))
    df["ema_233"] = compute_ema(df, period=period)
    return df

def _add_tsi(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    tsi_cfg = cfg.get("tsi", {})
    long_p = int(tsi_cfg.get("long_period", 25))
    short_p = int(tsi_cfg.get("short_period", 13))
    signal_p = int(tsi_cfg.get("signal_period", 7))
    tsi_df = compute_tsi(df, long_period=long_p, short_period=short_p, signal_period=signal_p)
    df["tsi"] = tsi_df["tsi"]
    df["tsi_signal"] = tsi_df["tsi_signal"]
    return df

def _add_stoch_rsi(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    sr_cfg = cfg.get("stoch_rsi", {})
    length = int(sr_cfg.get("length", 14))
    smooth_k = int(sr_cfg.get("smooth_k", 3))
    smooth_d = int(sr_cfg.get("smooth_d", 3))
    rsi_period = int(sr_cfg.get("rsi_period", 14))
    sr_df = compute_stoch_rsi(df, length=length, smooth_k=smooth_k, smooth_d=smooth_d, rsi_period=rsi_period)
    df["stoch_rsi_k"] = sr_df["stoch_rsi_k"]
    df["stoch_rsi_d"] = sr_df["stoch_rsi_d"]
    return df


_INDICATOR_BUILDERS = {
    "ema": _add_ema,
    "ema_233_builder": _add_ema_233, # internal use for EMA strategy
    "macd": _add_macd,
    "rsi": _add_rsi,
    "smi": _add_smi,
    "stoch_rsi": _add_stoch_rsi,
    "tsi": _add_tsi,
    "vwap": _add_vwap,
    "williams_r": _add_williams_r,
}


def _get_resample_shift_info(df: pd.DataFrame, resample_tf: int) -> tuple[int, int]:
    """Calculate the base timeframe in minutes and the necessary shift to align with close bars."""
    actual_base_min = 5
    if len(df.index) > 1:
        diff = df.index[1] - df.index[0]
        actual_base_min = int(diff.total_seconds() / 60)
    
    shift_mins = resample_tf - actual_base_min
    return actual_base_min, max(0, shift_mins)


def _identify_resampled_close_bars(idx: pd.DatetimeIndex, resample_min: int, base_min: int = 5) -> pd.Series:
    """Mark bars that are the last base-TF bar of their resampled-TF window."""
    bar_width = pd.Timedelta(f"{base_min}min")
    freq = f"{resample_min}min"
    floors_now = idx.floor(freq)
    floors_next = (idx + bar_width).floor(freq)
    is_close = pd.Series(floors_now != floors_next, index=idx, dtype=bool)
    if len(is_close) > 0:
        is_close.iloc[-1] = True
    return is_close


def _detect_event(df: pd.DataFrame, spec: EventSpec) -> pd.Series:
    if spec.event_type not in _EVENT_DETECTORS:
        raise ValueError(
            f"Unknown event type {spec.event_type!r}. "
            f"Valid options: {', '.join(sorted(_EVENT_DETECTORS))}"
        )
    
    if spec.column not in df.columns:
        raise ValueError(f"Column {spec.column!r} not found in DataFrame after compute_indicators()")

    if spec.event_type in ("intrabar_crossover", "intrabar_crossunder"):
        curr_val = df[spec.column]
        threshold = df[spec.column_b] if spec.column_b else float(spec.threshold)
        # Intrabar cross needs the previous bar's close
        if "close" not in df.columns:
             raise ValueError("intrabar cross detection requires 'close' column in DataFrame")
        prev_close = df["close"].shift(1)
        return _EVENT_DETECTORS[spec.event_type](curr_val, threshold, prev_close)

    if spec.event_type in ("series_crossover", "series_crossunder"):
        if not spec.column_b:
            raise ValueError(
                f"event_type={spec.event_type!r} requires a second column. "
                "Set the corresponding *_column_b config."
            )
        if spec.column_b not in df.columns:
            raise ValueError(f"Column {spec.column_b!r} not found in DataFrame after compute_indicators()")
        return _EVENT_DETECTORS[spec.event_type](df[spec.column], df[spec.column_b])

    return _EVENT_DETECTORS[spec.event_type](df[spec.column], float(spec.threshold))


def _apply_vwap_filter(
    df: pd.DataFrame,
    long_trigger: pd.Series,
    short_trigger: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    if "vwap_indicator" not in df.columns:
        raise ValueError(
            "vwap_filter=True but 'vwap_indicator' column is missing from DataFrame. "
            "Call compute_indicators() first, or set vwap_filter=False."
        )
    return (
        long_trigger & (df["close"] > df["vwap_indicator"]),
        short_trigger & (df["close"] < df["vwap_indicator"]),
    )


def _combine_trigger_chain(
    trigger_events: list[tuple[pd.Series, pd.Series]],
    is_sequential: bool,
    window: int,
    bidirectional: bool,
    index: pd.Index,
) -> tuple[pd.Series, pd.Series]:
    """Combine trigger events into a single (long, short) pair.

    Sequential mode: trigger[0] arms the system; all subsequent triggers must fire
    within ``window`` bars of the original arm.  For N=2 this delegates to
    ``apply_armed_logic`` (backwards compatible); for N>2 it uses
    ``apply_armed_chain`` which enforces a single window from trigger[0].

    Windowed mode (is_sequential=False): each trigger must have fired within
    ``window`` bars (rolling, independent per trigger). A combined signal fires
    only when ALL triggers have at least one occurrence within the same rolling
    ``window``-bar lookback.

    bidirectional (legacy "either"): for 2-trigger chains, try both orderings
    and OR the results.
    """
    if len(trigger_events) == 1:
        return trigger_events[0]

    def _chain(events: list[tuple[pd.Series, pd.Series]]) -> tuple[pd.Series, pd.Series]:
        if is_sequential:
            long_arrays = [e[0].values for e in events]
            short_arrays = [e[1].values for e in events]
            if len(events) == 2:
                # N=2: use original apply_sequential_logic for exact backwards compat
                long_result = apply_sequential_logic(long_arrays[0], long_arrays[1], window)
                short_result = apply_sequential_logic(short_arrays[0], short_arrays[1], window)
            else:
                # N>2: single-window chain from trigger[0]
                long_result = apply_sequential_chain(long_arrays, window)
                short_result = apply_sequential_chain(short_arrays, window)
            return (
                pd.Series(long_result, index=index),
                pd.Series(short_result, index=index),
            )
        else:
            # Windowed: all triggers must co-occur within window of the first
            first_long, first_short = events[0]
            long_ok = within_window(first_long, window)
            short_ok = within_window(first_short, window)
            for evt_long, evt_short in events[1:]:
                long_ok = long_ok & within_window(evt_long, window)
                short_ok = short_ok & within_window(evt_short, window)
            # Signal fires on the last trigger's bar (when all conditions met)
            last_long, last_short = events[-1]
            return last_long & long_ok, last_short & short_ok

    long_result, short_result = _chain(trigger_events)

    if bidirectional and len(trigger_events) == 2:
        rev_long, rev_short = _chain([trigger_events[1], trigger_events[0]])
        long_result = long_result | rev_long
        short_result = short_result | rev_short

    return long_result, short_result


def _build_indicators_on_df(
    df: pd.DataFrame,
    pipeline: PipelineConfig,
    config: dict,
) -> pd.DataFrame:
    """Add indicator columns for all triggers in the pipeline."""
    cfg = pipeline.config_section
    is_ema_233 = _get_signal_system(config) == "ema_233"
    seen: set[str] = set()
    for spec in pipeline.trigger_specs:
        indicator = spec.indicator
        builder_key = f"{indicator}_233_builder" if indicator == "ema" and is_ema_233 else indicator
        if builder_key not in _INDICATOR_BUILDERS:
            raise ValueError(f"Unknown indicator {indicator!r}")
        if indicator in seen:
            continue
        seen.add(indicator)
        df = _INDICATOR_BUILDERS[builder_key](df, cfg)
    return df


def compute_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add all indicator columns required by the configured signal flow."""
    pipeline = _normalize_pipeline_config(config)
    cfg = pipeline.config_section
    df = df.copy()

    if pipeline.resample_tf:
        df_resampled = aggregate_to_Nmin(df, pipeline.resample_tf)
        df_resampled = _build_indicators_on_df(df_resampled, pipeline, config)

        actual_base_min, shift_mins = _get_resample_shift_info(df, pipeline.resample_tf)

        # Shift resampled index so the label matches the close bar of the base timeframe, preventing lookahead bias
        if shift_mins > 0:
            df_resampled.index = df_resampled.index + pd.Timedelta(minutes=shift_mins)

        # Map back to 5-min index via forward-fill
        for col in [c for c in df_resampled.columns if c not in {"open", "high", "low", "close", "volume"}]:
            df[col] = df_resampled[col].reindex(df.index, method="ffill")

        if pipeline.entry_price_hint and "ema_233" in df.columns:
            offset = cfg.get("entry_offset_cents", 0.02)
            df["ema_entry_long"] = df["ema_233"] + offset
            df["ema_entry_short"] = df["ema_233"] - offset

        is_close = _identify_resampled_close_bars(df.index, pipeline.resample_tf, actual_base_min)
        if pipeline.resample_tf == 15:
            df["is_15m_close_bar"] = is_close
        else:
            df[f"is_{pipeline.resample_tf}m_close_bar"] = is_close

    else:
        df = _build_indicators_on_df(df, pipeline, config)

        if pipeline.entry_price_hint and "ema_233" in df.columns:
            offset = cfg.get("entry_offset_cents", 0.02)
            df["ema_entry_long"] = df["ema_233"] + offset
            df["ema_entry_short"] = df["ema_233"] - offset

    if pipeline.vwap_filter and "vwap_indicator" not in df.columns:
        df = _add_vwap(df, cfg)

    # Warn on mid-series NaN/inf
    ohlcv = {"open", "high", "low", "close", "volume"}
    for col in [c for c in df.columns if c not in ohlcv and not c.startswith("is_")]:
        series = df[col]
        first_valid = series.first_valid_index()
        if first_valid is None:
            continue
        mid_nans = series.loc[first_valid:].isna().sum()
        mid_infs = series.loc[first_valid:].isin([float("inf"), float("-inf")]).sum()
        if mid_nans > 0:
            logger.warning(
                "%s has %d mid-series NaN values — events may be silently missed",
                col, mid_nans,
            )
        if mid_infs > 0:
            logger.warning(
                "%s has %d mid-series inf values — signals may be silently suppressed",
                col, mid_infs,
            )

    return df


def _detect_all_trigger_events(
    df: pd.DataFrame,
    pipeline: PipelineConfig,
) -> list[tuple[pd.Series, pd.Series]]:
    """Detect long/short events for every trigger in the chain."""
    events: list[tuple[pd.Series, pd.Series]] = []
    for spec in pipeline.trigger_specs:
        long_evt = _detect_event(df, spec.long)
        short_evt = _detect_event(df, spec.short)
        events.append((long_evt, short_evt))
    return events


def generate_signals(df: pd.DataFrame, config: dict) -> pd.Series:
    """Generate +1 / -1 / 0 signals for the configured signal flow."""
    pipeline = _normalize_pipeline_config(config)

    if pipeline.resample_tf:
        df_resampled = aggregate_to_Nmin(df, pipeline.resample_tf)
        
        actual_base_min, shift_mins = _get_resample_shift_info(df, pipeline.resample_tf)

        # Shift resampled index so the label matches the close bar of the base timeframe
        if shift_mins > 0:
            df_resampled.index = df_resampled.index + pd.Timedelta(minutes=shift_mins)
            
        resampled_indicators = df.reindex(df_resampled.index, method="ffill")
        for col in ["open", "high", "low", "close", "volume"]:
            resampled_indicators[col] = df_resampled[col]

        trigger_events = _detect_all_trigger_events(resampled_indicators, pipeline)
        long_trigger, short_trigger = _combine_trigger_chain(
            trigger_events, pipeline.is_sequential, pipeline.sync_window,
            pipeline.bidirectional, resampled_indicators.index,
        )

        if pipeline.vwap_filter:
            if "vwap_indicator" not in df.columns:
                df["vwap_indicator"] = compute_vwap(df)
            vwap_resampled = df["vwap_indicator"].reindex(resampled_indicators.index, method="ffill")
            long_trigger = long_trigger & (resampled_indicators["close"] > vwap_resampled)
            short_trigger = short_trigger & (resampled_indicators["close"] < vwap_resampled)

        signal_resampled = pd.Series(0, index=resampled_indicators.index, dtype=int)
        signal_resampled[long_trigger] = 1
        signal_resampled[short_trigger] = -1
        signal_resampled[long_trigger & short_trigger] = 0

        is_close_col = "is_15m_close_bar" if pipeline.resample_tf == 15 else f"is_{pipeline.resample_tf}m_close_bar"
        is_close = df[is_close_col] if is_close_col in df.columns else _identify_resampled_close_bars(df.index, pipeline.resample_tf, actual_base_min)

        mapped_signals = signal_resampled.reindex(df.index, method="ffill").fillna(0).astype(int)

        signal_base = pd.Series(0, index=df.index, dtype=int)
        signal_base[is_close] = mapped_signals[is_close].values

        if pipeline.entry_price_hint:
            _write_entry_price_hint(df, signal_base, pipeline.config_section)

        return signal_base

    else:
        trigger_events = _detect_all_trigger_events(df, pipeline)
        long_trigger, short_trigger = _combine_trigger_chain(
            trigger_events, pipeline.is_sequential, pipeline.sync_window,
            pipeline.bidirectional, df.index,
        )

        if pipeline.vwap_filter:
            long_trigger, short_trigger = _apply_vwap_filter(df, long_trigger, short_trigger)

        signal = pd.Series(0, index=df.index, dtype=int)
        both = long_trigger & short_trigger
        signal[long_trigger & ~both] = 1
        signal[short_trigger & ~both] = -1
        # conflicting bars remain 0

        if pipeline.entry_price_hint:
            _write_entry_price_hint(df, signal, pipeline.config_section)

        return signal

def _write_entry_price_hint(df: pd.DataFrame, signals: pd.Series, cfg: dict):
    """Specific to EMA system: writes entry_price_hint as side-effect."""
    offset = cfg.get("entry_offset_cents", 0.02)
    ema_col = "ema_233" # assumed for now
    if ema_col not in df.columns:
         return
         
    df["entry_price_hint"] = np.nan
    long_mask = signals == 1
    short_mask = signals == -1
    if long_mask.any():
        df.loc[long_mask, "entry_price_hint"] = df.loc[long_mask, ema_col] + offset
    if short_mask.any():
        df.loc[short_mask, "entry_price_hint"] = df.loc[short_mask, ema_col] - offset
