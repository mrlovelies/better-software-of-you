# SoY Desktop: Vision, Build Plan & Critical Flags

**April 7, 2026 — Alex & Kerry working document**

---

## The Core Thesis

SoY today is powerful but inaccessible. The people who'd benefit most — freelancers drowning in client relationships, consultants who lose track of commitments, agency owners juggling fifteen things — are exactly the people who'll never touch a terminal.

The answer isn't a friendlier terminal. It's a GUI that handles the known workflows with buttons and guided flows, with natural language available for power users who want it. The terminal requires the user to translate their intent into words. The GUI eliminates that translation step entirely.

Kerry's BusinessBrain prototype proved this pattern works: **dashboard + search bar + pre-built clickable questions**. The user doesn't need to know what to ask — the system shows them the questions that matter, computed from their actual data. Click, get an answer, take action.

---

## What We're Building

A native Mac menu bar app (Tauri 2.x) that wraps SoY's existing React hub and data layer, adding:

### The QPack System

Every module ships a set of pre-built clickable questions — we're calling them **QPacks**, borrowed from BusinessBrain. Each question is a full pipeline: SQL context queries against computed views, a prompt template, an answer format, and action buttons.

**~50 questions across 8 core modules.** Over 60% don't need an LLM at all — they're direct SQL against computed views, rendered as cards. Instant.

| Module | Example Featured Questions |
|--------|--------------------------|
| **CRM** | Who should I prioritize this week? / Which relationships are going cold? / Who haven't I talked to in a month? |
| **Projects** | What's overdue across all projects? / How is {project} tracking? / What should I work on next? |
| **Email** | What emails need my reply? / What did {contact} email me about? |
| **Calendar** | What's on today? / Prep me for my next meeting / What's my week look like? |
| **Nudges** | What needs my attention right now? / What did I let slip this week? |
| **Decisions** | What decisions are pending outcomes? / Show decisions I should revisit |
| **Journal** | What patterns show up in my journal? / How has my mood been this month? |
| **Conversations** | What commitments came out of my last call with {contact}? / What's my talk-to-listen ratio? |

Extensions ship their own QPacks — Specsite gets "How is my pipeline looking?", Speed-to-Lead gets "What's my average response time to leads?", etc. The app discovers all QPack files at startup.

### Five Answer Formats

| Format | Used For | Visual |
|--------|---------|--------|
| **data_table** | Lists, search results, entity lookups | Sortable table with clickable entity links |
| **prioritized_list** | "Who to focus on", ranked recommendations | Cards with per-item action buttons |
| **summary_card** | Entity deep-dives ("How's my relationship with Sarah?") | Stats grid + narrative section |
| **insight_synthesis** | LLM-synthesized answers (the BusinessBrain pattern) | Blue (findings) / Amber (insights) / Green (actions) |
| **metric_snapshot** | Quick numbers ("How much meeting time this week?") | Big number + trend + breakdown |

### Dynamic Smart Suggestions

The home screen shows 3 cards computed from the user's actual data state — not static featured questions:

- Urgent nudges exist → "5 things need your attention" (red)
- Meeting within 2 hours → "Prep for call with Jessica in 47m" (blue)
- Emails awaiting reply → "3 emails waiting for a reply" (amber)
- Active contact going cold → "Haven't heard from Sarah in a while"
- Overdue tasks → "4 overdue tasks across projects" (red)

Falls back to featured QPack questions if nothing urgent.

### Contextual Questions on Entity Pages

Contact page for Jessica Martin:
```
Quick Questions
  How is my relationship with Jessica?     [->]
  What do I owe Jessica?                   [->]
  When did we last talk?                   [->]
```

Project page for The Grow App:
```
Quick Questions
  How is The Grow App tracking?            [->]
  What's overdue on this project?          [->]
  What decisions have I made about it?     [->]
```

### The Menu Bar Experience

