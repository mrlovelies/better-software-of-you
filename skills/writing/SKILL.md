---
name: writing
description: Versioned draft management — create, version, tag, and review prose drafts with feedback tracking and lore cross-referencing.
version: 1.0.0
---

# Writing Module

## When to Use

- When the user creates, edits, or reviews prose drafts
- When managing chapter/scene structure for a writing project
- When logging feedback or revision notes on a draft
- When tagging characters or linking lore to drafts
- When checking writing progress on a project
- When the user asks to see their feedback queue or lore coverage

## Core Concepts

### Draft Hierarchy

Drafts are hierarchical. A **chapter** can contain **scenes**. A **scene** can stand alone. **Fragments** are loose pieces not yet placed.

```
Chapter 1: Bevelle
  ├── Scene 1: The Temple Steps (draft, 2,400 words)
  ├── Scene 2: The Audience Chamber (outline)
  └── Scene 3: Departure (draft, 1,100 words)
Chapter 2: The Mi'ihen Highroad
  └── Scene 1: First Camp (revision, 3,200 words)
Fragment: Jecht's drinking song (draft, 340 words)
```

Use `parent_id` to nest scenes within chapters. Use `sort_order` to control sequence.

### Versioning

Every content save creates a new row in `draft_versions`. Content is **never overwritten** — old versions are always retrievable. The `current_version` field on the draft points to the latest.

**Creating a new version:**
1. Determine the next version number: `current_version + 1`
2. INSERT into `draft_versions` with the new content and version number
3. UPDATE `writing_drafts` set `current_version`, `word_count`, `updated_at`

**Reading the current content:**
```sql
SELECT dv.content, dv.word_count, dv.change_summary
FROM draft_versions dv
WHERE dv.draft_id = ? AND dv.version_number = (
    SELECT current_version FROM writing_drafts WHERE id = ?
);
```

### Draft Status Flow

| Status | Meaning |
|--------|---------|
| `outline` | Structural placeholder — synopsis only, no prose yet |
| `draft` | First pass prose exists |
| `revision` | Being actively revised based on feedback |
| `review` | Handed off for review (user or AI) |
| `final` | Considered done (still versionable if reopened) |
| `archived` | Set aside — not deleted, just out of active rotation |

### Feedback Types

| Type | When to Use |
|------|-------------|
| `note` | General observation about the draft |
| `revision` | Specific change needed |
| `critique` | Analytical feedback on what isn't working and why |
| `suggestion` | Proposed alternative or addition |
| `question` | Something that needs clarification or a decision |

Feedback can optionally include `highlighted_text` to anchor it to a specific passage. Feedback has a status lifecycle: `open` → `addressed` / `dismissed` / `deferred`.

### Lore Links

When a draft references, establishes, contradicts, or extends a `creative_context` entry (from the Creative Identity module), create a `draft_lore_links` row.

| Link Type | Meaning |
|-----------|---------|
| `references` | Draft mentions or relies on this lore/context |
| `establishes` | Draft is the source that creates this piece of lore |
| `contradicts` | Draft conflicts with established context (flag for resolution) |
| `extends` | Draft adds detail or nuance to existing context |

**Contradiction links are especially important** — they surface continuity problems for the user to resolve.

### Character Tagging

Tag which characters appear in each draft and their role:

| Role | Meaning |
|------|---------|
| `pov` | This character's perspective drives the draft |
| `featured` | Significant presence, dialogue, or action |
| `mentioned` | Referenced but not present |
| `absent` | Deliberately absent (useful for tracking who hasn't appeared yet) |

## Workflows

### Creating a New Draft

1. INSERT into `writing_drafts` with title, draft_type, project_id, sort_order, pov_character
2. If it has initial content, create version 1 in `draft_versions`
3. Update `current_version = 1` and `word_count` on the draft
4. Tag characters with `draft_characters`
5. Link relevant lore with `draft_lore_links`
6. Log to `activity_log`

### Saving a New Version

1. Count words in the new content
2. INSERT into `draft_versions` (draft_id, version_number, content, word_count, change_summary)
3. UPDATE `writing_drafts` SET current_version, word_count, updated_at
4. Log to `activity_log`

### Reviewing a Draft

1. Load the current version content
2. Load open feedback for this draft
3. Load lore links to check continuity context
4. Present the draft with feedback annotations
5. After review, add new feedback entries or mark existing ones addressed

### Checking Project Progress

Use the `v_writing_progress` view:
```sql
SELECT * FROM v_writing_progress WHERE project_id = ?;
```

Present as a progress summary: "12 drafts, 28,400 words. 3 final, 6 in draft, 2 in revision, 1 outlined. 4 open feedback items."

### Viewing the Feedback Queue

Use the `v_feedback_queue` view:
```sql
SELECT * FROM v_feedback_queue WHERE project_id = ?;
```

### Checking Lore Coverage

Use the `v_lore_coverage` view to find orphaned context entries (lore that no draft references) or heavily-referenced entries:
```sql
SELECT * FROM v_lore_coverage WHERE project_id = ? AND draft_references = 0;
```

"3 lore entries have no draft references yet: The Calm Lands geography, Seymour's lineage, Yevon's internal hierarchy."

### Viewing Character Appearances

Use `v_character_appearances`:
```sql
SELECT * FROM v_character_appearances WHERE project_ids LIKE '%209%';
```

## Computed Views Reference

| View | Use For |
|------|---------|
| `v_draft_overview` | Full draft detail with version count, feedback count, characters |
| `v_writing_progress` | Per-project word counts, status breakdown, POV characters |
| `v_feedback_queue` | Open feedback items with age, ordered by recency |
| `v_lore_coverage` | Which creative_context entries are referenced by drafts |
| `v_character_appearances` | Character frequency and role breakdown across drafts |

## Integration with Creative Identity

When the Creative Identity module is active:
- **Lore links** connect drafts to `creative_context` entries (characters, lore, canon, relationships)
- **Approved drafts** (status = `final`) can be fed into `writing_samples` for baseline updates
- **Narrative principles** should inform feedback — if a draft drifts from established principles, note it as a `critique`
- **Creative sessions** can reference which drafts were worked on via `scenes_worked`

## Presentation Guidelines

- Show draft structure as an indented outline when displaying a project's writing
- Word counts in human-readable format: "2.4k words" not "2,400"
- Show feedback counts inline: "Scene 3 (draft, 1.1k words, 2 open notes)"
- Lore contradictions should be surfaced prominently — these are continuity bugs
- When showing version history, show change summaries, not full diffs
