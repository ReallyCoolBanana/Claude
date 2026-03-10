"""
Research Orchestrator - Main entry point for the Sonnet Research API system.

The ResearchOrchestrator coordinates multiple SonnetResearcher instances,
dispatching research tasks through a RequestQueue for parallel execution.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from .client import SonnetResearchClient
from .config import ANTHROPIC_API_KEY, RESULTS_DIR
from .queue_manager import RequestQueue
from .researcher import SonnetResearcher


@dataclass
class MissionResult:
    """Result of a completed research mission."""
    mission_id: str
    objective: str
    results: list
    combined_summary: str
    total_tokens: int
    duration_seconds: float
    timestamp: str


class ResearchOrchestrator:
    """
    Main entry point for dispatching research tasks to Claude Sonnet.

    Creates and coordinates SonnetResearcher instances, manages the request
    queue, and collects results. Designed to be used by agentic teams
    running inside Claude Code.

    Usage:
        orchestrator = ResearchOrchestrator()
        results = orchestrator.spawn_researchers(["topic1", "topic2"])
        # or
        result = orchestrator.run_research_mission(mission_dict)
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the orchestrator with a Sonnet client and request queue.

        Args:
            api_key: Anthropic API key. Falls back to env var if not provided.
        """
        self._api_key = api_key or ANTHROPIC_API_KEY
        self._client = SonnetResearchClient(api_key=self._api_key)
        self._queue = RequestQueue()
        self._progress_callback: Optional[Callable] = None
        self._current_mission_id: Optional[str] = None
        self._mission_status: dict = {}
        self._researchers: list[SonnetResearcher] = []

    def on_progress(self, callback: Callable):
        """
        Register a progress callback.

        Args:
            callback: Function receiving (completed: int, total: int, latest_result: dict)
        """
        self._progress_callback = callback

    def get_mission_status(self) -> dict:
        """
        Get the current mission progress.

        Returns:
            Dict with mission_id, status, completed, total, and elapsed_seconds.
        """
        return dict(self._mission_status)

    def spawn_researchers(
        self,
        topics: list[str],
        depth: str = "standard",
        max_concurrent: int = 3,
    ) -> list[dict]:
        """
        Create one SonnetResearcher per topic and process all in parallel.

        Args:
            topics: List of research topics.
            depth: Research depth - "standard" or "deep".
            max_concurrent: Max parallel requests through the queue.

        Returns:
            List of research result dicts from each researcher.
        """
        self._researchers = []
        results = []

        # Create a researcher for each topic and enqueue their work
        for idx, topic in enumerate(topics):
            researcher = SonnetResearcher(client=self._client, topic=topic)
            self._researchers.append(researcher)

            request_id = f"research-{uuid.uuid4().hex[:8]}-{idx}"
            self._queue.enqueue(
                request_id=request_id,
                prompt=f"Research the following topic in depth: {topic}",
                system_prompt=(
                    f"You are a thorough research assistant. Conduct {depth}-depth "
                    f"research on the given topic. Provide structured findings with "
                    f"key facts, analysis, and sources where possible."
                ),
                priority=0,
                callback=None,
            )

        # Process the queue
        self._queue.process_queue(self._client, max_concurrent=max_concurrent)

        # Collect results from each researcher
        completed = 0
        total = len(topics)
        for researcher in self._researchers:
            result = researcher.research(researcher._topic, depth=depth)
            formatted = researcher.format_results()
            results.append(formatted)

            completed += 1
            if self._progress_callback:
                self._progress_callback(completed, total, formatted)

        return results

    def run_research_mission(self, mission: dict) -> MissionResult:
        """
        Execute a structured research mission.

        Args:
            mission: Dict with keys:
                - objective (str): High-level mission objective
                - sub_tasks (list[dict]): Each with topic, depth, priority
                - max_concurrent (int): Parallel limit (default 3)
                - output_format (str): "combined" or "individual" (default "combined")

        Returns:
            MissionResult dataclass with all results and metadata.
        """
        start_time = time.time()
        mission_id = f"mission-{uuid.uuid4().hex[:12]}"
        self._current_mission_id = mission_id

        objective = mission.get("objective", "Research mission")
        sub_tasks = mission.get("sub_tasks", [])
        max_concurrent = mission.get("max_concurrent", 3)
        output_format = mission.get("output_format", "combined")

        total_tasks = len(sub_tasks)
        self._mission_status = {
            "mission_id": mission_id,
            "status": "running",
            "completed": 0,
            "total": total_tasks,
            "elapsed_seconds": 0.0,
        }

        self._researchers = []
        results = []

        # Sort sub_tasks by priority (higher priority first)
        sorted_tasks = sorted(
            sub_tasks, key=lambda t: t.get("priority", 0), reverse=True
        )

        # Enqueue all tasks
        for idx, task in enumerate(sorted_tasks):
            topic = task.get("topic", f"sub-task-{idx}")
            depth = task.get("depth", "standard")
            priority = task.get("priority", 0)

            researcher = SonnetResearcher(client=self._client, topic=topic)
            self._researchers.append((researcher, depth))

            request_id = f"{mission_id}-task-{idx}"
            self._queue.enqueue(
                request_id=request_id,
                prompt=f"Research the following topic: {topic}\nMission objective: {objective}",
                system_prompt=(
                    f"You are a research assistant working on a larger mission: {objective}. "
                    f"Conduct {depth}-depth research on the given sub-topic. "
                    f"Provide structured, detailed findings."
                ),
                priority=priority,
                callback=None,
            )

        # Process queue in parallel
        self._queue.process_queue(self._client, max_concurrent=max_concurrent)

        # Collect results from researchers
        for researcher, depth in self._researchers:
            result = researcher.research(researcher._topic, depth=depth)
            formatted = researcher.format_results()
            results.append(formatted)

            self._mission_status["completed"] += 1
            self._mission_status["elapsed_seconds"] = time.time() - start_time

            if self._progress_callback:
                self._progress_callback(
                    self._mission_status["completed"],
                    total_tasks,
                    formatted,
                )

        # Build combined summary
        if output_format == "combined":
            combined_summary = self._build_combined_summary(objective, results)
        else:
            combined_summary = ""

        duration = time.time() - start_time
        total_tokens = getattr(self._client, "total_tokens_used", 0)

        self._mission_status["status"] = "completed"
        self._mission_status["elapsed_seconds"] = duration

        return MissionResult(
            mission_id=mission_id,
            objective=objective,
            results=results,
            combined_summary=combined_summary,
            total_tokens=total_tokens,
            duration_seconds=round(duration, 2),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def _build_combined_summary(self, objective: str, results: list[dict]) -> str:
        """
        Build a combined summary from all research results.

        Args:
            objective: The mission objective.
            results: List of formatted result dicts.

        Returns:
            A combined markdown summary string.
        """
        sections = [f"# Research Mission: {objective}\n"]

        for idx, result in enumerate(results, 1):
            topic = result.get("topic", f"Topic {idx}")
            summary = result.get("summary", "No summary available.")
            sections.append(f"## {idx}. {topic}\n\n{summary}\n")

        sections.append(
            f"\n---\n*Mission completed at {time.strftime('%Y-%m-%d %H:%M:%S UTC')}*\n"
        )
        return "\n".join(sections)
