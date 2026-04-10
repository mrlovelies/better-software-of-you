#!/bin/bash
# Build C: Stock Claude Opus — no GSD, no persona review, just raw Claude
set -e
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$HOME/.local/bin:$PATH"
cd "$HOME/.software-of-you"

WORKSPACE="builds/forecast-1-20260327-build-c"
LOG="/tmp/build-c.log"

echo '========================================' | tee "$LOG"
echo 'Build C: drinkingaloneina.bar (Stock Claude, no framework)' | tee -a "$LOG"
echo "Started: $(date)" | tee -a "$LOG"
echo '========================================' | tee -a "$LOG"

# Prepare workspace
mkdir -p "$WORKSPACE"
cp builds/forecast-1-20260327-171709/REQUIREMENTS.md "$WORKSPACE/"
cp builds/forecast-1-20260327-171709/seed.md "$WORKSPACE/"
cd "$WORKSPACE"
git init 2>/dev/null
git add -A && git commit -m 'Initial workspace' 2>/dev/null

# Write build meta
python3 -c "
import json
from datetime import datetime
meta = {
    'source_type': 'forecast',
    'source_id': 1,
    'workspace': '/home/mrlovelies/.software-of-you',
    'created_at': datetime.now().isoformat(),
    'status': 'building',
    'build_started_at': datetime.now().isoformat(),
    'budget': 75.0,
    'variant': 'stock_claude'
}
with open('.build-meta.json', 'w') as f: json.dump(meta, f, indent=2)
"

echo '--- Running stock Claude build ---' | tee -a "$LOG"

# Raw Claude — just the brief, no GSD decomposition
claude -p --model claude-opus-4-6 --output-format json "You are building a product called drinkingaloneina.bar. Read REQUIREMENTS.md for the full brief including pain point, competitive landscape, and monetization strategy.

Build the COMPLETE product as described — a hyper-contextual social proximity PWA where users at bars broadcast availability signals and nearby users can discover them. React 19 + Vite + Tailwind frontend, Hono/Express API, SQLite database.

Build everything needed for a working MVP:
1. Project scaffold (monorepo with api, shared, web packages)
2. Auth (email signup + login + JWT)
3. Signal CRUD (create, read nearby, dismiss, auto-expire)
4. Map view with Leaflet showing nearby signals
5. Real-time updates via WebSocket or SSE
6. Venue pages
7. PWA manifest + service worker
8. Tests for core flows

Write ALL the code. Create ALL the files. Make it production-quality, not a prototype." > build.log 2>&1

EXIT=$?
echo "Build completed. Exit code: $EXIT" | tee -a "$LOG"
echo "Finished: $(date)" | tee -a "$LOG"

# Update meta
python3 -c "
import json
from datetime import datetime
with open('.build-meta.json') as f: meta = json.load(f)
meta['status'] = 'success' if $EXIT == 0 else 'error'
meta['build_completed_at'] = datetime.now().isoformat()
meta['exit_code'] = $EXIT
with open('.build-meta.json', 'w') as f: json.dump(meta, f, indent=2)
"

echo '========================================' | tee -a "$LOG"
echo 'Build C complete.' | tee -a "$LOG"
echo '========================================' | tee -a "$LOG"
