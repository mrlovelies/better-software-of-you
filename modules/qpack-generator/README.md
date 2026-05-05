# QPack Generator

**Adaptive question bundles for the SoY GUI layer.**

## What is a QPack?

A QPack is a JSON file containing pre-built clickable questions for a SoY module. Each question has:

- **Label** — what the user sees ("Who should I prioritize this week?")
- **Context queries** — SQL against SoY's computed views that fetch the data needed to answer
- **Answer format** — how to render the result (table, ranked list, stats card, synthesis)
- **LLM config** — whether the question needs a language model for synthesis, or is pure data

```
User clicks "Which relationships are going cold?"
    → Pipeline runs SQL: SELECT * FROM v_contact_health WHERE days_silent > 21
    → Formats as data_table with column metadata
    → GUI renders a sortable table with clickable contact names
    → No LLM needed. Instant.
```

60%+ of QPack questions are pure SQL — no LLM, sub-second response. The remaining questions use local models for synthesis ("Who should I prioritize?" requires reasoning across nudges, calendar, emails, and contact health).

## How the pipeline works

```
scan → template → filter → validate → adapt → deploy
 │        │         │          │         │        │
 │        │         │          │         │        └─ Write QPack JSON to qpacks/
 │        │         │          │         └─ Inject onboarding Qs for sparse data
 │        │         │          └─ Test all SQL queries against live DB
 │        │         └─ Drop Qs where data is empty
 │        └─ Load question templates per module
 └─ Detect installed modules, data counts, view availability
```

The pipeline is **modular** — steps can be added, removed, reordered, or replaced:

```python
from pipeline import Pipeline
from steps import ScanStep, TemplateStep, FilterStep, ValidateStep, AdaptStep, DeployStep

p = Pipeline([ScanStep(), TemplateStep(), FilterStep(), ValidateStep(), AdaptStep(), DeployStep()])
p.insert_before("validate", MyCustomStep())  # Add a step
p.remove("adapt")                             # Remove a step
p.run()
```

## Quick start

```bash
# Generate QPacks from your current data
python3 modules/qpack-generator/run.py generate

# See your data state and available templates
python3 modules/qpack-generator/run.py scan

# Validate all context queries without deploying
python3 modules/qpack-generator/run.py validate

# Execute a specific question against live data
python3 modules/qpack-generator/run.py execute crm.cold_relationships

# List all available questions
python3 modules/qpack-generator/run.py execute

# Run health check on generated QPacks
python3 modules/qpack-generator/steps/health.py

# Start the HTTP API server
python3 modules/qpack-generator/serve.py

# Get smart suggestions (what should the home screen show?)
python3 modules/qpack-generator/suggestions.py

# Route a natural language query to a QPack question
python3 modules/qpack-generator/router.py "who should I focus on"
```

## The runtime layer

Beyond generation, the pipeline includes:

- **Smart Suggestions** (`suggestions.py`) — Computes the 3 most relevant questions based on actual data state. Urgent nudges → email queue → stalled projects → untracked contacts. This is what the home screen shows.
- **Keyword Router** (`router.py`) — Maps natural language to QPack questions. Entity name detection → keyword scoring → fuzzy label matching.
- **Answer Formatter** (`formatter.py`) — Structures raw query results into 5 GUI-ready JSON formats: `data_table`, `prioritized_list`, `summary_card`, `insight_synthesis`, `metric_snapshot`.
- **Health Check** (`steps/health.py`) — Detects schema drift, empty data, stale QPacks, missing templates.
- **HTTP API** (`serve.py`) — Serves everything over HTTP on port 8788.

## API endpoints

```
GET  /api/qpacks              — List all QPack files with metadata
GET  /api/qpacks/{module}     — Get a specific QPack
GET  /api/suggestions         — Smart suggestions for home screen
POST /api/qpacks/execute      — Execute a question: {"question_id": "crm.cold_relationships"}
POST /api/qpacks/route        — Route a query: {"query": "who should I focus on"}
GET  /api/qpacks/health       — Health check report
```

## Data-adaptive behavior

The pipeline detects data richness and adapts:

