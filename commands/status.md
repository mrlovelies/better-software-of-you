---
description: Show system status and installed modules
allowed-tools: ["Bash", "Read"]
---

# System Status

Query the database at `${CLAUDE_PLUGIN_ROOT:-$(pwd)}/data/soy.db` and present a clear status overview.

Run these queries:

1. **Installed modules:** `SELECT name, version, installed_at, enabled FROM modules;`
2. **Contact count:** `SELECT COUNT(*) FROM contacts WHERE status = 'active';`
3. **Recent activity:** `SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 5;`

If the project-tracker module is installed (check modules table), also query:
- `SELECT COUNT(*) FROM projects WHERE status IN ('active', 'planning');`
- `SELECT status, COUNT(*) FROM tasks GROUP BY status;`

If the CRM module is installed, also query:
- `SELECT COUNT(*) FROM follow_ups WHERE status = 'pending' AND due_date <= date('now', '+7 days');`

If the platform-health module is installed, also query:
- `SELECT check_type, machine, status, details FROM v_health_summary ORDER BY machine, check_type;`
- `SELECT sweep_type, machine, summary, created_at FROM health_sweeps ORDER BY created_at DESC LIMIT 1;`

If the learning module is installed, also query:
- `SELECT * FROM v_learning_stats;`
- `SELECT digest_type, digest_date, title FROM learning_digests ORDER BY created_at DESC LIMIT 3;`

Read module manifests from `${CLAUDE_PLUGIN_ROOT:-$(pwd)}/modules/*/manifest.json` to identify active cross-module enhancements (where both modules in an enhancement pair are installed).

Present the results as a clean, scannable summary. Group by: Modules, Data Counts, Platform Health, Learning, Recent Activity, Active Enhancements. If any section is empty, skip it — don't show zeroes for modules that aren't installed.
