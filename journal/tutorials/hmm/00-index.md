---
tags: [tutorial, hmm, hidden-markov-model, index]
---
# Hidden Markov Model Crash Course

A 5-part series on Hidden Markov Models for quantitative trading, written around this project. The focus is practical: what HMMs are, how the math works, where they fit in a signal pipeline, where they break, and exactly how you would wire one into this codebase.

---

## Reading Order

| Part | File | What You'll Learn |
|------|------|-------------------|
| 1 | [01-intuition.md](01-intuition.md) | What an HMM actually is, the "market has moods" intuition, hidden vs. observed states, and why your indicators are not enough on their own |
| 2 | [02-the-math.md](02-the-math.md) | The three defining components (transition, emission, initial), the Forward algorithm, Viterbi decoding, and Baum-Welch training — with formulas |
| 3 | [03-quant-applications.md](03-quant-applications.md) | How quants use HMMs: regime detection, volatility filtering, signal gating, position sizing, and drawdown avoidance |
| 4 | [04-pitfalls.md](04-pitfalls.md) | Where HMMs break down: look-ahead bias, regime persistence illusions, overfitting, non-stationarity, and the dangerous things HMMs cannot tell you |
| 5 | [05-our-implementation.md](05-our-implementation.md) | Exactly how to wire a 2- or 3-state HMM into this codebase — feature selection, training setup, integration points, config additions, and a decision framework |

---

## Prerequisites

- Basic probability (conditional probability, what a distribution is)
- Familiarity with this project's signal pipeline (`src/signals/`)
- No advanced math required — Part 2 builds from first principles

## One-Sentence Summary

An HMM answers: **"What unobservable market regime is the price series most likely in right now — and should my strategy be trading at all?"**
