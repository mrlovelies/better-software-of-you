# Routing Panel — The Librarian and the Friend

**Date:** 2026-04-12
**Stimulus:** V2 benchmark comparison report. The data shows flat search and loci aren't competing — they're complementary. The question is no longer "which is better" but "how do you deploy both, when do you route to which, what does it cost, and where does the whole premise break down?"

**Panelists:**

- **Nadia Okonjo** — Systems architect. Has built routing layers for multi-retrieval systems. Cares about latency, fallback paths, and what happens when the router is wrong.
- **Marcus Adler** — Product designer. Cares about user experience, invisible intelligence, and the failure mode where the system "thinks it's being smart but the user didn't ask for smart."
- **Priya Nair** — Returning from the schema panel. Cognitive scientist. Will challenge whether the librarian/friend framing is real or post-hoc narrative fitting on benchmark noise.
- **Devon Sato** — Ops/cost. Cares about tokens, latency budgets, and whether the marginal quality improvement justifies the marginal compute.
- **Facilitator** — Presents data, asks pointed questions, doesn't advocate.

---

## Round 1 — Is the two-persona framing real?

**Facilitator:** The V2 benchmark shows arm C (loci) winning on prep and connection prompts by +1.0–1.5 on the C-A gap, while flat search ties or wins on recall and single-fact synthesis. The report calls these "the librarian" and "the friend." Priya, you were skeptical of framing claims in the schema panel. Is this real?

**Priya:** It's real in the data, but the framing is too neat. Let me explain what I mean.

The benchmark has 17 prompts across 5 buckets. On the Qwen 14B tier, arm C wins 9 prompts, ties 6, and loses 2. That's a genuine pattern — but calling it "two personas" implies a clean binary classification that the data doesn't support. Look at the edge cases:

- **S3** ("Has Collaborator K's feedback influenced my decisions?") is a *synthesis* prompt that requires *temporal-causal* reasoning. Neither persona handles it well. Arm B actually wins because it surfaces the interaction timestamps, which is neither "librarian lookup" nor "friend association" — it's "timeline reconstruction." That's a third mode.
- **P1** ("About to record VO with Client E") is a *prep* prompt where loci wins — but it wins by pulling studio gear details (a specific fact) rather than by discovering a pattern. That's the librarian's job surfaced through the friend's mechanism.
- **A2** regressed on arm C. The friend's broader walk made entity resolution *harder*, not easier. That's not "the friend isn't needed here" — it's "the friend actively hurt."

The honest framing is: **the data supports a retrieval-mode spectrum, not a binary.** On one end: direct fact lookup (keyword → row → answer). On the other: associative pattern discovery (seeds → walk → neighborhood → narrative). Most questions live somewhere in between, and the optimal retrieval strategy shifts along the spectrum per question.

**Marcus:** I agree with Priya on the spectrum, but I disagree that it invalidates the two-persona metaphor. Metaphors don't need to be taxonomically perfect — they need to be *operationally useful*. If the metaphor gives the router (human or machine) a fast heuristic for which tool to invoke, it's doing its job even when the edge cases are messy.

The user doesn't think "I need retrieval-mode 0.6 on the lookup-association spectrum." They think "I need a quick fact" or "help me see the picture." Two personas maps to that intuition. The edge cases are real but they're the router's problem, not the user's.

**Nadia:** I'll go further — the edge cases are the *interesting* part of the routing problem. A binary classifier that handles 80% of questions correctly is cheap and fast. The remaining 20% is where you need a fallback strategy. The question isn't "is the binary clean?" — it's "what happens when the router misclassifies?"

---

## Round 2 — How do you route with no friction?

**Facilitator:** The benchmark data suggests simple keyword heuristics could classify prompts: "connect/relate/threads" → loci, proper noun + "what/when" → flat. Marcus, from a UX perspective, how does this manifest?

**Marcus:** Three options, each with different friction profiles:

**Option 1: Invisible routing.** The system classifies internally and invokes the right retrieval path. The user never sees which persona was used. Zero friction — the user asks a question and gets an answer. This is the right default for production.

Problem: when the router misclassifies, the user doesn't know WHY the answer is thin. "I asked for connections and got a lookup" is invisible; the user just sees a bad answer and blames the system. No affordance for correction.

