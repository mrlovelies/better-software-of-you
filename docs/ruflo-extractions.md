# Ruflo Extractions — What We Took, Why, and How

**Source:** github.com/ruvnet/ruflo (formerly Claude Flow) v3
**Extracted by:** Signal Harvester pipeline team (Alex Somerville, Kerry Morrison)
**Date started:** 2026-03-27

---

## Why Ruflo?

Ruflo is a 9,964-file multi-agent orchestration framework with 6,000+ commits. We evaluated it alongside Paperclip for our Signal Harvester pipeline. The verdict: **Paperclip is our dispatch layer** (it's a server with REST APIs, agent management, heartbeat scheduling). **Ruflo is our intelligence layer** — it contains battle-tested algorithms for the hard problems: semantic search, adaptive routing, content security, and learning from outcomes.

We're not using Ruflo as a framework. We're extracting specific components and porting them to Python to run inside our existing pipeline.

---

## Extraction 1: HNSW Vector Index

**Source:** `v3/@claude-flow/memory/src/hnsw-index.ts` (~650 lines)
**Ported to:** `shared/hnsw_index.py`
**Status:** Complete

### What It Does
Hierarchical Navigable Small World (HNSW) index — a data structure for fast approximate nearest-neighbor search in high-dimensional vector space. Think of it as a multi-layer graph where each layer is progressively sparser, allowing log(n) search time for finding the most similar vectors.

Key implementation details from Ruflo:
- BinaryMinHeap/BinaryMaxHeap for O(log n) priority operations
- Pre-normalized vectors for O(1) cosine similarity (skip the normalization step)
- Bounded max-heap for efficient top-k tracking (never stores more than k results)
- 150x-12,500x speedup over brute-force linear scan

### Why It Matters to Us
**Signal deduplication and similarity clustering.** Our regex-based dedup catches exact URL matches, but people post the same pain point in different words across different subreddits. "I wish there was a tool to track my freelance invoices" and "Freelancers need better billing software" are the same signal — regex won't catch it, but vector similarity will.

**How we use it:**
1. Each harvested signal gets embedded (via Ollama or a lightweight embedding model)
2. Before storing a new signal, query the HNSW index: "Is there a signal with >0.85 similarity already?"
3. If yes → mark as semantic duplicate, link to the original, boost the original's weight
4. If no → store as new signal, add to index
5. Bonus: cluster related signals to identify pain-point patterns ("7 people in different subs all want better freelance invoicing")

### What Changed During Extraction
- Ported from TypeScript to Python
- Replaced TypeScript-specific Float32Array with numpy arrays
- Simplified the API to match our pipeline's needs
- Added Ollama embedding integration for signal vectorization

---

## Extraction 2: Q-Learning Router

**Source:** `v3/@claude-flow/cli/src/ruvector/q-learning-router.ts` (~450 lines)
**Ported to:** `shared/q_router.py`
**Status:** Complete

### What It Does
Reinforcement learning router that learns which agent (or in our case, which LLM) handles which type of task best, based on outcome quality. It maintains a Q-table (state × action → expected reward) and updates it every time a decision leads to a good or bad outcome.

Key implementation details from Ruflo:
- Feature hashing for O(1) state representation (signal keywords → 64-dim vector)
- 8 action routes (coder, tester, reviewer, etc.) — we adapt to our LLM routes
- Epsilon-greedy exploration with decay (starts exploring, gradually exploits what works)
- LRU cache (256 entries, 5min TTL) for repeated signal patterns
- Experience replay buffer (1000 entries) for stable learning
- Model persistence to JSON (survives restarts)

### Why It Matters to Us
**Adaptive LLM routing.** Right now our triage is hardcoded: Mistral 7B for T1, Qwen 14B for T2. But some signal types might triage better with different models. A fashion industry signal might score more accurately on Qwen 7B than 14B (faster, good enough). A dev tools signal might need the 14B for nuanced scoring.

The Q-learning router learns this from outcomes:
1. Signal arrives → router picks an LLM based on signal features
2. LLM triages the signal → human approves or rejects
3. If human agreed with the score → reward = high → router reinforces this route
4. If human overrode the score → reward = low → router adjusts
5. Over time, routing adapts to match human judgment patterns

**Also applies to:**
- Which subreddits to prioritize harvesting (route: subreddit → yield quality)
- Which forecast mode to use (route: signal features → pattern/silence/adjacent/etc.)
- Cost optimization (route: signal complexity → cheap model vs expensive model)

### What Changed During Extraction
- Ported from TypeScript to Python
- Adapted action space from code-agent roles to LLM model routes
- Integrated with our evolution tracking tables for reward signals
- Added persistence to SQLite (alongside pipeline data) instead of JSON file

---

## Extraction 3: AIDefence Threat Detection

**Source:** `v3/@claude-flow/aidefence/src/domain/services/threat-detection-service.ts` (~400 lines)
**Ported to:** `shared/content_sanitizer.py`
**Status:** Complete — all tests pass, 0.04ms avg scan time

### What It Does
Content security scanner that detects prompt injection, PII leakage, encoding attacks, and manipulation patterns in text. Designed to protect LLMs from adversarial input — exactly what we need when feeding untrusted Reddit content into our triage models.

Key implementation details from Ruflo:
- 50+ threat patterns across 6 categories:
  - **Instruction override** ("ignore previous instructions", "forget everything")
  - **Role switching** ("you are now a different AI", "pretend to be")
  - **Jailbreak** ("DAN mode", "bypass restrictions")
  - **Context manipulation** (system message injection, delimiter abuse)
  - **Encoding attacks** (base64/rot13/hex obfuscation)
  - **Hypothetical framing** (low confidence but flagged)
- 6 PII detection patterns: emails, SSNs, credit cards, API keys, passwords
- Confidence scoring with context adjustment (repetition, multi-pattern hits)
- Performance target: <10ms detection

### Why It Matters to Us
**We feed raw Reddit text into LLMs.** A malicious post could contain prompt injection designed to manipulate our triage model into approving garbage signals or leaking system instructions. Not paranoia — adversarial ML is a real attack surface.

**How we use it:**
1. Every harvested signal passes through the sanitizer BEFORE hitting any LLM
2. **High threat (severity >= critical)** → signal quarantined, logged, not processed
3. **PII detected** → stripped before storage (we don't need people's emails/SSNs in our DB)
4. **Medium threat** → flagged but processed (logged for review)
5. **Low/no threat** → normal processing
6. Encoding attacks decoded and rescanned (catch base64-wrapped injection)

**Also protects:**
- The dashboard API (user-submitted notes, rejection reasons)
- Kerry's brief ingestion (when clients submit build briefs)
- The forecaster's LLM prompts (which include raw signal text)

### What Changed During Extraction
- Ported from TypeScript to Python
- Adapted patterns for Reddit-specific content (markdown, spoiler tags, etc.)
- Added Reddit-specific PII patterns (u/ mentions, subreddit mod information)
- Integrated as a pre-processing step in signal_harvester.py and signal_triage.py

---

## Integration Architecture

```
Reddit post → content_sanitizer.py → signal_harvester.py
                   ↓ (quarantine threats, strip PII)

Stored signal → hnsw_index.py → dedup check
                   ↓ (cluster similar signals)

Unique signal → q_router.py → select best LLM
                   ↓ (adaptive routing based on outcomes)

LLM triage → human review → q_router learns from decision
                   ↓
              hnsw_index updated with new signal embedding
```

---

## What's Next (Tier 2 Extractions — Not Yet Started)

| Component | Source | Why |
|-----------|--------|-----|
| **Hooks & Workers** | `v3/@claude-flow/hooks/` | Auto-harvest triggers, event-driven pipeline |
| **Guidance System** | `v3/@claude-flow/guidance/` | Triage quality rules, violation tracking |
| **ReasoningBank** | `v3/@claude-flow/neural/` | Long-term trajectory learning |

---

*Last updated: 2026-03-27*
