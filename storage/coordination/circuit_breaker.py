"""Circuit Breaker -- per-endpoint failure tracking with three-state machine.

Prevents cascading failures by tracking per-endpoint error rates and
short-circuiting calls when an endpoint is unhealthy.  Integrates with
the coordination system's SQLite WAL infrastructure.

States:
- CLOSED: Normal operation, failures tracked against threshold.
- OPEN: All calls rejected immediately (fail fast).  After recovery_timeout
  seconds, transitions to HALF_OPEN.
- HALF_OPEN: One test call allowed.  Success resets to CLOSED; failure
  reopens to OPEN.

Usage:
    cb = CircuitBreaker("/path/to/db")
    if cb.call_allowed("https://api.example.com/v1/data", "agent-1"):
        try:
            result = do_api_call(...)
            cb.record_success("https://api.example.com/v1/data", "agent-1")
        except Exception:
            cb.record_failure("https://api.example.com/v1/data", "agent-1")
    else:
        # Circuit is OPEN -- fail fast, use fallback
        ...

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
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CIRCUIT_CLOSED = "closed"
CIRCUIT_OPEN = "open"
CIRCUIT_HALF_OPEN = "half_open"

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS circuit_breaker (
    endpoint TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'closed',
    failure_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    last_failure_time REAL,
    last_success_time REAL,
    last_state_change REAL NOT NULL,
    half_open_attempts INTEGER NOT NULL DEFAULT 0,
    failure_threshold INTEGER NOT NULL DEFAULT 5,
    recovery_timeout REAL NOT NULL DEFAULT 60.0,
    half_open_max INTEGER NOT NULL DEFAULT 1,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS circuit_breaker_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    event TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT,
    failure_count INTEGER,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cb_state ON circuit_breaker(state);
CREATE INDEX IF NOT EXISTS idx_cb_log_endpoint ON circuit_breaker_log(endpoint, ts);
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
                body: dict) -> None:
    """Fire-and-forget bus notification (JSONL atomic append)."""
    if bus_dir is None:
        return
    try:
        os.makedirs(bus_dir, exist_ok=True)
        msg = {
            "id": str(uuid.uuid4()),
            "type": "info",
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


# ===================================================================
# CircuitBreaker
# ===================================================================


class CircuitBreaker:
    """Per-endpoint circuit breaker with three states: closed, open, half-open.

    Parameters
    ----------
    db_path:
        Path to the SQLite database for circuit breaker state.
    failure_threshold:
        Number of consecutive failures before opening the circuit.
    recovery_timeout:
        Seconds to wait in OPEN state before transitioning to HALF_OPEN.
    half_open_max:
        Maximum concurrent test calls allowed in HALF_OPEN state.
    bus_dir:
        Optional path to the JSONL bus directory for notifications.
    """

    def __init__(
        self,
        db_path: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
        bus_dir: Optional[str] = None,
    ) -> None:
        self.db_path = db_path
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self.bus_dir = bus_dir
        self._lock = threading.Lock()
        self._conn = _open_db(db_path)
        self._closed = False

    def _check_closed(self) -> None:
        """Raise RuntimeError if this instance has been closed."""
        if self._closed:
            raise RuntimeError("CircuitBreaker instance is closed")

    def _ensure_endpoint(self, conn: sqlite3.Connection, endpoint: str) -> None:
        """Ensure an endpoint row exists, inserting defaults if missing."""
        conn.execute(
            """INSERT OR IGNORE INTO circuit_breaker
               (endpoint, state, failure_count, success_count,
                last_state_change, half_open_attempts,
                failure_threshold, recovery_timeout, half_open_max)
               VALUES (?, 'closed', 0, 0, ?, 0, ?, ?, ?)""",
            (endpoint, time.time(), self.failure_threshold,
             self.recovery_timeout, self.half_open_max),
        )

    def _log_event(self, conn: sqlite3.Connection, endpoint: str,
                   agent_id: str, event: str, old_state: str,
                   new_state: str, failure_count: int) -> None:
        """Insert a circuit breaker event log entry."""
        conn.execute(
            """INSERT INTO circuit_breaker_log
               (endpoint, agent_id, event, old_state, new_state, failure_count, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (endpoint, agent_id, event, old_state, new_state, failure_count,
             time.time()),
        )

    @_retry_on_busy
    def call_allowed(self, endpoint: str, agent_id: str) -> bool:
        """Check if a call to this endpoint is currently allowed.

        State transitions:
        - CLOSED: always allowed.
        - OPEN: check if recovery_timeout has elapsed.  If so, transition
          to HALF_OPEN and allow one test call.  Otherwise, reject.
        - HALF_OPEN: allow up to half_open_max concurrent test calls.

        Parameters
        ----------
        endpoint:
            The endpoint identifier (URL, service name, etc.).
        agent_id:
            The agent requesting permission.

        Returns
        -------
        bool
            True if the call should proceed, False if it should be rejected.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_endpoint(self._conn, endpoint)

                row = self._conn.execute(
                    "SELECT * FROM circuit_breaker WHERE endpoint = ?",
                    (endpoint,),
                ).fetchone()

                state = row["state"]
                recovery_timeout = row["recovery_timeout"]
                half_open_max = row["half_open_max"]

                if state == CIRCUIT_CLOSED:
                    self._conn.execute("COMMIT")
                    return True

                if state == CIRCUIT_OPEN:
                    elapsed = now - row["last_state_change"]
                    if elapsed >= recovery_timeout:
                        # Transition OPEN -> HALF_OPEN
                        self._conn.execute(
                            """UPDATE circuit_breaker
                               SET state = 'half_open',
                                   half_open_attempts = 1,
                                   last_state_change = ?
                               WHERE endpoint = ?""",
                            (now, endpoint),
                        )
                        self._log_event(
                            self._conn, endpoint, agent_id,
                            "recovery_timeout_elapsed",
                            CIRCUIT_OPEN, CIRCUIT_HALF_OPEN,
                            row["failure_count"],
                        )
                        self._conn.execute("COMMIT")

                        _bus_notify(self.bus_dir, "circuit-breaker", agent_id, {
                            "event": "circuit-half-open",
                            "endpoint": endpoint,
                            "after_seconds": elapsed,
                        })
                        return True
                    else:
                        # Still in cooldown -- reject
                        self._conn.execute("COMMIT")
                        return False

                if state == CIRCUIT_HALF_OPEN:
                    if row["half_open_attempts"] < half_open_max:
                        self._conn.execute(
                            """UPDATE circuit_breaker
                               SET half_open_attempts = half_open_attempts + 1
                               WHERE endpoint = ?""",
                            (endpoint,),
                        )
                        self._conn.execute("COMMIT")
                        return True
                    else:
                        # Already at max test calls -- reject additional
                        self._conn.execute("COMMIT")
                        return False

                # Unknown state -- allow cautiously
                self._conn.execute("COMMIT")
                return True

            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def record_success(self, endpoint: str, agent_id: str) -> None:
        """Record a successful call to an endpoint.

        State transitions:
        - HALF_OPEN -> CLOSED (circuit recovered)
        - CLOSED: resets failure count to 0
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_endpoint(self._conn, endpoint)

                row = self._conn.execute(
                    "SELECT state, failure_count FROM circuit_breaker WHERE endpoint = ?",
                    (endpoint,),
                ).fetchone()

                old_state = row["state"]

                if old_state == CIRCUIT_HALF_OPEN:
                    # Recovery confirmed -- close the circuit
                    self._conn.execute(
                        """UPDATE circuit_breaker
                           SET state = 'closed',
                               failure_count = 0,
                               success_count = success_count + 1,
                               half_open_attempts = 0,
                               last_success_time = ?,
                               last_state_change = ?
                           WHERE endpoint = ?""",
                        (now, now, endpoint),
                    )
                    self._log_event(
                        self._conn, endpoint, agent_id,
                        "recovery_success",
                        CIRCUIT_HALF_OPEN, CIRCUIT_CLOSED, 0,
                    )
                    self._conn.execute("COMMIT")

                    _bus_notify(self.bus_dir, "circuit-breaker", agent_id, {
                        "event": "circuit-closed",
                        "endpoint": endpoint,
                        "reason": "recovery_success",
                    })
                else:
                    # Normal success in CLOSED state -- reset failure count
                    self._conn.execute(
                        """UPDATE circuit_breaker
                           SET failure_count = 0,
                               success_count = success_count + 1,
                               last_success_time = ?
                           WHERE endpoint = ?""",
                        (now, endpoint),
                    )
                    self._conn.execute("COMMIT")

            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def record_failure(self, endpoint: str, agent_id: str) -> None:
        """Record a failed call to an endpoint.

        State transitions:
        - CLOSED: increment failure_count. If >= threshold, transition to OPEN.
        - HALF_OPEN -> OPEN (test call failed, reopen circuit).
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_endpoint(self._conn, endpoint)

                row = self._conn.execute(
                    "SELECT * FROM circuit_breaker WHERE endpoint = ?",
                    (endpoint,),
                ).fetchone()

                old_state = row["state"]
                failure_count = row["failure_count"] + 1
                threshold = row["failure_threshold"]

                if old_state == CIRCUIT_HALF_OPEN:
                    # Test call failed -- reopen
                    self._conn.execute(
                        """UPDATE circuit_breaker
                           SET state = 'open',
                               failure_count = ?,
                               half_open_attempts = 0,
                               last_failure_time = ?,
                               last_state_change = ?
                           WHERE endpoint = ?""",
                        (failure_count, now, now, endpoint),
                    )
                    self._log_event(
                        self._conn, endpoint, agent_id,
                        "half_open_failure",
                        CIRCUIT_HALF_OPEN, CIRCUIT_OPEN, failure_count,
                    )
                    self._conn.execute("COMMIT")

                    _bus_notify(self.bus_dir, "circuit-breaker", agent_id, {
                        "event": "circuit-reopened",
                        "endpoint": endpoint,
                        "failure_count": failure_count,
                    })

                elif old_state == CIRCUIT_CLOSED and failure_count >= threshold:
                    # Threshold exceeded -- open the circuit
                    self._conn.execute(
                        """UPDATE circuit_breaker
                           SET state = 'open',
                               failure_count = ?,
                               half_open_attempts = 0,
                               last_failure_time = ?,
                               last_state_change = ?
                           WHERE endpoint = ?""",
                        (failure_count, now, now, endpoint),
                    )
                    self._log_event(
                        self._conn, endpoint, agent_id,
                        "threshold_exceeded",
                        CIRCUIT_CLOSED, CIRCUIT_OPEN, failure_count,
                    )
                    self._conn.execute("COMMIT")

                    _bus_notify(self.bus_dir, "circuit-breaker", agent_id, {
                        "event": "circuit-opened",
                        "endpoint": endpoint,
                        "failure_count": failure_count,
                        "threshold": threshold,
                    })

                else:
                    # Still under threshold -- just increment
                    self._conn.execute(
                        """UPDATE circuit_breaker
                           SET failure_count = ?,
                               last_failure_time = ?
                           WHERE endpoint = ?""",
                        (failure_count, now, endpoint),
                    )
                    self._conn.execute("COMMIT")

            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def get_circuit_status(self, endpoint: str) -> dict:
        """Get current state, failure count, last failure time for an endpoint.

        Returns
        -------
        dict
            Keys: endpoint, state, failure_count, success_count,
            last_failure_time, last_success_time, last_state_change,
            half_open_attempts, failure_threshold, recovery_timeout.
            Returns empty dict if endpoint not tracked.
        """
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM circuit_breaker WHERE endpoint = ?",
                (endpoint,),
            ).fetchone()
        if row is None:
            return {}
        return dict(row)

    @_retry_on_busy
    def get_all_circuits(self) -> list:
        """Get status of all tracked endpoints.

        Returns
        -------
        list[dict]
            List of circuit breaker state dicts, sorted by endpoint.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM circuit_breaker ORDER BY endpoint",
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_open_circuits(self) -> list:
        """Get all circuits currently in OPEN or HALF_OPEN state.

        Useful for dashboard display and alerting.

        Returns
        -------
        list[dict]
            Circuits that are currently tripped.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM circuit_breaker
                   WHERE state IN ('open', 'half_open')
                   ORDER BY last_state_change DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def reset_circuit(self, endpoint: str, agent_id: str) -> None:
        """Manually reset a circuit to CLOSED state.

        Used for administrative override when an endpoint is known to be
        healthy again.

        Parameters
        ----------
        endpoint:
            The endpoint to reset.
        agent_id:
            The agent performing the reset (for audit trail).
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT state, failure_count FROM circuit_breaker WHERE endpoint = ?",
                    (endpoint,),
                ).fetchone()

                if row is None:
                    self._conn.execute("ROLLBACK")
                    raise ValueError(f"Endpoint {endpoint!r} not tracked")

                old_state = row["state"]
                self._conn.execute(
                    """UPDATE circuit_breaker
                       SET state = 'closed',
                           failure_count = 0,
                           half_open_attempts = 0,
                           last_state_change = ?
                       WHERE endpoint = ?""",
                    (now, endpoint),
                )
                self._log_event(
                    self._conn, endpoint, agent_id,
                    "manual_reset",
                    old_state, CIRCUIT_CLOSED, 0,
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        _bus_notify(self.bus_dir, "circuit-breaker", agent_id, {
            "event": "circuit-reset",
            "endpoint": endpoint,
            "old_state": old_state,
        })

    @_retry_on_busy
    def get_event_log(self, endpoint: Optional[str] = None,
                      limit: int = 100) -> list:
        """Get recent circuit breaker events.

        Parameters
        ----------
        endpoint:
            Filter to a specific endpoint. None for all endpoints.
        limit:
            Maximum number of events to return.

        Returns
        -------
        list[dict]
            Event log entries, newest first.
        """
        self._check_closed()
        with self._lock:
            if endpoint:
                rows = self._conn.execute(
                    """SELECT * FROM circuit_breaker_log
                       WHERE endpoint = ?
                       ORDER BY ts DESC LIMIT ?""",
                    (endpoint, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM circuit_breaker_log
                       ORDER BY ts DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

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
