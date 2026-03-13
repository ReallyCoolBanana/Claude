"""Coordinator Hub -- real-time agent monitoring via shared SQLite status table.

Hub-and-spoke model: every agent writes its status to a shared SQLite table
via AgentReporter.  The coordinator reads from it via CoordinatorDashboard.
Agents CANNOT see each other's entries -- the coordinator mediates all
cross-agent communication through the coordinator_instructions table.

All writes use BEGIN IMMEDIATE for atomic operations and threading.Lock for
connection safety, following Proto A patterns (SQLite WAL mode).
Stdlib only (no external dependencies).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .db_partition import PartitionedStore
    from .circuit_breaker import CircuitBreaker
    from .deadlock_detector import DeadlockDetector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_AGENT_STATUSES = frozenset({
    "initializing", "working", "blocked", "complete", "error",
})

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_status (
    agent_id TEXT PRIMARY KEY,
    team TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'initializing',
    progress_pct REAL DEFAULT 0,
    current_task TEXT,
    blockers TEXT,
    findings_count INTEGER DEFAULT 0,
    output_files TEXT,
    error_message TEXT,
    started_at REAL NOT NULL,
    last_updated REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS coordinator_instructions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_agent TEXT NOT NULL,
    instruction_type TEXT NOT NULL,
    payload TEXT,
    status TEXT DEFAULT 'pending',
    created_at REAL NOT NULL,
    read_at REAL
);

CREATE INDEX IF NOT EXISTS idx_agent_status_team ON agent_status(team);
CREATE INDEX IF NOT EXISTS idx_agent_status_status ON agent_status(status);
CREATE INDEX IF NOT EXISTS idx_coordinator_instructions_target_status
    ON coordinator_instructions(target_agent, status);
"""

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
                    # P0 FIX: Cap backoff at 50ms to avoid 1.6s+ delays
                    delay = min(delay * 2, 0.05)
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------


