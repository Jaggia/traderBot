---
tags: [logging, error-handling, standards]
---
# Decision 007 — Logging and Error Handling Standard

**Date:** 2026-03-01
**Log entry:** none (refactor session, no narrative log written)

---

## Context

The codebase grew entirely on `print()` for all status output and had two silent exception swallowers:

- `src/backtest/engine.py` — `except Exception: print(...) → BS fallback` (hid Databento API errors silently)
- `src/data/databento_loader.py` — `except Exception: print(...) → return pd.DataFrame()` (returned empty data on API failure, letting the backtest run on zero option prices)

Entry points had no top-level error handling, so any crash produced a raw Python traceback with no clean exit code. CLI args were not validated, so passing a bad date silently propagated into pandas and exploded deep in the stack.

---

## Decision

Adopt the Python `logging` module project-wide, fix all broad exception catches to re-raise, add top-level handlers to all entry points, and validate CLI args at the boundary.

---

## Rules (enforced in CLAUDE.md)

1. **Every module** adds `logger = logging.getLogger(__name__)` at the top. No bare `print()` for diagnostics.

2. **`print()` is only allowed** in `print_metrics()` and similar intentional tabular/interactive displays. All other output goes through the logger.

3. **Entry points** (`main_runner/*.py`, batch scripts in `scripts_py/`) must:
   - Call `setup_logging()` from `src/utils/logging_config.py` as the first thing in `__main__`
   - Wrap `main()` in `try/except KeyboardInterrupt + Exception` — log the error with `exc_info=True` and `sys.exit(1)`

4. **No silent exception swallowing.** The pattern `except Exception as e: log; return empty` is banned. Use `logger.error(...); raise` and let the caller decide.
   - Exception: batch/loop scripts (e.g. `download_options_databento.py`) may catch per-item errors, log them, and `continue` — but must count and report errors in the summary.

5. **CLI date/path args** are validated before any work begins. Bad input → `logger.error(...)` + `sys.exit(1)`.

---

## Infrastructure added

- `src/utils/__init__.py` — makes `src/utils` a package
- `src/utils/logging_config.py` — `setup_logging()`: single stdout handler, format `HH:MM:SS [LEVEL] module: message`

---

## Files changed in this session

`src/backtest/engine.py`, `src/data/databento_loader.py`, `src/data/alpaca_loader.py`,
`src/analysis/metrics.py`, `src/analysis/monte_carlo.py`,
`main_runner/run_backtest_db.py`, `main_runner/run_backtest_with_alpaca.py`,
`main_runner/run_backtest_tv.py`, `main_runner/run_monte_carlo.py`,
`scripts_py/download_and_aggregate_databento.py`, `scripts_py/download_options_databento.py`
