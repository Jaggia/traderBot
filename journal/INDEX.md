---
tags: [index, navigation, table-of-contents, overview]
---
# Journal Index

## How This Is Organised

**`log/`** — chronological dev log. Narrative "blog post" entries written per session. The story of how the project was built. Read these to understand *why* things evolved the way they did.

**`decisions/`** — distilled rationale docs. Structured summaries of key design choices, extracted from the log. Good for quickly answering "why did we do X?"

**`runbooks/`** — step-by-step operational guides. How to actually run things. Updated when the code changes.

**`concepts/`** — reference and educational docs. What things are, how they work.

**`tutorials/`** — end-to-end setup guides. Walk through configuring external dependencies (brokers, data providers) from scratch to a working state.

**`docs/`** — living state documents read by agents (_state.md, _modules.md). Always current.

---

## Dev Log (`log/`)

Original numbered entries — narrative, chronological, preserves the full story.

| # | File | Date | Summary |
|---|---|---|---|
| 001 | `log/001-armed-mode-and-signal-alignment.md` | 2026-02-15 | Armed/non-armed signal modes; Pine Script ↔ Python logic alignment; VWAP checked at fire time not arm time |
| 002 | `log/002-how-it-works.md` | 2026-02-15 | Full system overview: indicators (SMI, W%R), lookforward modes, armed mode, VWAP filter, backtest engine, data sources, output |
| 003 | `log/003-databento-1min-aggregator.md` | 2026-02-15 | 1-min → 5-min OHLCV aggregation; validation against native Alpaca 5-min; filter to 09:30–15:55; drop incomplete bars |
| 004 | `log/004-databento-consolidation-and-unified-pipeline.md` | 2026-02-15 | Unified download + aggregate script; Databento data directory structure; removed redundant wrapper scripts |
| 005 | `log/005-options-predownload-and-results-restructure.md` | 2026-02-23 | Options cache pre-download script; results dir restructured to `{source}/{Month-DD-YYYY}/{mode}/{timeframe}/`; dashboard Plotly interactive charts |
| 006 | `log/006-monte-carlo-simulation.md` | 2026-02-28 | Trade-bootstrap MC simulation; percentile bands on equity curves; OTM vs ITM analysis; 71% expiration-worthless finding |
| 007 | `log/007-strategy-explained.md` | 2026-02-28 | Beginner-friendly explanation of SMI, Williams %R, armed mode signal flow, and VWAP as a directional filter |
| 008 | `log/008-live-runner-db-alpaca-paper.md` | 2026-02-28 | Live runner: Databento streaming → Alpaca paper options orders; OCC format; run instructions; IBKR vs Polygon comparison |
| 009 | `log/009-entry-exit-logic-test-coverage.md` | 2026-03-12 | 29 tests for `entry_logic.build_option_position` and `exit_rules.check_option_exit`; completes unit coverage for `src/options/` |
| 010 | `log/010-live-module-test-coverage.md` | 2026-03-12 | 52 tests covering `src/live/` (alpaca_trader, databento_streamer, live_engine); SDK and signal pipeline mocked; total suite 291 tests |
| 011 | `log/011-remaining-test-coverage-gaps.md` | 2026-03-13 | 23 new tests closing 4 gaps: engine options mode end-to-end, DatabentoOptionsLoader cache/retry, alpaca download functions, logging_config; total suite 314 tests |
| 012 | `log/012-strategy-pattern-engine-unification-oos-split.md` | 2026-03-13 | Indicator Strategy Pattern (Indicator ABC + concrete classes); engine + live_engine wired to shared build_option_position/check_option_exit; IS/OOS split via is_fraction config; results dir naming updated |
| 013 | `log/013-test-suite-quality-audit-and-fixes.md` | 2026-03-13 | Test quality audit: added end-to-end integration test (22 tests, no mocks); Greek range assertions; expiration exit in engine loop; VWAP filter boundary coverage; MC divide-by-zero warning fixed; 341 tests total |
| 014 | `log/014-codebase-walkthrough-documentation.md` | 2026-03-17 | 5 concept docs tracing full code execution from shell script to results: main walkthrough + data loading, engine loop, options pipeline, analytics deep dives |
| 015 | `log/015-tv-validation-put-bugfix-benchmarks.md` | 2026-03-21 | TV validation skill + SMI/WR cross-validation vs TTI; put strike selection bugfix; options A/B tests (VWAP = primary edge); buy-and-hold benchmark |
| 016 | `log/016-edge-case-hardening.md` | 2026-03-22 | Guards + missing tests from full audit: greeks ATM-at-expiry delta fix, S/K validation, WR zero-range inf, VWAP missing-column error, alpaca month-boundary bug; 391 tests total |
| 017 | `log/017-bugfixes-and-live-hardening.md` | 2026-03-23 | 4-agent bug sweep: dte_years intraday fix, update_price bypass, zero-price guard; live engine: intrabar polling + crash recovery; P&L validation aligned to always-long model; 660 tests |
| 018 | `log/018-eod-close-and-sigma-fixes.md` | 2026-03-23 | Cherry-picked from stale branch: EOD close logic fix (hour>15 case), BS fallback sigma reads from config; full module review — clean |
| 019 | `log/019-comprehensive-bug-audit-and-fixes.md` | 2026-03-25 | Full 70-file line-by-line audit (9 parallel agents); 7 bugs fixed: put intrabar stop/limit, options loader UTC/EST, live streamer reconnect, live engine zombie + thread bugs, EOD entry bypass, implicit margin, warmup_start crash; 714 tests |
| 020 | `log/020-backtest-accuracy-overhaul-and-full-cleanup.md` | 2026-03-26 | 3 accuracy fixes (next-bar-open, IB costs, per-position IV); all 11 Low bugs fixed; all 5 nitpick items fixed; 8 test suite quality fixes; 738 tests. Jan–Mar 2026 backtest: +$464, profit factor 1.49, outperformed buy-and-hold by $8,749 |
| 021 | `log/021-ibkr-live-integration.md` | 2026-04-02 | IBKR live data + paper trading: IBKRStreamer, IBKRTrader, run_live_ibkr.py, shell script; broker-agnostic engine fix (get_order_status interface); 50 new tests |
| 022 | `log/022-ibkr-code-review-fixes.md` | 2026-04-02 | PR code review hardening: dead fallback removed, KI re-raise fix, cancelMktData finally, constructor error handling, logger.warning, NaN sentinel, BrokerProtocol; +12 tests → 800 total |
| 023 | `log/023-ibkr-sunday-prep-and-data-logging.md` | 2026-04-05 | IBKR live runner tested successfully; added CSV logging for live bars + closed trades; concluded IBKR cannot replace Databento for backtesting (API rate limits, missing options data); final architecture: Databento (backtest), IBKR (live paper trading) |
| 024 | `log/024-test-coverage-gaps-documented.md` | 2026-04-05 | Six previously undocumented test files catalogued: trade_logic intrabar exits, EMA 233 pipeline, compute_ema unit tests, BaseBacktestRunner IS/OOS split, visualize smoke tests, and regression fixture |
| 025 | `log/025-testing-standards-bug-fixes-and-skills.md` | 2026-04-05 | Journal audit; RG-TDD standards in CLAUDE.md + rg-tdd skill; 8 round-1 bugs fixed; 3 of 6 round-2 bugs already fixed, 3 newly fixed; /test-gotcha-review skill; 910 tests total |
| 026 | `log/026-generic-armed-mode-system.md` | 2026-04-11 | System 3: generic armed-mode pipeline; RSI + MACD indicators; state machine primitives extracted to shared module; symbol parameterisation in streamers/loader |
| 027 | `log/027-metrics-refactor-and-vwap-test.md` | 2026-04-12 | Indicator primitives refactor (base.py); Trade-level Sharpe + PSR metrics; time and resampling helpers; VWAP manual-calculation verification test |
| 028 | `log/028-data-provider.md` | 2026-05-01 | Unified data provider Protocol (Ports & Adapters); three concrete providers; simplified runners to config-only classes |
| 029 | `log/029-deflated-sharpe-ratio.md` | 2026-05-01 | Deflated Sharpe Ratio (DSR) — PSR corrected for multiple testing bias; Acklam inverse CDF; count_trials from run_key.yaml |
| — | `log/bug-analysis-report.md` | 2026-03-24 | Full line-by-line audit of 70+ files: 43 issues catalogued across Critical/High/Medium/Low severity — option sizing, profit-factor crash, dashboard discovery, IS/OOS boundary, intrabar options exits, and more |

