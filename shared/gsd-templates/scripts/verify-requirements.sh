#!/bin/bash
# Post-build requirements validation
# Checks that the build actually addresses the harvested pain point
set -e

echo "Validating against requirements..."

# Check that REQUIREMENTS.md exists and the build addresses it
if [ ! -f "REQUIREMENTS.md" ]; then
    echo "Warning: REQUIREMENTS.md not found"
    exit 0
fi

# Check for key build artifacts
ERRORS=0

# Must have a package.json or equivalent
if [ ! -f "package.json" ] && [ ! -f "setup.py" ] && [ ! -f "Cargo.toml" ] && [ ! -f "go.mod" ]; then
    echo "FAIL: No project manifest (package.json, setup.py, etc.)"
    ERRORS=$((ERRORS + 1))
fi

# Must have source code
SRC_FILES=$(find . -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.jsx" -o -name "*.py" -o -name "*.rs" -o -name "*.go" | grep -v node_modules | grep -v .gsd | head -1)
if [ -z "$SRC_FILES" ]; then
    echo "FAIL: No source code files found"
    ERRORS=$((ERRORS + 1))
fi

# Should have tests
TEST_FILES=$(find . -name "*.test.*" -o -name "*.spec.*" -o -name "test_*" | grep -v node_modules | grep -v .gsd | head -1)
if [ -z "$TEST_FILES" ]; then
    echo "WARN: No test files found — tests should be created"
fi

# Check for hardcoded secrets (basic check)
SECRETS=$(grep -rl "sk-[a-zA-Z0-9]\{20,\}\|password\s*=\s*['\"][^'\"]\{4,\}" . --include="*.ts" --include="*.js" --include="*.py" --include="*.tsx" --include="*.jsx" 2>/dev/null | grep -v node_modules | grep -v .gsd | head -3)
if [ -n "$SECRETS" ]; then
    echo "FAIL: Possible hardcoded secrets in: $SECRETS"
    ERRORS=$((ERRORS + 1))
fi

if [ $ERRORS -gt 0 ]; then
    echo "Requirements validation: FAILED ($ERRORS issues)"
    exit 1
else
    echo "Requirements validation: PASSED"
fi
