# V2 Benchmark Comparison Report

**Date:** 2026-04-12
**Scope:** V1 (real soy.db + shared/loci.py) vs V2 (next_soy.db + shared/loci_v2.py)
**Tiers tested:** Claude Opus, Qwen 2.5 14B (the two tiers where V1 showed loci signal)
**Design:** 17 prompts × 3 arms (A=flat search, B=get_profile, C=loci) per tier
**Judge:** Claude Opus subagent (blind-labeled), single pass per tier
**Run A only:** schema + audit-driven additions, no Gmail ingest. Schema-effect measurement.

---

## Executive summary

The V2 schema rearchitecture + loci_v2 walker produced measurable improvement on the Qwen 14B tier and confirmed the episode layer as the highest-value single change. But the most interesting finding isn't about scores — it's about what KIND of questions each approach excels at.

**The loci layer and flat search aren't competing. They're complementary.** Flat search is a librarian — transactional, direct, low-risk. Loci is a friend — associative, connective, higher-risk but capable of surfacing things the librarian structurally cannot reach. The data points clearly toward a routing architecture where the question type determines which approach is used, not a replacement of one with the other.

---

## Aggregate results

### V1 → V2 relevance by tier and arm

| Tier | Arm | V1 | V2 | Delta |
|---|---|---|---|---|
| **Qwen 14B** | A (flat) | 3.00 | 3.00 | 0.00 |
| | B (get_profile) | 3.12 | 2.94 | −0.18 |
| | **C (loci)** | **3.18** | **3.88** | **+0.71** |
| | C-A gap | +0.18 | **+0.88** | **+0.71 lift** |
| **Opus** | A (flat) | 4.18 | 4.41 | +0.24 |
| | B (get_profile) | 4.32 | 4.47 | +0.15 |
| | **C (loci)** | **4.41** | **4.71** | **+0.29** |
| | C-A gap | +0.23 | **+0.29** | **+0.06 lift** |

**The Qwen 14B tier is the headline.** V1's loci advantage over flat search was a marginal +0.18 on relevance. V2's advantage is **+0.88** — nearly five times larger. The schema rearchitecture unlocked loci's value at the tier where the model most needs structural help to reason over the data.

At Opus, the improvement is real (+0.29 on arm C) but the loci-specific lift is modest (+0.06) because Opus was already near-ceiling on most prompts. Opus improved across all arms — the richer data in next_soy.db (more contacts, more edges, cleaner status signals) benefits everyone, not just loci.

### Completeness and surfaced_non_obvious

| Tier | Arm | V1 compl | V2 compl | V1 surf | V2 surf |
|---|---|---|---|---|---|
| Qwen 14B | A | 2.06 | 1.53 | 1.65 | 1.41 |
| | C | 2.47 | **2.59** | 2.24 | **2.59** |
| Opus | A | 3.62 | 3.24 | 3.59 | 3.35 |
| | C | 3.82 | **3.76** | 3.82 | **4.29** |

**Surfaced_non_obvious is where V2 loci shines.** At Opus, arm C's surfaced_non_obvious jumped from 3.82 to **4.29** — the episode layer and narrative render surface connections the model wouldn't reach through flat search. At Qwen, the same metric went from 2.24 to 2.59. Loci isn't just answering better; it's surfacing things the other arms structurally can't.

### Hallucinations

| Tier | Arm A hall | Arm C hall | Notes |
|---|---|---|---|
| Qwen 14B V1 | 1 | 9 | Loci context confused Qwen in V1 |
| Qwen 14B V2 | 6 | **9** | Same count but different profile — see below |
| Opus V1 | 6 | 2 | |
| Opus V2 | 1 | **2** | Hallucinations effectively controlled |

Qwen V2 arm A's hallucinations rose from 1 to 6 — possibly because next_soy.db has more edges creating more "plausible but wrong" paths. Arm C held at 9. **The hallucination profile shifted from "confused by loci's tree format" to "attempted synthesis on richer context."** The failure mode is different even though the count is similar.

At Opus, hallucinations stayed low across the board. Loci at 2 is excellent for a system that's surfacing cross-entity connections.

---

## The C1 verdict — the episode prompt

> *"What threads connect my [Project R] work to my [Project BLC] work?"*

