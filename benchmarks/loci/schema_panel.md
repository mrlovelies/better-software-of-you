# Data Schema Architecture Panel — Loci V2

Three practitioners were handed the four-tier benchmark synthesis, the existing constructive panel, the current `shared/loci.py`, and migrations 001-008 + 014, and asked: given what we've learned about where loci succeeds and fails, what should the DATA LAYER beneath the retrieval look like? Not a re-litigation of the benchmark and not a duplicate of Aisha's render rewrite, Takeshi's community walk, or Lena's briefing cache — concrete schema evolution.

## Sam Okafor — Normalize Aggressively, Measure Everything

**Philosophy:** A personal-data graph should have exactly one way to express "A is related to B" at the schema level, and that one way should be a typed junction table. The 40 lines of `_parse_id_list` and `_name_in_linked_field` in loci.py aren't a traversal bug; they're a symptom of a schema with four incompatible ways of saying "this note is linked to that project." The fix isn't cleverer parsing, it's killing the polymorphic text columns outright. SQLite scales fine to hundreds of thousands of edge rows; what it doesn't scale to is ambiguous schemas where every reader reinvents link resolution.

**Proposal 1: `entity_edges` — one junction table to rule them all**

```sql
CREATE TABLE entity_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_type TEXT NOT NULL,            -- 'contact', 'project', 'standalone_note', ...
    src_id INTEGER NOT NULL,
    dst_type TEXT NOT NULL,
    dst_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,           -- 'mentions', 'client_of', 'supersedes', 'shares_framing', ...
    weight REAL DEFAULT 1.0,           -- 0-1, optional; default 1 for categorical edges
    established_at TEXT,               -- when this connection started (nullable)
    ended_at TEXT,                     -- when it stopped (nullable; NULL = active)
    source TEXT NOT NULL DEFAULT 'manual',  -- 'manual', 'auto_extract', 'import', 'merge'
    metadata TEXT,                     -- JSON blob for edge-type-specific fields
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (src_type, src_id, dst_type, dst_id, edge_type)
);

CREATE INDEX idx_edges_src ON entity_edges(src_type, src_id, edge_type);
CREATE INDEX idx_edges_dst ON entity_edges(dst_type, dst_id, edge_type);
CREATE INDEX idx_edges_type ON entity_edges(edge_type);
CREATE INDEX idx_edges_active ON entity_edges(src_type, src_id) WHERE ended_at IS NULL;
```

Addresses: #1 (replaces `linked_projects`/`linked_contacts` with real edges), #3 (calendar_events get edges instead of a perpetually-NULL text column), #6 (edge_type, weight, established_at are the edge metadata), #11 (cross-project "shares_framing" becomes a first-class edge).

Backfill parses the legacy `linked_*` columns and inserts edges; old columns are deprecated but kept write-through for one release. The loci expanders collapse from 10+ table-specific functions to two generic ones — `_expand_outbound(src)` and `_expand_inbound(dst)` — each a single query.

**Proposal 2: `contact_identities` — canonical identity with aliases**

```sql
CREATE TABLE contact_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    identity_type TEXT NOT NULL CHECK (identity_type IN ('email', 'phone', 'linkedin', 'alias_name', 'external_id')),
    identity_value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    first_seen TEXT,
    last_seen TEXT,
    UNIQUE (identity_type, identity_value)
);

CREATE INDEX idx_identities_canonical ON contact_identities(canonical_contact_id);
CREATE INDEX idx_identities_value ON contact_identities(identity_type, identity_value);

-- Also: add canonical_contact_id to contacts itself for soft-merge
ALTER TABLE contacts ADD COLUMN merged_into_id INTEGER REFERENCES contacts(id);
ALTER TABLE contacts ADD COLUMN merged_at TEXT;
```

Addresses: #2 — the James Andrews duplicate. Duplicate gets `merged_into_id` set and stays for history; every email/interaction/edge is re-homed to the canonical id. Writers check `contact_identities` first when resolving an incoming address, so new duplicates can't be created.

**Proposal 3: `schema_invariants` — a machine-checkable data quality table**

