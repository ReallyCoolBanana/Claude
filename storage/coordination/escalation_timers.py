"""Escalation Timer Manager -- background timer framework per SOP-012v2.

Provides automatic escalation for coordination problems that go unresolved.
Timers are SQLite-backed (survive restarts) and checked by a background
daemon thread.

Timer types:
- BLOCKER_ESCALATION: fires 3 min after blocker reported, if unresolved
- IDLE_AGENT_REASSIGNMENT: fires 1 min after agent completes, if still idle
- STALL_ALERT: fires 3 min after no progress update from agent
- DEPENDENCY_TIMEOUT: fires 5 min after agent starts waiting for upstream

When a timer expires, the manager:
1. Updates the timer status in the database
2. Generates an escalation action dict
3. Publishes the escalation to the JSONL message bus
4. Optionally invokes a callback (if registered)

Integrates with coordinator_hub.py (can be wired into the heartbeat loop)
and with the existing bus infrastructure for notification delivery.

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
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Timer types with default timeouts (seconds)
TIMER_TYPES = {
    "BLOCKER_ESCALATION": 180,         # 3 minutes
    "IDLE_AGENT_REASSIGNMENT": 60,     # 1 minute
    "STALL_ALERT": 180,                # 3 minutes
    "DEPENDENCY_TIMEOUT": 300,         # 5 minutes
}

TIMER_STATUS_ACTIVE = "active"
TIMER_STATUS_FIRED = "fired"
TIMER_STATUS_CANCELLED = "cancelled"
TIMER_STATUS_ACKNOWLEDGED = "acknowledged"

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS escalation_timers (
    id TEXT PRIMARY KEY,
    timer_type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    team TEXT NOT NULL,
    timeout_seconds REAL NOT NULL,
    context TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    fired_at REAL,
    cancelled_at REAL,
    escalation_action TEXT
);

CREATE TABLE IF NOT EXISTS escalation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timer_id TEXT NOT NULL,
    timer_type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    team TEXT NOT NULL,
    event TEXT NOT NULL,
    details TEXT,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_timers_status_expires
    ON escalation_timers(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_timers_agent
    ON escalation_timers(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_timers_team
    ON escalation_timers(team, status);
CREATE INDEX IF NOT EXISTS idx_timers_type
    ON escalation_timers(timer_type, status);
CREATE INDEX IF NOT EXISTS idx_escalation_log_timer
    ON escalation_log(timer_id);
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
                    delay = min(delay * 2, 0.05)
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------


def _open_db(db_path: str, busy_timeout_ms: int = 30000) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection and ensure schema exists."""
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
    """Fire-and-forget bus notification (JSONL atomic append)."""
    if bus_dir is None:
        return
    try:
        os.makedirs(bus_dir, exist_ok=True)
        msg = {
            "id": str(uuid.uuid4()),
            "type": "blocker",
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
# EscalationTimerManager
# ===================================================================


class EscalationTimerManager:
    """Background timer framework for automatic escalation per SOP-012v2.

    Timers are persisted in SQLite so they survive process restarts.
    A background daemon thread periodically checks for expired timers
    and fires escalation actions.

    Parameters
    ----------
    db_path:
        Path to the SQLite database for timer state.
    bus_dir:
        Optional path to the JSONL bus directory for escalation messages.
    callbacks:
        Optional dict mapping timer_type -> callable(timer_dict).
        Called when a timer of that type fires.
    """

    def __init__(
        self,
        db_path: str,
        bus_dir: Optional[str] = None,
        callbacks: Optional[dict[str, Callable]] = None,
    ) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self._callbacks: dict[str, Callable] = callbacks or {}
        self._lock = threading.Lock()
        self._conn = _open_db(db_path)
        self._closed = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("EscalationTimerManager instance is closed")

    # ------------------------------------------------------------------
    # Timer CRUD
    # ------------------------------------------------------------------

    @_retry_on_busy
    def start_timer(
        self,
        timer_type: str,
        agent_id: str,
        team: str,
        timeout_seconds: Optional[float] = None,
        context: Optional[dict] = None,
    ) -> str:
        """Start an escalation timer.

        Parameters
        ----------
        timer_type:
            One of TIMER_TYPES keys, or a custom string.
        agent_id:
            The agent this timer is associated with.
        team:
            The team this timer is associated with.
        timeout_seconds:
            Seconds until escalation.  If None, uses the default for
            the timer_type (or 300 seconds for custom types).
        context:
            Optional dict with additional context (e.g., blocker description,
            pipeline_id, dependency name).

        Returns
        -------
        str
            The timer ID (UUID).
        """
        self._check_closed()

        if timeout_seconds is None:
            timeout_seconds = TIMER_TYPES.get(timer_type, 300)

        timer_id = str(uuid.uuid4())
        now = time.time()
        expires_at = now + timeout_seconds
        context_json = json.dumps(context) if context else None

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """INSERT INTO escalation_timers
                       (id, timer_type, agent_id, team, timeout_seconds,
                        context, status, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                    (timer_id, timer_type, agent_id, team, timeout_seconds,
                     context_json, now, expires_at),
                )
                self._conn.execute(
                    """INSERT INTO escalation_log
                       (timer_id, timer_type, agent_id, team, event, details, ts)
                       VALUES (?, ?, ?, ?, 'started', ?, ?)""",
                    (timer_id, timer_type, agent_id, team,
                     json.dumps({"timeout_seconds": timeout_seconds, "context": context}),
                     now),
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        logger.info(
            "Escalation timer started: type=%s agent=%s team=%s timeout=%.0fs id=%s",
            timer_type, agent_id, team, timeout_seconds, timer_id,
        )
        return timer_id

    @_retry_on_busy
    def cancel_timer(self, timer_id: str) -> bool:
        """Cancel an active timer.

        Parameters
        ----------
        timer_id:
            The timer to cancel.

        Returns
        -------
        bool
            True if the timer was cancelled, False if it was already
            fired/cancelled or not found.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT status, timer_type, agent_id, team FROM escalation_timers WHERE id = ?",
                    (timer_id,),
                ).fetchone()

                if row is None or row["status"] != TIMER_STATUS_ACTIVE:
                    self._conn.execute("ROLLBACK")
                    return False

                self._conn.execute(
                    """UPDATE escalation_timers
                       SET status = 'cancelled', cancelled_at = ?
                       WHERE id = ?""",
                    (now, timer_id),
                )
                self._conn.execute(
                    """INSERT INTO escalation_log
                       (timer_id, timer_type, agent_id, team, event, ts)
                       VALUES (?, ?, ?, ?, 'cancelled', ?)""",
                    (timer_id, row["timer_type"], row["agent_id"],
                     row["team"], now),
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        logger.info("Escalation timer cancelled: %s", timer_id)
        return True

    @_retry_on_busy
    def cancel_timers_for_agent(self, agent_id: str,
                                timer_type: Optional[str] = None) -> int:
        """Cancel all active timers for an agent, optionally filtered by type.

        Useful when a blocker is resolved or an agent resumes work.

        Parameters
        ----------
        agent_id:
            The agent whose timers to cancel.
        timer_type:
            If provided, only cancel timers of this type.

        Returns
        -------
        int
            Number of timers cancelled.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if timer_type:
                    rows = self._conn.execute(
                        """SELECT id, timer_type, team FROM escalation_timers
                           WHERE agent_id = ? AND timer_type = ? AND status = 'active'""",
                        (agent_id, timer_type),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """SELECT id, timer_type, team FROM escalation_timers
                           WHERE agent_id = ? AND status = 'active'""",
                        (agent_id,),
                    ).fetchall()

                if not rows:
                    self._conn.execute("ROLLBACK")
                    return 0

                timer_ids = [r["id"] for r in rows]
                placeholders = ",".join("?" for _ in timer_ids)
                self._conn.execute(
                    f"""UPDATE escalation_timers
                        SET status = 'cancelled', cancelled_at = ?
                        WHERE id IN ({placeholders})""",
                    [now] + timer_ids,
                )

                # Log cancellations
                for r in rows:
                    self._conn.execute(
                        """INSERT INTO escalation_log
                           (timer_id, timer_type, agent_id, team, event, ts)
                           VALUES (?, ?, ?, ?, 'cancelled', ?)""",
                        (r["id"], r["timer_type"], agent_id, r["team"], now),
                    )

                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        count = len(timer_ids)
        logger.info(
            "Cancelled %d timer(s) for agent %s (type=%s)",
            count, agent_id, timer_type or "all",
        )
        return count

    # ------------------------------------------------------------------
    # Expiry checking
    # ------------------------------------------------------------------

    @_retry_on_busy
    def check_expired(self) -> list:
        """Check for expired timers and return escalation actions.

        Atomically transitions active timers past their expires_at to
        'fired' status and generates escalation action dicts.

        Returns
        -------
        list[dict]
            Escalation actions for each fired timer.  Each dict contains:
            timer_id, timer_type, agent_id, team, context, fired_at,
            escalation_action.
        """
        self._check_closed()
        now = time.time()
        escalations = []

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """SELECT * FROM escalation_timers
                       WHERE status = 'active' AND expires_at <= ?
                       ORDER BY expires_at ASC""",
                    (now,),
                ).fetchall()

                if not rows:
                    self._conn.execute("ROLLBACK")
                    return []

                for row in rows:
                    timer_id = row["id"]
                    timer_type = row["timer_type"]
                    agent_id = row["agent_id"]
                    team = row["team"]
                    context = json.loads(row["context"]) if row["context"] else {}

                    # Build escalation action based on timer type
                    action = self._build_escalation_action(
                        timer_type, agent_id, team, context,
                    )

                    self._conn.execute(
                        """UPDATE escalation_timers
                           SET status = 'fired',
                               fired_at = ?,
                               escalation_action = ?
                           WHERE id = ?""",
                        (now, json.dumps(action), timer_id),
                    )
                    self._conn.execute(
                        """INSERT INTO escalation_log
                           (timer_id, timer_type, agent_id, team, event, details, ts)
                           VALUES (?, ?, ?, ?, 'fired', ?, ?)""",
                        (timer_id, timer_type, agent_id, team,
                         json.dumps(action), now),
                    )

                    escalation = {
                        "timer_id": timer_id,
                        "timer_type": timer_type,
                        "agent_id": agent_id,
                        "team": team,
                        "context": context,
                        "fired_at": now,
                        "escalation_action": action,
                    }
                    escalations.append(escalation)

                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        # Post-commit: publish bus notifications and invoke callbacks
        for esc in escalations:
            _bus_notify(
                self.bus_dir, "escalation", esc["agent_id"], esc["team"],
                {
                    "event": "timer-fired",
                    "timer_id": esc["timer_id"],
                    "timer_type": esc["timer_type"],
                    "agent_id": esc["agent_id"],
                    "team": esc["team"],
                    "escalation_action": esc["escalation_action"],
                },
            )

            logger.warning(
                "Escalation timer fired: type=%s agent=%s team=%s action=%s",
                esc["timer_type"], esc["agent_id"], esc["team"],
                esc["escalation_action"].get("action", "unknown"),
            )

            # Invoke registered callback if any
            cb = self._callbacks.get(esc["timer_type"])
            if cb is not None:
                try:
                    cb(esc)
                except Exception:
                    logger.exception(
                        "Escalation callback failed for timer %s",
                        esc["timer_id"],
                    )

        return escalations

    @staticmethod
    def _build_escalation_action(
        timer_type: str,
        agent_id: str,
        team: str,
        context: dict,
    ) -> dict:
        """Build an escalation action dict based on timer type.

        Returns a dict describing what should happen now that the timer
        has fired.
        """
        if timer_type == "BLOCKER_ESCALATION":
            return {
                "action": "escalate_blocker",
                "description": (
                    f"Agent {agent_id} on team {team} has been blocked for "
                    f"over 3 minutes. Escalating to coordinator for intervention."
                ),
                "recommended_actions": [
                    "reassign_task",
                    "request_help_from_idle_team",
                    "break_dependency",
                ],
                "severity": "high",
                "blocker_info": context,
            }

        if timer_type == "IDLE_AGENT_REASSIGNMENT":
            return {
                "action": "reassign_idle_agent",
                "description": (
                    f"Agent {agent_id} on team {team} has been idle for "
                    f"over 1 minute after completion. Reassigning to help."
                ),
                "recommended_actions": [
                    "assign_to_open_help_request",
                    "assign_to_queued_work",
                    "mark_team_available",
                ],
                "severity": "medium",
            }

        if timer_type == "STALL_ALERT":
            return {
                "action": "stall_alert",
                "description": (
                    f"Agent {agent_id} on team {team} has not sent a "
                    f"progress update in over 3 minutes. May be stalled."
                ),
                "recommended_actions": [
                    "send_heartbeat_check",
                    "check_agent_health",
                    "prepare_replacement",
                ],
                "severity": "high",
            }

        if timer_type == "DEPENDENCY_TIMEOUT":
            return {
                "action": "dependency_timeout",
                "description": (
                    f"Agent {agent_id} on team {team} has been waiting "
                    f"for upstream dependency for over 5 minutes."
                ),
                "recommended_actions": [
                    "check_upstream_status",
                    "bypass_dependency",
                    "reassign_upstream_task",
                ],
                "severity": "high",
                "dependency_info": context,
            }

        # Custom timer type
        return {
            "action": f"custom_escalation_{timer_type}",
            "description": (
                f"Custom timer '{timer_type}' fired for agent {agent_id} "
                f"on team {team}."
            ),
            "severity": "medium",
            "context": context,
        }

    # ------------------------------------------------------------------
    # Background monitor thread
    # ------------------------------------------------------------------

    def start_monitor_thread(self, check_interval: float = 10.0) -> threading.Thread:
        """Start a background daemon thread that checks for expired timers.

        The thread runs in the background and calls check_expired() at
        regular intervals.  It will stop when stop_monitor() is called
        or when the manager is closed.

        Parameters
        ----------
        check_interval:
            Seconds between expiry checks.

        Returns
        -------
        threading.Thread
            The monitor thread (already started).
        """
        self._check_closed()

        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            logger.warning("Monitor thread already running")
            return self._monitor_thread

        self._stop_event.clear()

        def _monitor_loop():
            logger.info(
                "Escalation monitor thread started (interval=%.1fs)",
                check_interval,
            )
            while not self._stop_event.is_set():
                try:
                    fired = self.check_expired()
                    if fired:
                        logger.info(
                            "Escalation monitor: %d timer(s) fired", len(fired),
                        )
                except Exception:
                    logger.exception("Escalation monitor: error checking timers")

                self._stop_event.wait(timeout=check_interval)

            logger.info("Escalation monitor thread stopped")

        thread = threading.Thread(
            target=_monitor_loop,
            name="escalation-monitor",
            daemon=True,
        )
        thread.start()
        self._monitor_thread = thread
        return thread

    def stop_monitor(self) -> None:
        """Stop the background monitor thread."""
        self._stop_event.set()
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)
            self._monitor_thread = None

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    @_retry_on_busy
    def get_active_timers(self) -> list:
        """Get all currently active (not yet fired/cancelled) timers.

        Returns
        -------
        list[dict]
            Active timer records, sorted by expires_at ascending.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM escalation_timers
                   WHERE status = 'active'
                   ORDER BY expires_at ASC""",
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("context"):
                d["context"] = json.loads(d["context"])
            result.append(d)
        return result

    @_retry_on_busy
    def get_fired_timers(self, since: Optional[float] = None) -> list:
        """Get timers that have fired.

        Parameters
        ----------
        since:
            If provided, only return timers fired after this timestamp.

        Returns
        -------
        list[dict]
            Fired timer records.
        """
        self._check_closed()
        with self._lock:
            if since:
                rows = self._conn.execute(
                    """SELECT * FROM escalation_timers
                       WHERE status = 'fired' AND fired_at >= ?
                       ORDER BY fired_at DESC""",
                    (since,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM escalation_timers
                       WHERE status = 'fired'
                       ORDER BY fired_at DESC""",
                ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("context"):
                d["context"] = json.loads(d["context"])
            if d.get("escalation_action"):
                d["escalation_action"] = json.loads(d["escalation_action"])
            result.append(d)
        return result

    @_retry_on_busy
    def get_timer(self, timer_id: str) -> Optional[dict]:
        """Get a specific timer by ID.

        Returns
        -------
        dict or None
        """
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM escalation_timers WHERE id = ?",
                (timer_id,),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("context"):
            d["context"] = json.loads(d["context"])
        if d.get("escalation_action"):
            d["escalation_action"] = json.loads(d["escalation_action"])
        return d

    @_retry_on_busy
    def get_timers_for_agent(self, agent_id: str,
                             status: Optional[str] = None) -> list:
        """Get all timers for a specific agent.

        Parameters
        ----------
        agent_id:
            The agent to query.
        status:
            If provided, filter by timer status.

        Returns
        -------
        list[dict]
        """
        self._check_closed()
        with self._lock:
            if status:
                rows = self._conn.execute(
                    """SELECT * FROM escalation_timers
                       WHERE agent_id = ? AND status = ?
                       ORDER BY created_at DESC""",
                    (agent_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM escalation_timers
                       WHERE agent_id = ?
                       ORDER BY created_at DESC""",
                    (agent_id,),
                ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("context"):
                d["context"] = json.loads(d["context"])
            if d.get("escalation_action"):
                d["escalation_action"] = json.loads(d["escalation_action"])
            result.append(d)
        return result

    @_retry_on_busy
    def get_escalation_log(self, timer_id: Optional[str] = None,
                           limit: int = 100) -> list:
        """Get escalation log entries.

        Parameters
        ----------
        timer_id:
            Filter to a specific timer. None for all.
        limit:
            Maximum entries to return.

        Returns
        -------
        list[dict]
        """
        self._check_closed()
        with self._lock:
            if timer_id:
                rows = self._conn.execute(
                    """SELECT * FROM escalation_log
                       WHERE timer_id = ?
                       ORDER BY ts DESC LIMIT ?""",
                    (timer_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM escalation_log
                       ORDER BY ts DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def acknowledge_escalation(self, timer_id: str, agent_id: str) -> bool:
        """Mark a fired escalation as acknowledged.

        Used by the coordinator or human operator to indicate the
        escalation has been seen and is being handled.

        Returns True if acknowledged, False if not found/already handled.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT status, timer_type, team FROM escalation_timers WHERE id = ?",
                    (timer_id,),
                ).fetchone()

                if row is None or row["status"] != TIMER_STATUS_FIRED:
                    self._conn.execute("ROLLBACK")
                    return False

                self._conn.execute(
                    """UPDATE escalation_timers
                       SET status = 'acknowledged'
                       WHERE id = ?""",
                    (timer_id,),
                )
                self._conn.execute(
                    """INSERT INTO escalation_log
                       (timer_id, timer_type, agent_id, team, event, details, ts)
                       VALUES (?, ?, ?, ?, 'acknowledged', ?, ?)""",
                    (timer_id, row["timer_type"], agent_id, row["team"],
                     json.dumps({"acknowledged_by": agent_id}), now),
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        return True

    @_retry_on_busy
    def get_summary(self) -> dict:
        """Get a summary of all timers by status and type.

        Returns
        -------
        dict
            {
                "active": int,
                "fired": int,
                "cancelled": int,
                "acknowledged": int,
                "by_type": {timer_type: {"active": int, "fired": int, ...}},
            }
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT timer_type, status, COUNT(*) as cnt
                   FROM escalation_timers
                   GROUP BY timer_type, status""",
            ).fetchall()

        summary: dict[str, Any] = {
            "active": 0,
            "fired": 0,
            "cancelled": 0,
            "acknowledged": 0,
            "by_type": {},
        }

        for row in rows:
            tt = row["timer_type"]
            status = row["status"]
            cnt = row["cnt"]

            if status in summary:
                summary[status] += cnt

            if tt not in summary["by_type"]:
                summary["by_type"][tt] = {
                    "active": 0, "fired": 0, "cancelled": 0, "acknowledged": 0,
                }
            if status in summary["by_type"][tt]:
                summary["by_type"][tt][status] = cnt

        return summary

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Clean shutdown: stop monitor thread and close database."""
        self.stop_monitor()
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
