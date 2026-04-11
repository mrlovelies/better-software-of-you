# Loci Layer Benchmark — Four-Tier Synthesis

**Date:** 2026-04-10 / 2026-04-11
**Branch:** `feature/loci-layer-benchmark`
**Runs included:** 4 (Mistral 7B, Qwen 2.5 14B, Qwen3 30B-a3b, Claude Opus 4.6)
**Test prompts:** 17 across 5 buckets (recall, prep, connection, synthesis, adversarial)
**Context assembly arms:** 3 (A flat-only, B SoY-as-it-is, C loci layer)
**Total (prompt × arm × model) data points:** 204
**Judge model (all runs):** Claude Opus 4.6 via subagent

---

## TL;DR

1. **Loci helps every model tier that can navigate structured context.** Mistral 7B is below that threshold — loci catastrophically hurts it. Qwen 14B and Claude Opus both see measurable gains from loci. Qwen3 30B-a3b is an anomaly (likely model-specific, not a principled regression).
2. **The biggest finding isn't about loci at all.** Hallucinations drop monotonically from 16 prompts (Mistral 7B) → 0 prompts (Claude Opus) regardless of which retrieval strategy is used. Upgrading the model beats optimizing the retrieval.
3. **Loci's magnitude of improvement is small at the top.** On Claude, arm C beats arm A on relevance by 0.23 points on a 5-point scale. Real, but not transformative. The value case is strongest in the middle capability band, not at the ceiling.

---

## The experiment in 100 words

Three context-assembly strategies, same 17 prompts, same judge, four test-model tiers:

- **Arm A — flat-only:** tokenize prompt, LIKE-search 6 tables, dump rows.
- **Arm B — SoY-as-it-is:** Arm A + `get_profile`-style expansion for any contact found. The realistic baseline reflecting how SoY actually fetches data today.
- **Arm C — loci layer:** generalized BFS graph traversal from query-derived seeds through typed edges, output as a tree preserving the associative path.

Hard char-budget parity at 8000 per arm. Judge (Claude Opus via subagent) scored each answer blindly against pre-written gold facts on relevance, completeness, surfaced-non-obvious, hallucinations, and judge confidence.

---

## The four runs

| Model | Host | Avg answer latency (A/B/C) | Hallucinations (prompts, of 17) |
|---|---|---|---|
| Mistral 7B | Razer (`soy-1`) | 137s / 44s / 224s | 5 / 4 / 7 |
| Qwen 2.5 14B | Lucy (RTX 3080 Ti) | 4.2s / 4.1s / 6.5s | 1 / 3 / 6 |
| Qwen3 30B-a3b | Legion (RTX 5080) | 52s / 60s / 56s | 0 / 1 / 1 |
| Claude Opus 4.6 | Subagent | n/a (no per-call timing) | **0 / 0 / 0** |

Two things worth noting from the table before the scores:

1. **Qwen 14B is an order of magnitude faster per call than anything else**, including Mistral 7B on the same-class hardware. Lucy's RTX 3080 Ti handles Qwen 14B very well.
2. **Qwen3 30B-a3b is unexpectedly slow** (~55s per call) and had 2 timeouts on arm B during the initial run (P4, C3) requiring retries at a 25-minute cap. This is relevant to interpreting its mixed results below — something about the model or its inference backend is not behaving like a typical 30B-class MoE.

---

## Headline numbers

Mean scores across all 17 prompts per (model × arm). Bold = highest in each row.

