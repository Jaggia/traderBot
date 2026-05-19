---
tags: [accuracy, next-bar-open, costs, implied-vol, standards]
---
# 008 — Backtest Accuracy Standards

**Session:** 020 (2026-03-26)

## The Three Industry-Standard Rules We Implemented

### 1. Next-bar-open fill (no lookahead bias)

**Rule:** A signal detected on bar `i`'s close must fill at bar `i+1`'s open.

**Why:** You can only know a bar closed with a valid signal *after* the bar closes. You cannot trade at the same close that triggered the signal — by the time you act, the price has already moved. Filling at bar `i`'s close is lookahead bias: you're implicitly trading on information you didn't yet have.

**Implementation:** `pending_entry` buffer in the engine. Signal stored on bar `i`, consumed at bar `i+1`'s open. The `BarContext` passed to `build_entry` uses `opens[i+1]` as the effective price.

**Effect:** Every trade entry is ~1 bar later. Stop/limit levels shift accordingly. Net P&L impact was small in this strategy (fill price rarely diverges much at the 5-min scale) but the *correctness* is fundamental — any strategy that survives only with same-bar fills should be viewed with suspicion.

---

### 2. Realistic costs

**Rule:** Always include round-trip commission and spread costs at realistic market rates.

**Why:** Zero-cost backtests systematically overstate performance. For 0-DTE SYMBOL options:
- IB charges $0.65/contract (standard retail).
- Bid-ask spread on near-ATM 0-DTE SYMBOL options is ~$0.10–$0.20. We model $0.10/contract flat (conservative).
- Round-trip = ~$1.50/contract. With 1 contract per trade and 72 trades/period, this is ~$108 in costs — meaningful against ~$464 gross P&L.

**Why flat, not percentage slippage:** Percentage slippage penalizes cheap OTM options disproportionately (a $0.05 option with 1% slippage = $0.05 — impossible when the whole premium is $0.05). Flat per-contract cost better models the bid-ask reality.

**Config:**
```yaml
costs:
  commission_per_contract: 0.65
  slippage_pct: 0.0
  slippage_per_contract: 0.10
```

---

### 3. Per-position implied vol

**Rule:** When using Black-Scholes as a pricing fallback, the vol parameter must be consistent across a position's lifetime — entry, intrabar checks, and mark-to-market must all use the same IV.

**Why:** Using hardcoded `sigma=0.25` for all positions creates arbitrage artifacts. A position entered when realized IV was 0.40 will have its intrabar stop/target computed at 0.25, producing option prices inconsistent with what the market would show. Back-solving IV at entry from the market price anchors all subsequent BS calls to the actual observed vol.

**Implementation:** `implied_vol()` bisection at entry → stored as `position.entry_iv` → threaded as `sigma=pos.entry_iv` kwarg through all `get_option_price()` calls for that position. Falls back to config sigma if back-solve diverges.

**Note:** This only matters when Databento market data is unavailable (pure BS mode). When real market bars are used, BS is not called at all — the market price is used directly.

---

## When These Rules Matter Most

| Rule | Impact on short strategies | Impact on long strategies |
|------|---------------------------|--------------------------|
| Next-bar-open | Low (5-min open ≈ prior close) | Low but non-zero on gappy opens |
| Realistic costs | High for high-frequency options strategies | Low for long-horizon equity strategies |
| Per-position IV | Medium when BS fallback used | Not applicable (equities don't use BS) |

For this strategy (0-DTE, ~1 trade/day, options), all three rules are relevant. The biggest correctness fix was next-bar-open; the biggest P&L impact was realistic costs.
