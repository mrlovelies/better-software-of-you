# Wiring Review Panel — The Explore Tool and Everything Leading to It

**Date:** 2026-04-12
**Scope:** Review the full arc: V2 schema → seed script → loci_v2 walker → V2 benchmark → routing panel → explore MCP tool wiring. One session's worth of decisions, examined for soundness.

**Panelists:**
- **Nadia Okonjo** (systems architect) — returning. Will challenge the wiring and the production path.
- **Devon Sato** (ops/cost) — returning. Will challenge what this costs to maintain.
- **Priya Nair** (cognitive scientist) — returning. Will challenge whether the benchmark data supports the claims.
- **Sara Okonkwo** (benchmark design) — new. Specifically reviewing the V2 benchmark methodology for confounds.
- **Facilitator** — presents, asks, doesn't advocate.

---

## Part 1 — The wiring

**Facilitator:** The explore tool is now registered in the MCP server. It reads `next_soy.db` (the V2 parallel schema), calls `loci_v2.assemble_context`, and returns a narrative render. The existing `search` tool and `get_profile` tool continue to read `soy.db` (production). Two databases, two retrieval modes, one server. Nadia, what do you see?

**Nadia:** Three concerns, one of which is urgent.

**Concern 1 (urgent): Two databases means data drift.** The `search` tool sees real soy.db — contacts added yesterday, emails synced this morning, decisions logged an hour ago. The `explore` tool sees next_soy.db — a snapshot from April 11 that will NEVER update unless someone re-runs the seed script.

When the model invokes `search`, it gets current data. When the model invokes `explore`, it gets stale data. When the model uses both (the fallback pattern), it gets inconsistent data. A contact added after the seed was taken will appear in `search` results but not in `explore` walks. An episode about yesterday's work doesn't exist in next_soy.db.

This is the single biggest risk in the current wiring. The fix isn't hard — add the V2 tables (entity_edges, memory_episodes, notes_v2, wikilinks, etc.) to real soy.db as a migration, then point both tools at the same DB. But until that migration lands, the two-database split is a ticking clock. The longer it runs, the more the data drifts, and the more the explore tool's answers diverge from reality.

**Recommendation:** Treat the production migration as P0. The explore tool in its current form is a demo, not a production tool. Ship it for Alex to play with and validate the UX, but don't trust its answers for anything time-sensitive until both tools point at the same DB.

**Concern 2 (medium): Import path fragility.** The explore tool injects `shared/` into `sys.path` at import time using a relative path (`Path(__file__).resolve().parents[4]`). This works when the MCP server is installed in its development location. It breaks if the package is installed via pip into a virtualenv where the repo root isn't 4 levels up. Not a problem today (single-user dev setup), but a maintenance trap if the server ever gets deployed differently.

**Recommendation:** Move `loci_v2.py` into the MCP package itself (as `software_of_you/loci.py`) or add it as a package dependency. The `sys.path` hack is fine for now but has a shelf life.

**Concern 3 (minor): The tool description doesn't mention the data-freshness limitation.** The model reads the tool description to decide when to invoke `explore`. The description says "walk the relationship graph" — it doesn't say "this graph is a snapshot from April 11 and may be stale." If the model invokes `explore` for a question about something that happened after the seed, it'll get a plausible-looking answer that's missing recent data, and neither the model nor the user will know.

**Recommendation:** Add a freshness signal to the tool's return value: `"data_as_of": "2026-04-11T21:22:01"` (from the seed report's snapshot manifest). The `_context` field should include this. The model can then hedge: "Based on data through April 11..."

---

## Part 2 — The benchmark methodology

**Facilitator:** Sara, you haven't seen the V2 benchmark until now. The setup: 17 prompts × 3 arms × 2 model tiers, judged by Claude Opus subagent. V1 ran against real soy.db; V2 ran against next_soy.db with loci_v2. What's your read?

**Sara:** Four methodological notes. Two are legitimate confounds, two are not.

**Legitimate confound 1: Different data substrates.** V1 ran against soy.db (33 contacts, no entity_edges). V2 ran against next_soy.db (48 contacts, 379 edges, 4 episodes, 122 wikilinks). The databases don't have the same rows — V2 has 15 new contacts, status flips on 25 existing contacts, and entirely new tables.

This means the V1→V2 delta conflates THREE changes: (1) the schema rearchitecture, (2) the walker rewrite, (3) the data additions from the contact audit. The implementation plan anticipated this and designed a Run A (schema only) vs Run B (schema + Gmail ingest) ablation — but it didn't design a Run 0 (same data, new schema only, no new contacts). Without Run 0, you can't separate "the 15 new contacts helped" from "entity_edges helped."

