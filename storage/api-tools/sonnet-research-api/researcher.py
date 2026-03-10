"""
Sonnet Research API - Researcher

High-level research orchestrator that uses the SonnetResearchClient to
conduct multi-step research workflows at varying depths.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from client import QueryError, QueryResult, SonnetResearchClient
from config import RESULTS_DIR

# ---------------------------------------------------------------------------
# System prompt for research queries
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM_PROMPT = (
    "You are a thorough and meticulous research assistant. When answering "
    "research questions:\n"
    "- Cite sources and references wherever possible.\n"
    "- Clearly distinguish established facts from speculation or inference.\n"
    "- Structure your findings with headings, bullet points, and numbered "
    "lists for clarity.\n"
    "- When uncertain, state the degree of confidence and note what "
    "additional information would be needed.\n"
    "- Provide a concise summary at the end of your response."
)

FOLLOW_UP_SYSTEM_PROMPT = (
    "You are a thorough research assistant performing iterative deepening on "
    "a research topic. You have already gathered preliminary findings (provided "
    "below). Your job is to:\n"
    "- Identify gaps, unanswered questions, or areas needing more detail.\n"
    "- Expand on the most important points with deeper analysis.\n"
    "- Cite sources and distinguish facts from speculation.\n"
    "- Structure your response clearly.\n"
    "- End with a concise summary of the NEW information you have added."
)

SYNTHESIS_SYSTEM_PROMPT = (
    "You are a research assistant producing a final synthesis. You will be "
    "given all the findings gathered during a multi-step research session. "
    "Produce a clear, well-structured summary that:\n"
    "- Integrates all findings into a coherent narrative.\n"
    "- Cites sources where available.\n"
    "- Distinguishes facts from speculation.\n"
    "- Highlights key takeaways.\n"
    "- Notes remaining open questions or limitations."
)

# Depth presets: (follow-up rounds, description)
DEPTH_CONFIG: dict[str, int] = {
    "quick": 0,
    "standard": 2,
    "deep": 5,
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class QueryRecord:
    """Stores one prompt/response pair."""

    def __init__(self, prompt: str, response_text: str | None, error: str | None = None) -> None:
        self.prompt = prompt
        self.response_text = response_text
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"prompt": self.prompt}
        if self.response_text is not None:
            d["response"] = self.response_text
        if self.error is not None:
            d["error"] = self.error
        return d


# ---------------------------------------------------------------------------
# Researcher
# ---------------------------------------------------------------------------

class SonnetResearcher:
    """Orchestrates multi-step research sessions via SonnetResearchClient."""

    def __init__(self, client: SonnetResearchClient) -> None:
        self.client = client
        self.research_id: str = ""
        self.topic: str = ""
        self.depth: str = ""
        self.queries: list[QueryRecord] = []
        self.summary: str = ""
        self.timestamp: str = ""
        self._token_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def research(self, topic: str, depth: str = "standard") -> dict[str, Any]:
        """Run a research session on *topic* at the given *depth*.

        *depth* must be one of ``"quick"``, ``"standard"``, or ``"deep"``.

        Returns the structured results dict (same as ``format_results()``).
        """
        if depth not in DEPTH_CONFIG:
            raise ValueError(f"Invalid depth '{depth}'. Choose from: {list(DEPTH_CONFIG)}")

        # Reset state for a new session.
        self.research_id = str(uuid.uuid4())
        self.topic = topic
        self.depth = depth
        self.queries = []
        self.summary = ""
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self._token_usage = {"input_tokens": 0, "output_tokens": 0}

        follow_ups = DEPTH_CONFIG[depth]

        # --- Initial query ---
        initial_prompt = f"Research the following topic thoroughly:\n\n{topic}"
        initial_result = self._execute_query(initial_prompt, RESEARCH_SYSTEM_PROMPT)

        if initial_result is None:
            # All we got was an error; still produce a summary.
            self.summary = "(Research failed on the initial query.)"
            return self.format_results()

        accumulated_findings = initial_result

        # --- Follow-up rounds ---
        for i in range(follow_ups):
            follow_up_prompt = (
                f"Previous findings on the topic \"{topic}\":\n\n"
                f"{accumulated_findings}\n\n"
                f"This is follow-up round {i + 1} of {follow_ups}. "
                "Please deepen the research: identify gaps, expand on key "
                "points, and add new details or perspectives."
            )
            round_result = self._execute_query(follow_up_prompt, FOLLOW_UP_SYSTEM_PROMPT)
            if round_result is not None:
                accumulated_findings += "\n\n" + round_result

        # --- Synthesis (for standard and deep) ---
        if follow_ups > 0:
            synthesis_prompt = (
                f"Topic: {topic}\n\n"
                f"All research findings collected over {follow_ups + 1} rounds:\n\n"
                f"{accumulated_findings}\n\n"
                "Please produce a final, integrated synthesis of all findings."
            )
            synthesis = self._execute_query(synthesis_prompt, SYNTHESIS_SYSTEM_PROMPT)
            self.summary = synthesis or accumulated_findings
        else:
            self.summary = accumulated_findings

        return self.format_results()

    def format_results(self) -> dict[str, Any]:
        """Return a structured dictionary of the research session."""
        return {
            "research_id": self.research_id,
            "topic": self.topic,
            "depth": self.depth,
            "queries_made": [q.to_dict() for q in self.queries],
            "summary": self.summary,
            "token_usage": dict(self._token_usage),
            "timestamp": self.timestamp,
        }

    def save_results(self, output_dir: str | Path | None = None) -> Path:
        """Persist the research results as JSON.

        Returns the ``Path`` to the saved file.
        """
        directory = Path(output_dir) if output_dir else RESULTS_DIR
        directory.mkdir(parents=True, exist_ok=True)

        filename = f"{self.research_id}.json"
        filepath = directory / filename

        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(self.format_results(), fh, indent=2, ensure_ascii=False)

        return filepath

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _execute_query(self, prompt: str, system_prompt: str) -> str | None:
        """Run a single query, record it, track tokens, return text or None."""
        result = self.client.query(prompt=prompt, system_prompt=system_prompt)

        if isinstance(result, QueryResult):
            self._token_usage["input_tokens"] += result.input_tokens
            self._token_usage["output_tokens"] += result.output_tokens
            self.queries.append(QueryRecord(prompt=prompt, response_text=result.text))
            return result.text
        else:
            # QueryError
            self.queries.append(
                QueryRecord(prompt=prompt, response_text=None, error=result.message)
            )
            return None