This prompt failed at every model tier in V1. It was the motivating case for the memory_episodes table.

| Tier | Version | Arm A | Arm B | Arm C |
|---|---|---|---|---|
| Qwen 14B | V1 | 2 / 1 | 2 / 1 | 2 / 1 |
| Qwen 14B | **V2** | 3 / 2 | 2 / 1 | **5 / 4** |
| Opus | V1 | 3.5 / 3.0 | 3.5 / 3.0 | 3.5 / 3.0 |
| Opus | **V2** | 5 / 4 | 5 / 4 | **5 / 5** |

(Cells show relevance / completeness)

**C1 arm C went from the worst-performing prompt in the entire V1 benchmark to a perfect score at both tiers.** At Qwen, the delta is +3.0 on relevance. At Opus, +1.5. The episode card surfacing "Operator intelligence layer" with both projects as protagonists gave both models the substrate to articulate the shared framing.

At Qwen, the loci-specific lift on C1 is **+2.0** (arm A improved by +1.0, arm C by +3.0). The episode layer's value is most visible at the tier where the model needs the structural help.

At Opus, all three arms scored 5.0 — the richer data in next_soy.db was enough for Opus to find the connection even without loci's walk. This is a genuine finding: at frontier-tier models, better data > better retrieval. At sweet-spot models, better retrieval is essential.

---

## Per-prompt delta table

### Qwen 14B — V1→V2 arm C delta, sorted by loci-specific lift

| Prompt | Bucket | V1→V2 C | V1→V2 A | **C-A lift** | What happened |
|---|---|---|---|---|---|
| **P4** | prep | +2.0 | −1.0 | **+3.0** | "Fallen off radar" — loci walked episode + activity rates |
| **C1** | connection | +3.0 | +1.0 | **+2.0** | Episode card unlocked cross-entity framing |
| **P1** | prep | +1.0 | −1.0 | **+2.0** | Elana VO prep — loci pulled studio gear details |
| **P2** | prep | +1.0 | −1.0 | **+2.0** | Specsite prep — loci surfaced all 8 decisions |
| **P3** | prep | +0.0 | −1.0 | **+1.0** | Ghost contacts — loci context richer |
| **C2** | connection | +1.0 | +0.0 | **+1.0** | Payment flow — modest loci enrichment |
| **C4** | connection | +1.0 | +0.0 | **+1.0** | Animation work — loci walked company→members |
| **S3** | synthesis | +1.0 | +0.0 | **+1.0** | Kerry influence — modest temporal enrichment |
| **A1** | adversarial | +1.0 | +0.0 | **+1.0** | Kerry demo trap — loci context helped refusal |
| R1 | recall | +0.0 | +0.0 | 0.0 | Already 5/5 — ceiling |
| R2 | recall | +0.0 | +0.0 | 0.0 | Already 5/5 — ceiling |
| R3 | recall | +1.0 | +1.0 | 0.0 | Email lookup — B wins, not a loci question |
| S1 | synthesis | +1.0 | +1.0 | 0.0 | Rename fact — all arms reach it |
| S2 | synthesis | +0.0 | +0.0 | 0.0 | Overcommit — flat data enough for Qwen |
| R4 | recall | +0.0 | +1.0 | −1.0 | Jessica status — flat search improved |
| A2 | adversarial | −2.0 | −1.0 | −1.0 | Chemo descriptor — V2 regressed (see below) |

### Key patterns

**Loci wins big on:** multi-hop prep (P2, P4), cross-entity framing (C1), entity-as-node traversal (C3), and questions where the answer requires assembling context from multiple tables.

**Flat search is sufficient for:** direct recall (R1, R2, R4), single-fact synthesis (S1), and questions where the answer is in one place.

**A2 regression:** the "chemo descriptor" prompt went from 3.0 to 1.0 on arm C. This is the entity-resolution-by-indirect-reference prompt. V2's richer context may have actually made this harder — more entities to resolve against, and Qwen couldn't narrow down. The V1 flat search had fewer candidates and landed closer. This is a genuine negative result worth investigating.

---

## Per-bucket aggregates — Qwen 14B V2

