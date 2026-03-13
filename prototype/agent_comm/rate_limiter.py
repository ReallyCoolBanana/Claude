"""
Per-agent sliding window rate limiter with SQLite backing.

Fixes the 64.6% denial rate caused by the original global rate limiter
in state.py which counted ALL agent calls against a shared limit.
This implementation tracks calls per-agent per-endpoint, so each agent
gets its own quota within the configured window.

All writes use BEGIN IMMEDIATE transactions with exponential backoff
retry on SQLITE_BUSY, per SOP-034 (max 4 concurrent SQLite writers).
"""

import functools
import logging
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rl_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    called_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rl_calls_agent_endpoint
    ON rl_calls (agent_id, endpoint, called_at);

CREATE TABLE IF NOT EXISTS rl_config (
    endpoint TEXT NOT NULL,
    agent_id TEXT,  -- NULL means default for all agents on this endpoint
    max_calls INTEGER NOT NULL,
    window_seconds INTEGER NOT NULL,
    PRIMARY KEY (endpoint, agent_id)
);
"""

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1


def _retry_on_busy(func):
    """Decorator: retry on sqlite3.OperationalError (SQLITE_BUSY)."""
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


class RateLimiter:
    """Per-agent sliding window rate limiter backed by SQLite.

    Key difference from the original state.py rate limiter:
    - Tracks calls PER AGENT PER ENDPOINT (not globally per endpoint)
    - Supports per-agent overrides on top of default endpoint limits
    - Periodically prunes expired call records to keep the table small
    """

    def __init__(self, db_path: str, busy_timeout_ms: int = 30000) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path, timeout=busy_timeout_ms / 1000, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._last_prune = 0.0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @_retry_on_busy
    def configure(
        self,
        endpoint: str,
        max_calls: int,
        window_seconds: int,
        agent_id: Optional[str] = None,
    ) -> None:
        """Set rate limit config.

        If agent_id is None, sets the default limit for all agents on this
        endpoint. If agent_id is provided, sets an override for that specific
        agent.
        """
        key = agent_id if agent_id is not None else ""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO rl_config (endpoint, agent_id, max_calls, window_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(endpoint, agent_id) DO UPDATE SET
                    max_calls=excluded.max_calls,
                    window_seconds=excluded.window_seconds
                """,
                (endpoint, key, max_calls, window_seconds),
            )
            self._conn.commit()

    @_retry_on_busy
    def check_and_reserve(self, endpoint: str, agent_id: str) -> bool:
        """Atomically check + reserve a call slot for this agent+endpoint.

        Returns True if allowed (slot reserved), False if rate limit exceeded.
        Uses BEGIN IMMEDIATE to prevent TOCTOU between check and insert.
        """
        conn = self._conn
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise  # let _retry_on_busy handle

            try:
                # Look for agent-specific override first, then default
                row = conn.execute(
                    "SELECT max_calls, window_seconds FROM rl_config "
                    "WHERE endpoint = ? AND agent_id = ?",
                    (endpoint, agent_id),
                ).fetchone()

                if row is None:
                    # Fall back to default config (agent_id = "")
                    row = conn.execute(
                        "SELECT max_calls, window_seconds FROM rl_config "
                        "WHERE endpoint = ? AND agent_id = ?",
                        (endpoint, ""),
                    ).fetchone()

                if row is None:
                    # No config at all -- allow and record
                    conn.execute(
                        "INSERT INTO rl_calls (endpoint, agent_id, called_at) "
                        "VALUES (?, ?, ?)",
                        (endpoint, agent_id, time.time()),
                    )
                    conn.execute("COMMIT")
                    return True

                max_calls = row["max_calls"]
                window_seconds = row["window_seconds"]
                window_start = time.time() - window_seconds

                # Count only THIS agent's calls on THIS endpoint
                count = conn.execute(
                    "SELECT COUNT(*) FROM rl_calls "
                    "WHERE endpoint = ? AND agent_id = ? AND called_at > ?",
                    (endpoint, agent_id, window_start),
                ).fetchone()[0]

                if count >= max_calls:
                    conn.execute("ROLLBACK")
                    return False

                conn.execute(
                    "INSERT INTO rl_calls (endpoint, agent_id, called_at) "
                    "VALUES (?, ?, ?)",
                    (endpoint, agent_id, time.time()),
                )
                conn.execute("COMMIT")

                # Opportunistic prune (at most once per 60 seconds)
                now = time.time()
                if now - self._last_prune > 60:
                    self._last_prune = now
                    self._prune_expired_unlocked()

                return True

            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def get_usage(self, endpoint: str, agent_id: str) -> dict:
        """Return current usage stats for an agent+endpoint pair."""
        with self._lock:
            row = self._conn.execute(
                "SELECT max_calls, window_seconds FROM rl_config "
                "WHERE endpoint = ? AND agent_id = ?",
                (endpoint, agent_id),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "SELECT max_calls, window_seconds FROM rl_config "
                    "WHERE endpoint = ? AND agent_id = ?",
                    (endpoint, ""),
                ).fetchone()

            if row is None:
                return {"configured": False}

            max_calls = row["max_calls"]
            window_seconds = row["window_seconds"]
            window_start = time.time() - window_seconds

            count = self._conn.execute(
                "SELECT COUNT(*) FROM rl_calls "
                "WHERE endpoint = ? AND agent_id = ? AND called_at > ?",
                (endpoint, agent_id, window_start),
            ).fetchone()[0]

            return {
                "configured": True,
                "max_calls": max_calls,
                "window_seconds": window_seconds,
                "used": count,
                "remaining": max(0, max_calls - count),
            }

    @_retry_on_busy
    def prune_expired(self) -> int:
        """Remove call records older than the largest configured window + 60s buffer."""
        with self._lock:
            return self._prune_expired_unlocked()

    def _prune_expired_unlocked(self) -> int:
        """Internal prune -- caller must hold self._lock."""
        # Find the largest window so we don't prune records still in-window
        row = self._conn.execute(
            "SELECT MAX(window_seconds) as max_window FROM rl_config"
        ).fetchone()
        max_window = row["max_window"] if row and row["max_window"] else 3600
        cutoff = time.time() - max_window - 60  # 60s safety buffer
        cur = self._conn.execute(
            "DELETE FROM rl_calls WHERE called_at < ?", (cutoff,)
        )
        self._conn.commit()
        removed = cur.rowcount
        if removed > 0:
            logger.debug("Pruned %d expired rate limit records", removed)
        return removed
