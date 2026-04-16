# Constructive Panel — How to Make Loci Actually Work

Three practitioners were handed the four-tier synthesis, the current `shared/loci.py` implementation, and the 17-prompt set, and asked the same question: the hypothesis that structured context preserving associative paths should beat flat retrieval isn't falsified, but the current BFS-tree implementation isn't delivering the dramatic gains the hypothesis predicts — what would you change to flip the numbers?

## Aisha Raman — "Fix the render format"

**Diagnosis:** The `render_subtree` function in `loci.py` is producing a format LLMs parse poorly: `└── Decision: Stripe over Shopify [via decision.project_id]` with an indented 200-char detail fragment floating below it on a separate line. That `[via X.Y]` edge label is schema metadata leaking into the prompt — the model has to translate "decision.project_id" into "this decision belongs to the project we started from" before it can use the fact, and smaller models don't pay that translation cost, they just hallucinate around it. Sara Okonkwo's judge-confidence anomaly (arm C scoring 4.71 confidence on Mistral's worst answers) is the giveaway: the tree format *looks* authoritative to both the generator and the judge while actually degrading grounding.

**Proposed fix:** Replace the tree renderer entirely with a per-entity narrative block format. Each top-level seed gets a short prose paragraph that reads like a briefing, with relationships expressed in natural language ("Jessica Martin is the client on The Grow App; her most recent email on April 10 refined the paywall scope and mentioned her mom is in chemo at Sunnybrook"), followed by a compact bulleted "related" section for the neighborhood. Kill the `└──` characters, kill the `[via X.Y]` edge labels entirely, and move the 200-char detail cutoff to 600 chars for `standalone_note.content`, `interaction.summary`, and `email.snippet` (the existing recommendation 3 from the synthesis). Keep the loci walk exactly as-is; this is a render-only change, maybe 80 lines of Python.

```
## Jessica Martin — Founder of Grow & The Grow App (client)
Alex is building The Grow App for Jessica. Most recent contact was an
inbound email on 2026-04-10 where Jessica refined the paywall scope
(Shop tab + profiles + cart paywalled; News/About/Membership free),
asked about sharing curated gardening videos to the News feed, and
mentioned her mom is in chemo at Sunnybrook this week, doing well.
She's working on the business plan this weekend and mentioned a
possible EY grant to pay Alex a salary.

Active decisions on The Grow App:
- Stripe over Shopify (2026-03-02)
- Paywall scope refined (2026-04-10) — matches the email above
- Trust-based auth, Vercel+GoDaddy, deferred postal-code restrictions

Other threads near Jessica: [no other projects, no follow-ups logged]
```

**Why this preserves the hypothesis:** The associative path is still preserved — the narrative explicitly encodes "walking from Jessica to the decisions via the project, to the email via the contact" in prose. The graph walk isn't changing; only the surface presentation of what the walk found. This is still loci — structured context that preserves relationships — just rendered in a format an LLM can actually parse without a translation step.

**Prediction:** P1, P3, P4, C2, S3, A2 all jump 1-2 points at the Claude tier, because the model stops having to reverse-engineer the tree. Mistral's catastrophic arm C collapse (relevance 2.29, hallucinations 0.82) recovers to roughly B-baseline parity because the fluent prose format is what Mistral was trained on. Qwen 14B gains another 0.3-0.5 on completeness. Risk of regression: R1 and R2 (ceiling prompts) are unaffected, S1 might lose slightly if the narrative enrichment over-editorializes.

**Riskier variant:** Skip the deterministic template entirely and have a cheap model (Qwen 14B on Lucy, ~4s) rewrite the raw neighborhood into a narrative brief as a pre-pass before handing it to the test model. You turn loci into a two-stage pipeline: walk + summarize. That could unlock huge gains or introduce a whole new class of hallucinations in the summarization step.

## Dr. Takeshi Okoye — "Replace the walk strategy"

**Diagnosis:** The current `assemble_context` is a raw BFS from LIKE-search seeds with uniform breadth/depth caps — it's the retrieval equivalent of a 2019 baseline. The `max_breadth_per_node=5, max_total_nodes=60` parameters do nothing query-specific: C1 (the Reprise↔BATL connection prompt that stayed stuck at 1-3 across every model) fails because BFS from both project seeds walks their tag neighborhoods independently and never materializes the *shared concept* ("private intelligence layer for the operator") that lives in the overlap. The tag-intersection walk in `_expand_standalone_note` is the one primitive in the code that could find this, and it's buried two hops deep behind a contact-first or project-first seed.

**Proposed fix:** Replace the BFS core with a two-phase query-guided walk inspired by GraphRAG's community detection. Phase 1: precompute (once, cached) community clusters over the full SoY graph using a simple Louvain or label-propagation pass on the combined FK + tag-intersection edges, and generate a one-sentence summary per community ("private intelligence layer work: Reprise competitive analysis, BATL ops dashboards, Specsite analytics"). Phase 2: at query time, classify the query as local (single-entity) or global (cross-community), and for global queries, *start the walk from community summaries, not from LIKE-matched row seeds*. For local queries, keep the BFS but add query-term scoring to prioritize which children to expand. The BFS core in `assemble_context` becomes one strategy among several; the dispatch is based on query shape.

```
def assemble_context(db, query, ...):
    shape = classify_query(query)  # "local" | "global" | "temporal"
    if shape == "global":
        communities = load_or_build_communities(db)
        relevant = rank_communities(communities, query)
        return assemble_from_communities(db, query, relevant)
    elif shape == "temporal":
        return assemble_temporal(db, query)  # time-window walk
    else:
        return assemble_local(db, query)  # current BFS, refined
```

**Why this preserves the hypothesis:** Communities are associative paths, just precomputed and summarized. The hypothesis says "walked neighborhood presented as a narrative of connections beats a flat dump" — community summaries are literally that, at the right granularity for cross-project questions. The BFS variant is still there for local queries where it works.

**Prediction:** C1 finally breaks above 3 at Claude (probably 4-5) because "private intelligence layer" is exactly the kind of cross-project concept that a community detector would surface as a cluster summary. C3 (axe throwing ↔ BATL Lane Command) also improves because community clustering over tags would group venue-operations work together. S2 and P4 (silent drift, what's fallen off my radar) improve at Qwen 14B and up because global queries get a structured comparison across communities rather than a flat node dump. Risks a regression on R1/R2 if the classifier mis-routes single-fact queries through the global path.

**Riskier variant:** Drop SQL LIKE seed selection entirely and use local sentence embeddings (bge-small, ~50MB) to do semantic seed selection and community ranking. This would fix A2 (the chemo descriptor) cleanly because "client whose mom is going through chemo" would embed near the Jessica interaction summary regardless of keyword overlap, but it introduces a new dependency and a new class of embedding drift failures.

## Lena Bertillon — "Narrow the scope"

**Diagnosis:** Loci is trying to be a general-purpose retrieval layer that beats flat search on arbitrary queries, and the benchmark scored it that way: 17 prompts spanning recall, prep, connection, synthesis, and adversarial. The synthesis says the quiet part out loud — "Prep is loci's best home turf on capable models" and arm C wins Prep at Qwen3 30B and Claude. Every other bucket is marginal or worse. The current implementation is paying the complexity and hallucination cost of general-purpose graph walking for gains that only show up on one prompt shape. At Claude, arm C beats arm A on Prep by 0.25 points; on Recall it loses by 0.25. You're buying the wins and the losses in a single product.

**Proposed fix:** Repurpose loci as a *pre-computed briefing engine* for exactly three task shapes — contact prep, project status, and "what fell off my radar." Nightly (or on-write), generate a cached briefing blob for every active contact and active project: a fixed-schema markdown doc with "recent activity," "open threads," "decisions," "related projects," "open follow-ups." Store these in a new `briefings` table keyed by entity. At query time, classify the query: if it's a prep/status query, fetch the cached briefing directly (no walk). If it's anything else (recall, adversarial, arbitrary synthesis), fall back to flat search — the benchmark shows loci doesn't help those anyway. The `loci.py` module becomes a briefing generator that runs offline, not a query-time assembler.

```
# Nightly job (or write-triggered):
for c in active_contacts(): briefings.upsert(generate_contact_brief(c))
for p in active_projects(): briefings.upsert(generate_project_brief(p))

# Query time:
def assemble_context(db, query):
    intent = classify(query)  # "prep" | "status" | "radar" | "other"
    if intent in ("prep", "status"):
        entity = resolve_entity(query)
        if entity: return briefings.get(entity) or flat_fallback(query)
    if intent == "radar":
        return assemble_radar_briefing(db)  # specialized aggregate
    return arms.flat(query)  # known-good baseline for everything else
```

**Why this preserves the hypothesis:** The briefings themselves are structured context preserving associative paths — each one is a walked neighborhood, narratively rendered, centered on a specific entity. The hypothesis never said it had to happen at query time. Moving the walk from synchronous-on-query to async-on-write keeps the associative structure and adds quality (you can spend 30 seconds on a briefing that the current runtime tries to do in 6 seconds).

**Prediction:** R4, P1, P2, P3, P4, C2 all jump meaningfully at every tier above Mistral because the briefings are pre-curated and can afford better selection logic. The Jessica briefing contains the chemo line by default, so A2 resolves correctly at Qwen 14B and up. C1 (Reprise↔BATL) won't improve because it's cross-project — but that's fine, you're explicitly not claiming to handle it anymore. Hallucinations drop across the board because the briefings are deterministic text, not dynamic tree renders.

**Riskier variant:** Have a local LLM (Qwen 14B on Lucy) write the briefings at generation time — not template-filled, actually summarized into prose. You get much higher-quality briefings at the cost of briefing-generation becoming a hallucination surface of its own.

## Cross-persona synthesis

Aisha's render-format fix is by far the most tractable — it's maybe a day of work, no new architecture, no new dependencies, and the existing 204 data points give you a direct comparison of "did the scores improve purely because I stopped producing unreadable trees." Takeshi's community-detection rewrite is most likely to produce a dramatic improvement, specifically on the connection bucket and C1 which has stayed stuck at 1-3 across every model — but it's also a multi-week rebuild with a real chance of not working if the SoY graph is too sparse for community detection to find meaningful clusters. All three would agree that the **single riskiest assumption in the current implementation is that raw BFS output is directly consumable by an LLM without a format transform** — whether you fix that with a render rewrite (Aisha), a smarter walker that emits community summaries (Takeshi), or an offline briefing generator (Lena), the shared conviction is that the current tree with edge-label breadcrumbs is the load-bearing failure. Recommended order of experiments: (1) Aisha's render rewrite first, rerun the Qwen 14B and Claude tiers — if those two rows alone shift by 0.3+ on relevance and surfaced-non-obvious you have a publishable improvement for a week of work; (2) Lena's briefing cache for Prep-bucket prompts only, measured against the new render baseline — this tells you whether scope-narrowing beats format-fixing on the prompt shape where loci is supposed to shine; (3) Takeshi's community walk last, because it's the biggest bet and the one that benefits most from having a cleaned-up rendering layer already in place to show its work. Do not run them in parallel — the variance in the existing N=1 benchmark cells is too high to separate three simultaneous changes, and each experiment narrows the hypothesis space for the next.
