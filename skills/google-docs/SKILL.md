---
name: google-docs
description: Use when creating, reading, editing, or exporting Google Docs. Provides formatting patterns, export workflows, and cross-module linking guidance.
version: 1.0.0
---

# Google Docs

This skill provides reference material for the Google Docs module — document creation, content management, and export workflows.

## When to Use

- Creating a new Google Doc (`/docs create`)
- Reading or editing existing documents
- Exporting generated views (dashboards, entity pages, briefs) to Google Docs
- Linking documents to contacts or projects
- Any operation involving the Google Docs API

## Key References

- `references/api-patterns.md` — Google Docs API request patterns, batch updates, text insertion/deletion
- `references/export-workflow.md` — How to convert HTML views to Google Docs content

## Core Principles

1. **Track everything locally.** Every doc created or accessed gets a row in `google_docs`. This enables cross-referencing with contacts, projects, and activity logs.
2. **Plain text for content, not HTML.** The Google Docs API works with plain text and structural requests (bold, headers, etc.), not raw HTML. When exporting HTML views, extract the text content first.
3. **Link documents to entities.** When a doc is created in the context of a contact or project, always set the foreign key. The connections are the value.
4. **Log activity.** All create/edit operations log to `activity_log` with `entity_type = 'google_doc'`.
5. **Scope awareness.** The module requires `documents` (read/write) and `drive.file` scopes. If operations fail with 403, the user needs to re-auth via `/google-setup`.
