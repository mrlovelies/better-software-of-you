# next_soy Implementation Plan — What to Build After the DDL is Approved

**Status:** Draft companion to `next_soy_schema_v1.md`. Review artifact, not executable.
**Prerequisites:** Schema DDL reviewed and approved.
**Scope:** The specific files to write, in what order, with what dependencies. No code in this doc — just the plan.

---

## Overview

Three deliverables after schema approval, in dependency order:

1. **`seed_next_soy.py`** — builds `data/next_soy/next_soy.db` from real SoY + Gmail ingest, produces a validation report.
2. **`loci_v2.py`** — new traversal layer that queries `entity_edges` via two generic expanders instead of 10+ per-type functions. Also replaces the tree renderer with Aisha's per-entity narrative format.
3. **Benchmark re-run** — same 17 prompts, same judge setup, against `next_soy.db` via `loci_v2.py`. Compare side-by-side with the existing 204-data-point benchmark.

Each deliverable has its own gate. Build → review → next.

---

## Deliverable 1 — `seed_next_soy.py`

**Location:** `benchmarks/loci/seed_next_soy.py`

**Dependencies:** DDL approved, Gmail MCP still reachable, real SoY DB accessible at `~/.local/share/software-of-you/soy.db`.

**Structure:**

```
seed_next_soy.py
├── Config & paths
├── init_schema()                      — applies the DDL to a fresh file
├── backfill_carry_over_tables()       — step 1 from the DDL doc
├── translate_standalone_notes()        — step 2
├── populate_structural_edges()         — step 3 (one function per edge_type)
├── parse_legacy_linked_columns()       — step 4 (the hard one)
├── populate_contact_identities()       — step 5
├── resolve_james_andrews_duplicate()   — step 6
├── ingest_gmail_gaps()                 — step 7
├── seed_memory_episodes()              — step 8 (hand-authored data)
├── populate_wikilinks()                — step 9
├── validate()                          — migration safety checks
└── write_validation_report()           — final markdown summary
```

**Design notes:**

