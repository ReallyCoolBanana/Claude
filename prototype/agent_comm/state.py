"""
SQLite WAL shared state for multi-agent coordination.

Provides agent registration, heartbeats, rate limiting, phase signals,
and a persistent message store. All writes use appropriate transaction
isolation to prevent TOCTOU races.
"""

import json
import logging
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    team TEXT NOT NULL,
    role TEXT NOT NULL,
    pid INTEGER,
    status TEXT DEFAULT 'alive',
    last_heartbeat REAL NOT NULL,
    registered_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_endpoint TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    called_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limit_config (
    api_endpoint TEXT PRIMARY KEY,
    max_calls INTEGER NOT NULL,
    window_seconds INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    ts REAL NOT NULL,
    data TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id TEXT UNIQUE NOT NULL,
    channel TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    body TEXT NOT NULL,
    ts REAL NOT NULL,
    expires_at REAL,
    in_reply_to TEXT
);
"""

# Max retries for SQLITE_BUSY errors on operations that don't rely on
# the built-in busy_timeout (e.g. BEGIN IMMEDIATE).
_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1  # seconds, doubles each retry


def _retry_on_busy(func):
    """Decorator: retry a method on sqlite3.OperationalError (SQLITE_BUSY)."""
    import functools

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
                    delay *= 2
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


class SharedState:
    """SQLite WAL-mode shared state store for multi-agent coordination."""

    def __init__(self, db_path: str, busy_timeout_ms: int = 30000) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        wal_result = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if wal_result is None or wal_result[0].lower() != "wal":
            logger.warning(
                "Failed to enable WAL mode for %s (got %r); "
                "concurrent performance may be degraded",
                db_path, wal_result[0] if wal_result else None,
            )
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Agent registry
    # ------------------------------------------------------------------

    @_retry_on_busy
    def register_agent(self, agent_id: str, team: str, role: str, pid: int) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agents (agent_id, team, role, pid, status, last_heartbeat, registered_at)
                VALUES (?, ?, ?, ?, 'alive', ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    team=excluded.team, role=excluded.role, pid=excluded.pid,
                    status='alive', last_heartbeat=excluded.last_heartbeat
                """,
                (agent_id, team, role, pid, now, now),
            )
            self._conn.commit()

    @_retry_on_busy
    def heartbeat(self, agent_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET last_heartbeat = ?, status = 'alive' WHERE agent_id = ?",
                (time.time(), agent_id),
            )
            self._conn.commit()

    @_retry_on_busy
    def get_all_agents(self) -> list[dict]:
        """Return all registered agents as a list of dicts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT agent_id, team, role, status, last_heartbeat FROM agents"
            ).fetchall()
        return [dict(r) for r in rows]

    @_retry_on_busy
    def get_coordinator_heartbeat(self) -> float | None:
        """Return the last heartbeat timestamp of the alive coordinator, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_heartbeat FROM agents WHERE role = 'coordinator' AND status = 'alive'"
            ).fetchone()
        if row is None:
            return None
        return row["last_heartbeat"]

    @_retry_on_busy
    def get_dead_agents(self, timeout: float = 120.0) -> list[dict]:
        cutoff = time.time() - timeout
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agents WHERE last_heartbeat < ? AND status = 'alive'",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Rate limiting  (RC-4 fix: BEGIN IMMEDIATE to prevent TOCTOU)
    #
    # DEPRECATED: This global rate limiter counts ALL agents' calls against
    # a single shared limit per endpoint, causing a 64.6% denial rate when
    # multiple agents hit the same endpoint concurrently. Use the per-agent
    # rate limiter in rate_limiter.py (RateLimiter.check_and_reserve) instead.
    # ------------------------------------------------------------------

    @_retry_on_busy
    def reserve_api_call(self, api_endpoint: str, agent_id: str) -> bool:
        """Atomically check + reserve an API call slot. Returns True if allowed.

        .. deprecated::
            This method uses a global (not per-agent) call count, which causes
            excessive denials under concurrent load. Use
            ``rate_limiter.RateLimiter.check_and_reserve()`` instead, which
            tracks calls per-agent per-endpoint.
        """
        # We use raw connection.execute to control transaction boundaries.
        conn = self._conn
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise  # let _retry_on_busy handle it

            try:
                # Fetch rate limit config
                row = conn.execute(
                    "SELECT max_calls, window_seconds FROM rate_limit_config WHERE api_endpoint = ?",
                    (api_endpoint,),
                ).fetchone()

                if row is None:
                    # No config means no limit — allow and record
                    conn.execute(
                        "INSERT INTO rate_limits (api_endpoint, agent_id, called_at) VALUES (?, ?, ?)",
                        (api_endpoint, agent_id, time.time()),
                    )
                    conn.execute("COMMIT")
                    return True

                max_calls = row["max_calls"]
                window_seconds = row["window_seconds"]
                window_start = time.time() - window_seconds

                count = conn.execute(
                    "SELECT COUNT(*) FROM rate_limits WHERE api_endpoint = ? AND called_at > ?",
                    (api_endpoint, window_start),
                ).fetchone()[0]

                if count >= max_calls:
                    conn.execute("ROLLBACK")
                    return False

                conn.execute(
                    "INSERT INTO rate_limits (api_endpoint, agent_id, called_at) VALUES (?, ?, ?)",
                    (api_endpoint, agent_id, time.time()),
                )
                conn.execute("COMMIT")
                return True

            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def configure_rate_limit(self, api_endpoint: str, max_calls: int, window_seconds: int) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO rate_limit_config (api_endpoint, max_calls, window_seconds)
                VALUES (?, ?, ?)
                ON CONFLICT(api_endpoint) DO UPDATE SET
                    max_calls=excluded.max_calls, window_seconds=excluded.window_seconds
                """,
                (api_endpoint, max_calls, window_seconds),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Phase signals
    # ------------------------------------------------------------------

    @_retry_on_busy
    def signal_phase(self, phase: str, signal_type: str, agent_id: str, data: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO phase_signals (phase, signal_type, agent_id, ts, data) VALUES (?, ?, ?, ?, ?)",
                (phase, signal_type, agent_id, time.time(), data),
            )
            self._conn.commit()

    @_retry_on_busy
    def get_phase_signals(self, phase: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM phase_signals WHERE phase = ? ORDER BY ts",
                (phase,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @_retry_on_busy
    def cleanup_expired(self) -> int:
        """Delete old rate_limit records and expired messages. Returns total rows removed."""
        now = time.time()
        with self._lock:
            cur1 = self._conn.execute(
                "DELETE FROM rate_limits WHERE called_at < ?",
                (now - 3600,),  # clean up records older than 1 hour
            )
            cur2 = self._conn.execute(
                "DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            total = cur1.rowcount + cur2.rowcount
            self._conn.commit()
        return total
