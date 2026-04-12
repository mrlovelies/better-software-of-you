# Loci architecture findings — context for mempalace-rust

Technical notes from an independent graph-traversal context-assembly experiment (schema: `next_soy`; walker: `loci_v2`). Five findings mapped to `mempalace-rust`'s architecture, four anti-patterns, four unresolved threads. Shared for cross-pollination; the mapping sections are architectural suggestions, not prescriptions — evaluate against the actual `mempalace-rust` code.

**Experimental context (abbreviated):** parallel SQLite schema with 22 tables, ~500 rows, ~380 typed edges in an `entity_edges` table. BFS walker with two generic expanders (outbound/inbound) and a narrative renderer. Head-to-head benchmark: 17 prompts × 3 retrieval arms (flat, SQL-as-is, loci) × 2 model tiers against flat-search baseline. V1 just landed; V2 improvements captured below.

---

## Finding 1 — Episode containers beat edges alone for cross-entity framing questions

### The failure mode

A benchmark prompt of the form *"How does X relate to Y"* — where X and Y share a conceptual framing but no direct foreign-key relationship — failed at every model tier in our V1. Specifically: two projects that both fit the framing "private owner-facing intelligence layer distinct from the public product," with the shared framing living only in prose across multiple notes. Flat search couldn't answer it. Per-entity graph walk couldn't either, because the connecting concept wasn't materialized as an edge.

### The fix

A first-class `memory_episodes` table with explicit member rows:

```sql
CREATE TABLE memory_episodes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT,
    started_at TEXT,
    ended_at TEXT,
    episode_type TEXT,      -- project_phase, conceptual_thread, life_event, relationship_phase, user_defined
    emotional_tone TEXT
);

CREATE TABLE episode_members (
    episode_id INTEGER,
    entity_type TEXT,
    entity_id INTEGER,
    role TEXT,              -- protagonist, witness, setting, artifact, member
    PRIMARY KEY (episode_id, entity_type, entity_id)
);
```

Episodes are **hand-authored, not auto-clustered**. We seeded four explicitly ("Operator intelligence layer," "VO career 2026 push," "Axe throwing day job," "Estate planning 2026") with explicit membership lists. The walker is episode-aware: during BFS, when it reaches an entity that's a member of any episode, it surfaces the whole episode as a context card at the top of the output. The part_of_episode edge is materialized alongside the episode_members row so the walker discovers it through the generic outbound expander without special-casing.

### Why hand-authored matters

Auto-clustering from co-occurrence would not have answered the cross-entity framing question, because the shared framing lives in prose that only a human or an LLM pass can interpret. The panel warning we honored: never run an LLM extraction pass to populate episodes, because once LLMs are writing to your graph, debugging retrieval becomes debugging the extraction pass and the audit trail disappears. V1 uses hand-authored seeds only; V2 deferred auto-clustering as a separate research thread.

### Mapping to mempalace-rust

`workflow-archetypes` does something similar at the observation level — clustering workflow patterns with confidence metadata. The extension to consider: an explicit `named-threads` or `conceptual-episodes` slot populated from user-placed markers (special comment prefixes in task files, commit message tags, or a dedicated `EPISODES.md` the observer reads). A verdict model of **Hand-authored / Auto-clustered / Inapplicable** — paralleling Live/Thin/Inapplicable — would let the `LOCI.md` consumer distinguish high-confidence user framing from probabilistic archetypes.

Load-bearing question: does the workflow-archetype clusterer currently have a way to upgrade a cluster from "observed" to "user-confirmed"? If not, that's likely the cheapest place to add the cross-entity framing capability without building a whole new slot.

---

## Finding 2 — Ghost entries dilute recall more than missing entries

### The audit result

In our data store, **24 of 33 existing contact rows (72%) had zero real correspondence** over the past 12 months. They were aspirational/research records — "people I might want to reach out to someday" — that had been seeded without ever producing interactions. Loci walks near any related node burned budget expanding into these dead ends, which distorted seed selection, crowded out relevant nodes within the total-nodes cap, and diluted recall on multi-keyword queries.

### The fix

