# Signal Harvester

**Project:** Automated demand-discovery and solution pipeline
**Status:** Active — Harvester + Triage pipeline tested end-to-end, Paperclip running on Razer
**Collaborators:** Alex Somerville, Kerry Morrison
**Created:** 2026-03-26

---

## Vision

Automate the entire loop from **demand discovery** to **shipped product**:

```
Harvest signals → Triage for viability → Scope & dispatch build → Ship → $$$
     ↑                                                              |
     └──────────── more signal from more users ←────────────────────┘
```

People shout their problems into the void every day — Reddit, HN, Twitter, Facebook groups, Fiverr, Upwork. Instead of manually reading threads looking for ideas, we programmatically harvest pain-point signals, filter them for real market viability, and feed the winners into an agent-powered build pipeline.

**What makes this different from "build what Reddit wants":**
- It's not a side project idea generator. It's an end-to-end pipeline with agent orchestration.
- The harvest concept is universal — point it at any definable target and it finds signals.
- Solutions ship as SoY leaf packages (platform modules) or standalone micro-products.
- Revenue from shipped solutions generates more user activity, which generates more signal.

## Architecture

### Stage 1: Harvest
**Status: MVP working ✓**

`shared/signal_harvester.py` — Python script that hits Reddit's public JSON endpoints with pain-point search queries. Regex patterns catch expressions like:
- "I wish there was..."
- "Why isn't there..."
- "Someone should build..."
- "I'd pay for..."
- "Is there an app that..."
- "I'm so tired/frustrated of..."

Also supports **targeted harvesting** — define any topic and it generates targeted queries for that domain.

**Usage:**
```bash
# General harvest across default subreddits
python3 shared/signal_harvester.py harvest

# Global search only (faster, broader)
python3 shared/signal_harvester.py harvest --global-only --limit 25

# Targeted harvest for a specific domain
python3 shared/signal_harvester.py harvest --target="freelance invoicing"

# View harvested signals
python3 shared/signal_harvester.py signals --min-upvotes 50

# Stats
python3 shared/signal_harvester.py stats
```

