# External Learnings — Sophie's mempalace-rust

**Source:** https://github.com/sophdn/mempalace-rust
**Cloned to:** `/tmp/loci-external/mempalace-rust` (read-only, not committed)
**Context:** Sophie took her own independent stab at the memory-palace-for-LLM-context concept and built a complete Rust implementation + A/B/C benchmark. This document extracts concrete learnings for our next_soy / loci_v2 design.

---

## TL;DR

Sophie's project is **not a graph traversal of a database**. It's a **Claude Code session observer** that reads session history, task files, git logs, and (optionally) a "bardo" session store, then writes a static `LOCI.md` document with seven predefined "locus slots." Totally different substrate and totally different output model from our loci. Her benchmark results are dramatically better than ours — arm C wins every prompt on relevance (5/5 across 16 prompts) and mean completeness is 4.75 — but the comparison isn't apples-to-apples: her arm A is "no context" (cold start), ours is "flat SQL search." She's measuring the delta from zero; we're measuring the delta from a competent baseline.

**The biggest single learning isn't any specific technique — it's the architectural pattern: fixed slots with verdicts (Live / Thin / Inapplicable) instead of a dynamic traversal tree.** This pattern maps cleanly onto our constructive panel's Aisha proposal (narrative render) and Rachel's daily_logs approach, and it's validated by real benchmark data.

---

## What mempalace actually is

Sophie's project reads:

- `~/.claude/projects/<project>/*.jsonl` — Claude Code session transcripts
- Task/chain markdown files at the project root (auto-detected by shape — any markdown file with `TYPE_slug_YYYY-MM-DD` naming or similar)
- A roles directory
- Git log + commit history
- A "routing/navigation index" document at the project root (detected by shape — a markdown file with ≥3 table rows and `.md` references)
- An optional "bardo" session store (a separate Sophie project providing richer session metadata)

And writes:

- `LOCI.md` at the project root — a fixed markdown document with 7 sections
- Scaffolded task files for findings ("fragmented workflow," "stalled chain," "isolated task")
- A synthesis prompt to stdout
- `run-log.json` — a checkpoint so repeated runs don't duplicate output

The seven "locus slots" (each gets a verdict: `Live`, `Thin`, or `Inapplicable`):

1. **`recent-activity`** — sessions, commits, completed tasks since last checkpoint
2. **`active-chains`** — open task chains and their current step
3. **`open-tasks`** — open tasks not in a chain
4. **`role-usage`** — roles observed in the window + co-occurrence patterns
5. **`cross-chain-connections`** — chains sharing roles, tasks, or file paths
6. **`workflow-archetypes`** — clustered workflow patterns with confidence
7. **`routing-index`** — the project's navigation doc + its declared workflow entries

Written in Rust, ~5,500 lines across 11 source files. The heaviest modules are `loci_writer.rs` (1054 lines, writes the output), `routing_health.rs` (1050 lines, analyzes navigation indexes), and `archetypes.rs` (812 lines, clusters workflow patterns into named archetypes).

---

## Sophie's benchmark — dramatically different from ours

Sophie ran a 16-prompt A/B/C benchmark (5 buckets: Recall, Prep, Connection, Synthesis, Adversarial — identical structure to ours). Arms:

