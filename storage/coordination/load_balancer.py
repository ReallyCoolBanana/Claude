"""Load-Aware Work Distribution with backpressure signaling.

Tracks per-team load metrics (active tasks, queue depth, completion rate,
last activity) and distributes work to the least-loaded capable team.
Integrates with work_stealing.py and help_protocol.py for load-aware
assignment decisions.

SQLite-backed with WAL mode, auto-expiry of stale load data (5 min default).
Thread-safe with _retry_on_busy for SQLITE_BUSY resilience.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1

# Default staleness threshold: load data older than this is considered expired
DEFAULT_STALE_SECONDS = 300  # 5 minutes

# Default overload threshold
DEFAULT_OVERLOAD_THRESHOLD = 5


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
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
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


_LOAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_load (
    team TEXT PRIMARY KEY,
    active_tasks INTEGER NOT NULL DEFAULT 0,
    queue_depth INTEGER NOT NULL DEFAULT 0,
    completions_total INTEGER NOT NULL DEFAULT 0,
    completion_rate REAL NOT NULL DEFAULT 0.0,
    backpressure INTEGER NOT NULL DEFAULT 0,
    last_activity REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_team_load_active
    ON team_load (active_tasks);

CREATE INDEX IF NOT EXISTS idx_team_load_updated
    ON team_load (updated_at);
"""


