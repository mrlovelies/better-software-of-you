# Export Workflow: HTML Views → Google Docs

## Overview

SoY generates HTML views (dashboards, entity pages, project briefs, etc.) in the `output/` directory. The Google Docs module can export these to Google Docs for sharing.

## Workflow

1. **Generate the view** — run the relevant command (`/dashboard`, `/entity-page`, `/project-brief`, etc.)
2. **Extract text content** — read the generated HTML file from `output/`, strip HTML tags, preserve structure (headers, lists, tables as text)
3. **Create the doc** — use `docs` tool with `action: "export"`, providing title and extracted content
4. **Link to entity** — if the export is for a contact or project, include `contact_id` or `project_id`

## Content Conversion Notes

- **Headers:** Convert `<h1>` through `<h6>` to lines prefixed with `#` markers or use paragraph style requests after insertion
- **Tables:** Convert to tab-separated or aligned text. Google Docs API supports table creation but it's complex — plain text tables are usually sufficient for exports.
- **Lists:** Convert `<ul>/<ol>` to lines prefixed with `- ` or `1. `
- **Bold/Italic:** These can be applied via `updateTextStyle` batch requests after text insertion
- **Links:** Include URLs inline as `text (url)` — the Docs API can create hyperlinks but plain text is simpler

## Suggested Title Conventions

- Entity pages: `"SoY: {Contact Name} — Intelligence Brief"`
- Project briefs: `"SoY: {Project Name} — Project Brief"`
- Dashboards: `"SoY: Dashboard — {date}"`
- General exports: `"SoY: {descriptive title}"`

## Linking

Always set `doc_type = 'export'` for exported views. This distinguishes them from manually created documents in the `google_docs` table.
