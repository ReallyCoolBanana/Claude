#!/bin/bash
set -euo pipefail

# Only run in remote (web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Add dependency installation commands here as the project grows.
# Examples:
#   npm install
#   pip install -r requirements.txt
#   cargo build

echo "Session start hook completed successfully."
