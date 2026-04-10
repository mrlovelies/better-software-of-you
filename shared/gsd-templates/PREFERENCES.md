---
version: 1
mode: "solo"

# Model routing — research is cheap, execution is deep
models:
  research: "claude-sonnet-4-6"
  planning: "claude-opus-4-6"
  execution: "claude-opus-4-6"
  execution_simple: "claude-sonnet-4-6"
  completion: "claude-opus-4-6"
  subagent: "claude-sonnet-4-6"

# Git — isolate builds in worktrees, squash on completion
git:
  auto_push: false
  isolation: "worktree"
  merge_strategy: "squash"

# Budget — hard ceiling per build, halt at limit
budget_ceiling: 75.00
budget_enforcement: "halt"

# Dynamic model routing — downgrade on budget pressure
dynamic_routing:
  enabled: true
  tier_models:
    light: "claude-sonnet-4-6"
    medium: "claude-opus-4-6"
    heavy: "claude-opus-4-6"
  escalate_on_failure: true
  budget_pressure: true

# Phases — always do research and reassessment
phases:
  skip_research: false
  skip_reassess: false
  reassess_after_slice: true

# Quality gates — custom questions for our pipeline
gate_evaluation:
  enabled: true
  custom_questions:
    - "Does the built solution directly address the harvested pain point described in REQUIREMENTS.md?"
    - "Is this monetizable according to the strategy in REQUIREMENTS.md? Are the revenue channels implementable?"
    - "Could this be deployed as a SoY leaf module, or does it need standalone deployment? Is the deployment path clear?"
    - "Is the code production-quality — not a prototype? Could a user pay for this today?"
    - "Are there any hardcoded secrets, API keys, or credentials in the codebase?"
    - "Is there adequate error handling for user-facing flows?"

# Verification commands — run after each task
verification_commands:
  - "bash scripts/verify-security.sh"
  - "bash scripts/verify-requirements.sh"
  - "npm run lint --if-present"
  - "npm run typecheck --if-present"
  - "npm run test --if-present"
  - "bash scripts/verify-e2e.sh"
verification_auto_fix: true
verification_max_retries: 2

# Verification classes — compliance gates before milestone completion
verification_classes:
  - name: "signal-satisfaction"
    description: "Does the build solve what was harvested?"
    questions:
      - "Does the product address the specific pain point from the demand signal?"
      - "Would the original poster on Reddit find this useful?"
      - "Is the target audience correctly identified and served?"
  - name: "monetization-readiness"
    description: "Is this ready to generate revenue?"
    questions:
      - "Is at least one revenue channel from the monetization strategy implemented or implementable?"
      - "Is there a clear path from free user to paying user?"
      - "Are pricing and payment flows designed (even if not fully built in MVP)?"
  - name: "deployment-readiness"
    description: "Can this be shipped?"
    questions:
      - "Does the project have a working build step?"
      - "Are environment variables documented?"
      - "Is there a deployment target (Cloudflare Pages, Vercel, Docker, Chrome Web Store, etc.)?"
  - name: "security"
    description: "OWASP basics"
    questions:
      - "Are user inputs validated and sanitized?"
      - "Are SQL queries parameterized (no string concatenation)?"
      - "Are authentication flows secure (no plaintext passwords, proper session handling)?"
      - "Are there any exposed secrets or API keys in the codebase?"

# Skills — prefer our stack
prefer_skills:
  - "react"
  - "typescript"
  - "tailwindcss"
  - "vite"
  - "nodejs"
  - "python"
  - "sqlite"
  - "cloudflare-pages"
skill_discovery: "auto"

# Post-unit hooks
post_unit_hooks:
  - name: "security-scan"
    when: "execute-task"
    action: "verification"
  - name: "e2e-test"
    when: "complete-slice"
    action: "verification"

# Parallel execution (conservative for now)
parallel:
  enabled: false
  max_workers: 1

experimental:
  rtk: false
---

# Signal Harvester Build Preferences

This build is managed by the Signal Harvester pipeline. The product being built was identified through automated demand discovery — the pain point, competitive landscape, and monetization strategy are documented in REQUIREMENTS.md.

## Build Philosophy

1. **Solve the specific pain point.** Don't over-engineer. The signal told us what people need — build that, not more.
2. **Ship something real.** This isn't a prototype or demo. Build something a person would pay for.
3. **Minimize ongoing maintenance.** High autonomy score means the product should run itself after deployment.
4. **Security first.** We're feeding untrusted data through the pipeline — the products we build must be secure by default.

## Architecture Preferences

- React 19 + Vite + Tailwind for web frontends
- Express or Hono for APIs
- SQLite for simple data, PostgreSQL for complex
- Cloudflare Pages for static deploys, Docker for services
- Chrome Manifest V3 for browser extensions
- Mobile: PWA first, native only if required