def _open_db(db_path: str, busy_timeout_ms: int = 30000) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection and ensure schema exists.

    Note: Both ``sqlite3.connect(timeout=...)`` and ``PRAGMA busy_timeout``
    are set intentionally.  The connect *timeout* governs Python-level
    retry/sleep behaviour inside the sqlite3 module, while the PRAGMA
    controls SQLite's internal busy handler.  Keeping both aligned ensures
    consistent behaviour regardless of which layer handles contention first.
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(
        db_path, timeout=busy_timeout_ms / 1000, check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Bus notification helper
# ---------------------------------------------------------------------------


def _bus_notify(bus_dir: Optional[str], channel: str, agent_id: str,
                team: str, body: dict) -> None:
    """Fire-and-forget bus notification (JSONL atomic append).

    Best-effort: failures are logged but never propagated to callers.
    """
    if bus_dir is None:
        return
    try:
        os.makedirs(bus_dir, exist_ok=True)
        msg = {
            "id": str(uuid.uuid4()),
            "type": "info",
            "channel": channel,
            "team": team,
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


# ===================================================================
# AgentReporter
# ===================================================================


class AgentReporter:
    """Used by agents to post their status to the shared hub.

    Each agent creates one AgentReporter instance at startup.  It registers
    the agent in the agent_status table and provides methods to update
    progress, report errors/completion, and check for coordinator instructions.

    Parameters
    ----------
    db_path:
        Path to the shared SQLite database.
    agent_id:
        Unique identifier for this agent.
    team:
        Team this agent belongs to.
    role:
        Role description for this agent.
    bus_dir:
        Optional path to the JSONL bus directory for notifications.
    partitioned_store:
        Optional PartitionedStore instance.  When provided, the hub partition
        is used for agent_status and coordinator_instructions tables instead
        of opening a new connection to db_path.  This enables incremental
        migration to the partitioned database layout.
    ack_protocol:
        Optional AckProtocol instance.  When provided, instructions received
        via ``check_instructions()`` are automatically acknowledged on the
        bus so the coordinator can track delivery.
    """

    def __init__(self, db_path: str, agent_id: str, team: str, role: str,
                 bus_dir: Optional[str] = None,
                 partitioned_store: Optional["PartitionedStore"] = None,
                 ack_protocol: Optional[object] = None) -> None:
        self.db_path = db_path
        self.agent_id = agent_id
        self.team = team
        self.role = role
        self.bus_dir = bus_dir
        self._partitioned_store = partitioned_store
        self._ack_protocol = ack_protocol
        self._lock = threading.Lock()
        if partitioned_store is not None:
            # Use the hub partition for agent_status and coordinator_instructions
            self._conn = partitioned_store.hub.conn
            self._owns_conn = False
        else:
            self._conn = _open_db(db_path)
            self._owns_conn = True
        self._closed = False
        # P0 FIX: Status write deduplication — cache last-written status in
        # memory, only write to DB if status actually changed. Status updates
        # are ~60% of total coordination overhead per benchmarks.
        self._last_status: dict[str, Any] = {}
        self._register()

    def _check_closed(self) -> None:
        """Raise RuntimeError if this instance has been closed."""
        if self._closed:
            raise RuntimeError("AgentReporter instance is closed")

    @_retry_on_busy
    def _register(self) -> None:
        """Register this agent in the agent_status table with 'initializing' status."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO agent_status
                   (agent_id, team, role, status, progress_pct, started_at, last_updated)
                   VALUES (?, ?, ?, 'initializing', 0, ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET
                       team = excluded.team,
                       role = excluded.role,
                       status = 'initializing',
                       progress_pct = 0,
                       current_task = NULL,
                       blockers = NULL,
                       findings_count = 0,
                       output_files = NULL,
                       error_message = NULL,
                       started_at = excluded.started_at,
                       last_updated = excluded.last_updated
                """,
                (self.agent_id, self.team, self.role, now, now),
            )
            self._conn.commit()

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "agent-registered",
            "agent_id": self.agent_id,
            "role": self.role,
        })

    @_retry_on_busy
    def update_status(self, status: str, progress_pct: float,
                      current_task: str, blockers: Optional[str] = None,
                      findings_count: int = 0,
                      output_files: Optional[str] = None) -> None:
        """Update this agent's status in the shared table.

        Performs an atomic INSERT OR REPLACE so the row is always current.

        Parameters
        ----------
        status:
            One of: 'initializing', 'working', 'blocked', 'complete', 'error'.
        progress_pct:
            Percentage complete (0-100).
        current_task:
            Description of the current task.
        blockers:
            Optional description of what is blocking progress.
        findings_count:
            Number of findings/results produced so far.
        output_files:
            Optional comma-separated list of output file paths.
        """
        self._check_closed()
        if status not in VALID_AGENT_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}. Must be one of: "
                f"{', '.join(sorted(VALID_AGENT_STATUSES))}"
            )
        if not (0 <= progress_pct <= 100):
            raise ValueError(
                f"progress_pct must be between 0 and 100, got {progress_pct}"
            )

        # P0 FIX: Status write deduplication — skip DB write if nothing changed.
        # Status updates account for ~60% of total coordination overhead per
        # benchmarks; deduplicating them provides an immediate large gain.
        new_vals = {
            "status": status,
            "progress_pct": progress_pct,
            "current_task": current_task,
            "blockers": blockers,
            "findings_count": findings_count,
            "output_files": output_files,
        }
        if self._last_status == new_vals:
            # Identical to last write — skip DB round-trip entirely
            return

        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO agent_status
                   (agent_id, team, role, status, progress_pct, current_task,
                    blockers, findings_count, output_files, error_message,
                    started_at, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET
                       team = excluded.team,
                       role = excluded.role,
                       status = excluded.status,
                       progress_pct = excluded.progress_pct,
                       current_task = excluded.current_task,
                       blockers = excluded.blockers,
                       findings_count = excluded.findings_count,
                       output_files = excluded.output_files,
                       error_message = NULL,
                       last_updated = excluded.last_updated
                """,
                (self.agent_id, self.team, self.role, status, progress_pct,
                 current_task, blockers, findings_count, output_files,
                 now, now),
            )
            self._conn.commit()

        # Cache the values we just wrote so the next identical call is skipped
        self._last_status = new_vals

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "agent-status-update",
            "agent_id": self.agent_id,
            "status": status,
            "progress_pct": progress_pct,
        })

    @_retry_on_busy
    def report_error(self, error_message: str) -> None:
        """Set this agent's status to 'error' with the given error message.

        Parameters
        ----------
        error_message:
            Description of the error that occurred.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute(
                """UPDATE agent_status
                   SET status = 'error', error_message = ?, progress_pct = 0,
                       last_updated = ?
                   WHERE agent_id = ?""",
                (error_message, now, self.agent_id),
            )
            self._conn.commit()

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "agent-error",
            "agent_id": self.agent_id,
            "error_message": error_message,
        })

    @_retry_on_busy
    def report_complete(self, output_files: Optional[str] = None) -> None:
        """Set this agent's status to 'complete' with progress at 100%.

        Parameters
        ----------
        output_files:
            Optional comma-separated list of output file paths produced.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute(
                """UPDATE agent_status
                   SET status = 'complete', progress_pct = 100,
                       output_files = ?, last_updated = ?
                   WHERE agent_id = ?""",
                (output_files, now, self.agent_id),
            )
            self._conn.commit()

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "agent-complete",
            "agent_id": self.agent_id,
            "output_files": output_files,
        })

    @_retry_on_busy
    def check_instructions(self) -> list[dict]:
        """Read pending coordinator instructions for this agent and mark them as read.

        Returns a list of instruction dicts. Each instruction is marked with
        status='read' and a read_at timestamp after retrieval.

        Returns
        -------
        list[dict]
            List of instruction dictionaries with keys: id, target_agent,
            instruction_type, payload, status, created_at, read_at.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """SELECT * FROM coordinator_instructions
                       WHERE target_agent = ? AND status = 'pending'
                       ORDER BY created_at ASC""",
                    (self.agent_id,),
                ).fetchall()

                instructions = [dict(r) for r in rows]

                if instructions:
                    ids = [inst["id"] for inst in instructions]
                    placeholders = ",".join("?" for _ in ids)
                    self._conn.execute(
                        f"""UPDATE coordinator_instructions
                            SET status = 'read', read_at = ?
                            WHERE id IN ({placeholders})""",
                        [now] + ids,
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        # Auto-ack instructions via ack_protocol if available
        if self._ack_protocol is not None and instructions:
            for inst in instructions:
                payload = inst.get("payload")
                if payload:
                    try:
                        payload_data = json.loads(payload) if isinstance(payload, str) else payload
                        # Look for an ack-tracked message ID in the payload
                        msg_id = payload_data.get("_ack_message_id")
                        if msg_id:
                            self._ack_protocol.ack(msg_id)
                    except Exception as e:
                        logger.debug("Auto-ack for instruction %s failed: %s", inst.get("id"), e)
                # Also try acking by instruction_id pattern on the bus
                try:
                    inst_id = str(inst.get("id", ""))
                    if inst_id:
                        self._ack_protocol.ack(
                            f"instruction-{inst_id}",
                            channel="global",
                        )
                except Exception:
                    pass  # Best-effort ack

        return instructions

    def close(self) -> None:
        """Clean shutdown: close the database connection if we own it."""
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._owns_conn:
                    self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ===================================================================
# CoordinatorDashboard
# ===================================================================


class CoordinatorDashboard:
    """Used by the coordinator to monitor all agents and send instructions.

    Provides read access to the agent_status table and write access to the
    coordinator_instructions table.  The coordinator is the only entity
    that should see all agents' statuses.

    Parameters
    ----------
    db_path:
        Path to the shared SQLite database.
    bus_dir:
        Optional path to the JSONL bus directory for notifications.
    partitioned_store:
        Optional PartitionedStore instance.  When provided, the hub partition
        is used for agent_status and coordinator_instructions tables instead
        of opening a new connection to db_path.  This enables incremental
        migration to the partitioned database layout.
    circuit_breaker:
        Optional CircuitBreaker instance.  When provided, ``run_health_checks()``
        will include open/half-open circuit information in the health report.
    deadlock_detector:
        Optional DeadlockDetector instance.  When provided, ``run_health_checks()``
        will scan for pipeline, help-request, and work-queue deadlocks.
    health_check_interval:
        Seconds between automatic health checks when monitoring is active.
        Defaults to 60 seconds.  Set to 0 to disable automatic checks.
    ack_protocol:
        Optional AckProtocol instance.  When provided, critical instruction
        types (redirect, reassign, shutdown) are sent via send_no_wait for
        reliable delivery with acknowledgment tracking.
    """

    # Instruction types that require reliable ack-tracked delivery
    _CRITICAL_INSTRUCTION_TYPES = frozenset({"redirect", "reassign", "shutdown"})

    def __init__(self, db_path: str, bus_dir: Optional[str] = None,
                 partitioned_store: Optional["PartitionedStore"] = None,
                 circuit_breaker: Optional["CircuitBreaker"] = None,
                 deadlock_detector: Optional["DeadlockDetector"] = None,
                 health_check_interval: float = 60.0,
                 ack_protocol: Optional[object] = None) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self._partitioned_store = partitioned_store
        self._circuit_breaker = circuit_breaker
        self._deadlock_detector = deadlock_detector
        self._health_check_interval = health_check_interval
        self._ack_protocol = ack_protocol
        self._lock = threading.Lock()
        if partitioned_store is not None:
            self._conn = partitioned_store.hub.conn
            self._owns_conn = False
        else:
            self._conn = _open_db(db_path)
            self._owns_conn = True
        self._closed = False
        self._health_thread: Optional[threading.Thread] = None
        self._health_stop = threading.Event()
        self._last_health_report: Optional[dict] = None

    def _check_closed(self) -> None:
        """Raise RuntimeError if this instance has been closed."""
        if self._closed:
            raise RuntimeError("CoordinatorDashboard instance is closed")

    @_retry_on_busy
    def get_all_status(self) -> list[dict]:
        """Return a list of all agent statuses, sorted by last_updated descending.

        Returns
        -------
        list[dict]
            Each dict contains all columns from the agent_status table.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_status ORDER BY last_updated DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_active_agents(self) -> list[dict]:
        """Return agents not in 'complete' or 'error' state.

        Returns
        -------
        list[dict]
            Active agent status records.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM agent_status
                   WHERE status NOT IN ('complete', 'error')
                   ORDER BY last_updated DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_blocked_agents(self) -> list[dict]:
        """Return agents with status='blocked'.

        Returns
        -------
        list[dict]
            Blocked agent status records.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM agent_status
                   WHERE status = 'blocked'
                   ORDER BY last_updated DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_completed_agents(self) -> list[dict]:
        """Return agents with status='complete'.

        Returns
        -------
        list[dict]
            Completed agent status records.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM agent_status
                   WHERE status = 'complete'
                   ORDER BY last_updated DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_agent_status(self, agent_id: str) -> Optional[dict]:
        """Get a single agent's status.

        Parameters
        ----------
        agent_id:
            The agent identifier to look up.

        Returns
        -------
        dict or None
            The agent's status record, or None if not found.
        """
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_status WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return dict(row) if row else None

    @_retry_on_busy
    def send_instruction(self, target_agent: str, instruction_type: str,
                         payload: Optional[str] = None,
                         verify_exists: bool = False) -> int:
        """Post an instruction for an agent to pick up.

        Parameters
        ----------
        target_agent:
            The agent_id to send the instruction to.
        instruction_type:
            Type of instruction (e.g., 'redirect', 'pause', 'priority_change').
        payload:
            Optional JSON string with instruction details.
        verify_exists:
            If True, raise ValueError when *target_agent* is not registered
            in the agent_status table.

        Returns
        -------
        int
            The instruction id.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            if verify_exists:
                row = self._conn.execute(
                    "SELECT 1 FROM agent_status WHERE agent_id = ?",
                    (target_agent,),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        f"Target agent {target_agent!r} not found in agent_status"
                    )
            cur = self._conn.execute(
                """INSERT INTO coordinator_instructions
                   (target_agent, instruction_type, payload, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (target_agent, instruction_type, payload, now),
            )
            instruction_id = cur.lastrowid
            self._conn.commit()

        body = {
            "event": "instruction-sent",
            "target_agent": target_agent,
            "instruction_type": instruction_type,
            "instruction_id": instruction_id,
        }
        is_critical = instruction_type in self._CRITICAL_INSTRUCTION_TYPES
        if is_critical and self._ack_protocol is not None:
            try:
                self._ack_protocol.send_no_wait(
                    channel="global",
                    msg_type="instruction",
                    body=body,
                    target_agent=target_agent,
                    priority=8,
                    ttl=3600,
                )
            except Exception as e:
                logger.warning(
                    "Ack-tracked instruction to %s failed: %s; falling back to bus",
                    target_agent, e,
                )
                _bus_notify(self.bus_dir, "global", "coordinator", "coordinator", body)
        else:
            _bus_notify(self.bus_dir, "global", "coordinator", "coordinator", body)

        return instruction_id

    def get_unacknowledged_instructions(self, older_than: float = 60.0) -> list[dict]:
        """Check which agents haven't acknowledged critical instructions.

        Requires an ack_protocol to be configured.  Returns a list of
        unacked message dicts from the ack tracking table.  If no
        ack_protocol is configured, returns an empty list.

        Parameters
        ----------
        older_than:
            Only consider messages older than this many seconds (default 60).

        Returns
        -------
        list[dict]
            Unacknowledged instruction messages.
        """
        self._check_closed()
        if self._ack_protocol is None:
            return []
        try:
            unacked = self._ack_protocol.get_unacked(older_than=older_than)
            # Filter to only instruction-type messages
            return [
                msg for msg in unacked
                if msg.get("msg_type") == "instruction"
            ]
        except Exception as e:
            logger.warning("get_unacknowledged_instructions failed: %s", e)
            return []

    @_retry_on_busy
    def get_stale_agents(self, timeout_seconds: int = 300) -> list[dict]:
        """Return active agents whose last_updated is older than *timeout_seconds*.

        Useful for detecting agents that may have crashed or hung.  Only agents
        that are still active (not 'complete' or 'error') are considered.

        Parameters
        ----------
        timeout_seconds:
            Number of seconds since last update to consider an agent stale.

        Returns
        -------
        list[dict]
            Stale agent status records, sorted by last_updated ascending.
        """
        # BUG-CH-002: Push staleness check into SQL using strftime for
        # correct server-side time comparison instead of Python-side cutoff.
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM agent_status
                   WHERE status NOT IN ('complete', 'error')
                     AND (strftime('%s', 'now') - last_updated) > ?
                   ORDER BY last_updated ASC""",
                (timeout_seconds,),
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def broadcast_instruction(self, instruction_type: str, payload: Optional[str] = None,
                              team: Optional[str] = None) -> list[int]:
        """Send an instruction to all active agents, optionally filtered by team.

        Parameters
        ----------
        instruction_type:
            Type of instruction (e.g., 'pause', 'shutdown', 'priority_change').
        payload:
            Optional JSON string with instruction details.
        team:
            If provided, only target active agents in this team.

        Returns
        -------
        list[int]
            List of created instruction IDs.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if team is not None:
                    rows = self._conn.execute(
                        """SELECT agent_id FROM agent_status
                           WHERE status NOT IN ('complete', 'error')
                             AND team = ?""",
                        (team,),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """SELECT agent_id FROM agent_status
                           WHERE status NOT IN ('complete', 'error')""",
                    ).fetchall()

                # Convert Row objects to plain dicts before any further
                # statements can invalidate them.
                agent_ids = [row["agent_id"] for row in rows]

                # P0 FIX: Batch insert instructions using executemany
                # instead of one-by-one INSERT in a loop
                if agent_ids:
                    self._conn.executemany(
                        """INSERT INTO coordinator_instructions
                           (target_agent, instruction_type, payload, status, created_at)
                           VALUES (?, ?, ?, 'pending', ?)""",
                        [(aid, instruction_type, payload, now) for aid in agent_ids],
                    )
                    # Retrieve the inserted IDs — executemany's lastrowid is the
                    # last row; we know they are sequential autoincrement IDs
                    last_id = self._conn.execute(
                        "SELECT last_insert_rowid()"
                    ).fetchone()[0]
                    instruction_ids = list(range(
                        last_id - len(agent_ids) + 1, last_id + 1
                    ))
                else:
                    instruction_ids = []
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        # For critical instruction types, use ack-tracked delivery if available
        is_critical = instruction_type in self._CRITICAL_INSTRUCTION_TYPES
        for aid, inst_id in zip(agent_ids, instruction_ids):
            body = {
                "event": "instruction-sent",
                "target_agent": aid,
                "instruction_type": instruction_type,
                "instruction_id": inst_id,
            }
            if is_critical and self._ack_protocol is not None:
                try:
                    self._ack_protocol.send_no_wait(
                        channel="global",
                        msg_type="instruction",
                        body=body,
                        target_agent=aid,
                        priority=8,
                        ttl=3600,
                    )
                except Exception as e:
                    logger.warning(
                        "Ack-tracked broadcast to %s failed: %s; falling back to bus",
                        aid, e,
                    )
                    _bus_notify(self.bus_dir, "global", "coordinator", "coordinator", body)
            else:
                _bus_notify(self.bus_dir, "global", "coordinator", "coordinator", body)

        return instruction_ids

    @_retry_on_busy
    def get_team_status(self, team: str) -> list[dict]:
        """Return all agents belonging to a specific team.

        Parameters
        ----------
        team:
            The team name to filter by.

        Returns
        -------
        list[dict]
            Agent status records for the given team, sorted by last_updated
            descending.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM agent_status
                   WHERE team = ?
                   ORDER BY last_updated DESC""",
                (team,),
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_failed_agents(self) -> list[dict]:
        """Return agents with status='error'.

        Returns
        -------
        list[dict]
            Failed agent status records, sorted by last_updated descending.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM agent_status
                   WHERE status = 'error'
                   ORDER BY last_updated DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_summary(self) -> dict:
        """Return a summary dict with aggregate counts and average progress.

        Returns
        -------
        dict
            Keys: total, active, blocked, complete, error, avg_progress.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, progress_pct FROM agent_status",
            ).fetchall()

        total = len(rows)
        if total == 0:
            return {
                "total": 0,
                "active": 0,
                "blocked": 0,
                "complete": 0,
                "error": 0,
                "avg_progress": 0.0,
            }

        active = sum(1 for r in rows if r["status"] not in ("complete", "error"))
        blocked = sum(1 for r in rows if r["status"] == "blocked")
        complete = sum(1 for r in rows if r["status"] == "complete")
        error = sum(1 for r in rows if r["status"] == "error")
        avg_progress = sum(r["progress_pct"] for r in rows) / total

        return {
            "total": total,
            "active": active,
            "blocked": blocked,
            "complete": complete,
            "error": error,
            "avg_progress": avg_progress,
        }

    # ------------------------------------------------------------------
    # Health checks (circuit breaker + deadlock detector integration)
    # ------------------------------------------------------------------

    def run_health_checks(self) -> dict:
        """Run circuit breaker and deadlock detector health checks.

        Collects data from both optional subsystems and returns a combined
        health report.  Safe to call even when neither subsystem is wired
        in -- the report will simply contain empty sections.

        Returns
        -------
        dict
            {
                "checked_at": float,
                "deadlocks": {...} or None,
                "open_circuits": [...] or None,
                "stale_agents": [...],
                "healthy": bool,
            }
        """
        self._check_closed()
        report: dict[str, Any] = {
            "checked_at": time.time(),
            "deadlocks": None,
            "open_circuits": None,
            "stale_agents": [],
            "healthy": True,
        }

        # Deadlock detector scan
        if self._deadlock_detector is not None:
            try:
                scan = self._deadlock_detector.scan_all()
                report["deadlocks"] = scan
                if scan.get("total_deadlocks", 0) > 0:
                    report["healthy"] = False
                    logger.warning(
                        "Health check: %d deadlock(s) detected",
                        scan["total_deadlocks"],
                    )
            except Exception:
                logger.exception("Health check: deadlock scan failed")
                report["deadlocks"] = {"error": "scan_failed"}
                report["healthy"] = False

        # Circuit breaker scan
        if self._circuit_breaker is not None:
            try:
                all_circuits = self._circuit_breaker.get_all_circuits()
                open_circuits = [
                    c for c in all_circuits
                    if c.get("state") in ("open", "half_open")
                ]
                report["open_circuits"] = open_circuits
                if open_circuits:
                    report["healthy"] = False
                    logger.warning(
                        "Health check: %d open/half-open circuit(s)",
                        len(open_circuits),
                    )
            except Exception:
                logger.exception("Health check: circuit breaker scan failed")
                report["open_circuits"] = [{"error": "scan_failed"}]
                report["healthy"] = False

        # Also include stale agent detection as part of health
        try:
            report["stale_agents"] = self.get_stale_agents()
            if report["stale_agents"]:
                report["healthy"] = False
        except Exception:
            logger.exception("Health check: stale agent check failed")

        self._last_health_report = report

        _bus_notify(self.bus_dir, "health-check", "coordinator", "coordinator", {
            "event": "health-check-complete",
            "healthy": report["healthy"],
            "deadlock_count": (
                report["deadlocks"].get("total_deadlocks", 0)
                if isinstance(report.get("deadlocks"), dict) else 0
            ),
            "open_circuit_count": (
                len(report["open_circuits"])
                if isinstance(report.get("open_circuits"), list) else 0
            ),
            "stale_agent_count": len(report.get("stale_agents", [])),
        })

        return report

    def get_last_health_report(self) -> Optional[dict]:
        """Return the most recent health check report, or None if never run."""
        return self._last_health_report

    def start_health_monitor(self) -> Optional[threading.Thread]:
        """Start a background thread that runs health checks periodically.

        The thread calls ``run_health_checks()`` every
        ``health_check_interval`` seconds.  Returns the thread (already
        started), or None if the interval is zero or no subsystems are
        configured.

        Returns
        -------
        threading.Thread or None
        """
        if self._health_check_interval <= 0:
            return None
        if self._circuit_breaker is None and self._deadlock_detector is None:
            return None
        if self._health_thread is not None and self._health_thread.is_alive():
            logger.warning("Health monitor thread already running")
            return self._health_thread

        self._health_stop.clear()

        def _monitor():
            logger.info(
                "Health monitor started (interval=%.1fs)",
                self._health_check_interval,
            )
            while not self._health_stop.is_set():
                try:
                    self.run_health_checks()
                except Exception:
                    logger.exception("Health monitor: check failed")
                self._health_stop.wait(timeout=self._health_check_interval)
            logger.info("Health monitor stopped")

        thread = threading.Thread(
            target=_monitor,
            name="coordinator-health-monitor",
            daemon=True,
        )
        thread.start()
        self._health_thread = thread
        return thread

    def stop_health_monitor(self) -> None:
        """Stop the background health monitor thread if running."""
        self._health_stop.set()
        if self._health_thread is not None and self._health_thread.is_alive():
            self._health_thread.join(timeout=5.0)
            self._health_thread = None

    def close(self) -> None:
        """Clean shutdown: stop health monitor and close database connection."""
        self.stop_health_monitor()
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._owns_conn:
                    self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
