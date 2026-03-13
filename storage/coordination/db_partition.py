"""Partitioned SQLite Database Manager for Multi-Agent Coordination.

Solves the core SQLite contention bottleneck by splitting the single monolithic
database into four functional partitions, each independently using WAL mode:

- **hub.db**      -- agent_status, coordinator_instructions (coordinator writes only, ~1 writer)
- **queue.db**    -- work_queue, pipelines, pipeline_stages (work stealing, max 4 claimers)
- **comms.db**    -- channels, presence, progress, read_offsets, messages (multi-writer, team-partitioned)
- **findings.db** -- team_findings, think_tank, scratchpad, work_items,
                     help_requests, team_capabilities, team_status (append-heavy, few conflicts)

Design rationale (SOP-034 compliance):
  - Each partition stays under the 4-concurrent-writer limit
  - hub.db has exactly 1 writer (coordinator) -- zero contention
  - queue.db contention is bounded by atomic steal semantics (BEGIN IMMEDIATE)
  - comms.db writes are naturally partitioned by team
  - findings.db is append-heavy with rare conflicts

All writes use BEGIN IMMEDIATE with retry on SQLITE_BUSY (5 retries, exponential
backoff + jitter).  Each DB independently uses WAL mode and busy_timeout=30s.
Thread-safe via per-DB threading.Lock.  Connection pooling: 1 connection per DB
per thread (via thread-local storage).

Backwards-compatible: existing code can migrate incrementally by swapping the
single db_path with PartitionedStore.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import random
import sqlite3
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 0.1  # seconds
BUSY_TIMEOUT_MS = 30000

# Partition names and their DB filenames
PARTITION_NAMES = ("hub", "queue", "comms", "findings")

# ---------------------------------------------------------------------------
# Schemas per partition
# ---------------------------------------------------------------------------

_HUB_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_ci_target_status
    ON coordinator_instructions(target_agent, status);
"""

