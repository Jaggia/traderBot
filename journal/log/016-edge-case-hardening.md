---
tags: [edge-cases, bugfixes, greeks, vwap, alpaca, guards]
---
# 016 — Edge-Case Hardening: Guards, Fixes & Missing Tests

**Date:** 2026-03-22

---

## What We Did

Ran a full codebase audit to find untested edge cases and subtle bugs, then fixed them all in one pass.  Session 015 fixed the 1_OTM/1_ITM put inversion — this session fixes everything the audit flagged as medium or low risk, plus adds the missing tests.

---

## Source Code Fixes (5 files)

### `src/options/greeks.py`

**ATM-at-expiry delta bug** — When `T=0` and `S == K`, the old code returned `delta = 0.0` for calls and `0.0` for puts (both fell into the `else` branch).  Correct convention for a coin-flip boundary is `delta = ±0.5`.  Fixed with explicit `if/elif/else` on `S > K`, `S < K`, `S == K`.

**Input validation** — No guard existed for `S ≤ 0` or `K ≤ 0`, which would cause a `math.log(S/K)` domain error deep inside `_d1d2`.  Added `ValueError` raises at the top of `compute_greeks`.

### `src/options/option_pricer.py`

**Input validation** — Same domain-error risk for non-positive S or K.  Added matching `ValueError` guards.

**T=0 semantics** — Added a comment clarifying that `T == 0` means "option expires at this bar's close" (still valid, should return intrinsic), distinguishing it from `T < 0` (already expired).  Logic unchanged; comment removes ambiguity.

### `src/indicators/williams_r.py`

**Zero-range inf propagation** — When the rolling window contains only identical prices (`high == low`), the denominator is zero and the result is `±inf`.  Unlike SMI, Williams %R had no guard.  Added `wr.replace([np.inf, -np.inf], np.nan)` to match SMI's treatment and prevent inf values from propagating into signals.

### `src/signals/smi_wr_generator.py`

**Missing VWAP column** — If `vwap_filter=True` but the caller forgot to call `compute_indicators()` first, `df["vwap_indicator"]` would raise a bare `KeyError` with no useful context.  Replaced with an explicit `KeyError` that names the problem and says how to fix it.

### `src/data/alpaca_loader.py`

**Month boundary bug in `_needs_update()`** — The staleness check compared `last_date.day` numerically without verifying that `last_date` belongs to the requested year/month.  A February CSV whose UTC→EST conversion shifted its last timestamp back to January 31 would have `last_date.day = 31`, and `31 < last_day_feb - 2 = 26` is False — so the code would falsely conclude the file was complete.  Added a year/month guard: if `last_date.year != year or last_date.month != month`, return `True` (stale) immediately.

---

## New Tests (8 test additions)

### `tests/options/test_greeks.py` — 7 new tests

| Test | What it pins |
|------|-------------|
| `test_expired_atm_call_delta_half` | T=0, S=K call → delta=0.5, price=0 |
| `test_expired_atm_put_delta_neg_half` | T=0, S=K put → delta=-0.5, price=0 |
| `test_expired_itm_put` | T=0, K>S put → delta=-1, price=K-S |
| `test_expired_otm_put` | T=0, S>K put → delta=0, price=0 |
| `test_nonpositive_s_raises` (Greeks) | S=0 → ValueError |
| `test_negative_s_raises` (Greeks) | S=-10 → ValueError |
| `test_nonpositive_k_raises` (Greeks) | K=0 → ValueError |
| `test_nonpositive_s_raises` (Pricer) | S=0 → ValueError |
| `test_nonpositive_k_raises` (Pricer) | K=0 → ValueError |

### `tests/indicators/test_indicators.py` — 4 new tests

| Test | What it pins |
|------|-------------|
| `test_at_lowest_low` | WR = -100 when close = rolling low |
| `test_zero_range_bars_yield_nan` | Flat price → NaN not inf in WR |
| `test_flat_price_yields_nan_not_inf` | Flat price → NaN not inf in SMI |
| `test_premarketbars_are_included_in_cumsum` | Documents VWAP pre-market behaviour; callers must pre-filter |

### `tests/indicators/test_signals.py` — 1 new test

| Test | What it pins |
|------|-------------|
| `test_vwap_filter_missing_column_raises` | `vwap_filter=True` + no column → KeyError with description |

### `tests/data/test_data_loaders.py` — 1 new test

| Test | What it pins |
|------|-------------|
| `test_needs_update_wrong_month_in_csv_returns_true` | Feb CSV with Jan data → `_needs_update` returns True |

---

## Test Suite

| Scope | Before | After |
|-------|--------|-------|
| Affected test files (4 files) | 84 | 97 |
| Full suite (excl. matplotlib-dependent) | 378 | 391 |

No regressions.  The 2 pre-existing failures (`test_eod_close_*`) are an environment-only issue: `US/Eastern` timezone key is missing from the system tzdata on this machine; they were failing before this session and are unrelated.

---

## Remaining Known Gaps (not fixed — require deeper refactor or are doc-only)

- **`engine.py` nearest-bar lookup** — `get_indexer(..., method="nearest")` has no bounds check. A large gap in options data could silently return a stale price. No fix this session; low occurrence risk given pre-download script.
- **VWAP pre-market filtering** — VWAP implementation doesn't filter pre-market bars; callers are responsible.  Documented with a new test rather than adding filtering logic (the backtest already feeds only RTH data).
- **Aggregator vs Alpaca between-time difference** — Aggregator uses `09:30–15:55`, Alpaca loader uses `09:30–16:00`. Difference is intentional (aggregator produces 5-min bars, 15:55 is the last complete bar; Alpaca raw 1-min/5-min keeps up to 16:00). Documented as a comment in the code.
