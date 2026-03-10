"""
Research Orchestrator - Main entry point for the Sonnet Research API system.

The ResearchOrchestrator coordinates multiple SonnetResearcher instances,
dispatching research tasks through a RequestQueue for parallel execution.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from client import SonnetResearchClient
from config import ANTHROPIC_API_KEY, RESULTS_DIR
from queue_manager import RequestQueue
from researcher import SonnetResearcher


@dataclass
class MissionResult:
    """Result of a completed research mission."""

    mission_id: str
    objective: str
    results: list[dict[str, Any]]
    combined_summary: str
    total_tokens: int
    duration_seconds: float
    timestamp: str


class ResearchOrchestrator:
    """
    Main entry point for dispatching research tasks to Claude Sonnet.

    Creates and coordinates SonnetResearcher instances, manages the request
    queue, and collects results.  Designed to be used by agentic teams
    running inside Claude Code.

    Usage::

        orchestrator = ResearchOrchestrator()
        results = orchestrator.spawn_researchers(["topic1", "topic2"])
        # or
        result = orchestrator.run_research_mission(mission_dict)
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        """Initialize the orchestrator with a Sonnet client and request queue.

        Args:
            api_key: Anthropic API key.  Falls back to the ANTHROPIC_API_KEY
                     environment variable when not provided.
        """
        self._api_key = api_key or ANTHROPIC_API_KEY
        self._client = SonnetResearchClient(api_key=self._api_key)
        self._queue = RequestQueue()
        self._progress_callback: Optional[Callable[[int, int, dict[str, Any]], None]] = None
        self._current_mission_id: Optional[str] = None
        self._mission_status: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Progress / status helpers
    # ------------------------------------------------------------------

    def on_progress(self, callback: Callable[[int, int, dict[str, Any]], None]) -> None:
        """Register a progress callback.

        Args:
            callback: Function receiving ``(completed, total, latest_result)``.
        """
        self._progress_callback = callback

    def get_mission_status(self) -> dict[str, Any]:
        """Return a snapshot of the current mission progress.

        Returns:
            Dict with *mission_id*, *status*, *completed*, *total*, and
            *elapsed_seconds*.
        """
        return dict(self._mission_status)

    # ------------------------------------------------------------------
    # spawn_researchers
    # ------------------------------------------------------------------

    def spawn_researchers(
        self,
        topics: list[str],
        depth: str = "standard",
        max_concurrent: int = 3,
    ) -> list[dict[str, Any]]:
        """Create one ``SonnetResearcher`` per topic and run them.

        Each researcher is executed sequentially via its own ``research()``
        call (which internally performs multi-step querying at the requested
        depth).  The queue is available for lower-level batching inside each
        researcher if needed.

        Args:
            topics: List of research topics.
            depth: Research depth -- ``"quick"``, ``"standard"``, or ``"deep"``.
            max_concurrent: Maximum parallel requests through the queue.

        Returns:
            List of research result dicts from each researcher.
        """
        results: list[dict[str, Any]] = []
        total = len(topics)

        self._mission_status = {
            "mission_id": f"spawn-{uuid.uuid4().hex[:8]}",
            "status": "running",
            "completed": 0,
            "total": total,
            "elapsed_seconds": 0.0,
        }
        start_time = time.time()

        for idx, topic in enumerate(topics):
            researcher = SonnetResearcher(client=self._client)
            result = researcher.research(topic, depth=depth)
            formatted = researcher.format_results()
            results.append(formatted)

            self._mission_status["completed"] = idx + 1
            self._mission_status["elapsed_seconds"] = round(time.time() - start_time, 2)

            if self._progress_callback:
                self._progress_callback(idx + 1, total, formatted)

        self._mission_status["status"] = "completed"
        self._mission_status["elapsed_seconds"] = round(time.time() - start_time, 2)
        return results

    # ------------------------------------------------------------------
    # run_research_mission
    # ------------------------------------------------------------------

    def run_research_mission(self, mission: dict[str, Any]) -> MissionResult:
        """Execute a structured research mission.

        Args:
            mission: Dict with keys:

                * **objective** (*str*) -- High-level mission objective.
                * **sub_tasks** (*list[dict]*) -- Each dict must contain
                  ``topic`` and optionally ``depth`` and ``priority``.
                * **max_concurrent** (*int*, default 3) -- Parallel limit.
                * **output_format** (*str*, ``"combined"`` or ``"individual"``,
                  default ``"combined"``).

        Returns:
            A :class:`MissionResult` dataclass with all results and metadata.
        """
        start_time = time.time()
        mission_id = f"mission-{uuid.uuid4().hex[:12]}"
        self._current_mission_id = mission_id

        objective: str = mission.get("objective", "Research mission")
        sub_tasks: list[dict[str, Any]] = mission.get("sub_tasks", [])
        max_concurrent: int = mission.get("max_concurrent", 3)
        output_format: str = mission.get("output_format", "combined")

        total_tasks = len(sub_tasks)
        self._mission_status = {
            "mission_id": mission_id,
            "status": "running",
            "completed": 0,
            "total": total_tasks,
            "elapsed_seconds": 0.0,
        }

        # Sort sub-tasks by priority (higher priority first).
        sorted_tasks = sorted(
            sub_tasks, key=lambda t: t.get("priority", 0), reverse=True
        )

        results: list[dict[str, Any]] = []

        for idx, task in enumerate(sorted_tasks):
            topic: str = task.get("topic", f"sub-task-{idx}")
            depth: str = task.get("depth", "standard")

            researcher = SonnetResearcher(client=self._client)
            result = researcher.research(topic, depth=depth)
            formatted = researcher.format_results()
            results.append(formatted)

            self._mission_status["completed"] = idx + 1
            self._mission_status["elapsed_seconds"] = round(time.time() - start_time, 2)

            if self._progress_callback:
                self._progress_callback(idx + 1, total_tasks, formatted)

        # Build combined summary if requested.
        if output_format == "combined":
            combined_summary = self._build_combined_summary(objective, results)
        else:
            combined_summary = ""

        duration = time.time() - start_time
        total_tokens = self._client.total_input_tokens + self._client.total_output_tokens

        self._mission_status["status"] = "completed"
        self._mission_status["elapsed_seconds"] = round(duration, 2)

        return MissionResult(
            mission_id=mission_id,
            objective=objective,
            results=results,
            combined_summary=combined_summary,
            total_tokens=total_tokens,
            duration_seconds=round(duration, 2),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_combined_summary(objective: str, results: list[dict[str, Any]]) -> str:
        """Build a combined markdown summary from all research results."""
        sections = [f"# Research Mission: {objective}\n"]

        for idx, result in enumerate(results, 1):
            topic = result.get("topic", f"Topic {idx}")
            summary = result.get("summary", "No summary available.")
            depth = result.get("depth", "standard")
            sections.append(f"## {idx}. {topic} (depth: {depth})\n\n{summary}\n")

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        sections.append(f"\n---\n*Mission completed at {ts}*\n")
        return "\n".join(sections)