A `status` enum on contacts with four values: `active`, `prospect`, `inactive`, `broadcast_only`. Concrete semantics:

| status          | loci behavior                                      | example            |
|-----------------|---------------------------------------------------|---------------------|
| active          | full expansion, primary seed                      | current clients     |
| prospect        | not walked, visible in inspector only             | aspirational leads  |
| inactive        | walked only on explicit query, low weight         | archived relationships |
| broadcast_only  | never expanded; orientation context only          | newsletter senders  |

The walker's seed selection and expand_outbound/expand_inbound both filter against this column. `prospect` rows stay in the DB — they're still valid research artifacts — but the walker never reaches them. The 72%/28% split was enough to produce measurable recall improvement on benchmark prompts because the walker's total-nodes budget now reaches more signal-bearing nodes before the cap.

### The load-bearing distinction

"Missing because never happened" vs. "missing because deliberately filtered" are semantically different silences, and the LLM consuming the context needs that distinction. Our V1 initially conflated them, so the LLM would hallucinate activity in gaps that were actually intentionally-filtered dead zones. The fix wasn't just filtering — it was *making the filter visible in the output* so the LLM knows the silence is deliberate.

### Mapping to mempalace-rust

The three-verdict model (Live / Thin / Inapplicable) already handles this at the *slot* level, which is the right shape. The extension to consider: the same verdict discipline *within* a slot at the observation level. Concretely:

- `recent-activity` already filters by the checkpoint window, but task chains that haven't moved in N days within the window are different from chains that are actively stalled — one is "quiet on purpose," one is "broken silently." A `stale-active` vs `stale-archived` distinction inside `active-chains` would let `LOCI.md` surface the difference.
- `open-tasks` might benefit from a similar annotation for tasks that were touched once and never progressed. These are the `prospect`-equivalents — they should appear in the inspector slot but not distort workflow-archetype clustering.

The general pattern: **every filter that excludes observations should emit an explicit "I excluded N things of type X because Y" line somewhere in the output.** It's the single cheapest way to stop the LLM from synthesizing over invisible gaps.

---

## Finding 3 — One generic walker per direction beats N per-type expanders

### The V1 mess

V1 loci had ~10 per-entity-type expander functions (`_expand_contact`, `_expand_project`, `_expand_decision`, `_expand_standalone_note`, etc.) in ~800 lines of Python. Every new entity type added a new expander; every new relationship type added code. The expander files were a maintenance tax and a source of silent drift — schema changes would leave edge walks stale without any compile-time check.

### The V2 rewrite

V2 collapsed all expanders into two generic calls against a single `entity_edges` table:

```python
def expand_outbound(conn, node, breadth):
    rows = conn.execute("""
        SELECT * FROM (
            SELECT dst_type, dst_id, edge_type, weight, metadata,
                   ROW_NUMBER() OVER (
                       PARTITION BY edge_type
                       ORDER BY weight DESC, created_at DESC
                   ) AS rn
            FROM entity_edges
            WHERE src_type = ? AND src_id = ? AND ended_at IS NULL
        ) WHERE rn <= ?
    """, (node.entity_type, node.entity_id, breadth)).fetchall()
    return resolve_rows(conn, rows, direction="outbound")

def expand_inbound(conn, node, breadth):
    # Mirror of expand_outbound with dst swapped for src
    ...
```

~40 lines of Python replaced ~800. Adding a new edge type is a data change, not a code change.

### Why PARTITION BY is load-bearing

Without the window function, a node with 50 `email_with` edges crowds out a single `client_of` edge — the breadth budget fills with low-information neighbors. With `PARTITION BY edge_type`, breadth is budgeted *per edge type*, so the walker always sees at least one client/project/decision even on a contact with hundreds of email rows. This was the single biggest quality lift in the V2 rewrite that wasn't directly attributable to the schema change.

### Mapping to mempalace-rust

If `mempalace` grows to track relationships between observations — a task referencing another chain, a role co-appearing with tasks, a commit touching multiple task files — a unified `observation_edges` table scales better than per-relationship traversal code in the observer. Specifically:

