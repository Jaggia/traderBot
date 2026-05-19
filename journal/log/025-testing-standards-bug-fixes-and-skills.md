---
tags: [testing, standards, tdd, bugfixes, skills]
---
# 025 — Testing Standards, RG-TDD, 8+3 Bug Fixes, and Skills

**Date:** 2026-04-05
**Tests:** 910 passing (up from 800)
**Commits:** `92de3fd..2b5bbe6`

---

## What happened this session

### 1. Journal audit and documentation sync

Full audit of `journal/` against the current codebase to catch stale references and unlisted files:

- `_state.md`: Removed false claim that the Strategy Pattern was removed — `src/signals/strategy.py` exists and is active. Added `## Signal Strategy Pattern` section documenting the ABC, two concrete classes, and `create_strategy()` factory.
- `_state.md`: Fixed broken pointer `TODO.md` → `TODOs/TODO_WIP.md` and `TODOs/TODO_DONE.md`.
- `_modules.md`: Added entries for `src/signals/strategy.py` and `src/backtest/trade_logic.py` (both previously unlisted).
- `journal/INDEX.md`: Added `bug-analysis-report.md` and `DESIGN_PATTERNS_GUIDE.md` to the tables; added log 024.
- `journal/log/024-test-coverage-gaps-documented.md`: New entry cataloguing 6 previously undocumented test files.
- `journal/log/bug-analysis-report.md`: Fixed stale `smi_wr_generator` → `smi_wr_pipeline` references (2 locations).
- `CLAUDE.md`: Updated Key Modules table (added `ema.py`, `ema_pipeline.py`, `strategy.py`, `monte_carlo.py`, IBKR live modules; renamed `smi_wr_generator` → `smi_wr_pipeline`; expanded `trade_logic.py` description to include `ExitResult`). Updated Live Paper Trading section to cover both runners.
- `README.md`: Added `run_live_ibkr.py` to entry points.
- `QUICKSTART.md`: Expanded to reflect current commands and data sources.

### 2. Testing standards formalized

Added `## Testing Standards` section to `CLAUDE.md` — now required for all new features and bug fixes:

- Mandatory RG-TDD workflow (red before green, no exceptions)
- Required coverage: happy path + edge case + error/exception path
- No `print()` in tests — use `caplog`
- Test file naming mirrors `src/` structure under `tests/`
- Three test categories: pure functions (no fixtures), stateful engine (fixture DataFrames), external dependencies (mocks/monkeypatching)

Created `.claude/skills/skills/rg-tdd/skill.md`: 8-step workflow skill with templates for all three test categories, logging assertion pattern, and commit format. Invokable via `/rg-tdd`.

### 3. Round 1 bug fixes — 8 bugs, all via RG-TDD

All fixes on `main` branch. Tests written first (red), then minimum implementation (green), committed together.

| Bug | Module | Fix | Test class |
|-----|--------|-----|------------|
| Final-bar signal silently dropped | `engine.py` | Logs `WARNING` when `pending_entry` dropped on last bar | `TestFinalBarSignalWarning` |
| Monte Carlo silent skip on < 5 trades | `monte_carlo.py` | Raises `ValueError` | `TestRunMonteCarloInsufficientTrades` |
| `implied_vol()` bisection silent failure | `option_pricer.py` | Raises `ValueError` when market price outside BS range | `TestImpliedVolBisectionFailure` |
| Options double-slippage in `_transaction_cost()` | `portfolio.py` | Branch on mode — not additive | `TestOptionsSlippageCostModel` |
| EMA 15-min bar misfire on data gaps | `ema_pipeline.py` | `ts.floor("15min") != (ts + 5min).floor("15min")` | `TestIdentify15mCloseBarsGapRobustness` |
| `implied_vol()` T=0 silent config fallback | `option_pricer.py` | Logs `WARNING` when `T <= 0` | `TestImpliedVolAtExpiry` |
| `put_call` normalization no validation | `databento_loader.py` | Validates `{"C","P"}`, raises on unexpected | `test_data_loaders.py` |
| EOD 15:55 hardcoded | `engine.py`, `trade_logic.py` | `_is_eod()` helper + `eod_cutoff_time` in config | `TestConfigurableEodCutoff` |

