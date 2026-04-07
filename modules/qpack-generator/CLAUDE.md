# QPack Generator — Briefing for Review

## Context: What you're looking at

This is a prototype pipeline that generates **QPack** files — adaptive question bundles for a GUI layer on top of Software of You. It was built as a proof-of-concept for the SoY Desktop app that Alex and Kerry have been designing.

**The core idea:** SoY has a rich local SQLite database (contacts, emails, projects, tasks, calendar, decisions, commitments, transcripts) with pre-computed SQL views that already crunch the hard analytics. QPacks are a thin question layer on top — each question maps to SQL context queries + an optional LLM prompt template + a structured answer format. The GUI shows clickable questions. The user clicks, gets an answer, takes action. No terminal required.

**This connects directly to Kerry's BusinessBrain prototype.** BusinessBrain had QPacks (themed question bundles per department), pre-built clickable questions with prompt templates, expert personas per division, and a search-first layout with progressive disclosure. This pipeline takes that UX pattern and builds it on SoY's local data engine instead of Supabase/Pinecone/GPT-4.

## How to run it

The pipeline runs against the local SoY database. If you're on a machine with SoY installed:

```bash
# The one-command demo — generates QPacks, runs suggestions, executes questions, routes queries
python3 modules/qpack-generator/demo.py

# Individual commands:
python3 modules/qpack-generator/run.py generate     # Full pipeline
python3 modules/qpack-generator/run.py scan          # See data state
python3 modules/qpack-generator/run.py validate      # Test all SQL queries
python3 modules/qpack-generator/run.py execute       # List all questions
python3 modules/qpack-generator/run.py execute crm.cold_relationships  # Run one question

# Runtime layer:
python3 modules/qpack-generator/suggestions.py       # What should the home screen show?
python3 modules/qpack-generator/router.py "who should I focus on"  # NL → QPack routing
python3 modules/qpack-generator/serve.py              # HTTP API on :8788
```

If you're reviewing without a SoY database, the key files to read are:
1. `README.md` — architecture overview
2. `templates/crm.json` — example question template (shows the QPack format)
3. `templates/speed-to-lead.json` — extension concept showing how Kerry's gstack maps into QPacks
4. `examples/` — real formatted output JSON (the API contract a GUI would consume)
5. `pipeline.py` — the modular step engine
6. `formatter.py` — the 5 answer format renderers
7. `SOY-DESKTOP-VISION.md` — the full vision doc with build plan AND critical flags

## What to look at (priority order)