**How bad is this?** Look at where arm A improved from V1→V2. If arm A improved, the data change helped everyone (not just loci). At Opus, arm A went from 4.18→4.41 (+0.24). At Qwen, arm A stayed at 3.00. So the data change helped Opus (modestly) but didn't help Qwen. The Qwen loci-specific lift of +0.71 is almost entirely attributable to the schema+walker change, not the data additions. The Opus lift of +0.06 is harder to attribute. For C1 specifically at Opus, arm A also went to 5.0, suggesting the data change (more notes, richer contact records) was sufficient for Opus to answer C1 even without loci.

**Bottom line:** The Qwen results are credible as a schema+walker effect. The Opus results are confounded by data quality improvements that help all arms.

**Legitimate confound 2: Judge inconsistency across V1 and V2.** V1's Opus tier was judged by Qwen 14B (cross-family). V2's Opus tier was judged by Opus (subagent). Different judge models can have different scoring tendencies. The V1 benchmark previously validated that the cross-family judge didn't deflate Opus scores significantly — but "didn't deflate significantly" isn't the same as "produced identical distributions." Any comparison of Opus V1 vs Opus V2 carries a judge-model confound.

The Qwen tier was judged by Opus in both V1 and V2, so there's no judge confound for the Qwen comparison. This makes the Qwen results the cleaner comparison and further strengthens the "Qwen is the headline tier" framing.

**Not a confound: single-pass judging.** Each V2 entry was judged once (the V1 Opus tier had two judge passes; V2 has one). Single-pass judging has higher variance but isn't systematically biased. The per-prompt conclusions might be noisy, but the aggregate trends are fine.

**Not a confound: subagent-as-test-model for the Opus tier.** The V2 Opus tier used Claude Code subagents to generate answers (split into 3 batches of 17). These are Claude Opus instances reading context + question and answering — exactly what the benchmark is designed to test. The batching introduces no bias because entries are independent. The answers might differ from a single-session run (different random seeds, different context management), but not in a systematic direction.

**Priya:** I want to amplify Sara's confound 1. The V2 benchmark report's "two-persona" analysis — classifying prompts as "librarian wins" vs "friend wins" — is based on the V2 data only. But the V2 data includes data additions that specifically target certain prompts. The contact audit added contacts that C1, C3, and the episode prompts depend on. Of course those prompts improved on arm C — the data was designed to make them work.

This doesn't invalidate the finding. The episode layer IS the new capability, and it DOES require the new data to function. But the claim should be "entity_edges + episodes + new contacts unlock C1" — not "loci unlocks C1." Loci without the data additions wouldn't have unlocked C1 either, because the episode rows wouldn't exist.

**Sara:** Agreed. The correct attribution is: **the schema rearchitecture (entity_edges + episodes) is a necessary condition for C1. The contact audit (new contacts, new edges) is also a necessary condition. The loci_v2 walker is the mechanism that translates both into an answerable prompt.** All three are required; none is sufficient alone.

---

## Part 3 — Whether the gains justify the complexity

**Devon:** Let me be direct. Here's the ledger:

### What was built in this session

| Artifact | Lines | Purpose |
|---|---|---|
| `001_core.sql` | 496 | V2 DDL |
| `seed_next_soy.py` | ~900 | Seed script |
| `loci_v2.py` | ~660 | Walker rewrite |
| `arms.py` changes | ~50 | Benchmark wiring |
| `runner.py` changes | ~20 | --loci-version flag |
| `test_subagent.py` changes | ~5 | loci_version threading |
| `explore.py` | ~110 | MCP tool |
| `server.py` change | ~3 | Registration |
| Panel docs + reports | ~2,500 | Analysis, sharing |
| Sophie share repo | ~4,200 | Cross-pollination |

**Total new code: ~2,250 lines.** Total documentation/analysis: ~6,700 lines. The documentation-to-code ratio is 3:1.

### What the code produces

On the Qwen 14B tier (the headline):
- Mean relevance improved from 3.18 → 3.88 on arm C (+0.71)
- C1 went from 2→5 (the poster-child result)
- 9 of 17 prompts improved or maintained on arm C
- 2 prompts regressed (A2 −2, R4 −1)

### Whether it's worth it

The honest answer is: **it depends on what you're building toward.**