**Option 2: Visible personas as tools.** The MCP server exposes `search` (librarian) and `explore` (friend) as separate tools. The LLM front-end (Claude, ChatGPT, whatever) chooses which to invoke based on the question, and the tool choice appears in the response. Low friction — the model routes — but the user can see which tool was used and can redirect: "no, I meant explore, not search."

This is my recommendation. It maps to how tool-use already works in Claude Code and similar systems. The model picks the tool; the user can nudge. No new UI needed.

**Option 3: User-selected mode.** A toggle or prefix: "search: what's Collaborator K's email" vs "explore: what connects Project R to Project BLC." Maximum user control, maximum friction. Only for power users who understand the distinction.

For SoY specifically, I'd ship Option 2 and never build Option 3. The model is a better router than a keyword heuristic because it can read the full question in context. The tool descriptions do the classification:

```
search: Retrieve specific facts, decisions, emails, or contact details from SoY.
        Use when the user asks for a specific known entity or a direct fact.

explore: Walk the relationship graph to discover connections, prep context,
         or surface patterns across entities. Use when the question involves
         connecting things, prepping for a meeting, or finding what's changed.
```

The model reads both descriptions and picks. That's the router. No ML, no keyword heuristic, no new infrastructure.

**Nadia:** I'll add the fallback layer. In production, the pattern should be:

1. Model picks a tool (search or explore).
2. Tool returns context.
3. If the model's answer is thin or it explicitly flags insufficient context ("Based on available data, I can't find a connection"), the system *automatically* tries the other tool and merges.

This is the "mixed" case from the benchmark report. The cost is 2× retrieval for ~20% of questions. The benefit is: misclassification is self-healing. The user never sees a bad answer because the router was wrong; they see a slightly slower answer because the system tried both.

**Devon:** Hold on. "Automatically try the other tool" means every misclassification costs double the tokens and double the latency. At Opus pricing, a loci walk that produces 8K chars of context is ~2K input tokens per try. Two tries = 4K tokens just for retrieval context, before the model even answers. On top of the question tokens and the answer tokens.

For a personal system where Alex is the only user, the absolute cost is small — maybe $0.02 per double-retrieval query. But the *principle* matters: if you design for automatic fallback, you're designing for the assumption that the router will be wrong often enough to justify the overhead. If the router is wrong 20% of the time and each wrong classification costs an extra $0.01, that's $0.002 per query amortized. Negligible. But if the router is wrong 50% of the time, you're just running both tools on every query with extra latency. At that point, always run both and merge.

**Marcus:** Devon's math actually argues FOR Option 2. If the model is a good enough router (>80% correct classification), the amortized cost of the fallback is negligible and the UX is clean. If the model is a bad router (<60% correct), you should just always run both and merge — which is still cheap for a single-user system.

The question is: **how good is the model at choosing between `search` and `explore` from the tool descriptions alone?** We can measure that directly by taking the 17 benchmark prompts, giving a model the two tool descriptions, and asking which it would invoke. If it matches the "which arm wins" data, the router works.

---

## Round 3 — How do you make it better?

**Facilitator:** The V2 loci layer just landed. What are the highest-leverage improvements to the two-persona system?

**Nadia:** Three things, in priority order:

**1. Merge the outputs, don't pick one.** The benchmark compares arms in isolation — each prompt gets EITHER flat search OR get_profile OR loci. In production, the best answer on many prompts would be flat search (for the specific fact) PLUS loci (for the surrounding context). The "Jessica status" prompt (R4) is the clearest example: flat search finds the latest email, loci finds the decision history and project context. Neither alone is the full answer; both together are.

The implementation is straightforward: run flat search first (fast, cheap), check if the answer seems sufficient. If not, run loci, append the loci context after the flat results. The model sees both and synthesizes. Cost: ~1.5× average retrieval (flat always runs; loci runs conditionally).

**2. Feed loci's walk back into the next flat search.** When the loci walker discovers an entity the user didn't name — say, it walks from Client A to Project Alpha and discovers a related decision — that decision's id should be available for a *follow-up* flat lookup if the model needs more detail. Right now loci returns a rendered narrative; it should also return structured metadata (entity ids, edge types) that the model can use to make targeted follow-up search calls.

