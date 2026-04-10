---
name: research
description: Check Ambient Research pipeline status, view wikis, trigger runs, or manage streams
trigger: /research
---

# Ambient Research Pipeline

Check the pipeline status, view living wiki documents, trigger research runs, or manage streams.

## What to do

1. Run bootstrap: `bash "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/bootstrap.sh"`

2. Determine what the user wants:
   - **status** → Show pipeline overview (streams, findings, wiki stats, machine health)
   - **wikis** → Show wiki content for a specific stream or all streams
   - **run tier1/tier2** → Trigger a manual research sweep
   - **streams** → List, add, edit, or deactivate research streams
   - **findings** → Show recent findings for a stream
   - **log** → Show recent ambient-research.log entries
   - **recommendations** → Review the infrastructure recommendation queue
     produced by the SoY Infrastructure & Claude Ecosystem stream
     (subcommands: list, show, preview, approve, reject, defer)

3. For **status**: Query research_streams, research_tasks, research_findings, research_wikis tables.
   Also check machine health by hitting Ollama endpoints:
   - Razer: `curl -s http://100.125.139.126:11434/api/tags`
   - Lucy: `curl -s http://100.74.238.16:11434/api/tags`

4. For **run**: Execute via SSH:
   - Tier 1: `ssh mrlovelies@100.125.139.126 "cd ~/.software-of-you && python3 modules/ambient-research/run.py tier1"`
   - Tier 2: `ssh mrlovelies-gaming@100.74.238.16 "cd ~/wkspaces/better-software-of-you && python3 modules/ambient-research/run.py tier2"`

5. For **wikis**: Query `SELECT content FROM research_wikis WHERE stream_id = ? ORDER BY updated_at DESC LIMIT 1`
   Present in a readable format with the stream name as heading.

6. Always show a brief summary and suggest next actions.

## Machine Reference

| Machine | Role | Tailscale IP | SSH User |
|---------|------|-------------|----------|
| Razer | Tier 1 (7B models) | 100.125.139.126 | mrlovelies |
| Lucy | Tier 2 (14B model) | 100.74.238.16 | mrlovelies-gaming |

## Cron Schedule

- Tier 1 (Razer): Every 6 hours
- Tier 2 (Lucy): Every 12 hours
- Tier 3 (Razer): 3am ET daily (Claude CLI digest)

## Infrastructure Recommendations

The SoY Infrastructure & Claude Ecosystem stream feeds an autonomous
recommendation pipeline (`modules/ambient-research/infra_evaluator.py`)
that scores findings against the live SoY architecture and produces
actionable improvement suggestions sitting in `infra_recommendations`
awaiting human triage.

When the user asks about **recommendations**, dispatch to:

```
python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/modules/ambient-research/infra_evaluator.py" <subcommand> [args]
```

### Subcommands

| Subcommand | What it does |
|---|---|
| `list` | Show the pending review queue, ranked by composite score, with title, category, user_impact, and target files |
| `show <id>` | Full detail for one recommendation: scores, description, proposed changes, target files, why-review |
| `preview <id>` | Invoke Claude CLI on demand to generate a full implementation plan (diffs, migrations, test plan, rollback) **without** committing the recommendation. Use this when the user wants to see "what would this actually do" before approving. |
| `approve <id> [notes]` | Mark approved, write calibration rows for each scoring dimension. Tier 3 will pick it up on its next planning run, OR run `preview <id>` immediately. |
| `reject <id> [reason]` | Mark rejected, write calibration rows. The reason is stored in review_notes. |
| `defer <id>` | Mark deferred (no calibration). Use when the rec is fine but not now. |

### Conversational pattern

When the user says "show me the recommendations" or similar, run `list`
and present the queue conversationally — don't dump raw output. For each
rec, lead with the title and user_impact (the "what does this do for me"
answer), then composite score and category. If the user picks one, run
`show <id>` and present sections naturally. If they want to know what it
would actually do, run `preview <id>` and warn them it takes 30-60 seconds.

After approve/reject, suggest the next action: "Want to look at the next
one in the queue?" or "I'll run `preview` if you want to see the
implementation plan."
