"""Deadlock Detector -- graph-based cycle detection for pipelines, help requests,
and work queues.

Detects circular dependencies that cause agents to wait indefinitely:
- Pipeline stages with circular depends_on chains
- Help requests where A helps B and B helps A (mutual blocking)
- Work queue items waiting on each other

Uses DFS with back-edge detection for cycle finding.  Reads from the existing
SQLite tables (pipelines, pipeline_stages, help_requests, work_queue) created
by work_stealing.py and help_protocol.py.

Resolution strategies:
- timeout: Break the oldest participant in the cycle
- priority: Break the lowest-priority participant
- random: Break a random participant

All reads use the shared SQLite WAL database.  Resolution writes use
BEGIN IMMEDIATE for atomic operations.
Stdlib only (no external dependencies).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1

# ---------------------------------------------------------------------------
# Retry helper (mirrors Proto A pattern)
# ---------------------------------------------------------------------------


def _retry_on_busy(func):
    """Decorator: retry a method on sqlite3.OperationalError (SQLITE_BUSY)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        delay = _RETRY_BACKOFF
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    last_err = e
                    logger.debug(
                        "SQLITE_BUSY on %s (attempt %d/%d), retrying in %.2fs",
                        func.__name__, attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 0.05)
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


# ---------------------------------------------------------------------------
# Bus notification helper
# ---------------------------------------------------------------------------