This is the "friend suggests, librarian fetches" pattern. The friend says "oh, this reminds me of Decision 41." The librarian then pulls Decision 41's full record. The model composes both.

**3. Add a "walk explanation" to loci's output.** Right now the narrative render shows *what* was found. It doesn't explain *why* each entity was reached — which edge was walked, at what distance, from which seed. Adding a compact walk-trace footer (collapsed by default, expandable) would let the model cite its reasoning: "I reached this decision via the client_of edge from Project Alpha." This is the provenance signal that makes the friend's output auditable.

**Priya:** I'll add a fourth, from the cognitive-science side:

**4. Use the friend's output to prime the librarian's next query.** When the user asks an exploration question, the friend produces a neighborhood. The entities in that neighborhood should update the *seed selection* for the user's next question — even if the next question is a lookup. This is the "ambient priming" pattern: the friend's walk leaves a residue that makes subsequent lookups contextually richer.

Concretely: if the user asks "what connects Project R to Project BLC?" and the loci walk surfaces the "Operator intelligence layer" episode, then the user's next question "what decisions have I made about Project R recently?" should implicitly benefit from knowing the episode context — maybe by boosting seeds that are episode members.

This is where the metaphor earns its keep. A real friend doesn't forget the last conversation when you ask them a factual question. The friend's context persists. The librarian's doesn't.

**Marcus:** Priya's point is the most important one and the hardest to implement. Session-level context persistence across retrieval calls is an architecture choice, not a feature — it means the retrieval layer has state, which flat search explicitly does not. You'd need a "session context" object that accumulates entities touched during the conversation and biases future seed selection.

For a Claude Code / MCP setup, this maps to: the MCP server maintains a per-conversation entity set that loci's `find_seeds` consults. Every loci walk extends the set. Every flat search benefits from the set for tie-breaking. The set decays over time (or conversation turns) so stale context doesn't accumulate.

---

## Round 4 — What does it cost?

**Facilitator:** Devon, give us the cost model.

**Devon:** Breaking it down for a single-user SoY system queried ~50 times per day:

### Per-query costs (Opus tier)

| Path | Context assembly | Context tokens (input) | Answer tokens (output) | Total tokens | Est. cost |
|---|---|---|---|---|---|
| Flat search only | ~5ms | ~1,500 | ~500 | ~2,000 | ~$0.01 |
| Loci only | ~15ms | ~2,500 | ~700 | ~3,200 | ~$0.02 |
| Both (merge) | ~20ms | ~3,500 | ~800 | ~4,300 | ~$0.03 |
| Both + fallback retry | ~25ms | ~5,000 | ~800 | ~5,800 | ~$0.04 |

### Daily budget at 50 queries

| Strategy | Daily cost | Monthly cost |
|---|---|---|
| Always flat search | $0.50 | $15 |
| Router (80/20 flat/loci) | $0.60 | $18 |
| Always both (merge) | $1.50 | $45 |
| Always loci only | $1.00 | $30 |

**The routing strategy is nearly free compared to always-both.** The incremental cost of occasionally running loci is $0.10/day — $3/month. For a personal system, this is noise.

### Where cost DOES matter

Token cost isn't the issue. **Latency is.** Loci assembly takes ~15ms today because the DB is small (48 contacts, 379 edges). At 500 contacts and 5,000 edges (the trajectory if Gmail ingest, calendar sync, and daily logs are all flowing), loci assembly could reach 50-100ms — still fast in absolute terms, but noticeable in a conversational flow where the user expects sub-second responses.

The fix is Sophie's tiered-delivery pattern: Tier 0 (orientation, ≤1,200 tokens) loads instantly; Tier 1+ loads on demand. For the friend persona, Tier 0 is the "quick association" — just the seed names, episode titles, and edge-type distribution. Tier 1 is the full narrative walk. Most exploration questions can be answered from Tier 0 + a targeted Tier 1 expansion on the most relevant seed.

### The real cost question

The cost that matters isn't dollars or latency. It's **attention cost.** When the friend surfaces 8K chars of narrative context that includes 45 entities across 7 types, the model has to process all of it to produce an answer. Most of that context is "adjacent but not relevant to THIS specific question." The friend is generous by nature — it walks outward and includes everything within budget. That generosity is a feature for exploration ("show me the landscape") and a cost for focused follow-ups ("just tell me about this one thing").