```sql
CREATE TABLE schema_invariants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invariant_name TEXT NOT NULL,
    check_sql TEXT NOT NULL,
    expected_result TEXT NOT NULL DEFAULT '0',  -- usually 0 rows = healthy
    last_run_at TEXT,
    last_result TEXT,
    last_status TEXT CHECK (last_status IN ('pass', 'fail', 'error')),
    severity TEXT DEFAULT 'warn' CHECK (severity IN ('info', 'warn', 'error'))
);

-- seed rows:
-- 'no_duplicate_contact_emails': SELECT COUNT(*) FROM (SELECT email FROM contacts WHERE email IS NOT NULL GROUP BY email HAVING COUNT(*) > 1)
-- 'calendar_events_have_edges': SELECT COUNT(*) FROM calendar_events ce WHERE NOT EXISTS (SELECT 1 FROM entity_edges WHERE src_type='calendar_event' AND src_id=ce.id)
-- 'standalone_notes_linked_projects_parseable': SELECT COUNT(*) FROM standalone_notes WHERE linked_projects IS NOT NULL AND json_valid(linked_projects) = 0
-- 'entity_tags_coverage_contacts': SELECT COUNT(*) FROM contacts WHERE id NOT IN (SELECT entity_id FROM entity_tags WHERE entity_type='contact')
```

Addresses: #1, #2, #3, #4, #5 — not by fixing them, but by making them visible. A nightly cron runs every invariant and surfaces failures to a `v_data_health` view. You can't normalize a schema you can't measure.

**What I'd ship first:** `entity_edges`. Everything else presumes it exists. Once edges are real rows you can index and query, loci becomes a 100-line traversal and the `linked_*` disaster stops growing.

**What I would NOT do:** Do NOT split `entity_tags` into `contact_tags`, `project_tags`, etc. Tagging behavior is the same for every entity — this is exactly the case polymorphism was designed for. The fix for #5 isn't denormalization, it's coverage: tag more things. Splitting adds four migrations and four writer paths while solving zero data problems. Add a CHECK constraint on `entity_type` if you want type safety; don't split the table.

## Priya Nair — The Schema Is the Memory Model

**Philosophy:** If the hypothesis is that associative retrieval beats flat search, the schema has to encode what makes biological associative memory work — and that isn't foreign keys. Real memory has weights, valence, temporal layering, and active-vs-dormant states. The current SoY schema has one kind of edge (FK presence) with none of those. Walking from Jessica to a 2024 decision looks identical to walking from Jessica to yesterday's email. That's a graph of tables, not a graph of memory. The additions I want let a walker distinguish "this connection is vivid and current" from "this connection is technically present in the database."

**Proposal 1: `edge_salience` — a sidecar weighting table for the edge graph**

```sql
CREATE TABLE edge_salience (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id INTEGER NOT NULL REFERENCES entity_edges(id) ON DELETE CASCADE,
    -- Associative strength signals
    recency_score REAL,           -- 0-1, decays with time since last reinforcement
    frequency_score REAL,         -- 0-1, how often the pair co-occurs in the data
    emotional_valence REAL,       -- -1 to 1, derived from sentiment of linking contexts
    emotional_intensity REAL,     -- 0-1, how charged (independent of valence)
    user_pinned INTEGER DEFAULT 0,-- boolean, user explicitly marked this important
    -- Decay and refresh
    last_reinforced_at TEXT,
    reinforcement_count INTEGER DEFAULT 1,
    computed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_salience_edge ON edge_salience(edge_id);
CREATE INDEX idx_salience_pinned ON edge_salience(user_pinned) WHERE user_pinned = 1;
```

Addresses: #6 (edge metadata at the associative-memory layer), indirectly #9 (a walker can prefer high-recency-high-intensity edges to reconstruct "active threads"). Salience is a SIDECAR on Sam's `entity_edges`, not a replacement: edges encode "this connection exists," salience encodes "how loud is it right now." A nightly job recomputes from recency, co-occurrence in journals and notes, and linking-evidence sentiment. Loci prunes low-salience branches early and spends its 60-node budget on the neighborhood that feels alive.

**Proposal 2: `memory_episodes` — first-class temporal layering**

```sql
CREATE TABLE memory_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,                  -- "March 2024 VO push", "Jessica onboarding"
    summary TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,                        -- NULL = ongoing
    episode_type TEXT CHECK (episode_type IN ('project_phase', 'relationship_phase', 'life_event', 'user_defined')),
    emotional_tone TEXT,                  -- free text: "tense", "hopeful", "exhausted", "breakthrough"
    created_by TEXT DEFAULT 'user',       -- 'user' | 'auto_cluster'
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE episode_members (
    episode_id INTEGER NOT NULL REFERENCES memory_episodes(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    role TEXT,                            -- 'protagonist', 'witness', 'setting', 'artifact'
    added_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (episode_id, entity_type, entity_id)
);
```

Addresses: #7 (the cluster/community metadata the benchmark was missing), #10 (episodes have `started_at`/`ended_at`, so you can ask "what episodes was I in as of March 1"), #11 (Reprise and BATL Lane Command can both be members of an "Operator intelligence layer" episode). Critical distinction from Sam's `entity_edges`: episodes are *named associative contexts*, not pairwise links. Human memory doesn't store "BATL is related to Reprise"; it stores "I was in a period of thinking about operator intelligence, and both projects lived inside it."