Config addition: `backtest.eod_cutoff_time: "15:55"` in `strategy_params.yaml`.

### 4. Round 2 bug fixes — 6 bugs from bug-analysis-report

Three were already fixed in prior sessions (confirmed via RG-TDD: tests passed immediately on a supposedly-broken codebase):

| Bug | Status | Note |
|-----|--------|------|
| `round(float('inf'))` crash in `compute_metrics()` | Already fixed (`99473b5`) | `min(pf, 999.99)` guard existed |
| IS/OOS equity curves overlap at split boundary | Already fixed (`cddd3f7`) | Regression tests pinned as `test_split_boundary_bar_appears_in_oos_only` |
| Option exits don't use intrabar high/low | Already fixed (`cddd3f7`) | Tests existed and passed |

Three were newly fixed:

| Bug | Module | Fix | Test |
|-----|--------|-----|------|
| `discover_runs()` never matches folder names | `dashboard.py` | Regex `(?:^|[_-])([A-Z][a-z]+-\d{2}-\d{4})(?:$|[_-])` extracts date token from compound names | `tests/test_dashboard.py` (3 tests) |
| IS/OOS boundary had no regression coverage | `base_runner.py` | Wrote regression tests confirming fixes hold | `test_split_boundary_bar_appears_in_oos_only`, `test_oos_start_snapped_to_next_bar` |
| Stale option prices via `get_indexer(nearest)` | `engine.py` | `_get_option_price()` returns `None` + logs `WARNING` when gap > threshold; configurable via `config["data"]["max_option_staleness_minutes"]` (default 25 min) | `TestOptionPriceStaleness` |

### 5. `/test-gotcha-review` skill

Created `.claude/skills/skills/test-gotcha-review/skill.md` — a pre-commit test quality checklist built from 12 specific failure modes encountered during this session:

1. Patch target path (definition vs usage site)
2. Tautology mock asserts (no output assertion)
3. Dead engine test (`engine = BacktestEngine(...)` unused)
4. Stale data: old `RuntimeError` pattern after behavior changed to `None + WARNING`
5. EOD cutoff tests only verify the default, not the configurable override
6. Slippage modes not tested as exclusive (both-params-set case)
7. Monte Carlo boundary: `n=5` pass case missing
8. EMA 15-min: no DST or mid-session gap scenario
9. `caplog` assertions too broad (no level scoping)
10. `put_call` normalization: mixed-case roundtrip not tested
11. Staleness threshold config override not tested
12. Env-coupled data tests reading real `data/` files

The skill is designed as a living checklist — it explicitly instructs Claude to append new gotchas when they're discovered during future reviews.

---

## Test suite growth

| Session | Tests |
|---------|-------|
| Start of session (journal 023) | 800 |
| After round 1 fixes | ~870 |
| After round 2 fixes | 910 |

---

## Pylance note

Several tests in `test_engine.py` had `engine = BacktestEngine(...)` where the result was never used (constructor called only for mock side-effects). Pylance flagged these. Fixed by removing the assignment entirely. Pattern: when testing that a constructor triggers a mock call, just call `BacktestEngine(...)` without assignment.

---

## Still open

**Testing TODOs (from TODO_WIP.md):**
- VWAP indicator unit test (hand-calculated values)
- Options end-to-end integration test
- Good Friday / holiday expiry roll test for `get_target_expiry`
- Cross-source validation test

**Medium/low bugs still open** — 14 medium, 3 low in `bug-analysis-report.md`. No new critical bugs identified this session.

**Pinned idea:** User mentioned a test-related idea before round 1 bug fixes began — pinned for next session.
