"""Work Stealing, Pipeline Chaining, and Shared Scratchpad for Proto A.

Provides three coordination primitives on top of SQLite WAL:

- **WorkStealing**: A shared work queue where any team can enqueue tasks
  and idle teams can atomically steal unclaimed work items.
- **PipelineManager**: Multi-stage pipelines with dependency tracking.
  Stages auto-trigger via bus notifications when upstream stages complete.
- **Scratchpad**: Namespaced key-value store with TTL expiration for
  sharing intermediate results between agents.

All writes use BEGIN IMMEDIATE for atomicity and the _retry_on_busy
decorator from Proto A's state.py pattern for SQLITE_BUSY resilience.
Only uses the Python standard library.
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
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry helper (mirrors prototype/agent_comm/state.py pattern)
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1


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
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_team TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'queued',
    claimed_by TEXT,
    claimed_at REAL,
    completed_at REAL,
    result TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pipelines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    stages TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER REFERENCES pipelines(id),
    stage_name TEXT NOT NULL,
    assigned_team TEXT,
    depends_on TEXT,
    status TEXT DEFAULT 'waiting',
    input_data TEXT,
    output_data TEXT,
    error_message TEXT,
    started_at REAL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS scratchpad (
    key TEXT NOT NULL,
    namespace TEXT NOT NULL,
    value TEXT NOT NULL,
    written_by TEXT NOT NULL,
    ttl_seconds INTEGER DEFAULT 3600,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (key, namespace)
);

CREATE INDEX IF NOT EXISTS idx_work_queue_status_priority
    ON work_queue (status, priority, created_at);

CREATE INDEX IF NOT EXISTS idx_pipeline_stages_pipeline_status
    ON pipeline_stages (pipeline_id, status);

CREATE INDEX IF NOT EXISTS idx_scratchpad_namespace_expires
    ON scratchpad (namespace, expires_at);
"""


def _open_db(db_path: str, busy_timeout_ms: int = 30000) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection and ensure schema exists."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Bus notification helper
# ---------------------------------------------------------------------------