_QUEUE_SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_work_queue_status_priority
    ON work_queue(status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_stages_pipeline_status
    ON pipeline_stages(pipeline_id, status);
"""

_COMMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT UNIQUE NOT NULL,
    channel_type TEXT NOT NULL,
    created_by TEXT NOT NULL,
    participants TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL,
    last_activity REAL
);

CREATE TABLE IF NOT EXISTS presence (
    team TEXT PRIMARY KEY,
    status TEXT DEFAULT 'available',
    current_channels TEXT,
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    team TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    progress_pct REAL DEFAULT 0,
    items_total INTEGER DEFAULT 0,
    items_done INTEGER DEFAULT 0,
    estimated_completion_ts REAL,
    current_bottleneck TEXT,
    last_updated REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS read_offsets (
    team TEXT NOT NULL,
    channel TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team, channel)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT NOT NULL,
    sender_team TEXT NOT NULL,
    sender_agent TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_name, created_at);
CREATE INDEX IF NOT EXISTS idx_channels_status ON channels(status);
"""

_FINDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium',
    evidence TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS think_tank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_team TEXT NOT NULL,
    finding_id INTEGER,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'new',
    created_at REAL NOT NULL,
    resolved_at REAL
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

CREATE INDEX IF NOT EXISTS idx_findings_team ON team_findings(team);
CREATE INDEX IF NOT EXISTS idx_findings_category ON team_findings(category);
CREATE INDEX IF NOT EXISTS idx_think_tank_status ON think_tank(status);
CREATE INDEX IF NOT EXISTS idx_scratchpad_ns_exp ON scratchpad(namespace, expires_at);
CREATE INDEX IF NOT EXISTS idx_work_items_status ON work_items(status);
CREATE INDEX IF NOT EXISTS idx_work_items_team ON work_items(assigned_to);
CREATE INDEX IF NOT EXISTS idx_help_requests_status ON help_requests(status);
CREATE INDEX IF NOT EXISTS idx_help_requests_work ON help_requests(work_item_id);
"""

PARTITION_SCHEMAS = {
    "hub": _HUB_SCHEMA,
    "queue": _QUEUE_SCHEMA,
    "comms": _COMMS_SCHEMA,
    "findings": _FINDINGS_SCHEMA,
}

# Map table names to their partition
TABLE_TO_PARTITION = {
    "agent_status": "hub",
    "coordinator_instructions": "hub",
    "work_queue": "queue",
    "pipelines": "queue",
    "pipeline_stages": "queue",
    "channels": "comms",
    "presence": "comms",
    "progress": "comms",
    "read_offsets": "comms",
    "messages": "comms",
    "team_findings": "findings",
    "think_tank": "findings",
    "scratchpad": "findings",
    "work_items": "findings",
    "help_requests": "findings",
    "team_capabilities": "findings",
    "team_status": "findings",
}

# ---------------------------------------------------------------------------
# Retry decorator with jitter (SOP-034 compliant)
# ---------------------------------------------------------------------------


def retry_on_busy(func):
    """Decorator: retry on SQLITE_BUSY with exponential backoff + jitter.

    5 retries, base delay 100ms, doubles each attempt, with up to 50% jitter.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        delay = RETRY_BACKOFF_BASE
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    last_err = e
                    jitter = delay * random.uniform(0, 0.5)
                    sleep_time = delay + jitter
                    logger.debug(
                        "SQLITE_BUSY on %s (attempt %d/%d), retrying in %.3fs",
                        func.__name__, attempt + 1, MAX_RETRIES, sleep_time,
                    )
                    time.sleep(sleep_time)
                    delay *= 2
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


# ---------------------------------------------------------------------------
# Single Partition Connection Manager
# ---------------------------------------------------------------------------


class _PartitionDB:
    """Manages a single SQLite partition database.

    Provides thread-safe connection access with WAL mode, busy_timeout,
    and schema initialization.  Uses thread-local storage for connection
    pooling (one connection per thread).

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    schema:
        SQL schema to initialize (CREATE TABLE IF NOT EXISTS statements).
    name:
        Human-readable partition name for logging.
    """

    def __init__(self, db_path: str, schema: str, name: str) -> None:
        self.db_path = db_path
        self.schema = schema
        self.name = name
        self._lock = threading.Lock()
        self._local = threading.local()
        self._closed = False

        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        # Initialize schema on the main thread connection
        conn = self._get_connection()
        with self._lock:
            conn.executescript(schema)
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local connection."""
        if self._closed:
            raise RuntimeError(f"Partition {self.name!r} is closed")

        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=BUSY_TIMEOUT_MS / 1000,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            self._local.conn = conn
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Thread-local connection (read-only property)."""
        return self._get_connection()

    @property
    def lock(self) -> threading.Lock:
        """The partition-level lock for serializing writes."""
        return self._lock

    def execute_write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a write operation with the partition lock held.

        Returns the cursor for lastrowid / rowcount access.
        """
        conn = self._get_connection()
        with self._lock:
            cursor = conn.execute(sql, params)
            conn.commit()
        return cursor

    def execute_read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Execute a read query. Reads do not need the lock in WAL mode."""
        conn = self._get_connection()
        return conn.execute(sql, params).fetchall()

    def execute_read_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute a read query returning a single row or None."""
        conn = self._get_connection()
        return conn.execute(sql, params).fetchone()

    def begin_immediate(self) -> sqlite3.Connection:
        """Start a BEGIN IMMEDIATE transaction. Caller must COMMIT/ROLLBACK.

        Returns the connection for use within a with-lock block.
        The caller MUST hold self._lock when using this.
        """
        conn = self._get_connection()
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def close(self) -> None:
        """Close all connections and mark as closed."""
        self._closed = True
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def get_wal_size(self) -> int:
        """Return the WAL file size in bytes, or 0 if not found."""
        wal_path = self.db_path + "-wal"
        try:
            return os.path.getsize(wal_path)
        except OSError:
            return 0

    def checkpoint(self) -> None:
        """Force a WAL checkpoint (TRUNCATE mode)."""
        conn = self._get_connection()
        with self._lock:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


# ---------------------------------------------------------------------------
# Partitioned Store
# ---------------------------------------------------------------------------


class PartitionedStore:
    """Unified interface to all partitioned databases.

    Creates and manages four SQLite databases in the given directory,
    routing operations to the correct partition based on table name.

    Parameters
    ----------
    db_dir:
        Directory to store partition database files.
        Defaults to ``<coordination_dir>/db/``.
    """

    def __init__(self, db_dir: Optional[str] = None) -> None:
        if db_dir is None:
            db_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "db"
            )
        self.db_dir = db_dir
        os.makedirs(db_dir, exist_ok=True)

        self._partitions: dict[str, _PartitionDB] = {}
        for name in PARTITION_NAMES:
            db_path = os.path.join(db_dir, f"{name}.db")
            self._partitions[name] = _PartitionDB(
                db_path=db_path,
                schema=PARTITION_SCHEMAS[name],
                name=name,
            )
        self._closed = False

        logger.info(
            "PartitionedStore initialized with %d partitions in %s",
            len(self._partitions), db_dir,
        )

    # -- Partition access ---------------------------------------------------

    def get_partition(self, name: str) -> _PartitionDB:
        """Get a partition by name (hub, queue, comms, findings)."""
        if self._closed:
            raise RuntimeError("PartitionedStore is closed")
        if name not in self._partitions:
            raise ValueError(
                f"Unknown partition {name!r}. Valid: {list(self._partitions)}"
            )
        return self._partitions[name]

    def partition_for_table(self, table_name: str) -> _PartitionDB:
        """Get the partition that owns the given table."""
        partition_name = TABLE_TO_PARTITION.get(table_name)
        if partition_name is None:
            raise ValueError(
                f"Unknown table {table_name!r}. Known tables: {list(TABLE_TO_PARTITION)}"
            )
        return self.get_partition(partition_name)

    @property
    def hub(self) -> _PartitionDB:
        """The hub partition (agent_status, coordinator_instructions)."""
        return self._partitions["hub"]

    @property
    def queue(self) -> _PartitionDB:
        """The queue partition (work_queue, pipelines, pipeline_stages)."""
        return self._partitions["queue"]

    @property
    def comms(self) -> _PartitionDB:
        """The comms partition (channels, presence, progress, messages)."""
        return self._partitions["comms"]

    @property
    def findings(self) -> _PartitionDB:
        """The findings partition (team_findings, scratchpad, help, etc.)."""
        return self._partitions["findings"]

    # -- Hub operations (Task 1: delta-lead design) -------------------------

    @retry_on_busy
    def register_agent(self, agent_id: str, team: str, role: str) -> None:
        """Register an agent in the hub partition."""
        now = time.time()
        self.hub.execute_write(
            """INSERT INTO agent_status
               (agent_id, team, role, status, progress_pct, started_at, last_updated)
               VALUES (?, ?, ?, 'initializing', 0, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
                   team=excluded.team, role=excluded.role,
                   status='initializing', progress_pct=0,
                   current_task=NULL, blockers=NULL, findings_count=0,
                   output_files=NULL, error_message=NULL,
                   started_at=excluded.started_at, last_updated=excluded.last_updated
            """,
            (agent_id, team, role, now, now),
        )

    @retry_on_busy
    def update_agent_status(self, agent_id: str, team: str, role: str,
                            status: str, progress_pct: float,
                            current_task: str, blockers: Optional[str] = None,
                            findings_count: int = 0,
                            output_files: Optional[str] = None) -> None:
        """Update an agent's status in the hub partition."""
        now = time.time()
        self.hub.execute_write(
            """INSERT INTO agent_status
               (agent_id, team, role, status, progress_pct, current_task,
                blockers, findings_count, output_files, error_message,
                started_at, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
                   team=excluded.team, role=excluded.role,
                   status=excluded.status, progress_pct=excluded.progress_pct,
                   current_task=excluded.current_task, blockers=excluded.blockers,
                   findings_count=excluded.findings_count,
                   output_files=excluded.output_files, error_message=NULL,
                   last_updated=excluded.last_updated
            """,
            (agent_id, team, role, status, progress_pct, current_task,
             blockers, findings_count, output_files, now, now),
        )

    @retry_on_busy
    def get_all_agent_status(self) -> list[dict]:
        """Read all agent statuses from hub."""
        rows = self.hub.execute_read(
            "SELECT * FROM agent_status ORDER BY last_updated DESC"
        )
        return [dict(r) for r in rows]

    @retry_on_busy
    def send_instruction(self, target_agent: str, instruction_type: str,
                         payload: Optional[str] = None) -> int:
        """Post a coordinator instruction to hub."""
        now = time.time()
        cur = self.hub.execute_write(
            """INSERT INTO coordinator_instructions
               (target_agent, instruction_type, payload, status, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (target_agent, instruction_type, payload, now),
        )
        return cur.lastrowid

    @retry_on_busy
    def check_instructions(self, agent_id: str) -> list[dict]:
        """Read and mark pending instructions for an agent."""
        now = time.time()
        db = self.hub
        with db.lock:
            conn = db.begin_immediate()
            try:
                rows = conn.execute(
                    """SELECT * FROM coordinator_instructions
                       WHERE target_agent = ? AND status = 'pending'
                       ORDER BY created_at ASC""",
                    (agent_id,),
                ).fetchall()
                instructions = [dict(r) for r in rows]
                if instructions:
                    ids = [inst["id"] for inst in instructions]
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"""UPDATE coordinator_instructions
                            SET status='read', read_at=?
                            WHERE id IN ({placeholders})""",
                        [now] + ids,
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return instructions

    # -- Queue operations ---------------------------------------------------

    @retry_on_busy
    def enqueue_work(self, team: str, title: str, description: str = "",
                     priority: int = 5) -> int:
        """Add a work item to the shared queue."""
        now = time.time()
        cur = self.queue.execute_write(
            """INSERT INTO work_queue
               (owner_team, title, description, priority, status, created_at)
               VALUES (?, ?, ?, ?, 'queued', ?)""",
            (team, title, description, priority, now),
        )
        return cur.lastrowid

    @retry_on_busy
    def steal_work(self, team: str) -> Optional[dict]:
        """Atomically claim the highest-priority unclaimed work item."""
        db = self.queue
        now = time.time()
        with db.lock:
            conn = db.begin_immediate()
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
                    """UPDATE work_queue
                       SET status='claimed', claimed_by=?, claimed_at=?
                       WHERE id=?""",
                    (team, now, work_id),
                )
                conn.execute("COMMIT")
                result = dict(row)
                result["claimed_by"] = team
                result["claimed_at"] = now
                result["status"] = "claimed"
                return result
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @retry_on_busy
    def complete_work(self, work_id: int, team: str, result: dict) -> None:
        """Mark a work item as completed."""
        now = time.time()
        cur = self.queue.execute_write(
            """UPDATE work_queue
               SET status='completed', result=?, completed_at=?
               WHERE id=? AND (claimed_by=? OR owner_team=?)""",
            (json.dumps(result), now, work_id, team, team),
        )
        if cur.rowcount == 0:
            raise ValueError(f"Work item {work_id} not found or not authorized for team {team!r}")

    @retry_on_busy
    def get_queue_depth(self) -> int:
        """Return number of queued (unclaimed) items."""
        row = self.queue.execute_read_one(
            "SELECT COUNT(*) as cnt FROM work_queue WHERE status='queued'"
        )
        return row["cnt"] if row else 0

    # -- Pipeline operations ------------------------------------------------

    @retry_on_busy
    def create_pipeline(self, name: str, stages: list[dict]) -> int:
        """Create a pipeline with given stages in the queue partition."""
        now = time.time()
        db = self.queue
        with db.lock:
            conn = db.begin_immediate()
            try:
                cur = conn.execute(
                    """INSERT INTO pipelines (name, stages, status, created_at)
                       VALUES (?, ?, 'active', ?)""",
                    (name, json.dumps(stages), now),
                )
                pipeline_id = cur.lastrowid
                for stage in stages:
                    deps = json.dumps(stage.get("depends_on", []))
                    initial_status = "ready" if not stage.get("depends_on") else "waiting"
                    conn.execute(
                        """INSERT INTO pipeline_stages
                           (pipeline_id, stage_name, assigned_team, depends_on, status)
                           VALUES (?, ?, ?, ?, ?)""",
                        (pipeline_id, stage["name"], stage.get("team"), deps, initial_status),
                    )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        return pipeline_id

    @retry_on_busy
    def get_pipeline_status(self, pipeline_id: int) -> dict:
        """Return full pipeline status including all stages."""
        db = self.queue
        pipeline_row = db.execute_read_one(
            "SELECT * FROM pipelines WHERE id=?", (pipeline_id,)
        )
        if pipeline_row is None:
            return {}
        stage_rows = db.execute_read(
            "SELECT * FROM pipeline_stages WHERE pipeline_id=? ORDER BY id",
            (pipeline_id,),
        )
        stages = [dict(s) for s in stage_rows]
        all_completed = all(s["status"] == "completed" for s in stages)
        any_failed = any(s["status"] == "failed" for s in stages)
        if all_completed:
            effective = "completed"
        elif any_failed:
            effective = "failed"
        else:
            effective = pipeline_row["status"]
        return {
            "id": pipeline_row["id"],
            "name": pipeline_row["name"],
            "status": effective,
            "created_at": pipeline_row["created_at"],
            "stages": stages,
        }

    # -- Comms operations ---------------------------------------------------

    @retry_on_busy
    def set_presence(self, team: str, status: str) -> None:
        """Set a team's presence in the comms partition."""
        now = time.time()
        self.comms.execute_write(
            """INSERT INTO presence (team, status, last_seen)
               VALUES (?, ?, ?)
               ON CONFLICT(team) DO UPDATE SET
                   status=excluded.status, last_seen=excluded.last_seen""",
            (team, status, now),
        )

    @retry_on_busy
    def get_presence(self, team: Optional[str] = None) -> Any:
        """Get presence for one team or all teams."""
        if team is not None:
            row = self.comms.execute_read_one(
                "SELECT * FROM presence WHERE team=?", (team,)
            )
            if row is None:
                return {"team": team, "status": "unknown", "current_channels": [], "last_seen": None}
            d = dict(row)
            d["current_channels"] = json.loads(d["current_channels"]) if d["current_channels"] else []
            return d
        rows = self.comms.execute_read("SELECT * FROM presence")
        results = []
        for row in rows:
            d = dict(row)
            d["current_channels"] = json.loads(d["current_channels"]) if d["current_channels"] else []
            results.append(d)
        return results

    @retry_on_busy
    def update_progress(self, team: str, phase: str, progress_pct: float,
                        items_total: int, items_done: int,
                        est_completion_ts: Optional[float] = None,
                        bottleneck: Optional[str] = None) -> None:
        """Update a team's progress in the comms partition."""
        now = time.time()
        self.comms.execute_write(
            """INSERT INTO progress
               (team, phase, progress_pct, items_total, items_done,
                estimated_completion_ts, current_bottleneck, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(team) DO UPDATE SET
                   phase=excluded.phase, progress_pct=excluded.progress_pct,
                   items_total=excluded.items_total, items_done=excluded.items_done,
                   estimated_completion_ts=excluded.estimated_completion_ts,
                   current_bottleneck=excluded.current_bottleneck,
                   last_updated=excluded.last_updated""",
            (team, phase, progress_pct, items_total, items_done,
             est_completion_ts, bottleneck, now),
        )

    @retry_on_busy
    def store_message(self, channel_name: str, sender_team: str,
                      sender_agent: str, msg_type: str, body: dict) -> int:
        """Store a message in the comms partition."""
        now = time.time()
        cur = self.comms.execute_write(
            """INSERT INTO messages
               (channel_name, sender_team, sender_agent, msg_type, body, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (channel_name, sender_team, sender_agent, msg_type, json.dumps(body), now),
        )
        return cur.lastrowid

    # -- Findings operations ------------------------------------------------

    @retry_on_busy
    def add_finding(self, team: str, agent_id: str, category: str,
                    title: str, description: str = "",
                    priority: str = "medium", evidence: str = "") -> int:
        """Add a team finding to the findings partition."""
        now = time.time()
        cur = self.findings.execute_write(
            """INSERT INTO team_findings
               (team, agent_id, category, title, description, priority, evidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (team, agent_id, category, title, description, priority, evidence, now),
        )
        return cur.lastrowid

    @retry_on_busy
    def get_findings(self, team: Optional[str] = None,
                     category: Optional[str] = None) -> list[dict]:
        """Read findings, optionally filtered by team and/or category."""
        sql = "SELECT * FROM team_findings WHERE 1=1"
        params: list = []
        if team:
            sql += " AND team=?"
            params.append(team)
        if category:
            sql += " AND category=?"
            params.append(category)
        sql += " ORDER BY created_at DESC"
        rows = self.findings.execute_read(sql, tuple(params))
        return [dict(r) for r in rows]

    @retry_on_busy
    def scratchpad_write(self, key: str, value: Any, namespace: str,
                         written_by: str, ttl: int = 3600) -> None:
        """Write to the scratchpad in the findings partition."""
        now = time.time()
        expires_at = now + ttl
        self.findings.execute_write(
            """INSERT INTO scratchpad
               (key, namespace, value, written_by, ttl_seconds, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key, namespace) DO UPDATE SET
                   value=excluded.value, written_by=excluded.written_by,
                   ttl_seconds=excluded.ttl_seconds, created_at=excluded.created_at,
                   expires_at=excluded.expires_at""",
            (key, namespace, json.dumps(value), written_by, ttl, now, expires_at),
        )

    @retry_on_busy
    def scratchpad_read(self, key: str, namespace: str) -> Optional[Any]:
        """Read from the scratchpad. Returns None if not found or expired."""
        now = time.time()
        row = self.findings.execute_read_one(
            "SELECT value FROM scratchpad WHERE key=? AND namespace=? AND expires_at>?",
            (key, namespace, now),
        )
        if row is None:
            return None
        return json.loads(row["value"])

    @retry_on_busy
    def add_help_request(self, requesting_team: str, requesting_agent: str,
                         work_item_id: int, description: str,
                         required_capabilities: Optional[str] = None) -> int:
        """Create a help request in the findings partition."""
        now = time.time()
        cur = self.findings.execute_write(
            """INSERT INTO help_requests
               (requesting_team, requesting_agent, work_item_id,
                description, required_capabilities, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?)""",
            (requesting_team, requesting_agent, work_item_id,
             description, required_capabilities, now),
        )
        return cur.lastrowid

    @retry_on_busy
    def offer_help(self, request_id: int, helper_team: str,
                   helper_agent: str) -> bool:
        """Atomically accept a help request. Returns True if claimed."""
        db = self.findings
        with db.lock:
            conn = db.begin_immediate()
            try:
                row = conn.execute(
                    "SELECT status, requesting_team FROM help_requests WHERE id=?",
                    (request_id,),
                ).fetchone()
                if row is None or row["status"] != "open":
                    conn.execute("ROLLBACK")
                    return False
                if row["requesting_team"] == helper_team:
                    conn.execute("ROLLBACK")
                    raise ValueError("Cannot help your own request")
                conn.execute(
                    """UPDATE help_requests
                       SET status='accepted', accepted_by_team=?, accepted_by_agent=?
                       WHERE id=? AND status='open'""",
                    (helper_team, helper_agent, request_id),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    # -- Lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close all partition connections."""
        self._closed = True
        for name, db in self._partitions.items():
            try:
                db.close()
            except Exception:
                logger.warning("Error closing partition %s", name, exc_info=True)

    def checkpoint_all(self) -> dict[str, int]:
        """Checkpoint all partitions. Returns {name: wal_size_before}."""
        results = {}
        for name, db in self._partitions.items():
            results[name] = db.get_wal_size()
            db.checkpoint()
        return results

    def get_stats(self) -> dict[str, dict]:
        """Return basic stats for each partition (WAL size, table counts)."""
        stats = {}
        for name, db in self._partitions.items():
            partition_stats = {
                "db_path": db.db_path,
                "wal_size_bytes": db.get_wal_size(),
            }
            # Count rows in each table
            try:
                tables = db.execute_read(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                for t in tables:
                    tname = t["name"]
                    row = db.execute_read_one(f"SELECT COUNT(*) as cnt FROM [{tname}]")
                    partition_stats[f"rows_{tname}"] = row["cnt"] if row else 0
            except Exception:
                pass
            stats[name] = partition_stats
        return stats

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ---------------------------------------------------------------------------
# Migration helper: convert monolithic DB to partitioned
# ---------------------------------------------------------------------------


def migrate_from_monolithic(monolithic_path: str, target_dir: str) -> PartitionedStore:
    """Migrate data from a monolithic SQLite DB to partitioned stores.

    Reads tables from the old single database and inserts them into the
    appropriate partition.  Skips tables that don't exist in the source.

    Parameters
    ----------
    monolithic_path:
        Path to the existing single SQLite database.
    target_dir:
        Directory for the new partitioned databases.

    Returns
    -------
    PartitionedStore
        The initialized partitioned store with migrated data.
    """
    if not os.path.exists(monolithic_path):
        raise FileNotFoundError(f"Monolithic DB not found: {monolithic_path}")

    store = PartitionedStore(db_dir=target_dir)

    src = sqlite3.connect(monolithic_path, timeout=30)
    src.row_factory = sqlite3.Row

    # Get list of tables in source
    src_tables = {
        row[0]
        for row in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    migrated = {}
    for table_name, partition_name in TABLE_TO_PARTITION.items():
        if table_name not in src_tables:
            continue

        target_db = store.get_partition(partition_name)

        # Read all rows from source
        rows = src.execute(f"SELECT * FROM [{table_name}]").fetchall()
        if not rows:
            migrated[table_name] = 0
            continue

        # Get column names
        cols = [desc[0] for desc in src.execute(f"SELECT * FROM [{table_name}] LIMIT 0").description]
        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(f"[{c}]" for c in cols)

        with target_db.lock:
            conn = target_db.conn
            for row in rows:
                conn.execute(
                    f"INSERT OR REPLACE INTO [{table_name}] ({col_list}) VALUES ({placeholders})",
                    tuple(row),
                )
            conn.commit()

        migrated[table_name] = len(rows)

    src.close()

    logger.info("Migration complete: %s", migrated)
    return store


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    """Quick self-test to verify partitioning works correctly."""
    import tempfile
    import shutil

    test_dir = tempfile.mkdtemp(prefix="db_partition_test_")
    try:
        store = PartitionedStore(db_dir=test_dir)

        # Test hub operations
        store.register_agent("test-agent-1", "team-alpha", "lead")
        agents = store.get_all_agent_status()
        assert len(agents) == 1, f"Expected 1 agent, got {len(agents)}"
        assert agents[0]["agent_id"] == "test-agent-1"

        # Test queue operations
        work_id = store.enqueue_work("team-alpha", "Test task", "Do something")
        assert work_id is not None
        depth = store.get_queue_depth()
        assert depth == 1, f"Expected queue depth 1, got {depth}"

        stolen = store.steal_work("team-beta")
        assert stolen is not None
        assert stolen["claimed_by"] == "team-beta"
        depth = store.get_queue_depth()
        assert depth == 0

        # Test comms operations
        store.set_presence("team-alpha", "available")
        p = store.get_presence("team-alpha")
        assert p["status"] == "available"

        store.update_progress("team-alpha", "phase-1", 50.0, 10, 5)

        # Test findings operations
        fid = store.add_finding("team-alpha", "agent-1", "bug", "Test bug", "Found it")
        assert fid is not None
        findings = store.get_findings(team="team-alpha")
        assert len(findings) == 1

        # Test scratchpad
        store.scratchpad_write("key1", {"data": 42}, "global", "agent-1")
        val = store.scratchpad_read("key1", "global")
        assert val == {"data": 42}, f"Expected dict, got {val}"

        # Test instruction flow
        inst_id = store.send_instruction("test-agent-1", "redirect", '{"target": "new-task"}')
        assert inst_id is not None
        insts = store.check_instructions("test-agent-1")
        assert len(insts) == 1

        # Test stats
        stats = store.get_stats()
        assert "hub" in stats
        assert "queue" in stats
        assert "comms" in stats
        assert "findings" in stats

        # Verify partition isolation
        assert os.path.exists(os.path.join(test_dir, "hub.db"))
        assert os.path.exists(os.path.join(test_dir, "queue.db"))
        assert os.path.exists(os.path.join(test_dir, "comms.db"))
        assert os.path.exists(os.path.join(test_dir, "findings.db"))

        store.close()
        print("PASS: All self-tests passed.")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _self_test()