**Proposal 3: `entity_temporal_state` — snapshot the graph's knowledge state**

```sql
CREATE TABLE entity_temporal_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    field TEXT NOT NULL,                 -- 'status', 'role', 'company', 'relationship_depth'
    value TEXT,
    valid_from TEXT NOT NULL,
    valid_to TEXT,                       -- NULL = current
    source_event TEXT,                   -- 'import', 'email_extraction', 'user_edit'
    source_ref TEXT                      -- optional FK-ish: 'email:123' or 'note:45'
);

CREATE INDEX idx_temporal_entity ON entity_temporal_state(entity_type, entity_id, field);
CREATE INDEX idx_temporal_asof ON entity_temporal_state(valid_from, valid_to);
```

Addresses: #10 exclusively. Bitemporal-lite: store not just the current value but when you *learned* it. You can ask "what did SoY know about Jessica as of March 1" without a full audit log. Only tracked fields where change-over-time matters — status, role, relationship depth, decision outcomes. A trigger writes a row whenever a tracked field changes.

**What I'd ship first:** `memory_episodes` + `episode_members`. C1 (Reprise↔BATL) is the most diagnostic failure in the benchmark, and it failed because there was no schema way to represent what the user actually remembers: a period of being interested in operator intelligence, inside which both projects live. Shippable independently of Sam's `entity_edges` refactor.

**What I would NOT do:** Do NOT auto-derive salience or emotional valence via an LLM call on every write. Salience should be computed deterministically from observable signals (recency, frequency, user pins, explicit emotional tags the user wrote themselves). "Just have Claude tag every note with a valence" makes the data unrepeatable and benchmarks unreplayable. If you want LLM-derived emotion, put it in a separate advisory table the user can reject.

## Rachel Kwon — Design for How People Actually Write

**Philosophy:** Every personal-data schema I've seen that was designed like an enterprise CRM eventually broke against the user's actual writing habits. People don't write "contact records with linked interactions"; they write daily notes that mention names. They tag once and stop. A schema that rewards sloppy writing and does the linking *for* the user will stay alive. A schema that requires populating `entity_tags` for every new contact will sit at 8% coverage in six months — which is exactly what #5 shows. The current schema treats journal entries and standalone notes as second-class text columns the traversal barely walks. They should be first-class citizens loci walks *through*, not around. Writing is the substrate; the graph is the emergent structure.

**Proposal 1: `daily_logs` + `log_mentions` — the missing "what happened today" layer**

```sql
CREATE TABLE daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date TEXT NOT NULL,              -- YYYY-MM-DD, one per day
    content TEXT NOT NULL,               -- freeform, markdown OK
    mood TEXT,
    energy INTEGER,
    focus_area TEXT,                     -- what the user said they were working on
    auto_summary TEXT,                   -- nightly-computed one-line summary
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (log_date)
);

CREATE TABLE log_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL REFERENCES daily_logs(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    mention_text TEXT,                   -- the literal text that triggered the match
    char_start INTEGER,
    char_end INTEGER,
    confidence REAL DEFAULT 1.0,
    resolution_source TEXT               -- 'wikilink', 'name_match', 'user_confirmed', 'llm_extracted'
);

CREATE INDEX idx_log_mentions_log ON log_mentions(log_id);
CREATE INDEX idx_log_mentions_entity ON log_mentions(entity_type, entity_id);
CREATE INDEX idx_daily_logs_date ON daily_logs(log_date);
```

Addresses: #8 (the journal is empty because the current input path is too heavy; daily_logs is one row per day, freeform, linking done for you), #9 (the user's current focus lives in today's and yesterday's `focus_area` + mentions — that's what "active threads" actually is in practice). On write, scan content for `[[wikilinks]]` and known names, store resolved mentions with the literal trigger text. Loci can now walk "what have I been writing about this week" by querying recent daily_logs and pivoting through `log_mentions`. This is the primitive that makes P4 answerable.

**Proposal 2: `notes_v2` with typed and promoted content — fix `standalone_notes` properly**

