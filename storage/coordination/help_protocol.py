"""
Team Help Protocol and Idle Detection for Proto A.

Solves the observed problem: teams finishing and sitting idle while one team
is still busy.  Provides work-item tracking, help-request/offer/accept flow,
idle detection, capability matching, and automatic assignment of idle teams
to teams that need help.

All writes use BEGIN IMMEDIATE for atomic operations and threading.Lock for
connection safety, following Proto A patterns (SQLite WAL mode).
"""

import functools
import json
import logging
import re
import sqlite3
import threading
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .db_partition import PartitionedStore
    from .capability_discovery import CapabilityRegistry
    from .load_balancer import LoadBalancer

logger = logging.getLogger(__name__)

# Valid team statuses
VALID_STATUSES = {"idle", "working", "needs_help", "helping", "complete"}

# ---------------------------------------------------------------------------
# Schema — new tables added alongside existing Proto A state tables
# ---------------------------------------------------------------------------

_HELP_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    estimated_minutes REAL,
    required_capabilities TEXT,
    assigned_to TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS help_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requesting_team TEXT NOT NULL,
    requesting_agent TEXT NOT NULL,
    work_item_id INTEGER REFERENCES work_items(id),
    description TEXT NOT NULL,
    required_capabilities TEXT,
    status TEXT DEFAULT 'open',
    accepted_by_team TEXT,
    accepted_by_agent TEXT,
    created_at REAL NOT NULL,
    resolved_at REAL
);

CREATE TABLE IF NOT EXISTS team_capabilities (
    team TEXT NOT NULL,
    capability TEXT NOT NULL,
    proficiency TEXT DEFAULT 'standard',
    PRIMARY KEY (team, capability)
);

