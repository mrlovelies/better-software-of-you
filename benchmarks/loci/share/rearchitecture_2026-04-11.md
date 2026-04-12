# Loci schema rearchitecture — V1 → V2 migration notes

Parallel technical document to `findings.md`. Where `findings.md` captures *patterns to consider*, this document captures *the migration story* — what V1 looked like, why it was painful, how we moved to V2 without losing benchmark comparability, and what we learned from the migration process itself. Intended for Claude consumption as background context on a working schema rewrite that just landed.

---

## V1: the schema we were rebuilding from

The original store was a conventional personal-data-management schema built over ~8 migrations: contacts, projects, tasks, milestones, decisions, notes, emails, calendar events, transcripts, commitments, journal entries, polymorphic tags. Foreign keys wired it together. A context-assembly layer (`loci.py` V1, ~800 lines) walked it via ~10 per-entity-type expander functions and produced a tree-rendered context blob for LLMs.

Six specific pain points motivated the rewrite:

### Pain 1 — TEXT columns holding ambiguous reference lists

Two tables used TEXT columns to encode many-to-many relationships as serialized id lists. The columns were populated inconsistently over time:

```sql
-- standalone_notes.linked_contacts and .linked_projects contained 3 formats:
--   JSON array of ids:   '[7, 12]'
--   Bare numeric string: '7'
--   Plain name string:   'Jessica Martin'  (sic — a *name*, not an id)
--
-- calendar_events.contact_ids had a similar TEXT-list shape but was
-- populated on exactly one row out of 12.
```

The loci walker had to parse every invocation, with a defensive `_parse_id_list` function that tried JSON first, then regex for digits, then a separate `_name_in_linked_field` path for the plain-name case. Name matching was the worst part: naive substring matching produced false positives (`'Repri'` would falsely match `'Reprise'`), so matching had to be exact against parsed list elements. Every new edge class added a new variant of this parser.

### Pain 2 — Per-entity-type expander sprawl

Walking the graph required a dedicated function per entity type: `_expand_contact`, `_expand_project`, `_expand_decision`, `_expand_standalone_note`, `_expand_email`, `_expand_transcript`, `_expand_tag`, `_expand_interaction`. Each was 30-80 lines of hand-written SQL tailored to the specific FK relationships on that table. Adding a new entity type meant another expander; adding a new relationship meant modifying the expander for both sides (forward and reverse walks were separate code paths).

The code worked. It just didn't scale across schema evolution. Every ALTER TABLE was a hunt through 800 lines for "which expander knows about this column."

### Pain 3 — Duplicate entities with no reconciliation layer

Specific concrete case: one real person had been entered twice as two contact rows with slightly different company names. Both rows were referenced by different emails and interactions. Deduping required a merge operation that rewired edges, but there was no schema for tracking the merge — deleting one row would strand references; updating foreign keys by hand across eight tables was error-prone.

### Pain 4 — Ghost records diluting walks

An audit of the contact table found that a majority of rows (~72%) had zero real correspondence. They were aspirational/research records entered once and never touched. Without a status enum, the walker treated them as live — walks near any adjacent node would expand into these dead ends and burn budget. Recall suffered quantitatively on benchmark prompts that needed to reach many hops from a single seed.

### Pain 5 — No layer for cross-entity framing

Benchmarks surfaced a specific failure class: *"how does X relate to Y"* prompts where X and Y share a conceptual framing but no direct FK. The shared framing lived only in prose across multiple note bodies. Flat retrieval couldn't answer these because the connecting concept wasn't materialized; graph walk couldn't answer them because there was no edge to walk. This was the single hardest failure class and it motivated the episodes table (see `findings.md` Finding 1).

### Pain 6 — No status separation for merged/archived rows

When rows were "no longer active" there was no way to say so in the schema. Contacts that had gone cold, projects that had been abandoned, decisions that had been superseded — all lived in the same row space as active items. Walks couldn't filter cleanly. The audit fix required adding a status enum on contacts; a general-purpose archival model is deferred to a future iteration.

---

## The decision: parallel DB, not in-place migration

We had two options for the rewrite:

1. **In-place migration** of the existing database — ALTER TABLE / CREATE TABLE / data backfill against the live store.
2. **Parallel database** (`next_soy.db`) built from a fresh schema with data copied from the source.

We chose parallel for three reasons:

### Reason 1 — Benchmark comparability

