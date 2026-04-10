#!/bin/bash
# Post-build E2E test via Playwright
# Called by GSD's verification_commands after slice completion
set -e

echo "Running E2E tests..."

# Only run if playwright config exists
if [ -f "playwright.config.ts" ] || [ -f "playwright.config.js" ]; then
    npx playwright test --reporter=line 2>&1
    EXIT=$?
    if [ $EXIT -eq 0 ]; then
        echo "E2E tests: PASSED"
    else
        echo "E2E tests: FAILED"
        exit 1
    fi
elif [ -d "e2e" ] || [ -d "tests/e2e" ]; then
    npx playwright test --reporter=line 2>&1
    EXIT=$?
    if [ $EXIT -eq 0 ]; then
        echo "E2E tests: PASSED"
    else
        echo "E2E tests: FAILED"
        exit 1
    fi
else
    echo "No Playwright config found — skipping E2E tests"
    echo "Note: E2E tests should be created as part of the build"
fi