---

## Decisions

| File | Summary |
|---|---|
| `decisions/001-armed-mode-design.md` | Armed mode rationale; VWAP at fire not arm time; window expiry before fire; naming convention fix |
| `decisions/002-databento-aggregator.md` | Why we built 1m→5m aggregation; 100% validation against Alpaca; key resample decisions |
| `decisions/003-options-pipeline-design.md` | Results directory nested structure; options pre-download to avoid API calls during backtest |
| `decisions/004-monte-carlo-method.md` | Why trade-level not bar-level bootstrap; what percentiles mean; OTM vs ITM findings; 71% expiration-worthless problem |
| `decisions/005-live-runner-architecture.md` | Why Databento + Alpaca split; warmup from cache; signal pipeline reuse; V1 exit design |
| `decisions/006-data-provider-comparison.md` | Databento vs Polygon.io vs IBKR: cost, complexity, verdict (keep DB+Alpaca now, move to IBKR for live) |
| `decisions/007-logging-and-error-handling.md` | Project-wide logging standard: `logging` module everywhere, no silent swallows, top-level handlers on all entry points, CLI validation |
| `decisions/008-backtest-accuracy-standards.md` | Three industry-standard accuracy rules: next-bar-open fill, realistic IB costs ($0.65 + $0.10/contract), per-position implied vol back-solved at entry |

