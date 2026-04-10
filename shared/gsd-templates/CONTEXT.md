# Pipeline Build Context

> This build is managed by the Signal Harvester pipeline.
> All context below was generated automatically from harvested demand signals.

## Pre-Build Research Phase

Before planning milestones, you MUST complete these research steps:

### 1. Competitive Intelligence (REQUIRED)

Research 3-5 existing solutions in this space. For each:
- Product name and URL
- What they do well
- What they do poorly (this is our opportunity)
- Pricing model
- Target audience overlap with ours

Write findings to `COMPETITIVE-ANALYSIS.md` in the project root.

### 2. Technical Feasibility (REQUIRED)

Before planning:
- Identify the core technical challenge
- Confirm key APIs/services are available
- Check for critical dependencies
- Estimate infrastructure requirements

Write findings to `TECH-FEASIBILITY.md` in the project root.

### 3. MVP Scope Definition (REQUIRED)

Define the absolute minimum viable product:
- What's the ONE thing this must do on day one?
- What can wait for v2?
- What's the deploy target?

Write to `MVP-SCOPE.md` in the project root.

## Post-Build Validation

After each slice completion, verify:
1. `semgrep --config=auto --error --severity ERROR .` passes
2. The built features actually address the pain point in REQUIREMENTS.md
3. At least one monetization hook is present (even if not fully functional)

After milestone completion:
1. Full E2E test suite passes
2. Deploy to staging is possible
3. A new user could understand and use the product without documentation