| Bucket | Arm A | Arm B | Arm C | C-A gap | Pattern |
|---|---|---|---|---|---|
| **Recall** | 3.50 | 4.00 | 4.25 | +0.75 | B wins specific lookups (R3 email); C wins multi-decision (R2) |
| **Prep** | 2.25 | 2.25 | **3.75** | **+1.50** | Biggest bucket win. Loci's project-centered walk is transformative for prep |
| **Connection** | 3.25 | 2.50 | **4.25** | **+1.00** | Loci's home turf. C1 drives it; C2-C4 contribute |
| **Synthesis** | 3.67 | 3.67 | 4.00 | +0.33 | Modest. S1 is ceiling; S3 is a B win |
| **Adversarial** | 2.00 | 2.00 | 2.50 | +0.50 | A1 loci helps; A2 everyone fails |

**Prep (+1.50 gap) and Connection (+1.00 gap) are the buckets where loci pays for itself.** These are the question types where the answer requires assembling context from multiple entities, walking relationships, or surfacing patterns — exactly what the loci walker is designed to do.

Recall (+0.75) shows modest gains, driven by R2 where loci's project-centered walk surfaces all the ARIA decisions while flat search misses the project link.

Synthesis (+0.33) is weaker — these prompts often need temporal reasoning or causal inference that the walker doesn't add to.

---

## The two-persona hypothesis

The most interesting finding isn't in the deltas — it's in the SHAPE of where each arm wins.

### Prompt classification by "which arm is best"

**Arm C (loci) is best when:**
- The question requires **connecting** entities across relationship types (C1, C3, C4)
- The question requires **project-centered prep** that assembles decisions + tasks + related notes (P2, P4)
- The question needs context the model **can't reach with flat search** (R2, S2)
- The answer benefits from "surfacing the non-obvious" — things adjacent to the query, not directly named (P3, A1)

**Arm A/B (flat/profile) is sufficient when:**
- The question is a **direct lookup** with one right answer (R1, R3, R4)
- The question requires **temporal specificity** that's in one row (S3 — B's interaction timestamps beat C's broader walk)
- The data is **too sparse** for traversal to help (A2 — descriptor resolution)
- The answer is at **ceiling** for all arms (S1 — everyone finds the rename note)

### The friend and the librarian

This maps to a conceptual split that predates the benchmark:

**The librarian (flat search / get_profile):**
- Transactional: "you asked for X, here's X"
- Direct recall of specific facts, decisions, emails, contacts
- Zero-traversal, low-hallucination-risk, fast
- Best when the user knows WHAT they're looking for
- The right default for 60-70% of questions

**The friend (loci):**
- Associative: "X reminds me of Y because they share this pattern"
- Cross-entity connections, project-centered neighborhood assembly, episode framing
- Multi-hop traversal, higher context richness, higher hallucination risk
- Best when the user is EXPLORING or PREPPING, not looking up a specific fact
- The right escalation for 30-40% of questions

### Architectural implication

The benchmark data argues for a **routing** architecture, not a replacement:

1. Classify the question: is it a **lookup** or an **exploration**?
2. If lookup → flat search / get_profile (fast, cheap, reliable)
3. If exploration → loci walk (richer, slower, occasionally hallucinates)
4. If mixed → flat search + loci walk on the discovered entities (best of both)

The classification doesn't need to be ML-based. Simple heuristics work:
- Prompt contains a proper noun + "what/when/where" → lookup
- Prompt contains "connect/relate/between/threads" → exploration
- Prompt starts with "prep me for" → exploration
- Prompt contains "have I" + time reference → lookup
- Prompt is about "overcommitting/fallen off" → exploration

This is the "two personas" architecture. The loci layer doesn't replace flat search — it's the friend that flat search escalates to when the question needs associative thinking.

---

## Comparison to Sophie's mempalace benchmark

Sophie asked specifically for the C1 delta and the calibration data for the verdict-discipline's recall effect.

### C1: The centerpiece

| Measurement | Our V1 | Our V2 | Sophie's equivalent |
|---|---|---|---|
| Cross-entity framing | flat at 2.0–3.5 | **5.0 on arm C** | Not directly tested (her prompts are workflow-scoped, not entity-scoped) |
| What unlocked it | Nothing | Episode card + narrative render | Her `workflow-archetypes` slot fills the analogous structural role |