- **Icon** with three states: dormant (monochrome), active (pulse during sync), attention (colored dot for urgent items)
- **Dropdown** with three zones: Glance (next meeting + nudge counts), Feed (urgent/soon nudge items, max 8), Quick Actions (New Note, Log Interaction, Search)
- **Main window** via click-through or `Cmd+Shift+Space`

### Command Bar (Dual Mode)

Persistent at the top of every screen. `Cmd+K` to focus.

- **Search mode** (default): typing filters across contacts, projects, emails, notes, transcripts, decisions. Spotlight-style grouped results.
- **NL mode** (triggered by `?` or Tab): question goes to QPack router or LLM. Answer appears in collapsible panel below the bar.

### Adaptive LLM Backend

Setup wizard detects hardware and configures the best backend:
- Local models via Ollama (automated setup if GPU detected)
- Claude API key
- OpenAI-compatible API key
- Claude subscription auth

Three-layer query routing:
1. **Pattern match** (0ms) — ~30 regex patterns covering known intents → direct SQL
2. **Heuristic** (0-5ms) — structural analysis for ambiguous queries
3. **LLM fallback** (<500ms) — local model disambiguates when layers 1-2 fail

Most SoY queries never need an LLM. Computed views are the retrieval layer.

### Extension Ecosystem

Existing 21 modules formalized into extension manifests. Extensions declare: tables, UI surfaces (sidebar, dashboard cards, entity panels), QPacks, events emitted/consumed, LLM requirements. Adjacent tools (Specsite, Speed-to-Lead, Story-Score) become extensions that communicate through the events bus — no direct coupling.

### Privacy Tiers

| Tier | LLM | Data | Google |
|------|-----|------|--------|
| **Fortress** | Local only | Never leaves machine | Disconnected |
| **Cloud-Assisted** | Local routine + cloud complex | SQLite local, query context sent to API | Connected, stored locally |
| **Cloud Sync** (future) | Via SoY backend | Cloud DB | Via backend |

Radical transparency: show users exactly where data goes, let them choose, make the audit trail accessible.

---

## How BusinessBrain Informs This

| Port the Pattern | Rebuild Differently | Skip Entirely |
|-----------------|--------------------|--------------| 
| QPack card layout (icon + question + arrow) | Static questions → dynamic JSON loaded at startup | Document upload + chunking + Pinecone |
| Three-part answer (blue/amber/green) | Pinecone vector search → SQL computed views | Supabase cloud backend |
| Progressive disclosure ("More Ideas") | Simple keyword routing → three-layer classification | Next.js SSR |
| Search-first home layout | Static division sidebar → module-aware sidebar | Ecommerce/HR QPacks (wrong domain) |
| Expert persona per division | GPT-4 only → adaptive multi-tier LLM | Google Analytics connector |

The spirit: dashboard + search + guided questions + structured answers. The engine: SoY's local SQLite, computed views, and adaptive LLM routing instead of BusinessBrain's cloud stack.

---

## The Build Sequence

| Week | Alex (Infrastructure) | Kerry (UX/Frontend) |
|------|----------------------|-------------------|
| 1 | Tauri shell wrapping React hub, Rust SQLite reader, menu bar icon | Command bar component, smart suggestion card design, answer card wireframes |
| 2 | Keyword router, `/api/suggestions` + `/api/qpacks/execute` endpoints | CommandBar component (dual-mode), smart suggestion cards on home screen |
| 3 | All 8 QPack JSON files, QPack loader, context query pipeline | All 5 answer format renderers, entity links, action buttons |
| 4 | Contextual questions API, entity name detection in router | Quick Questions on contact/project views, progressive disclosure |
| 5 | Three-state menu bar icon, dropdown (Glance/Feed/Quick Actions) | Menu bar dropdown component, full flow testing |
| 6 | **Dogfood week. Fix what breaks.** | **Dogfood week. Polish. Start outreach to 10 freelancers.** |
| 7 | Ollama client in Rust, Layer 2+3 router, LLM-backed QPacks | "Thinking" animation, insight_synthesis renderer, confidence badges |
| 8 | Claude API integration, budget enforcement, entity pseudonymization | Privacy indicator UI, LLM settings panel, budget display |
| 9 | Extension QPack loader, Specsite + Speed-to-Lead QPacks | Extension UI in sidebar, extension settings |
| 10 | "Draft reply" action → Gmail, action execution pipeline | Email draft composer, action confirmation modal |
| 11 | Licensing, free/Pro/BYO Key tiers | Subscription flow, paywall touchpoints |
| 12 | **DMG packaging, code signing, auto-update** | **Screenshots, demo video, website. Ship to beta.** |