| Tier | Threshold | Behavior |
|------|-----------|----------|
| **Rich** | 10+ contacts, 50+ emails | Full question set, all featured questions active |
| **Moderate** | 3+ contacts, 10+ emails | Most questions active, some onboarding hints |
| **Sparse** | 1+ contacts, any emails | Onboarding questions injected ("Connect Gmail to get started"), data-dependent questions demoted |
| **Empty** | 0 contacts | Full onboarding mode — all questions replaced with setup guidance |

Questions that would produce empty answers are filtered out. The user never sees a question that returns "no results."

## Question templates

Templates live in `templates/` as JSON files — one per module. Each template declares:
- A persona (expert name, tone, system prompt)
- Questions with context queries, answer formats, and data requirements

Current templates:

| Module | Questions | LLM Required | Notes |
|--------|-----------|-------------|-------|
| CRM | 5 | 1 (priority ranking) | Core contacts/relationship intelligence |
| Projects | 5 | 1 (next action) | Task tracking, project health |
| Email | 4 | 0 | Response queue, contact threads, discovery |
| Calendar | 3 | 1 (meeting prep) | Schedule, prep briefs |
| Nudges | 5 | 0 | Unified attention feed across all modules |
| Decisions | 3 | 0 | Decision log with outcome tracking |
| **Speed-to-Lead** | **5** | **2** | **Extension concept — Kerry's gstack lead response agent** |

The Speed-to-Lead template demonstrates how external products become SoY extensions. Its questions (response times, missed leads, conversion funnel, source analysis, weekly performance) are filtered out when the `stl_leads` table doesn't exist. When the extension is installed and migrated, the pipeline automatically activates those questions on the next run. Zero config change needed.

Adding a new module's QPack: drop a JSON file in `templates/`, run `generate`. The pipeline picks it up automatically.

## Answer formats

| Format | Used For | Example |
|--------|---------|---------|
| `data_table` | Lists, lookups, search results | Contacts going cold, emails needing reply |
| `prioritized_list` | Ranked recommendations | Who to focus on, what to work on next |
| `summary_card` | Entity deep-dives | Relationship health, project status |
| `insight_synthesis` | LLM-synthesized answers | Meeting prep, journal patterns |
| `metric_snapshot` | Quick stats | Email queue count, meeting hours |

## Architecture

```
templates/*.json          Question definitions (source of truth)
    ↓
pipeline (run.py)         Scan → Filter → Validate → Adapt → Deploy
    ↓
qpacks/*.qpack.json       Generated QPack files (consumed by GUI)
    ↓
serve.py                  HTTP API for frontend consumption
suggestions.py            Smart home screen suggestions
router.py                 Natural language → QPack question matching
formatter.py              Raw data → structured GUI-ready JSON
```

The pipeline reads from SoY's computed SQL views (`v_contact_health`, `v_nudge_items`, `v_project_health`, etc.) — these are the same views that power the existing CLI and dashboard. QPacks are a thin question layer on top of data that's already computed.

## Connection to the bigger picture

This pipeline is one piece of the SoY Desktop vision:

1. **QPack Generator** (this) → produces the question catalog
2. **Tauri/React shell** → renders QPacks as clickable cards
3. **Command bar** → routes typed queries via the keyword router
4. **Menu bar dropdown** → shows smart suggestions
5. **Extension system** → third-party modules ship their own QPack templates

The pipeline pattern (scan → evaluate → generate → validate → deploy) is the same one used by Specsite for website generation and ambient-research for intelligence gathering. QPacks are a new output format for a proven pipeline architecture.

## Where this is headed: self-generating questions

Today the pipeline loads hand-authored templates. The architecture is designed for the next step: **the pipeline generates its own questions from schema discovery.**

```
Specsite:  discover businesses → harvest data → generate website → QA → deploy
QPack:     discover schema    → harvest data  → generate questions → QA → deploy
```

When a new extension installs and creates tables, the pipeline would scan the new schema, understand the columns and relationships, and propose candidate questions with SQL context queries — without anyone writing a template. The existing validation, filtering, and health steps catch bad SQL before it ships. Hand-authored templates remain the trusted baseline; generated questions are additive.

The pipeline is modular for this reason: `p.insert_after("template", LLMGenerateStep())` and it slots in.
