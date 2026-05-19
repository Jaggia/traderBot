---
tags: [testing, quality-audit, integration-tests, greeks, vwap]
---
# 013 — Test Suite Quality Audit and Fixes

**Date:** 2026-03-13

## What Happened

Ran a full quality audit of the 314-test suite asking a specific question: are the tests actually catching bugs, or do they pass trivially because of over-mocking and weak assertions?

The audit identified three categories of real problems:

### 1. Full pipeline never tested together (critical gap)
`test_engine.py` patches both `compute_indicators` and `generate_signals` entirely. The engine tests verify that the *engine loop responds correctly to injected signals*, but a completely broken indicator or signal generator would not be caught. Similarly, all three `lookforward_mode` variants and `armed_mode` had zero integration coverage — only their helper functions were tested in isolation.

**Fix:** Added `tests/test_integration.py` (22 tests). Exercises the full pipeline — real indicator computation, real signal generation, real engine loop — with seeded synthetic OHLCV data. Tests cover:
- Indicator columns present and non-NaN after engine init
- Williams %R always in [-100, 0]
- At least one signal generated from seeded data
- All positions closed at end of run
- Equity curve length == bar count
- Closed trades have valid exit reasons and directions matching signals
- Final equity == initial_capital + sum(pnl)
- All three `lookforward_mode` values (parametrized)
- `armed_mode=True` with all three modes
- `armed_mode` fires ≤ signals than non-armed (it adds a gate, never more signals)

### 2. Options expiration only tested as pure function
`check_option_exit` had thorough pure-function tests for the expiration branch. But no test exercised the expiration path inside the actual `engine.run()` hot loop, where `check_option_exit` is called via `pos.expiry - ts` DTE computation.

**Fix:** Added `test_options_expiration_exit` to `TestEngineOptionsMode`. Builds bars all on `2025-01-02` and creates a position with `expiry = 2025-01-02` (tz-aware). The first exit-check bar after entry fires `expiration` since `ts.date() >= pos.expiry.date()`. TP/SL set wide and EOD/opposite-signal disabled so expiration is the only trigger. Asserts `exit_reason == "expiration"`.

### 3. Greek value assertions too weak
`TestBuildOptionPositionGreeks` only asserted `not None and not math.isnan(value)`. This passes even if delta=0.0, vega=-50, or gamma=999. Wrong Greeks directly affect options P&L and would not be detected.

**Fix:** Replaced the class with proper range assertions grounded in Black-Scholes theory:
- Call delta: `0 < delta <= 1`
- Put delta: `-1 <= delta < 0`
- Gamma: `> 0` (convexity is always positive for long options)
- Theta: `< 0` (time decay always hurts the buyer)
- Vega: `> 0` (higher vol always increases long option value)
- ATM call delta: in `(0.3, 0.7)` — should be near 0.5
- ATM put delta: in `(-0.7, -0.3)` — should be near -0.5

Refactored into `_build_call_pos()` / `_build_put_pos()` helpers so each test is a one-liner.

### 4. VWAP filter checked at only one bar
The VWAP filter test set up one trigger and checked `signals.iloc[5] == 0`. An off-by-one in the filter (e.g. `>=` vs `>`) would still pass that single-point check.

**Fix:** Set up two independent long triggers (bars 5 and 12), checked both are suppressed, and added a final `assert (signals == 1).sum() == 0` to confirm no long signal fires anywhere. Added the inverse test (`close > VWAP → signal fires`) so both sides of the comparison are covered.

### 5. Spurious divide-by-zero warning in monte_carlo.py
`np.where(gross_loss > 0, gross_profit / gross_loss, np.inf)` always evaluates both branches before applying the condition, generating 12 `RuntimeWarning: divide by zero` warnings during every test run even though the result is correctly set to `inf`.

**Fix:** Wrapped with `np.errstate(divide="ignore", invalid="ignore")` — the guard is already correct, just the warning was noisy.

## Result

341 tests, all passing, 0 warnings.

The key improvement: the full signal→engine→P&L pipeline is now exercised without mocks on every `pytest` run. Bugs introduced into `compute_indicators`, `generate_signals`, or their handoff to the engine will surface rather than hide behind patched boundaries.
