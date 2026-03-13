#!/bin/bash
# Pre-commit hook: validate schema and check for duplicates
# Runs schema_validator.py on changed data files
# Runs dedup_checker.py to prevent new duplicates

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VALIDATOR="$REPO_ROOT/storage/scripts/schema_validator.py"
DEDUP_CHECKER="$REPO_ROOT/storage/scripts/dedup_checker.py"

# Get staged files matching our data directories
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | \
  grep -E '^(knowledge-base|storage|market-research)/.*\.(json|md)$' || true)

if [ -z "$STAGED_FILES" ]; then
  echo "[pre-commit] No data files staged, skipping validation."
  exit 0
fi

echo "[pre-commit] Validating $(echo "$STAGED_FILES" | wc -l | tr -d ' ') staged data file(s)..."

# --- Schema Validation (FAIL on errors) ---
VALIDATION_ERRORS=0
if [ -f "$VALIDATOR" ]; then
  for file in $STAGED_FILES; do
    full_path="$REPO_ROOT/$file"
    if [ -f "$full_path" ]; then
      if ! python3 "$VALIDATOR" "$full_path" 2>&1; then
        echo "[pre-commit] FAIL: Schema validation failed for $file"
        VALIDATION_ERRORS=$((VALIDATION_ERRORS + 1))
      fi
    fi
  done

  if [ "$VALIDATION_ERRORS" -gt 0 ]; then
    echo "[pre-commit] ERROR: $VALIDATION_ERRORS file(s) failed schema validation."
    exit 1
  fi
  echo "[pre-commit] Schema validation passed."
else
  echo "[pre-commit] WARNING: schema_validator.py not found at $VALIDATOR, skipping."
fi

# --- Duplicate Detection (WARN only, do not fail) ---
if [ -f "$DEDUP_CHECKER" ]; then
  echo "[pre-commit] Running duplicate check..."
  DEDUP_OUTPUT=$(python3 "$DEDUP_CHECKER" 2>&1 || true)
  if [ -n "$DEDUP_OUTPUT" ]; then
    echo "[pre-commit] WARNING: Potential duplicates detected:"
    echo "$DEDUP_OUTPUT"
    echo "[pre-commit] (This is a warning only; commit will proceed.)"
  else
    echo "[pre-commit] No duplicates detected."
  fi
else
  echo "[pre-commit] WARNING: dedup_checker.py not found at $DEDUP_CHECKER, skipping."
fi

echo "[pre-commit] All checks passed."
exit 0
