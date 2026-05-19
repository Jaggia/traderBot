---
tags: [smi, williams-r, armed-mode, vwap, explanation]
---
# 007 — Strategy Explained: SMI + Williams %R + VWAP

**Date:** 2026-02-28
**Audience:** Anyone unfamiliar with the indicators — no finance background assumed.

---

## The Big Picture

The strategy watches a target equity (e.g. a broad-market ETF) on 5-minute bars. Every 5 minutes it asks: *"Is momentum shifting from oversold to bullish — or from overbought to bearish?"* If two independent indicators agree within a short time window, it places a trade.

Three layers of logic, each adding confidence:

1. **SMI** — is the broader momentum trend flipping?
2. **Williams %R** — is price bouncing out of an extreme zone?
3. **VWAP** (optional filter) — is price on the right side of the day's fair value?

---

## Layer 1: SMI (Stochastic Momentum Index)

### What is it measuring?

Imagine the last 13 five-minute bars. Find the highest price and the lowest price in that window — that's the recent *range*. The midpoint of that range is the "equilibrium". SMI answers:

> **"Where is the current close relative to the midpoint of the recent range, and how strongly is it moving there?"**

- **SMI = +100**: close is pushing hard toward the top of the range — strong upward momentum
- **SMI = -100**: close is pushing hard toward the bottom — strong downward momentum
- **SMI = 0**: close is right at the midpoint — no clear momentum

The "strongly" part comes from double-smoothing with EMAs (exponential moving averages), which filter out random noise so you don't react to every tiny wiggle.

### Fast SMI vs Slow SMI

The strategy runs **two** SMIs simultaneously:

| | Period | Behavior |
|--|--|--|
| **Fast SMI** | 5 bars (25 min) | Reactive — picks up short-term shifts quickly |
| **Slow SMI** | 13 bars (65 min) | Smoother — reflects the broader momentum trend |

Both use the same smoothing (EWM span=8 applied twice).

### The crossover signal

When **Fast SMI crosses above Slow SMI**, it means the short-term momentum has just overtaken the longer-term trend — a potential start of an upward move.

When **Fast SMI crosses below Slow SMI**, short-term momentum has rolled over beneath the trend — a potential start of a downward move.

```
         Fast SMI ↗
    _______________X_______    ← crossover = momentum shift detected
   /    Slow SMI  /
__/              /
```

This crossover is the **first trigger** — it "arms" the system and starts a 30-minute countdown.

---

## Layer 2: Williams %R

### What is it measuring?

Williams %R looks at the same kind of window (last 13 bars) and asks a simpler question:

> **"Where is today's close relative to the highest price seen in the recent window?"**

- **%R near 0** (e.g., -5): close is near the recent high — strong, potentially overbought
- **%R near -100** (e.g., -95): close is near the recent low — weak, potentially oversold
- **%R = -50**: close is right in the middle — neutral

The scale is always −100 to 0 (the negative sign is just convention).

### The threshold crossings

The strategy watches for two specific moments:

| Signal | What it means |
|--|--|
| **%R crosses above −80** (from below) | Price just bounced *out* of oversold territory — buyers stepping in |
| **%R crosses below −20** (from above) | Price just rolled *out* of overbought territory — sellers taking over |

These are not arbitrary numbers — −80 and −20 are the classic Williams %R "zone boundaries" used by traders for decades. A cross of −80 upward means: "we were beaten down, and now we're recovering." A cross of −20 downward means: "we were stretched too high, and now we're cracking."

### Why is Williams %R different from SMI?

| | SMI | Williams %R |
|--|--|--|
| **Smoothed?** | Yes (double EMA) | No — raw calculation |
| **Speed** | Slower, more stable | Faster, more reactive |
| **What it captures** | Sustained momentum trend | Immediate price position in the range |

They complement each other: SMI tells you the momentum trend is shifting, W%R confirms the price itself is in an extreme zone and bouncing out of it. Both saying the same thing at roughly the same time is much more reliable than either alone.

---

## How They Combine: The Armed Mode

With `armed_mode: true`, the signal fires in two steps (the exact `lookforward_mode` and `sync_window` are configurable in `config/strategy_params.yaml`):

### Long (buy) signal — step by step (example: `lookforward_mode: smi_then_wr`)

```
Step 1: Fast SMI crosses above Slow SMI
        → System is ARMED. Countdown begins (configurable sync_window).

Step 2: Within that window, Williams %R crosses above -80
        → System FIRES a long signal. Position opened. System disarms.

If Step 2 doesn't happen within the window → arm is discarded, no trade.
```