```rust
struct ObservationEdge {
    src_type: ObservationKind,
    src_id: String,
    dst_type: ObservationKind,
    dst_id: String,
    edge_kind: EdgeKind,              // references, co_occurs, touches, derives_from
    weight: f32,
    created_at: DateTime<Utc>,
    metadata: Option<serde_json::Value>,
}
```

Cost: one table + two query functions. Benefit: adding a new edge kind is a `EdgeKind` enum variant, not new observer code. The `cross-chain-connections` slot would read directly from this table via the same PARTITION BY trick.

Concrete flag: the current README mentions `cross-chain-connections` surfacing chains that share roles, tasks, or file paths. If those three "sharing" relationships each have their own observation code path, collapsing them into a single `observation_edges` table with `edge_kind ∈ {shares_role, shares_task, shares_file}` is the refactor that pays off when the list grows to six or eight kinds.

---

## Finding 4 — Narrative render with deterministic field-backed claims

### The V1 problem

V1 rendered a neighborhood as a tree with `└── via X.Y` edge labels. Two failure modes observed:

1. LLMs wasted tokens parsing the ASCII tree structure instead of the content.
2. LLMs frequently synthesized claims that weren't in the raw rows — hallucinating because the rendered output felt "generated" rather than "quoted."

### The V2 render

Per-entity paragraphs with bulleted related lists, each bullet optionally followed by a second-line detail drawn from the source row:

```
## Jessica Martin — Client / Founder at The Grow App

Client for The Grow App. Phase 1 contract $3,750 ($1,250 deposit received).
Pitch deadline July 2026.

Related:
  - The Grow App [active, target 2026-07-01] [client_of ← reverse]
  - Decision: Stripe over Shopify for payments (2026-03-02) [involves_contact ← reverse]
      Rationale: Stripe Checkout is leaner than going through Shopify...
  - Task: Mailchimp integration [blocked] [belongs_to_project ← reverse]
```

### The invariant

**Every claim must trace back to a specific row field.** No synthesized prose, no counts the walker hasn't computed, no inferred relationships. "Client for The Grow App" comes from `contacts.notes`. The decision rationale comes from `decisions.rationale`. The status comes from `projects.status`. The LLM can synthesize on top of deterministic raw material, but if the context itself is synthesized, hallucination compounds through the pipeline and becomes impossible to debug.

Concretely: the renderer refuses to produce any output that isn't a direct function of (row, edge type, optional metadata). No templates with gap-filling. No "summary" fields computed at render time unless they're stored in the schema. This constraint felt limiting initially but eliminated an entire class of benchmark failures where the LLM would confidently describe relationships that didn't exist.

### Mapping to mempalace-rust

`LOCI.md` is already in roughly this shape — structured sections, verdicts per slot, derived from observations. The extension to consider: **second-line detail beneath each bullet in high-information slots**.

Examples from the existing slots:

- `recent-activity`: below each session entry, one line of the session's actual topic (drawn from a Claude Code message or a task file, not synthesized).
- `active-chains`: below each chain's current-step line, one line of the actual task title or commit message — not a summary, the raw string.
- `open-tasks`: below each task, one line of the task file's first non-metadata paragraph.

Cost: 3-5 extra lines per populated bullet in `LOCI.md`. Benefit: the LLM reading the brief has deterministic anchors for every claim and stops reaching for plausible-sounding details that aren't in the source.

The shape to avoid: putting a *summary* on the second line. Summaries are synthesized, and synthesized content in the context layer is where hallucination pipelines start.

---

## Finding 5 — Keyword match-count seed ranking (subtle retrieval bug worth naming)

### The bug

Our V1 seed selection iterated by keyword and broke early when the seed budget filled:

```python
for keyword in query_keywords:
    for table in tables:
        add_matches(table, keyword, limit=5)
        if len(seeds) >= max_seeds:
            break
```

Failure mode: generic keywords like "things," "some," "land" consumed the budget before specific keywords like "chemo" were ever searched. One benchmark prompt ("tell me about mom's treatment situation") failed across every model tier because the single interaction containing "mom is in chemo" was never seeded — the budget was full of generic matches before the relevant keyword was reached.