def _bus_notify(bus_dir: str, channel: str, agent_id: str, team: str, body: dict) -> None:
    """Fire-and-forget bus notification (JSONL atomic append).

    Best-effort: failures are logged but never propagated to callers.
    The DB write has already succeeded by the time this is called.
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
# WorkStealing
# ===================================================================


class WorkStealing:
    """Shared work queue with atomic steal semantics.

    Any team can enqueue work items.  Idle teams call ``steal_work()``
    to atomically claim the highest-priority unclaimed item.  The claim
    uses ``BEGIN IMMEDIATE`` to prevent double-steal races.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    bus_dir:
        Path to the JSONL bus directory for notifications (or None to skip).
    team:
        Team label for this instance (used as owner_team on enqueue).
    agent_id:
        Agent identifier for bus messages.
    auto_reclaim:
        If True, start a background daemon thread that periodically
        reclaims abandoned (zombie) work items.
    reclaim_interval:
        Seconds between reclaim sweeps (default 30).
    reclaim_timeout:
        Seconds a work item can stay in 'claimed' before being reclaimed
        (default 1800).
    partitioned_store:
        Optional PartitionedStore instance.  When provided, the queue
        partition is used for work_queue, pipelines, and pipeline_stages
        tables instead of opening a new connection to db_path.
    """

    def __init__(self, db_path: str, bus_dir: Optional[str], team: str, agent_id: str,
                 auto_reclaim: bool = False, reclaim_interval: int = 30,
                 reclaim_timeout: int = 1800,
                 partitioned_store: Optional["PartitionedStore"] = None) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self.team = team
        self.agent_id = agent_id
        self._partitioned_store = partitioned_store
        self._lock = threading.Lock()
        if partitioned_store is not None:
            self._conn = partitioned_store.queue.conn
            self._owns_conn = False
        else:
            self._conn = _open_db(db_path)
            self._owns_conn = True
        self._closed = False
        self._reclaim_thread: Optional[threading.Thread] = None
        self._reclaim_stop = threading.Event()
        # P0 FIX: Optionally start automatic zombie reclaim on construction
        if auto_reclaim:
            self.start_reclaim_thread(interval=reclaim_interval,
                                     timeout_seconds=reclaim_timeout)

    def start_reclaim_thread(self, interval: int = 30,
                             timeout_seconds: int = 1800) -> None:
        """Start a background daemon thread that periodically reclaims zombie work.

        The thread calls ``reclaim_abandoned_work(timeout_seconds)`` every
        *interval* seconds.  It is a daemon thread so it will not prevent
        interpreter shutdown.

        Parameters
        ----------
        interval:
            Seconds between reclaim sweeps (default 30).
        timeout_seconds:
            Passed through to ``reclaim_abandoned_work()`` as the staleness
            threshold (default 1800 = 30 minutes).
        """
        if self._reclaim_thread is not None and self._reclaim_thread.is_alive():
            logger.warning("Reclaim thread already running; ignoring duplicate start")
            return

        self._reclaim_stop.clear()

        def _reclaim_loop() -> None:
            logger.info(
                "Reclaim thread started (interval=%ds, timeout=%ds)",
                interval, timeout_seconds,
            )
            while not self._reclaim_stop.wait(timeout=interval):
                if self._closed:
                    break
                try:
                    reclaimed = self.reclaim_abandoned_work(timeout_seconds)
                    if reclaimed:
                        logger.info(
                            "Auto-reclaimed %d zombie work items: %s",
                            len(reclaimed), reclaimed,
                        )
                except Exception:
                    logger.warning(
                        "Reclaim sweep failed", exc_info=True,
                    )
            logger.info("Reclaim thread stopped")

        t = threading.Thread(target=_reclaim_loop, name="work-reclaim-daemon",
                             daemon=True)
        t.start()
        self._reclaim_thread = t

    def stop_reclaim_thread(self) -> None:
        """Signal the background reclaim thread to stop and wait for it."""
        self._reclaim_stop.set()
        if self._reclaim_thread is not None:
            self._reclaim_thread.join(timeout=5)
            self._reclaim_thread = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        # Stop the reclaim thread outside the lock to avoid deadlock
        self.stop_reclaim_thread()
        if self._owns_conn:
            self._conn.close()

    @_retry_on_busy
    def enqueue_work(self, title: str, description: str = "", priority: int = 5) -> int:
        """Add a work item to the shared queue. Returns the work item id."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO work_queue (owner_team, title, description, priority, status, created_at)
                   VALUES (?, ?, ?, ?, 'queued', ?)""",
                (self.team, title, description, priority, now),
            )
            self._conn.commit()
            work_id = cur.lastrowid

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "work-enqueued",
            "work_id": work_id,
            "title": title,
            "priority": priority,
        })
        return work_id

    @_retry_on_busy
    def steal_batch(self, batch_size: int = 10) -> list[dict]:
        """Atomically claim up to ``batch_size`` highest-priority unclaimed items.

        Uses a single ``BEGIN IMMEDIATE`` transaction to claim multiple items,
        reducing SQLite contention by ~5x compared to single-item stealing.
        Returns a list of claimed work item dicts (may be shorter than
        ``batch_size`` if fewer items are available, or empty if none).

        See KB-0027: batch stealing gives 4.7x throughput improvement over
        single-item stealing with no architecture changes required.
        """
        conn = self._conn
        now = time.time()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise  # let _retry_on_busy handle SQLITE_BUSY

            try:
                rows = conn.execute(
                    """SELECT id, owner_team, title, description, priority, created_at
                       FROM work_queue
                       WHERE status = 'queued'
                       ORDER BY priority ASC, created_at ASC
                       LIMIT ?""",
                    (batch_size,),
                ).fetchall()

                if not rows:
                    conn.execute("ROLLBACK")
                    return []

                results = []
                for row in rows:
                    work_id = row["id"]
                    conn.execute(
                        "UPDATE work_queue SET status = 'claimed', claimed_by = ?, claimed_at = ? WHERE id = ?",
                        (self.team, now, work_id),
                    )
                    item = dict(row)
                    item["claimed_by"] = self.team
                    item["claimed_at"] = now
                    item["status"] = "claimed"
                    results.append(item)

                conn.execute("COMMIT")

            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "work-batch-stolen",
            "count": len(results),
            "work_ids": [r["id"] for r in results],
            "stolen_by": self.team,
        })
        return results

    @_retry_on_busy
    def steal_work(self) -> Optional[dict]:
        """Atomically claim the highest-priority unclaimed work item.

        Uses BEGIN IMMEDIATE to prevent double-steal.  Returns a dict
        with the work item fields, or None if nothing is available.
        """
        conn = self._conn
        now = time.time()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise  # let _retry_on_busy handle SQLITE_BUSY

            try:
                row = conn.execute(
                    """SELECT id, owner_team, title, description, priority, created_at
                       FROM work_queue
                       WHERE status = 'queued'
                       ORDER BY priority ASC, created_at ASC
                       LIMIT 1""",
                ).fetchone()

                if row is None:
                    conn.execute("ROLLBACK")
                    return None

                work_id = row["id"]
                conn.execute(
                    "UPDATE work_queue SET status = 'claimed', claimed_by = ?, claimed_at = ? WHERE id = ?",
                    (self.team, now, work_id),
                )
                conn.execute("COMMIT")
                result = dict(row)
                result["claimed_by"] = self.team
                result["claimed_at"] = now
                result["status"] = "claimed"

            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "work-stolen",
            "work_id": result["id"],
            "title": result["title"],
            "stolen_by": self.team,
        })
        return result

    @_retry_on_busy
    def complete_work(self, work_id: int, result: dict) -> None:
        """Mark a work item as completed with a JSON result.

        Only the team that claimed the item (or the owner team) may complete it.
        Raises ValueError if the calling team is not authorized.
        """
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE work_queue SET status = 'completed', result = ?, completed_at = ? "
                "WHERE id = ? AND (claimed_by = ? OR owner_team = ?)",
                (json.dumps(result), now, work_id, self.team, self.team),
            )
            if cur.rowcount == 0:
                # Determine reason: not found vs not authorized
                row = self._conn.execute(
                    "SELECT claimed_by, owner_team FROM work_queue WHERE id = ?",
                    (work_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Work item {work_id} not found")
                raise ValueError(
                    f"Team '{self.team}' is not authorized to complete work item {work_id} "
                    f"(claimed by '{row['claimed_by']}', owned by '{row['owner_team']}')"
                )
            self._conn.commit()

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "work-completed",
            "work_id": work_id,
        })

    @_retry_on_busy
    def fail_work(self, work_id: int, reason: str) -> None:
        """Mark a work item as failed."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE work_queue SET status = 'failed', result = ?, completed_at = ? WHERE id = ?",
                (json.dumps({"error": reason}), now, work_id),
            )
            self._conn.commit()

    @_retry_on_busy
    def get_queue_depth(self, team: Optional[str] = None) -> int:
        """Return number of queued (unclaimed) items, optionally filtered by owner team."""
        with self._lock:
            if team:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM work_queue WHERE status = 'queued' AND owner_team = ?",
                    (team,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM work_queue WHERE status = 'queued'",
                ).fetchone()
        return row[0]

    @_retry_on_busy
    def reclaim_abandoned_work(self, timeout_seconds: int = 1800) -> list[int]:
        """BUG-WS-003: Reclaim work items stuck in 'claimed' status past timeout.

        Finds work items in 'claimed' status where claimed_at is older than
        *timeout_seconds* ago, and resets them to 'queued' status so they can
        be stolen again.

        Returns a list of reclaimed work item IDs.
        """
        cutoff = time.time() - timeout_seconds
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """SELECT id FROM work_queue
                       WHERE status = 'claimed' AND claimed_at < ?""",
                    (cutoff,),
                ).fetchall()
                reclaimed_ids = [r["id"] for r in rows]
                if reclaimed_ids:
                    placeholders = ",".join("?" for _ in reclaimed_ids)
                    self._conn.execute(
                        f"""UPDATE work_queue
                            SET status = 'queued', claimed_by = NULL, claimed_at = NULL
                            WHERE id IN ({placeholders})""",
                        reclaimed_ids,
                    )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        for wid in reclaimed_ids:
            _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
                "event": "work-reclaimed",
                "work_id": wid,
            })
        return reclaimed_ids

    @_retry_on_busy
    def get_stealable_work(self) -> list[dict]:
        """Return all unclaimed work items sorted by priority (ascending)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, owner_team, title, description, priority, status, created_at
                   FROM work_queue
                   WHERE status = 'queued'
                   ORDER BY priority ASC, created_at ASC""",
            ).fetchall()
        return [dict(r) for r in rows]


