---
name: creative-identity
description: Persistent creative writing identity — style baseline, narrative DNA, and project creative context. Use during any creative writing session.
version: 1.0.0
---

# Creative Identity

## When to Use

- During any creative writing or drafting session
- When the user asks to capture feedback about writing style or narrative approach
- When starting work on a creative project (load project profile)
- When reviewing AI-generated draft output (check drift, capture principles)
- Post-session to log decisions, observations, and open questions

## Style Modes

| Mode | What's Applied | When to Use |
|------|---------------|-------------|
| `raw` (default) | Nothing — blank slate | Getting unbiased first drafts, experimenting freely |
| `exploratory` | Narrative principles + project context | Trying new forms while keeping thematic DNA |
| `learned` | Everything — baseline + principles + context | Consistency at volume, polished output |

## Workflow: Creative Session

### Starting a Session
1. Check current mode: `get_mode`
2. Load project context if applicable: `get_project_profile`
3. If mode is `learned` or `exploratory`, load full profile: `get_profile`

### During Drafting
- Capture new principles from feedback with `add_principle`
- Add project context entries (characters, decisions, lore) with `add_context`
- If user reacts to draft quality, note what worked and encode as a principle

### Post-Session
- Log the session with `log_session` — observations, decisions made, open questions
- Update any context entries that changed status (scene completed, thread resolved)
- If the user approved a draft, ingest it as a sample with `add_sample` (source_type: `ai_approved`)

## Principle Categories

| Category | What It Captures |
|----------|-----------------|
| `structure` | How prose is organized — revelation patterns, dramatic irony, closing beats |
| `pacing` | When to accelerate/decelerate — earned interiority, restraint, depth timing |
| `character` | Construction patterns — opacity, voice architecture, POV exclusion as a tool |
| `theme` | What the prose trusts readers to infer vs states, thematic tendencies |
| `pov` | POV preferences, when different approaches serve different functions |
| `tone` | Tonal registers — can coexist without resolving, genre-specific tendencies |
| `dialogue` | How characters speak — subtext, repetition, dialect, silence as dialogue |
| `general` | Cross-cutting preferences that don't fit neatly into one category |

## Context Types

| Type | What It Stores |
|------|---------------|
| `character` | Character profiles, readings, interpretations specific to this project |
| `structure` | POV approach, timeline handling, what's dramatized vs implied |
| `theme` | Thematic framework, central tensions, what the work is "about" |
| `scene` | Individual scenes — status, key beats, notes |
| `decision` | Creative decisions made during drafting (and why) |
| `thread` | Open narrative questions, unresolved elements |
| `canon` | Guardrails — what can't be contradicted |
| `lore` | World-building, setting details, background |
| `relationship` | Character relationships and dynamics |
| `note` | Freeform creative notes |

## Drift Detection

`check_drift` compares a text sample against the mechanical baseline. It flags:
- Sentence length > 30% deviation from baseline
- Dialogue ratio > 40% deviation
- Paragraph density > 30% deviation

This is a drift **detector**, not a style **enforcer**. Flag deviations conversationally, don't auto-correct. The user decides whether drift is intentional exploration or unintentional verbosity.

## Key Reference: Seed Principles

The initial narrative principles (from the first creative session) are seeded in the migration and represent the starting creative DNA. See `references/seed-principles.md` for the full set with evidence.
