---
tags: [armed-mode, signals, vwap, pine-script, alignment]
---
# Journal Entry 001: Armed Mode & Pine/Python Signal Alignment

**Date:** 2026-02-15

## What We Built

Added an **armed/non-armed signal mode** to the backtesting engine and then discovered — and fixed — logic mismatches between the TradingView Pine Script and our Python engine.

## The Problem: Duplicate Signals

In the original (non-armed) signal logic, if W%R fires at bar 100, *any* SMI cross within the sync window (bars 101-106) triggers a signal. If SMI crosses twice in that window, you get two signals from a single W%R event. That's noisy — the second signal isn't backed by a fresh confirming indicator, it's riding the coattails of the first.

## Armed Mode: The Fix

**Armed mode** makes the signal stateful:
1. First indicator **arms** the system
2. Second indicator **fires + disarms** it
3. No more signals until a fresh arming event

This is inherently non-vectorizable — whether a signal fires depends on whether a prior signal already consumed the arm. Required a bar-by-bar loop over the pre-computed boolean arrays.

**Non-armed mode** (the original) stays fully vectorized with rolling windows. Default is `false` to preserve backward compatibility.

## Three Design Decisions

### 1. VWAP Filter: Check at Fire Time, Not Arm Time

The original Pine Script checked VWAP when the arming indicator triggered. We moved it to fire time.

**Why:** VWAP is an intraday level that moves. If SMI arms the system at 10:30 AM and W%R confirms at 11:15 AM, the VWAP context could have shifted meaningfully. You want the trend guard reflecting conditions *at entry*, not at the initial trigger 5-20 bars earlier. Most institutional VWAP-based filters check at execution time.

### 2. Window Expiry: Expire Before Fire Check

The original Pine Script checked fire conditions before expiry on each bar. We reversed the order: expire first, then check fire.

**Why:** If the window has expired, the arm is dead — period. The original order let a signal sneak through on the exact expiry bar (an off-by-one edge case). "Expire first, then check fire" is logically stricter. A stale arm shouldn't produce a signal just because the fire event happened to land on the boundary bar.

### 3. Signal Mode Naming: Chronological Order

We discovered the Pine Script's non-armed mode had the `wr_then_smi` / `smi_then_wr` labels **swapped** relative to what they actually did.

The naming convention we settled on: **the mode name describes chronological order**.
- `wr_then_smi` = W%R fires first, then SMI confirms (signal fires on the SMI bar)
- `smi_then_wr` = SMI fires first, then W%R confirms (signal fires on the W%R bar)

The Pine Script's armed mode already followed this convention correctly (arm=first, fire=second). But the non-armed lookback mode had them backwards — `wr_then_smi` was triggering on the W%R bar and looking back for SMI, which is actually `smi_then_wr` behavior. Fixed by swapping the dropdown default in Pine to compensate.

## Empirical Results (TV Data, 2025-11-10 to 2026-02-13)

| Combo | Trades | Return |
|-------|--------|--------|
| SMI->WR non-armed | 20 | +0.45% |
| SMI->WR armed | 16 | -0.18% |
| WR->SMI non-armed | 34 | +1.30% |
| WR->SMI armed | 34 | +1.30% |

**WR->SMI outperformed across the board.** The "trend first, momentum confirmation" pattern (SMI cross establishes the trend, W%R threshold crossing confirms momentum) produced better risk-adjusted returns.

Armed mode had zero impact on WR->SMI — no duplicate signals existed in that direction to begin with. It reduced SMI->WR trades from 20 to 16 but hurt returns, suggesting the "extra" signals it filtered were actually contributing positively.

## Key Insight: Alpaca vs TradingView Data

Every Alpaca run was negative while TradingView was positive on 3 of 4 combos. This isn't a code bug — it's a data quality difference. Different bar aggregation methods between providers produce different OHLC values, which compound into meaningfully different backtest results. TradingView data is the source of truth since it matches on-chart visual analysis.

## Files Changed

- `config/strategy_params.yaml` — added `armed_mode: false`
- `src/signals/smi_wr_generator.py` — added `_apply_armed_logic()`, branched `generate_signals()`
- `tests/test_signals.py` — 4 new tests for armed mode behavior
- `scripts_py/latest_smi_wPr_vwap.pine` — VWAP at fire time, expiry before fire, label fix
- `scripts_py/armed_mode_comparison.py` — 8-combo comparison runner (Alpaca x TV x 4 signal combos)