The loci work was under active benchmarking against a 17-prompt × 3-retrieval-arm × multi-model-tier suite. An in-place migration would have meant the benchmark's data substrate was changing under the runner, and every failure would carry ambiguity ("is this a retrieval-layer change or a schema change?"). Parallel DBs let us run the same benchmarks against V1 and V2 schemas on the same source rows and attribute deltas cleanly to the schema change.

### Reason 2 — Risk isolation

The source DB was the working production store. Bugs in a backfill script could corrupt live data. Parallel DBs let the seed script open the source in read-only mode (`file:<path>?mode=ro`) with zero write-path exposure. Any bug in the seed script only damaged the parallel copy, which was disposable by design.

### Reason 3 — Ablation for data-vs-schema effect

The richer question beneath the benchmark was *"does the schema change help, or does the additional seed data (audit-driven new rows, memory episodes) help?"* With a parallel DB and a deterministic seed, we can build two variants — one with schema changes only, one with schema + new data — and compare both against the V1 baseline. The deltas separate cleanly:

- **V1 → V2-schema-only:** pure schema effect
- **V2-schema-only → V2-with-new-data:** pure data effect
- **V1 → V2-full:** combined effect

An in-place migration couldn't produce this attribution without undoing and redoing work.

### What parallel cost us

The cost of parallel was a 900-line seed script that had to faithfully replicate ~500 rows across 16 carry-over tables and then populate 8 new tables with audit-driven additions. Straightforward work but meticulous — every ALTER TABLE the V1 schema had gained over 8 migrations needed to be present in the V2 DDL, and every column had to match type/constraint. We wrote the DDL as a single `001_core.sql` file (rather than a migration chain) because V2 is being treated as a clean snapshot, not a live-evolving store yet.

### Mapping to mempalace-rust

`mempalace-rust` already has some of this discipline baked in — the observer reads source data (`~/.claude/projects/*.jsonl`, git log, task files) without modifying it, and writes derived output (`LOCI.md`) separately. The architectural bet is the same: **derived stores should be disposable and regeneratable from source, not mutated in place.**

If `mempalace` grows a cached observation store (e.g., a SQLite index of previously-observed sessions to skip re-parsing), the parallel-DB principle applies: the cache should be regeneratable from source, never edited by hand, and the schema should be a snapshot (single CREATE file) rather than a migration chain until it's mature.

---

## The landing shape: 8 new tables

| Table | Replaces | Purpose |
|---|---|---|
| `entity_edges` | implicit FK walks + TEXT-list columns | Typed junction edges. The load-bearing table. Every loci traversal consults it via two generic expanders. |
| `contact_identities` | nothing (new) | Canonical identity + aliases. Solves duplicates via merge support: `merged_into_id` on contacts + unique index on `(identity_type, identity_value)`. |
| `notes_v2` | `standalone_notes` | Same data without the ambiguous TEXT columns. Adds `note_kind`, `promoted_to_*` for tracking idea→decision→outcome chains, and `source_ref` for provenance. |
| `daily_logs` | empty in V1 | New input surface. One row per day of freeform markdown. Scanned for `[[wikilinks]]` on write. |
| `log_mentions` | nothing (new) | Resolved entity mentions in daily_logs. Has `mention_status` (`resolved`/`suggested`/`rejected`) so LLM-suggested mentions go into a review queue, never directly resolved. |
| `wikilinks` | nothing (new) | Alias resolution table. First-class alias→entity mapping. `[[Jessica]]` in a daily_log resolves through this table. |
| `memory_episodes` | nothing (new) | Named temporal-associative contexts. Hand-authored in V1 (see `findings.md` Finding 1). |
| `episode_members` | nothing (new) | Entity membership in episodes with a `role` field (protagonist/witness/setting/artifact/member). |

The `contacts` table is modified in place (inside the fresh V2 schema, not the V1 source) to add three columns: `merged_into_id`, `merged_at`, and an expanded `status` enum (`active`/`prospect`/`inactive`/`broadcast_only`).

The total of 22 tables in V2 is intentionally smaller than the V1 source (~58 migrations). We explicitly excluded modules that don't affect loci results: auditions, signal harvester, learning digests, financial records. V2 is a benchmark target, not a full mirror.

---

## The edge_type enum strategy

This is load-bearing and worth explaining in detail.

### Three tiers

Edge types are organized into three priority tiers, each captured as a constant in the walker:

