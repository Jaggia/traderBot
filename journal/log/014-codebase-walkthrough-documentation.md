---
tags: [documentation, code-walkthrough, concepts]
---
# 014 — Codebase Walkthrough Documentation

**Date:** 2026-03-17

## What

Created 5 concept docs that trace actual code execution from shell script invocation to final `results/` output:

1. **`concepts/code-walkthrough.md`** — Main onboarding doc. Follows the Databento path through 7 phases, citing every function, file, and class. Includes a pipeline overview diagram, complete file map, and annotated config reference.

2. **`concepts/data-loading-deep-dive.md`** — Three loaders (Databento, Alpaca, TradingView), the 1m→5m aggregator, options caching, timezone handling, and the common output contract they all return.

3. **`concepts/engine-loop-deep-dive.md`** — Numpy array extraction, the exits→entries→MTM bar loop, equity vs options exit cascades, position sizing, and mark-to-market accounting.

4. **`concepts/options-pipeline-deep-dive.md`** — Strike selection (ATM/ITM/OTM/target_delta), OCC symbol construction, Black-Scholes Greeks, pricing fallback chain, 100x multiplier, and exit priority.

5. **`concepts/analytics-deep-dive.md`** — Metrics computation (Sharpe/Sortino on monthly returns matching TradingView), visualization functions, and Monte Carlo bootstrap methodology.

## Why

The existing concept docs (`system-overview.md`, `smi-williams-r-vwap.md`) explain *what* modules do but don't trace *how code actually flows*. A newcomer could read the overview and understand the architecture, but couldn't follow a function call chain through the source. This gap made onboarding harder — you had to grep around to connect the pieces yourself.

## Design Decisions

- **Split main + deep dives** instead of one 2000+ line monolith. The main walkthrough uses callouts like `> Deep dive: [filename](filename)` at handoff points.
- **Traced the Databento path** in detail (it's the default and most complex). Alpaca and TV differ only in `load_data()` — covered via comparison tables.
- **Real code signatures**, not pseudocode — keeps docs auditable against the source.
- **Kept `system-overview.md`** as a quick-reference cheat sheet. The walkthrough is for onboarding; the overview is for reminders.