### The fix

Every keyword searches every table. Seeds are then ranked by *how many distinct keywords each entity matched*, not by insertion order:

```python
hits_by_key = {}
for kw in keywords:
    for table in tables:
        for row in search(table, kw, limit=per_table):
            k = (row.entity_type, row.id)
            hits_by_key[k] = hits_by_key.get(k, 0) + 1
seeds = sorted(hits_by_key, key=lambda k: -hits_by_key[k])[:max_seeds]
```

Multi-keyword matches naturally surface above generic single-keyword matches. A single fix that recovered an entire class of failing benchmarks.

### Mapping to mempalace-rust

Does not apply directly because `mempalace-rust` isn't running free-text query over a corpus — it's observing fixed structure and emitting. However, if mempalace grows a mode where `LOCI.md` is generated *in response to* an incoming Claude session's actual first-message prompt (a "brief me on what's relevant to *this* specific question" mode, not just "brief me on what's been happening lately"), this pattern is the first optimization to reach for.

The general principle generalizes beyond retrieval: **any greedy iteration over a ranked dimension that can fill a budget will produce this bug.** Worth scanning the codebase for any `for item in ranked_list: if budget_full: break` pattern; that's the shape of the bug.

---

## Anti-patterns we deliberately avoided

Four things we ruled out after running a schema architecture panel on the design, in case any of them are temptations in `mempalace-rust`:

### 1. No LLM-based auto-extraction into edges

Edges come from either (a) structural FK backfill from the source schema, or (b) explicit user action (hand-authored wikilinks, typed mentions, user-placed markers). **Never from an LLM parsing row content.** The warning: once an LLM is writing to your graph, debugging retrieval becomes debugging the LLM's extraction pass. You lose the deterministic audit trail and every bug becomes "did the LLM hallucinate this edge, or is this a retrieval failure?" Painful in benchmarks, catastrophic in production when users are trying to understand why a specific relationship surfaced.

### 2. No per-write LLM summarization into episodes

Same principle, applied to the episodes layer. V1 `memory_episodes` are hand-authored only. Auto-clustering of episodes from temporal or co-occurrence signal is deferred as a V2 research thread — the point of V1 is to validate that explicit episodes unlock cross-entity framing questions *at all* before investing in automatic episode detection.

### 3. No salience auto-derivation in V1

We had a designed-but-deferred `edge_salience` table with recency/frequency/valence weighting. Deferred explicitly to measure the uniform-weight baseline first. Salience is an *optimization over a working system*, not a *requirement for the system to work*. Measuring without it first gives a clean delta to attribute the optimization later — "did adding salience help, and by how much?" — which is impossible if salience was present from day one.

### 4. No polymorphic tag table splits

Real temptation: typed tag tables (`contact_tags`, `project_tags`, `task_tags`) feel cleaner than a polymorphic `entity_tags(entity_type, entity_id, tag_id)`. Panel was unanimous: splits add migration cost for zero data-quality improvement, and the polymorphic table is already correct. The single place where the polymorphic shape breaks — a future need for tag metadata specific to one entity type — is cheap to add as an override column, not a table split.

Mapping note: mempalace's `role-usage` slot probably has an analog here. If roles ever gain type-specific metadata (a role has different properties depending on which task kind it appears in), the temptation will be to split the role table. Don't — add an override column instead.

---

## Unresolved threads

Four questions we haven't fully answered. Flagged here because `mempalace-rust`'s design might have answers we don't, or because the answers in `mempalace-rust` might influence its own future design.

### 1. Checkpoint drift under silent history rewrites

Our V1 snapshot manifest records the source DB's mtime + row counts per core table at seed time. This catches "the source changed" but doesn't catch "the source was rewritten to look the same" — a history rewrite, a restored backup, or a silent re-import could produce an identical manifest with different underlying content.

The mempalace `[mempalace-checkpoint]` commit prefix + `run-log.json` is a cleaner solution for a git-backed source, but has the same weakness under `git rebase -i` + force push. Question we're chewing on: is a content hash per row (blake3 over the canonical row serialization) cheap enough to include in the manifest? What's mempalace doing here?

