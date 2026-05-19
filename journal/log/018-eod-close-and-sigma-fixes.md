---
tags: [eod, close-logic, sigma, black-scholes, bugfix]
---
# 018 — EOD Close Logic Fix + BS Fallback Sigma from Config

**Date:** 2026-03-23

Cherry-picked two bug fixes from a stale branch (`claude/code-review-bug-analysis-rFY8Y`) that had been forked before journal 017 work. The branch also contained live engine reverts — those were discarded; only the fixes were applied manually.

## Bugs Fixed

### 1. EOD Close Logic Fails for Hours > 15 (trade_logic.py, exit_rules.py)

**Severity: MEDIUM**

The EOD close condition was:
```python
if bar.hour >= 15 and bar.minute >= 55:
```

Both conditions use `and`, so at 16:00 (hour=16, minute=0) the check fails because `minute >= 55` is False. While data is normally filtered to 09:30–15:55, any data source including a 16:00 bar (or timestamps shifted by timezone issues) would silently skip EOD close.

**Fix:** Changed to `(bar.hour > 15 or (bar.hour == 15 and bar.minute >= 55))` in both `trade_logic.py` and `exit_rules.py`.

### 2. Hardcoded sigma=0.25 in BS Fallback (engine.py)

**Severity: LOW**

`_get_option_price()` used a hardcoded `sigma = 0.25` for Black-Scholes fallback, ignoring `config.options.sigma`. If the user changes sigma in config, the fallback pricer wouldn't reflect it.

**Fix:** Changed to `self.config.get("options", {}).get("sigma", 0.25)`.

## Full Module Review (No Issues Found)

The branch also included a full read-through of every module. Clean bill of health for:

- `src/indicators/` — SMI, Williams %R, VWAP all correct
- `src/signals/smi_wr_generator.py` — armed logic, rolling window, VWAP filter
- `src/options/` — greeks, option_pricer, strike_selector, entry_logic
- `src/data/` — aggregator, databento_loader
- `src/backtest/portfolio.py` — cash flows, 100x multiplier
- `src/analysis/` — metrics, monte_carlo, visualize
- `main_runner/base_runner.py` — template method, IS/OOS, date validation

### Noted (Not Fixed — Low Priority)

- **Live engine high/low always equal close** (`live_engine.py:108`): `_check_exits` passes close for both high and low. Dormant since only options mode is used live, but would matter for equity mode.
- **numpy.datetime64 compatibility in dte_years**: `bar.timestamp` (numpy.datetime64) subtracted from `datetime` relies on pandas auto-conversion. Works today but fragile across numpy/pandas version upgrades.