CREATE TABLE IF NOT EXISTS team_status (
    team TEXT PRIMARY KEY,
    status TEXT DEFAULT 'idle',
    progress_pct REAL DEFAULT 0,
    estimated_completion REAL,
    current_task TEXT,
    work_items_total INTEGER DEFAULT 0,
    work_items_done INTEGER DEFAULT 0,
    last_updated REAL NOT NULL
);
"""

# Retry configuration for SQLITE_BUSY
_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1


def _is_busy_or_locked(exc: sqlite3.OperationalError) -> bool:
    """Check if a sqlite3.OperationalError is SQLITE_BUSY or SQLITE_LOCKED.

    Uses the sqlite_errorcode attribute (Python 3.11+) when available,
    falling back to string matching for older Python versions.
    SQLITE_BUSY = 5, SQLITE_LOCKED = 6.
    """
    # Python 3.11+ exposes the SQLite error code directly
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        # Also match extended error codes (e.g. SQLITE_BUSY_SNAPSHOT = 517)
        base_code = code & 0xFF
        return base_code in (5, 6)  # SQLITE_BUSY=5, SQLITE_LOCKED=6
    # Fallback for older Python versions
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


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
                if _is_busy_or_locked(e):
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


class HelpProtocol:
    """Manages team help requests, idle detection, and automatic work assignment.

    Parameters
    ----------
    db_path:
        Path to the SQLite database (shared state DB).
    bus_dir:
        Path to the JSONL bus directory for publishing messages.
    team:
        Team identifier for this instance.
    agent_id:
        Agent identifier for this instance.
    partitioned_store:
        Optional PartitionedStore instance.  When provided, work_items,
        help_requests, team_capabilities, and team_status are routed to
        the findings partition instead of the monolithic database.  This
        enables incremental migration to the partitioned database layout.
    capability_registry:
        Optional CapabilityRegistry instance.  When provided,
        ``auto_assign_idle_teams()`` uses ``match_work_to_team()`` for
        intelligent capability-based assignment instead of the built-in
        capability matching.
    load_balancer:
        Optional LoadBalancer instance.  When provided,
        ``auto_assign_idle_teams()`` uses ``get_least_loaded()`` to break
        ties between equally-capable teams.  ``offer_help()`` and
        ``fulfill_help()`` update load metrics automatically.
    """

    def __init__(self, db_path: str, bus_dir: str, team: str, agent_id: str,
                 partitioned_store: Optional["PartitionedStore"] = None,
                 ack_protocol: Optional[object] = None,
                 capability_registry: Optional["CapabilityRegistry"] = None,
                 load_balancer: Optional["LoadBalancer"] = None) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self.team = team
        self.agent_id = agent_id
        self._partitioned_store = partitioned_store
        self._ack_protocol = ack_protocol
        self._capability_registry = capability_registry
        self._load_balancer = load_balancer
        self._lock = threading.Lock()
        if partitioned_store is not None:
            # All HelpProtocol tables live in the findings partition
            self._conn = partitioned_store.findings.conn
            self._owns_conn = False
        else:
            self._conn = sqlite3.connect(
                db_path, timeout=30, check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.executescript(_HELP_SCHEMA)
            # Add performance indexes (INEFF-PERF-006)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_status ON work_items(status)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_help_requests_status ON help_requests(status)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_team ON work_items(assigned_to)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_help_requests_work ON help_requests(work_item_id)")
            # P0 indexes: team-based lookups for work_items and help_requests
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_owner_team ON work_items(team)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_help_requests_requesting_team ON help_requests(requesting_team)")
            self._conn.commit()
            self._owns_conn = True
        self._closed = False

    def _check_closed(self) -> None:
        """Raise RuntimeError if the instance is closed."""
        if self._closed:
            raise RuntimeError("HelpProtocol instance is closed")

    def close(self) -> None:
        """Close the database connection if we own it."""
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

    # ------------------------------------------------------------------
    # Bus message helpers
    # ------------------------------------------------------------------

    def _publish_bus(self, channel: str, msg_type: str, body: dict) -> Optional[str]:
        """Publish a message to the JSONL bus. Returns message ID or None.

        Bus write failures are logged but never propagated, so that DB
        operations already committed are not lost.
        """
        try:
            import os
            import uuid as _uuid

            msg = {
                "id": str(_uuid.uuid4()),
                "type": msg_type,
                "channel": channel,
                "team": self.team,
                "agent_id": self.agent_id,
                "ts": time.time(),
                "ttl": 3600,
                "body": body,
            }
            raw = json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n"
            safe_ch = re.sub(r'[^a-zA-Z0-9_-]', '_', channel)
            filepath = os.path.join(self.bus_dir, f"{safe_ch}.jsonl")
            os.makedirs(self.bus_dir, exist_ok=True)
            fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)
            return msg["id"]
        except Exception as e:
            logger.error("Bus write failed on channel %s: %s", channel, e)
            return None

    # ------------------------------------------------------------------
    # Team status management
    # ------------------------------------------------------------------

    @_retry_on_busy
    def register_capabilities(self, capabilities: list[str]) -> None:
        """Register capabilities for this team.

        Parameters
        ----------
        capabilities:
            List of capability strings (e.g. ["coding", "research", "testing"]).
        """
        self._check_closed()
        with self._lock:
            for cap in capabilities:
                self._conn.execute(
                    """
                    INSERT INTO team_capabilities (team, capability, proficiency)
                    VALUES (?, ?, 'standard')
                    ON CONFLICT(team, capability) DO UPDATE SET
                        proficiency = excluded.proficiency
                    """,
                    (self.team, cap),
                )
            self._conn.commit()

    @_retry_on_busy
    def update_status(
        self,
        status: str,
        progress_pct: float,
        current_task: str,
        est_completion: Optional[float] = None,
    ) -> None:
        """Update this team's status.

        Parameters
        ----------
        status:
            One of: idle, working, needs_help, helping, complete.
        progress_pct:
            Percentage complete (0-100).
        current_task:
            Description of current task.
        est_completion:
            Estimated completion timestamp (epoch seconds).
        """
        self._check_closed()
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        # FIX: BUG-HP-016 - validate progress_pct is between 0 and 100
        if not (0 <= progress_pct <= 100):
            raise ValueError(
                f"progress_pct must be between 0 and 100, got {progress_pct}"
            )
        now = time.time()
        with self._lock:
            # Count work items for this team
            row = self._conn.execute(
                "SELECT COUNT(*) as total FROM work_items WHERE team = ?",
                (self.team,),
            ).fetchone()
            total = row["total"] if row else 0

            row = self._conn.execute(
                "SELECT COUNT(*) as done FROM work_items WHERE team = ? AND status = 'completed'",
                (self.team,),
            ).fetchone()
            done = row["done"] if row else 0

            self._conn.execute(
                """
                INSERT INTO team_status (team, status, progress_pct, estimated_completion,
                    current_task, work_items_total, work_items_done, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team) DO UPDATE SET
                    status = excluded.status,
                    progress_pct = excluded.progress_pct,
                    estimated_completion = excluded.estimated_completion,
                    current_task = excluded.current_task,
                    work_items_total = excluded.work_items_total,
                    work_items_done = excluded.work_items_done,
                    last_updated = excluded.last_updated
                """,
                (self.team, status, progress_pct, est_completion,
                 current_task, total, done, now),
            )
            self._conn.commit()

        # Publish status-update to bus
        self._publish_bus("global", "info", {
            "event": "status-update",
            "team": self.team,
            "status": status,
            "progress_pct": progress_pct,
            "est_completion": est_completion,
        })

    # ------------------------------------------------------------------
    # Work item management
    # ------------------------------------------------------------------

    @_retry_on_busy
    def add_work_item(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        est_minutes: float = 0,
        required_caps: Optional[list[str]] = None,
    ) -> int:
        """Add a work item for this team. Returns the work item ID."""
        self._check_closed()
        # FIX: BUG-HP-012 - validate priority is within allowed values
        valid_priorities = {'critical', 'high', 'medium', 'low'}
        if priority not in valid_priorities:
            raise ValueError(
                f"Invalid priority {priority!r}. Must be one of: {', '.join(sorted(valid_priorities))}"
            )
        now = time.time()
        caps_json = json.dumps(required_caps) if required_caps is not None else None
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO work_items (team, agent_id, title, description, status,
                    priority, estimated_minutes, required_capabilities, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (self.team, self.agent_id, title, description, priority,
                 est_minutes, caps_json, now, now),
            )
            work_id = cur.lastrowid
            self._conn.commit()
        return work_id

    @_retry_on_busy
    def mark_work_available(self, work_item_id: int) -> None:
        """Mark a work item as available for help from other teams."""
        self._check_closed()
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE work_items
                SET status = 'available_for_help', updated_at = ?
                WHERE id = ? AND team = ?
                """,
                (now, work_item_id, self.team),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                raise ValueError(
                    f"Work item {work_item_id} not found for team {self.team!r}"
                )
            self._conn.commit()

    @_retry_on_busy
    def complete_work_item(self, work_item_id: int) -> None:
        """Mark a work item as completed."""
        self._check_closed()
        now = time.time()
        with self._lock:
            # FIX: BUG-HP-011 - verify the completing agent owns the work item (AND team = ?)
            cur = self._conn.execute(
                """
                UPDATE work_items
                SET status = 'completed', updated_at = ?
                WHERE id = ? AND team = ?
                """,
                (now, work_item_id, self.team),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                raise ValueError(
                    f"Work item {work_item_id} not found for team {self.team!r}"
                )
            self._conn.commit()

    @_retry_on_busy
    def is_pipeline_complete(self) -> bool:
        """Check if all work items for this team are completed.

        Returns False for empty pipelines instead of vacuously True.
        """
        self._check_closed()
        with self._lock:
            items = self._conn.execute(
                "SELECT status FROM work_items WHERE team = ?",
                (self.team,),
            ).fetchall()
        # FIX: BUG-HP-009 - empty list guard; all([]) == True is misleading
        if not items:
            return False
        return all(row["status"] == "completed" for row in items)

    # ------------------------------------------------------------------
    # Help request flow
    # ------------------------------------------------------------------

    @_retry_on_busy
    def request_help(self, work_item_id: int, description: str) -> int:
        """Create a help request for a work item. Returns the request ID.

        Also marks the work item as available_for_help and publishes a
        help-request message on the bus.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            # FIX: BUG-HP-010 - validate work_item_id exists before creating help request
            row = self._conn.execute(
                "SELECT required_capabilities, estimated_minutes FROM work_items WHERE id = ?",
                (work_item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Work item {work_item_id} does not exist")
            caps = row["required_capabilities"]
            est = row["estimated_minutes"]

            # Mark work item available
            self._conn.execute(
                "UPDATE work_items SET status = 'available_for_help', updated_at = ? WHERE id = ?",
                (now, work_item_id),
            )

            cur = self._conn.execute(
                """
                INSERT INTO help_requests (requesting_team, requesting_agent, work_item_id,
                    description, required_capabilities, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?)
                """,
                (self.team, self.agent_id, work_item_id, description, caps, now),
            )
            request_id = cur.lastrowid
            self._conn.commit()

        # Publish help-request to bus (with ack tracking if available)
        help_body = {
            "event": "help-request",
            "request_id": request_id,
            "work_item_id": work_item_id,
            "description": description,
            "required_capabilities": json.loads(caps) if caps else [],
            "estimated_minutes": est,
        }
        if self._ack_protocol is not None:
            try:
                self._ack_protocol.send_no_wait(
                    channel="global",
                    msg_type="info",
                    body=help_body,
                    target_agent="coordinator",
                    priority=5,
                    ttl=3600,
                )
            except Exception as e:
                logger.warning("Ack-tracked help-request send failed: %s", e)
                # Fall back to plain bus publish
                self._publish_bus("global", "info", help_body)
        else:
            self._publish_bus("global", "info", help_body)

        return request_id

    @_retry_on_busy
    def get_open_help_requests(self) -> list[dict]:
        """Return all open help requests."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT hr.*, wi.title as work_title, wi.priority as work_priority
                FROM help_requests hr
                LEFT JOIN work_items wi ON hr.work_item_id = wi.id
                WHERE hr.status = 'open'
                ORDER BY
                    CASE wi.priority
                        WHEN 'critical' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 3
                        ELSE 4
                    END,
                    hr.created_at ASC
                """,
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def offer_help(self, request_id: int) -> bool:
        """Atomically accept a help request. Returns True if this team won.

        Uses BEGIN IMMEDIATE to prevent race conditions when multiple
        idle teams try to accept the same request.
        """
        self._check_closed()
        conn = self._conn
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise

            try:
                # Check if still open
                row = conn.execute(
                    "SELECT status, work_item_id, requesting_team FROM help_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()

                if row is None or row["status"] != "open":
                    conn.execute("ROLLBACK")
                    return False

                # FIX: BUG-HP-007 - prevent a team from offering help to itself
                if row["requesting_team"] == self.team:
                    conn.execute("ROLLBACK")
                    raise ValueError(
                        f"Team {self.team!r} cannot offer help to its own request"
                    )

                work_item_id = row["work_item_id"]

                # Atomically claim it
                conn.execute(
                    """
                    UPDATE help_requests
                    SET status = 'accepted',
                        accepted_by_team = ?,
                        accepted_by_agent = ?
                    WHERE id = ? AND status = 'open'
                    """,
                    (self.team, self.agent_id, request_id),
                )

                # Assign the work item to this team's agent
                if work_item_id is not None:
                    conn.execute(
                        """
                        UPDATE work_items
                        SET assigned_to = ?, status = 'in_progress', updated_at = ?
                        WHERE id = ?
                        """,
                        (self.agent_id, time.time(), work_item_id),
                    )

                conn.execute("COMMIT")

                # Publish help-accepted to bus (ack-tracked to requesting team)
                accepted_body = {
                    "event": "help-accepted",
                    "request_id": request_id,
                    "assigned_to_team": self.team,
                    "work_item_id": work_item_id,
                }
                if self._ack_protocol is not None:
                    try:
                        requesting_team = row["requesting_team"]
                        self._ack_protocol.send_no_wait(
                            channel="global",
                            msg_type="info",
                            body=accepted_body,
                            target_agent=requesting_team,
                            priority=5,
                            ttl=3600,
                        )
                    except Exception as e:
                        logger.warning("Ack-tracked help-accepted send failed: %s", e)
                        self._publish_bus("global", "info", accepted_body)
                else:
                    self._publish_bus("global", "info", accepted_body)

                # Update load balancer: team is taking on work
                if self._load_balancer is not None:
                    try:
                        load = self._load_balancer.get_load(self.team)
                        new_active = (load["active_tasks"] + 1) if load else 1
                        new_queue = load["queue_depth"] if load else 0
                        self._load_balancer.update_load(
                            self.team, new_active, new_queue,
                        )
                    except Exception:
                        logger.warning(
                            "Load balancer update failed after offer_help "
                            "(team=%s, request=%d)",
                            self.team, request_id, exc_info=True,
                        )

                return True

            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def fulfill_help(self, request_id: int) -> None:
        """Mark a help request as fulfilled and its work item as completed.

        Raises ValueError if this team is not the accepted helper.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT work_item_id, accepted_by_team, requesting_team, status FROM help_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()

                if row is None:
                    self._conn.execute("ROLLBACK")
                    raise ValueError(f"Help request {request_id} does not exist")

                if row["status"] != "accepted":
                    self._conn.execute("ROLLBACK")
                    raise ValueError(
                        f"Help request {request_id} has status {row['status']!r}, "
                        f"expected 'accepted'. Cannot fulfill an un-accepted request."
                    )

                # FIX: BUG-HP-005 - only the assigned team can fulfill a help request
                if row["accepted_by_team"] != self.team:
                    self._conn.execute("ROLLBACK")
                    raise ValueError(
                        f"Team {self.team!r} is not the accepted helper for request {request_id}. "
                        f"Accepted by {row['accepted_by_team']!r}."
                    )

                self._conn.execute(
                    "UPDATE help_requests SET status = 'fulfilled', resolved_at = ? WHERE id = ?",
                    (now, request_id),
                )

                requesting_team = row["requesting_team"]

                if row and row["work_item_id"] is not None:
                    self._conn.execute(
                        "UPDATE work_items SET status = 'completed', updated_at = ? WHERE id = ?",
                        (now, row["work_item_id"]),
                    )

                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        # Publish help-fulfilled notification (ack-tracked to requesting team)
        fulfilled_body = {
            "event": "help-fulfilled",
            "request_id": request_id,
            "fulfilled_by_team": self.team,
        }
        if self._ack_protocol is not None and requesting_team:
            try:
                self._ack_protocol.send_no_wait(
                    channel="global",
                    msg_type="info",
                    body=fulfilled_body,
                    target_agent=requesting_team,
                    priority=5,
                    ttl=3600,
                )
            except Exception as e:
                logger.warning("Ack-tracked help-fulfilled send failed: %s", e)
                self._publish_bus("global", "info", fulfilled_body)
        else:
            self._publish_bus("global", "info", fulfilled_body)

        # Update load balancer: team completed work
        if self._load_balancer is not None:
            try:
                self._load_balancer.record_completion(self.team)
            except Exception:
                logger.warning(
                    "Load balancer completion update failed "
                    "(team=%s, request=%d)",
                    self.team, request_id, exc_info=True,
                )

    # ------------------------------------------------------------------
    # Ack-based reliability
    # ------------------------------------------------------------------

    def check_unacked_requests(self, older_than: float = 60.0) -> list[dict]:
        """Check for unacknowledged help-related messages and retry them.

        Requires an ack_protocol to be configured. Returns a list of
        unacked message dicts from the ack tracking table. If no
        ack_protocol is configured, returns an empty list.

        Parameters
        ----------
        older_than:
            Only consider messages older than this many seconds (default 60).

        Returns
        -------
        list[dict]
            Unacknowledged messages that were retried.
        """
        if self._ack_protocol is None:
            return []
        try:
            unacked = self._ack_protocol.get_unacked(older_than=older_than)
            if unacked:
                self._ack_protocol.retry_unacked(older_than=older_than)
            return unacked
        except Exception as e:
            logger.warning("check_unacked_requests failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Idle detection
    # ------------------------------------------------------------------

    @_retry_on_busy
    def get_idle_teams(self) -> list[dict]:
        """Return teams whose status is 'idle' or 'complete'."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ts.*, GROUP_CONCAT(tc.capability) as capabilities
                FROM team_status ts
                LEFT JOIN team_capabilities tc ON ts.team = tc.team
                WHERE ts.status IN ('idle', 'complete')
                GROUP BY ts.team
                ORDER BY ts.last_updated DESC
                """,
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            caps = d.pop("capabilities", None)
            d["capabilities"] = caps.split(",") if caps else []
            result.append(d)
        return result

    @_retry_on_busy
    def get_busy_teams(self) -> list[dict]:
        """Return teams whose status is 'working' or 'helping'."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM team_status
                WHERE status IN ('working', 'helping')
                ORDER BY progress_pct ASC
                """,
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_teams_needing_help(self) -> list[dict]:
        """Return teams whose status is 'needs_help' or have open help requests."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT ts.*
                FROM team_status ts
                LEFT JOIN help_requests hr ON ts.team = hr.requesting_team AND hr.status = 'open'
                WHERE ts.status = 'needs_help' OR hr.id IS NOT NULL
                ORDER BY ts.progress_pct ASC
                """,
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Zombie / timeout detection
    # ------------------------------------------------------------------

    @_retry_on_busy
    def get_zombie_work_items(self, timeout_seconds: float = 3600) -> list[dict]:
        """Return work items stuck in 'in_progress' longer than *timeout_seconds*."""
        self._check_closed()
        cutoff = time.time() - timeout_seconds
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'in_progress' AND updated_at < ?
                ORDER BY updated_at ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_expired_help_requests(self, timeout_seconds: float = 1800) -> list[dict]:
        """Return help requests accepted but not fulfilled within *timeout_seconds*."""
        self._check_closed()
        cutoff = time.time() - timeout_seconds
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM help_requests
                WHERE status = 'accepted' AND resolved_at IS NULL AND created_at < ?
                ORDER BY created_at ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Auto-matching
    # ------------------------------------------------------------------

    @_retry_on_busy
    def find_compatible_helpers(self, request_id: int) -> list[dict]:
        """Find idle/complete teams whose capabilities match a help request.

        Parameters
        ----------
        request_id:
            The help request to match against.

        Returns
        -------
        List of team dicts with matching capabilities, sorted by match quality.

        Note
        ----
        FIX: TOCTOU race — previously released the lock between querying the
        help request and querying idle teams. Now fetches both in a single
        transaction to ensure a consistent snapshot.
        """
        self._check_closed()
        with self._lock:
            # FIX: TOCTOU — fetch request capabilities AND idle teams in a
            # single lock acquisition to avoid races where team status changes
            # between the two queries.
            row = self._conn.execute(
                "SELECT required_capabilities FROM help_requests WHERE id = ?",
                (request_id,),
            ).fetchone()

            if row is None:
                return []

            required = json.loads(row["required_capabilities"]) if row["required_capabilities"] else []

            # Fetch idle teams in the same lock scope
            idle_rows = self._conn.execute(
                """
                SELECT ts.*, GROUP_CONCAT(tc.capability) as capabilities
                FROM team_status ts
                LEFT JOIN team_capabilities tc ON ts.team = tc.team
                WHERE ts.status IN ('idle', 'complete')
                GROUP BY ts.team
                ORDER BY ts.last_updated DESC
                """,
            ).fetchall()

        idle_teams = []
        for r in idle_rows:
            d = dict(r)
            caps = d.pop("capabilities", None)
            d["capabilities"] = caps.split(",") if caps else []
            idle_teams.append(d)

        if not required:
            # No specific capabilities needed — all idle teams are compatible
            return idle_teams

        # Score teams by how many required capabilities they have
        scored = []
        required_set = set(required)
        for team_info in idle_teams:
            team_caps = set(team_info.get("capabilities", []))
            match_count = len(team_caps & required_set)
            if match_count > 0:
                team_info["match_score"] = match_count / len(required_set)
                scored.append(team_info)

        # Sort by match score descending
        scored.sort(key=lambda t: t.get("match_score", 0), reverse=True)
        return scored

    @_retry_on_busy
    def auto_assign_idle_teams(self) -> list[dict]:
        """Automatically assign idle teams to open help requests.

        Matches based on capabilities. Uses BEGIN IMMEDIATE for atomic
        assignment to prevent double-assignment races.

        FIX: N+1 query eliminated — all required data (open requests, idle
        teams, capabilities) is fetched in a SINGLE transaction before the
        matching loop. Matching is done in pure Python without re-querying.

        Returns
        -------
        List of assignment dicts: {"request_id", "team", "work_item_id"}.
        """
        self._check_closed()

        # FIX: Fetch ALL required data in a single lock acquisition to avoid
        # O(n) lock acquisitions and re-queries inside the loop.
        with self._lock:
            open_rows = self._conn.execute(
                """
                SELECT hr.*, wi.title as work_title, wi.priority as work_priority
                FROM help_requests hr
                LEFT JOIN work_items wi ON hr.work_item_id = wi.id
                WHERE hr.status = 'open'
                ORDER BY
                    CASE wi.priority
                        WHEN 'critical' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 3
                        ELSE 4
                    END,
                    hr.created_at ASC
                """,
            ).fetchall()

            idle_rows = self._conn.execute(
                """
                SELECT ts.*, GROUP_CONCAT(tc.capability) as capabilities
                FROM team_status ts
                LEFT JOIN team_capabilities tc ON ts.team = tc.team
                WHERE ts.status IN ('idle', 'complete')
                GROUP BY ts.team
                ORDER BY ts.last_updated DESC
                """,
            ).fetchall()

        open_requests = [dict(r) for r in open_rows]
        if not open_requests:
            return []

        all_idle_teams = []
        for r in idle_rows:
            d = dict(r)
            caps = d.pop("capabilities", None)
            d["capabilities"] = caps.split(",") if caps else []
            all_idle_teams.append(d)

        if not all_idle_teams:
            return []

        # Pre-parse required_capabilities from the already-fetched request data
        # so we don't need to re-query inside the loop.
        request_caps = {}
        for req in open_requests:
            rc = req.get("required_capabilities")
            request_caps[req["id"]] = json.loads(rc) if rc else []

        assignments = []
        assigned_teams = set()
        idle_team_names = [t["team"] for t in all_idle_teams]

        for req in open_requests:
            request_id = req["id"]

            # Use pre-fetched capabilities instead of re-querying
            required = request_caps[request_id]

            # --- Capability-based matching ---
            # When a CapabilityRegistry is provided, delegate scoring to it
            # for more sophisticated proficiency-weighted matching.
            if self._capability_registry is not None and required:
                try:
                    best_team = self._capability_registry.match_work_to_team(
                        work_item={"required_capabilities": required},
                        exclude_teams=[req["requesting_team"]] + list(assigned_teams),
                        idle_teams=idle_team_names,
                    )
                    if best_team is not None:
                        # Build helpers list with the registry's pick first,
                        # then remaining idle teams as fallback
                        helpers = [
                            t for t in all_idle_teams if t["team"] == best_team
                        ]
                        # If load_balancer is available and multiple teams
                        # had equal capability scores, use it to break ties
                        if self._load_balancer is not None:
                            remaining = [
                                t for t in all_idle_teams
                                if t["team"] != best_team
                                and t["team"] != req["requesting_team"]
                                and t["team"] not in assigned_teams
                            ]
                            if remaining:
                                remaining_names = [t["team"] for t in remaining]
                                least = self._load_balancer.get_least_loaded(remaining_names)
                                if least:
                                    helpers.extend(
                                        t for t in remaining if t["team"] == least
                                    )
                    else:
                        helpers = []
                except Exception:
                    logger.warning(
                        "CapabilityRegistry match failed for request %d, "
                        "falling back to built-in matching",
                        request_id, exc_info=True,
                    )
                    # Fall through to built-in matching below
                    helpers = None
            else:
                helpers = None

            # Built-in matching fallback (when no registry or registry failed)
            if helpers is None:
                if required:
                    helpers = []
                    required_set = set(required)
                    for team_info in all_idle_teams:
                        team_caps = set(team_info.get("capabilities", []))
                        match_count = len(team_caps & required_set)
                        if match_count > 0:
                            helper = dict(team_info)
                            helper["match_score"] = match_count / len(required_set)
                            helpers.append(helper)
                    helpers.sort(key=lambda t: t.get("match_score", 0), reverse=True)

                    # When load_balancer is available, use it to break ties
                    # among top-scoring helpers
                    if self._load_balancer is not None and len(helpers) > 1:
                        top_score = helpers[0].get("match_score", 0)
                        tied = [h for h in helpers if h.get("match_score", 0) == top_score]
                        if len(tied) > 1:
                            tied_names = [h["team"] for h in tied]
                            least = self._load_balancer.get_least_loaded(tied_names)
                            if least:
                                # Move least-loaded to front
                                helpers = (
                                    [h for h in helpers if h["team"] == least]
                                    + [h for h in helpers if h["team"] != least]
                                )
                else:
                    # No specific capabilities needed — all idle teams are compatible
                    helpers = list(all_idle_teams)

                    # Use load balancer to pick among all idle teams
                    if self._load_balancer is not None and len(helpers) > 1:
                        candidate_names = [h["team"] for h in helpers]
                        least = self._load_balancer.get_least_loaded(candidate_names)
                        if least:
                            helpers = (
                                [h for h in helpers if h["team"] == least]
                                + [h for h in helpers if h["team"] != least]
                            )

            # FIX: BUG-HP-013 - only fall back to any team if no capabilities were required
            if not helpers and not required:
                helpers = list(all_idle_teams)

            for helper in helpers:
                helper_team = helper["team"]
                # Skip self-assignment
                if helper_team == req["requesting_team"]:
                    continue
                # Skip teams already assigned in this round
                if helper_team in assigned_teams:
                    continue

                # Try to atomically claim — create a temporary HelpProtocol
                # for the helper team to offer help
                conn = self._conn
                with self._lock:
                    begin_ok = False
                    for _attempt in range(_MAX_RETRIES):
                        try:
                            conn.execute("BEGIN IMMEDIATE")
                            begin_ok = True
                            break
                        except sqlite3.OperationalError as e:
                            if _is_busy_or_locked(e):
                                logger.warning(
                                    "BEGIN IMMEDIATE failed for request %d, team %s (attempt %d/%d): %s",
                                    request_id, helper_team, _attempt + 1, _MAX_RETRIES, e,
                                )
                                time.sleep(_RETRY_BACKOFF * (2 ** _attempt))
                            else:
                                raise
                    if not begin_ok:
                        logger.error(
                            "BEGIN IMMEDIATE exhausted retries for request %d, team %s",
                            request_id, helper_team,
                        )
                        continue

                    try:
                        row = conn.execute(
                            "SELECT status, work_item_id FROM help_requests WHERE id = ?",
                            (request_id,),
                        ).fetchone()

                        if row is None or row["status"] != "open":
                            conn.execute("ROLLBACK")
                            break  # Request already taken

                        work_item_id = row["work_item_id"]
                        now = time.time()

                        conn.execute(
                            """
                            UPDATE help_requests
                            SET status = 'accepted',
                                accepted_by_team = ?,
                                accepted_by_agent = 'auto-assign'
                            WHERE id = ? AND status = 'open'
                            """,
                            (helper_team, request_id),
                        )

                        if work_item_id is not None:
                            conn.execute(
                                """
                                UPDATE work_items
                                SET assigned_to = ?, status = 'in_progress', updated_at = ?
                                WHERE id = ?
                                """,
                                (f"{helper_team}/auto", now, work_item_id),
                            )

                        # Update helper team status to 'helping'
                        conn.execute(
                            """
                            UPDATE team_status
                            SET status = 'helping', current_task = ?, last_updated = ?
                            WHERE team = ?
                            """,
                            (f"Helping {req['requesting_team']}: {req['description'][:100]}",
                             now, helper_team),
                        )

                        conn.execute("COMMIT")

                        assigned_teams.add(helper_team)
                        assignments.append({
                            "request_id": request_id,
                            "team": helper_team,
                            "work_item_id": work_item_id,
                        })

                        # Publish assignment to bus
                        self._publish_bus("global", "info", {
                            "event": "help-accepted",
                            "request_id": request_id,
                            "assigned_to_team": helper_team,
                            "work_item_id": work_item_id,
                            "auto_assigned": True,
                        })

                        # Update load balancer: helper team is taking on work
                        if self._load_balancer is not None:
                            try:
                                load = self._load_balancer.get_load(helper_team)
                                new_active = (load["active_tasks"] + 1) if load else 1
                                new_queue = load["queue_depth"] if load else 0
                                self._load_balancer.update_load(
                                    helper_team, new_active, new_queue,
                                )
                            except Exception:
                                logger.warning(
                                    "Load balancer update failed after auto-assign "
                                    "(team=%s, request=%d)",
                                    helper_team, request_id, exc_info=True,
                                )

                        break  # Move to next request

                    except Exception:
                        try:
                            conn.execute("ROLLBACK")
                        except sqlite3.OperationalError:
                            pass
                        raise

        return assignments