### 2. Live / Thin threshold calibration

Our V1 has hard buckets (active / prospect / inactive / broadcast_only) assigned by the audit. We're not sure whether a continuous score with a configurable threshold would've been smarter. mempalace's Live/Thin/Inapplicable discipline feels more honest than our boolean-ish status, but we're curious how the threshold between Live and Thin is determined, and whether it's static or derived from the corpus size. A project with 5 sessions has a different signal density than one with 500.

### 3. Workflow archetype clustering without LLMs

We ended up with no auto-clustering for episodes — hand-authoring only — because every auto-clustering heuristic we tried (temporal proximity, member overlap, co-occurrence in notes) produced clusters that were obvious-in-retrospect but not useful-in-practice. This feels like a loss, but was the right V1 call given the anti-pattern list above.

mempalace's `workflow-archetypes` slot includes "clustered workflow patterns with confidence metadata." If that clustering is producing useful surfaces in practice, the specific clustering criterion would be informative — we'd like to know whether it's role co-occurrence, file-path co-occurrence, temporal proximity, or something else. Particularly interested in whether your clusters feel stable across repeated runs on a mature repo, or whether they churn.

### 4. Synthesis trigger: LLM in the loop vs. deterministic render

`mempalace-rust` has a `SynthesisTrigger` trait with a pluggable emitter. We ended up rendering narratives directly in the walker with zero LLM in the loop — every output character is traceable to a row field. The trade-off we keep circling:

- **LLM-in-the-loop synthesis** produces richer, more flexible output. Better at handling unusual neighborhoods. Unlimited expressive range.
- **Deterministic rendering** is auditable and debuggable. Every claim can be traced to a field. No hallucination in the context layer.

Hard to have both. Our V1 chose deterministic; mempalace's trait-based design suggests either is reachable. If `mempalace-rust` is currently using a deterministic emitter (or stub), the trait is the right place to stage a future LLM-backed synthesis path without changing the observer core — that matches our thinking. If it's already LLM-backed, the question becomes: what's the grounding discipline that keeps the synthesized output traceable to observations?

---

## Schema reference (inline, for Claude consumption)

Minimal DDL for the concepts referenced above. Annotated to flag which parts are load-bearing.

```sql
-- Load-bearing: every walk consults this table via two generic expanders.
CREATE TABLE entity_edges (
    id INTEGER PRIMARY KEY,
    src_type TEXT NOT NULL,
    src_id INTEGER NOT NULL,
    dst_type TEXT NOT NULL,
    dst_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    established_at TEXT,
    ended_at TEXT,                      -- NULL = active; set this, don't delete
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'backfill', 'wikilink', 'import', 'merge', 'user_pin')),
    metadata TEXT,                       -- JSON; edge-type-specific
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (src_type, src_id, dst_type, dst_id, edge_type)
);

-- Partial index for the common case (walks usually want active edges only)
CREATE INDEX idx_edges_active ON entity_edges(src_type, src_id) WHERE ended_at IS NULL;

-- Load-bearing for cross-entity framing questions (Finding 1).
-- Members are hand-authored in V1; auto-clustering is deferred.
CREATE TABLE memory_episodes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,                       -- NULL = ongoing
    episode_type TEXT CHECK (episode_type IN (
        'project_phase', 'relationship_phase', 'life_event',
        'conceptual_thread', 'user_defined'
    )),
    emotional_tone TEXT
);

CREATE TABLE episode_members (
    episode_id INTEGER REFERENCES memory_episodes(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    role TEXT,                           -- protagonist, witness, setting, artifact, member
    PRIMARY KEY (episode_id, entity_type, entity_id)
);

-- Load-bearing for ghost-entry filtering (Finding 2).
-- Applied by both seed selection and walk expansion.
ALTER TABLE contacts ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'prospect', 'inactive', 'broadcast_only'));
```

The walker's traversal loop is the two-expander pattern from Finding 3. The renderer is the field-traceable pattern from Finding 4. The seed ranker is the match-count pattern from Finding 5. All three of these compose; none requires the others, but the win from each compounds when all three are in place.
