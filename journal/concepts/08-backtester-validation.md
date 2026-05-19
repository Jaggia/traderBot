---
tags: [validation, greeks, py-vollib, lambda-class, external-validation]
---
# Backtester Validation: Cross-Checking Against External Libraries

How we verify the mathematically sensitive parts of the backtester against independent reference implementations.

---

## Why Validate Externally?

Unit tests catch regressions, but they can't tell you if the formula itself is wrong — you'd just be testing your own logic against your own logic. External validation answers a different question: **does our implementation agree with established, production-grade libraries?**

We validate two things directly:

1. **Pricing & Greeks** — does `compute_greeks()` agree with an industry-standard Black-Scholes library?
2. **P&L accounting** — do our realized P&L calculations match an independent options backtester once contract multiplier, trade direction, and costs are handled consistently?

---

## Validation 1: Greeks vs py_vollib

**Reference library:** [py_vollib](https://github.com/vollib/py_vollib) — built on Peter Jäckel's LetsBeRational, widely used in quantitative finance.

**Test file:** `tests/validation/test_greeks_vs_vollib.py`

### What's Tested

A matrix of 22 scenarios covering:

| Dimension | Values |
|-----------|--------|
| Moneyness | ATM, ITM, OTM, deep ITM (S=450/K=400), deep OTM (S=350/K=400) |
| Option type | Call, Put |
| Time to expiry | 1 hour (1/8760y), 1 day, 30 days, 90 days |
| Volatility | Low (0.15), Medium (0.25), High (0.50) |

For each scenario, five values are compared:
- **Price** — `black_scholes_price()` vs `py_vollib.black_scholes.black_scholes()`
- **Delta** — `compute_greeks()["delta"]` vs `py_vollib.black_scholes.greeks.analytical.delta()`
- **Gamma** — same pattern
- **Theta** — same pattern
- **Vega** — same pattern

Plus 7 edge-case tests:
- Deep ITM call delta ≈ 1.0
- Deep OTM call delta ≈ 0.0
- Put-call parity on price: `C - P = S - K·e^(-rT)`
- Put-call parity on delta: `Δ_call - Δ_put = 1`
- Call/put gamma equality
- Call/put vega equality
- Zero-rate ATM symmetry: `C = P` when `r = 0`

### Convention Alignment

Both libraries use the same conventions (verified empirically):

| Greek | Convention | Unit |
|-------|-----------|------|
| Price | Per-share option price | dollars |
| Delta | Standard BS delta | call: 0–1, put: -1–0 |
| Gamma | Standard BS gamma | per dollar |
| Theta | Per calendar day | ÷365 baked in |
| Vega | Per 1% vol move | ÷100 baked in |

No conversion factors needed — values match directly.

### Result

**139/139 tests pass.** Our `math.erf`-based normal CDF is numerically equivalent to py_vollib's implementation across all scenarios including extreme edge cases.

---

## Validation 2: P&L vs LambdaClass Options Portfolio Backtester

**Reference library:** [LambdaClass options_portfolio_backtester](https://github.com/lambdaclass/options_backtester) — a production-grade options backtester with a Rust core, Greeks-aware portfolio, and pluggable execution models.

**Test file:** `tests/validation/test_pnl_vs_lambdaclass.py`

**Reference repo (read-only):** `../git_sanity_check_deps/lambdaclass_backtester/`

### How P&L Differs Between the Two

**Our accounting** (explicit direction, plus configured costs in the portfolio layer):
```python
# Options:  (exit_price - entry_price) * contracts * 100 - entry_cost - exit_cost
# Equities: direction * (exit_notional - entry_notional) - entry_cost - exit_cost
```

**LambdaClass formula** (direction-agnostic, from `analytics/trade_log.py:35`):
```python
gross_pnl = (exit_price - entry_price) * quantity * shares_per_contract
```

They use a **single formula** for both long and short. The trick is in their Rust engine (`backtest.rs`), which encodes direction into cost signs:

| Action | Cost sign | Effect |
|--------|-----------|--------|
| Long entry (BTO) | `+price × 100` | Debit |
| Long exit (STC) | `-price × 100` | Credit |
| Short entry (STO) | `-price × 100` | Credit |
| Short exit (BTC) | `+price × 100` | Debit |

When you expand their formula with signed costs:
- **Long profit:** `(+exit - (+entry)) × qty × 100 = (exit - entry) × qty × 100` ✓
- **Short profit:** `(-exit - (-entry)) × qty × 100 = (entry - exit) × qty × 100` ✓

Both approaches are algebraically identical.

### What's Tested

1. **Synthetic trades** — 7 parametrized scenarios (long/short × profit/loss/breakeven/fractional prices) verifying our formula produces correct P&L
2. **Formula equivalence** — 6 scenarios verifying our formula and LambdaClass's (with signed costs applied) produce identical results
3. **Real backtest replay** — loads every `backtest.csv` in `results/` (options only), recomputes P&L for every trade, and compares against the recorded `pnl` column

### One Difference: Transaction Costs

LambdaClass supports pluggable cost models:
- `NoCosts` — zero commissions
- `PerContractCommission` — fixed $0.65/contract (IBKR-style)
- `TieredCommission` — volume-based tiering
- `SpreadSlippage` — bid-ask spread fraction

Our codebase now models costs in the main portfolio/accounting path via `commission_per_contract`, `slippage_pct`, and `slippage_per_contract`. The validation doc here is about formula equivalence; whether a specific backtest is gross or net depends on the config used in that run.

### Result

The validation suite replays historical backtest CSVs and checks that recomputed trade P&L matches the recorded results within tolerance.

---

## How to Run

```bash
# Greeks validation (requires py_vollib: pip install py_vollib)
pytest tests/validation/test_greeks_vs_vollib.py -v

# P&L validation (no external deps — formula is replicated inline)
pytest tests/validation/test_pnl_vs_lambdaclass.py -v

# Both
pytest tests/validation/ -v
```

---

## What This Doesn't Validate

- **Signal correctness** — whether SMI/WR signals are firing at the right bars (validated separately in `tests/test_indicators_vs_tti.py` against the TTI library)
- **Options market data accuracy** — whether Databento's option prices are correct
- **Engine loop logic** — whether exits fire at the right bar (covered by `tests/test_integration.py` end-to-end tests)
- **Execution slippage realism** — whether configured slippage matches live fills (would need live trading data to validate)