```python
EDGE_PRIORITY = {
    # Tier 1 — structural / high-information (backfilled from source FKs)
    "client_of":          1,  # project → contact
    "decided_in":         1,  # decision → project
    "involves_contact":   1,  # decision → contact
    "belongs_to_project": 1,  # task → project, milestone → project
    "email_with":         1,  # email → contact
    "interaction_with":   1,  # contact_interaction → contact
    "event_with":         1,  # calendar_event → contact
    "participated_in":    1,  # transcript → contact
    "commitment_by":      1,  # commitment → contact

    # Tier 2 — professional/personal network (audit-driven, manual)
    "works_at":           2,
    "employed_by":        2,
    "collaborator_on":    2,
    "colleague_of":       2,
    "family_of":          2,
    "close_friend_of":    2,
    "mentor_of":          2,
    "books_for":          2,
    "building_site_for":  2,
    "agent_of":           2,
    "represented_by":     2,
    "shareholder_of":     2,
    "owner_of":           2,
    "cc_regular_of":      2,
    "neighbor_of":        2,
    "prospect_for":       2,

    # Tier 3 — content / conceptual (mentions, episodes, framing)
    "mentions":           3,
    "part_of_episode":    3,
    "shares_framing_with": 3,
    "promoted_to":        3,
    "supersedes":         3,
    "derived_from":       3,
}
```

Tiers act as a secondary sort key during walks when the breadth budget is tight — structural edges get processed first, then network, then content. This prevents a node with 50 `mentions` edges from crowding out a single `client_of` edge when the budget is 5.

### No CHECK constraint on edge_type

Deliberate choice: the `edge_type` column has no CHECK constraint listing the valid values. Adding a new edge type is a *data* change (write a new row with `source='manual'`), not a schema migration. If we used a CHECK, every new type would need an ALTER TABLE.

Trade-off: no database-enforced validation. We rely on a code-level convention (the `EDGE_PRIORITY` dict above) and a validation check in the seed script's report (`unknown entity_types present` is a failure). This is the right trade for a benchmark DB that evolves weekly; a production DB might want the CHECK.

### Asymmetric vs symmetric edges

Asymmetric edges (`client_of`, `decided_in`) have one canonical direction. Symmetric edges (`colleague_of`, `family_of`, `close_friend_of`) are recorded once in the direction that feels natural and walked from both sides via `expand_inbound`. The walker synthesizes reverse labels (`[client_of ← reverse]`) at render time from the `via_direction` field on nodes.

We considered auto-materializing reciprocal rows at insert time. Rejected because it doubles edge count for zero query advantage once `expand_inbound` exists. The schema carries one row per edge; the walker handles direction.

### metadata JSON is edge-type-specific

Every edge row has a nullable `metadata` TEXT column holding edge-type-specific JSON. Examples:

- `mentions`: `{"char_start": int, "char_end": int, "mention_text": str}`
- `building_site_for`: `{"workspace": "/path/to/workspace"}`
- `shares_framing_with`: `{"framing_concept": str, "evidence_sources": [str]}`
- `client_of`: `null` (structural; no metadata needed)

Edge-type metadata shapes are documented separately. We considered per-edge-type columns; rejected as overkill for a benchmark DB.

### Mapping to mempalace-rust

If `mempalace` grows a unified `observation_edges` table (see `findings.md` Finding 3), the three-tier priority strategy maps cleanly. For mempalace, the tiers might be:

- **Tier 1** (structural): `references_task`, `commits_file`, `touches_chain` — hard, observable relationships
- **Tier 2** (pattern): `shares_role`, `same_window`, `same_archetype` — observed co-occurrence
- **Tier 3** (semantic): `continues_thread`, `discusses`, `renames` — inferred, cheaper

And the "no CHECK constraint" discipline lets `edge_kind` become an `enum` in the Rust code without a migration every time a new kind lands.

---

## The 9-step backfill

The seed script is structured as nine explicit phases, each idempotent:

### Step 1 — Apply DDL + copy carry-over tables

Apply `001_core.sql` to a fresh file. Then attach the V1 source in read-only mode and `INSERT INTO ... SELECT` for every carry-over table. Explicit column lists (not `SELECT *`) so renamed/dropped columns fail loudly. Tables copied in FK order.

### Step 2 — Translate `standalone_notes` → `notes_v2`

```sql
INSERT INTO notes_v2 (id, title, content, note_kind, pinned, source, created_at, updated_at)
SELECT id, title, content, 'freeform', pinned, 'migrated_from_standalone', created_at, updated_at
FROM soy.standalone_notes;
```