class LoadBalancer:
    """Load-aware work distribution with backpressure signaling.

    Tracks per-team: active_tasks, queue_depth, completion_rate, last_activity.
    Distributes work to least-loaded capable team.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    stale_seconds:
        Load data older than this is considered expired and cleaned up.
        Defaults to 300 (5 minutes).
    """

    def __init__(self, db_path: str, stale_seconds: int = DEFAULT_STALE_SECONDS) -> None:
        self.db_path = db_path
        self.stale_seconds = stale_seconds
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(
            db_path, timeout=30, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_LOAD_SCHEMA)
        self._conn.commit()
        self._closed = False

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("LoadBalancer instance is closed")

    def _cleanup_stale(self) -> None:
        """Remove load entries older than stale_seconds. Must hold self._lock."""
        cutoff = time.time() - self.stale_seconds
        self._conn.execute(
            "DELETE FROM team_load WHERE updated_at < ?",
            (cutoff,),
        )

    @_retry_on_busy
    def update_load(self, team: str, active_tasks: int, queue_depth: int) -> None:
        """Report current load metrics for a team.

        Parameters
        ----------
        team:
            Team identifier.
        active_tasks:
            Number of tasks currently being worked on.
        queue_depth:
            Number of tasks waiting in the team's queue.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._cleanup_stale()
            self._conn.execute(
                """
                INSERT INTO team_load
                    (team, active_tasks, queue_depth, completions_total,
                     completion_rate, backpressure, last_activity, updated_at)
                VALUES (?, ?, ?, 0, 0.0, 0, ?, ?)
                ON CONFLICT(team) DO UPDATE SET
                    active_tasks = excluded.active_tasks,
                    queue_depth = excluded.queue_depth,
                    last_activity = excluded.last_activity,
                    updated_at = excluded.updated_at
                """,
                (team, active_tasks, queue_depth, now, now),
            )
            self._conn.commit()

    @_retry_on_busy
    def record_completion(self, team: str) -> None:
        """Record a task completion for a team, updating completion rate.

        Completion rate is calculated as completions per minute based on
        time since the team's first recorded activity.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT completions_total, last_activity, active_tasks FROM team_load WHERE team = ?",
                (team,),
            ).fetchone()
            if row is None:
                # Team not tracked yet; create entry
                self._conn.execute(
                    """
                    INSERT INTO team_load
                        (team, active_tasks, queue_depth, completions_total,
                         completion_rate, backpressure, last_activity, updated_at)
                    VALUES (?, 0, 0, 1, 0.0, 0, ?, ?)
                    """,
                    (team, now, now),
                )
            else:
                new_total = row["completions_total"] + 1
                active = max(0, row["active_tasks"] - 1)
                # Completion rate: completions per minute
                elapsed = max(now - row["last_activity"], 1.0)
                rate = new_total / (elapsed / 60.0)
                self._conn.execute(
                    """
                    UPDATE team_load SET
                        completions_total = ?,
                        completion_rate = ?,
                        active_tasks = ?,
                        last_activity = ?,
                        updated_at = ?
                    WHERE team = ?
                    """,
                    (new_total, rate, active, now, now, team),
                )
            self._conn.commit()

    @_retry_on_busy
    def get_load(self, team: str) -> Optional[dict]:
        """Get current load for a team.

        Returns None if the team has no load data or data is stale.

        Returns
        -------
        Dict with keys: team, active_tasks, queue_depth, completions_total,
        completion_rate, backpressure, last_activity, updated_at.
        """
        self._check_closed()
        cutoff = time.time() - self.stale_seconds
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM team_load WHERE team = ? AND updated_at >= ?",
                (team, cutoff),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    @_retry_on_busy
    def get_least_loaded(self, capable_teams: list[str]) -> Optional[str]:
        """Among capable teams, return the least loaded one.

        Scoring: active_tasks + (queue_depth * 0.5). Lower is better.
        Teams with backpressure signaled are excluded.
        Teams with stale data are excluded.

        Parameters
        ----------
        capable_teams:
            List of team names to consider.

        Returns
        -------
        Team name of the least loaded team, or None if all are overloaded/stale.
        """
        self._check_closed()
        if not capable_teams:
            return None

        cutoff = time.time() - self.stale_seconds
        placeholders = ",".join("?" for _ in capable_teams)

        with self._lock:
            self._cleanup_stale()
            rows = self._conn.execute(
                f"""
                SELECT team, active_tasks, queue_depth
                FROM team_load
                WHERE team IN ({placeholders})
                    AND backpressure = 0
                    AND updated_at >= ?
                ORDER BY (active_tasks + queue_depth * 0.5) ASC
                LIMIT 1
                """,
                [*capable_teams, cutoff],
            ).fetchall()

        if rows:
            return rows[0]["team"]

        # If no teams have load data, return the first capable team
        # (they haven't reported load yet, so assume they're idle)
        return capable_teams[0] if capable_teams else None

    @_retry_on_busy
    def is_overloaded(self, team: str, threshold: int = DEFAULT_OVERLOAD_THRESHOLD) -> bool:
        """Check if team has more active tasks than threshold.

        Also returns True if backpressure has been signaled for the team.
        """
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT active_tasks, backpressure FROM team_load WHERE team = ?",
                (team,),
            ).fetchone()
        if row is None:
            return False
        return row["active_tasks"] > threshold or row["backpressure"] == 1

    @_retry_on_busy
    def signal_backpressure(self, team: str) -> None:
        """Signal that a team is overloaded -- stop assigning new work.

        The team must call clear_backpressure() or update_load() to resume.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO team_load
                    (team, active_tasks, queue_depth, completions_total,
                     completion_rate, backpressure, last_activity, updated_at)
                VALUES (?, 0, 0, 0, 0.0, 1, ?, ?)
                ON CONFLICT(team) DO UPDATE SET
                    backpressure = 1,
                    updated_at = excluded.updated_at
                """,
                (team, now, now),
            )
            self._conn.commit()
        logger.warning("Backpressure signaled for team %s", team)

    @_retry_on_busy
    def clear_backpressure(self, team: str) -> None:
        """Clear backpressure signal for a team, allowing work assignment again."""
        self._check_closed()
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE team_load SET backpressure = 0, updated_at = ? WHERE team = ?",
                (now, team),
            )
            self._conn.commit()
        logger.info("Backpressure cleared for team %s", team)

    @_retry_on_busy
    def get_distribution_report(self) -> dict:
        """Get load distribution across all teams.

        Returns
        -------
        Dict with:
            teams: list of team load dicts (non-stale only)
            total_active: sum of active tasks across all teams
            total_queued: sum of queue depths across all teams
            overloaded: list of team names with backpressure signaled
            avg_load: average active tasks per team
        """
        self._check_closed()
        cutoff = time.time() - self.stale_seconds
        with self._lock:
            self._cleanup_stale()
            rows = self._conn.execute(
                "SELECT * FROM team_load WHERE updated_at >= ? ORDER BY active_tasks DESC",
                (cutoff,),
            ).fetchall()
            self._conn.commit()

        teams = [dict(r) for r in rows]
        total_active = sum(t["active_tasks"] for t in teams)
        total_queued = sum(t["queue_depth"] for t in teams)
        overloaded = [t["team"] for t in teams if t["backpressure"] == 1]
        avg_load = total_active / len(teams) if teams else 0.0

        return {
            "teams": teams,
            "total_active": total_active,
            "total_queued": total_queued,
            "overloaded": overloaded,
            "avg_load": round(avg_load, 2),
            "team_count": len(teams),
        }
