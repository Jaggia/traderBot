---
tags: [deflated-sharpe-ratio, dsr, psr, multiple-testing, inverse-cdf]
---
# 029 — Deflated Sharpe Ratio (DSR)

**Date:** 2026-05-01

## What

Implemented the Deflated Sharpe Ratio (Bailey & López de Prado, 2014) — a multiple-testing-corrected version of PSR that accounts for the number of independent strategy variants tested.

## Why

When you test N strategy variants and pick the one with the highest Sharpe, that Sharpe is inflated by selection bias. PSR corrects for skewness/kurtosis/sample size but ignores how many variants you tried. DSR fixes this by replacing the `benchmark_sharpe = 0` baseline in PSR with the *expected maximum Sharpe under the null hypothesis* (all N strategies have zero true Sharpe).

## What Changed

### New functions in `src/analysis/metrics.py`

- `_norm_ppf(p)` — inverse standard normal CDF (Acklam rational approximation, ~1e-9 accuracy). No scipy dependency needed.
- `EULER_MASCHERONI` — module-level constant (0.5772...)
- `_expected_max_sharpe(n_trials)` — computes E[max SR] under the null using:
  ```
  E[max SR] = (1 - γ) · Φ⁻¹(1 - 1/N) + γ · Φ⁻¹(1 - 1/(N·e))
  ```
- `_dsr(sharpe, n_trials, n_observations, skewness, kurtosis)` — delegates to `_psr()` with `benchmark_sharpe = _expected_max_sharpe(n_trials)`
- `count_trials(run_key_path)` — reads `results/run_key.yaml` and counts distinct variant keys. Returns 1 if file missing/empty.

### Integration

- `compute_metrics()` now accepts `n_trials: int = 1` parameter (backward compatible)
- DSR only appears in output when `n_trials > 1` (avoids noise for single-variant runs)
- `save_report_md()` displays DSR as "Deflated Sharpe (DSR)" in the metrics table
- `BaseBacktestRunner.run()` calls `count_trials()` before `compute_metrics()` with `current_tag=run_tag` — the current variant is included in the count (so a brand-new config still incurs the multiple-testing penalty), and `_update_run_key()` happens after metrics are computed

### Tests

- `tests/analysis/test_dsr.py` — 24 tests covering:
  - `_norm_ppf`: accuracy (median=0, Φ(1)≈0.84134, 95th percentile≈1.96), symmetry, roundtrip with `_norm_cdf`
  - `_expected_max_sharpe`: zero for N=1, monotonically increasing, known range for N=100
  - `_dsr`: equals PSR when N=1, strictly less when N>1, edge cases (None, too few observations)
  - `count_trials`: missing file, empty yaml, key counting
  - Integration: `compute_metrics()` produces DSR only when `n_trials > 1`, DSR ≤ PSR

## Design Decisions

1. **Pure-math `_norm_ppf`** (no scipy): The codebase avoids scipy. Acklam's rational approximation provides ~1e-9 accuracy using only `math.sqrt` and `math.log`.
2. **`n_trials` defaults to 1**: Existing callers unchanged. DSR only appears when multiple variants have been tested.
3. **Count before update**: `count_trials()` is called before `_update_run_key()` but with `current_tag=run_tag` so the current variant IS included. DSR answers "given N total trials including this one, is *this* Sharpe significant?" — a newly tested variant still incurs the multiple-testing penalty.
4. **`run_key.yaml` as trial tracker**: The existing `_update_run_key()` deduplicates by tag, so re-running the same config counts once — correct behavior for DSR.