- **Read-only against real SoY.** The script opens `soy.db` with `mode=ro` so there's zero risk of corrupting live data. Every read is an attach + SELECT.
- **Idempotent.** Running the script twice produces the same `next_soy.db`. If `next_soy.db` already exists, the script refuses and asks for `--force` to overwrite. No silent state accumulation.
- **Pure Python stdlib.** Same constraint as the rest of `benchmarks/loci/`. No pandas, no ORM. `sqlite3` + `json` + `urllib` (for Gmail MCP calls if we bypass the MCP and go direct).
- **Gmail ingest uses the deterministic path.** No LLM summarization. Interaction summaries are first-500-chars with ellipsis. Rachel's warning: never let auto-extraction write edges without user review. The only edge types generated during Gmail ingest are the structural ones (`email_with`, `interaction_with`) — no content `mentions` from email bodies in V1.
- **Unresolved-link strategy** (open question #4 in the DDL doc): **default to log-and-skip.** The seed script produces `seed_unresolved.log` listing every `linked_projects = "Something Weird"` case, and the user reviews the log before running `loci_v2` against the DB. No stub project creation, no interactive prompting.
- **Validation report format:** markdown, saved to `benchmarks/loci/seed_reports/seed_<timestamp>.md`. Includes row counts per table, edge counts per type, unresolved items, wikilink ambiguities, duration breakdown.

**How long:** ~1-2 days of focused work once DDL is approved. The hard parts are (a) the `linked_projects` parsing (already largely solved in `shared/loci.py`, just needs porting), (b) Gmail pagination for the ingest path, and (c) the hand-authored episode seed data (blocked on user review of which episodes to include).

**Exit criterion:** `next_soy.db` file exists, validation report has no errors, user has reviewed `seed_unresolved.log` and `wikilinks_ambiguous.log`.

---

## Deliverable 2 — `loci_v2.py`

**Location:** `shared/loci_v2.py` (new file alongside the current `shared/loci.py`)

**Dependencies:** `next_soy.db` exists and passes validation.

**What it replaces:**

The current `shared/loci.py` has ~800 lines structured around 10+ per-entity-type expander functions (`_expand_contact`, `_expand_project`, `_expand_decision`, etc.) plus a tree renderer with `└── via X.Y` edge labels. `loci_v2.py` collapses this dramatically:

```
loci_v2.py
├── ALLOWED_TABLES (updated — now includes entity_edges, notes_v2, etc.)
├── find_seeds()                       — same keyword-match-count approach, updated for notes_v2
├── expand_outbound(src_type, src_id)  — generic outward walk via entity_edges
├── expand_inbound(dst_type, dst_id)   — generic inward walk via entity_edges
├── assemble_context()                 — BFS using the two generic expanders
├── render_narrative()                 — Aisha's per-entity narrative format
└── __main__ block                     — CLI for ad-hoc testing
```

**Key architectural changes from v1:**

1. **Two generic expanders instead of 10+ per-type functions.** The loci walk becomes: "from this seed, query `entity_edges` where src=(my_type, my_id) ORDER BY weight DESC LIMIT breadth" and resolve each destination to the full row. Same query shape works for every entity type.

2. **Per-entity narrative render instead of the tree format.** Aisha's constructive panel proposal, now with `entity_edges` as the substrate. Each top-level seed gets a prose paragraph; related items in a bulleted list below; no `└──` characters, no `[via X.Y]` edge labels.

3. **Episode-aware walk.** When the BFS reaches an entity that's a member of an active `memory_episode`, the walker also surfaces the episode (as a "context card") and the other members of that episode (if within the budget). This is the query-time payoff of Priya's schema contribution — C1 works because the BFS from Reprise walks to the "Operator intelligence layer" episode, and from there to BATL Lane Command.

4. **Daily logs integrated as walkable entities.** When the prompt is temporal ("what's fallen off my radar"), the seeds include recent `daily_logs` rows, and the walk follows `log_mentions` to find what the user has been writing about.

5. **No more `linked_projects` parsing.** The defensive parse code (`_parse_id_list`, `_name_in_linked_field`) is deleted. Those formats don't exist in `next_soy`.

**What stays the same:**

- Python stdlib only.
- `find_seeds()` still uses keyword match-count ranking (the v1 fix).
- BFS depth/breadth/total caps same as v1 defaults (depth 2, breadth 5 per node, total 60 nodes).
- Defensive error handling (log-to-stderr on `OperationalError`, don't swallow).

**How long:** ~1 day once `next_soy.db` exists. The generic expanders are much simpler than the per-type ones. The narrative renderer is the main new code.

**Exit criterion:** `python3 shared/loci_v2.py "prep me for Jessica"` produces a sensible narrative brief. Smoke-tested on 3-5 prompts locally before the full benchmark run.

---

## Deliverable 3 — Benchmark re-run

**Dependencies:** `next_soy.db` and `loci_v2.py` both exist.

**What runs:**

Same 17 prompts from `prompts.json`, same three arms (flat, SoY-as-is, loci), two test models (Claude Opus + Qwen 14B — the tiers where the loci story actually lives), same judge configuration (Opus subagent, with Qwen 14B as second-family judge for the Claude results to triangulate).

**Changes from the original benchmark harness:**

The runner needs a small adjustment to target `next_soy.db` instead of the real `soy.db`:

- New `--soy-db` flag (already exists on `runner.py run`) points at `data/next_soy/next_soy.db`.
- The arm implementations in `arms.py` need to import from `shared.loci_v2` instead of `shared.loci`. Either a parallel `arms_v2.py` or a flag on the existing module.
- Judge package dump, subagent judging, score import, report generation all work unchanged — they don't care which loci variant produced the contexts.

**Scope of the re-run** (open question #5 in the DDL doc): **my recommendation is 2 tiers, not 4.** Rerunning Mistral 7B and Qwen3 30B would add ~3 hours of wallclock and give us data on model tiers where the original benchmark already told a clear story (Mistral catastrophically fails loci; Qwen3 30B is an anomaly that's probably model-specific). The two tiers that matter for the loci hypothesis are Claude Opus and Qwen 14B. Re-running just those two gives us the apples-to-apples comparison Priya's replayability argument required.

That said, if the 2-tier re-run shows dramatic improvement on next_soy, Mistral 7B becomes interesting *again* (does the schema fix unlock the cheap tier, which the original benchmark said wasn't possible?). Hold that as a follow-up.

**Comparison protocol:**

The re-run produces its own `results.db` entries under a new `run_id`. The comparison against the original 204-data-point benchmark is a SQL join on `(prompt_id, arm_id)`:

```sql
SELECT
    p.prompt_id,
    p.arm_id,
    orig_score.relevance AS orig_rel,
    new_score.relevance AS next_soy_rel,
    new_score.relevance - orig_score.relevance AS delta
FROM ... prompts p
JOIN judge_scores orig_score ON orig_score.run_id = '<claude_original>'
JOIN judge_scores new_score  ON new_score.run_id  = '<claude_next_soy>'
ORDER BY delta DESC;
```

The report surfaces:

- **Per-prompt delta table** — which prompts improved, regressed, or stayed the same
- **Per-arm aggregate** — does loci (arm C) gain more than flat (arm A) or SoY-as-is (arm B)? If loci gains 0.5 and flat gains 0.5, that's "better data, same retrieval." If loci gains 0.8 and flat gains 0.2, that's "loci specifically benefited from the schema work."
- **Per-bucket delta** — does the Prep bucket still win for arm C? Does Connection finally move? Does Adversarial hold?
- **The C1 verdict** — the single prompt that failed at every model tier in the original. Did episode-aware walking + narrative render finally unlock it?
- **Hallucination comparison** — do the numbers stay close to the Claude/Opus-judge zero, or does the new schema reduce hallucinations further?

**Exit criterion:** A new markdown report file (`benchmarks/loci/next_soy_rerun_report.md`) with the full comparison. This becomes the input to a "V2 synthesis" doc that either validates the loci hypothesis decisively or points at the next iteration (Takeshi's community walk, Lena's briefing cache, or something else entirely).

---

## Timeline estimate

Assuming DDL is approved without major changes:

| Step | Duration | Blocked on |
|---|---|---|
| Write `seed_next_soy.py` | 1-2 days | DDL approval |
| Run seed script, review validation report | ~0 (same day) | Script completion |
| Write `loci_v2.py` | 1 day | next_soy.db exists |
| Smoke-test loci_v2 on 3-5 prompts | ~2 hours | loci_v2.py written |
| Benchmark re-run (2 tiers × 17 prompts × 3 arms) | ~1 hour wallclock | loci_v2 smoke-tested |
| Judge re-run (Opus subagent + Qwen second-family) | ~30 min | Answers exist |
| Write V2 synthesis | ~half day | All above complete |

**Total:** roughly 4-5 working days from DDL approval to V2 synthesis, assuming no surprises in seed data or loci_v2 implementation. If the seed script finds unexpected data shapes or the narrative renderer doesn't produce sensible output on first try, add another day.

---

## Risks and mitigations

### Risk 1 — Seed ingest surfaces data shapes the DDL doesn't handle

**Example:** `linked_projects = "Braska's Pilgrimage"` is the project name case the DDL anticipated. But what if there's also `linked_projects = "2026 Q1 VO"` which doesn't match any project AND doesn't look like a valid project name either? Or worse: what if `linked_contacts` has some rows containing an email address instead of an ID or name?

**Mitigation:** The unresolved log catches these. Before running `loci_v2` against the seeded DB, we review the log and either (a) accept the data loss for a small number of genuinely garbage rows, (b) hand-fix them in real SoY first and re-seed, or (c) add a new parse path to the seed script. **Option (a) is the default for V1.** V2 schemas get better at migration when we've seen what real data looks like.

### Risk 2 — Gmail ingest changes row counts in a way that breaks benchmark comparisons

**Example:** The original Claude benchmark ran against a soy.db with 162 emails. The next_soy.db after Gmail ingest might have 250. Loci's seed selection picks different seeds now because there's more data to match. Is that "the schema improved loci" or "the data improved loci"?

**Mitigation:** **Run two next_soy benchmarks**, not one.
- **Run A:** `next_soy.db` with ONLY the carry-over + schema changes, no Gmail ingest. This measures the schema effect in isolation.
- **Run B:** `next_soy.db` with the Gmail ingest applied. This measures schema + data combined.

The delta between A-baseline and A-next_soy is schema effect. The delta between A-next_soy and B-next_soy is data effect. They separate cleanly.

This adds ~1 hour of additional benchmark runtime but makes the attribution unambiguous. Priya's replayability argument in the schema panel was specifically about separating these — this is how we honor it.

### Risk 3 — `loci_v2.py` tree render is worse than the old one for some prompts

**Example:** Per-entity narrative blocks are great for "brief me on Jessica" but might be worse for "what threads connect Reprise and BATL" because the two entity narratives don't naturally share context.

**Mitigation:** The renderer uses a different format when the walk touches a `memory_episode`. Episode-aware rendering looks like:

```
## Episode: Operator intelligence layer (since 2026-03-01, ongoing)

A period of thinking about private, owner-facing intelligence layers
distinct from the public product. Currently active.

Inside this episode:
  - Reprise (project) — competitive analysis note, music-as-signal
    note, API budget note
  - BATL Lane Command (project) — private ops intelligence note,
    daily metrics note

Connection: both projects treat the user as the privileged consumer
of cross-source aggregated signal. This framing is explicit in the
BATL ops intelligence note and implicit in Reprise's product vision.
```

That format is specifically designed for cross-entity synthesis prompts. Per-entity narrative for single-entity prompts, episode card for cross-entity prompts. The renderer switches based on which kind of seed drove the walk.

### Risk 4 — We commit to next_soy and real SoY drifts away

**The danger:** The user keeps using real SoY (adding contacts, journal entries, decisions) while we're building next_soy. By the time the benchmark re-runs, real SoY has different data than the next_soy seed captured. Comparing the two becomes less clean.

**Mitigation:** **Snapshot discipline.** The seed script records the real SoY DB's modification time and a row-count manifest at seed time. Any subsequent benchmark comparison notes explicitly whether real SoY has drifted since the seed. If drift is significant (more than 5% row change in core tables), re-seed before comparing.

---

## The "if this works, then what" question

Assuming the re-run shows meaningful improvement (say, Claude arm C relevance goes from 4.35 to 4.6+ on next_soy, Prep bucket jumps to 4.5+, C1 finally hits 4+), here's the sequence after:

1. **Accept next_soy as the new production target for SoY's schema.** The next_soy schema becomes the basis for a real migration script that evolves live SoY into the same shape. The migration is a separate, careful workstream.

2. **Deprecate `shared/loci.py` in favor of `shared/loci_v2.py`.** The v1 becomes dead code and gets removed in a later commit.

3. **Update the MCP server's `get_profile`, `search`, and related tools to use `loci_v2`.** This is where Aisha's render format change finally affects the production system Claude Code talks to.

4. **Open a PR to Kerry's upstream (`kmorebetter/better-software-of-you`).** The PR body cites `synthesis-four-tier.md`, the schema panel, the next_soy DDL, and the re-run report. Kerry gets the full provenance, not just a patch.

5. **Plan Phase 3** — the deferred items from the schema panel. Either `edge_salience` (Priya's bet on associative recency weighting) or Takeshi's community detection walk become the next candidate optimizations. We'll know more after the re-run tells us where the ceiling is now.

If the re-run DOESN'T show improvement (or shows regression), the next iteration is more diagnostic than architectural. Look at per-prompt deltas, figure out which proposal didn't pay off, and revise. The worst-case outcome of this whole path is "we learn that the schema panel was wrong about something specific," which is still valuable.

---

## What you're being asked to review in the next session

1. **The DDL doc** (`next_soy_schema_v1.md`) — does the schema look right? Any tables missing, any columns you'd add or drop?
2. **The six open questions in the DDL doc** — my recommendations are documented as defaults, but you know your data better than I do.
3. **This plan** — does the build order and scope make sense? Anything you'd cut or add?
4. **The two open risks** — specifically risk 2 (data-effect vs schema-effect attribution, which requires running two variants) and risk 3 (episode-aware rendering) are the ones where your call changes what gets built.

Once those are reviewed, the seed script is a focused 1-2 day push.
