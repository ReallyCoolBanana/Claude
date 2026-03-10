"""
Sonnet Research API - Configuration and Defaults

Central configuration for the research API system. The Anthropic API key
must be set as the ANTHROPIC_API_KEY environment variable.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# API / Model
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL: str = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------
RATE_LIMIT_REQUESTS_PER_MINUTE: int = 50
MAX_CONCURRENT_REQUESTS: int = 3

# ---------------------------------------------------------------------------
# Retry Settings
# ---------------------------------------------------------------------------
MAX_RETRIES: int = 3
RETRY_BACKOFF_SECONDS: list[float] = [1.0, 2.0, 4.0]

# ---------------------------------------------------------------------------
# Queue Settings
# ---------------------------------------------------------------------------
MAX_QUEUE_SIZE: int = 500

# ---------------------------------------------------------------------------
# Token Defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_TOKENS: int = 4096

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent
RESULTS_DIR: Path = BASE_DIR / "results"

# Ensure the results directory exists at import time.
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
