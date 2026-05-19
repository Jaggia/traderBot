---
tags: [accuracy, next-bar-open, costs, implied-vol, cleanup]
---
# 020 — Backtest Accuracy Overhaul, Full Bug Sweep, and Cleanup

**Date:** 2026-03-26

## What Happened

Two phases in one session:

1. **Backtest accuracy audit** — identified 3 industry-standard issues and fixed all of them (next-bar-open entry, realistic costs, per-position implied vol).
2. **Full remaining bug sweep** — knocked out all 11 remaining Low bugs, all 5 tracked Nitpick items, and 8 test suite quality issues. Zero remaining open items.

Result: **738/738 tests passing** (up from 714).

---

## Phase 1 — Backtest Accuracy Fixes

### A-1 — Next-bar-open entry fill (`src/backtest/engine.py`)

**Problem:** Entry was executing at `bar[i].close` — the same bar where the signal was detected. This is close-of-bar lookahead bias: you can only *know* the bar closed with a signal *after* it closes, so you can't trade at that same close. Industry standard is next-bar-open fill.

**Fix:** Added a `pending_entry` buffer at the engine level. When a signal fires at bar `i`, it's stored as `pending_entry = (signal, bar)`. At the top of bar `i+1`'s loop iteration, the pending entry is consumed and filled at `opens[i+1]`. The entry bar's `BarContext` is constructed with `opens[i]` as both `close` and the effective fill price, so stop/limit levels are calculated correctly from the actual fill.

The `opens` array was added to the pre-extracted numpy arrays. The EOD guard was also replicated in the pending-entry execution path so signals near market close are correctly discarded.

### A-2 — Realistic transaction costs (`config/strategy_params.yaml`, `src/backtest/portfolio.py`)

**Problem:** Both commission and slippage were set to 0. A backtest with zero costs is not representative of real trading — commissions compound on every trade and bid-ask spread costs are real.

**Fix:**
- `commission_per_contract: 0.65` — Interactive Brokers standard for SYMBOL options.
- `slippage_per_contract: 0.10` — flat per-contract slippage modelling the 0-DTE SYMBOL bid-ask spread. (Not percentage-based — percentage is proportional to price, which over-penalizes cheap OTM options.)
- `Portfolio._transaction_cost()` updated to sum commission + percentage slippage (legacy) + flat slippage. Round-trip cost: ~$1.50/contract ($0.65 + $0.10 each way).

### A-3 — Per-position implied vol (`src/options/option_pricer.py`, `src/options/entry_logic.py`, `src/options/position.py`, `src/backtest/engine.py`, `src/backtest/trade_logic.py`)

**Problem:** All Black-Scholes calculations — at entry, during intrabar stop/limit checks, and in mark-to-market — used a hardcoded `sigma=0.25`. When Databento market data is unavailable (BS fallback), this produced prices inconsistent with each other: entry IV ≠ intrabar IV ≠ MTM IV.

**Fix:**
- Added `implied_vol()` bisection function to `option_pricer.py`. It back-solves IV from the observed market price at entry using Brent's method on the Black-Scholes formula.
- Added `entry_iv: Optional[float]` field to `Position` dataclass.
- `build_option_position()` in `entry_logic.py` now back-solves IV at entry, stores as `position.entry_iv`, and uses it for Greeks computation. Falls back to config sigma if back-solve fails (negative prices, boundary cases).
- All subsequent `get_option_price()` calls in `trade_logic.py` pass `sigma=pos.entry_iv` via `**_iv_kw` kwargs, threading the per-position vol through every BS call for that position's lifetime.

---

## Phase 2 — Remaining Low Bugs

### Engine / Portfolio / Metrics (L-1 to L-5)

| Bug | Fix |
|-----|-----|
| **L-1** Equity curve final point missing realized P&L | Added `portfolio.mark_to_market(last_ts)` after the `backtest_end` close loop |
| **L-2** `get_equity_df()` crashes on empty curve | Added early-return guard returning empty DataFrame with correct columns |
| **L-3** Silent empty backtest when `trade_start` beyond data | Added `logger.warning` when `trade_start_idx >= total_bars` |
| **L-4** No equity baseline before first bar | Added `portfolio.record_initial_equity()` method; called before main loop at `trade_start_idx` timestamp |
| **L-5** Sortino/Sharpe guard inconsistency | Both now guard `len(returns) < 2` (Sortino was `== 0`) |

### Options / Data / Scripts (L-6 to L-16)

| Bug | Fix |
|-----|-----|
| **L-6** `check_option_exit` recomputed P&L inline | Replaced with `pos.pnl_pct()` call |
| **L-7** `target_delta` strike search only ±20 from ATM | Widened to ±50 strikes |
| **L-12** `validate_aggregator.py` hardcoded `-30` day | Uses `calendar.monthrange()` for correct last day |
| **L-13** `armed_mode_comparison.py` crashes on missing TV CSV | Added `os.path.exists()` guard; TV skipped when absent |
| **L-14** `databento_loader` silently accepted invalid `option_type` | Added `.upper()` + `ValueError` for non-C/P inputs |
| **L-15** Alpaca loader used `keep="first"` vs TV's `keep="last"` | Changed to `keep="last"` for consistency |
| **L-16** `tv_qqq_5min.py` ran code on import; used `print()` | Wrapped in `main()` with `__main__` guard; `print` → `logger.info` |

### Live Engine (L-8 to L-11 — previously fixed)

Already fixed in an earlier session — confirmed closed.

---

## Phase 3 — Nitpick Cleanup

| Item | Fix |
|------|-----|
| Stale config comment (`sync_window: 20 # 6 = 30 min`) | Updated to `# 5-min bars; 20 bars = 100 minutes` |
| Env var inconsistency (`DATA_BENTO_PW` vs `DATABENTO_API_KEY`) | Both download scripts now check `DATA_BENTO_PW or DATABENTO_API_KEY` |
| `greeks.py` vega `/100` unexplained | Comment expanded: "per 1%-vol convention; differs from textbook ∂V/∂σ" |
| `compute_drawdown_pct` division by zero if equity hits 0 | Protected with `np.errstate` + `replace(0, np.nan)` |
| Missing metrics show `0.00%` in armed_mode_comparison | Added `_fmt_pct()` helper returning `"N/A"` for None values |

---

## Test Suite Updates

Three tests needed updates for the new engine semantics:

1. **`test_equity_curve_length_equals_bar_count`** — `record_initial_equity()` adds 1 entry before the loop; `mark_to_market()` after `backtest_end` adds 1 more. Updated assertion to `len(df) + 2`.

2. **`test_equity_mid_trade_reflects_unrealized_pnl`** — signal at bar[3] now fills at bar[4].open. The initial baseline entry at index 0 shifts all curve indices by 1 — bar[5]'s MTM is now at `equity_curve[6]`.

3. **`test_duplicates_dropped`** (Alpaca loader) — `keep="last"` change means last row wins; updated expected `open` value and comment.

---

## Backtest Results — Jan 2 → Mar 25 2026

Run immediately after all fixes to validate:

| Metric | Value |
|--------|-------|
| Trades | 72 |
| Win Rate | 45.8% |
| Profit Factor | 1.49 |
| Total P&L | +$464 (+0.31%) |
| Max Drawdown | -0.13% |
| Buy & Hold | -$8,285 (-5.52%) |
| Outperformance | **+$8,749** |

---

## Test Count

738 tests, all passing. Up from 714 before this session.