```sql
CREATE TABLE notes_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT NOT NULL,
    note_kind TEXT NOT NULL DEFAULT 'freeform'
        CHECK (note_kind IN ('freeform', 'meeting', 'idea', 'decision_draft', 'brief', 'journal')),
    -- Replaces linked_contacts TEXT and linked_projects TEXT — links live in entity_edges now.
    -- Replaces tags TEXT — tags live in entity_tags (polymorphic).
    pinned INTEGER DEFAULT 0,
    promoted_to_type TEXT,               -- 'decision', 'project', 'follow_up' — when a note grew up
    promoted_to_id INTEGER,
    source TEXT DEFAULT 'manual',        -- 'manual', 'daily_log_extract', 'email_clip', 'voice_memo'
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Addresses: #1 and #4 by killing the TEXT-blob link/tag columns in favor of Sam's edge table and the existing `entity_tags`. The new piece is `promoted_to_type` / `promoted_to_id`: a note that grew into a decision keeps a pointer to it, so the walker can render "idea on March 12 → decision on March 15 → outcome on April 3" as a temporal chain. Migration is mechanical: copy rows, parse legacy `linked_*` into edges, leave `standalone_notes` as a read-only compat view for one release.

**Proposal 3: `wikilinks` — a first-class `[[entity]]` resolution table**

```sql
CREATE TABLE wikilinks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,                 -- "Jessica", "Grow App", "BATL"
    canonical_type TEXT NOT NULL,
    canonical_id INTEGER NOT NULL,
    is_primary INTEGER DEFAULT 0,        -- primary alias per entity
    confidence REAL DEFAULT 1.0,
    created_by TEXT DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (alias, canonical_type, canonical_id)
);

CREATE INDEX idx_wikilinks_alias ON wikilinks(alias);
CREATE INDEX idx_wikilinks_entity ON wikilinks(canonical_type, canonical_id);
```

Addresses: #2 (James Andrews → single canonical), #5 (makes link-by-writing frictionless — no entity page visit needed). When the user types `[[Jessica]]` in a daily log, the writer consults `wikilinks`, finds the canonical entity, and inserts the right `log_mentions`/`entity_edges` row. The user never thinks about IDs. The schema rewards the behavior (writing people's names) you want to reinforce (building the graph).

**What I'd ship first:** `daily_logs` + `log_mentions`. Everything else is cleanup of things that already exist in some form; daily_logs is a missing primitive. The whole "fallen off radar" class of prompts has no home in the current schema. Make writing one sentence a day the easiest thing in the system, and the graph populates itself within a month.

**What I would NOT do:** Do NOT build an LLM auto-extractor that reads daily_logs and populates `entity_edges` on write. I've watched this pattern burn users in three PKM products: the extractor gets it 80% right, the user trusts it, the 20% wrong cases accumulate silently, and six months later the graph is full of weakly-hallucinated edges indistinguishable from real ones. Use deterministic wikilink resolution for the high-confidence path, leave ambiguous mentions as *suggestions* in a review queue. Add a `mention_status` column: `resolved | suggested | rejected`. Never let auto-extraction write directly to `entity_edges`.

## Cross-persona synthesis

All three panelists would sign off on the same three-proposal core: **Sam's `entity_edges`** (the foundation everyone else's proposals assume), **Priya's `memory_episodes` + `episode_members`** (the only proposal that directly addresses the C1/Reprise-BATL failure and the "active threads" gap in one move), and **Rachel's `daily_logs` + `log_mentions`** (the input-side primitive that keeps the rest of the schema from starving). Together they address problems #1, #3, #6, #7, #8, #9, #11 and most of #5 and #10. The biggest disagreement is polymorphic-vs-dedicated junctions: Rachel wants dedicated tables where behavior differs (wikilinks separate from entity_tags), Sam wants one edge table discriminated by `edge_type`, and Priya wouldn't touch `entity_tags` at all — she'd rather a half-tagged graph with good salience than a fully-tagged graph without it. On LLM-in-the-loop the split is cleaner: Priya and Rachel insist auto-extraction must never write directly to the edge table (suggestion queue only); Sam is agnostic as long as writes are observable via `schema_invariants`. Recommended implementation order: (1) `entity_edges` first with backfill from polymorphic columns; (2) `daily_logs` + `log_mentions` + `wikilinks` as a three-table unit unlocking the "write → graph" feedback loop; (3) `memory_episodes` + `episode_members`, backfillable from edges once they exist; (4) `contact_identities` and `schema_invariants` as cleanup; (5) defer `edge_salience` and `entity_temporal_state` — biggest conceptual leaps, not on the critical path for any specific benchmark failure. On migrate-in-place vs parallel DB, all three converge on **build it in a parallel `next_soy.db` first, leveraging the fake-SoY database the user is already planning**. Sam's reason: migration safety — backfilling edges from three legacy formats with live data underneath invites silent loss. Priya's reason: benchmark replayability — if the live schema evolves, the 204 existing data points are no longer apples-to-apples; a parallel schema lets you measure schema-delta separately from retrieval-delta. Rachel's reason: schema ergonomics only show up under real daily writing volume, which the fake DB can simulate at variety the live DB hasn't accumulated. Sequence: build in `next_soy.db`, benchmark against the same 17 prompts, compare to arm C on the live DB, then port back only after the delta confirms the schema changes are paying for themselves.