def _bus_notify(bus_dir: Optional[str], channel: str, agent_id: str,
                body: dict) -> None:
    """Fire-and-forget bus notification (JSONL atomic append)."""
    if bus_dir is None:
        return
    try:
        os.makedirs(bus_dir, exist_ok=True)
        msg = {
            "id": str(uuid.uuid4()),
            "type": "blocker",
            "channel": channel,
            "team": "system",
            "agent_id": agent_id,
            "ts": time.time(),
            "ttl": 3600,
            "body": body,
        }
        raw = json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n"
        safe_ch = re.sub(r'[^a-zA-Z0-9_-]', '_', channel)
        filepath = os.path.join(bus_dir, f"{safe_ch}.jsonl")
        fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
    except Exception:
        logger.warning(
            "Bus notification failed (channel=%s, body=%s)",
            channel, body, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Graph cycle detection utilities
# ---------------------------------------------------------------------------


def _find_cycles_dfs(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Find all cycles in a directed graph using DFS with back-edge detection.

    Parameters
    ----------
    adjacency:
        Directed graph as {node: [neighbors]}.

    Returns
    -------
    list[list[str]]
        Each inner list is a cycle path (e.g., ["A", "B", "C", "A"]).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in adjacency}
    parent = {node: None for node in adjacency}
    cycles: list[list[str]] = []

    def _dfs(u: str, path: list[str]) -> None:
        color[u] = GRAY
        path.append(u)
        for v in adjacency.get(u, []):
            if v not in color:
                # Node not in our graph -- skip (dangling reference)
                continue
            if color[v] == GRAY:
                # Back edge found -- extract cycle
                cycle_start = path.index(v)
                cycle = path[cycle_start:] + [v]
                cycles.append(cycle)
            elif color[v] == WHITE:
                parent[v] = u
                _dfs(v, path)
        path.pop()
        color[u] = BLACK

    for node in adjacency:
        if color[node] == WHITE:
            _dfs(node, [])

    return cycles


# ===================================================================
# DeadlockDetector
# ===================================================================


class DeadlockDetector:
    """Detects circular dependencies in pipelines, help requests, and work queues.

    Reads from the existing coordination SQLite tables.  Does not create its
    own tables -- it queries pipelines, pipeline_stages, help_requests, and
    work_queue as created by PipelineManager, HelpProtocol, and WorkStealing.

    Parameters
    ----------
    db_path:
        Path to the SQLite database with coordination tables.
    bus_dir:
        Optional path to the JSONL bus directory for notifications.
    agent_id:
        Agent identifier for bus messages and audit trail.
    """

    def __init__(
        self,
        db_path: str,
        bus_dir: Optional[str] = None,
        agent_id: str = "deadlock-detector",
    ) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self.agent_id = agent_id
        self._lock = threading.Lock()
        self._conn = self._open_readonly(db_path)
        self._closed = False

    @staticmethod
    def _open_readonly(db_path: str) -> sqlite3.Connection:
        """Open a read-only WAL-mode SQLite connection.

        Does NOT create tables -- assumes they already exist from
        PipelineManager, HelpProtocol, WorkStealing initialization.
        """
        conn = sqlite3.connect(
            db_path, timeout=30, check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("DeadlockDetector instance is closed")

    # ------------------------------------------------------------------
    # Pipeline deadlock detection
    # ------------------------------------------------------------------

    @_retry_on_busy
    def check_pipeline_deadlock(self, pipeline_id: int) -> list:
        """Detect circular dependencies in pipeline stages.

        Builds a directed graph from the depends_on relationships of all
        stages in the given pipeline and runs cycle detection.

        Parameters
        ----------
        pipeline_id:
            The pipeline to check.

        Returns
        -------
        list[list[str]]
            Each inner list is a cycle path through stage names.
            Empty list means no deadlock detected.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT stage_name, depends_on, status
                   FROM pipeline_stages
                   WHERE pipeline_id = ?""",
                (pipeline_id,),
            ).fetchall()

        if not rows:
            return []

        # Build adjacency: dependency -> dependent (edge means "must complete before")
        # For deadlock: we want edges from each stage to its dependencies
        # A cycle means A depends on B, B depends on C, C depends on A
        adjacency: dict[str, list[str]] = {}
        for row in rows:
            stage = row["stage_name"]
            adjacency.setdefault(stage, [])
            deps = json.loads(row["depends_on"]) if row["depends_on"] else []
            for dep in deps:
                adjacency.setdefault(dep, [])
                # Edge: stage -> dep means "stage is waiting for dep"
                adjacency[stage].append(dep)

        cycles = _find_cycles_dfs(adjacency)

        if cycles:
            for cycle in cycles:
                logger.warning(
                    "Pipeline deadlock detected in pipeline %d: %s",
                    pipeline_id, " -> ".join(cycle),
                )
                _bus_notify(self.bus_dir, "deadlock-detector", self.agent_id, {
                    "event": "pipeline-deadlock",
                    "pipeline_id": pipeline_id,
                    "cycle": cycle,
                })

        return cycles

    @_retry_on_busy
    def check_all_pipeline_deadlocks(self) -> dict:
        """Check all active pipelines for deadlocks.

        Returns
        -------
        dict
            {pipeline_id: [cycles]} for pipelines with deadlocks.
            Empty dict means no deadlocks found.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM pipelines WHERE status = 'active'",
            ).fetchall()

        results = {}
        for row in rows:
            pid = row["id"]
            cycles = self.check_pipeline_deadlock(pid)
            if cycles:
                results[pid] = cycles
        return results

    # ------------------------------------------------------------------
    # Help request deadlock detection
    # ------------------------------------------------------------------

    @_retry_on_busy
    def check_help_deadlock(self) -> list:
        """Detect circular help requests.

        Looks for situations where team A has an open/accepted help request
        to team B, and team B has an open/accepted help request to team A
        (or longer chains: A->B->C->A).

        Builds a graph where edges represent "team X is waiting for help
        from team Y" (accepted requests where Y is helping X).

        Returns
        -------
        list[list[str]]
            Each inner list is a cycle path through team names.
            Empty list means no deadlock detected.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT requesting_team, accepted_by_team
                   FROM help_requests
                   WHERE status = 'accepted'
                     AND accepted_by_team IS NOT NULL""",
            ).fetchall()

        if not rows:
            return []

        # Build adjacency: helper_team -> requesting_team
        # Edge means "helper_team is busy helping requesting_team"
        # A cycle means A is helping B, B is helping C, C is helping A
        # -- they're all blocked helping each other
        adjacency: dict[str, list[str]] = {}
        all_teams = set()
        for row in rows:
            helper = row["accepted_by_team"]
            requester = row["requesting_team"]
            all_teams.add(helper)
            all_teams.add(requester)
            adjacency.setdefault(helper, [])
            adjacency[helper].append(requester)

        # Ensure all nodes in adjacency
        for t in all_teams:
            adjacency.setdefault(t, [])

        cycles = _find_cycles_dfs(adjacency)

        if cycles:
            for cycle in cycles:
                logger.warning(
                    "Help request deadlock detected: %s",
                    " -> ".join(cycle),
                )
                _bus_notify(self.bus_dir, "deadlock-detector", self.agent_id, {
                    "event": "help-deadlock",
                    "cycle": cycle,
                })

        return cycles

    # ------------------------------------------------------------------
    # Work queue deadlock detection
    # ------------------------------------------------------------------

    @_retry_on_busy
    def check_work_queue_deadlock(self) -> list:
        """Detect work items waiting on each other.

        Looks for situations where team A has claimed work owned by team B,
        and team B has claimed work owned by team A -- both are working on
        each other's tasks and potentially blocking their own work.

        Returns
        -------
        list[list[str]]
            Each inner list is a cycle path through team names.
            Empty list means no deadlock detected.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT owner_team, claimed_by
                   FROM work_queue
                   WHERE status = 'claimed'
                     AND claimed_by IS NOT NULL
                     AND owner_team != claimed_by""",
            ).fetchall()

        if not rows:
            return []

        # Build adjacency: claimed_by -> owner_team
        # Edge means "claimed_by team is doing work for owner_team"
        adjacency: dict[str, list[str]] = {}
        all_teams = set()
        for row in rows:
            claimer = row["claimed_by"]
            owner = row["owner_team"]
            all_teams.add(claimer)
            all_teams.add(owner)
            adjacency.setdefault(claimer, [])
            adjacency[claimer].append(owner)

        for t in all_teams:
            adjacency.setdefault(t, [])

        cycles = _find_cycles_dfs(adjacency)

        if cycles:
            for cycle in cycles:
                logger.warning(
                    "Work queue deadlock detected: %s",
                    " -> ".join(cycle),
                )
                _bus_notify(self.bus_dir, "deadlock-detector", self.agent_id, {
                    "event": "work-queue-deadlock",
                    "cycle": cycle,
                })

        return cycles

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @_retry_on_busy
    def resolve_deadlock(self, cycle: list, strategy: str = "timeout") -> dict:
        """Break a detected deadlock by acting on one participant.

        Parameters
        ----------
        cycle:
            The cycle path (list of node names, last element == first).
        strategy:
            Resolution strategy:
            - 'timeout': Break the oldest participant (first in cycle).
            - 'priority': Break the lowest-priority participant.
            - 'random': Break a random participant.

        Returns
        -------
        dict
            Resolution details: {strategy, broken_node, cycle, action_taken}.
        """
        self._check_closed()
        if len(cycle) < 2:
            return {"error": "Cycle too short to resolve", "cycle": cycle}

        # The cycle includes the repeated node at the end; participants
        # are all nodes except the last duplicate
        participants = cycle[:-1]

        if strategy == "timeout":
            target = participants[0]  # oldest / first in chain
        elif strategy == "priority":
            target = self._lowest_priority_participant(participants)
        elif strategy == "random":
            target = random.choice(participants)
        else:
            return {"error": f"Unknown strategy: {strategy}", "cycle": cycle}

        action = self._break_participant(target)

        result = {
            "strategy": strategy,
            "broken_node": target,
            "cycle": cycle,
            "action_taken": action,
            "resolved_at": time.time(),
        }

        _bus_notify(self.bus_dir, "deadlock-detector", self.agent_id, {
            "event": "deadlock-resolved",
            **result,
        })

        logger.info(
            "Deadlock resolved: broke %r using strategy %r in cycle %s",
            target, strategy, " -> ".join(cycle),
        )

        return result

    def _lowest_priority_participant(self, participants: list[str]) -> str:
        """Find the participant with the lowest priority in help/work tables."""
        # Check help_requests first for priority info
        priority_map = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        worst_priority = -1
        worst_node = participants[0]

        with self._lock:
            for p in participants:
                # Check as a team in help_requests
                row = self._conn.execute(
                    """SELECT wi.priority
                       FROM help_requests hr
                       JOIN work_items wi ON hr.work_item_id = wi.id
                       WHERE hr.accepted_by_team = ?
                         AND hr.status = 'accepted'
                       ORDER BY
                           CASE wi.priority
                               WHEN 'critical' THEN 0
                               WHEN 'high' THEN 1
                               WHEN 'medium' THEN 2
                               WHEN 'low' THEN 3
                               ELSE 4
                           END DESC
                       LIMIT 1""",
                    (p,),
                ).fetchone()

                if row and row["priority"]:
                    pri = priority_map.get(row["priority"], 4)
                    if pri > worst_priority:
                        worst_priority = pri
                        worst_node = p

        return worst_node

    @_retry_on_busy
    def _break_participant(self, node: str) -> str:
        """Break a deadlock by timing out the node's blocking relationships.

        Attempts to cancel/timeout the node's involvement:
        1. Cancel accepted help requests where this node is the helper.
        2. Release claimed work items back to 'queued'.

        Returns a description of the action taken.
        """
        actions = []
        now = time.time()

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Cancel help requests where this node is the accepted helper
                cur = self._conn.execute(
                    """UPDATE help_requests
                       SET status = 'open',
                           accepted_by_team = NULL,
                           accepted_by_agent = NULL
                       WHERE accepted_by_team = ?
                         AND status = 'accepted'""",
                    (node,),
                )
                if cur.rowcount > 0:
                    actions.append(
                        f"Released {cur.rowcount} help request(s) accepted by {node}"
                    )

                # Release claimed work items back to queued
                cur = self._conn.execute(
                    """UPDATE work_queue
                       SET status = 'queued',
                           claimed_by = NULL,
                           claimed_at = NULL
                       WHERE claimed_by = ?
                         AND status = 'claimed'""",
                    (node,),
                )
                if cur.rowcount > 0:
                    actions.append(
                        f"Released {cur.rowcount} work item(s) claimed by {node}"
                    )

                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        if not actions:
            return f"No blocking relationships found for {node}"
        return "; ".join(actions)

    # ------------------------------------------------------------------
    # Comprehensive scan
    # ------------------------------------------------------------------

    def scan_all(self) -> dict:
        """Run all deadlock checks and return a comprehensive report.

        Returns
        -------
        dict
            {
                "pipeline_deadlocks": {pipeline_id: [cycles]},
                "help_deadlocks": [cycles],
                "work_queue_deadlocks": [cycles],
                "total_deadlocks": int,
                "scanned_at": float,
            }
        """
        self._check_closed()

        pipeline_deadlocks = self.check_all_pipeline_deadlocks()
        help_deadlocks = self.check_help_deadlock()
        work_deadlocks = self.check_work_queue_deadlock()

        total = (
            sum(len(cycles) for cycles in pipeline_deadlocks.values())
            + len(help_deadlocks)
            + len(work_deadlocks)
        )

        report = {
            "pipeline_deadlocks": pipeline_deadlocks,
            "help_deadlocks": help_deadlocks,
            "work_queue_deadlocks": work_deadlocks,
            "total_deadlocks": total,
            "scanned_at": time.time(),
        }

        if total > 0:
            logger.warning(
                "Deadlock scan found %d deadlock(s): %d pipeline, %d help, %d work_queue",
                total,
                sum(len(c) for c in pipeline_deadlocks.values()),
                len(help_deadlocks),
                len(work_deadlocks),
            )
            _bus_notify(self.bus_dir, "deadlock-detector", self.agent_id, {
                "event": "deadlock-scan-complete",
                "total_deadlocks": total,
                "pipeline_count": sum(len(c) for c in pipeline_deadlocks.values()),
                "help_count": len(help_deadlocks),
                "work_queue_count": len(work_deadlocks),
            })
        else:
            logger.info("Deadlock scan complete: no deadlocks detected")

        return report

    def close(self) -> None:
        """Clean shutdown: close the database connection."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