The V2 benchmark showed this on P1: arm C pulled Client E's studio gear (Neumann TLM 103, etc.) which is great for "prep me for recording" — but it also pulled 4 hallucinations because the model tried to USE all the context, including parts that weren't relevant. The friend's breadth is a hallucination vector when the question is narrower than the walk.

**Nadia:** Devon's attention-cost point is the key constraint for the merge strategy. If you run both and concatenate, the model sees flat results (specific, narrow) followed by loci results (broad, associative). The model has to figure out which parts of the loci output ADD to the flat results vs. which parts are noise. That's a synthesis task the model may or may not do well, depending on the tier.

At Opus, the model handles it gracefully — it can read 4K tokens of flat + 8K tokens of loci and produce a coherent answer that draws on both. At Qwen 14B, the model is more likely to get confused by the volume — which is exactly the hallucination pattern we saw on arm C.

**The merge strategy works best at the tier where it's least needed** (Opus, which is already near-ceiling on flat search alone) **and works worst at the tier where it's most needed** (Qwen, which benefits most from loci but also hallucinates most from broad context).

---

## Round 5 — Where does it fall short?

**Facilitator:** Where does the whole two-persona premise break down? Where is neither the librarian nor the friend the right tool?

**Priya:** Three blind spots.

**Blind spot 1: Temporal reasoning.** Neither persona does temporal reasoning well. The librarian finds rows by keyword; the friend walks edges by type. Neither one can answer "what happened in the last 48 hours across all my projects" because that requires a time-windowed cross-table scan that's neither a keyword match nor an edge walk — it's a temporal aggregation.

The benchmark shows this on P4 ("fallen off my radar"), which scored best on arm C (+2 lift) but still only reached 4/5 at Qwen. The gold answer includes "16 unprocessed voice call transcripts" and a "booking with Carrie at 2pm" — neither of which was in any arm's context. The friend walked episodes and activity rates, which helped, but the underlying data (transcripts with no summaries, a phone-call booking with no follow-up) wasn't reachable via edge walking because the edges weren't there.

Fix: a dedicated temporal-aggregation path — "what changed since date X" — that scans `created_at` / `updated_at` / `occurred_at` across all entity tables, groups by recency, and surfaces the most recent items regardless of keyword or edge relevance. This is a third retrieval mode, not a variant of the first two.

**Blind spot 2: Entity resolution by indirect reference.** A2 ("the client whose mom is going through chemo") requires resolving a *description* to an *entity*. The librarian can't do it because "chemo" matches the email summary but there's no join to the contact. The friend can't do it because the walk starts from keyword seeds and "chemo" seeds the wrong neighborhood (if it seeds at all).

This is a natural-language understanding task, not a retrieval task. The fix isn't better retrieval; it's a pre-retrieval resolution step: "before you search, identify which entity the user is referring to." That's an LLM inference call, not a DB query. The cost is one cheap LLM call (~100 tokens) to resolve "the client whose mom is going through chemo" → "Client A" before the retrieval layer even activates.

**Blind spot 3: Absence detection.** Both personas are biased toward surfacing what EXISTS. Neither is good at surfacing what DOESN'T exist — missing follow-ups, unlogged interactions, stalled projects with no recent activity. The friend's episode walk can surface "this episode has no recent activity" if the episode exists, but it can't surface "there's no episode for this cluster of related work that probably should have one."

The benchmark shows this on P3 (agency follow-ups): all arms correctly say "no follow-ups logged" — but none says "here are the 7 specific contacts at these agencies you could follow up with." The gold answer includes the named contacts; the actual answers just report emptiness.

Fix: a "gap detector" mode that explicitly scans for expected-but-missing patterns. This is closest to Sophie's `routing_health` module — a self-health-check that finds structural absences and surfaces them as "things you probably should know are missing." It's neither lookup nor exploration; it's auditing.

**Devon:** I'll add a fourth:

**Blind spot 4: The loci layer is only as good as its edges.** This whole experiment validated the *architecture* (entity_edges + episodes + two-expander walk). But the actual edges in next_soy.db are hand-authored from a contact audit. In production, edges need to be maintained — new contacts arrive, relationships change, episodes end. If the edge table goes stale, the friend's walks degrade silently. There's no "edge freshness" signal; the walker treats a 6-month-old edge and a 6-minute-old edge the same way (both weight 1.0).