### 1. The question templates (`templates/*.json`)
These ARE the product. Each file defines questions for a SoY module with:
- `context_queries` — SQL against computed views (most questions are pure SQL, no LLM)
- `prompt_template` — for questions that need LLM synthesis
- `answer_format` — one of 5 types: `data_table`, `prioritized_list`, `summary_card`, `insight_synthesis`, `metric_snapshot`
- `data_requires` — minimum data thresholds (questions auto-hide when data is sparse)
- `persona` — expert persona per module (same pattern as BusinessBrain's CFO/CRO/CHRO personas)

**Key insight:** 60%+ of questions need zero LLM. They're instant SQL lookups formatted for display. The LLM is reserved for synthesis questions ("Who should I prioritize?", "Prep me for my next meeting").

**Extension concept:** Check `templates/speed-to-lead.json` — this shows how Kerry's gstack (the speed-to-lead AI lead responder) maps into the QPack system. Five questions covering response times, missed leads, conversion funnel, source analysis, and weekly performance. The pipeline auto-discovers it, but filters all 5 questions out because the `stl_leads` table doesn't exist yet. When speed-to-lead is installed as a SoY extension with its migration, those questions activate automatically. This is how every external product (Specsite, Story-Score, etc.) becomes part of the guided discovery experience.

### 2. The pipeline (`pipeline.py` + `steps/`)
Modular step architecture — each step is pluggable:
- `scan` — detects installed modules, data counts, view availability
- `template` — loads question JSON files
- `filter` — drops questions where underlying data is empty
- `validate` — tests every SQL query against the live database
- `adapt` — adjusts questions based on data richness (rich/moderate/sparse), injects onboarding questions for new users
- `deploy` — writes QPack JSON to `qpacks/`

Steps can be inserted, removed, replaced, or reordered at runtime. This is the same pipeline pattern used by Specsite (discover → harvest → generate → QA → deploy) and the ambient research module (Tier 1 → Tier 2 → Tier 3).

### 3. Smart suggestions (`suggestions.py`)
The algorithm that picks what the home screen shows. Checks in priority order:
1. Urgent nudges → "5 things need your attention"
2. Imminent meetings → "Prep for call with Jessica in 47m"
3. Email backlog → "78 emails need a reply"
4. Cold active contacts → "Haven't heard from Sarah in a while"
5. Overdue tasks → "3 overdue tasks"
6. Stalled projects → "3 projects have stalled"
7. Discovery candidates → "8 untracked emailers found"

Returns top 3 with icon, color, and data preview. Falls back to featured QPack questions if nothing urgent.

### 4. The answer formatter (`formatter.py`)
Takes raw query results and structures them into GUI-ready JSON. Five formats:
- `data_table` — auto-detects column types, generates sortable table metadata
- `prioritized_list` — ranked items with badges and per-item actions
- `summary_card` — stats grid + narrative for entity deep-dives
- `insight_synthesis` — the BusinessBrain three-part card (findings/insights/actions in blue/amber/green)
- `metric_snapshot` — big number + trend + breakdown

Check the `examples/` directory for real output from each format.

### 5. The keyword router (`router.py`)
Maps natural language to QPack questions:
- Layer 1: Entity name match (detects contact/project names from the DB)
- Layer 2: Module keyword scoring
- Layer 3: Question label fuzzy matching

### 6. HTTP API (`serve.py`)
Serves everything over HTTP for frontend consumption. Six endpoints covering QPack listing, execution, routing, suggestions, and health.

## What this doesn't do (yet)

- **No GUI** — this is the backend/API layer. The Tauri/React shell that renders QPacks as clickable cards is the next phase.
- **LLM execution is optional** — works fully without Ollama. Questions that need LLM gracefully degrade to showing raw data.
- **No auto-generated questions (yet)** — templates are currently hand-authored. See "The Generator Vision" below for where this is headed.
- **No cost tracking** — local LLM usage isn't metered. Cloud API budgeting would come with the Pro tier.

## The Generator Vision: Why This Is Called a "Generator"

The pipeline today loads hand-authored question templates. But the architecture is designed for something bigger.

**The Specsite pipeline** (Alex's website generation system) follows this pattern:
1. **Discover** businesses via Google Places
2. **Harvest** their reviews, photos, hours
3. **Generate** a website from that data
4. **QA** via Lighthouse + screenshots
5. **Deploy** to Cloudflare

**The QPack generator follows the same pattern**, pointed inward at SoY's own data:
1. **Discover** — scan installed modules, detect computed views, check what columns exist, what data is populated
2. **Harvest** — sample data shapes, understand relationships between tables, detect which views have meaningful content
3. **Generate** — today this is "load a template." The next evolution: a local LLM examines the schema + sample data and proposes candidate questions with SQL context queries. "This view has `days_silent` and `active_projects` columns — a useful question would be 'Which active clients haven't heard from me?'"
4. **QA** — validate the generated SQL runs, check it returns data, score the question for relevance (the validate + filter + health steps already do this)
5. **Deploy** — approved questions get written to QPack JSON alongside hand-authored ones

**This is what makes "infinitely adaptable" real instead of marketing.** When Kerry installs speed-to-lead and creates the `stl_leads` table, the pipeline wouldn't just activate pre-written templates — it would discover the new table, understand its columns (`received_at`, `first_response_at`, `status`, `source`), and generate questions like "What's my fastest response time?" or "Which source has the most leads?" without anyone writing a template.

**The pipeline is modular for exactly this reason.** Adding the LLM generation step is:
```python
p.insert_after("template", LLMGenerateStep())
```
The validation, filtering, and adaptation steps downstream catch any bad SQL the LLM produces. The hand-authored templates remain the trusted baseline. Generated questions are additive.

**What's built today:** The framework that makes this possible — modular steps, schema scanning, query validation, data-state adaptation. The generation step is the next piece to plug in.

## Included: The full vision doc

`SOY-DESKTOP-VISION.md` contains the complete SoY Desktop vision document — the build plan, the QPack system design, the BusinessBrain connection, AND the critical flags (cold start problem, no-GPU dead zone, prerequisite death spiral, Apple Intelligence threat, revenue reality). It's the balanced view — both the pitch and the honest risk assessment. Read this for the strategic context around why this pipeline exists.

## How this connects to the SoY Desktop vision

```
This pipeline (QPack Generator)
    ↓ produces
QPack JSON files (question catalog)
    ↓ consumed by
Tauri/React shell (menu bar app)
    ├── Home screen shows Smart Suggestions
    ├── Command bar routes queries via Keyword Router
    ├── Module pages show contextual QPack questions
    └── Clicking a question → Execute → Format → Render answer card
```

The pipeline also feeds the extension ecosystem. When a new module (Specsite, Speed-to-Lead, Story-Score) ships a template JSON in its directory, the pipeline discovers it automatically and generates QPacks for it.

## Questions for discussion

1. **Speed-to-Lead integration**: Check `templates/speed-to-lead.json`. This maps 5 gstack questions into the QPack system (response times, missed leads, funnel, sources, weekly performance). Does this capture what matters for the lead response product? What questions are missing? The `stl_leads` schema assumed there is a starting point — does it match what the gstack repo actually stores?

2. **BusinessBrain port**: The `insight_synthesis` format is directly inspired by BusinessBrain's blue/amber/green cards. Kerry — does this capture what worked in that prototype? What UX details from BusinessBrain are we missing?

3. **Answer format count**: We have 5 formats. A review panel suggested shipping 2 (table + card) and letting others emerge from actual GUI needs. Thoughts on which 2 matter most?

4. **Persona system**: Each module has an expert persona (Relationship Advisor, Project Strategist, Lead Response Analyst, etc.) — same pattern as BusinessBrain's CFO/CRO. Are these calibrated right for SoY's audience?

5. **Extension delivery**: The speed-to-lead template sits in `templates/` alongside core modules. For the real extension system, these would live in `leaves/speed-to-lead/qpacks/`. The pipeline already scans both locations. Is the `templates/` → `leaves/` migration path clean enough?

6. **The pipeline-as-QPack-generator insight**: Alex's observation that the Specsite pipeline (scan → harvest → generate → QA → deploy) can produce QPacks as an output format, making question generation data-adaptive rather than static. The pipeline already adapts based on data state — the next step would be LLM-generated questions from schema discovery. Worth exploring or premature?

7. **The cost question**: 60%+ of questions are pure SQL (zero cost). LLM questions route to local Ollama first. But the "Who should I prioritize?" synthesis question is the one that sells the product — and it needs a decent model. What's the minimum viable model for synthesis questions? Is Mistral 7B good enough, or does it need 14B+?

8. **What's the first demo?**: If we were showing this to a potential user next week, which 3 questions would we demo? What data state do we need to make it impressive?
