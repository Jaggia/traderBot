---
tags: [tutorial, hmm, intuition, regime-detection]
---
# Part 1: Intuition — What Is a Hidden Markov Model?

[< Index](00-index.md) | [Part 2: The Math >](02-the-math.md)

---

## The Name

A Hidden Markov Model has two key words to unpack:

**Markov** — named after Andrei Markov, a Russian mathematician. A Markov process is one where the future depends only on the present, not the full past. "Given where I am now, it doesn't matter how I got here." This is called the Markov property.

**Hidden** — the Markov process is running, but you can't see it directly. You only see a noisy consequence of it.

The model was developed in the 1960s by Leonard Baum and colleagues. It became foundational in speech recognition — voices produce sounds you hear, but the underlying phonemes (the "hidden states") are what you're trying to identify. From there it spread into biology (gene sequencing), finance (regime detection), and dozens of other fields.

---

## The Core Intuition: The Market Has Moods

Think of the market as having moods it doesn't announce. One day SYMBOL is trending cleanly, the next it's chopping sideways in a range, the third it's in a slow grind down. These moods — *regimes* — are real, but you can't look them up in a database. You can only observe the *consequence* of them: price moves, volume, volatility.

An HMM says: **the market is always in one of a small number of hidden states. Each state produces observations (bar returns, volatility) with different statistical characteristics. By watching the observations, we can estimate which state we're probably in right now.**

This is the key insight: **the state is the thing you care about. The observation is the thing you can measure.**

---

## A Concrete Analogy: The Weather Machine

Imagine you're locked in a room with no windows. A weather machine is running outside — it switches between "Sunny" and "Rainy" days according to its own internal logic. You can't see outside, but every day someone slides a number under your door: how many ice creams were sold in the city today.

```
                   [Hidden Layer]
  Today's weather → [Sunny]    [Rainy]
                       ↓          ↓
                   [Observed Layer]
  Ice creams sold → {3, 5, 1}  {0, 1, 2}
```

On sunny days, ice cream sales cluster around 3–5. On rainy days, they cluster around 0–2. You observe the sales number; you want to infer the weather.

Now replace this with:
- **Hidden state** = market regime (trending up, trending down, choppy/mean-reverting)
- **Observation** = what you can actually measure: 5-min bar return, intraday volatility, Williams %R value, SMI value

The HMM's job is to infer the hidden regime from the sequence of observations.

---

## Why Your Indicators Are Not Enough

Your current strategy (SMI + Williams %R) looks at indicator values and fires when certain crossing conditions are met. This is deterministic: given the same bar data, it always fires the same signal.

The problem is that **the same indicator pattern means different things in different regimes**:

| Situation | SMI cross | Actual outcome |
|-----------|-----------|----------------|
| Strong uptrend, pullback resolved | SMI fast crosses above slow | High-probability long — momentum resumes |
| Sideways chop, random oscillation | SMI fast crosses above slow | Low-probability — likely to reverse again |
| Volatility spike, news event | SMI fast crosses above slow | Unknown — regime has changed, indicator means nothing |

The SMI crossing is the same in all three cases. But the *regime* is different, and the regime is what determines whether the signal has edge.

This is the problem HMMs solve. They don't replace your indicators — they **contextualize** them. "SMI just crossed and we're in a trending regime" is a much stronger signal than "SMI just crossed and we're in a choppy regime."

---

## What "Hidden" Really Means (and Doesn't)

"Hidden" does not mean the states are magical or unknowable. It just means:

1. They're **latent** — not directly measured in your data
2. You can only estimate them from indirect evidence (observations)
3. The estimate is probabilistic, not certain

An HMM never tells you "you are definitely in the trending regime." It tells you "given everything observed so far, there is a 78% probability you are in the trending regime." This probability changes bar by bar as new observations arrive.

This is fundamentally different from a simple threshold rule like "if VIX > 25, we're in high-vol regime." An HMM accounts for the full *sequence* of observations and for the natural inertia of regimes (markets don't flip from trending to choppy every 5 minutes).

---

## The Three Questions an HMM Answers

Researchers traditionally frame three core HMM problems:

### 1. Evaluation — "How likely is this sequence?"
Given a trained HMM and an observed sequence, what is the probability the model generated this sequence? Used to compare models.

### 2. Decoding — "What hidden states produced this sequence?"
Given a trained HMM and an observed sequence, what is the most likely sequence of hidden states? **This is the one we use in trading.** The answer is the regime label for each bar.

### 3. Learning — "How do we fit the model to data?"
Given observed sequences, estimate the model parameters (transition probabilities, emission distributions). This is training.

In practice: train the model on historical data (Learning), then run it bar-by-bar on new data to infer the current regime (Decoding), and use that regime label to gate or scale your signals.

---

## A Visual of the Structure

```
Hidden states (regimes — you can't observe these):
  
  [Trending Up] ──→ [Trending Up] ──→ [Choppy] ──→ [Trending Down]
       ↓                  ↓               ↓               ↓
Observations (what your strategy can measure):

  [return=+0.3%]  [return=+0.2%]  [return=+0.05%]  [return=-0.4%]
  [vol=0.8%]      [vol=0.6%]      [vol=1.5%]       [vol=1.1%]
  [SMI=65]        [SMI=72]        [SMI=48]          [SMI=22]
```

The top row is the Markov chain (states transition according to fixed probabilities). The bottom row is what you see in your DataFrame. The HMM's decoder reverse-engineers the top row from the bottom row.

---

## Where This Fits in the Strategy

In this project, the HMM would sit **above** the existing signal logic:

```
[Raw bars]
    ↓
[Indicator calculation] ← unchanged: SMI, W%R, VWAP, EMA
    ↓
[HMM regime estimation] ← new layer: "what regime is the market in?"
    ↓
[Signal gating]         ← only fire SMI/WR signals in favorable regimes
    ↓
[Backtest engine]       ← unchanged
```

It's a filter, not a replacement. The VWAP filter currently does something similar — it blocks signals when price is fighting the day's institutional flow. The HMM would do something more sophisticated: block signals when the market's overall *behavior pattern* is unfavorable for momentum strategies.

---

**Key takeaway:** An HMM models the market as switching between a small number of hidden regimes. By observing price behavior (returns, volatility, indicator values), it estimates the current regime probability. This context lets you gate or scale your existing signals based on whether conditions actually favor momentum trading right now.

[< Index](00-index.md) | [Part 2: The Math >](02-the-math.md)