## Runbooks

| File | Summary |
|---|---|
| `runbooks/run-backtest.md` | Run backtests across all data sources; output locations; pre-downloading options; config knobs |
| `runbooks/run-live-trading.md` | Start live paper trading on Monday; what happens at startup; stopping; EOD behaviour |
| `runbooks/download-databento-data.md` | Download + aggregate equity bars; validate aggregator; pre-download options contracts |
| `runbooks/run-monte-carlo.md` | Run MC on existing results or inline after backtest; reading percentile output |

## Concepts

| File | Summary |
|---|---|
| `concepts/00-system-overview.md` | Full system map: modules, data flow, backtest engine design, data sources, output structure |
| `concepts/01-smi-williams-r-vwap.md` | How SMI, Williams %R, and VWAP work individually and how they combine in armed mode |
| `concepts/02-code-walkthrough.md` | Step-by-step code trace from shell script to results: 7 phases, every function/file cited |
| `concepts/03-data-loading-deep-dive.md` | Three loaders, 1m→5m aggregator, options cache, timezone handling, common output contract |
| `concepts/04-engine-loop-deep-dive.md` | Numpy extraction, bar-by-bar loop, exit cascades (equities vs options), position sizing, MTM |
| `concepts/05-options-pipeline-deep-dive.md` | Strike selection, OCC symbols, Black-Scholes Greeks, pricing fallback, 100x multiplier |
| `concepts/06-analytics-deep-dive.md` | Metrics (Sharpe/Sortino on monthly returns), visualization, Monte Carlo bootstrap |
| `concepts/07-occ-symbol-format.md` | OCC/OSI symbol anatomy, strike encoding, root padding, Alpaca's spaceless variant |
| `concepts/08-backtester-validation.md` | External validation: Greeks vs py_vollib (139 tests), P&L vs LambdaClass backtester (19 tests), convention alignment |
| `concepts/09-ema-233-signal-system.md` | System 2: 233 EMA intrabar cross on 15-min bars — signal logic, fill price hint, timeframe mechanics, config, comparison to System 1 |
| `concepts/10-armed-mode-generic-system.md` | System 3: generic armed-mode pipeline — configurable indicator pairs, event types, short-side threshold overrides, RSI+MACD default config, code locations |
| `concepts/DESIGN_PATTERNS_GUIDE.md` | Patterns used throughout the codebase: Template Method (BaseRunner), Adapter/Protocol (broker interface), GoF Strategy (signal systems), Functional Pipeline, Shared Domain Logic, Strategy-by-Configuration, Lazy Initialization |

## Tutorials (`tutorials/`)

| File | Summary |
|---|---|
| `tutorials/ibkr-streaming-setup.md` | End-to-end setup: IB Gateway install, API socket config, data subscriptions (~$4.50/mo), config verification, and running the IBKR live runner |
| `tutorials/monte_carlo/` | **Monte Carlo Crash Course** (5-part series): intuition & thought experiments, bootstrap math & formulas, quant applications (10 use cases), pitfalls & limitations, code walkthrough & decision framework |
| `tutorials/hmm/` | **Hidden Markov Model Crash Course** (5-part series): regime intuition & the "market has moods" model, the three HMM components & Forward/Viterbi/Baum-Welch math, quant applications (signal gating, vol filtering, sizing, analytics), pitfalls (look-ahead bias, overfitting, non-stationarity), and a full blueprint for wiring an HMM regime filter into this codebase |

## Reference Docs (`docs/`)

| File | Summary |
|---|---|
| `docs/_state.md` | Current config values, active TODO items, live runner details, signal mode settings |
| `docs/_modules.md` | Module-by-module implementation notes (engine, strike selector, metrics, dashboard, scripts) |