The cost here isn't compute or tokens — it's **maintenance burden**. Every new email thread that establishes a relationship ("Alex, meet Sarah from the marketing team") should create an edge. Every stalled project should end its episode. If that maintenance isn't automated, the friend slowly becomes a friend who remembers last year but doesn't know what happened this week.

This circles back to the LLM-extraction warning from the schema panel: don't let an LLM auto-write edges. But the alternative — manual edge maintenance — doesn't scale. The resolution is probably a semi-automated path: deterministic extraction from structured events (new email → email_with edge, new calendar invite → event_with edge) combined with user-confirmed episode management (the system suggests "this looks like a new episode" and the user confirms or dismisses).

**Marcus:** Devon's point lands for me as the single biggest risk to the two-persona architecture. **The friend is high-maintenance.** The librarian (flat search) works on raw rows — it doesn't need curated edges or episodes. It degrades gracefully as data gets stale because it just searches what's there. The friend requires a curated graph, and curated graphs require ongoing investment.

The question for Alex is: are you willing to spend 5 minutes a week maintaining edges and episodes? If yes, the friend stays sharp. If no, the friend slowly becomes a hallucinating acquaintance who remembers some things from a while ago but can't keep up.

---

## Round 6 — What do we actually ship?

**Facilitator:** Given all of the above, what's the concrete next step?