**Critical path to demo: Weeks 1-3.** Someone opens the app, sees 3 dynamic suggestions, types "who should I prioritize" and gets a ranked list from their own data in ~200ms with zero LLM calls.

### Pricing

| Tier | Price | Includes |
|------|-------|---------|
| **Free / Local** | $0 | Full app, local LLM, core modules, 1 extension |
| **Pro** | $12/mo | Cloud LLM routing, Gmail/Calendar sync, unlimited extensions, transcripts, coaching |
| **Pro+ (BYO Key)** | $8/mo | Everything in Pro, user provides own API key |

---

## Critical Flags

Everything above is the vision. Below is what could kill it. Both sides need to be on the table.

### Flag 1: The Prerequisite Problem

The whole pitch is "remove the terminal barrier." But the current architecture requires Claude Code, which requires an Anthropic account, a terminal install, and possibly Xcode command line tools. **The product that removes the terminal barrier has a terminal prerequisite.**

The QPack system solves this in theory — 85% of queries are SQL, no LLM needed. But that system is the proposed architecture, not the current one. Until it's built, the app can't function without Claude Code.

**What this means:** The QPack engine isn't a nice-to-have layer on top of Claude Code. It's the foundational requirement that makes the desktop app viable for non-technical users. It needs to be the first thing built, not the third.

### Flag 2: The Cold Start Is Brutal

SoY with 2 contacts and no synced emails is an empty dashboard. The relationship scoring, nudge engine, and cross-referencing that make SoY special require **2-4 weeks of accumulated data** before anything interesting surfaces.

A new user opens it on Day 2, sees the same near-empty dashboard, and doesn't open it on Day 3.

**Proposed fix:** Make Gmail OAuth the FIRST onboarding step, not contact entry. Import contacts FROM email history. Analyze email frequency to auto-populate the CRM with the top 20 people the user actually communicates with. The system populates itself instead of asking the user to do tedious data entry.

This one change could compress time-to-first-value from weeks to hours.

### Flag 3: The "No GPU, No API Key" Dead Zone

The free local tier doesn't work on a MacBook Air with 8GB RAM. Local models need ~5GB for inference. Without any LLM backend, the high-value questions ("Who should I prioritize?", "Prep me for my meeting") don't work. SoY becomes a SQLite browser with a nice skin.

The hidden cost floor is ~$20/mo (Claude subscription) on top of the $12/mo Pro tier. That's $32/mo for a personal CRM. HubSpot free does contact management and email tracking with zero setup.

**What this means for pricing:** The free tier needs to be genuinely useful without any LLM. The 60%+ of QPack questions that are pure SQL need to deliver real value on their own — not feel like a crippled version of the paid product. The free tier is the CRM with nice views and guided questions. The paid tier adds the intelligence layer (synthesis, coaching, drafts, meeting prep). If the free tier isn't good enough to create habit, nobody upgrades.

**Possible solution:** Offer a small cloud LLM allocation on the free tier — something like 50 cloud queries/month. Enough to experience the "holy shit" moment of a meeting prep brief, not enough to use daily. This is the Raycast model: generous free tier that hooks you, paid tier for daily use.

### Flag 4: The Maintenance Surface

50 QPack questions, each with SQL context queries referencing specific computed views. 57 migrations. 21 modules. 5 answer renderers. A keyword router. Extension manifests. Each QPack query is a hidden contract with the database schema — when a migration changes a view, every QPack touching that view needs testing.

Two people building, maintaining, AND marketing this is tight. Something will be neglected.

