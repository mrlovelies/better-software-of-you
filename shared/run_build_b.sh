#!/bin/bash
set -e
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$HOME/.local/bin:$PATH"
cd "$HOME/.software-of-you"

WORKSPACE="builds/forecast-1-20260327-171709"
LOG="/tmp/build-b.log"

echo '========================================' | tee -a "$LOG"
echo 'Build B: drinkingaloneina.bar (with Persona Review)' | tee -a "$LOG"
echo "Started: $(date)" | tee -a "$LOG"
echo '========================================' | tee -a "$LOG"

# Step 1: Check if planning is already done (from previous run)
ROADMAP=$(find "$WORKSPACE/.gsd/milestones" -name '*ROADMAP.md' 2>/dev/null | head -1)
if [ -n "$ROADMAP" ]; then
    echo "Planning already complete: $ROADMAP" | tee -a "$LOG"
else
    echo '--- Step 1: GSD Planning Phase ---' | tee -a "$LOG"
    cd "$WORKSPACE"
    gsd headless --timeout 1200000 --json "auto --yolo seed.md" > planning.log 2>&1 &
    GSD_PID=$!
    echo "GSD planning started (PID: $GSD_PID)" | tee -a "$LOG"

    # Wait for ANY roadmap to appear (up to 20 minutes)
    WAITED=0
    while [ -z "$(find .gsd/milestones -name '*ROADMAP.md' 2>/dev/null | head -1)" ] && [ $WAITED -lt 1200 ]; do
        sleep 15
        WAITED=$((WAITED + 15))
        echo "  Waiting for roadmap... (${WAITED}s)" | tee -a "$LOG"
    done

    ROADMAP=$(find .gsd/milestones -name '*ROADMAP.md' 2>/dev/null | head -1)
    if [ -z "$ROADMAP" ]; then
        echo 'ERROR: Roadmap not generated after 20 minutes' | tee -a "$LOG"
        kill $GSD_PID 2>/dev/null
        exit 1
    fi

    # Let GSD finish its current turn then kill
    sleep 30
    kill $GSD_PID 2>/dev/null
    sleep 5
    echo "Planning complete: $ROADMAP" | tee -a "$LOG"
    cd "$HOME/.software-of-you"
fi

# Step 2: Run Persona Review Gate
echo '' | tee -a "$LOG"
echo '--- Step 2: Persona Review Gate ---' | tee -a "$LOG"
python3 shared/persona_review.py full "$WORKSPACE" --local 2>&1 | tee -a "$LOG"

# Step 3: Resume GSD with review feedback
echo '' | tee -a "$LOG"
echo '--- Step 3: GSD Build with Review Feedback ---' | tee -a "$LOG"
cd "$WORKSPACE"
gsd headless --timeout 7200000 --json "auto --yolo seed.md" > build.log 2>&1

EXIT=$?
echo '' | tee -a "$LOG"
echo "Build completed. Exit code: $EXIT" | tee -a "$LOG"
echo "Finished: $(date)" | tee -a "$LOG"

# Step 4: Feed results back
cd "$HOME/.software-of-you"
python3 shared/gsd_bridge.py feedback "$WORKSPACE" 2>&1 | tee -a "$LOG"

echo '========================================' | tee -a "$LOG"
echo 'Build B complete.' | tee -a "$LOG"
echo '========================================' | tee -a "$LOG"

# Step 5: Trigger Build C (stock Claude baseline)
echo '' | tee -a "$LOG"
echo '--- Triggering Build C (stock Claude baseline) ---' | tee -a "$LOG"
nohup bash "$HOME/.software-of-you/shared/run_build_c.sh" > /tmp/build-c.log 2>&1 &
echo "Build C started: PID $!" | tee -a "$LOG"
