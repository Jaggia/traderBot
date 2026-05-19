---
tags: [tradingview, validation, put-bugfix, benchmarks, vwap]
---
# Session 015 — TV Validation Skill, Put Strike Bugfix, Buy & Hold Benchmark

Date: 2026-03-21

## What Changed

### TV Backtest Validation Skill
- Created `.claude/skills/skills/validate-tv-backtest/skill.md` — a reusable Claude skill that takes a TradingView screenshot, downloads TV data, runs a matching backtest, and generates a comparison report
- Validated SYMBOL equity backtest (Dec 15 2025 – Mar 20 2026) against a TV screenshot: **$11 / 0.9% P&L difference** — PASS verdict
- Saved validation report in `results/tv/.../tv_validation/validation_report.md`

### Cross-Validation Tests (SMI + Williams %R vs TTI)
- Cloned `trading-technical-indicators` (TTI) library into `claudeCoding/` for independent cross-validation
- Created `tests/test_indicators_vs_tti.py` with 14 tests:
  - Williams %R: exact match (< 1e-10 tolerance) across 3 periods + edge values
  - SMI: convergence after warm-up. EMA seeding differs (`min_periods=0` in ours matching Pine Script vs `min_periods=period` in TTI) but decays exponentially — no trading impact due to 3-month warm-up
  - Both indicators tested on real TV CSV data

### Put Strike Selection Bug (Fixed)
- Discovered that `1_OTM` / `1_ITM` labels were **inverted for puts** — `1_OTM` put was selecting a higher strike (actually ITM), `1_ITM` put was selecting lower (actually OTM)
- Root cause: `strike_selector.py:125` had `atm + offset` branch for puts, but the offset signs (lines 117-123) already encode put direction correctly. The `+ offset` caused double-negation
- Fix: always `strike = atm_strike - offset` (offset signs do all the work)
- Added 4 put-side tests to `tests/options/test_options_pricing.py`
- User observation: "feels concerning it wasn't caught earlier" — all prior tests only tested calls (`option_type="C"` default). No TV validation was ever run for options (equities only)

### Options Backtest Experiments (2026 0-DTE)
- Ran multiple DB options backtests with different configs to isolate the effect of `eod_close` and `vwap_filter`:
  - `eod_close: true` + `vwap_filter: true` → profitable configuration, solid Sharpe
  - `eod_close: true` + `vwap_filter: false` → significantly worse — the VWAP filter proved to be load-bearing for this period
  - `eod_close: false` + `vwap_filter: true` → higher raw return but worse drawdown and Sharpe
- Config updated: `eod_close` default set to `true`, `sizing_mode` to `"fixed"`, added comment explaining why `percent_of_equity` is misleading for options

### Buy & Hold Benchmark (Uncommitted)
- Added `compute_buy_hold_benchmark()` to `src/analysis/metrics.py` — matches TradingView's methodology (fractional shares at first bar close, no commission)
- Added `print_benchmark()` for console output
- Updated `save_report_md()` to include benchmark comparison section
- Integrated into `base_runner.py` — every backtest now shows strategy vs buy-and-hold

## Why

- **TV validation skill**: Eliminates manual screenshot comparison; can be invoked with a single command + image path
- **TTI cross-validation**: Our indicators were only validated against TV screenshots (visual). TTI provides a programmatic second opinion. Williams %R is exact; SMI matches after warm-up
- **Put strike fix**: Any options backtest with put trades was using wrong strikes. Since we mostly run equities-mode TV validation, this went undetected. Test-writing surfaced a real code bug
- **VWAP edge confirmed**: The A/B test proves the strategy has no edge without VWAP filtering — counter-trend signals are extremely costly with 0-DTE options (100x multiplier amplifies losses)
- **Buy & hold benchmark**: TradingView always shows this; having it in our reports makes comparison easier

## Key Details

- EMA seeding difference between our code and TTI is **not a bug** — our `min_periods=0` matches Pine Script's `ta.ema()` behavior. TTI uses `min_periods=smoothing_period`. After `period + 4*max(smooth1, smooth2)` warm-up bars, both converge to < 0.01 tolerance
- The put strike fix changed one line but affects every put trade historically. All prior options backtest results with puts are invalid
- `percent_of_equity` sizing for options computes `(equity * pct) / underlying_price` — gives share-equivalent contracts, not premium-controlled. Each contract controls 100 shares, so actual exposure is much higher than `sizing_pct` suggests