If the goal is "make the existing SoY search tool slightly better," the answer is no. The existing search tool already scores 3.0 at Qwen and 4.18 at Opus. Spending 2,250 lines of code and a full session of work to add +0.71 on one tier is not an efficient use of engineering time for marginal quality improvement.

If the goal is "unlock an entirely new class of questions that the system couldn't answer before," the answer is yes. C1, P2, P4, C3, and C4 are questions that flat search STRUCTURALLY cannot answer — there's no amount of tuning flat search that would make it walk cross-entity edges or surface episodes. The loci layer is a capability addition, not a quality improvement. The +0.71 aggregate understates this because it averages the capability prompts with the ceiling prompts.

If the goal is "build a platform for the friend-model vision described in the original loci conversation," the answer is strongly yes. The two-persona routing architecture, the episode layer, the narrative render, the entity_edges substrate — these are the infrastructure for a product direction where the AI assistant has two modes of operation (lookup and exploration) that map to different cognitive needs. The benchmark validates that the infrastructure works; the product question is whether users want both modes, and that's not answerable from benchmark data.

**My recommendation:** The work justified itself if and only if you commit to the production migration (add V2 tables to real soy.db) within the next 2-3 weeks. Without the migration, the explore tool is a demo that will go stale. With the migration, it's the foundation for the friend-model product direction.

---

## Part 4 — What would make this stronger

**Facilitator:** If you could ask for one more thing from this work, what would it be?

**Nadia:** The production migration. Until both tools read the same DB, the explore tool is a liability.

**Devon:** A measurement of the organic search/explore distribution. Log routing decisions for 2 weeks and see what percentage of Alex's real questions are "explore" questions. If <10%, the engineering was premature. If >30%, it was justified.

**Sara:** A Run 0 ablation. Same next_soy.db schema and data, but with loci_v1 (the old walker) instead of loci_v2. That separates "the walker rewrite helped" from "the schema + data helped." The walker rewrite is the smallest piece of the three changes; it'd be nice to know if it's pulling its weight independently.

**Priya:** Honestly? A second user. Everything in this system — the prompts, the episodes, the edges, the benchmark — is designed around one person's data. The two-persona hypothesis might be specific to Alex's relationship with his data, not a general pattern. Sophie's independent convergence on similar architecture is the strongest external signal, but she's testing against a different substrate (Claude Code sessions, not personal CRM). One more user with a personal data store trying the explore tool would tell you more than another benchmark run.

---

## Synthesis

### The panel endorses

1. **The explore tool wiring** as a preview/demo. Clean code, correct import path for the dev setup, appropriate tool description for model-driven routing.
2. **The V2 benchmark results** at the Qwen tier as credible evidence of a schema+walker effect. C1 going from 2→5 is real and the loci-specific lift of +2.0 on that prompt is clean.
3. **The two-persona routing architecture** as the right production shape. The routing-classifier experiment showed 70.6% raw accuracy / 94.1% with fallback, which clears the bar.
4. **Sharing with Sophie** via the loci-journey repo as a high-value investment. The cross-pollination is already producing technical insights (tiered delivery, archetype clustering, checkpoint patterns) that wouldn't exist from internal work alone.

### The panel flags

1. **Data drift is urgent.** The two-database split has a shelf life of ~2 weeks before the explore tool's answers become materially stale.
2. **The Opus V1→V2 comparison is confounded** by both data additions and judge-model differences. Draw Opus conclusions cautiously.
3. **The per-prompt "librarian vs friend" classification is noise-level** at N=1 per prompt. The aggregate pattern is credible; the individual prompt labels are not.
4. **The A2 regression (3→1 on arm C)** is an unresolved negative result. The explore tool can make entity resolution WORSE when the walk is broader than the question requires.

### Recommended next steps (in priority order)

| Priority | Action | Gate | Why |
|---|---|---|---|
| **P0** | Production migration — add V2 tables to real soy.db | None | Explore tool is a demo until this ships |
| **P1** | Add data_as_of freshness signal to explore responses | None | Prevents silent stale-data answers |
| **P2** | Log routing decisions for 2 weeks | After P0 | Tells you the organic search/explore distribution |
| **P3** | Run B — Gmail ingest into next_soy.db | After P0 | Separates schema effect from data effect |
| **P4** | Run 0 ablation — V1 walker against V2 schema | After P2 | Separates walker effect from schema+data effect |
| **P5** | Sophie writeup — three-column comparison | After P3 numbers | C1 delta is the centerpiece; needs final attribution clarity |
