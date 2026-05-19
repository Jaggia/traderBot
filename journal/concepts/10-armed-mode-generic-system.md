---
tags: [armed-mode, system-3, generic-pipeline, rsi, macd, indicators]
---
# Concept: Advanced Signal Systems (Systems 3 & 4)

Beyond the fixed pairings of System 1 (SMI + Williams %R) and System 2 (EMA 233), the framework provides highly flexible, config-driven signal generation via the **Generic Armed Mode** and the **Trigger Chain**.

---

## System 3: Generic Armed Mode

System 3 generalises the "Arm → Fire" pattern used in System 1. It allows you to pick *any two supported indicators* as the arm and fire conditions via config alone.

Select it with:
```yaml
strategy:
  signal_system: "armed_mode"  # Alias for indicator_pair with armed_mode: true
```

### The Idea: Arm → Fire
The first indicator *arms* the system (detects a setup), and the second *fires* a signal (confirms the entry) within a `sync_window`.

**Default example: RSI → MACD**
- RSI crossing below 30 (oversold) **arms** for a long.
- MACD histogram crossing above 0 (bullish flip) **fires** the signal.

---

## System 4: Trigger Chain

System 4 is the most powerful signal strategy. It generalises the state machine to **N stages** (1, 2, or more indicators in sequence) and supports both **armed** (sequential) and **windowed** (co-occurrence) modes.

Select it with:
```yaml
strategy:
  signal_system: "trigger_chain"
```

### Core Logic: Sequential Gates
The system maintains a `current_stage` counter. A signal is only generated when the final indicator in the list fires while the system is at the penultimate stage.

1. **Trigger 1 fires**: Stage 0 → Stage 1. Window timer starts.
2. **Trigger 2 fires**: Stage 1 → Stage 2. (If within window).
3. **Trigger N fires**: Stage N-1 → Signal. (If within window).

### Windowed Mode (Non-Armed)
By setting `armed_mode: false`, the system shifts from sequential to "co-occurrence" logic. It checks if *all* triggers have fired at least once within the last `sync_window` bars. This is useful for indicators that signal a "state" rather than a "moment."

---

## State Machine (Shared Primitives)

The core logic for both systems lives in `src/signals/indicator_pair_pipeline.py`. It uses a bar-by-bar loop over boolean trigger arrays.

Key rules:
- **One signal per sequence.** After a fire, the state resets.
- **Window expiry.** If the sequence isn't completed within `sync_window` bars, the state resets to 0.
- **Re-triggering resets the window.** A new fire of the *first* trigger resets the countdown.

---

## Supported Indicators

| Name | Config key | Column(s) produced |
|---|---|---|
| RSI | `rsi` | `rsi` |
| MACD | `macd` | `macd_line`, `macd_signal`, `macd_histogram` |
| SMI | `smi` | `smi_fast`, `smi_slow` |
| Williams %R | `williams_r` | `williams_r` |
| EMA | `ema` | `ema` |
| VWAP | `vwap` | `vwap_indicator` |

---

## Short-Side Handling

For each long-side event, the short side uses the inverted event type (e.g. `crossover` ↔ `crossunder`).

**Asymmetric oscillators** (e.g. RSI 0–100): Use explicit `threshold_short` overrides:
```yaml
- indicator: rsi
  threshold: 30         # long-side arm: RSI < 30
  threshold_short: 70   # short-side arm: RSI > 70
```

---

## Configuration Examples

### System 3 (Armed Mode)
```yaml
armed_mode:
  arm_indicator: rsi
  arm_event: crossunder
  arm_threshold: 30
  fire_indicator: macd
  fire_event: crossover
  sync_window: 5
```

### System 4 (Trigger Chain)
```yaml
trigger_chain:
  triggers:
    - indicator: rsi
      event: crossunder
      threshold: 30
    - indicator: smi
      event: series_crossover
    - indicator: macd
      event: crossover
      threshold: 0
  sync_window: 10
  armed_mode: true
```

---

## Code Locations

| File | Role |
|---|---|
| `src/signals/indicator_pair_pipeline.py` | Unified pipeline implementation for all systems. |
| `src/signals/armed_mode.py` | Low-level state machine primitives (`apply_armed_logic`, `apply_trigger_chain_logic`). |
| `src/signals/strategy.py` | Strategy Pattern wrappers (`IndicatorPairStrategy`, `TriggerChainStrategy`). |
| `src/indicators/base.py` | Shared mathematical primitives (`rolling_high_low`, `double_ema_smooth`). |

---

## Comparison of Generic Systems

| Dimension | Indicator Pair (System 3) | Trigger Chain (System 4) |
|---|---|---|
| Max Indicators | 2 | Unlimited (N) |
| Sequence | Sequential only | Sequential or Co-occurrence |
| Use Case | Simple confirmation | Complex multi-filter setups |
| Config Block | `armed_mode:` | `trigger_chain:` |