**Nadia:** Ship the two-tool MCP pattern (Marcus's Option 2) with Nadia's fallback layer. Concretely:

1. **`soy_search` tool** — flat LIKE search + optional get_profile expansion. The librarian. Used for direct lookups.
2. **`soy_explore` tool** — loci_v2 walk + narrative render. The friend. Used for connections, prep, patterns.
3. **Fallback rule**: if the model's answer after using one tool contains an explicit uncertainty marker ("I don't have enough context for," "No data found for the connection between"), automatically invoke the other tool and append its context. The model gets a second pass with both.

Implementation cost: modify the SoY MCP server to expose two tools instead of one. Reuse the existing `search` and `get_profile` code for tool 1; wrap `loci_v2.assemble_context` + `render_narrative` for tool 2. The fallback is a hook in the MCP response handler.

**Marcus:** Agreed. Ship the two-tool pattern. DON'T ship:
- Temporal aggregation (Priya's blind spot 1) — that's a separate tool, `soy_recent`, for a future iteration
- Entity pre-resolution (blind spot 2) — that's a system-prompt instruction, not a retrieval feature
- Gap detection (blind spot 3) — that's `soy_health`, another future tool
- Auto-edge-maintenance (Devon's blind spot 4) — that's the Gmail/calendar sync pipeline, not the retrieval layer

The two-tool pattern is the 80% solution. The blind spots are real but they're each separate workstreams with separate validation criteria. Don't block the 80% on the 20%.

**Priya:** I want to register one dissent. The benchmark data that supports the two-persona framing comes from 17 prompts, 2 model tiers, and a single judge pass per tier. The per-prompt sample size is 1 (or 2 for the V1 Opus tier which had cross-family judging). We're drawing architectural conclusions from N=1 per-prompt observations. The aggregate patterns are credible; the per-prompt classifications ("this is a librarian question, that's a friend question") are not statistically robust.

My recommendation: before shipping the two-tool architecture, **run the 17 prompts through the routing classifier (give the model both tool descriptions, ask which it would choose) and compare its classification to the "which arm wins" ground truth from the benchmark.** If the model's classification matches at ≥80% accuracy, ship. If <60%, the two-tool pattern will misroute too often and you should just always run both.

This is a 30-minute experiment with zero implementation cost. Run it before building anything.

**Devon:** Priya's experiment is the right gate. I'd add one measurement to it: for the prompts where the model chooses wrong, measure the *harm* of the misclassification. If choosing "search" when "explore" was optimal produces a 3→2 on relevance, that's a 1-point miss. If choosing "explore" when "search" was optimal produces a 5→3 with 2 hallucinations, that's a 2-point miss plus hallucination damage. **Asymmetric misclassification costs should inform the default**: if explore-when-search-was-better is worse than search-when-explore-was-better, the default should be search with explore as escalation, not the other way around.

The V2 data suggests this asymmetry exists. Arm C at Qwen had 9 hallucinations vs arm A's 6. The friend is riskier than the librarian. Default to the librarian; escalate to the friend.

**Nadia:** That's exactly the fallback direction I described: search first, explore if insufficient. Devon's asymmetry argument gives us the default ordering. Priya's classification experiment gives us the gate criteria. Marcus's two-tool MCP pattern gives us the implementation shape. I think we have a plan.

---

## Synthesis

### What the panel agrees on

1. **The two-persona framing is operationally useful** even though the underlying reality is a spectrum. The metaphor gives the router a fast heuristic.
2. **Two MCP tools (`search` and `explore`) with model-driven routing** is the right production shape. The model reads tool descriptions and picks; the user can see which was chosen; a fallback retries the other tool if the first answer is thin.
3. **Default to search, escalate to explore.** The librarian is lower-risk (fewer hallucinations, faster, cheaper). The friend is higher-value on the prompts where it wins but higher-risk on the prompts where it doesn't. Asymmetric misclassification costs → conservative default.
4. **Run Priya's routing-classifier experiment** (30 minutes, zero implementation cost) before building anything. If the model can't classify at ≥80% accuracy, always run both and merge.

### What the panel disagrees on

1. **Priya** thinks the per-prompt evidence is too thin to draw per-question-type conclusions. The aggregate pattern is real; the individual classifications are noise. She wants more data before committing to a routing architecture.
2. **Devon** thinks the attention-cost problem (model drowning in loci's broad context) is worse than the report acknowledges, especially at the Qwen tier where the gains are largest but the hallucinations are highest.
3. **Marcus** thinks the blind spots (temporal aggregation, entity resolution, gap detection) should be tools in the same MCP server, not separate workstreams — shipping one tool at a time means the user sees an inconsistent experience where some questions work and others don't.

### What to build

| Priority | What | Why | Gate |
|---|---|---|---|
| **P0** | Priya's routing-classifier experiment | 30 min, zero cost, tells us if the two-tool pattern works before we build it | Run before anything else |
| **P1** | `soy_search` + `soy_explore` MCP tools | The 80% solution. Two tools, model-driven routing, fallback retry | Routing classifier ≥80% accuracy |
| **P2** | Edge freshness signal | Without it, the friend degrades silently as edges go stale | After the two-tool pattern ships |
| **P3** | Session context persistence (Priya's ambient priming) | The friend's context should benefit subsequent librarian calls | After P1 is validated in production |
| **P4** | `soy_recent` tool (temporal aggregation) | Third retrieval mode for "what happened recently" | After P1-P3 |
| **P5** | Semi-automated edge maintenance | Deterministic extraction from emails/calendar + user-confirmed episodes | Parallel workstream, not gated on P1 |

### The one question the panel couldn't answer

**What percentage of a real user's questions are "explore" questions vs "search" questions?**

The benchmark has 17 designed prompts with intentional bucket distribution (4 recall, 4 prep, 4 connection, 3 synthesis, 2 adversarial). That's not representative of organic usage. If Alex's real daily questions are 90% lookups and 10% exploration, the friend is a luxury. If they're 50/50, the friend is essential. If they're 30% lookups and 70% "help me think about this," the friend should be the DEFAULT and the librarian should be the fallback.

We don't know. The panel recommends **logging the routing decisions for the first 2 weeks after the two-tool pattern ships**, then reviewing the distribution. The architecture should support flipping the default (search-first vs explore-first) without a code change — it's a configuration, not an implementation.

---

## Devon's parting note

> The marginal gains on the aggregate are real but small (+0.71 at Qwen, +0.06 at Opus). If someone asked me "is loci worth the engineering investment based on the aggregate numbers alone," I'd say no — the flat search baseline is good enough for most questions, and the complexity cost of maintaining a curated graph is ongoing.
>
> But the aggregate isn't the right frame. The VALUE of loci isn't in the average — it's in the **specific prompts where flat search structurally cannot answer.** C1 went from 2→5. P2 went from 2→5. P4 went from 2→4. These aren't marginal improvements on questions flat search was already handling; they're entire capability classes that didn't exist before.
>
> The friend doesn't make the librarian 10% better. The friend answers questions the librarian can't hear. That's the case for building it, and it doesn't show up in the average.
