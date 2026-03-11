"""
Team Help Protocol and Idle Detection for Proto A.

Solves the observed problem: teams finishing and sitting idle while one team
is still busy.  Provides work-item tracking, help-request/offer/accept flow,
idle detection, capability matching, and automatic assignment of idle teams
to teams that need help.

All writes use BEGIN IMMEDIATE for atomic operations and threading.Lock for
connection safety, following Proto A patterns (SQLite WAL mode).
"""

import json
import logging
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

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


def _retry_on_busy(func):
    """Decorator: retry a method on sqlite3.OperationalError (SQLITE_BUSY)."""
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
                    delay *= 2
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
    """

    def __init__(self, db_path: str, bus_dir: str, team: str, agent_id: str) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self.team = team
        self.agent_id = agent_id
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path, timeout=30, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_HELP_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Bus message helpers
    # ------------------------------------------------------------------

    def _publish_bus(self, channel: str, msg_type: str, body: dict) -> Optional[str]:
        """Publish a message to the JSONL bus. Returns message ID or None."""
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
        safe_ch = channel.replace("/", "_").replace("..", "_")
        filepath = os.path.join(self.bus_dir, f"{safe_ch}.jsonl")
        os.makedirs(self.bus_dir, exist_ok=True)
        fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
        return msg["id"]

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
        now = time.time()
        caps_json = json.dumps(required_caps) if required_caps else None
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
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                UPDATE work_items
                SET status = 'available_for_help', updated_at = ?
                WHERE id = ? AND team = ?
                """,
                (now, work_item_id, self.team),
            )
            self._conn.commit()

    @_retry_on_busy
    def complete_work_item(self, work_item_id: int) -> None:
        """Mark a work item as completed."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                UPDATE work_items
                SET status = 'completed', updated_at = ?
                WHERE id = ?
                """,
                (now, work_item_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Help request flow
    # ------------------------------------------------------------------

    @_retry_on_busy
    def request_help(self, work_item_id: int, description: str) -> int:
        """Create a help request for a work item. Returns the request ID.

        Also marks the work item as available_for_help and publishes a
        help-request message on the bus.
        """
        now = time.time()
        with self._lock:
            # Get work item details for capabilities
            row = self._conn.execute(
                "SELECT required_capabilities, estimated_minutes FROM work_items WHERE id = ?",
                (work_item_id,),
            ).fetchone()
            caps = row["required_capabilities"] if row else None
            est = row["estimated_minutes"] if row else None

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

        # Publish help-request to bus
        self._publish_bus("global", "info", {
            "event": "help-request",
            "request_id": request_id,
            "work_item_id": work_item_id,
            "description": description,
            "required_capabilities": json.loads(caps) if caps else [],
            "estimated_minutes": est,
        })

        return request_id

    @_retry_on_busy
    def get_open_help_requests(self) -> list[dict]:
        """Return all open help requests."""
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
        conn = self._conn
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise

            try:
                # Check if still open
                row = conn.execute(
                    "SELECT status, work_item_id FROM help_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()

                if row is None or row["status"] != "open":
                    conn.execute("ROLLBACK")
                    return False

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

                # Publish help-accepted to bus
                self._publish_bus("global", "info", {
                    "event": "help-accepted",
                    "request_id": request_id,
                    "assigned_to_team": self.team,
                    "work_item_id": work_item_id,
                })

                return True

            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def fulfill_help(self, request_id: int) -> None:
        """Mark a help request as fulfilled and its work item as completed."""
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT work_item_id FROM help_requests WHERE id = ?",
                (request_id,),
            ).fetchone()

            self._conn.execute(
                "UPDATE help_requests SET status = 'fulfilled', resolved_at = ? WHERE id = ?",
                (now, request_id),
            )

            if row and row["work_item_id"] is not None:
                self._conn.execute(
                    "UPDATE work_items SET status = 'completed', updated_at = ? WHERE id = ?",
                    (now, row["work_item_id"]),
                )

            self._conn.commit()

    # ------------------------------------------------------------------
    # Idle detection
    # ------------------------------------------------------------------

    @_retry_on_busy
    def get_idle_teams(self) -> list[dict]:
        """Return teams whose status is 'idle' or 'complete'."""
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
        """
        with self._lock:
            # Get request's required capabilities
            row = self._conn.execute(
                "SELECT required_capabilities FROM help_requests WHERE id = ?",
                (request_id,),
            ).fetchone()

        if row is None:
            return []

        required = json.loads(row["required_capabilities"]) if row["required_capabilities"] else []

        idle_teams = self.get_idle_teams()

        if not required:
            # No specific capabilities needed — all idle teams are compatible
            return idle_teams

        # Score teams by how many required capabilities they have
        scored = []
        for team_info in idle_teams:
            team_caps = set(team_info.get("capabilities", []))
            required_set = set(required)
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

        Returns
        -------
        List of assignment dicts: {"request_id", "team", "work_item_id"}.
        """
        open_requests = self.get_open_help_requests()
        if not open_requests:
            return []

        assignments = []

        for req in open_requests:
            request_id = req["id"]
            helpers = self.find_compatible_helpers(request_id)

            if not helpers:
                # Fall back to any idle team
                helpers = self.get_idle_teams()

            for helper in helpers:
                helper_team = helper["team"]
                # Skip self-assignment
                if helper_team == req["requesting_team"]:
                    continue

                # Try to atomically claim — create a temporary HelpProtocol
                # for the helper team to offer help
                conn = self._conn
                with self._lock:
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                    except sqlite3.OperationalError:
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

                        break  # Move to next request

                    except Exception:
                        try:
                            conn.execute("ROLLBACK")
                        except sqlite3.OperationalError:
                            pass
                        raise

        return assignments
