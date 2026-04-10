#!/bin/bash
# Post-build security scan via Semgrep
# Called by GSD's verification_commands
set -e

export PATH="$HOME/.local/bin:$PATH"

echo "Running Semgrep security scan..."

# Run Semgrep with auto config, fail on ERROR severity
if command -v semgrep &> /dev/null; then
    semgrep --config=auto --error --severity ERROR --quiet . 2>&1
    EXIT=$?
    if [ $EXIT -eq 0 ]; then
        echo "Security scan: PASSED"
    else
        echo "Security scan: FAILED — fix ERROR-level findings"
        exit 1
    fi
else
    echo "Warning: semgrep not installed, skipping security scan"
fi
