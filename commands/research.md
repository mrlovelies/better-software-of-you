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