**Mitigation:** Ruthless scoping. Ship with 3 modules (CRM, Calendar, Nudges) and 15-20 QPack questions, not 8 modules and 50 questions. Expand when the core is solid and users are asking for more. The extension system means modules can be added without touching the core.

### Flag 5: "Infinitely Adaptable" vs. "50 Pre-Built Questions"

SoY via Claude Code IS infinitely adaptable — Claude reads the full schema, writes arbitrary SQL, generates ad-hoc views for any question. The QPack system replaces this with a finite set of pre-built interactions. When a user asks something not covered by any QPack, the system either falls through to an LLM (which they may not have) or can't help.

**This is a capability downgrade traded for an accessibility upgrade.** That's the right tradeoff for the target audience, but it needs to be acknowledged in how the product is positioned. The command bar with NL mode is the escape hatch — it should always be available and clearly signposted when a QPack doesn't match.

The honest positioning: "SoY handles the questions that matter most out of the box. For everything else, ask in your own words."

### Flag 6: Apple Intelligence Is Coming

macOS will natively provide email summaries, calendar-aware suggestions, contact intelligence, and proactive notifications — for free, with zero setup. It won't have SoY's relationship scoring or commitment tracking, but it covers the "prep me for my meeting" and "summarize Sarah's emails" gateway use cases.

**What we have that Apple doesn't:** The cross-referenced data graph. Apple Intelligence can summarize emails but can't connect them to your CRM notes, project commitments, call transcripts, and decision journal. The unified intelligence layer is the moat. We need to lead with that in positioning, not with "AI summaries" which Apple will commoditize.

### Flag 7: The Revenue Reality

Getting to 100 paying users in 12 months requires ~1,000 installs with 10% conversion. Getting 1,000 installs of a niche Mac-only personal CRM requires visibility that two developers don't naturally have.

The realistic path to the first 100 users:
- Kerry's Claude Code course as the warm funnel
- A Show HN post framed around local-first adaptive LLM architecture
- Every BetterStory consulting client as a potential SoY user
- Content marketing about workflows, not the product

The milestone that matters at 6 months isn't revenue — it's **retention**. If 10 people use it daily for 3+ months, the product works. If they churn after the novelty, no amount of marketing fixes that.

---

## The Strategic Question

There are two paths forward:

### Path A: Desktop App (the build plan above)
- Strongest for privacy story and local-first positioning
- Hardest onboarding (requires Claude Code or local LLM setup)
- Best long-term architecture (extension ecosystem, multi-tier LLM)
- Slower to first paying user (12+ weeks to MVP)
- Audience: technical-adjacent professionals who care about data ownership

### Path B: Web App (SaaS-first)
- No install, no prerequisites, sign up and go
- Gmail OAuth in the browser (no scary "unverified app" warning if we get Google verification)
- Fastest time to first value (connect Google → system auto-populates → guided questions immediately)
- Cloud LLM included in subscription (no "bring your own")
- Privacy story is weaker but honest ("your data is encrypted, here's exactly where it lives")
- Audience: any freelancer/consultant with a browser

### Path C: Both, Staged
- Build the QPack engine and answer renderers as the **core** — framework-agnostic
- Ship as a local web UI first (soy_server.py serves the React app, no Tauri, no Rust, no code signing)
- Validate the UX pattern with 10-20 users in weeks, not months
- THEN decide whether the shell is a desktop app, a SaaS, or both — based on what users actually want

Path C is the fastest to validation. It tests the QPack hypothesis without committing to Tauri/Rust. If guided questions on local data resonate, invest in the native shell. If they don't, you've saved months.

---

## What We Agree On

Regardless of which path: the QPack pattern is the product. Dashboard + search + guided questions + structured answers + action buttons. BusinessBrain proved it works for business data. SoY's computed views and cross-referenced data graph make it work for personal professional data. The LLM backend is a config choice, not a commitment. The GUI is the product. SoY is the engine.

The question isn't whether to build this. It's what to build first.