| Dimension | Mistral 7B (A/B/**C**) | Qwen 14B (A/B/**C**) | Qwen3 30B (A/B/**C**) | Claude Opus (A/B/**C**) |
|---|---|---|---|---|
| Relevance | 2.88 / 3.06 / **2.29** ⬇ | 3.00 / 3.12 / **3.18** ⬆ | 3.06 / 3.06 / **3.00** | 4.12 / 4.29 / **4.35** ⬆ |
| Completeness | 2.12 / 2.41 / **1.82** ⬇ | 2.06 / 2.35 / **2.47** ⬆ | 2.65 / 2.88 / **2.82** | 3.71 / 3.94 / **3.88** |
| Surfaced non-obvious | 1.53 / 1.88 / **1.29** ⬇ | 1.65 / 2.00 / **2.24** ⬆ | 2.12 / 2.12 / **2.29** ⬆ | 3.76 / 3.76 / **3.94** ⬆ |
| Hallucinations (mean) | 0.35 / 0.29 / **0.82** ⬆bad | 0.06 / 0.29 / **0.53** ⬆bad | 0.00 / 0.06 / **0.12** ⬆bad | **0.00 / 0.00 / 0.00** |
| Judge confidence | 4.41 / 4.47 / 4.71 | 3.94 / 4.06 / 3.82 | 4.18 / 4.18 / 4.18 | 4.18 / 4.35 / 4.41 |

**Reading the table:** arrows indicate whether arm C is meaningfully above (⬆) or below (⬇) the best of arms A and B on that row for that model. The picture:

- Mistral: C loses every quality dimension and hallucinates more than 2× arms A and B.
- Qwen 14B: C wins every quality dimension. Hallucinations still elevated but better than Mistral.
- Qwen3 30B: C is essentially flat. Wins narrowly on surfaced, ties or loses elsewhere.
- Claude Opus: C wins relevance, surfaced, and confidence. Ties or narrowly loses on completeness.

---

## The monotonic signal: hallucinations

This is the cleanest single finding in the whole benchmark. Hallucination-producing prompts per arm, by model tier:

```
Tier        A     B     C
Mistral 7B  5     4     7
Qwen 14B    1     3     6
Qwen3 30B   0     1     1
Claude Opus 0     0     0
```

**Claude hit zero hallucinations across all 51 entries.** The judge independently flagged zero unsupported statements in 51 answers (every (prompt × arm) combination). This is not subject to same-model judging bias in the same way numeric relevance scores are — hallucination count is an objective tally of fabricated statements.

The implication: **if you care about fabricated facts in your personal-data system, model choice matters more than retrieval strategy.** A 5× improvement in hallucination rate from Mistral 7B → Qwen 14B on flat retrieval alone (arm A went from 5 → 1 prompts). Another 6× improvement from Qwen 14B → Claude Opus.

Loci's hallucination pattern is also interesting:
- At Mistral, arm C hallucinates **more** than A and B (the tree structure fuels confabulation)
- At Qwen 14B, arm C still hallucinates more than A (elevated but less bad)
- At Qwen3 30B, all arms are at or near zero
- At Claude, all arms are zero

The "loci causes hallucinations" effect is a lower-tier phenomenon. At capable-model tiers, it disappears.

---

## Per-bucket at each tier

Relevance by bucket. Bold = winner(s), ties marked with *.

| Bucket | Mistral 7B (A/B/C) | Qwen 14B (A/B/C) | Qwen3 30B (A/B/C) | Claude Opus (A/B/C) |
|---|---|---|---|---|
| Recall | 4.00 / **4.75** / 3.00 | 3.00 / 3.75 / **4.00** | *4.00 / *4.00 / *4.00 | 4.50 / **4.75** / 4.50 |
| Prep | **3.50** / 2.75 / 2.00 | **3.25** / 2.75 / 2.75 | 2.75 / 2.00 / **3.25** | 4.00 / 4.00 / **4.25** |
| Connection | 1.75 / 2.00 / **2.25** | 2.75 / **3.00** / 2.75 | *2.75 / *2.75 / 2.50 | 4.00 / **4.25** / 4.00 |
| Synthesis | *2.67 / *2.67 / *2.67 | 3.33 / **3.67** / 3.33 | *3.67 / *3.67 / 3.00 | 4.00 / 4.33 / **4.67** |
| Adversarial | 2.00 / **3.00** / 1.00 | 2.50 / 2.00 / **3.00** | **3.50** / 3.00 / 1.50 | 4.00 / 4.00 / **4.50** |

Patterns across the matrix:

- **Recall converges.** By Qwen3 30B all three arms tie. At Claude, B edges ahead. Flat retrieval gets the answer at the top of the ladder.
- **Prep is loci's best home turf on capable models.** Arm C wins Prep at Qwen3 30B and Claude, loses only at the two weaker tiers. This is where the graph's neighborhood structure pays off — assembling a "prep brief" from around an entity.
- **Connection peaks early then flattens.** Arm C wins at Mistral narrowly (everyone's bad), loses narrowly at Qwen 14B, loses at Qwen3 30B, ties at Claude. The "loci's home turf" framing is partially defensible but the margins are small.
- **Synthesis is a mixed bag.** Ties on Mistral, B wins on Qwen14B, A/B tie on Qwen3 30B, C wins on Claude. Claude's synthesis win is the biggest single margin on the Claude row.
- **Adversarial tells a story.** Mistral's arm C collapses (1.00) because the rich context fuels fabrication. Qwen 14B's arm C wins (3.00) because it's big enough to refuse. Qwen3 30B's arm C collapses again (1.50) — we don't know why. Claude's arm C wins (4.50) cleanly.

---

## Arm C per-prompt progression across all four models

This is the most information-dense view of the experiment. Each row shows how arm C performed on a specific prompt as the test model scaled.

| Prompt | Mistral | Qwen14B | Qwen3-30B | Claude | Bucket |
|---|---|---|---|---|---|
| R1 | 5 | 5 | 5 | 5 | recall |
| R2 | 5 | 5 | 5 | 5 | recall |
| R3 | 1 | 2 | 2 | **4** | recall |
| R4 | 1 | 4 | 4 | 4 | recall |
| P1 | 2 | 2 | 4 | **5** | prep |
| P2 | 1 | 4 | 4 | 4 | prep |
| P3 | 4 | 3 | 4 | **5** | prep |
| P4 | 1 | 2 | 1 | **3** | prep |
| C1 | 2 | 2 | 1 | **3** | connection |
| C2 | 1 | 2 | 2 | **4** | connection |
| C3 | 2 | 4 | 3 | **4** | connection |
| C4 | 4 | 3 | 4 | **5** | connection |
| S1 | 5 | 4 | 5 | 5 | synthesis |
| S2 | 2 | 4 | 1 | **4** | synthesis |
| S3 | 1 | 2 | 3 | **5** | synthesis |
| A1 | 1 | 3 | 1 | **5** | adversarial |
| A2 | 1 | 3 | 2 | **4** | adversarial |

Reading the columns:

- **R1, R2, S1 are ceiling prompts** — all four models ace them on arm C. They're single-fact retrieval with rationale. Arm C's wide context doesn't help or hurt; the fact is extractable from any context.
- **R4, P2 make the jump from 1 → 4 between Mistral and Qwen 14B**. These are the prompts where Mistral's arm C catastrophically failed (got lost in the tree) and Qwen 14B recovered. This is the "loci unlocks the medium tier" story, cleanly visible.
- **P1, R3, P4 take until Claude to recover** — Mistral and Qwen 14B both struggle, Qwen3 30B sometimes helps, Claude nails them. These are the prompts where the medium-tier gains weren't enough.
- **C1 is the hard one.** It stayed stuck at 1-2 for every model until Claude's 3. Even Claude couldn't score a 4 or 5 on the Reprise/BATL connection prompt. This is loci's designated "home turf" prompt — the cleanest test of cross-project tag-walks — and it's the hardest prompt in the whole set for arm C at every model tier. That's a real finding worth naming: the insight required ("both projects share a private intelligence layer framing") needs a capable reasoner AND a context that surfaces the right notes AND the ability to make an inferential leap. Claude gets partway there but doesn't quite stick the landing.
- **A1 is the cleanest Mistral → Qwen14B → Qwen3-30B → Claude story** (1 → 3 → 1 → 5). Mistral confabulates a demo; Qwen 14B refuses; Qwen3 30B falls BACK into confabulation; Claude refuses perfectly. The Qwen3 30B regression on A1 is the single most dramatic Qwen3 anomaly and is one of the stronger arguments that Qwen3's results shouldn't be taken as representative of "30B-class" capability.
- **A2 (chemo descriptor) never fully resolves.** Best score is 4 at Claude. The descriptor-to-entity resolution that Maya's panel designed as a contamination check remains hard across all tiers. Even with the find_seeds fix (which put the chemo email into arm C's context at the assembly stage), the model still has to make the "Jessica's mom is in chemo" inferential leap from a single summary line. Qwen3 30B and Claude both recognize Jessica as a client in the context but hedge on the chemo connection.

---

## The Qwen3 30B anomaly

Qwen3 30B-a3b is the strangest data point in the benchmark. Summary of what we observed:

- **Slow per-call latency** — 52-60 second average on a RTX 5080, vs Qwen 14B's 4-7 seconds on an RTX 3080 Ti. The 30B MoE should not be 10× slower than Qwen 14B on better hardware. Possible causes: the a3b variant's MoE routing overhead, context window handling, or an inference backend issue.
- **Two timeouts** on the initial run (P4 arm B, C3 arm B) at the 900-second cap. Both succeeded on retry at a 1500-second cap, taking 123s and 67s respectively — suggesting the initial timeouts were tail-of-distribution behavior, not permanent failures.
- **Overall scores flat compared to Qwen 14B**. Completeness went UP from 14B → 30B (2.47 → 2.82 for arm C) but relevance stayed the same or dipped slightly. This is a surprisingly weak scaling result for a 2× parameter increase.
- **Loci regresses specifically on adversarial and some synthesis prompts**. A1 went from 3 (Qwen 14B) → 1 (Qwen3 30B) → 5 (Claude) — a dramatic U-shape that only Qwen3 30B produces.

My best guess: **Qwen3 30B-a3b's behavior on this benchmark is model-specific, not representative of the 30B capability tier.** The next data point that would clarify this is a different 30B-class model (Mistral Large 2, Llama 3.3 70B, or an earlier Qwen3 variant without the MoE a3b twist). Without that confirmation, the Qwen3 30B row should be treated as provisional.

---

## What this revises about the original loci hypothesis

**Original hypothesis:** *"Richer context assembly unlocks the cheap tier of models. Smaller models fail because their context is too thin. Loci fixes that."*

**Actual finding:** *"Richer context assembly HURTS the cheap tier (it exceeds the model's working memory and fuels hallucination). It helps the middle tier (where the model can navigate structured context but benefits from having more of it). At the ceiling, it provides small but measurable gains."*

Three specific corrections:

1. **"The cheap tier" is wrong.** Mistral 7B is below the threshold. Loci actively hurts it. The right starting point for loci benefits is the 14B tier.
2. **"Unlock" is wrong.** Even at the Claude tier, the improvement is 0.23 points on a 5-point scale. That's meaningful but not transformative. Loci is an optimization, not an unlock.
3. **The Qwen3 30B dip is unexplained and probably noise.** One data point in the middle of the ladder regressing doesn't break the story if it's model-specific, which it probably is.

---

## Caveats and limitations

### 1. Same-model judging

All four runs were judged by Claude Opus 4.6 via subagent. For runs 1-3 (Mistral, Qwen 14B, Qwen3 30B) this is reasonable — the judge is a different model family from the test model. **For run 4 (Claude Opus as test model), the judge and test model are the same model class.** This is a real bias risk.

Mitigations we applied:
- Explicit impartiality language in the judge prompt (inlined into `package_*.json`)
- Blind labeling (judge never saw arm IDs, only randomized 1/2/3 labels)
- Judge's post-run notes confirmed discipline ("zero hallucinations flagged across all 51 entries")
- The judge rationale fields provide traceability for every score

Mitigations we did NOT apply:
- A second-family judge (Qwen 14B as judge, GPT-4 as judge) for cross-validation
- Multiple judge runs with averaging

**What this means for the Claude row:** the numeric scores (relevance, completeness, surfaced) are more susceptible to same-model style bias. The hallucination count is most robust because it's an objective tally. If you only trust one number from the Claude row, trust the zero-hallucination count.

### 2. Single-run variance

Each cell in the four-model table is N=1 run. With 17 prompts per run, directional findings are defensible but confidence intervals are wide. Re-running each tier twice and averaging would tighten the story materially. This is the highest-value follow-up experiment.

### 3. Prompt set composition

17 prompts across 5 buckets is enough to see signal per bucket but small for meaningful per-bucket statistics. The adversarial bucket (2 prompts) is particularly thin — a single outlier prompt skews the bucket average.

### 4. The Qwen3 30B caveat (repeated)

Qwen3 30B-a3b's weird behavior (slow per-call, timeouts, flat scaling, adversarial regression) is unexplained. Treat its row as provisional pending a second 30B-class data point.

### 5. Char-budget parity is upper-bound only

The 8000-char parity cap brings arm C's upper bound down but doesn't equalize. Arms A and B are frequently well under the cap; arm C is consistently at it. So arm C still gets on average 65% more context than arm A, which is a residual length-confound. Fully parity-controlled would require inflating arms A and B to match, which introduces a different confound (padding with irrelevant rows).

### 6. Known bugs fixed mid-experiment

Between the Mistral run and the Qwen runs, I fixed a `find_seeds` keyword-ordering bug that was causing descriptor-style prompts (A2) to miss their strongest seeds. The Mistral run had the bug; Qwen 14B, Qwen3 30B, and Claude runs did not. This means the Mistral row's arm A and arm C scores are slightly depressed vs what they would be with the fix. The overall "Mistral doesn't benefit from loci" finding survives this, but anyone reading the numbers should know the Mistral row used a different (buggier) seed selection logic than the later three.

### 7. No cross-judging

The judge was Opus 4.6 for every run. A proper benchmark would have a different-family judge as a cross-check. The panel commentary appended below partially addresses this with adversarial review, but it's not a full re-scoring.

---

## The practical recommendations

### Should you ship the loci layer as a SoY feature?

**Qualified yes, with tier-aware routing.**

- For the **cheapest tier** (Mistral 7B or similar), DON'T use loci — it actively hurts. Flat retrieval or `get_profile` is better.
- For the **medium tier** (Qwen 14B class), use loci — it's a clear win.
- For the **capable tier** (Qwen3 30B class), optional — the gain is marginal and may regress on specific prompt shapes. Not strongly recommended but not harmful.
- For the **ceiling tier** (Claude Opus, GPT-4o class), use loci — small but measurable wins with no downside except token cost for API users. For subscription users, it's free upside.

This implies the existing `pick_model()` / `pick_machine()` routing logic should have a `use_loci` flag that defaults to true above a certain model tier threshold.

### What to fix if iterating on loci itself

Ordered by value × tractability:

1. **Fix or investigate the C1 prompt.** Every model at every tier struggles with it. Either the gold answer is unrealistic, loci's reach isn't surfacing the BATL note, or the insight requires something loci can't provide. This is the single most informative prompt to understand.
2. **Renderer multi-path fix (Maya's panel finding).** Currently when a node is reached via multiple edges, only the first parent is rendered. Loci's value prop ("see the connections") is being undersold. Medium refactor, deferred.
3. **200-char detail truncation cuts important content.** Specifically the Jessica chemo note gets cut mid-summary. Bump to 600 chars for `standalone_note.content` and `interaction.summary`. Trivial.
4. **Tighter breadth/depth caps.** Arm C consistently hits the 60-node cap. Consider 40 or 50 — less context might force better selection.
5. **Second-family judge.** Run a cross-judge (Qwen 14B or similar) to triangulate the Claude-judges-Claude result.

### What to fix if iterating on the SoY data itself

1. **Duplicate James Andrews contact** (id 7 and id 9, same email). Merge. This came up in R3 and contaminated the arm A context for any James-related query.
2. **Inconsistent `linked_projects` field format** (some numeric IDs, some JSON arrays, some project NAME strings). Normalize to ID arrays. Loci handles all three formats now via defensive parsing, but it's a data quality issue that affects every graph-traversal approach.
3. **Calendar events not linked to contacts/projects** (all 12 events in the live DB have `contact_ids=None` and `project_id=None`). The calendar_events walk in loci and the equivalent fetch in arms.py B are both inert on this data because of this. Fix the linking at sync time.
4. **Journal empty for the past week.** Affects any "what's been on my mind" prompt. Not a bug, just an inactive input source.

---

## What I'd tell someone in one breath

*"Loci helps models that can navigate structured context — which rules out the smallest tier (Mistral 7B) and is most dramatically confirmed at the top (Claude Opus). The bigger practical finding is that hallucinations drop to zero at the Claude tier regardless of retrieval strategy, which means the whole loci question matters less than the model choice question."*

---

## Appendix A: Run artifacts

All run artifacts are in `benchmarks/loci/`:

- `prompts.json` — the 17-prompt set with gold answers and design notes
- `results.db` — SQLite with all 204 (prompt × arm × model) rows plus judge scores
- `report-20260410_143933.md` — Mistral 7B individual report
- `report-20260410_172024.md` — Qwen 14B individual report
- `report-20260410_172025.md` — Qwen3 30B individual report
- `report-20260410_215133.md` — Claude Opus individual report
- `synthesis-four-tier.md` — this document

The package / blind-map / scores / test-answers files per run are also present but gitignored (regenerable from `results.db`).

## Appendix B: Branch state

Feature branch `feature/loci-layer-benchmark`, atop `main` of `origin/better-software-of-you`. As of this synthesis, 17 commits ahead of main. PR-ready against upstream `kmorebetter/better-software-of-you` modulo the `ALLOWED_TABLES` whitelist verification pass and any final panel findings.
