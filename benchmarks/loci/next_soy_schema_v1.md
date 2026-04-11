# next_soy Schema v1 — DDL Document for Review

**Status:** Draft for review. Not yet applied, not yet seeded.
**Scope:** Concrete schema for `next_soy.db`, a parallel SQLite database for testing loci V2 against the schema evolution proposed by the schema architecture panel.
**Basis:** Synthesizes the three-proposal core from `schema_panel.md` (Sam's `entity_edges`, Priya's `memory_episodes`, Rachel's `daily_logs`) with the carry-over tables from real SoY needed for entity continuity.
**Relationship to real SoY:** Parallel, not replacement. Real `soy.db` is untouched. `next_soy.db` is a separate file at `data/next_soy/next_soy.db` (path TBD), populated from a seed script that reads real SoY's tables plus Gmail ingest per `email_gap_findings.md`.

---

## Scope and philosophy

This schema is designed to do three things simultaneously:

1. **Carry over the entity continuity** from real SoY so the 17 benchmark prompts still make sense (Jessica, Elana, James Andrews, the projects, etc. all need to exist as the same entities with the same semantics).
2. **Encode the three-proposal core** from the schema panel: `entity_edges` (Sam), `memory_episodes` + `episode_members` (Priya), `daily_logs` + `log_mentions` (Rachel), plus supporting tables (`contact_identities`, `wikilinks`, `notes_v2`).
3. **Honor the "unanimous don't" list** from the panel: no per-write LLM extraction into edges, no polymorphic-to-dedicated splits of `entity_tags`, no salience auto-derivation via LLM.

The schema is intentionally **smaller than the full schema panel's recommendations**. It includes only what's needed for a V1 benchmark re-run. Deferred items are listed at the end with the reasoning.

---

## Tables included in v1

| # | Table | Source | Purpose |
|---|---|---|---|
| 1 | `soy_meta` | Carry from real SoY, unchanged | Platform metadata |
| 2 | `contacts` | Carry from real SoY, unchanged | Canonical contact records |
| 3 | `contact_identities` | **NEW (Sam)** | Canonical identity + aliases (solves James Andrews dupe) |
| 4 | `projects` | Carry from real SoY, unchanged | Projects |
| 5 | `project_tasks` | Carry from real SoY (renamed from `tasks`) | Tasks under projects |
| 6 | `milestones` | Carry from real SoY, unchanged | Project milestones |
| 7 | `decisions` | Carry from real SoY, unchanged | Decision log |
| 8 | `journal_entries` | Carry from real SoY, unchanged | Mood/energy journal |
| 9 | `contact_interactions` | Carry from real SoY, unchanged | Logged interactions (email/call/meeting/etc.) |
| 10 | `emails` | Carry from real SoY, unchanged | Synced Gmail messages |
| 11 | `calendar_events` | Carry from real SoY, unchanged | Synced calendar events |
| 12 | `transcripts` + `transcript_participants` | Carry from real SoY, unchanged | Meeting transcripts |
| 13 | `commitments` | Carry from real SoY, unchanged | Extracted commitments |
| 14 | `tags` + `entity_tags` | Carry from real SoY, unchanged | Polymorphic tagging |
| 15 | `notes` (polymorphic) | Carry from real SoY, unchanged | Notes attached to specific entities |
| 16 | `notes_v2` | **NEW (Rachel)** | Replaces `standalone_notes` — no linked_* TEXT columns |
| 17 | `daily_logs` | **NEW (Rachel)** | One row per day of freeform writing |
| 18 | `log_mentions` | **NEW (Rachel)** | Resolved entity mentions in daily_logs |
| 19 | `wikilinks` | **NEW (Rachel)** | Alias resolution table |
| 20 | `memory_episodes` | **NEW (Priya)** | Named temporal-associative contexts |
| 21 | `episode_members` | **NEW (Priya)** | Entity membership in episodes |
| 22 | **`entity_edges`** | **NEW (Sam)** | **The load-bearing new table.** Typed junction edges replace all polymorphic link columns. |

Total: 22 tables, of which **8 are new**. Compared to real SoY's ~58 migrations, this is a focused subset chosen to support the benchmark re-run without pulling in modules that don't affect the loci results (auditions, signal harvester, learning module, etc.).

---

## What's explicitly NOT in v1 (deferred with reasons)

| Table | Proposed by | Reason for deferral |
|---|---|---|
| `edge_salience` | Priya | Requires recency/frequency/valence computation that we don't have a reliable source for yet. Deferred until we've measured whether the uniform-weight baseline already improves loci — if so, salience is an optimization, not a requirement. |
| `entity_temporal_state` | Priya | Bitemporal-lite "what did SoY know as of date X" is powerful but not tested by any current benchmark prompt. Add when a prompt specifically requires historical-state queries. |
| `schema_invariants` | Sam | Machine-checkable data quality checks are useful in production but overkill for a benchmark DB. Sam's point about visibility is valid; we'll manually check invariants during seed verification instead. |
| Dedicated per-type tag tables | (Rachel considered, Sam rejected) | All three panelists agreed: keep `entity_tags` polymorphic. Splitting adds migrations for zero data-quality improvement. |
| LLM-based auto-extraction into `entity_edges` | (warned against by all three) | Deterministic-only for V1. Edges come from either backfill (structural from real SoY) or explicit user action (wikilinks). |

---

## Table-by-table DDL (new tables only)

For the carry-over tables, refer to the real SoY migrations 001-008 — the DDL is unchanged. The new tables and one replacement follow.

### `entity_edges` — Sam's foundation

```sql
-- The load-bearing table. Replaces:
--   - standalone_notes.linked_contacts / linked_projects (3-format TEXT disaster)
--   - calendar_events.contact_ids (perpetually-NULL TEXT column)
--   - Implicit FK relationships we want to walk but can't query uniformly
--
-- Policy: every loci traversal consults this table. Expanders become two
-- generic functions: outbound (src_type, src_id) → rows, and inbound
-- (dst_type, dst_id) → rows. No per-entity-type expanders.

CREATE TABLE IF NOT EXISTS entity_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_type TEXT NOT NULL,
    src_id INTEGER NOT NULL,
    dst_type TEXT NOT NULL,
    dst_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0
        CHECK (weight >= 0.0 AND weight <= 1.0),
    established_at TEXT,                -- nullable: when the edge started
    ended_at TEXT,                      -- nullable: NULL = active, datetime = ended
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'backfill', 'wikilink', 'import', 'merge', 'user_pin')),
    metadata TEXT,                      -- JSON; edge-type-specific
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (src_type, src_id, dst_type, dst_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_src
    ON entity_edges(src_type, src_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_dst
    ON entity_edges(dst_type, dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_type
    ON entity_edges(edge_type);
-- Partial index for active edges only (most queries want active)
CREATE INDEX IF NOT EXISTS idx_edges_active
    ON entity_edges(src_type, src_id) WHERE ended_at IS NULL;
```

### `contact_identities` — Sam's identity + alias layer

```sql
-- Canonical identity tracking. Solves the James Andrews duplicate.
-- Every incoming email address, phone, linkedin handle, or external
-- identifier resolves to exactly one canonical contact.

CREATE TABLE IF NOT EXISTS contact_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_contact_id INTEGER NOT NULL
        REFERENCES contacts(id) ON DELETE CASCADE,
    identity_type TEXT NOT NULL
        CHECK (identity_type IN (
            'email', 'phone', 'linkedin', 'github_handle',
            'discord_handle', 'alias_name', 'external_id'
        )),
    identity_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    first_seen TEXT,
    last_seen TEXT,
    verified INTEGER NOT NULL DEFAULT 0,    -- 1 if user confirmed
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (identity_type, identity_value)
);

CREATE INDEX IF NOT EXISTS idx_identities_canonical
    ON contact_identities(canonical_contact_id);
CREATE INDEX IF NOT EXISTS idx_identities_value
    ON contact_identities(identity_type, identity_value);

-- Soft merge support on contacts itself
ALTER TABLE contacts ADD COLUMN merged_into_id INTEGER
    REFERENCES contacts(id);
ALTER TABLE contacts ADD COLUMN merged_at TEXT;
```

### `notes_v2` — Rachel's replacement for `standalone_notes`

```sql
-- Replaces standalone_notes with a cleaner model:
--   - No linked_contacts / linked_projects / tags TEXT columns
--     (those move to entity_edges and entity_tags respectively)
--   - Typed note kinds so loci can render differently per kind
--   - promoted_to_type/id for tracking when a note grew into a decision/
--     project/follow-up — loci can render "idea → decision → outcome" chains

CREATE TABLE IF NOT EXISTS notes_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT NOT NULL,
    note_kind TEXT NOT NULL DEFAULT 'freeform'
        CHECK (note_kind IN (
            'freeform', 'meeting', 'idea', 'decision_draft',
            'brief', 'observation', 'reference'
        )),
    pinned INTEGER NOT NULL DEFAULT 0,
    -- Promotion: when a note grew into a first-class entity
    promoted_to_type TEXT
        CHECK (promoted_to_type IN (
            NULL, 'decision', 'project', 'follow_up', 'task', 'milestone'
        )),
    promoted_to_id INTEGER,
    -- Origin tracking
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN (
            'manual', 'daily_log_extract', 'email_clip',
            'voice_memo', 'import', 'migrated_from_standalone'
        )),
    source_ref TEXT,                    -- 'daily_log:123', 'email:456', etc.
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notes_v2_kind ON notes_v2(note_kind);
CREATE INDEX IF NOT EXISTS idx_notes_v2_pinned ON notes_v2(pinned) WHERE pinned = 1;
CREATE INDEX IF NOT EXISTS idx_notes_v2_promoted
    ON notes_v2(promoted_to_type, promoted_to_id)
    WHERE promoted_to_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notes_v2_created ON notes_v2(created_at);
```

### `daily_logs` + `log_mentions` — Rachel's input layer

```sql
-- One row per calendar day of freeform writing. The primary input surface
-- that the rest of the graph populates from. Replaces the empty journal.
--
-- Goal: make writing one sentence a day the easiest thing in the system.
-- On write, scan content for [[wikilinks]] and known names, populate
-- log_mentions. Loci walks "what have I been writing about this week"
-- by querying recent daily_logs and pivoting through log_mentions.

CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date TEXT NOT NULL UNIQUE,       -- YYYY-MM-DD, exactly one per day
    content TEXT NOT NULL,               -- freeform, markdown, [[wikilinks]] OK
    mood TEXT,                           -- optional free text
    energy INTEGER CHECK (energy BETWEEN 1 AND 5),
    focus_area TEXT,                     -- what the user said they were working on
    auto_summary TEXT,                   -- nightly-computed one-line summary
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_daily_logs_date ON daily_logs(log_date);

CREATE TABLE IF NOT EXISTS log_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL REFERENCES daily_logs(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    mention_text TEXT,                   -- the literal text that triggered
    char_start INTEGER,
    char_end INTEGER,
    confidence REAL NOT NULL DEFAULT 1.0,
    mention_status TEXT NOT NULL DEFAULT 'resolved'
        CHECK (mention_status IN ('resolved', 'suggested', 'rejected')),
    resolution_source TEXT NOT NULL
        CHECK (resolution_source IN (
            'wikilink', 'name_match', 'user_confirmed', 'llm_suggested'
        )),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_log_mentions_log ON log_mentions(log_id);
CREATE INDEX IF NOT EXISTS idx_log_mentions_entity
    ON log_mentions(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_log_mentions_status
    ON log_mentions(mention_status) WHERE mention_status != 'resolved';
```

**Note on `mention_status`:** Rachel was explicit — LLM-suggested mentions go in a review queue, not directly into resolved state. Only `wikilink`, `name_match`, and `user_confirmed` paths write `resolved`. The `llm_suggested` path writes `suggested` and waits for user action.

### `wikilinks` — Rachel's alias resolution

```sql
-- First-class alias resolution for [[entity]] syntax in daily_logs and notes.
-- When user types [[Jessica]], the writer consults wikilinks, finds the
-- canonical entity, and inserts the right log_mentions and entity_edges rows.
-- User never thinks about IDs.

CREATE TABLE IF NOT EXISTS wikilinks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,                 -- "Jessica", "Grow App", "BATL"
    canonical_type TEXT NOT NULL,
    canonical_id INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,    -- primary alias per canonical entity
    confidence REAL NOT NULL DEFAULT 1.0,
    created_by TEXT NOT NULL DEFAULT 'user'
        CHECK (created_by IN ('user', 'auto_name_match', 'import')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (alias, canonical_type, canonical_id)
);

CREATE INDEX IF NOT EXISTS idx_wikilinks_alias ON wikilinks(alias);
CREATE INDEX IF NOT EXISTS idx_wikilinks_entity
    ON wikilinks(canonical_type, canonical_id);
```

### `memory_episodes` + `episode_members` — Priya's temporal-associative contexts

```sql
-- Named periods of associative context. Humans don't store "BATL is
-- related to Reprise"; they store "I was in a period of thinking about
-- operator intelligence, and both projects lived inside it." Episodes
-- encode that period-of-thinking layer.
--
-- Benchmark target: C1 (Reprise ↔ BATL "private intelligence layer"
-- framing) failed at every model tier because the shared framing lives
-- only in prose. With episodes, it becomes queryable: both projects are
-- episode_members of an "operator intelligence layer" episode.

CREATE TABLE IF NOT EXISTS memory_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,                  -- "March 2026 VO push", "Operator intelligence layer"
    summary TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,                        -- NULL = ongoing
    episode_type TEXT
        CHECK (episode_type IN (
            'project_phase', 'relationship_phase', 'life_event',
            'conceptual_thread', 'user_defined'
        )),
    emotional_tone TEXT,                  -- free text, user-provided
    created_by TEXT NOT NULL DEFAULT 'user'
        CHECK (created_by IN ('user', 'auto_cluster', 'import')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_episodes_active
    ON memory_episodes(started_at) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_type ON memory_episodes(episode_type);

CREATE TABLE IF NOT EXISTS episode_members (
    episode_id INTEGER NOT NULL
        REFERENCES memory_episodes(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    role TEXT,                            -- 'protagonist', 'witness', 'setting', 'artifact'
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (episode_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_episode_members_entity
    ON episode_members(entity_type, entity_id);
```

---

## The canonical `edge_type` enum for v1

This is load-bearing. Loci's traversal effectively becomes "walk outward from a seed via edge_type, ranked by weight and recency." The list of edge types determines what the walk can surface.

### Structural edges (carry over from real SoY's FK relationships)

| `edge_type` | Direction | Source FK (real SoY) |
|---|---|---|
| `client_of` | `project → contact` | `projects.client_id` |
| `decided_in` | `decision → project` | `decisions.project_id` |
| `involves_contact` | `decision → contact` | `decisions.contact_id` |
| `belongs_to_project` | `task → project` | `tasks.project_id` |
| `belongs_to_project` | `milestone → project` | `milestones.project_id` |
| `email_with` | `email → contact` | `emails.contact_id` (direction in metadata) |
| `interaction_with` | `contact_interaction → contact` | `contact_interactions.contact_id` |
| `event_with` | `calendar_event → contact` | `calendar_events.contact_ids` (one edge per contact in the list) |
| `participated_in` | `transcript → contact` | `transcript_participants.contact_id` |
| `commitment_by` | `commitment → contact` | `commitments.owner_contact_id` |

### Note/content edges (new)

| `edge_type` | Direction | Notes |
|---|---|---|
| `mentions` | `notes_v2 → *` | Note mentions any entity (was `linked_contacts`/`linked_projects`) |
| `mentions` | `daily_log → *` | Daily log mentions any entity (via `log_mentions`) |
| `promoted_to` | `note → decision` | Note grew into a decision |
| `promoted_to` | `note → project` | Note grew into a project |
| `promoted_to` | `note → task` | Note grew into a task |

### Professional network edges (new, from email gap findings)

| `edge_type` | Direction | Example |
|---|---|---|
| `employed_by` | `contact → contact` | Alex → BATL (BATL as company contact) |
| `employer_of` | `contact → contact` | BATL → Alex (inverse; also derivable) |
| `agent_of` | `contact → contact` | Alison Little → Elana (Alison represents Elana) |
| `represented_by` | `contact → contact` | Elana → Alison (inverse) |
| `colleague_of` | `contact → contact` | Alex ↔ Jon McLaren (ACTRA) |
| `collaborator_on` | `contact → project` | Kerry → AloneinaBar / SoY |
| `family_of` | `contact → contact` | Alex ↔ James Somerville |
| `mentor_of` | `contact → contact` | James Andrews → Alex |

### Conceptual / associative edges (new, Priya's territory at the edge level)

| `edge_type` | Direction | Example |
|---|---|---|
| `shares_framing_with` | `project ↔ project` | Reprise ↔ BATL Lane Command ("operator intelligence layer") |
| `supersedes` | `decision → decision` | Paywall scope refined → supersedes original paywall decision |
| `derived_from` | `project → project` | "Specsite" ← "better-websites" (AloneinaBar repo) |
| `part_of_episode` | `* → memory_episode` | Any entity can be tagged as part of an episode (alternate path to `episode_members`) |

### Rules for `edge_type` values

1. **Lowercase, snake_case.** No spaces, no capitalization.
2. **Verb phrases for asymmetric edges, symmetric single-word for symmetric.** `client_of` is asymmetric (project → contact); `colleague_of` is symmetric (either direction valid, recorded once with the alphabetically-lower src).
3. **Reciprocal edges are NOT auto-materialized.** A query that wants "all agents of Elana" queries for `dst_type='contact', dst_id=<elana>, edge_type='agent_of'`. The traversal code handles direction, not the schema.
4. **Adding a new edge_type is a schema-compatible change** — no migration needed, just a new `source='manual'` row. The enum is enforced by convention, not by CHECK constraint, because the constraint would require a migration for every new type.
5. **metadata JSON shape is edge-type-specific** and documented in a separate edge-type reference file (to be written alongside the seed script). For `mentions`: `{"char_start": int, "char_end": int, "mention_text": str}`. For `employed_by`: `{"role": str, "start_date": str, "end_date": str?}`. For `shares_framing_with`: `{"framing_concept": str, "evidence_sources": [str]}`.

---

## Backfill plan (real SoY → next_soy)

The seed script (to be written separately, see companion `next_soy_implementation_plan.md`) will perform these migrations:

### Step 1 — Copy carry-over tables

Straight SQL `INSERT INTO next_soy.<table> SELECT ... FROM soy.<table>` for:

- `soy_meta`
- `contacts` (plus the new `merged_into_id` / `merged_at` columns defaulting to NULL)
- `projects`
- `project_tasks` (rename from `tasks`)
- `milestones`
- `decisions`
- `journal_entries`
- `contact_interactions`
- `emails`
- `calendar_events`
- `transcripts` + `transcript_participants`
- `commitments`
- `tags` + `entity_tags`
- `notes` (polymorphic)

### Step 2 — Translate `standalone_notes` → `notes_v2`

```sql
INSERT INTO next_soy.notes_v2 (
    id, title, content, note_kind, pinned, source, created_at, updated_at
)
SELECT
    id,
    title,
    content,
    'freeform' AS note_kind,           -- all existing notes default to freeform
    pinned,
    'migrated_from_standalone' AS source,
    created_at,
    updated_at
FROM soy.standalone_notes;
```

The `linked_contacts`, `linked_projects`, and `tags` columns are dropped — their content is translated into `entity_edges` rows in step 3.

### Step 3 — Populate `entity_edges` from structural FKs

One insert per edge type. Example for `client_of`:

```sql
INSERT INTO next_soy.entity_edges (
    src_type, src_id, dst_type, dst_id, edge_type, source, metadata
)
SELECT
    'project', id, 'contact', client_id, 'client_of',
    'backfill',
    json_object('original_column', 'projects.client_id')
FROM soy.projects
WHERE client_id IS NOT NULL;
```

Similar inserts for every FK in the canonical enum (section above). Each insert tagged with `source='backfill'` and metadata pointing at the original column.

### Step 4 — Parse legacy `linked_*` columns into `mentions` edges

The hard one. `standalone_notes.linked_projects` has three formats (JSON array, bare numeric string, project name string). The seed script does:

1. For each row in `soy.standalone_notes`:
   - Parse `linked_contacts` with the existing `_parse_id_list` logic → list of contact IDs → one `entity_edges (src_type='notes_v2', src_id=new_note_id, dst_type='contact', dst_id=cid, edge_type='mentions', source='backfill')` per ID.
   - Parse `linked_projects` with `_parse_id_list` + name fallback → one edge per project.
   - Parse `tags` → one `entity_tags(entity_type='notes_v2', entity_id=new_note_id, tag_id=...)` row per tag. (Tag coverage is preserved as-is.)

Unresolved `linked_projects` (e.g., a project name that doesn't match any project) get logged to a `seed_unresolved.log` file. The seed script does NOT auto-create projects for these; it flags them for user review.

### Step 5 — Populate `contact_identities` from existing contact emails

```sql
INSERT INTO next_soy.contact_identities (
    canonical_contact_id, identity_type, identity_value,
    confidence, verified, source
)
SELECT id, 'email', email, 1.0, 1, 'backfill'
FROM soy.contacts
WHERE email IS NOT NULL AND email != '';
```

### Step 6 — Resolve the James Andrews duplicate

Manual step in the seed script:

```sql
-- Pick id 7 (James Andrews Talent Services) as canonical since it has
-- the longer company name and fuller role description. id 9 becomes
-- the merged duplicate.
UPDATE next_soy.contacts
SET merged_into_id = 7, merged_at = datetime('now')
WHERE id = 9;

-- Move any edges pointing to id 9 onto id 7
UPDATE next_soy.entity_edges
SET dst_id = 7
WHERE dst_type = 'contact' AND dst_id = 9;

UPDATE next_soy.entity_edges
SET src_id = 7
WHERE src_type = 'contact' AND src_id = 9;
```

### Step 7 — Gmail ingest (per `email_gap_findings.md`)

For each of the gap entities identified in the email gap findings report:

1. Fetch the thread list from Gmail via MCP with date-filtered queries (no more resultSizeEstimate misreading).
2. For each thread, insert `emails` rows for each message (populating `contact_id` to the canonical contact).
3. For each thread, insert a `contact_interactions` row summarizing the thread (type='email', direction based on first inbound vs outbound, subject from thread subject, summary auto-generated from first 500 chars of content).
4. The summary generation is **deterministic** — no LLM. Just first-N-chars with ellipsis. The seed script can note this as a known limitation.
5. Per Rachel's warning: no auto-extraction of mentions from email bodies into `entity_edges`. Only the explicit contact relationship gets an edge (`email_with`). Conceptual connections ("this email is about the demo production") don't get mentioned-edges without user review.

### Step 8 — Seed the `memory_episodes` table with 2-3 explicit episodes

Hand-authored for V1 to directly target the C1 prompt and make the schema's capability visible:

```sql
INSERT INTO memory_episodes (title, summary, started_at, episode_type, emotional_tone)
VALUES (
    'Operator intelligence layer',
    'A period of thinking about private, owner-facing intelligence layers '
    'distinct from the public product. Both Reprise (competitive analysis, '
    'music-as-signal, API budget controls) and BATL Lane Command (private '
    'ops intelligence, daily metrics, revenue dashboards) fit this framing.',
    '2026-03-01',
    'conceptual_thread',
    'focused, experimental'
);

INSERT INTO episode_members (episode_id, entity_type, entity_id, role)
VALUES
    (last_insert_rowid(), 'project', <reprise_id>, 'protagonist'),
    (last_insert_rowid(), 'project', <batl_lane_command_id>, 'protagonist'),
    (last_insert_rowid(), 'notes_v2', <batl_ops_note_id>, 'artifact'),
    (last_insert_rowid(), 'notes_v2', <cadence_competitive_note_id>, 'artifact');
```

This is explicitly **seeded data, not derived data**. The panel warned against LLM-based episode clustering for V1. A human-authored episode against a known-relevant benchmark prompt is fair game; auto-clustering is deferred.

Other seed episodes to include (pending user confirmation when the seed script is written):
- "VO career 2026 push" — spanning James Andrews sessions, Elana's site, ACTRA Game Expo, us-vo-agent-pursuit
- "Axe throwing day job" — BATL HR, T4 tips thread, the ambient context around Alex's employment

### Step 9 — Populate `wikilinks` from known entity names

For every contact, project, and decision in `next_soy`, insert a primary alias row:

```sql
INSERT INTO wikilinks (alias, canonical_type, canonical_id, is_primary, confidence, created_by)
SELECT name, 'contact', id, 1, 1.0, 'import' FROM next_soy.contacts;

INSERT INTO wikilinks (alias, canonical_type, canonical_id, is_primary, confidence, created_by)
SELECT name, 'project', id, 1, 1.0, 'import' FROM next_soy.projects;
```

Plus hand-curated short-forms where they make sense:
- `Jessica` → `Jessica Martin` (contact id 1)
- `Grow App` → `The Grow App` (project id 1)
- `BATL` → `BATL Lane Command` (project id 2) — AND → `BATL Axe Throwing` (contact, once created as employer)
- `Kerry` → `Kerry Morrison` (contact id 6)
- `Elana` → `Elana Dunkelman` (contact id 8)
- `James` → ambiguous, flagged for user disambiguation at query time (James Andrews vs James Somerville)

---

## Seed strategy beyond backfill (what's synthetic vs real)

**All of the following will be REAL, sourced from either real SoY or Gmail:**

- All carry-over table contents (via direct copy)
- All structural edges (via FK translation)
- All wikilink primary aliases (from entity names)
- All contact identities from existing email fields
- The James Andrews merge
- Gmail ingest for the Elana, James Andrews, BATL HR, and CESD/Buchwald-adjacent gap entities

**The following will be HAND-AUTHORED (with user review required before committing):**

- The 2-3 seed `memory_episodes` entries
- The hand-curated wikilink short-forms
- Any new contacts for entities identified in `email_gap_findings.md` but not in real SoY yet (Alison Little, Jason Thomas, Myles Dobson, ACTRA colleagues, BATL HR as contact, Gerald Karaguni, Meghan Hoople, Tish Hicks, BATL Axe Throwing as a company-type contact for the employer relationship)

**The following will NOT be synthetic in V1:**

- `daily_logs` — empty at V1 seed time. The user either writes some themselves after the schema is live, or we extract them retroactively from journal_entries. Decision deferred to the implementation plan.
- `edge_salience` — deferred table, not populated.
- Any auto-extracted mentions from email bodies.

---

## Migration safety and validation

Before any downstream work (loci_v2.py against the new schema, benchmark re-run), the seed script must pass validation:

1. **Row count parity.** Every carry-over table in `next_soy` has the same row count as in real SoY (with the exception of `notes_v2` vs `standalone_notes` which should match, and `contacts` which has one extra row marked as merged).
2. **Edge count sanity.** `entity_edges` should have at least N rows where N = count of non-NULL FK values across all carry-over tables. Higher is expected (from parsed `linked_*` columns).
3. **No orphaned edges.** Every `src_type, src_id` and `dst_type, dst_id` in `entity_edges` must reference a row that exists in next_soy.
4. **Unresolved-link audit.** The `seed_unresolved.log` file from step 4 is inspected manually before the script is considered complete.
5. **Identity uniqueness.** `contact_identities(identity_type, identity_value)` has no duplicates.
6. **Wikilink ambiguity report.** For every alias that resolves to more than one canonical entity, log to `wikilinks_ambiguous.log` and require user review.

The seed script should produce a validation report at the end: `next_soy_seed_report_<timestamp>.md` with all of the above counts and any warnings.

---

## What happens after this DDL is reviewed

Assuming the schema is accepted as-is or with small modifications:

1. **Write the seed script** (`benchmarks/loci/seed_next_soy.py`) following the backfill plan above.
2. **Run the seed script** to produce `data/next_soy/next_soy.db`. Review the validation report.
3. **Write `loci_v2.py`** — a rewrite of the current `shared/loci.py` that queries `entity_edges` via two generic expanders instead of 10+ per-entity expanders. This is where Aisha's render rewrite from the constructive panel lands.
4. **Run the benchmark** with the same 17 prompts against `next_soy.db` using `loci_v2.py`. Two model tiers: Claude Opus and Qwen 14B (the two that matter for loci's value proposition).
5. **Compare** to the existing benchmark results (synthesis-four-tier.md). The delta tells us whether the schema + implementation changes are paying for themselves.

Priya's replayability argument from the schema panel is load-bearing here: we want the next_soy results to be **apples-to-apples comparable** to the existing 204-data-point benchmark. Same prompts, same judge configuration, same test models — only the context-assembly pipeline changes.

---

## Open questions for your review

These are things the DDL draft doesn't settle and will need your answer before the seed script is written:

1. **`daily_logs` seed strategy.** Empty at V1 and wait for real writing? Or retroactively construct a week of logs from other activity (recent emails, commits, notes) to exercise the mention-resolution path?
2. **Seed episodes list.** The three proposed ("operator intelligence layer," "VO career 2026 push," "axe throwing day job") are my guesses at what matters. You'd know better. Want to revise before I commit them to the seed script?
3. **New contact additions.** The email gap findings identified ~10 new contacts that should exist (Alison Little, Jason Thomas, ACTRA colleagues, BATL as employer, Gerald Karaguni). Do we add them all at seed time, or add them incrementally?
4. **`linked_projects` unresolved-name strategy.** If the backfill finds a note with `linked_projects = "Braska's Pilgrimage"` and Braska's Pilgrimage IS in real SoY, it resolves. If it says `linked_projects = "Something Weird"` and nothing matches, do we (a) log and skip, (b) create a stub project, or (c) prompt for resolution?
5. **Benchmark tier scope for re-run.** Do we re-run all four tiers (Mistral, Qwen 14B, Qwen3 30B, Claude) or just Claude + Qwen 14B (the tiers where loci mattered)? Cheaper is the latter; more complete is the former.
6. **Schema file location.** Should the DDL live at `data/next_soy/schema/001_next_soy_core.sql` or somewhere else? Follow real SoY's migration numbering convention, or start fresh?

None of these block committing this DDL doc for review. They block the seed script.

---

## Alignment with prior documents

This schema:

- ✓ Implements the three-proposal core from `schema_panel.md` (entity_edges, memory_episodes + episode_members, daily_logs + log_mentions)
- ✓ Honors the unanimous "parallel DB, not in-place migration" recommendation
- ✓ Defers edge_salience, entity_temporal_state, and schema_invariants as the panel suggested
- ✓ Respects the three "don't do this" warnings (no LLM-derived salience, no auto-extractor writing to edges, no entity_tags split)
- ✓ Incorporates `email_gap_findings.md` — ingest plan references the specific entities and thread counts identified there
- ✓ Supports the constructive panel's proposals without committing to any single one — Aisha's render rewrite can happen against this schema, Lena's briefing cache can live in a future `entity_summaries` table, Takeshi's community walk can use `memory_episodes` as the community analog

The schema does NOT:

- ✗ Solve the Qwen3 30B anomaly (that's a model-specific issue, not a schema issue)
- ✗ Directly implement any of Aisha's / Takeshi's / Lena's retrieval-layer proposals (that's loci_v2.py's job, which comes after this schema is seeded)
- ✗ Address the C1 prompt by itself — the `memory_episodes` table is necessary but not sufficient. Loci_v2 still has to walk from the prompt, find the relevant episode, and render it coherently. Schema enables the answer; retrieval layer produces it.