# ===================================================================
# TeamLeadDistributor
# ===================================================================


class TeamLeadDistributor:
    """Hierarchical lead-distribute pattern for high-throughput work distribution.

    Decouples SQLite contention from workers: a single lead thread batch-steals
    from the SQLite work queue into an in-memory ``queue.Queue``, while worker
    threads pull from the in-memory queue with zero contention.

    This pattern gives **10.4x throughput improvement** over single-item stealing
    with near-perfect work distribution (CV=0.009). See KB-0027 and KB-0028.

    Architecture::

        SQLite DB ──[batch steal]──> Lead Thread ──> queue.Queue ──> Workers
                                         │
                                    (only 1 thread
                                     touches SQLite)

    Parameters
    ----------
    work_stealing:
        The underlying ``WorkStealing`` instance for SQLite access.
    batch_size:
        Number of items the lead steals per SQLite transaction (default 10).
    buffer_size:
        Maximum items in the in-memory queue (default 100). The lead pauses
        when the buffer is full to avoid over-fetching.
    poll_interval:
        Seconds between lead polling when the queue is full or DB is empty
        (default 0.05).
    """

    def __init__(
        self,
        work_stealing: WorkStealing,
        batch_size: int = 10,
        buffer_size: int = 100,
        poll_interval: float = 0.05,
    ) -> None:
        import queue as _queue_mod
        self._ws = work_stealing
        self._batch_size = batch_size
        self._buffer: _queue_mod.Queue = _queue_mod.Queue(maxsize=buffer_size)
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._lead_thread: Optional[threading.Thread] = None
        self._started = False
        self._items_distributed = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the lead thread that batch-steals from SQLite into the buffer."""
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        self._lead_thread = threading.Thread(
            target=self._lead_loop, name="team-lead", daemon=True
        )
        self._lead_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the lead thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._lead_thread is not None:
            self._lead_thread.join(timeout=timeout)
        self._started = False

    def _lead_loop(self) -> None:
        """Lead thread: batch-steal from SQLite and put items in the buffer."""
        while not self._stop_event.is_set():
            # Don't over-fill the buffer
            if self._buffer.full():
                self._stop_event.wait(self._poll_interval)
                continue

            try:
                items = self._ws.steal_batch(self._batch_size)
            except Exception:
                logger.warning("Lead thread: steal_batch failed", exc_info=True)
                self._stop_event.wait(self._poll_interval * 2)
                continue

            if not items:
                # No work available, back off
                self._stop_event.wait(self._poll_interval)
                continue

            for item in items:
                if self._stop_event.is_set():
                    break
                try:
                    self._buffer.put(item, timeout=1.0)
                    with self._lock:
                        self._items_distributed += 1
                except Exception:
                    break

    def get_work(self, timeout: float = 1.0) -> Optional[dict]:
        """Worker method: get the next work item from the in-memory buffer.

        This is the method workers call instead of ``steal_work()``.
        It never touches SQLite, so there is zero contention between workers.

        Parameters
        ----------
        timeout:
            Max seconds to wait for an item (default 1.0).

        Returns
        -------
        dict or None
            The work item, or None if no work is available within the timeout.
        """
        try:
            return self._buffer.get(timeout=timeout)
        except Exception:
            return None

    @property
    def items_distributed(self) -> int:
        """Total items distributed to workers since start."""
        with self._lock:
            return self._items_distributed

    @property
    def buffer_size(self) -> int:
        """Current number of items waiting in the buffer."""
        return self._buffer.qsize()


# ===================================================================
# PipelineManager
# ===================================================================


class PipelineManager:
    """Multi-stage pipeline with dependency tracking.

    Stages are defined with optional ``depends_on`` lists.  When a stage
    completes, downstream stages whose dependencies are all met become
    ready.  Bus notifications are sent on stage transitions.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    bus_dir:
        Path to the JSONL bus directory for notifications (or None to skip).
    team:
        Team label for this instance.
    agent_id:
        Agent identifier for bus messages.
    partitioned_store:
        Optional PartitionedStore instance.  When provided, the queue
        partition is used for pipelines and pipeline_stages tables instead
        of opening a new connection to db_path.
    vector_clock:
        Optional VectorClock instance.  When provided, pipeline stage
        transitions attach vector clock timestamps to bus notifications
        for causal ordering verification.  Incoming vector clocks from
        upstream stages are merged before processing.
    """

    def __init__(self, db_path: str, bus_dir: Optional[str], team: str, agent_id: str,
                 partitioned_store: Optional["PartitionedStore"] = None,
                 vector_clock: Optional[object] = None) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self.team = team
        self.agent_id = agent_id
        self._partitioned_store = partitioned_store
        self._vector_clock = vector_clock
        self._stage_vectors: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()
        if partitioned_store is not None:
            self._conn = partitioned_store.queue.conn
            self._owns_conn = False
        else:
            self._conn = _open_db(db_path)
            self._owns_conn = True
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_conn:
                self._conn.close()

    @staticmethod
    def _detect_cycles(stages: list[dict]) -> bool:
        """Detect cycles in the stage dependency graph using Kahn's algorithm.

        Returns True if a cycle exists, False otherwise.
        """
        # Build adjacency list and in-degree map
        stage_names = {s["name"] for s in stages}
        in_degree: dict[str, int] = {s["name"]: 0 for s in stages}
        adjacency: dict[str, list[str]] = {s["name"]: [] for s in stages}

        for s in stages:
            for dep in s.get("depends_on", []):
                if dep in stage_names:
                    adjacency[dep].append(s["name"])
                    in_degree[s["name"]] += 1

        # Kahn's algorithm
        queue = deque(name for name, deg in in_degree.items() if deg == 0)
        visited_count = 0

        while queue:
            node = queue.popleft()
            visited_count += 1
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return visited_count != len(stage_names)

    @_retry_on_busy
    def create_pipeline(self, name: str, stages: list[dict]) -> int:
        """Create a pipeline with the given stages.

        Parameters
        ----------
        name:
            Unique pipeline name.
        stages:
            List of dicts, each with ``name`` (str), optional ``team`` (str),
            optional ``depends_on`` (list[str]).

        Returns
        -------
        int
            The pipeline id.

        Raises
        ------
        ValueError
            If duplicate stage names are found or if the dependency graph
            contains cycles.
        """
        # Check for duplicate stage names
        stage_names = [s["name"] for s in stages]
        if len(stage_names) != len(set(stage_names)):
            seen = set()
            dupes = []
            for n in stage_names:
                if n in seen:
                    dupes.append(n)
                seen.add(n)
            raise ValueError(f"Duplicate stage names: {dupes}")

        # Check for cycles
        if self._detect_cycles(stages):
            raise ValueError("Pipeline dependency graph contains a cycle")

        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO pipelines (name, stages, status, created_at) VALUES (?, ?, 'active', ?)",
                (name, json.dumps(stages), now),
            )
            pipeline_id = cur.lastrowid

            for stage in stages:
                deps = json.dumps(stage.get("depends_on", []))
                # If a stage has no dependencies, it starts as 'ready'
                initial_status = "ready" if not stage.get("depends_on") else "waiting"
                self._conn.execute(
                    """INSERT INTO pipeline_stages
                       (pipeline_id, stage_name, assigned_team, depends_on, status)
                       VALUES (?, ?, ?, ?, ?)""",
                    (pipeline_id, stage["name"], stage.get("team"), deps, initial_status),
                )
            self._conn.commit()

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "pipeline-created",
            "pipeline_id": pipeline_id,
            "name": name,
            "stage_count": len(stages),
        })
        return pipeline_id

    @_retry_on_busy
    def check_stage_ready(self, pipeline_id: int, stage_name: str) -> bool:
        """Check whether all dependencies for a stage are completed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT depends_on FROM pipeline_stages WHERE pipeline_id = ? AND stage_name = ?",
                (pipeline_id, stage_name),
            ).fetchone()

            if row is None:
                return False

            deps = json.loads(row["depends_on"])
            if not deps:
                return True

            for dep in deps:
                dep_row = self._conn.execute(
                    "SELECT status FROM pipeline_stages WHERE pipeline_id = ? AND stage_name = ?",
                    (pipeline_id, dep),
                ).fetchone()
                if dep_row is None or dep_row["status"] != "completed":
                    return False
            return True

    @_retry_on_busy
    def start_stage(self, pipeline_id: int, stage_name: str,
                    incoming_vector_clock: Optional[dict] = None) -> None:
        """Mark a stage as in_progress.

        Only stages with status 'ready' can be started. Raises ValueError
        if the stage is already in_progress, completed, or failed.

        Parameters
        ----------
        pipeline_id:
            The pipeline this stage belongs to.
        stage_name:
            Name of the stage to start.
        incoming_vector_clock:
            Optional vector clock from the upstream stage that triggered
            this one.  When provided and a vector_clock is configured,
            the clock is merged before processing to maintain causal order.
        """
        now = time.time()

        # Merge incoming vector clock if available
        if incoming_vector_clock and self._vector_clock is not None:
            try:
                self._vector_clock.merge(incoming_vector_clock)
                stage_key = f"{pipeline_id}:{stage_name}"
                self._stage_vectors[stage_key] = self._vector_clock.vector
            except Exception as e:
                logger.warning("Vector clock merge failed for stage %s: %s", stage_name, e)

        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM pipeline_stages WHERE pipeline_id = ? AND stage_name = ?",
                (pipeline_id, stage_name),
            ).fetchone()
            if row is None:
                raise ValueError(f"Stage '{stage_name}' not found in pipeline {pipeline_id}")
            if row["status"] != "ready":
                raise ValueError(
                    f"Stage '{stage_name}' cannot be started: current status is '{row['status']}' (must be 'ready')"
                )
            self._conn.execute(
                "UPDATE pipeline_stages SET status = 'in_progress', started_at = ? WHERE pipeline_id = ? AND stage_name = ?",
                (now, pipeline_id, stage_name),
            )
            self._conn.commit()

        body: dict = {
            "event": "pipeline-stage-started",
            "pipeline_id": pipeline_id,
            "stage_name": stage_name,
        }
        if self._vector_clock is not None:
            body["vector_clock"] = self._vector_clock.vector

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, body)

    @_retry_on_busy
    def complete_stage(self, pipeline_id: int, stage_name: str, output_data: dict) -> None:
        """Mark a stage as completed and store its output data.

        When a vector_clock is configured, the clock is ticked and attached
        to the bus notification so downstream stages can merge it.
        """
        now = time.time()

        # Tick vector clock on stage completion
        vc_snapshot = None
        if self._vector_clock is not None:
            try:
                vc_snapshot = self._vector_clock.tick()
                stage_key = f"{pipeline_id}:{stage_name}"
                self._stage_vectors[stage_key] = vc_snapshot
            except Exception as e:
                logger.warning("Vector clock tick failed for stage %s: %s", stage_name, e)

        with self._lock:
            self._conn.execute(
                """UPDATE pipeline_stages
                   SET status = 'completed', output_data = ?, completed_at = ?
                   WHERE pipeline_id = ? AND stage_name = ?""",
                (json.dumps(output_data), now, pipeline_id, stage_name),
            )
            self._conn.commit()

        body: dict = {
            "event": "pipeline-stage-completed",
            "pipeline_id": pipeline_id,
            "stage_name": stage_name,
        }
        if vc_snapshot is not None:
            body["vector_clock"] = vc_snapshot

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, body)

    @_retry_on_busy
    def fail_stage(self, pipeline_id: int, stage_name: str, error: str) -> None:
        """Mark a stage as failed with an error message."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """UPDATE pipeline_stages
                   SET status = 'failed', error_message = ?, completed_at = ?
                   WHERE pipeline_id = ? AND stage_name = ?""",
                (error, now, pipeline_id, stage_name),
            )
            self._conn.commit()

        _bus_notify(self.bus_dir, "global", self.agent_id, self.team, {
            "event": "pipeline-stage-failed",
            "pipeline_id": pipeline_id,
            "stage_name": stage_name,
            "error": error,
        })

    @_retry_on_busy
    def get_ready_stages(self, pipeline_id: int) -> list[dict]:
        """Return stages whose dependencies are all met (status = 'ready' or newly ready)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Get all stages for this pipeline
                all_stages = self._conn.execute(
                    "SELECT * FROM pipeline_stages WHERE pipeline_id = ?",
                    (pipeline_id,),
                ).fetchall()

                completed = {s["stage_name"] for s in all_stages if s["status"] == "completed"}
                ready = []
                for stage in all_stages:
                    if stage["status"] in ("completed", "in_progress"):
                        continue
                    deps = json.loads(stage["depends_on"])
                    if all(d in completed for d in deps):
                        ready.append(dict(stage))

                # Update any 'waiting' stages to 'ready' if their deps are met
                for r in ready:
                    if r["status"] == "waiting":
                        self._conn.execute(
                            "UPDATE pipeline_stages SET status = 'ready' WHERE id = ?",
                            (r["id"],),
                        )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        return ready

    @_retry_on_busy
    def get_pipeline_status(self, pipeline_id: int) -> dict:
        """Return full pipeline status including all stages."""
        with self._lock:
            pipeline_row = self._conn.execute(
                "SELECT * FROM pipelines WHERE id = ?",
                (pipeline_id,),
            ).fetchone()
            if pipeline_row is None:
                return {}

            stage_rows = self._conn.execute(
                "SELECT * FROM pipeline_stages WHERE pipeline_id = ? ORDER BY id",
                (pipeline_id,),
            ).fetchall()

        stages = [dict(s) for s in stage_rows]
        all_completed = all(s["status"] == "completed" for s in stages)
        any_failed = any(s["status"] == "failed" for s in stages)

        if all_completed:
            effective_status = "completed"
        elif any_failed:
            effective_status = "failed"
        else:
            effective_status = pipeline_row["status"]

        return {
            "id": pipeline_row["id"],
            "name": pipeline_row["name"],
            "status": effective_status,
            "created_at": pipeline_row["created_at"],
            "stages": stages,
        }

    @_retry_on_busy
    def trigger_downstream(self, pipeline_id: int, completed_stage: str) -> list[str]:
        """After a stage completes, find and mark newly-ready downstream stages.

        Returns the names of stages that became ready.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                all_stages = self._conn.execute(
                    "SELECT * FROM pipeline_stages WHERE pipeline_id = ?",
                    (pipeline_id,),
                ).fetchall()

                completed = {s["stage_name"] for s in all_stages if s["status"] == "completed"}
                newly_ready: list[str] = []

                for stage in all_stages:
                    if stage["status"] != "waiting":
                        continue
                    deps = json.loads(stage["depends_on"])
                    if completed_stage in deps and all(d in completed for d in deps):
                        newly_ready.append(stage["stage_name"])

                # Update status to ready
                for name in newly_ready:
                    self._conn.execute(
                        "UPDATE pipeline_stages SET status = 'ready' WHERE pipeline_id = ? AND stage_name = ?",
                        (pipeline_id, name),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        # Bus notification for each newly-ready stage
        for name in newly_ready:
            body: dict = {
                "event": "pipeline-stage-ready",
                "pipeline_id": pipeline_id,
                "stage_name": name,
                "triggered_by": completed_stage,
            }
            # Attach vector clock from the completed stage so downstream
            # stages can merge it on start
            if self._vector_clock is not None:
                completed_key = f"{pipeline_id}:{completed_stage}"
                vc = self._stage_vectors.get(completed_key)
                if vc is not None:
                    body["vector_clock"] = vc
                else:
                    body["vector_clock"] = self._vector_clock.vector

            _bus_notify(self.bus_dir, "global", self.agent_id, self.team, body)

        return newly_ready

    def verify_ordering(self, pipeline_id: int) -> dict:
        """Verify that pipeline stage messages are in causal order.

        Uses the stored vector clock snapshots for each stage to check
        whether the completion order respects causal dependencies.

        Parameters
        ----------
        pipeline_id:
            The pipeline to verify.

        Returns
        -------
        dict
            {
                "pipeline_id": int,
                "in_order": bool,
                "violations": list[dict],
                "stage_vectors": dict,
            }
        """
        if self._vector_clock is None:
            return {
                "pipeline_id": pipeline_id,
                "in_order": True,
                "violations": [],
                "stage_vectors": {},
                "note": "No vector clock configured; ordering not tracked.",
            }

        # Gather stage vectors for this pipeline
        prefix = f"{pipeline_id}:"
        stage_vecs: dict[str, dict[str, int]] = {}
        for key, vc in self._stage_vectors.items():
            if key.startswith(prefix):
                stage_name = key[len(prefix):]
                stage_vecs[stage_name] = vc

        if not stage_vecs:
            return {
                "pipeline_id": pipeline_id,
                "in_order": True,
                "violations": [],
                "stage_vectors": {},
                "note": "No vector clock data recorded for this pipeline.",
            }

        # Get the pipeline dependency graph
        violations = []
        with self._lock:
            stages = self._conn.execute(
                "SELECT stage_name, depends_on, status FROM pipeline_stages WHERE pipeline_id = ?",
                (pipeline_id,),
            ).fetchall()

        for stage in stages:
            stage_name = stage["stage_name"]
            deps = json.loads(stage["depends_on"]) if stage["depends_on"] else []
            if stage_name not in stage_vecs:
                continue
            stage_vc = stage_vecs[stage_name]

            for dep_name in deps:
                if dep_name not in stage_vecs:
                    continue
                dep_vc = stage_vecs[dep_name]
                # The dependency should have happened-before this stage.
                # Import is avoided; we do a manual check inline.
                # is_before: dep_vc[a] <= stage_vc[a] for all a, and strict < for at least one
                all_agents = set(dep_vc.keys()) | set(stage_vc.keys())
                all_leq = True
                at_least_one_less = False
                for agent in all_agents:
                    c_dep = dep_vc.get(agent, 0)
                    c_stage = stage_vc.get(agent, 0)
                    if c_dep > c_stage:
                        all_leq = False
                        break
                    if c_dep < c_stage:
                        at_least_one_less = True

                if not (all_leq and at_least_one_less):
                    violations.append({
                        "stage": stage_name,
                        "dependency": dep_name,
                        "stage_vc": stage_vc,
                        "dep_vc": dep_vc,
                        "reason": (
                            f"Stage '{stage_name}' (vc={stage_vc}) does not causally "
                            f"follow dependency '{dep_name}' (vc={dep_vc})"
                        ),
                    })

        return {
            "pipeline_id": pipeline_id,
            "in_order": len(violations) == 0,
            "violations": violations,
            "stage_vectors": stage_vecs,
        }


# ===================================================================
# Scratchpad
# ===================================================================


class Scratchpad:
    """Namespaced key-value scratchpad with TTL expiration.

    Values are stored as JSON strings.  Each key is scoped to a namespace
    (defaults to the team name; use ``"global"`` for cross-team sharing).

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    team:
        Team label (used as default namespace).
    agent_id:
        Agent identifier (recorded as ``written_by``).
    partitioned_store:
        Optional PartitionedStore instance.  When provided, the findings
        partition is used for the scratchpad table instead of opening a
        new connection to db_path.
    """

    def __init__(self, db_path: str, team: str, agent_id: str,
                 partitioned_store: Optional["PartitionedStore"] = None) -> None:
        self.db_path = db_path
        self.team = team
        self.agent_id = agent_id
        self._partitioned_store = partitioned_store
        self._lock = threading.Lock()
        if partitioned_store is not None:
            self._conn = partitioned_store.findings.conn
            self._owns_conn = False
        else:
            self._conn = _open_db(db_path)
            self._owns_conn = True
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_conn:
                self._conn.close()

    @_retry_on_busy
    def write(self, key: str, value: Any, namespace: Optional[str] = None, ttl: int = 3600) -> None:
        """Write a key-value pair. Overwrites if key+namespace already exists."""
        ns = namespace or self.team
        now = time.time()
        expires_at = now + ttl
        with self._lock:
            self._conn.execute(
                """INSERT INTO scratchpad (key, namespace, value, written_by, ttl_seconds, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key, namespace) DO UPDATE SET
                       value=excluded.value, written_by=excluded.written_by,
                       ttl_seconds=excluded.ttl_seconds, created_at=excluded.created_at,
                       expires_at=excluded.expires_at""",
                (key, ns, json.dumps(value), self.agent_id, ttl, now, expires_at),
            )
            self._conn.commit()

    @_retry_on_busy
    def read(self, key: str, namespace: Optional[str] = None) -> Optional[Any]:
        """Read a value by key. Returns None if not found or expired."""
        ns = namespace or self.team
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM scratchpad WHERE key = ? AND namespace = ? AND expires_at > ?",
                (key, ns, now),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["value"])

    @_retry_on_busy
    def read_all(self, namespace: Optional[str] = None) -> dict:
        """Read all non-expired entries in a namespace. Returns {key: value}."""
        ns = namespace or self.team
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM scratchpad WHERE namespace = ? AND expires_at > ?",
                (ns, now),
            ).fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    @_retry_on_busy
    def delete(self, key: str, namespace: Optional[str] = None) -> None:
        """Delete a key from the scratchpad."""
        ns = namespace or self.team
        with self._lock:
            self._conn.execute(
                "DELETE FROM scratchpad WHERE key = ? AND namespace = ?",
                (key, ns),
            )
            self._conn.commit()

    @_retry_on_busy
    def cleanup_expired(self) -> int:
        """Delete all expired entries. Returns count of deleted rows."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM scratchpad WHERE expires_at <= ?",
                (now,),
            )
            count = cur.rowcount
            self._conn.commit()
        return count

    @_retry_on_busy
    def list_keys(self, namespace: Optional[str] = None) -> list[str]:
        """List all non-expired keys in a namespace."""
        ns = namespace or self.team
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT key FROM scratchpad WHERE namespace = ? AND expires_at > ?",
                (ns, now),
            ).fetchall()
        return [r["key"] for r in rows]
