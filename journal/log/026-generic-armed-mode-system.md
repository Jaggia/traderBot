---
tags: [system-3, armed-mode, generic-pipeline, rsi, macd]
---
# 026 — Generic Armed-Mode Signal System (System 3)

**Date:** 2026-04-11

---

## What Changed

Added a third signal strategy, `"armed_mode"`, that generalises the two-stage arm→fire pattern beyond the fixed SMI+Williams %R pair. The indicator pair is now fully configurable — any two supported indicators, any event types, any thresholds — all via `config/strategy_params.yaml`.

---

## New Modules

### `src/indicators/rsi.py`

RSI with Wilder's smoothing (`alpha=1/period`, `adjust=False`). This matches TradingView's default RSI implementation — important because the default config arms on RSI crossing below 30.

### `src/indicators/macd.py`

Standard 12/26/9 EMA-based MACD. Returns a three-column DataFrame: `macd_line`, `macd_signal`, `macd_histogram`. The histogram is the primary fire column in the default config (crosses above 0 = bullish flip).

### `src/signals/armed_mode.py`

Generic state machine and event detection primitives extracted from `smi_wr_pipeline.py`. Any pipeline can now import these without depending on SMI/WR logic:

- `crossover(series, threshold)` / `crossunder(series, threshold)` — threshold crossing events
- `series_crossover(fast, slow)` / `series_crossunder(fast, slow)` — two-line crossing events
- `within_window(trigger, window)` — rolling-window presence check (non-armed path)
- `apply_armed_logic(arm_events, fire_events, window)` — the bar-by-bar state machine

### `src/signals/armed_mode_pipeline.py`

The full pipeline. Reads `config["armed_mode"]` to:
1. Build indicator columns (via `_INDICATOR_BUILDERS` registry)
2. Detect arm and fire events on the chosen columns
3. Apply the state machine for long and short sides independently
4. Apply optional VWAP filter

Handles short-side asymmetry: explicit `arm_threshold_short` / `fire_threshold_short` keys override the `-threshold` fallback, which would produce unreachable values for bounded oscillators like RSI.

---

## Refactoring: `smi_wr_pipeline.py`

The ~40 lines of inline state machine code in `smi_wr_pipeline.py` were removed and replaced with imports from `src.signals.armed_mode`. No behavioral change — System 1 still works identically. The pipeline is now a consumer of shared primitives rather than their owner.

---

## Infrastructure Changes

### Symbol parameterisation

`DatabentoOptionsLoader`, `DatabentoStreamer`, and `IBKRStreamer` all now accept `symbol: str = "SYMBOL"` (default unchanged). Hardcoded `"SYMBOL"` strings in subscribe calls and log messages replaced with `self._symbol`. Prepares these components for use with other underlyings without any code changes.

### `live_engine.py` docstring

`reconcile_positions()` docstring updated to remove Alpaca/SYMBOL-specific references — now generic.

---

## Default Config (`armed_mode:` block)

```yaml
arm_indicator: rsi       # RSI < 30 arms long; RSI > 70 arms short
arm_event: crossunder
arm_threshold: 30
arm_threshold_short: 70  # asymmetric override — RSI can never reach -30

fire_indicator: macd     # histogram > 0 fires long; < 0 fires short
fire_event: crossover
fire_threshold: 0
fire_threshold_short: 0

sync_window: 5
vwap_filter: false
```

Active strategy is still `signal_system: smi_wr`. Switch to `armed_mode` to use System 3.

---

## Tests

- `tests/signals/test_armed_mode.py` — 11 test classes covering the state machine (boundary, re-arm, fire-disarms, NaN-safe first bar) and all four event detectors
- `tests/signals/test_armed_mode_pipeline.py` — pipeline integration tests: all six indicators, config validation, short-side threshold logic, `create_strategy()` factory wiring

---

## Journal / Docs Updated

- `journal/concepts/10-armed-mode-generic-system.md` — new concept doc covering System 3 in full
- `journal/docs/_state.md` — Signal Strategy Pattern section updated; test count updated
- `journal/INDEX.md` — this entry added