**What this means in plain English:**
> "Momentum just flipped bullish (SMI crossover), AND within the next 30 minutes, price confirmed it by bouncing out of oversold territory (W%R > -80). Both agree — buy."

### Short (sell) signal — step by step

```
Step 1: Fast SMI crosses below Slow SMI
        → System is ARMED. 30-minute countdown begins.

Step 2: Within those 30 minutes, Williams %R crosses below -20
        → System FIRES a short signal. Position opened. System disarms.
```

**What this means in plain English:**
> "Momentum just flipped bearish (SMI crossover), AND within 30 minutes, price confirmed it by rolling out of overbought territory (W%R < -20). Both agree — sell."

### Why "armed mode" instead of just checking both at once?

The armed/disarm design prevents the system from firing multiple times from a single momentum shift. Without it, if the SMI crossover stayed valid for a long time, you could fire repeated signals on every W%R tick. Arming once and disarming on fire keeps it clean: one arm → one trade.

---

## Layer 3: VWAP Filter (optional, currently ON)

### What is VWAP?

VWAP = **Volume Weighted Average Price**. It resets every morning at market open and asks:

> **"At what price has the majority of today's volume actually traded?"**

It's not a simple average — bars with higher volume get more weight. So if a big institutional order pushed through at 10am, that price level has more influence on VWAP than a quiet 3pm bar.

VWAP resets daily, so it's always reflecting *today's* activity.

### How it's used as a filter

| Trade direction | Requirement |
|--|--|
| Long (buy) | Close must be **above** VWAP at signal time |
| Short (sell) | Close must be **below** VWAP at signal time |

**Why?** VWAP represents where the institutional "smart money" has been trading today. If price is above VWAP, the bulk of the day's volume was transacted at lower prices — buyers are in control. A long signal here aligns with the day's dominant trend. Taking a long signal when price is *below* VWAP means you're fighting the day's flow.

Think of VWAP as a sanity check: "Even if SMI and W%R agree on a long, is the market-wide context actually bullish today?"

### VWAP doesn't generate signals — it blocks them

VWAP only acts as a gate. It cannot fire a buy or sell on its own. It can only reject a signal that SMI + W%R would otherwise produce.

---

## Complete Signal Flow

```
Every 5-min bar:
│
├─ SMI fast crosses above SMI slow?
│     YES → ARM the system (start 30-bar countdown)
│
├─ System is armed AND W%R crosses above -80?
│     YES → candidate LONG signal
│           │
│           └─ VWAP filter ON?
│                 YES → is close > VWAP?
│                           YES → FIRE LONG ✓
│                           NO  → signal blocked ✗
│                 NO  → FIRE LONG ✓ (no filter)
│
├─ SMI fast crosses below SMI slow?
│     YES → ARM for short
│
└─ System is armed AND W%R crosses below -20?
      YES → candidate SHORT signal → same VWAP check (close < VWAP?)
```

---

## Parameter Structure

```yaml
signals:
  smi_fast:    {period: ..., smooth1: ..., smooth2: ...}
  smi_slow:    {period: ..., smooth1: ..., smooth2: ...}
  williams_r:  {period: ...}
  sync_window: ...    # max bars between arm and fire
  lookforward_mode: ... # "smi_then_wr" | "wr_then_smi" | "either"
  armed_mode:  ...
  vwap_filter: ...
```

All values live in `config/strategy_params.yaml`. A common design choice: matching W%R and SMI slow periods so both indicators observe the same historical window — they're measuring the same recent context from different angles. The fast SMI period is typically shorter to provide early "something is changing" detection.

---

## Why Stack These Three Specifically?

Each indicator has a known weakness that the next one compensates for:

**SMI alone** can fire prematurely — momentum starts to shift but price hasn't actually moved yet. You get in too early.

**W%R alone** can fire in a trending market — price exits the oversold zone, but momentum is still generally bearish. You buy a dead cat bounce.

**Together**: SMI says "the trend is turning", W%R says "price has actually started moving out of the extreme." Two independent measurements of the same underlying reality arriving close together is a much stronger signal than either alone.

**VWAP adds**: "and the intraday context supports this direction." It removes counter-trend signals where SMI + W%R agree locally but the broader day's flow disagrees.

The result is fewer, higher-confidence signals — at the cost of missing some moves that only one indicator catches.