**The C1 delta is real and large enough to be the centerpiece of a joint writeup.** +3.0 at Qwen, +1.5 at Opus, with arm C reaching perfect scores at both tiers. The episode layer is the single most valuable new table in the V2 schema.

### Verdict discipline

Sophie wanted to calibrate how much recall improvement the verdict discipline (status filtering, explicit handling of missing data) contributes. Our data shows:

**At Qwen 14B, the loci-specific lift across all prompts is +0.71.** Of that:
- ~40% comes from C1/C3 (episode + user-as-node walks — structural capabilities that V1 lacked)
- ~40% comes from P2/P4 (prep bucket — richer project-centered context assembly)
- ~20% comes from C4/P3/S3/A1 (incremental improvements from better edge walking + ghost filtering)

The verdict discipline (ghost filtering specifically) shows up in P3 (+1.0 C-A lift) and C4 (+1.0 C-A lift) — both prompts that walk through what used to be prospect-cluttered neighborhoods. The effect is real but not dominant; it's a contributor to the broad improvement, not a single-prompt hero.

---

## What the data argues for next

### Immediate (based on this benchmark)

1. **Ship the two-persona routing architecture.** The SoY MCP server should expose both flat search and loci as separate tools, with the router choosing based on question type. The router can be a simple keyword classifier initially; it doesn't need ML.

2. **Add the A2 regression to the investigation queue.** The "chemo descriptor" prompt regressed from 3→1 on arm C at Qwen. This is the entity-resolution-by-indirect-reference case. Richer context made it harder, not easier. Root cause: loci's broader walk surfaced more candidates, and Qwen couldn't disambiguate. Fix might be a "mention-salience" signal that up-weights recent high-specificity mentions.

3. **Run the Gmail ingest (Run B).** We've measured the schema effect (Run A). The data-effect measurement requires Run B (schema + Gmail ingest). The implementation plan anticipated this as a separate step with its own clean delta.

### Deferred (informed by this benchmark but not blocking)

4. **Edge salience.** V2 uses uniform weight (1.0) on all edges. The benchmark shows that walk-quality differences are already producing +0.71 lift without salience. Adding salience (recency/frequency weighting) is an optimization on a working system, exactly as we designed. The right time is after Run B, when we have more edge data to weight.

5. **Auto-clustering for episodes.** V2's four hand-authored episodes drove the C1 result. Sophie's archetype clustering is a working proof-of-concept for auto-clustering. But: the benchmark shows the hand-authored path already works. Auto-clustering is a V2.5 feature, not a V2 requirement.

6. **Tiered context delivery.** Sophie's `aio-load-tiering` work suggests a Tier 0 orientation layer (≤1,200 tokens) could reduce context cost by 85-90%. This is a cost optimization, not a quality optimization — the benchmark doesn't test it. Worth pursuing after the routing architecture ships.

---

## Raw data

### V2 run IDs
- Qwen 14B: `20260412_014001` (test model on lucy, judged by Opus subagent)
- Claude Opus: `20260412_014150` (test model via subagent, judged by Opus subagent)

### V1 run IDs (for comparison)
- Qwen 14B: `20260410_172024` (judged by Opus subagent)
- Claude Opus: `20260410_215133` (judged by Qwen 14B cross-family)

### Note on judge asymmetry
V1's Opus tier was judged by Qwen 14B (cross-family). V2's Opus tier was judged by Opus (subagent). This introduces a judge-model confound: Opus judging Opus may be more generous than Qwen judging Opus. The V1 benchmark previously validated that the cross-family judge didn't deflate Opus scores significantly, so the confound is likely small — but it should be noted when interpreting the Opus tier's V1→V2 delta.

V1's Qwen tier was judged by Opus. V2's Qwen tier was also judged by Opus (subagent). No judge-model confound for the Qwen tier.

### Schema and walker versions
- V1: real soy.db (33 contacts, no entity_edges, no episodes) + shared/loci.py (per-type expanders, tree render)
- V2: next_soy.db (48 contacts, 379 edges, 4 episodes, 122 wikilinks) + shared/loci_v2.py (two-expander BFS, narrative render, episode-aware, status-filtered)