Preserves original ids so downstream references (especially from episode artifact lookups) stay stable. The ambiguous `linked_contacts` / `linked_projects` / `tags` columns are dropped — their content is translated into edges in step 4.

### Step 3 — Populate `entity_edges` from structural FKs

One `INSERT INTO entity_edges SELECT ...` per FK relationship in the canonical enum. Example:

```sql
INSERT OR IGNORE INTO entity_edges
    (src_type, src_id, dst_type, dst_id, edge_type, source, metadata)
SELECT 'project', id, 'contact', client_id, 'client_of',
       'backfill',
       json_object('original_column', 'projects.client_id')
FROM projects
WHERE client_id IS NOT NULL;
```

Every row is tagged `source='backfill'` and carries metadata pointing at the original column. `INSERT OR IGNORE` handles the UNIQUE constraint on `(src_type, src_id, dst_type, dst_id, edge_type)` — duplicate structural edges from the source are collapsed silently.

`event_with` (calendar_events → contacts) is a special case because the source is a TEXT list, not a scalar FK. The seed script parses the list per row and emits one edge per parsed id.

### Step 4 — The hard one: parse legacy TEXT columns into `mentions` edges

For every row in the source's `standalone_notes` table, parse `linked_contacts` and `linked_projects` using the three-format parser:

```python
def _parse_id_list(raw):
    """Parse a TEXT field that might be JSON, CSV, or a bare id string."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip().isdigit()]
    s = str(raw).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [int(x) for x in parsed if str(x).strip().isdigit()]
    except (json.JSONDecodeError, ValueError):
        pass
    return [int(x) for x in re.findall(r"\d+", s)]
```

For each parsed id, emit an `entity_edges` row: `(src_type='notes_v2', src_id=note_id, dst_type='contact'|'project', dst_id=parsed_id, edge_type='mentions', source='backfill', metadata={origin_column})`.

Then the name-fallback pass: for any element in the parsed field that *isn't* a digit (i.e., a plain entity-name string left over from the schema's early days), look it up by name in the target table:

```python
for name in _raw_linked_strings(raw):
    match_id = name_to_id_map.get(name)  # exact match only, no fuzzy
    if match_id:
        insert_mention_edge(...)
    else:
        unresolved.append(f"notes_v2:{note_id} linked_projects name={name!r} not found")
```

Unresolved matches get written to `seed_unresolved.log` for manual review, never guessed, never created as stub entities.

This step alone translated ~25 legacy mentions into typed edges, of which 16 came from the name-fallback path — meaning 64% of the legacy mentions data would have been lost without the fallback. High-value step for any migration out of TEXT-blob relationship columns.

### Step 5 — Populate `contact_identities` from existing `contacts.email`

```sql
INSERT OR IGNORE INTO contact_identities
    (canonical_contact_id, identity_type, identity_value, confidence, verified, source)
SELECT id, 'email', email, 1.0, 1, 'backfill'
FROM contacts WHERE email IS NOT NULL AND email != '';
```

Trivial but needed to populate the identity layer before the dedup step.

### Step 6 — Dedupe (the duplicate contact)

Merge the duplicate by updating `merged_into_id` on the losing row, then rewiring any edges that pointed at the losing id to the winning id, then pruning the losing id's identity rows. The losing row is kept (status flipped to `inactive`) rather than deleted — keeping it preserves referential integrity for any journal entry or email that still references the old id.

### Step 7 — Audit-driven additions (domain-specific, omitted here)

This step inserts new rows for entities discovered during the audit phase and applies status flips to existing ghost records. It's highly domain-specific (personal data) and the interesting part for a general audience is the *shape* of the operation, not the contents:

- A constant list of new rows to insert, each with a stable `key` handle
- A constant list of status flips: `[(source_id, new_status, reason), ...]`
- A constant list of new edges referencing new rows by `key` and existing rows by `id:N`
- A resolver function that translates keys to actual ids after insert

The shape generalizes to any "seed from audit findings" script. The key discipline: don't rely on auto-assigned ids until after insert; use stable handles during the data definition.

### Step 8 — Hand-authored episodes

Insert memory_episodes rows from a Python constant, then insert episode_members with a mix of resolver kinds:

```python
{
    "title": "Operator intelligence layer",
    "summary": "...",
    "started_at": "...",
    "members": [
        ("project", "id", 210, "protagonist"),          # by id
        ("project", "id", 2, "protagonist"),            # by id
        ("notes_v2", "title_like", "...private ops%", "artifact"),  # by title pattern
        ("contact", "key", "alex", "member"),           # by new-contact key
    ],
}
```

The resolver kinds (`id`, `title_like`, `key`) each have a different lookup path. If a `title_like` match fails (the note doesn't exist), log a warning and insert the episode *without* that member rather than failing the whole seed — episodes are useful even if one artifact is missing.

Also emit a `part_of_episode` edge for each successful member insert, so the walker discovers episodes via the generic `expand_outbound` path without needing a special-case episode walker.

### Step 9 — Populate wikilinks

Primary aliases: one row per contact, project, and decision with `alias = entity_name`, `is_primary = 1`, `created_by = 'import'`.

Hand-curated shortforms from a Python constant: `("Jessica", "contact_id", 1)`, etc.

Ambiguous aliases (same short-form maps to multiple entities) are not inserted as primary rows — they're written to `wikilinks_ambiguous.log` for manual review and resolved at query time by the writer, never auto-resolved.

---

## Validation: 9 checks, 1 cross-module acceptance test

The seed script runs 9 validation checks and writes a markdown report. The first 8 are standard migration-safety checks; the 9th is the load-bearing one and worth calling out.

```
1. Row count parity                       — carry-over tables match source counts
2. notes_v2 count matches standalone_notes
3. entity_edges has rows                   — walker has something to walk
4. No orphaned edges                       — every (type, id) reference resolves
5. Unresolved-link log reviewed            — seed_unresolved.log exists and was checked
6. Contact identity uniqueness             — no duplicate (identity_type, identity_value)
7. Wikilink ambiguities logged             — wikilinks_ambiguous.log exists for review
8. Contact status distribution             — at least one row in active/prospect/broadcast_only
9. building_site_for cross-reference test  ← LOAD-BEARING, explained below
```

### Check 9 — the cross-module acceptance test

The audit surfaced a specific class of fact: for four of the new contact rows, the user was actively building dev projects for them (there were workspace directories on disk at predictable paths like `/wkspaces/<name>-site`). This is a fact that the V1 flat schema couldn't express — there was no edge type for "X is building a development site for Y" — and it was the kind of cross-module fact (data spans both the contacts table and the workspace filesystem) that the loci layer was specifically designed to surface.

The validation check makes this explicit:

```python
expected_workspaces = {
    "/wkspaces/<client1>-site",
    "/wkspaces/<client2>-site",
    "/wkspaces/<client3>-site",
    "/wkspaces/<client4>-site",
}
found = set()
for row in conn.execute("""
    SELECT metadata FROM entity_edges
    WHERE edge_type = 'building_site_for' AND src_type = 'contact'
"""):
    md = json.loads(row["metadata"]) if row["metadata"] else {}
    if md.get("workspace"):
        found.add(md["workspace"])
missing = expected_workspaces - found
assert not missing, f"missing: {missing}"
```

If this check fails, the walker cannot answer "who is the user building sites for right now" even though the facts exist in the workspace. It's the single clearest example of a cross-module fact that V2 must express and V1 couldn't, and it's a regression test: if a future schema change accidentally drops these edges, this check catches it.

### Why these specific 9

The other eight checks are standard (row counts, orphan detection, uniqueness). Check 9 is domain-specific but the *pattern* is load-bearing: **every schema that's supposed to solve a specific cross-module problem should have an acceptance test that encodes that problem as a boolean assertion**. "Does the walker produce the right answer for our hardest known prompt?" is too vague; "does this specific edge exist in this specific shape?" is executable.

### Mapping to mempalace-rust

`mempalace` has a parallel opportunity here. The README mentions that `LOCI.md` is organized into seven locus slots with Live/Thin/Inapplicable verdicts. A validation step could assert: for a known test project with a known shape, each slot produces a specific expected verdict. If a refactor accidentally demotes a slot from Live to Thin (because a query pattern changed), the test catches it.

The general principle: **encode the project's hardest known failure mode as a unit test** so future changes can't silently regress it.

---

## Snapshot manifest for drift detection

The seed script writes a snapshot manifest into the validation report:

```json
{
  "soy_path": "/path/to/source.db",
  "soy_mtime": "2026-04-11T18:19:40.572712",
  "soy_size_bytes": 6148096
}
```

Purpose: when a benchmark is re-run weeks later, the manifest records exactly which V1 snapshot the V2 database was built from. If the source has drifted since (new rows added, old rows modified), subsequent benchmark comparisons carry a "drift note" — "this benchmark was built on V1 at mtime X; current V1 mtime is Y, so an apples-to-apples re-comparison requires a re-seed."

It's a cheap way to avoid the "I'm comparing old results against new results but the baseline has silently moved" class of bug.

### Known weakness

The manifest catches "the source mtime moved" but not "the source was rewritten to look the same" — a git history rewrite, a restored backup, or a silent re-import would produce an identical manifest with different underlying content. A content hash per row (blake3 over a canonical serialization) would catch this but adds cost. Deferred as an open question (see `findings.md` unresolved thread 1).

### Mapping to mempalace-rust

`mempalace-rust` has `run-log.json` + `[mempalace-checkpoint]` commit prefixes doing similar work against a git-backed source. The git-backed version is stronger because commit SHAs are content-addressed by construction, so a history rewrite would produce a different SHA and the run-log would flag the mismatch. Our SQLite source doesn't have that property natively, which is why a row-level content hash is still an open question on our side.

---

## What it cost, what it unlocked

Honest numbers from the rewrite:

### Cost

- **~900 lines of Python** for the seed script (`seed_next_soy.py`), stdlib only
- **~550 lines of SQL** for the DDL (`001_core.sql`)
- **~660 lines of Python** for the walker rewrite (`loci_v2.py`), also stdlib only
- **~2 weeks of design** before a line of implementation code, including two "architecture panel" sessions with adversarial review
- **~1 day of implementation** once the design was locked

The implementation was fast because the design was slow. Most of the friction was in the schema decisions — which tables, which edge types, how to organize the validation checks — not the code.

### Unlocked

- **The cross-entity framing question** (see `findings.md` Finding 1) became answerable. The specific benchmark prompt that had failed at every model tier in V1 now produces a coherent episode card at the top of the loci brief, with both target projects named as protagonists and the shared framing concept made explicit.
- **~800 lines of per-type expander code deleted** and replaced with ~40 lines of two generic functions (see `findings.md` Finding 3). The walker is now schema-evolution-friendly: adding an entity type is a constant map update, adding an edge type is a data change.
- **~72% of contact rows filtered out of walks** via the status enum (see `findings.md` Finding 2), with measurable recall improvement on benchmark prompts that need to reach many hops from a single seed.
- **Migration reproducibility** — the seed script is idempotent and regenerates `next_soy.db` deterministically from a V1 snapshot. Re-running against the same V1 mtime produces byte-identical output (modulo timestamps in `created_at` columns, which are the only variance source).
- **Ablation capability** — we can now run benchmarks against V1, V2-schema-only (no new data), and V2-full (schema + audit-driven additions) independently, and attribute deltas to schema effect vs data effect.

### What we still don't know

- **Does the schema effect dominate the data effect, or vice versa?** The ablation benchmarks haven't been run yet. `findings.md` unresolved thread 1 captures this.
- **Is `edge_salience` worth building?** We deferred it explicitly so we could measure the uniform-weight baseline first. We'll know after the ablation run.
- **Does auto-clustering for episodes ever pay off?** Hand-authoring was the right V1 call; whether V2 should invest in auto-clustering is downstream of the ablation data.

---

## TL;DR for a Claude that needs to apply this

If you're reading this as context for a parallel migration:

1. **Prefer parallel DB over in-place** when benchmark comparability matters.
2. **One `001_core.sql` file** is fine for a snapshot schema; migration chains only start paying off once the schema is live-evolving and being held up by production constraints.
3. **The hard step is parsing legacy TEXT columns into typed rows** (step 4 above). Write the parser once, reuse across columns, name-fallback matters, unresolved cases must be logged not guessed.
4. **Three-tier edge priority** (structural / network / conceptual) keeps breadth budgets sensible during walks.
5. **No CHECK constraint on `edge_type`** — adding types is a data change, not a migration. Validation happens in code.
6. **Encode your hardest known failure mode as an acceptance test** in the validation suite (check 9 pattern above).
7. **Deterministic-only during migration** — no LLMs in the backfill path, full stop. Logging over guessing.
8. **Snapshot manifest** the source at seed time so future benchmark comparisons can detect drift.
9. **Hand-author the hard things** (episodes, alias shortforms) in V1. Auto-derive in V2 after you have uniform-weight baselines to beat.
