#!/usr/bin/env python3
"""Seed next_soy.db from real SoY + audit-driven additions.

Builds the parallel benchmark database described in `next_soy_schema_v1.md`
(locked 2026-04-11). Reads real SoY in read-only mode, applies the v1 DDL
to a fresh file, copies carry-over tables, translates standalone_notes to
notes_v2, backfills entity_edges from structural FKs and parsed legacy
linked_* columns, adds the new contacts and edges identified in
`seed_contact_audit.md`, seeds four memory episodes, populates wikilinks,
validates, and writes a markdown report.

Gmail ingest is out of scope for v1 per impl plan risk 2 (run A first,
schema-only, so schema effect can be measured in isolation from data effect).

Usage:
    python3 seed_next_soy.py            # refuses if next_soy.db exists
    python3 seed_next_soy.py --force    # overwrite existing next_soy.db

Outputs:
    benchmarks/loci/next_soy_schema/next_soy.db
    benchmarks/loci/next_soy_schema/seed_unresolved.log
    benchmarks/loci/next_soy_schema/wikilinks_ambiguous.log
    benchmarks/loci/seed_reports/seed_<timestamp>.md
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = SCRIPT_DIR / "next_soy_schema"
DDL_PATH = SCHEMA_DIR / "001_core.sql"
OUT_DB = SCHEMA_DIR / "next_soy.db"
UNRESOLVED_LOG = SCHEMA_DIR / "seed_unresolved.log"
AMBIGUOUS_LOG = SCHEMA_DIR / "wikilinks_ambiguous.log"
REPORT_DIR = SCRIPT_DIR / "seed_reports"
REAL_SOY_DB = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"


# ---------------------------------------------------------------------------
# Carry-over table plan
# ---------------------------------------------------------------------------
#
# Each entry: (src_table_in_soy, dst_table_in_next_soy, columns).
# `columns` is the explicit column list used in both SELECT and INSERT — we
# list them rather than `*` so renamed/dropped columns fail loudly instead of
# silently shifting into the wrong slot. Order matters: dependents follow
# their parents so naive FK-ordered restores still work if FK enforcement
# is ever turned on.

CARRY_OVER_TABLES = [
    ("soy_meta", "soy_meta", ["key", "value", "updated_at"]),
    ("contacts", "contacts", [
        "id", "name", "email", "phone", "company", "role", "type",
        "status", "notes", "created_at", "updated_at",
    ]),
    ("projects", "projects", [
        "id", "name", "description", "client_id", "status", "priority",
        "start_date", "target_date", "completed_date", "workspace_path",
        "dev_port", "created_at", "updated_at",
    ]),
    # tasks → project_tasks (rename)
    ("tasks", "project_tasks", [
        "id", "project_id", "title", "description", "status", "priority",
        "assigned_to", "due_date", "completed_at", "sort_order",
        "created_at", "updated_at",
    ]),
    ("milestones", "milestones", [
        "id", "project_id", "name", "description", "target_date",
        "completed_date", "status", "created_at",
    ]),
    ("decisions", "decisions", [
        "id", "title", "context", "options_considered", "decision",
        "rationale", "outcome", "outcome_date", "status", "project_id",
        "contact_id", "decided_at", "confidence_level", "review_30_date",
        "review_90_date", "review_180_date", "process_quality",
        "outcome_quality", "within_control", "external_factors",
        "would_do_differently", "created_at", "updated_at",
    ]),
    ("journal_entries", "journal_entries", [
        "id", "content", "mood", "energy", "highlights", "entry_date",
        "linked_contacts", "linked_projects", "created_at", "updated_at",
    ]),
    ("contact_interactions", "contact_interactions", [
        "id", "contact_id", "type", "direction", "subject", "summary",
        "occurred_at", "created_at",
    ]),
    ("emails", "emails", [
        "id", "gmail_id", "thread_id", "contact_id", "direction",
        "from_address", "from_name", "to_addresses", "subject", "snippet",
        "body_preview", "labels", "is_read", "is_starred", "received_at",
        "account_id", "synced_at",
    ]),
    ("calendar_events", "calendar_events", [
        "id", "google_event_id", "calendar_id", "title", "description",
        "location", "start_time", "end_time", "all_day", "status",
        "attendees", "contact_ids", "project_id", "account_id", "synced_at",
    ]),
    ("transcripts", "transcripts", [
        "id", "title", "source", "raw_text", "summary", "duration_minutes",
        "occurred_at", "processed_at", "call_intelligence", "source_email_id",
        "source_calendar_event_id", "source_doc_id", "created_at", "updated_at",
    ]),
    ("transcript_participants", "transcript_participants", [
        "id", "transcript_id", "contact_id", "speaker_label", "is_user",
        "created_at",
    ]),
    ("commitments", "commitments", [
        "id", "transcript_id", "owner_contact_id", "is_user_commitment",
        "description", "deadline_mentioned", "deadline_date", "status",
        "linked_task_id", "linked_project_id", "completed_at", "created_at",
        "updated_at",
    ]),
    ("tags", "tags", ["id", "name", "color", "category"]),
    ("entity_tags", "entity_tags", ["entity_type", "entity_id", "tag_id"]),
    ("notes", "notes", ["id", "entity_type", "entity_id", "content", "created_at"]),
]


# ---------------------------------------------------------------------------
# Structural edge backfill plan
# ---------------------------------------------------------------------------
#
# Each entry describes one INSERT INTO entity_edges SELECT ... FROM <source>.
# `src_type` / `dst_type` use the next_soy entity-type naming convention
# (table name, lowered, no pluralization changes — matching how the schema
# doc writes them). When the source column is nullable, the WHERE clause
# filters to non-null rows so we never emit an edge pointing at id 0/NULL.

STRUCTURAL_EDGES = [
    # client_of: projects.client_id → contacts
    {
        "edge_type": "client_of",
        "src_type": "project", "src_col": "id",
        "dst_type": "contact", "dst_col": "client_id",
        "table": "projects", "where": "client_id IS NOT NULL",
        "origin_col": "projects.client_id",
    },
    # decided_in: decisions.project_id → projects
    {
        "edge_type": "decided_in",
        "src_type": "decision", "src_col": "id",
        "dst_type": "project", "dst_col": "project_id",
        "table": "decisions", "where": "project_id IS NOT NULL",
        "origin_col": "decisions.project_id",
    },
    # involves_contact: decisions.contact_id → contacts
    {
        "edge_type": "involves_contact",
        "src_type": "decision", "src_col": "id",
        "dst_type": "contact", "dst_col": "contact_id",
        "table": "decisions", "where": "contact_id IS NOT NULL",
        "origin_col": "decisions.contact_id",
    },
    # belongs_to_project: project_tasks.project_id → projects
    {
        "edge_type": "belongs_to_project",
        "src_type": "project_task", "src_col": "id",
        "dst_type": "project", "dst_col": "project_id",
        "table": "project_tasks", "where": "project_id IS NOT NULL",
        "origin_col": "project_tasks.project_id",
    },
    # belongs_to_project: milestones.project_id → projects
    {
        "edge_type": "belongs_to_project",
        "src_type": "milestone", "src_col": "id",
        "dst_type": "project", "dst_col": "project_id",
        "table": "milestones", "where": "project_id IS NOT NULL",
        "origin_col": "milestones.project_id",
    },
    # email_with: emails.contact_id → contacts
    {
        "edge_type": "email_with",
        "src_type": "email", "src_col": "id",
        "dst_type": "contact", "dst_col": "contact_id",
        "table": "emails", "where": "contact_id IS NOT NULL",
        "origin_col": "emails.contact_id",
    },
    # interaction_with: contact_interactions.contact_id → contacts
    {
        "edge_type": "interaction_with",
        "src_type": "contact_interaction", "src_col": "id",
        "dst_type": "contact", "dst_col": "contact_id",
        "table": "contact_interactions", "where": "contact_id IS NOT NULL",
        "origin_col": "contact_interactions.contact_id",
    },
    # participated_in: transcript_participants.contact_id → transcripts
    # (Modeled as "transcript participated by contact"; src is transcript.)
    {
        "edge_type": "participated_in",
        "src_type": "transcript", "src_col": "transcript_id",
        "dst_type": "contact", "dst_col": "contact_id",
        "table": "transcript_participants", "where": "contact_id IS NOT NULL",
        "origin_col": "transcript_participants.contact_id",
    },
    # commitment_by: commitments.owner_contact_id → contacts
    {
        "edge_type": "commitment_by",
        "src_type": "commitment", "src_col": "id",
        "dst_type": "contact", "dst_col": "owner_contact_id",
        "table": "commitments", "where": "owner_contact_id IS NOT NULL",
        "origin_col": "commitments.owner_contact_id",
    },
]


# ---------------------------------------------------------------------------
# Audit-driven seed data: new contacts, identity aliases, edges, episodes,
# wikilinks, status flips, Kerry promotion
# ---------------------------------------------------------------------------
#
# These are sourced directly from seed_contact_audit.md and the step 8/9
# sections of next_soy_schema_v1.md. They live inline (not in a JSON file)
# because a reviewer reading the seed script should see exactly what it's
# asserting about the data, not follow another file reference.

# key: stable handle used by NEW_EDGES, SEED_EPISODES, HAND_CURATED_WIKILINKS
# (so we don't depend on auto-assigned ids until after insert).
NEW_CONTACTS = [
    # Alex himself — the user. Not in real SoY as a contact, but needed as the
    # src for "Alex → <client>" edges. Stored in soy_meta as user_contact_id
    # so loci_v2 can resolve "the user" without hardcoding an id.
    {
        "key": "alex",
        "name": "Alex Somerville",
        "email": "j.alex.somerville@gmail.com",
        "phone": None,
        "company": None,
        "role": "user / self",
        "type": "individual",
        "status": "active",
        "notes": "The user. Voice actor, dev, Toronto. Primary SoY account holder.",
    },
    # Family (3 new, per Round 2 of the audit)
    {
        "key": "james_somerville",
        "name": "Jim Somerville",
        "email": "jamescsomerville@gmail.com",
        "phone": None,
        "company": None,
        "role": "father / advisor",
        "type": "individual",
        "status": "active",
        "notes": "Father. Lives in Portugal. Highest-volume personal correspondent; "
                 "cc-regular with Anna Lee, Cameron, Chris Graham, Ainslie. Owner of Dico.",
    },
    {
        "key": "cameron_somerville",
        "name": "Cameron Somerville",
        "email": "cameron.somerville@gmail.com",
        "phone": None,
        "company": None,
        "role": "family (brother)",
        "type": "individual",
        "status": "active",
        "notes": "Brother. Dico co-shareholder. Active in family threads.",
    },
    {
        "key": "ainslie_roberts",
        "name": "Ainslie Roberts",
        "email": "ainslieace1@aol.com",
        "phone": None,
        "company": None,
        "role": "family",
        "type": "individual",
        "status": "active",
        "notes": "Family, wellness-forward. In inner cc circle for estate planning threads.",
    },
    # VO professional network
    {
        "key": "ivan_sherry",
        "name": "Ivan Sherry",
        "email": "ivantoucan@yahoo.ca",
        "phone": None,
        "company": None,
        "role": "VO coach / site client",
        "type": "individual",
        "status": "active",
        "notes": "VO coach. Active coaching thread Mar 2026. Alex is building his site "
                 "(workspace: /wkspaces/ivan-sherry-site).",
    },
    {
        "key": "jon_mclaren",
        "name": "Jon McLaren",
        "email": "jonmclaren@me.com",
        "phone": None,
        "company": None,
        "role": "client / ACTRA peer",
        "type": "individual",
        "status": "active",
        "notes": "Voice actor, ACTRA Game Expo peer. Alex is building his site "
                 "(workspace: /wkspaces/jon-mclaren-vo).",
    },
    {
        "key": "craig_burnatowski",
        "name": "Craig Burnatowski",
        "email": "craigburnatowski@gmail.com",
        "phone": None,
        "company": None,
        "role": "client / ACTRA peer",
        "type": "individual",
        "status": "active",
        "notes": "Voice actor, ACTRA peer. Alex is building his site "
                 "(workspace: /wkspaces/craig-burnatowski-site).",
    },
    # Employment / neighborhood / advisory
    {
        "key": "batl_hr",
        "name": "Shauna (BATL HR)",
        "email": "hr@batlgrounds.com",
        "phone": None,
        "company": "BATL Axe Throwing",
        "role": "HR",
        "type": "individual",
        "status": "active",
        "notes": "BATL HR contact. Employment agreement, T4 tips, payroll threads.",
    },
    {
        "key": "gerald_karaguni",
        "name": "Gerald Karaguni",
        "email": "gerald.karaguni@gmail.com",
        "phone": None,
        "company": None,
        "role": "neighbor",
        "type": "individual",
        "status": "active",
        "notes": "Neighbor. Fence Project thread Feb–Apr 2026.",
    },
    {
        "key": "chris_graham",
        "name": "Chris Graham",
        "email": "CGraham@constellationhb.com",
        "phone": None,
        "company": "Constellation HB",
        "role": "advisor / Reprise business contact",
        "type": "individual",
        "status": "active",
        "notes": "President, Constellation HB. Early external Reprise touchpoint via "
                 "'Re: Reprise' thread (Mar 16–17 2026). Second identity: me@chrisg.ca.",
    },
    # Company-type contacts
    {
        "key": "batl_axe_throwing",
        "name": "BATL Axe Throwing",
        "email": None,
        "phone": None,
        "company": "BATL Axe Throwing",
        "role": "employer",
        "type": "company",
        "status": "active",
        "notes": "Alex's day-job employer.",
    },
    {
        "key": "dico",
        "name": "Dico",
        "email": None,
        "phone": None,
        "company": "Dico",
        "role": "holding company",
        "type": "company",
        "status": "active",
        "notes": "Jim Somerville's holding company. Alex and Cameron are shareholders.",
    },
    # Elana's agents — thin rows, exist only so represented_by edges have a target
    {
        "key": "alison_little",
        "name": "Alison Little",
        "email": None,
        "phone": None,
        "company": "(Elana's principal agent)",
        "role": "principal agent",
        "type": "individual",
        "status": "active",
        "notes": "Thin row. No direct correspondence — appears only in Elana's email "
                 "signature. Held here so Elana→represented_by edge has a target.",
    },
    {
        "key": "jason_thomas",
        "name": "Jason Thomas",
        "email": None,
        "phone": None,
        "company": "(Elana's voice agent)",
        "role": "voice agent",
        "type": "individual",
        "status": "active",
        "notes": "Thin row. Same pattern as Alison Little.",
    },
    # Broadcast-only: Tish Hicks (ACTRA Toronto stays as existing id 5, flipped below)
    {
        "key": "tish_hicks",
        "name": "Tish Hicks",
        "email": "tishhicks@thevodojo.com",
        "phone": None,
        "company": "The VO Dojo",
        "role": "promotional broadcaster",
        "type": "individual",
        "status": "broadcast_only",
        "notes": "Promotional newsletter blasts only. Held for orientation context.",
    },
]


# Additional contact_identities beyond the backfilled single-email-per-contact
# row. Kerry gets two; Chris Graham gets a second email.
# Each: (key_or_id, identity_type, identity_value, confidence, verified)
ADDITIONAL_IDENTITIES = [
    # Kerry promotion — the primary email gets backfilled from the update below,
    # but we also add the softwareof.you address.
    ("kerry_existing", "email", "kmo@betterstory.co", 1.0, 1),
    ("kerry_existing", "email", "kerry@softwareof.you", 0.9, 1),
    # Chris Graham — personal address beyond work
    ("chris_graham", "email", "me@chrisg.ca", 1.0, 1),
]


# Existing-contact status flips per schema doc.
# Each: (contact_id_in_real_soy, new_status, reason)
STATUS_FLIPS = [
    (5, "broadcast_only", "ACTRA Toronto — newsletter blasts only, zero two-way"),
    # The 8 cold talent-agency primary records + their sub-records
    (4, "prospect", "Ritter Talent — no email, aspirational reference"),
    (10, "prospect", "CESD — zero correspondence in 12mo"),
    (11, "prospect", "Billy Collura (CESD sub) — no correspondence"),
    (12, "prospect", "Marla Weber-Green (CESD sub) — no correspondence"),
    (13, "prospect", "Christian Sparks (CESD sub) — no correspondence"),
    (14, "prospect", "Atlas Talent — zero correspondence"),
    (15, "prospect", "Heather Dame (Atlas sub) — no correspondence"),
    (16, "prospect", "DDO Artists Agency — zero correspondence"),
    (17, "prospect", "Julie Gudz (DDO sub) — no correspondence"),
    (18, "prospect", "ACM Talent — zero correspondence"),
    (19, "prospect", "Melanie Thomas (ACM sub) — no correspondence"),
    (20, "prospect", "Stewart Talent — zero correspondence"),
    (21, "prospect", "Take 3 Talent — aspirational reference, no email"),
    (22, "prospect", "IDIOM — aspirational reference, no email"),
    (23, "prospect", "Avalon Artists Group — aspirational reference, no email"),
    (24, "prospect", "SBV Talent — zero correspondence"),
    (25, "prospect", "DPN Talent — aspirational reference, no email"),
    (26, "prospect", "Buchwald — zero correspondence"),
    (27, "prospect", "Pamela Goldman (Buchwald sub) — no correspondence"),
    (28, "prospect", "Katherine Ryan (Buchwald sub) — no correspondence"),
    (29, "prospect", "VOX Inc — aspirational reference, no email"),
    (30, "prospect", "Micaela Hicks (VOX sub) — no correspondence"),
    (31, "prospect", "AVO Talent — aspirational reference, no email"),
    (32, "prospect", "Innovative Artists — zero correspondence"),
]


# Kerry promotion — single in-place UPDATE on existing id 6.
KERRY_PROMOTION = {
    "id": 6,
    "email": "kmo@betterstory.co",
    "company": "Better Story",
    "role": "SoY collaborator / dev peer / close friend",
    "status": "active",
}


# Edges to materialize for new and promoted contacts. Each entry references
# keys in NEW_CONTACTS or uses "existing:<id>" for real-SoY contact ids.
# "project:<id>" likewise for existing projects.
#
# Each: (src, dst, edge_type, metadata_dict_or_None)
NEW_EDGES = [
    # building_site_for: Alex → four site clients
    ("alex", "elana_existing:8", "building_site_for", {"workspace": "/wkspaces/elana-dunkelman-vo"}),
    ("alex", "ivan_sherry", "building_site_for", {"workspace": "/wkspaces/ivan-sherry-site"}),
    ("alex", "jon_mclaren", "building_site_for", {"workspace": "/wkspaces/jon-mclaren-vo"}),
    ("alex", "craig_burnatowski", "building_site_for", {"workspace": "/wkspaces/craig-burnatowski-site"}),

    # close_friend_of: Alex ↔ Kerry
    ("alex", "existing:6", "close_friend_of", {"note": "tonal + historical; gates unfiltered banter context"}),

    # collaborator_on: Kerry → SoY (project id) + Kerry → Specsite (project id)
    ("existing:6", "project:206", "collaborator_on", {"role": "co-builder"}),

    # family_of: reciprocal family cluster (single-direction edges, loci walks both sides)
    ("alex", "james_somerville", "family_of", {"role": "father"}),
    ("alex", "cameron_somerville", "family_of", {"role": "brother"}),
    ("alex", "ainslie_roberts", "family_of", {"role": "family"}),
    ("james_somerville", "cameron_somerville", "family_of", {"role": "son"}),
    ("james_somerville", "ainslie_roberts", "family_of", {"role": "family"}),

    # cc_regular_of: the inner family-and-advisor cc circle
    ("james_somerville", "existing:3", "cc_regular_of", {"context": "accounting / family"}),
    ("james_somerville", "cameron_somerville", "cc_regular_of", None),
    ("james_somerville", "chris_graham", "cc_regular_of", None),
    ("james_somerville", "ainslie_roberts", "cc_regular_of", None),

    # mentor_of: James Andrews → Alex, Ivan Sherry → Alex
    ("existing:7", "alex", "mentor_of", {"domain": "VO demo production"}),
    ("ivan_sherry", "alex", "mentor_of", {"domain": "VO coaching"}),

    # colleague_of: ACTRA peers
    ("alex", "jon_mclaren", "colleague_of", {"context": "ACTRA"}),
    ("alex", "craig_burnatowski", "colleague_of", {"context": "ACTRA"}),

    # employment / works_at
    ("alex", "batl_axe_throwing", "works_at", {"role": "tipped employee"}),
    ("batl_hr", "batl_axe_throwing", "employed_by", {"role": "HR"}),

    # Dico holdings
    ("james_somerville", "dico", "owner_of", None),
    ("alex", "dico", "shareholder_of", None),
    ("cameron_somerville", "dico", "shareholder_of", None),

    # books_for: Jackie Warden → Alex (densest professional relationship)
    ("existing:2", "alex", "books_for", {"context": "primary talent agent — auditions"}),

    # agent_of / represented_by: Alison and Jason represent Elana
    ("alison_little", "existing:8", "agent_of", {"kind": "principal"}),
    ("jason_thomas", "existing:8", "agent_of", {"kind": "voice"}),
    ("existing:8", "alison_little", "represented_by", {"kind": "principal"}),
    ("existing:8", "jason_thomas", "represented_by", {"kind": "voice"}),

    # neighbor_of: Alex ↔ Gerald
    ("alex", "gerald_karaguni", "neighbor_of", None),

    # prospect_for: Chris Graham → Reprise project
    ("chris_graham", "project:210", "prospect_for",
     {"context": "Re: Reprise thread Mar 16–17 2026 — forwarded tech stack doc"}),

    # shares_framing_with: Reprise ↔ BATL Lane Command (operator intelligence layer)
    ("project:210", "project:2", "shares_framing_with",
     {"framing_concept": "operator intelligence layer",
      "evidence_sources": ["notes_v2:17", "notes_v2:15"]}),
]


# Seed memory episodes. Artifact note ids are resolved by title lookup at
# seed time (falls back to warn-and-skip if the title isn't found).
SEED_EPISODES = [
    {
        "title": "Operator intelligence layer",
        "summary": "A period of thinking about private, owner-facing intelligence "
                   "layers distinct from the public product. Both Reprise "
                   "(competitive analysis, music-as-signal, API budget controls) "
                   "and BATL Lane Command (private ops intelligence, daily metrics, "
                   "revenue dashboards) fit this framing.",
        "started_at": "2026-03-01",
        "ended_at": None,
        "episode_type": "conceptual_thread",
        "emotional_tone": "focused, experimental",
        "members": [
            # (entity_type, resolver_kind, resolver_value, role)
            ("project", "id", 210, "protagonist"),   # Reprise
            ("project", "id", 2, "protagonist"),     # BATL Lane Command
            ("notes_v2", "title_like", "BATL Lane Command%private ops intelligence%", "artifact"),
            ("notes_v2", "title_like", "Cadence%competitive landscape%", "artifact"),
        ],
    },
    {
        "title": "VO career 2026 push",
        "summary": "Demo production with James Andrews, ongoing Jackie Warden "
                   "auditions, Elana's and Ivan's site builds, the us-vo-agent-pursuit "
                   "project, and the ACTRA Game Expo volunteer work. A concentrated "
                   "period of VO career investment in Q1 2026.",
        "started_at": "2026-01-01",
        "ended_at": None,
        "episode_type": "project_phase",
        "emotional_tone": "focused",
        "members": [
            ("contact", "id", 7, "protagonist"),              # James Andrews (canonical)
            ("contact", "id", 2, "protagonist"),              # Jackie Warden
            ("contact", "id", 8, "protagonist"),              # Elana Dunkelman
            ("project", "id", 213, "setting"),                # us-vo-agent-pursuit
            ("project", "id", 207, "setting"),                # Alex Somerville VO
            ("project", "id", 212, "setting"),                # elana-dunkelman-vo
        ],
    },
    {
        "title": "Axe throwing day job",
        "summary": "The ambient employment context around Alex's work at BATL "
                   "Axe Throwing — HR, T4 tips, employment agreement, shift-related "
                   "signal. Distinct from the BATL Lane Command project, which is "
                   "Alex's private ops-intelligence build for the same employer.",
        "started_at": "2025-01-01",
        "ended_at": None,
        "episode_type": "life_event",
        "emotional_tone": "steady, routine",
        "members": [
            ("contact", "key", "alex", "protagonist"),
            ("contact", "key", "batl_hr", "witness"),
            ("contact", "key", "batl_axe_throwing", "setting"),
        ],
    },
    {
        "title": "James's estate planning 2026",
        "summary": "Jim Somerville's estate planning thread — 'Will ideas to be "
                   "discussed' (Jan 28), 'Will ideas Rev 2' (Jan 29), 'Time for a "
                   "Will' (Mar 28). Inner cc circle: Cameron, Ainslie, Anna Lee, "
                   "Alex. Alex's reply 'I have no intention of exiting Dico' "
                   "anchors the shareholder position.",
        "started_at": "2026-01-28",
        "ended_at": None,
        "episode_type": "life_event",
        "emotional_tone": "weighty",
        "members": [
            ("contact", "key", "james_somerville", "protagonist"),
            ("contact", "key", "alex", "member"),
            ("contact", "key", "cameron_somerville", "member"),
            ("contact", "key", "ainslie_roberts", "member"),
            ("contact", "id", 3, "witness"),           # Anna Lee — accountant context
            ("contact", "key", "dico", "artifact"),
        ],
    },
]


# Hand-curated wikilink short-forms. Each: (alias, resolver_kind, resolver_value)
# Primary aliases (the canonical full name) are auto-generated from contacts +
# projects + decisions before this list is applied.
HAND_CURATED_WIKILINKS = [
    ("Jessica", "contact_id", 1),
    ("Grow App", "project_id", 1),
    ("The Grow App", "project_id", 1),
    ("BATL", "project_id", 2),
    ("BATL Lane Command", "project_id", 2),
    ("Kerry", "contact_id", 6),
    ("Elana", "contact_id", 8),
    # New-contact shortforms — resolved by key at apply time
    ("Ivan", "contact_key", "ivan_sherry"),
    ("Dad", "contact_key", "james_somerville"),
    ("Jim", "contact_key", "james_somerville"),
    ("James Somerville", "contact_key", "james_somerville"),
    ("Cam", "contact_key", "cameron_somerville"),
    ("Cameron", "contact_key", "cameron_somerville"),
    ("Jon", "contact_key", "jon_mclaren"),
    ("Craig", "contact_key", "craig_burnatowski"),
    ("Gerald", "contact_key", "gerald_karaguni"),
    ("Ainslie", "contact_key", "ainslie_roberts"),
    ("Dico", "contact_key", "dico"),
    # Ambiguous — these MUST be logged to wikilinks_ambiguous.log, not resolved.
    ("James", "ambiguous", ["James Andrews (existing:7)", "Jim Somerville (new)"]),
    ("Chris", "ambiguous", ["Chris Graham (new)", "Chris Hudson (unresolved)"]),
]


# Entity_type → (table, pk_column). Used by the orphan-edge validation check.
ENTITY_TYPE_TO_TABLE = {
    "contact": ("contacts", "id"),
    "project": ("projects", "id"),
    "project_task": ("project_tasks", "id"),
    "milestone": ("milestones", "id"),
    "decision": ("decisions", "id"),
    "email": ("emails", "id"),
    "calendar_event": ("calendar_events", "id"),
    "contact_interaction": ("contact_interactions", "id"),
    "transcript": ("transcripts", "id"),
    "commitment": ("commitments", "id"),
    "notes_v2": ("notes_v2", "id"),
    "journal_entry": ("journal_entries", "id"),
    "daily_log": ("daily_logs", "id"),
    "memory_episode": ("memory_episodes", "id"),
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    sys.stderr.write(f"[{ts}] {msg}\n")
    sys.stderr.flush()


def _now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_id_list(raw):
    """Parse a linked_* field that might be JSON, CSV, or an id as a string.

    Ported verbatim from shared/loci.py:489. Copied rather than imported
    because benchmarks/loci/ is dependency-free (stdlib only).
    """
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


def _name_in_linked_field(name, raw):
    """Check if a project/contact name appears in a linked_* field as a
    plain string instead of an id.

    Ported from shared/loci.py:459.
    """
    if not name or raw is None:
        return False
    s = str(raw).strip()
    if not s:
        return False
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return any(str(x).strip() == name for x in parsed)
    except (json.JSONDecodeError, ValueError):
        pass
    parts = [p.strip() for p in s.split(",")]
    return name in parts


def _raw_linked_strings(raw):
    """Return the list of non-numeric strings found in a linked_* field,
    so we can log them as unresolved if no id match is found."""
    if not raw:
        return []
    s = str(raw).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed
                    if not str(x).strip().isdigit() and str(x).strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    return [p.strip() for p in s.split(",")
            if p.strip() and not p.strip().isdigit()]


# ---------------------------------------------------------------------------
# Step 1 — init schema + copy carry-over
# ---------------------------------------------------------------------------

def init_schema(conn, stats):
    _log("init_schema: applying 001_core.sql")
    ddl = DDL_PATH.read_text()
    conn.executescript(ddl)
    stats["schema_applied"] = True


def copy_carry_over_tables(conn, stats):
    _log("copy_carry_over_tables: ATTACH soy.db (ro) + INSERT SELECT per table")
    uri = f"file:{REAL_SOY_DB}?mode=ro"
    conn.execute(f"ATTACH DATABASE '{uri}' AS soy")
    counts = {}
    try:
        for src, dst, cols in CARRY_OVER_TABLES:
            col_list = ", ".join(cols)
            sql = (
                f"INSERT INTO main.{dst} ({col_list}) "
                f"SELECT {col_list} FROM soy.{src}"
            )
            cur = conn.execute(sql)
            counts[dst] = cur.rowcount if cur.rowcount != -1 else _row_count(conn, dst)
            _log(f"  {src} → {dst}: {counts[dst]} rows")
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE soy")
    stats["carry_over_counts"] = counts


def _row_count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Step 2 — translate standalone_notes → notes_v2
# ---------------------------------------------------------------------------

def translate_standalone_notes(conn, stats):
    _log("translate_standalone_notes → notes_v2")
    uri = f"file:{REAL_SOY_DB}?mode=ro"
    conn.execute(f"ATTACH DATABASE '{uri}' AS soy")
    try:
        # Preserve original ids so episode artifact references stay stable.
        conn.execute("""
            INSERT INTO notes_v2 (
                id, title, content, note_kind, pinned,
                source, created_at, updated_at
            )
            SELECT
                id, title, content, 'freeform', pinned,
                'migrated_from_standalone', created_at, updated_at
            FROM soy.standalone_notes
        """)
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE soy")
    stats["notes_v2_count"] = _row_count(conn, "notes_v2")
    _log(f"  notes_v2: {stats['notes_v2_count']} rows")


# ---------------------------------------------------------------------------
# Step 3 — structural edges from FKs
# ---------------------------------------------------------------------------

def populate_structural_edges(conn, stats):
    _log("populate_structural_edges: backfilling entity_edges from FKs")
    totals = {}
    for spec in STRUCTURAL_EDGES:
        sql = (
            f"INSERT OR IGNORE INTO entity_edges "
            f"(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
            f"SELECT "
            f"  '{spec['src_type']}', {spec['src_col']}, "
            f"  '{spec['dst_type']}', {spec['dst_col']}, "
            f"  '{spec['edge_type']}', 'backfill', "
            f"  json_object('original_column', '{spec['origin_col']}') "
            f"FROM {spec['table']} "
            f"WHERE {spec['where']}"
        )
        cur = conn.execute(sql)
        inserted = cur.rowcount if cur.rowcount != -1 else 0
        totals[spec["edge_type"]] = totals.get(spec["edge_type"], 0) + inserted
        _log(f"  {spec['edge_type']} ({spec['table']}): +{inserted}")

    # event_with is special — calendar_events.contact_ids is a TEXT list (CSV
    # or JSON), not a single FK. We need to parse it per row.
    event_with = 0
    for row in conn.execute(
        "SELECT id, contact_ids FROM calendar_events WHERE contact_ids IS NOT NULL"
    ):
        for cid in _parse_id_list(row["contact_ids"]):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO entity_edges "
                    "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "calendar_event", row["id"],
                        "contact", cid,
                        "event_with", "backfill",
                        json.dumps({"original_column": "calendar_events.contact_ids"}),
                    ),
                )
                event_with += 1
            except sqlite3.IntegrityError:
                pass
    totals["event_with"] = event_with
    _log(f"  event_with (calendar_events.contact_ids): +{event_with}")

    conn.commit()
    stats["structural_edge_counts"] = totals


# ---------------------------------------------------------------------------
# Step 4 — parse legacy linked_* into 'mentions' edges
# ---------------------------------------------------------------------------

def parse_legacy_linked_columns(conn, stats):
    _log("parse_legacy_linked_columns: legacy linked_* → 'mentions' edges")
    uri = f"file:{REAL_SOY_DB}?mode=ro"
    conn.execute(f"ATTACH DATABASE '{uri}' AS soy")
    unresolved = []
    mention_count = 0
    name_fallback_count = 0
    try:
        # Load lookup sets once
        project_names = {row["id"]: row["name"]
                         for row in conn.execute("SELECT id, name FROM projects")}
        contact_names = {row["id"]: row["name"]
                         for row in conn.execute("SELECT id, name FROM contacts")}
        project_name_to_id = {v: k for k, v in project_names.items()}
        contact_name_to_id = {v: k for k, v in contact_names.items()}

        # standalone_notes (now living in next_soy as notes_v2 with preserved ids)
        for row in conn.execute("""
            SELECT id, title, linked_contacts, linked_projects
            FROM soy.standalone_notes
        """):
            note_id = row["id"]

            # linked_contacts
            for cid in _parse_id_list(row["linked_contacts"]):
                if cid in contact_names:
                    _insert_mention_edge(conn, "notes_v2", note_id, "contact", cid,
                                         "standalone_notes.linked_contacts")
                    mention_count += 1
                else:
                    unresolved.append(
                        f"notes_v2:{note_id} linked_contacts id={cid} not found"
                    )
            # name-string fallback for linked_contacts
            for name in _raw_linked_strings(row["linked_contacts"]):
                cid = contact_name_to_id.get(name)
                if cid is not None:
                    try:
                        _insert_mention_edge(conn, "notes_v2", note_id, "contact", cid,
                                             "standalone_notes.linked_contacts (by name)")
                        mention_count += 1
                        name_fallback_count += 1
                    except sqlite3.IntegrityError:
                        pass
                else:
                    unresolved.append(
                        f"notes_v2:{note_id} linked_contacts name={name!r} not found"
                    )

            # linked_projects — same pattern but also try the name-matcher
            for pid in _parse_id_list(row["linked_projects"]):
                if pid in project_names:
                    _insert_mention_edge(conn, "notes_v2", note_id, "project", pid,
                                         "standalone_notes.linked_projects")
                    mention_count += 1
                else:
                    unresolved.append(
                        f"notes_v2:{note_id} linked_projects id={pid} not found"
                    )
            for name in _raw_linked_strings(row["linked_projects"]):
                pid = project_name_to_id.get(name)
                if pid is not None:
                    try:
                        _insert_mention_edge(conn, "notes_v2", note_id, "project", pid,
                                             "standalone_notes.linked_projects (by name)")
                        mention_count += 1
                        name_fallback_count += 1
                    except sqlite3.IntegrityError:
                        pass
                else:
                    unresolved.append(
                        f"notes_v2:{note_id} linked_projects name={name!r} not found"
                    )

        # journal_entries — same treatment (linked_contacts, linked_projects)
        for row in conn.execute("""
            SELECT id, linked_contacts, linked_projects
            FROM soy.journal_entries
        """):
            je_id = row["id"]
            for cid in _parse_id_list(row["linked_contacts"]):
                if cid in contact_names:
                    _insert_mention_edge(conn, "journal_entry", je_id, "contact", cid,
                                         "journal_entries.linked_contacts")
                    mention_count += 1
                else:
                    unresolved.append(
                        f"journal_entry:{je_id} linked_contacts id={cid} not found"
                    )
            for pid in _parse_id_list(row["linked_projects"]):
                if pid in project_names:
                    _insert_mention_edge(conn, "journal_entry", je_id, "project", pid,
                                         "journal_entries.linked_projects")
                    mention_count += 1
                else:
                    unresolved.append(
                        f"journal_entry:{je_id} linked_projects id={pid} not found"
                    )

        conn.commit()
    finally:
        conn.execute("DETACH DATABASE soy")

    stats["mentions_edge_count"] = mention_count
    stats["mentions_name_fallbacks"] = name_fallback_count
    stats["unresolved_count"] = len(unresolved)

    if unresolved:
        UNRESOLVED_LOG.write_text(
            "# seed_unresolved.log — linked_* references that could not be resolved\n"
            f"# Generated: {_now_iso()}\n\n"
            + "\n".join(unresolved) + "\n"
        )
        _log(f"  wrote {len(unresolved)} unresolved refs to {UNRESOLVED_LOG.name}")
    else:
        if UNRESOLVED_LOG.exists():
            UNRESOLVED_LOG.unlink()
    _log(f"  mentions edges: +{mention_count} "
         f"(name fallbacks: {name_fallback_count})")


def _insert_mention_edge(conn, src_type, src_id, dst_type, dst_id, origin):
    conn.execute(
        "INSERT OR IGNORE INTO entity_edges "
        "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
        "VALUES (?, ?, ?, ?, 'mentions', 'backfill', ?)",
        (src_type, src_id, dst_type, dst_id,
         json.dumps({"original_column": origin})),
    )


# ---------------------------------------------------------------------------
# Step 5 — contact_identities from contacts.email
# ---------------------------------------------------------------------------

def populate_contact_identities(conn, stats):
    _log("populate_contact_identities: backfill email-identity rows")
    cur = conn.execute("""
        INSERT OR IGNORE INTO contact_identities
            (canonical_contact_id, identity_type, identity_value,
             confidence, verified, source)
        SELECT id, 'email', email, 1.0, 1, 'backfill'
        FROM contacts
        WHERE email IS NOT NULL AND email != ''
    """)
    conn.commit()
    stats["contact_identity_count"] = _row_count(conn, "contact_identities")
    _log(f"  contact_identities: {stats['contact_identity_count']} rows")


# ---------------------------------------------------------------------------
# Step 6 — resolve James Andrews duplicate
# ---------------------------------------------------------------------------

def resolve_james_andrews_duplicate(conn, stats):
    _log("resolve_james_andrews_duplicate: merge id 9 → id 7")
    # Defensive guards — only merge if both rows still exist and look like
    # James Andrews.
    row9 = conn.execute(
        "SELECT id, name FROM contacts WHERE id = 9"
    ).fetchone()
    row7 = conn.execute(
        "SELECT id, name FROM contacts WHERE id = 7"
    ).fetchone()
    if not (row9 and row7):
        _log("  SKIP — id 7 or id 9 missing (James Andrews merge not applicable)")
        stats["james_andrews_merged"] = False
        return
    if "James Andrews" not in row9["name"] or "James Andrews" not in row7["name"]:
        _log(f"  SKIP — unexpected names: id 7={row7['name']!r}, id 9={row9['name']!r}")
        stats["james_andrews_merged"] = False
        return

    conn.execute("""
        UPDATE contacts
        SET merged_into_id = 7, merged_at = datetime('now'), status = 'inactive'
        WHERE id = 9
    """)
    # Re-home edges
    conn.execute("""
        UPDATE entity_edges SET dst_id = 7
        WHERE dst_type = 'contact' AND dst_id = 9
    """)
    conn.execute("""
        UPDATE entity_edges SET src_id = 7
        WHERE src_type = 'contact' AND src_id = 9
    """)
    # contact_identities: soft-dedupe by detaching id 9's identity rows.
    # (The UNIQUE constraint on (identity_type, identity_value) would reject
    # a direct re-point; since both James rows share the same email, id 9's
    # row should just be removed, not re-pointed.)
    conn.execute("""
        DELETE FROM contact_identities
        WHERE canonical_contact_id = 9
    """)
    conn.commit()
    stats["james_andrews_merged"] = True
    _log("  merged id 9 into id 7, rewired edges, pruned duplicate identity rows")


# ---------------------------------------------------------------------------
# Step 7 — seed new contacts + edges (audit-driven)
# ---------------------------------------------------------------------------

def seed_new_contacts_and_edges(conn, stats):
    _log("seed_new_contacts_and_edges: inserting audit-driven rows")

    # Apply status flips to existing contacts first
    flipped = 0
    for cid, new_status, _reason in STATUS_FLIPS:
        cur = conn.execute(
            "UPDATE contacts SET status = ? WHERE id = ?",
            (new_status, cid),
        )
        if cur.rowcount:
            flipped += 1
    _log(f"  status flips applied: {flipped}/{len(STATUS_FLIPS)}")

    # Kerry promotion
    conn.execute(
        "UPDATE contacts SET email = ?, company = ?, role = ?, status = ? "
        "WHERE id = ?",
        (
            KERRY_PROMOTION["email"],
            KERRY_PROMOTION["company"],
            KERRY_PROMOTION["role"],
            KERRY_PROMOTION["status"],
            KERRY_PROMOTION["id"],
        ),
    )
    # Kerry's email identity (since pre-promotion he had no email the backfill
    # skipped him; insert his primary now).
    conn.execute(
        "INSERT OR IGNORE INTO contact_identities "
        "(canonical_contact_id, identity_type, identity_value, "
        " confidence, verified, source) "
        "VALUES (?, 'email', ?, 1.0, 1, 'merge')",
        (KERRY_PROMOTION["id"], KERRY_PROMOTION["email"]),
    )
    _log(f"  Kerry Morrison (id {KERRY_PROMOTION['id']}) promoted")

    # Insert new contacts, capture assigned ids
    key_to_id = {}
    for c in NEW_CONTACTS:
        cur = conn.execute(
            "INSERT INTO contacts "
            "(name, email, phone, company, role, type, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (c["name"], c["email"], c["phone"], c["company"],
             c["role"], c["type"], c["status"], c["notes"]),
        )
        key_to_id[c["key"]] = cur.lastrowid
        # Identity row for the primary email, if present
        if c["email"]:
            conn.execute(
                "INSERT OR IGNORE INTO contact_identities "
                "(canonical_contact_id, identity_type, identity_value, "
                " confidence, verified, source) "
                "VALUES (?, 'email', ?, 1.0, 1, 'import')",
                (cur.lastrowid, c["email"]),
            )
    _log(f"  inserted {len(key_to_id)} new contacts")

    # Record Alex's id in soy_meta so loci_v2 can resolve "the user"
    alex_id = key_to_id["alex"]
    conn.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value) VALUES (?, ?)",
        ("user_contact_id", str(alex_id)),
    )

    # Apply ADDITIONAL_IDENTITIES (Kerry's secondary, Chris Graham's second email)
    # The "kerry_existing" sentinel points at real-SoY id 6.
    additional_inserted = 0
    for handle, itype, value, confidence, verified in ADDITIONAL_IDENTITIES:
        if handle == "kerry_existing":
            target_id = KERRY_PROMOTION["id"]
        else:
            target_id = key_to_id.get(handle)
            if target_id is None:
                _log(f"  WARN: additional identity handle not found: {handle!r}")
                continue
        try:
            conn.execute(
                "INSERT OR IGNORE INTO contact_identities "
                "(canonical_contact_id, identity_type, identity_value, "
                " confidence, verified, source) "
                "VALUES (?, ?, ?, ?, ?, 'import')",
                (target_id, itype, value, confidence, verified),
            )
            additional_inserted += 1
        except sqlite3.IntegrityError:
            pass
    _log(f"  additional identities inserted: {additional_inserted}")

    # Materialize new edges
    def resolve(handle):
        """Resolve an edge-spec handle to (entity_type, entity_id)."""
        if handle.startswith("existing:"):
            return "contact", int(handle.split(":", 1)[1])
        if handle.startswith("elana_existing:"):
            return "contact", int(handle.split(":", 1)[1])
        if handle.startswith("project:"):
            return "project", int(handle.split(":", 1)[1])
        if handle in key_to_id:
            return "contact", key_to_id[handle]
        raise KeyError(f"cannot resolve handle: {handle!r}")

    edge_count = 0
    for src, dst, edge_type, metadata in NEW_EDGES:
        src_type, src_id = resolve(src)
        dst_type, dst_id = resolve(dst)
        try:
            conn.execute(
                "INSERT INTO entity_edges "
                "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                "VALUES (?, ?, ?, ?, ?, 'manual', ?)",
                (src_type, src_id, dst_type, dst_id, edge_type,
                 json.dumps(metadata) if metadata else None),
            )
            edge_count += 1
        except sqlite3.IntegrityError as exc:
            _log(f"  WARN: duplicate edge skipped: {src}→{dst} [{edge_type}]: {exc}")

    conn.commit()
    stats["new_contact_count"] = len(key_to_id)
    stats["new_edge_count"] = edge_count
    stats["status_flip_count"] = flipped
    stats["new_contact_keys"] = key_to_id  # downstream steps need this
    _log(f"  new edges: +{edge_count}")


# ---------------------------------------------------------------------------
# Step 8 — seed memory episodes
# ---------------------------------------------------------------------------

def seed_memory_episodes(conn, stats):
    _log("seed_memory_episodes: 4 hand-authored episodes")
    key_to_id = stats["new_contact_keys"]
    episode_count = 0
    member_count = 0
    missing_members = []

    for ep in SEED_EPISODES:
        cur = conn.execute(
            "INSERT INTO memory_episodes "
            "(title, summary, started_at, ended_at, episode_type, "
            " emotional_tone, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 'import')",
            (ep["title"], ep["summary"], ep["started_at"], ep["ended_at"],
             ep["episode_type"], ep["emotional_tone"]),
        )
        episode_id = cur.lastrowid
        episode_count += 1

        for entity_type, resolver_kind, resolver_value, role in ep["members"]:
            entity_id = None
            if resolver_kind == "id":
                entity_id = resolver_value
            elif resolver_kind == "key":
                entity_id = key_to_id.get(resolver_value)
                if entity_id is None:
                    missing_members.append(
                        f"{ep['title']}: contact key {resolver_value!r} not found"
                    )
                    continue
            elif resolver_kind == "title_like":
                row = conn.execute(
                    "SELECT id FROM notes_v2 WHERE title LIKE ? LIMIT 1",
                    (resolver_value,),
                ).fetchone()
                if row is None:
                    missing_members.append(
                        f"{ep['title']}: note title-like {resolver_value!r} not found"
                    )
                    continue
                entity_id = row["id"]

            if entity_id is None:
                continue

            # Validate the target row exists
            table, pk = ENTITY_TYPE_TO_TABLE[entity_type]
            exists = conn.execute(
                f"SELECT 1 FROM {table} WHERE {pk} = ?", (entity_id,)
            ).fetchone()
            if not exists:
                missing_members.append(
                    f"{ep['title']}: {entity_type}:{entity_id} row not found"
                )
                continue

            try:
                conn.execute(
                    "INSERT INTO episode_members "
                    "(episode_id, entity_type, entity_id, role) "
                    "VALUES (?, ?, ?, ?)",
                    (episode_id, entity_type, entity_id, role),
                )
                member_count += 1
                # Also emit a part_of_episode edge for alternate-path walks
                conn.execute(
                    "INSERT OR IGNORE INTO entity_edges "
                    "(src_type, src_id, dst_type, dst_id, edge_type, source, metadata) "
                    "VALUES (?, ?, 'memory_episode', ?, 'part_of_episode', 'manual', ?)",
                    (entity_type, entity_id, episode_id,
                     json.dumps({"role": role, "episode_title": ep["title"]})),
                )
            except sqlite3.IntegrityError:
                pass

    conn.commit()
    stats["episode_count"] = episode_count
    stats["episode_member_count"] = member_count
    stats["episode_missing_members"] = missing_members
    for warning in missing_members:
        _log(f"  WARN: {warning}")
    _log(f"  episodes: +{episode_count} ({member_count} members)")


# ---------------------------------------------------------------------------
# Step 9 — wikilinks
# ---------------------------------------------------------------------------

def populate_wikilinks(conn, stats):
    _log("populate_wikilinks: primary aliases + hand-curated short-forms")
    key_to_id = stats["new_contact_keys"]

    # Primary aliases: entity name → itself (contacts, projects, decisions)
    primary_count = 0
    for row in conn.execute(
        "SELECT id, name FROM contacts WHERE merged_into_id IS NULL"
    ):
        try:
            conn.execute(
                "INSERT OR IGNORE INTO wikilinks "
                "(alias, canonical_type, canonical_id, is_primary, "
                " confidence, created_by) "
                "VALUES (?, 'contact', ?, 1, 1.0, 'import')",
                (row["name"], row["id"]),
            )
            primary_count += 1
        except sqlite3.IntegrityError:
            pass
    for row in conn.execute("SELECT id, name FROM projects"):
        try:
            conn.execute(
                "INSERT OR IGNORE INTO wikilinks "
                "(alias, canonical_type, canonical_id, is_primary, "
                " confidence, created_by) "
                "VALUES (?, 'project', ?, 1, 1.0, 'import')",
                (row["name"], row["id"]),
            )
            primary_count += 1
        except sqlite3.IntegrityError:
            pass
    for row in conn.execute("SELECT id, title FROM decisions"):
        try:
            conn.execute(
                "INSERT OR IGNORE INTO wikilinks "
                "(alias, canonical_type, canonical_id, is_primary, "
                " confidence, created_by) "
                "VALUES (?, 'decision', ?, 1, 1.0, 'import')",
                (row["title"], row["id"]),
            )
            primary_count += 1
        except sqlite3.IntegrityError:
            pass

    # Hand-curated short-forms
    curated_count = 0
    ambiguous_entries = []
    for alias, kind, value in HAND_CURATED_WIKILINKS:
        if kind == "ambiguous":
            ambiguous_entries.append((alias, value))
            continue
        if kind == "contact_id":
            ctype, cid = "contact", int(value)
        elif kind == "project_id":
            ctype, cid = "project", int(value)
        elif kind == "contact_key":
            cid = key_to_id.get(value)
            if cid is None:
                _log(f"  WARN: hand-curated wikilink key not found: {value!r}")
                continue
            ctype = "contact"
        else:
            continue
        try:
            conn.execute(
                "INSERT OR IGNORE INTO wikilinks "
                "(alias, canonical_type, canonical_id, is_primary, "
                " confidence, created_by) "
                "VALUES (?, ?, ?, 0, 1.0, 'user')",
                (alias, ctype, cid),
            )
            curated_count += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()

    # Log ambiguous aliases for review
    if ambiguous_entries:
        lines = [
            "# wikilinks_ambiguous.log — aliases that resolve to multiple entities",
            f"# Generated: {_now_iso()}",
            "",
        ]
        for alias, candidates in ambiguous_entries:
            lines.append(f"{alias}:")
            for c in candidates:
                lines.append(f"  - {c}")
            lines.append("")
        AMBIGUOUS_LOG.write_text("\n".join(lines))
        _log(f"  logged {len(ambiguous_entries)} ambiguous aliases")
    else:
        if AMBIGUOUS_LOG.exists():
            AMBIGUOUS_LOG.unlink()

    stats["wikilink_primary_count"] = primary_count
    stats["wikilink_curated_count"] = curated_count
    stats["wikilink_ambiguous_count"] = len(ambiguous_entries)
    _log(f"  wikilinks: {primary_count} primary, {curated_count} curated")


# ---------------------------------------------------------------------------
# Step 10 — validate (9 checks per schema doc)
# ---------------------------------------------------------------------------

def validate(conn, stats):
    _log("validate: running 9 checks from the DDL doc")
    checks = []

    # 1. Row count parity for carry-over tables
    uri = f"file:{REAL_SOY_DB}?mode=ro"
    conn.execute(f"ATTACH DATABASE '{uri}' AS soy")
    try:
        # Tables that legitimately grow during seeding:
        #   contacts  — audit-driven new rows
        #   soy_meta  — adds next_soy_schema_version + user_contact_id markers
        may_grow = {"contacts", "soy_meta"}
        mismatches = []
        for src, dst, _cols in CARRY_OVER_TABLES:
            soy_n = conn.execute(f"SELECT COUNT(*) FROM soy.{src}").fetchone()[0]
            next_n = conn.execute(f"SELECT COUNT(*) FROM main.{dst}").fetchone()[0]
            if src in may_grow:
                if next_n < soy_n:
                    mismatches.append(f"{src}→{dst}: soy={soy_n}, next={next_n} (shrank)")
            else:
                if soy_n != next_n:
                    mismatches.append(f"{src}→{dst}: soy={soy_n}, next={next_n}")
        checks.append({
            "name": "Row count parity",
            "passed": not mismatches,
            "detail": "; ".join(mismatches) if mismatches else "all carry-over tables match",
        })

        # notes_v2 vs standalone_notes
        sn = conn.execute("SELECT COUNT(*) FROM soy.standalone_notes").fetchone()[0]
        nv2 = _row_count(conn, "notes_v2")
        checks.append({
            "name": "notes_v2 count matches standalone_notes",
            "passed": sn == nv2,
            "detail": f"standalone_notes={sn}, notes_v2={nv2}",
        })
    finally:
        conn.execute("DETACH DATABASE soy")

    # 2. Edge count sanity
    edges_total = _row_count(conn, "entity_edges")
    checks.append({
        "name": "entity_edges has rows",
        "passed": edges_total > 0,
        "detail": f"{edges_total} total edges",
    })

    # 3. No orphaned edges
    orphans = []
    for etype, (table, pk) in ENTITY_TYPE_TO_TABLE.items():
        # src orphans
        n = conn.execute(
            f"SELECT COUNT(*) FROM entity_edges e "
            f"WHERE e.src_type = ? "
            f"AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{pk} = e.src_id)",
            (etype,),
        ).fetchone()[0]
        if n:
            orphans.append(f"{n} src orphans of type {etype}")
        n = conn.execute(
            f"SELECT COUNT(*) FROM entity_edges e "
            f"WHERE e.dst_type = ? "
            f"AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{pk} = e.dst_id)",
            (etype,),
        ).fetchone()[0]
        if n:
            orphans.append(f"{n} dst orphans of type {etype}")
    # Unknown entity types
    unknown_types = conn.execute("""
        SELECT DISTINCT src_type FROM entity_edges
        UNION
        SELECT DISTINCT dst_type FROM entity_edges
    """).fetchall()
    unhandled = [r[0] for r in unknown_types if r[0] not in ENTITY_TYPE_TO_TABLE]
    if unhandled:
        orphans.append(f"unknown entity_types present: {unhandled}")
    checks.append({
        "name": "No orphaned edges",
        "passed": not orphans,
        "detail": "; ".join(orphans) if orphans else "every edge resolves on both sides",
    })

    # 4. Unresolved-link audit
    checks.append({
        "name": "Unresolved-link log reviewed",
        "passed": True,  # informational — the log is the artifact
        "detail": (f"{stats.get('unresolved_count', 0)} entries in "
                   f"{UNRESOLVED_LOG.name}" if stats.get('unresolved_count')
                   else "no unresolved links"),
    })

    # 5. Identity uniqueness
    dup = conn.execute("""
        SELECT identity_type, identity_value, COUNT(*) c
        FROM contact_identities
        GROUP BY identity_type, identity_value HAVING c > 1
    """).fetchall()
    checks.append({
        "name": "Contact identity uniqueness",
        "passed": not dup,
        "detail": f"{len(dup)} duplicate identity rows" if dup else "all identities unique",
    })

    # 6. Wikilink ambiguity report
    checks.append({
        "name": "Wikilink ambiguities logged",
        "passed": True,
        "detail": (f"{stats.get('wikilink_ambiguous_count', 0)} aliases in "
                   f"{AMBIGUOUS_LOG.name}"
                   if stats.get('wikilink_ambiguous_count')
                   else "no ambiguous aliases"),
    })

    # 7. Contact status distribution
    rows = conn.execute(
        "SELECT status, COUNT(*) c FROM contacts GROUP BY status"
    ).fetchall()
    status_dist = {r["status"]: r["c"] for r in rows}
    required = ("active", "prospect", "broadcast_only")
    missing = [s for s in required if status_dist.get(s, 0) == 0]
    checks.append({
        "name": "Contact status distribution",
        "passed": not missing,
        "detail": (f"missing status buckets: {missing}. dist={status_dist}"
                   if missing else f"dist={status_dist}"),
    })

    # 8. building_site_for cross-reference test — 4 specific edges
    expected_workspaces = {
        "/wkspaces/elana-dunkelman-vo",
        "/wkspaces/ivan-sherry-site",
        "/wkspaces/jon-mclaren-vo",
        "/wkspaces/craig-burnatowski-site",
    }
    found = set()
    for row in conn.execute("""
        SELECT metadata FROM entity_edges
        WHERE edge_type = 'building_site_for' AND src_type = 'contact'
    """):
        try:
            md = json.loads(row["metadata"]) if row["metadata"] else {}
        except (TypeError, json.JSONDecodeError):
            continue
        ws = md.get("workspace")
        if ws:
            found.add(ws)
    missing_ws = expected_workspaces - found
    checks.append({
        "name": "building_site_for cross-reference",
        "passed": not missing_ws,
        "detail": (f"missing workspaces: {sorted(missing_ws)}"
                   if missing_ws
                   else f"all 4 workspace edges present: {sorted(found)}"),
    })

    # 9. Kerry promotion check
    kerry = conn.execute("""
        SELECT email, status FROM contacts WHERE id = ?
    """, (KERRY_PROMOTION["id"],)).fetchone()
    kerry_ok = (
        kerry is not None
        and kerry["email"] is not None
        and kerry["status"] == "active"
    )
    kerry_edge = conn.execute("""
        SELECT COUNT(*) FROM entity_edges
        WHERE src_type = 'contact' AND src_id = ?
          AND edge_type = 'collaborator_on'
    """, (KERRY_PROMOTION["id"],)).fetchone()[0]
    kerry_passed = kerry_ok and kerry_edge > 0
    checks.append({
        "name": "Kerry promotion check",
        "passed": kerry_passed,
        "detail": (f"email={kerry['email'] if kerry else 'None'}, "
                   f"status={kerry['status'] if kerry else 'None'}, "
                   f"collaborator_on edges={kerry_edge}"),
    })

    stats["validation_checks"] = checks
    failed = [c for c in checks if not c["passed"]]
    _log(f"  validation: {len(checks) - len(failed)}/{len(checks)} passed")
    for c in failed:
        _log(f"  FAIL: {c['name']} — {c['detail']}")


# ---------------------------------------------------------------------------
# Step 11 — write validation report
# ---------------------------------------------------------------------------

def write_validation_report(conn, stats, started_at, args):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"seed_{ts}.md"

    elapsed = time.monotonic() - started_at
    carry_counts = stats.get("carry_over_counts", {})
    struct_counts = stats.get("structural_edge_counts", {})
    checks = stats.get("validation_checks", [])
    passed = sum(1 for c in checks if c["passed"])

    # Per-edge-type totals (from entity_edges after all writes)
    edge_type_rows = conn.execute("""
        SELECT edge_type, COUNT(*) c FROM entity_edges
        GROUP BY edge_type ORDER BY c DESC
    """).fetchall()

    # Real-SoY snapshot manifest (for drift detection later)
    soy_stat = REAL_SOY_DB.stat()
    snapshot = {
        "soy_path": str(REAL_SOY_DB),
        "soy_mtime": datetime.fromtimestamp(soy_stat.st_mtime).isoformat(),
        "soy_size_bytes": soy_stat.st_size,
    }

    lines = [
        f"# next_soy seed report — {ts}",
        "",
        f"**Seed script:** `benchmarks/loci/seed_next_soy.py`  ",
        f"**Output DB:** `{OUT_DB.relative_to(SCRIPT_DIR.parent.parent)}`  ",
        f"**Duration:** {elapsed:.2f}s  ",
        f"**Real-SoY snapshot:** {snapshot['soy_mtime']}, "
        f"{snapshot['soy_size_bytes']:,} bytes  ",
        f"**Args:** force={args.force}",
        "",
        "## Validation",
        "",
        f"**{passed}/{len(checks)} checks passed.**",
        "",
        "| # | Check | Result | Detail |",
        "|---|---|---|---|",
    ]
    for i, c in enumerate(checks, 1):
        mark = "✓" if c["passed"] else "✗"
        detail = c["detail"].replace("\n", " ")
        lines.append(f"| {i} | {c['name']} | {mark} | {detail} |")

    lines += [
        "",
        "## Carry-over counts",
        "",
        "| Table | Rows |",
        "|---|---|",
    ]
    for _src, dst, _cols in CARRY_OVER_TABLES:
        lines.append(f"| {dst} | {carry_counts.get(dst, _row_count(conn, dst))} |")

    lines += [
        "",
        "## Structural edge counts (backfill)",
        "",
        "| edge_type | rows |",
        "|---|---|",
    ]
    for etype, n in sorted(struct_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {etype} | {n} |")

    lines += [
        "",
        f"**'mentions' edges from legacy linked_*:** "
        f"{stats.get('mentions_edge_count', 0)} "
        f"(name-fallback: {stats.get('mentions_name_fallbacks', 0)})",
        "",
        "## Final entity_edges distribution",
        "",
        "| edge_type | count |",
        "|---|---|",
    ]
    for row in edge_type_rows:
        lines.append(f"| {row['edge_type']} | {row['c']} |")

    lines += [
        "",
        "## Audit-driven additions",
        "",
        f"- New contacts inserted: **{stats.get('new_contact_count', 0)}**",
        f"- Status flips applied: **{stats.get('status_flip_count', 0)}** "
        f"(of {len(STATUS_FLIPS)} planned)",
        f"- James Andrews merge: "
        f"**{'applied' if stats.get('james_andrews_merged') else 'SKIPPED'}**",
        f"- New edges from audit: **{stats.get('new_edge_count', 0)}**",
        f"- Episodes seeded: **{stats.get('episode_count', 0)}** "
        f"with {stats.get('episode_member_count', 0)} members",
        f"- Wikilinks: **{stats.get('wikilink_primary_count', 0)}** primary, "
        f"**{stats.get('wikilink_curated_count', 0)}** curated, "
        f"**{stats.get('wikilink_ambiguous_count', 0)}** ambiguous",
        f"- Contact identities: **{stats.get('contact_identity_count', 0)}** rows",
        "",
    ]

    missing = stats.get("episode_missing_members", [])
    if missing:
        lines += ["### Episode member warnings", ""]
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")

    lines += [
        "## Logs",
        "",
        f"- Unresolved refs: `{UNRESOLVED_LOG.name}` "
        f"({stats.get('unresolved_count', 0)} entries)",
        f"- Ambiguous wikilinks: `{AMBIGUOUS_LOG.name}` "
        f"({stats.get('wikilink_ambiguous_count', 0)} entries)",
        "",
        "## Snapshot manifest (for drift detection)",
        "",
        "```json",
        json.dumps(snapshot, indent=2),
        "```",
        "",
    ]

    report_path.write_text("\n".join(lines))
    _log(f"wrote validation report → {report_path.relative_to(SCRIPT_DIR)}")
    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing next_soy.db",
    )
    args = parser.parse_args()

    if not REAL_SOY_DB.exists():
        sys.exit(f"FATAL: real SoY DB not found at {REAL_SOY_DB}")
    if not DDL_PATH.exists():
        sys.exit(f"FATAL: DDL file not found at {DDL_PATH}")

    if OUT_DB.exists():
        if not args.force:
            sys.exit(
                f"FATAL: {OUT_DB} already exists. Use --force to overwrite."
            )
        OUT_DB.unlink()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    stats = {}

    # FKs are OFF at load time (see 001_core.sql). Open with URI support so
    # ATTACH DATABASE can pass ?mode=ro for the real-SoY read.
    conn = sqlite3.connect(f"file:{OUT_DB}", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn, stats)
        copy_carry_over_tables(conn, stats)
        translate_standalone_notes(conn, stats)
        populate_structural_edges(conn, stats)
        parse_legacy_linked_columns(conn, stats)
        populate_contact_identities(conn, stats)
        resolve_james_andrews_duplicate(conn, stats)
        seed_new_contacts_and_edges(conn, stats)
        seed_memory_episodes(conn, stats)
        populate_wikilinks(conn, stats)
        validate(conn, stats)
        report_path = write_validation_report(conn, stats, started, args)
    finally:
        conn.close()

    failed = [c for c in stats.get("validation_checks", []) if not c["passed"]]
    if failed:
        _log(f"DONE with {len(failed)} validation failures. See {report_path}")
        sys.exit(1)
    _log(f"DONE. See {report_path}")


if __name__ == "__main__":
    main()
