# Loci Layer Benchmark

A/B/C harness for evaluating whether replacing SoY's flat retrieval with a graph-traversal "loci layer" produces better LLM answers, particularly when the test model has limited context budget (e.g., Mistral 7B).

**Status:** Work in progress. Foundation only — runners and judge land in subsequent commits.

## What this tests

Three context-assembly strategies, evaluated on the same prompts against the same test model, scored blind by an LLM judge:

| Arm | Strategy | What it represents |
|---|---|---|
| **A — flat-only** | `search` + per-table SQL queries, no cross-module assembly | The minimum baseline. Hand-rolled SQL retrieval, no traversal. |
| **B — SoY-as-it-is** | Arm A + the existing `get_profile` tool which already does hand-coded contact-centered traversal | The honest baseline. What SoY actually delivers today when the LLM picks the right tool. |
| **C — loci layer** | Generalized graph traversal from any starting entity, applied to all entity types | The new thing. What we're testing. |

The hypothesis is that arm B beats arm A on contact-centered prompts (because `get_profile` exists), and arm C matches B on contact prompts and beats it everywhere else by generalizing the traversal pattern.

## Test model and judge

- **Test model:** `mistral:7b` on the Razer (`100.91.234.67:11434`). Chosen because thin-context failure modes are most visible on smaller models — if loci helps anywhere, it should help here most.
- **Judge:** `claude-opus-4-6` via the Anthropic API. Blind to which arm produced each answer (arms are randomly labeled `1/2/3` per prompt and unscrambled after scoring).

The judge produces a structured rubric per answer (relevance, completeness, surfaced-something-non-obvious, hallucination count, judge confidence) along with one-sentence reasoning per dimension. The reasoning fields are intentional — they exist so the rubric can be panel-reviewed after the run, not just trusted on numeric grounds.

## Prompt set

17 prompts across 5 buckets:

| Bucket | Count | What it measures |
|---|---|---|
| Recall | 4 | Calibration. Single-fact and multi-fact retrieval. Loci shouldn't help much vs flat. |
| Prep | 4 | Neighborhood assembly around a starting entity. Where arm B starts to pull ahead of A. |
| Connection | 4 | Non-obvious links between things. Loci's home turf. |
| Synthesis | 3 | Multi-entity pattern recognition with pre-written gold answers (so the judge can grade). |
| Adversarial | 2 | Direct measurement of failure modes — refusal-appropriate prompts and indirect entity reference. |

Every prompt is anchored to a checkable fact in the actual local DB state. See `prompts.json` for the full set with gold answers and design notes per prompt.

## Upstream-compatibility constraint

This benchmark and the loci layer it tests are designed to be PR-able to upstream `kmorebetter/better-software-of-you`. To keep the diff clean, **the loci layer is restricted to tables and views from migrations 001-016**, which both the local fork and upstream share:

```
contacts, contact_tags                          (002)
projects, project_tasks, project_milestones    (003)
emails, email_threads                           (004)
calendar_events                                 (005)
conversation_intelligence tables               (006)
decisions, journal_entries                      (007)
notes                                           (008)
call_intelligence                               (009)
transcript_sources                              (010)
decision_outcomes_v2                            (011)
user_profiles                                   (013)
v_contact_health, v_project_health, etc.       (014, computed views)
multi_account contact tables                    (015, 016)
```

Tables introduced in local-only migrations 017+ (auditions, PM intelligence, signal harvester, financial, etc.) are intentionally **off-limits** to the loci core. They could be supported via a separate plugin layer in the future, but the core stays Kerry-PR-clean.

## Directory layout (forward-looking)

```
benchmarks/loci/
├── README.md           # this file
├── .gitignore          # ignore runtime artifacts (results.db, caches)
├── prompts.json        # 17 prompts with gold answers and design notes
├── runner.py           # main entry — subcommands like run, judge, report
├── arms.py             # arm A/B/C context-assembly implementations
├── judge.py            # Claude Opus judge via urllib (no anthropic SDK)
└── report.py           # markdown report generator from results.db
```

The loci layer itself lives in `shared/loci.py` (the production module being tested), not in `benchmarks/loci/`.

## How to run (placeholder — runners land in a later commit)

```bash
# Run all arms on all prompts, judged by Claude Opus
python3 benchmarks/loci/runner.py all

# Run just one arm
python3 benchmarks/loci/runner.py arm c

# Generate the markdown report from the latest results
python3 benchmarks/loci/runner.py report
```

Results are written to `benchmarks/loci/results.db` (gitignored) and a markdown report is generated alongside it.

## Conventions

This benchmark follows the same conventions as `benchmarks/gemma4/`:

- **Python stdlib only** — no external dependencies. `urllib` for both Ollama and the Anthropic API. `sqlite3` for results storage. `json` for prompts.
- **Self-contained** — no virtualenv, no setup, no `pip install`. Just `python3 runner.py`.
- **Tailscale hostnames hard-coded** — same `HOSTS` dict as `gemma4/benchmark.py` so model routing is consistent.