**Planned expansions:**
- Hacker News (Algolia API — free, no auth)
- Twitter/X (requires API key or computer use)
- Fiverr/Upwork gap analysis (computer use — Kerry's proven this works)
- Facebook groups, niche forums (computer use for walled gardens)
- Product Hunt "needs" and "alternatives wanted" threads

### Stage 2: Triage
**Status: Working ✓ — Multi-LLM tiered evaluation**

`shared/signal_triage.py` — Three-tier LLM evaluation pipeline:

| Tier | Model | Machine | Cost | Role |
|------|-------|---------|------|------|
| 1 | Mistral 7B | Razer | $0 | Binary noise filter — "is this a product pain point?" |
| 2 | Qwen 14B | Razer | $0 | Market viability scoring — structured JSON, 5 dimensions |
| 3 | Claude API | Cloud | ~$0.01-0.05/signal | Final synthesis, spec outline (not yet wired) |

**Scoring dimensions (Tier 2):**
1. **Market size** (1-10) — weighted 25%
2. **Monetizability** (1-10) — weighted 30%
3. **Build complexity** (1-10, 10=trivial) — weighted 15%
4. **Existing solutions gap** (1-10, 10=unserved) — weighted 20%
5. **SoY leaf fit** (1-10) — weighted 10%

**Human-in-the-loop approval gate** before anything moves to build.

**Usage:**
```bash
# Run full pipeline: filter → score → review
OLLAMA_HOST=http://100.91.234.67:11434 python3 shared/signal_triage.py pipeline

# Or run tiers individually
python3 shared/signal_triage.py filter --limit 50
python3 shared/signal_triage.py score --limit 20
python3 shared/signal_triage.py review

# Human decisions
python3 shared/signal_triage.py approve 42 --notes "Good fit for SoY leaf"
python3 shared/signal_triage.py reject 43 "Too niche, already solved by Notion"
```

### Stage 2b: Forecast (Creative Ideation)
**Status: Working ✓**

`shared/signal_forecast.py` — LLM-powered creative product ideation that generates ideas NOT directly signalled by users. "Sometimes the lack of noise IS the signal."

**Forecasting modes:**
- **pattern** — meta-trends from approved signals
- **silence** — gaps where problems exist but nobody's asking
- **adjacent** — problems next to solved ones
- **upstream** — root causes behind symptom clusters
- **automate** — manual processes ripe for full automation
- **creative** — all modes combined

**Key differentiator: not software-only.** The forecaster considers:
- Physical products via automated supply chains (print-on-demand, white-label, dropship)
- Service arbitrage — chaining existing APIs into new offerings
- Hybrid digital/physical solutions
- Data products, bots, automated agencies

**Autonomy scoring** on every idea — setup, operation, support, maintenance. We want products that run themselves.

**Usage:**
```bash
python3 shared/signal_forecast.py generate --mode creative --count 5
python3 shared/signal_forecast.py list --min-autonomy 7
python3 shared/signal_forecast.py approve <id>
```

### Stage 2c: Evolution (Self-Improvement)
**Status: Working ✓**

`shared/signal_evolution.py` — Tracks performance at every stage and adapts:
- Which queries/subreddits produce viable signals (amplify winners, prune losers)
- How well LLM triage aligns with human decisions (recalibrate)
- Which industries produce shippable products (focus effort)
- LLM-generated new search queries from successful signal patterns
- LLM-suggested new subreddits from hot industries

Current triage accuracy: **38%** (5/13) — the system is calibrating. As more human decisions flow through, scoring weights will adjust.

### Stage 2d: Competitive Intelligence
**Status: Working ✓**

`shared/competitive_intel.py` — Separate harvest pipeline targeting NAMED products being trashed. "Make the same thing, but better."

**What it finds:**
- Products people are switching away from
- Subscription services with bait-and-switch complaints
- Products with declining quality (including physical goods)
- Abandoned products with orphaned user bases
- Missing features that existing users are begging for

**Scoring dimensions:**
- **Market size** (1-10) — how big is this product's market
- **Switchability** (1-10) — how easy is it for users to leave
- **Build advantage** (1-10) — how much better could we make it
- **Revenue opportunity** (1-10) — can we capture their paying users

**Not software-only.** First harvest found: Fortnite creator tools (7.8), AI relationship tools (7.1), toilet paper quality decline (6.9), medical dictation software (6.65), abandoned fashion brand (6.4).

**Usage:**
```bash
# General harvest
python3 shared/competitive_intel.py harvest

# Target a specific product
python3 shared/competitive_intel.py harvest --product="Notion"

# Target a category
python3 shared/competitive_intel.py harvest --category=crm

# Analyze through LLM tiers
python3 shared/competitive_intel.py analyze

# See which products are accumulating complaints
python3 shared/competitive_intel.py targets

# Deep dive on a specific product
python3 shared/competitive_intel.py opportunity "Notion"
```

### Stage 3: Scope & Dispatch
**Status: Paperclip running on Razer, Ruflo cloned for evaluation**

Two agent orchestration frameworks under evaluation:

**Ruflo** (github.com/ruvnet/ruflo) — Multi-agent swarm orchestration.
- 9,964 files, three generations of code (v2/v3/root)
- v3 is `3.0.0-alpha.1` — active but not stable
- Interesting ideas: MCP integration, guidance system, swarm coordination
- Reality check needed: may be more valuable for patterns than as a drop-in tool

**Paperclip** (github.com/paperclipai/paperclip) — Agent management layer.
- 1,857 PRs merged, proper monorepo (server, UI, CLI, DB, adapters, plugins)
- **Builds clean and runs on the Razer** — embedded Postgres on 54329, server on 3100
- Agent-agnostic dispatch with org charts, budgets, and goal alignment
- Needs `pnpm paperclipai onboard` for agent JWT auth setup
- More immediately usable than Ruflo

**Current approach:** Claude Code manually, with Paperclip as the next dispatch layer to wire up.

### Stage 4: Build
Agent-assisted construction of the solution. Today this is Claude Code doing the heavy lifting with human review. The aspiration is agent swarms handling scaffolding, testing, deployment, and copy automatically.

### Stage 5: Ship & Monetize
Two output paths:
- **SoY leaf package** — if the solution fits the personal data platform model, it becomes an installable module
- **Standalone product** — micro-SaaS, Fiverr service, landing page + Stripe, Chrome extension, whatever fits

### The Specsite Connection
The Specsite pipeline already validates this pattern at a smaller scale:
```
Discover businesses without websites → Harvest their info → Generate spec sites → QA → Deploy → Outreach
```
Signal Harvester is the same bones with a wider mouth — instead of "businesses without websites" the harvest target is "people with unsolved problems."

## Infrastructure

### What's Running
- **Harvester:** `shared/signal_harvester.py` — Python, Reddit public JSON, stores to soy.db
- **Triage:** `shared/signal_triage.py` — Multi-LLM tier pipeline via Ollama on Razer
- **Database:** `harvest_signals`, `harvest_triage`, `harvest_builds` tables in soy.db (migration 044)
- **Razer (soy):** Always-on. Ollama with Mistral 7B, Qwen 7B, Qwen 14B, Llama 3.1 8B. 23GB RAM, 16 cores, 575GB disk. Paperclip installed at `~/agent-eval/paperclip/`.
- **Lucy:** Always-on (as of 2026-03-27). RTX 3080 Ti 12GB. Available for LLM overflow.
- **Paperclip:** Cloned, built, and tested at `~/agent-eval/paperclip/` on Razer. Runs with embedded Postgres.
- **Ruflo:** Cloned at `~/agent-eval/ruflo/` on Razer. Not yet installed/tested.

### Ollama Models Available (Razer)
| Model | Size | Best For |
|-------|------|----------|
| mistral:7b | 4.4 GB | Tier 1 noise filtering (fast) |
| qwen2.5:7b | 4.7 GB | Backup Tier 1 |
| qwen2.5:14b | 9.0 GB | Tier 2 market scoring |
| llama3.1:8b | 4.9 GB | General tasks |

## Known Issues & Risks

### Signal quality
Regex-only harvesting has ~40% precision — "I wish" appears in relationship advice, outfit posts, etc. **Fixed:** Tier 1 LLM noise filter (Mistral 7B) correctly filtered 2/5 false positives in testing. Still needs tuning for edge cases (the CFB post slipped through T1 but was properly down-scored by T2).

### Ruflo reality check
9,964 files across three version generations. v3 is alpha. The README promises more than the code may deliver. **Next step:** attempt a minimal agent coordination run to see what's actually functional. May end up extracting patterns rather than using as-is.

### The $$$ gap
Building is maybe 20% of the work. Distribution, pricing, support, iteration — these are the hard parts. Fiverr/Upwork as distribution channels (Kerry's experiment) partially solve this by putting the product where buyers already are.

### Legal surface area
- Reddit API: public JSON endpoints, rate limited, respect ToS
- Computer use on Fiverr/Upwork: explicitly against platform ToS if detected
- Scraped content: fair use for market research, murky for direct commercial use

## Test Results

### First harvest run (2026-03-26)
- 9 queries, global search, 1-week time filter
- 5 signals stored
- **2 relevant** (r/developers "I wish there was an app" thread, r/ClaudeCode frustration post)
- **3 noise** (relationship post, outfit post, college football post)
- Takeaway: regex-only filtering is ~40% precision. LLM triage is mandatory.

### First triage run (2026-03-27)
- **Tier 1 (Mistral 7B):** 5 signals evaluated, 2 correctly rejected as noise, 3 passed
- **Tier 2 (Qwen 14B via Razer Tailscale):** 3 signals scored
  - r/developers pain point thread: **6.05/10** composite (highest — meta-thread of actual pain points)
  - r/ClaudeCode usage limits: **5.50/10** (real pain, but existing solutions score low at 2/10)
  - r/CFB college football: **5.05/10** (correctly lower — niche, existing solutions)
- Takeaway: tiered dispatch works. Local models handle 95%+ of filtering at zero cost. Only top-scoring signals need Claude API for final synthesis.

### Paperclip evaluation (2026-03-27)
- Cloned, deps installed (19.3s), built clean (server + UI + CLI)
- **Server starts successfully** — embedded Postgres on 54329, HTTP on 3100, UI serving
- Plugin system, job scheduler, and backup system all initialize
- Needs `onboard` step for agent JWT auth
- **Verdict: production-ready software, not a concept repo.** 1,857 PRs, proper architecture.

## Conversation Context

**Kerry's Fiverr experiment (2026-03-26):**
Used Claude computer use + headless browser to scan Fiverr for underserved service categories, then autonomously created gigs with imagery, pricing, and descriptions. Proof that agent-driven market gap analysis → product creation works.

**Kerry's framework finds:**
- Sutro.email — agentic app builder, pretty UI, not architecturally interesting. Skip.
- Ruflo — multi-agent swarm orchestration. The build layer.
- Paperclip — agent management/dispatch. The "give it focus and say go" layer. Someone on YouTube claims $1,800 in 3 days from a $20k target.

**Alex's key insight:** The harvest concept is universal. Point it at any definable target — Reddit pain points, Fiverr gaps, underserved industries, specific verticals — and it finds signal. Combine with Kerry's computer use approach for sources without APIs. The Specsite pipeline already validates the pattern at smaller scale.

## What's Next

1. ~~Triage layer~~ ✓
2. ~~Ruflo/Paperclip eval on Razer~~ ✓ (Paperclip working, Ruflo needs deeper eval)
3. **SoY leaf package spec** — define the format so builds have a real target
4. **Paperclip onboarding** — set up agent JWT, wire Claude Code as an agent backend
5. **Larger harvest run** — targeted subreddits, 100+ signals, full pipeline test
6. **End-to-end test** — one full cycle from real Reddit signal to shipped product
7. **Expand harvest sources** — HN, targeted subreddits, computer-use sources
8. **Wire Tier 3** — Claude API integration for final synthesis on approved signals

---

### Harvest Dashboard (React App)
**Status: Scaffolded, needs npm install + Google OAuth credentials**

`dashboard/` — React 19 + Vite + Tailwind + Express + better-sqlite3. Runs on Razer via Tailscale serve.

**Pages:**
- **Overview** — pipeline funnel, action items, triage calibration, top subreddits/industries
- **Signals** — filterable signal queue with approve/reject/defer buttons
- **Competitive** — target board + competitive signal review
- **Forecasts** — forecast board with approve/kill

**Auth:** Google OAuth + invite system. Admin (Alex) auto-created. Kerry gets invited via email link.

**Digest API:** Single source of truth at `/api/digest/*` — Discord notifier consumes this instead of querying DB directly.

**To deploy:**
1. Set up Google OAuth credentials (Google Cloud Console)
2. `cd dashboard && npm install && npm run build`
3. Set env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ADMIN_EMAIL`, `BASE_URL`
4. `tailscale serve --bg 3200` for Tailscale access
5. `npm start` (or systemd service)

### Product Feedback Loop (Planned)
Once products ship, the same pipeline monitors them — harvesting complaints about OUR products, synthesizing feature demand, and feeding improvements back into the build queue. The pipeline becomes both market researcher and product manager.

---

### Database Sync Architecture
**Status: Fixed — Syncthing no longer corrupts the DB**

**Problem:** Syncthing was syncing `soy.db` in real-time across machines, overwriting the file mid-write and causing corruption. Hit this 3x in one session.

**Solution:**
- `.stignore` excludes `soy.db`, `*.db-wal`, `*.db-shm` from Syncthing
- Syncthing continues to sync code, scripts, migrations, docs — everything except the live database
- `shared/db_sync.sh` handles explicit backup/export on a schedule (hourly cron)
- Each machine has its own local DB
- Cross-machine sync via Syncthing-safe `.sync-export` snapshots

**Cron:** `0 * * * * cd ~/.software-of-you && bash shared/db_sync.sh backup`

### Overnight Run Results (2026-03-27)
- 54 pain-point signals harvested across 10+ subreddits
- 17 competitive intelligence signals
- 7 forecasts (3 human ideas + 4 LLM-generated) all with concrete monetization strategies
- T1 prompt tuned — now catches BestofRedditorUpdates and self-promotion posts
- Physical complexity flags on forecasts involving physical goods
- Dashboard rebuilt with larger fonts, nav badges, monetization cards
- DB corruption fixed via Syncthing exclusion

---

*Last updated: 2026-03-27*