- **A** — no nav context (cold start, zero tokens)
- **B** — routing signal table only (~400 tokens, the project's routing trigger list)
- **C** — full `LOCI.md` prepended (~2500 tokens)

### Her headline numbers

| Arm | Relevance | Completeness | Surfaced Non-Obvious | Hallucinations |
|---|---|---|---|---|
| A | 0.63 | 0.06 | 0.00 | 1 |
| B | 1.88 | 0.56 | 0.00 | 1 |
| C | **5.00** | **4.75** | 0.38 | 2 |

Arm C wins **every** prompt on relevance (5/5 across all 16). Arm C is strictly dominant on completeness on every prompt. Zero ties at the top.

Compare to our four-tier result for Claude Opus:

| Arm | Relevance | Completeness |
|---|---|---|
| A | 4.12 | 3.71 |
| B | 4.29 | 3.94 |
| C | 4.35 | 3.88 |

**Our delta is 0.23 points on relevance. Hers is 4.37.** Nearly twenty times larger. That deserves an explanation, not an echo.

### Why the delta is so different

Five reasons, in decreasing order of importance:

1. **Her arm A is a strawman; ours isn't.** She set arm A to "literally no context" (the model starts cold). We set arm A to "flat SQL search across the whitelisted tables" — a legitimate retrieval baseline. Her arm A scores 0.63 on relevance because there's nothing to score. Ours scores 4.12 because it's competent. Her headline result is "context beats no context." Ours is "structured context beats unstructured context, modestly." These are different experiments.

2. **Her prompts are workflow questions; ours are entity questions.** Hers: *"What work has been most active in the past few days?"*, *"Which chains are most stalled?"*, *"How does the mempalace chain connect to other work?"* Ours: *"What payment provider did I decide to use for The Grow App?"*, *"Prep me for picking up work on Specsite tomorrow"*, *"Where did things land with the client whose mom is going through chemo?"* Her substrate (session history) fits workflow questions natively. Ours (personal CRM) fits entity questions natively. We're testing retrieval for different data shapes.

3. **Her data substrate is dense with direct user signal.** Claude Code sessions are *literally a log of the user's recent actions*. Every session transcript is ground truth for "what the user just did." Her LOCI.md is summarizing high-signal data. Our SoY contacts table is inferential — "this person exists, here's what we guessed about them" — and we already found (in `email_gap_findings.md`) that the real data is much sparser than it should be.

4. **Her output is a fixed briefing doc; ours is a dynamic tree.** LOCI.md always has the same 7 sections in the same order. Even when a slot is `Inapplicable`, it's acknowledged and omitted deliberately. Our loci tree varies by seed, has edge-label breadcrumbs, and changes shape per query. For the answering model, fixed-layout narrative is easier to parse than variable-layout tree — exactly the point Aisha's constructive panel proposal made.

5. **Her test model setup is unclear but probably a single Claude.** She used a single judge (no cross-family triangulation), single test model, and her judge confidence was 5/5 on 15 of 16 prompts. That's very high — possibly because her output is more unambiguous to score (fixed slots → clear "did you surface it or not"), possibly because she didn't have our same-model-judging caveat flagged.

**The honest read:** Sophie's benchmark is valid for her experiment (LOCI.md vs no context vs minimal routing signal) but it doesn't directly test what we test. It confirms a weaker claim ("structured context helps when the alternative is nothing") that we already took for granted. It doesn't confirm our strong claim ("graph traversal of a personal data system beats flat retrieval of the same system") — nothing in her setup tests that.

**BUT** — her approach and architecture contain several ideas that are *directly transferable* to ours, and her T26 "smart traversal" spike directly addresses one of our open design questions.

---

## Five learnings worth applying to our loci_v2 / next_soy work

### 1. Fixed slots with verdicts beats dynamic tree

Sophie's output format is:

```
## recent-activity  [Live — 24 sessions, 63 completed tasks in window]

Dominant focus: mempalace (13/22 tasks complete). Recently closed:
y-not-fire-format-spec, decomp-pass, mempalace-git-reader, ...

## active-chains  [Live — 2 chains]

mempalace (13/22, 2 open)
  - mempalace-bardo-reader
  - mempalace-routing-health

lab-glyph-research (0/21, 17 open)
  - [17 task names]

## role-usage  [Inapplicable — no role invocations observed in window]

## cross-chain-connections  [Live — 1 connection]

corpus-block-1 ↔ lab-glyph-research (1 shared session)

...
```

Compare to our tree:

```
## Starting from: Jessica Martin (Client / Founder at The Grow App)
Jessica Martin (Client / Founder at The Grow App)
  └── Email: Re: The Grow App — big staging update (2026-04-10) [via email.contact_id]
        Jessica responded positively to the Apr 8 staging update...
  └── Decision: FB Marketplace-style geo privacy (2026-03-12) [via decision.contact_id]
        rationale: Adapts proven FB Marketplace pattern...
```

**Sophie's format has two properties ours doesn't:**

- **Every section is always in the same place.** The reader knows exactly where to look for "recent activity" regardless of the query. Our output is a different shape every time.
- **Every section has an explicit verdict.** `role-usage [Inapplicable]` is rendered, not hidden. This is the structural way to do Rachel's "honest refusal" — the schema enforces that "no data" is a first-class answer, not an implicit absence.

**What to change in our loci_v2 render:**

When Aisha's constructive panel proposed replacing the tree with per-entity narrative blocks, she was 80% of the way to Sophie's fixed-slot model. The missing piece is the **verdict**. Our narrative blocks should include explicit "this section has no data because the underlying walk found nothing" markers, rendered the same way across every prompt. Don't omit empty sections — render them with `[Inapplicable]`.

Concretely, our next_soy + loci_v2 combination should produce output that looks like:

```markdown
# Loci context for: Prep me for recording VO with Elana

## Contact neighborhood  [Live — 1 contact, 3 related projects, 8 recent interactions]

Elana Dunkelman (Actor/Writer at ACTRA Toronto). Active project
elana-dunkelman-vo (high priority, started Mar 24). Recent thread
"Website tweaks" (Apr 5-8, 8 messages). She's a colleague from ACTRA
with participation in Game Expo coordination Mar 28-29.

## Decisions near this entity  [Thin — 0 direct, 2 via linked projects]

No decisions logged directly against Elana. Two related decisions on
elana-dunkelman-vo: ...

## Active threads  [Live — 2]

- Website tweaks (last touched Apr 8) — iteration on bio, credits, studio copy
- Game Expo follow-up (ACTRA cohort, Mar 28-29)

## Historical context  [Live — 5 transcripts, 0 decisions, 16 emails]

...

## Episode context  [Inapplicable — no episodes this entity is a member of]

## Connected concepts  [Thin — 1 candidate via shared tags]

"VO career 2026 push" concept surfaces via shared tags on notes...
```

Note the `[Live]` / `[Thin]` / `[Inapplicable]` markers on every section. A prompt that has no decisions-near-entity data doesn't get a blank section; it gets an explicit "no data" verdict. The model reading this knows exactly what's missing and doesn't confabulate to fill the silence.

### 2. Workflow archetypes are a working implementation of memory_episodes

Sophie's `archetypes.rs` (812 lines) clusters workflow patterns by observed co-occurrence of roles, tasks, and session types. She ends up with named clusters like *"process / docs / chain"* (77% of her window) with frequency metadata. This is **exactly** what Priya's `memory_episodes` proposal in our schema panel aimed at — and Sophie has a working implementation on real data.

**What to port:** The clustering approach itself. Sophie's clustering uses observable signals (co-occurrence, frequency, role overlap). Our `memory_episodes` table in `next_soy_schema_v1.md` was designed for hand-authored entries plus optional auto-clustering later; Sophie demonstrates that auto-clustering **works** on the right substrate.

**Caveat from Priya's original panel:** Don't auto-derive episodes via LLM on every write. Sophie's approach isn't LLM-based — it's deterministic clustering on co-occurrence counts. That respects Priya's constraint.

**Concrete next step:** When we write the seed script and `loci_v2.py`, we can hand-seed 2-3 explicit episodes (per the schema doc) AND add a deterministic clustering pass as a V1.5 feature. Use Sophie's `archetypes.rs` as a reference for the clustering algorithm (label propagation + co-occurrence + frequency thresholds).

### 3. Her T26 "Smart Traversal" spike answers one of our open questions

Sophie ran a spike specifically on *"should we deliver the full LOCI.md or filter by query?"* and concluded: **full delivery.** Her reasoning:

- Arm C dominates every prompt on completeness at full-LOCI size. Smart filtering would only reduce *cost*, not improve *quality*.
- LOCI is session-start context, paid once per session, amortized over ~20-100 turns. Per-session token savings from filtering are ~4% of total session cost — not a significant driver.
- Keyword-based slot filtering is brittle (misses adversarial multi-bucket queries).
- Model-based slot classification adds latency (~300ms Haiku call) and cost that exceed the savings in many cases.

She **explicitly closed the spike without implementing smart traversal.**

**What this tells us about our constructive panel's Lena proposal** (pre-computed per-entity briefings cached offline, fetched at query time): Sophie's data argues that full-context delivery is the right default at our context sizes too. The Lena approach is a cost optimization, not a quality optimization. Defer it unless we hit a specific cost ceiling.

**Revised recommendation:** Implement Aisha's render rewrite against next_soy. Skip Lena's briefing cache for V1. If we later need to optimize serving cost, revisit — but don't pre-optimize.

### 4. Observer pattern — separate discovery from rendering

Sophie's code cleanly separates four phases:

1. **Discovery** (`discovery.rs`) — sniff the project root, find Claude directories, task folders, routing documents, etc. Produces a `ProjectSchema` struct.
2. **Observation** (`observer/*.rs`) — read the raw data from each discovered source. Five observer sub-modules (conversations, git, tasks, bardo, routing). Produces an `ObserverReport` struct.
3. **Analysis** (`archetypes.rs`, `routing_health.rs`, evaluator in `discovery.rs`) — compute verdicts, cluster archetypes, find issues.
4. **Rendering** (`loci_writer.rs`, `synthesis.rs`, `optimization_writer.rs`) — write the output documents.

Each phase consumes the previous phase's struct. No cross-phase state mutation. The `SynthesisTrigger` trait at the end makes the final phase pluggable.

**Our code conflates discovery + observation + rendering inside `loci.py`.** The expanders read data AND decide what to render AND format the output. Refactoring this into phases would be a clean win independent of the next_soy schema changes:

- `loci_v2/discover.py` — resolve the query to seed entities
- `loci_v2/observe.py` — walk entity_edges, read carry-over table rows
- `loci_v2/analyze.py` — compute verdicts per slot, rank salience, cluster episodes
- `loci_v2/render.py` — produce the final text (slot-with-verdict format)

**Why this matters for the benchmark:** phased separation makes it trivial to swap the renderer. If we want to A/B-test the tree render vs. the slot-with-verdict render, we swap one module. Our current architecture couples them.

### 5. Checkpoint + run-log pattern

Sophie tracks what mempalace has already seen so repeated runs don't duplicate findings:

- `run-log.json` at the project root — records `{timestamp, git_sha}` on each run
- `[mempalace-checkpoint]` commit prefix — any commit with this prefix is a checkpoint marker
- Either source recovers the other. On cold start (neither), full history is observed.

This lets her `optimization_writer.rs` avoid re-creating task files for findings that were already reported and closed in the prior run.

**Why it matters for us:** when next_soy becomes a real system (not just a benchmark harness), it'll be *continuously updated* from real SoY as the user works. A checkpoint pattern lets us incrementally re-run the entity_edges backfill as new data arrives without redoing everything. This isn't a V1 blocker but it's worth designing for — our schema should have a `last_seen_at` column somewhere that checkpoint logic can consult.

**Concrete add to the schema doc:** a `next_soy_meta` table with `(key, value, updated_at)` rows including `last_backfill_sha`, `last_gmail_ingest_at`, `last_episode_cluster_at`. Mirror real SoY's `soy_meta` pattern.

---

## Two things I would NOT copy from mempalace

### 1. Her arm A definition

"No context at all" is a strawman baseline. It makes everything look heroic compared to it. For benchmarks to produce actionable signal, arm A should be **the reasonable default you'd use if arm C didn't exist**. For us that's flat SQL search. For her it probably should have been something like "the project's README plus a ls of the task directory." Something a competent developer would check without tooling.

Our choice of flat-SQL-as-A is correct. Don't change it.

### 2. The fixed 7-slot list as-is

Her slots are shaped for Claude Code workflow observation:
- `recent-activity`, `active-chains`, `open-tasks`, `role-usage`, `cross-chain-connections`, `workflow-archetypes`, `routing-index`

Our slots need to be shaped for personal data context:
- Something like: `entity-brief`, `active-projects`, `recent-interactions`, `decisions-context`, `related-notes`, `episode-context`, `connected-concepts`, `data-gaps`

The **pattern** (fixed slots + verdicts) transfers. The **specific slots** don't. Design our own.

---

## Revised view on our constructive panel's three proposals

Sophie's work provides external evidence relevant to all three proposals:

### Aisha's render rewrite — **VALIDATED, stronger than the original proposal**

Aisha proposed per-entity narrative blocks. Sophie's fixed-slot-with-verdicts format is a superset: it's per-slot narrative with explicit verdicts. Adopt the stronger version. Aisha's diagnosis (*"the [via X.Y] edge labels are schema metadata leaking into the prompt"*) is confirmed by Sophie's format having zero such leakage and scoring 5/5 on relevance.

**Updated plan:** implement loci_v2's renderer as fixed-slot-with-verdicts, not tree. Aisha's proposal + Sophie's verdict pattern.

### Takeshi's community detection walk — **PARTIALLY VALIDATED via a different path**

Takeshi proposed Louvain/label-propagation clustering over the full graph, with per-community summaries. Sophie's `archetypes.rs` does clustering on workflow co-occurrence (not exactly Louvain, but the same category — unsupervised clustering of observed patterns) and it produces useful named archetypes. However, Sophie's clustering substrate is **sessions**, not entities. Her "process / docs / chain" archetype is a cluster over session types, not a cluster over contacts-and-projects.

The open question is whether auto-clustering works on OUR substrate. Takeshi's proposal assumed yes; Sophie provides no direct evidence. Her approach works because Claude Code sessions naturally cluster by tool-usage patterns — there's a rich signal. Our personal data graph may or may not have equivalent signal; we'd have to try.

**Updated plan:** keep Takeshi's proposal deferred behind Aisha's render rewrite. If next_soy + loci_v2 leaves specific prompts underperforming, auto-clustering is a candidate. But don't front-load it.

### Lena's briefing cache — **DE-PRIORITIZED**

Sophie's T26 spike directly tests the smart-traversal / filter-at-serve-time approach Lena proposed. Her data says it's a cost optimization, not a quality one, and the cost doesn't pay for itself at realistic context sizes. She closed the spike. We should too.

**Updated plan:** drop Lena's briefing cache from the roadmap unless we hit a specific cost ceiling (e.g., if we someday run loci on every API call instead of once per session).

---

## One structural idea I want to steal for next_soy

Sophie has a concept of "routing health" — a whole module (1050 lines) analyzing the project's own navigation document for stale signals, dead links, and gap candidates. It's a self-health-check on the project's documentation layer.

**The transferable idea:** next_soy should have a similar "schema health" view that surfaces:

- Unresolved `linked_projects` values from the backfill
- Duplicate contacts (James Andrews style)
- Contacts with zero interactions despite high relevance
- Wikilinks that are ambiguous (one alias resolving to multiple entities)
- Entity edges pointing at deleted rows
- Episodes with zero members

Sam's `schema_invariants` table in the schema panel was 90% of this idea. Sophie's routing_health pattern is a working example of what to do with those invariant results: render them into a user-facing report that's part of the loci output.

**Concrete add to next_soy_schema_v1.md:** instead of deferring `schema_invariants` entirely, reclaim it for V1 — not as the full production pattern Sam described, but as a lightweight "data health slot" that shows up in the loci output whenever there's a quality issue the user should know about. It's the schema-level analog of the `routing_index` slot Sophie already has working.

---

## What the evidence from Sophie's work adds to our synthesis

Putting our four-tier benchmark next to Sophie's 16-prompt benchmark, both running their own loci-style-context-vs-flat-retrieval comparison:

| Evidence | Our finding | Sophie's finding |
|---|---|---|
| Does rich structured context help? | Yes, modestly (0.23 on Claude relevance) | Yes, dramatically (4.37 on relevance) |
| Does it scale with model capability? | Yes, non-monotonic, sweet spot at 14B | Not tested directly |
| Does tree format work? | Works but undersells (Maya + Sara caveats) | Not tested — used fixed slots |
| Does fixed-slot format work? | Not tested | Yes, arm C = 5/5 |
| Is smart traversal worth the complexity? | Not tested | No, close the spike |
| Does auto-clustering produce useful archetypes? | Not tested | Yes, on session substrate |
| Do same-family judges collapse gradients? | Yes (Qwen 14B flagged what Opus missed) | Not tested — single judge |

**The combined story:** the hypothesis that *structured context helps LLMs reason about personal data* is credibly supported by two independent experiments. The magnitude of the benefit depends heavily on:
- How bad the baseline is (Sophie's zero-context baseline exaggerates; our flat-SQL baseline doesn't)
- Whether the output format matches the substrate (Sophie nailed this; we didn't)
- Whether the underlying data is dense enough to support navigation (Sophie has Claude session transcripts; we have a sparse CRM)

**The next_soy + loci_v2 work should target the first two directly**: keep our realistic baseline, but adopt Sophie's format. The third (data density) is separately addressed by the Gmail ingest from `email_gap_findings.md`.

---

## Concrete action items

From this analysis, things worth doing that weren't already in the plan:

1. **Revise `next_soy_schema_v1.md`'s render-layer assumption.** The schema is fine; the implicit expectation that loci_v2 would render a tree needs updating. loci_v2 should produce fixed slots with verdicts, not a tree with edge labels. This is a note in the schema doc's "what happens after DDL is reviewed" section, not a schema change.

2. **Add a `next_soy_meta` table to the schema DDL.** Checkpoint pattern for incremental re-backfill. `(key, value, updated_at)`.

3. **Reclaim `schema_invariants` for V1 (lightweight version).** Deferred in the schema doc; Sophie's routing_health pattern shows it has real value as a user-facing "data health" slot. Not the full production invariants system, but a 3-4 row seed with checks for duplicates, orphan edges, ambiguous wikilinks.

4. **Design loci_v2 as four phases (discover / observe / analyze / render)** instead of the current monolithic expanders-and-renderer coupling. Not a schema change, an implementation architecture.

5. **Drop Lena's briefing cache from the active roadmap.** Sophie's spike is convincing evidence that smart filtering at serve-time isn't worth the complexity. Add a note to `constructive_panel.md` or `next_soy_implementation_plan.md` pointing at Sophie's T26 spike as the reason.

6. **Treat Takeshi's community walk as a V1.5 candidate**, gated on "did loci_v2 with fixed slots already solve the C1-style prompts?" Sophie's archetype clustering is adjacent but not directly transferable; we'd build our own on entity_edges co-occurrence.

7. **Consider adopting the `[mempalace-checkpoint]` commit prefix pattern.** When we have a real incremental backfill story, a commit convention like `[next_soy-checkpoint] backfill through 2026-04-11` makes it trivial to find the last known-good state from git alone.

---

## What I did NOT do in this analysis

- Did not read the full 1054-line `loci_writer.rs` — the benchmark report told me the output format more efficiently than the code would have
- Did not build the Rust project or run mempalace against our SoY data — architectural-fit is wrong (she reads Claude sessions, not CRM rows)
- Did not try to merge her benchmark prompts into ours — her prompts test different questions
- Did not copy her arm B definition (routing trigger list) — our arm B (SoY-as-it-is with get_profile) is a better baseline for our substrate
- Did not examine `observer/bardo.rs` or the bardo integration — bardo is a separate Sophie project we don't have

---

## One thing that surprised me that you should probably tell Sophie

Her benchmark has a finding buried in section 2.1 (adversarial bucket, A3) that **exactly mirrors** what we found on the first Mistral 7B run: *"arm C follows stale LOCI data without qualification when the question asks for a specific current fact."* She calls it "navigation loss" and says the fix is a system-prompt hedge ("verify from chain file"), not more data.

We hit the same failure mode with Mistral 7B's arm C on R4 (*"where things stand with Jessica"*). Mistral's arm C got lost in the tree and refused; hers got over-confident in stale state and named the wrong task. Different model, different output format, same structural failure: **context-layer output presented as authoritative when the underlying data is stale**.

**The shared learning:** any loci-style context assembly needs an explicit freshness signal in its output. "This data is from checkpoint 2026-04-11; verify specific chain state against the chain file" should be structural, not optional. We should add a `checkpoint_at` field to our loci_v2 output that the answering model can use to hedge current-state claims.

That one design note is the single highest-leverage thing in this document.
