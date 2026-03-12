#!/usr/bin/env python3
"""Production-ready multi-team runner for Proto A coordination.

Launches N teams with M agents each, all communicating via the Proto A
JSONL message bus and SQLite shared state.  Accepts team configs as JSON,
initialises the coordination directory, registers all agents in SQLite,
starts the coordinator with heartbeat monitoring, provides clean shutdown,
and writes operational logs to the bus.

Usage:
    python multi_team_runner.py --config teams.json
    python multi_team_runner.py --teams 3 --agents-per-team 2
    python multi_team_runner.py --status
    python multi_team_runner.py --shutdown

Config JSON format:
    {
        "teams": [
            {
                "name": "alpha",
                "agents": [
                    {"agent_id": "alpha-lead", "role": "coordinator"},
                    {"agent_id": "alpha-worker-1", "role": "worker"}
                ]
            }
        ],
        "heartbeat_interval": 30,
        "dead_agent_timeout": 120,
        "cleanup_interval": 600
    }

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
import uuid

# ---------------------------------------------------------------------------
# Bus compaction — optional integration from prototype.agent_comm.compactor
# ---------------------------------------------------------------------------

try:
    from prototype.agent_comm.compactor import BusCompactor, OffsetStore
    _HAS_COMPACTOR = True
except ImportError:
    _HAS_COMPACTOR = False

try:
    from storage.coordination.escalation_timers import EscalationTimerManager
    _HAS_ESCALATION = True
except ImportError:
    try:
        # Fallback: relative import for when run as part of the package
        from .escalation_timers import EscalationTimerManager
        _HAS_ESCALATION = True
    except ImportError:
        _HAS_ESCALATION = False

try:
    from storage.coordination.db_partition import PartitionedStore
    _HAS_PARTITIONED = True
except ImportError:
    try:
        from .db_partition import PartitionedStore
        _HAS_PARTITIONED = True
    except ImportError:
        _HAS_PARTITIONED = False

try:
    from storage.coordination.coordinator_hub import CoordinatorDashboard
    _HAS_COORDINATOR_HUB = True
except ImportError:
    try:
        from .coordinator_hub import CoordinatorDashboard
        _HAS_COORDINATOR_HUB = True
    except ImportError:
        _HAS_COORDINATOR_HUB = False

try:
    from storage.coordination.help_protocol import HelpProtocol
    _HAS_HELP_PROTOCOL = True
except ImportError:
    try:
        from .help_protocol import HelpProtocol
        _HAS_HELP_PROTOCOL = True
    except ImportError:
        _HAS_HELP_PROTOCOL = False

try:
    from storage.coordination.direct_channels import DirectChannels
    _HAS_DIRECT_CHANNELS = True
except ImportError:
    try:
        from .direct_channels import DirectChannels
        _HAS_DIRECT_CHANNELS = True
    except ImportError:
        _HAS_DIRECT_CHANNELS = False

try:
    from storage.coordination.work_stealing import WorkStealing, PipelineManager, Scratchpad
    _HAS_WORK_STEALING = True
except ImportError:
    try:
        from .work_stealing import WorkStealing, PipelineManager, Scratchpad
        _HAS_WORK_STEALING = True
    except ImportError:
        _HAS_WORK_STEALING = False

# ---------------------------------------------------------------------------
# Paths — resolve relative to this script's location
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BUS_DIR = os.path.join(_SCRIPT_DIR, "bus")
_DB_DIR = os.path.join(_SCRIPT_DIR, "db")
_DB_PATH = os.path.join(_DB_DIR, "state.db")
_TEAMS_DIR = os.path.join(_SCRIPT_DIR, "teams")
_LOG_FILE = os.path.join(_SCRIPT_DIR, "runner.log")

# ---------------------------------------------------------------------------
# Logging — configured lazily via _setup_logging() so that importing this
# module as a library does not trigger basicConfig at import time
# (BUG-INFRA-010 / NEW-COORD-006).
# ---------------------------------------------------------------------------

log = logging.getLogger("multi_team_runner")

_logging_configured = False


def _setup_logging() -> None:
    """Configure logging once, guarded by a module-level flag."""
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(_LOG_FILE, mode="a"),
        ],
    )

# ---------------------------------------------------------------------------
# Valid message types (matches bus.py)
# ---------------------------------------------------------------------------

VALID_MSG_TYPES = frozenset({
    "info", "blocker", "phase-signal", "heartbeat", "request", "response",
})

# ---------------------------------------------------------------------------
# SQLite schema — extends the bus_cli.py schema
# ---------------------------------------------------------------------------

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

CREATE TABLE IF NOT EXISTS team_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS think_tank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team TEXT NOT NULL,
    proposal TEXT NOT NULL,
    votes_for INTEGER DEFAULT 0,
    votes_against INTEGER DEFAULT 0,
    status TEXT DEFAULT 'proposed',
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runner_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Bus writer (lightweight, matches bus.py / bus_cli.py patterns)
# ---------------------------------------------------------------------------

def _bus_write(channel: str, agent_id: str, team: str, msg_type: str,
               body: dict, ttl: int = 3600) -> str:
    """Append a message to a bus channel file.  Returns the message ID."""
    os.makedirs(_BUS_DIR, exist_ok=True)
    msg_id = str(uuid.uuid4())
    msg = {
        "id": msg_id,
        "type": msg_type,
        "channel": channel,
        "team": team,
        "agent_id": agent_id,
        "ts": time.time(),
        "ttl": ttl,
        "body": body,
    }
    raw = json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(raw) > 4096:
        raise ValueError(f"Message too large: {len(raw)} bytes (limit 4096)")
    safe = channel.replace("/", "_").replace("..", "_")
    filepath = os.path.join(_BUS_DIR, f"{safe}.jsonl")
    fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    return msg_id


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

# NOTE (NEW-COORD-004): The shared connection and its lock are encapsulated
# behind _get_connection() / _close_connection().  The module-level variables
# are an implementation detail; external code should only call those two
# functions.
_shared_conn: sqlite3.Connection | None = None
_shared_conn_lock = threading.Lock()


def _get_connection(busy_timeout_ms: int = 30000) -> sqlite3.Connection:
    """Return a lazily-created persistent SQLite connection (thread-safe).

    The connection is shared across all helper functions to avoid the
    overhead of opening and closing a new connection on every call.
    WAL mode and busy_timeout are configured once on creation.

    Callers must not cache the returned connection object; always call
    this function to ensure the connection is still valid.
    """
    global _shared_conn
    with _shared_conn_lock:
        if _shared_conn is None:
            os.makedirs(_DB_DIR, exist_ok=True)
            _shared_conn = sqlite3.connect(
                _DB_PATH,
                timeout=busy_timeout_ms / 1000,
                check_same_thread=False,
            )
            _shared_conn.row_factory = sqlite3.Row
            _shared_conn.execute("PRAGMA journal_mode=WAL")
            _shared_conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        return _shared_conn


def _close_connection() -> None:
    """Close the shared connection if open.  Safe to call multiple times."""
    global _shared_conn
    with _shared_conn_lock:
        if _shared_conn is not None:
            try:
                _shared_conn.close()
            except Exception:
                pass
            _shared_conn = None


def _get_db(busy_timeout_ms: int = 30000) -> sqlite3.Connection:
    """Return the shared persistent connection.

    Kept for backward-compatibility with any external callers; now
    delegates to _get_connection().
    """
    return _get_connection(busy_timeout_ms)


def _init_db() -> None:
    """Initialise the database schema."""
    conn = _get_connection()
    with _shared_conn_lock:
        conn.executescript(_SCHEMA)
        conn.commit()
    log.info("Database initialised at %s", _DB_PATH)


def _register_agent(agent_id: str, team: str, role: str,
                    pid: int | None = None) -> None:
    """Register or re-register an agent in the agents table."""
    now = time.time()
    pid = pid or os.getpid()
    conn = _get_connection()
    with _shared_conn_lock:
        conn.execute("""
            INSERT INTO agents (agent_id, team, role, pid, status,
                                last_heartbeat, registered_at)
            VALUES (?, ?, ?, ?, 'alive', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                team=excluded.team, role=excluded.role, pid=excluded.pid,
                status='alive', last_heartbeat=excluded.last_heartbeat
        """, (agent_id, team, role, pid, now, now))
        conn.commit()


def _heartbeat(agent_id: str) -> None:
    """Update the heartbeat timestamp for an agent."""
    conn = _get_connection()
    with _shared_conn_lock:
        conn.execute(
            "UPDATE agents SET last_heartbeat = ?, status = 'alive' "
            "WHERE agent_id = ?",
            (time.time(), agent_id),
        )
        conn.commit()


def _get_dead_agents(timeout: float = 120.0) -> list[dict]:
    """Return agents whose heartbeat is older than *timeout* seconds."""
    cutoff = time.time() - timeout
    conn = _get_connection()
    with _shared_conn_lock:
        rows = conn.execute(
            "SELECT * FROM agents WHERE last_heartbeat < ? AND status = 'alive'",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def _set_runner_state(key: str, value: str) -> None:
    """Persist a key/value pair in the runner_state table."""
    conn = _get_connection()
    with _shared_conn_lock:
        conn.execute("""
            INSERT INTO runner_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value, updated_at=excluded.updated_at
        """, (key, value, time.time()))
        conn.commit()


def _get_runner_state(key: str) -> str | None:
    """Read a value from the runner_state table."""
    conn = _get_connection()
    with _shared_conn_lock:
        try:
            row = conn.execute(
                "SELECT value FROM runner_state WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(config_path: str | None, num_teams: int,
                 agents_per_team: int) -> dict:
    """Build a normalised config dict from either a JSON file or CLI args."""
    if config_path:
        with open(config_path, "r") as f:
            cfg = json.load(f)
        # Validate
        if "teams" not in cfg or not isinstance(cfg["teams"], list):
            raise ValueError("Config must have a 'teams' list")
        for t in cfg["teams"]:
            if "name" not in t:
                raise ValueError("Each team must have a 'name'")
            if "agents" not in t or not isinstance(t["agents"], list):
                raise ValueError(f"Team {t['name']} must have an 'agents' list")
            for a in t["agents"]:
                if "agent_id" not in a or "role" not in a:
                    raise ValueError(
                        f"Each agent in team {t['name']} must have "
                        "'agent_id' and 'role'"
                    )
        return cfg

    # Generate from CLI counts
    teams = []
    for i in range(1, num_teams + 1):
        team_name = f"team-{i:02d}"
        agents = [
            {"agent_id": f"{team_name}-lead", "role": "coordinator"},
        ]
        for j in range(1, agents_per_team):
            agents.append(
                {"agent_id": f"{team_name}-worker-{j}", "role": "worker"},
            )
        teams.append({"name": team_name, "agents": agents})
    return {
        "teams": teams,
        "heartbeat_interval": 30,
        "dead_agent_timeout": 120,
        "cleanup_interval": 600,
    }


# ---------------------------------------------------------------------------
# MultiTeamRunner
# ---------------------------------------------------------------------------

class MultiTeamRunner:
    """Manages the lifecycle of N teams with M agents each.

    Responsibilities:
    - Initialise coordination directory (bus/ + db/)
    - Register all agents in SQLite
    - Run a coordinator heartbeat & health-check loop
    - Provide clean shutdown via SIGINT / SIGTERM
    - Write operational logs to the JSONL bus
    """

    def __init__(self, config: dict, partitioned: bool = True) -> None:
        self.config = config
        self.teams: list[dict] = config["teams"]
        self.heartbeat_interval: float = config.get("heartbeat_interval", 30.0)
        self.dead_agent_timeout: float = config.get("dead_agent_timeout", 120.0)
        self.cleanup_interval: float = config.get("cleanup_interval", 600.0)
        self.compaction_interval: float = config.get("compaction_interval", 0.0)
        self.enable_escalation: bool = config.get("enable_escalation", True)
        self.enable_partitioned: bool = partitioned and _HAS_PARTITIONED

        self._stop_event = threading.Event()
        self._hb_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._compaction_thread: threading.Thread | None = None
        self._compactor: BusCompactor | None = None
        self._offset_store: OffsetStore | None = None
        self._escalation_manager: EscalationTimerManager | None = None if _HAS_ESCALATION else None
        self._partitioned_store: PartitionedStore | None = None if _HAS_PARTITIONED else None
        self._coordinator_dashboard: CoordinatorDashboard | None = None if _HAS_COORDINATOR_HUB else None
        self._help_protocol: HelpProtocol | None = None if _HAS_HELP_PROTOCOL else None
        self._direct_channels: DirectChannels | None = None if _HAS_DIRECT_CHANNELS else None
        self._work_stealing: WorkStealing | None = None if _HAS_WORK_STEALING else None
        self._pipeline_manager: PipelineManager | None = None if _HAS_WORK_STEALING else None
        self._scratchpad: Scratchpad | None = None if _HAS_WORK_STEALING else None
        self._started_at: float | None = None
        self._phase = "init"
        # Track which agents already have active escalation timers to avoid
        # creating duplicate timers on every health-check cycle.
        self._agent_blocker_timers: dict[str, str] = {}   # agent_id -> timer_id
        self._agent_idle_timers: dict[str, str] = {}       # agent_id -> timer_id

    # -- initialisation -----------------------------------------------------

    def init_environment(self) -> None:
        """Create directories and initialise the database schema."""
        os.makedirs(_BUS_DIR, exist_ok=True)
        os.makedirs(_DB_DIR, exist_ok=True)
        os.makedirs(_TEAMS_DIR, exist_ok=True)
        _init_db()

        # Initialise bus compactor if available
        if _HAS_COMPACTOR:
            offsets_db = os.path.join(_DB_DIR, "bus_offsets.db")
            self._offset_store = OffsetStore(offsets_db)
            self._compactor = BusCompactor(_BUS_DIR, self._offset_store)
            log.info("Bus compactor initialised (offsets db: %s)", offsets_db)

        # Initialise escalation timer manager if available and enabled
        if _HAS_ESCALATION and self.enable_escalation:
            escalation_db = os.path.join(_DB_DIR, "escalation.db")
            self._escalation_manager = EscalationTimerManager(
                db_path=escalation_db,
                bus_dir=_BUS_DIR,
            )
            self._escalation_manager.start_monitor_thread(check_interval=10.0)
            log.info(
                "Escalation timer manager initialised (db: %s)",
                escalation_db,
            )

        log.info("Coordination environment initialised")
        _bus_write("global", "runner", "system", "info", {
            "event": "environment-initialised",
            "bus_dir": _BUS_DIR,
            "db_path": _DB_PATH,
        })

    def register_all_agents(self) -> int:
        """Register every agent from every team.  Returns the total count."""
        total = 0
        for team in self.teams:
            team_name = team["name"]
            for agent in team["agents"]:
                _register_agent(
                    agent["agent_id"], team_name, agent["role"],
                )
                total += 1
                log.info(
                    "Registered %s (%s/%s)",
                    agent["agent_id"], team_name, agent["role"],
                )
        _bus_write("global", "runner", "system", "info", {
            "event": "agents-registered",
            "count": total,
            "teams": [t["name"] for t in self.teams],
        })
        return total

    # -- coordinator loop ---------------------------------------------------

    def start(self) -> None:
        """Start the coordinator heartbeat and health-check loops."""
        self._started_at = time.time()
        self._phase = "running"
        self._stop_event.clear()

        # Register the runner itself as a coordinator agent
        _register_agent("runner", "system", "coordinator")

        # Heartbeat + health-check thread
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="runner-hb",
            daemon=True,
        )
        self._hb_thread.start()

        # Periodic cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="runner-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

        # Periodic bus compaction thread (if compactor available and interval > 0)
        if self._compactor and self.compaction_interval > 0:
            self._compaction_thread = threading.Thread(
                target=self._compaction_loop,
                name="runner-compaction",
                daemon=True,
            )
            self._compaction_thread.start()
            log.info(
                "Periodic bus compaction enabled (interval=%.0fs)",
                self.compaction_interval,
            )

        _bus_write("global", "runner", "system", "info", {
            "event": "runner-started",
            "pid": os.getpid(),
            "teams": len(self.teams),
            "phase": self._phase,
        })
        _set_runner_state("status", "running")
        _set_runner_state("pid", str(os.getpid()))
        _set_runner_state("started_at", str(self._started_at))
        _set_runner_state("config", json.dumps(self.config))

        log.info(
            "Multi-team runner started (pid=%d, teams=%d)",
            os.getpid(), len(self.teams),
        )

    def stop(self) -> None:
        """Graceful shutdown: stop threads, compact bus, broadcast shutdown message."""
        log.info("Shutting down multi-team runner...")
        self._phase = "shutdown"
        self._stop_event.set()

        if self._hb_thread is not None:
            self._hb_thread.join(timeout=self.heartbeat_interval + 2)
        if self._cleanup_thread is not None:
            self._cleanup_thread.join(timeout=5)
        if self._compaction_thread is not None:
            self._compaction_thread.join(timeout=5)

        # Run final bus compaction during clean shutdown
        if self._compactor is not None:
            log.info("Running final bus compaction before shutdown...")
            try:
                results = self._compactor.compact_all()
                total_before = sum(r.get("messages_before", 0) for r in results)
                total_after = sum(r.get("messages_after", 0) for r in results)
                log.info(
                    "Shutdown compaction complete: %d channels, %d -> %d messages",
                    len(results), total_before, total_after,
                )
            except Exception:
                log.exception("Shutdown bus compaction failed")

        # Close the offset store
        if self._offset_store is not None:
            try:
                self._offset_store.close()
            except Exception:
                pass

        _bus_write("global", "runner", "system", "info", {
            "event": "runner-stopped",
            "uptime": time.time() - (self._started_at or time.time()),
        })
        _set_runner_state("status", "stopped")
        _close_connection()
        log.info("Multi-team runner stopped")

    def _heartbeat_loop(self) -> None:
        """Periodically heartbeat and check agent health."""
        while not self._stop_event.is_set():
            try:
                _heartbeat("runner")
            except Exception:
                log.exception("Runner heartbeat failed")

            try:
                self._check_agent_health()
            except Exception:
                log.exception("Agent health check failed")

            self._stop_event.wait(self.heartbeat_interval)

    def _check_agent_health(self) -> None:
        """Detect dead agents and log warnings to the bus."""
        dead = _get_dead_agents(timeout=self.dead_agent_timeout)
        for agent in dead:
            elapsed = time.time() - agent["last_heartbeat"]
            log.warning(
                "Agent %s appears dead (last heartbeat %.1fs ago)",
                agent["agent_id"], elapsed,
            )
            _bus_write("global", "runner", "system", "info", {
                "event": "agent-dead",
                "agent_id": agent["agent_id"],
                "team": agent["team"],
                "last_heartbeat": agent["last_heartbeat"],
                "elapsed": round(elapsed, 1),
            })

    def _cleanup_loop(self) -> None:
        """Periodically clean up expired data."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.cleanup_interval)
            if self._stop_event.is_set():
                break
            try:
                self._run_cleanup()
            except Exception:
                log.exception("Cleanup failed")

    def _run_cleanup(self) -> None:
        """Purge expired rate_limits and messages from SQLite."""
        now = time.time()
        conn = _get_connection()
        with _shared_conn_lock:
            cur1 = conn.execute(
                "DELETE FROM rate_limits WHERE called_at < ?",
                (now - 3600,),
            )
            cur2 = conn.execute(
                "DELETE FROM messages WHERE expires_at IS NOT NULL "
                "AND expires_at < ?",
                (now,),
            )
            total = cur1.rowcount + cur2.rowcount
            conn.commit()
        if total:
            log.info("Cleanup: removed %d expired rows", total)

    def _compaction_loop(self) -> None:
        """Periodically compact JSONL bus files to reclaim space."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.compaction_interval)
            if self._stop_event.is_set():
                break
            if self._compactor is not None:
                try:
                    results = self._compactor.compact_all()
                    total_before = sum(r.get("messages_before", 0) for r in results)
                    total_after = sum(r.get("messages_after", 0) for r in results)
                    if total_before > 0:
                        log.info(
                            "Periodic compaction: %d channels, %d -> %d messages",
                            len(results), total_before, total_after,
                        )
                except Exception:
                    log.exception("Periodic bus compaction failed")

    # -- phase management ---------------------------------------------------

    def advance_phase(self, new_phase: str, data: dict | None = None) -> None:
        """Signal a phase transition to all agents."""
        old_phase = self._phase
        self._phase = new_phase

        conn = _get_connection()
        with _shared_conn_lock:
            conn.execute(
                "INSERT INTO phase_signals (phase, signal_type, agent_id, ts, data) "
                "VALUES (?, 'transition', 'runner', ?, ?)",
                (new_phase, time.time(), json.dumps(data) if data else None),
            )
            conn.commit()

        body = {"event": "phase-transition", "from": old_phase, "to": new_phase}
        if data:
            body["data"] = data
        _bus_write("global", "runner", "system", "phase-signal", body)
        _set_runner_state("phase", new_phase)
        log.info("Phase advanced: %s -> %s", old_phase, new_phase)

    # -- status -------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current runner state for diagnostics."""
        uptime = time.time() - self._started_at if self._started_at else 0.0
        conn = _get_connection()
        with _shared_conn_lock:
            try:
                agents = [
                    dict(r) for r in conn.execute(
                        "SELECT agent_id, team, role, status, last_heartbeat "
                        "FROM agents"
                    ).fetchall()
                ]
                findings_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM team_findings"
                ).fetchone()["cnt"]
            except sqlite3.OperationalError:
                agents = []
                findings_count = 0

        return {
            "phase": self._phase,
            "uptime": round(uptime, 2),
            "started_at": self._started_at,
            "pid": os.getpid(),
            "teams": len(self.teams),
            "agents": agents,
            "total_findings": findings_count,
        }


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

# NOTE (NEW-COORD-005): Only one MultiTeamRunner instance per process is
# supported.  This global is set in main() and read by the signal handler
# so that SIGINT/SIGTERM can trigger a clean shutdown.  Do not create
# multiple runners in the same process — the signal handler will only
# reference the most recently assigned instance.
_runner_instance: MultiTeamRunner | None = None


def _signal_handler(signum: int, frame) -> None:
    """Handle SIGINT/SIGTERM for clean shutdown."""
    sig_name = signal.Signals(signum).name
    log.info("Received %s — initiating shutdown", sig_name)
    if _runner_instance is not None:
        _runner_instance.stop()
    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_status() -> None:
    """Print current runner status from the database."""
    status = _get_runner_state("status")
    if status is None:
        print("No runner state found.  Has the runner been started?")
        return

    conn = _get_connection()
    with _shared_conn_lock:
        try:
            agents = [
                dict(r) for r in conn.execute(
                    "SELECT agent_id, team, role, status, last_heartbeat "
                    "FROM agents ORDER BY team, role"
                ).fetchall()
            ]
            findings = conn.execute(
                "SELECT COUNT(*) as cnt FROM team_findings"
            ).fetchone()["cnt"]
        except sqlite3.OperationalError:
            agents = []
            findings = 0

    info = {
        "runner_status": status,
        "runner_pid": _get_runner_state("pid"),
        "started_at": _get_runner_state("started_at"),
        "phase": _get_runner_state("phase"),
        "agents": agents,
        "total_findings": findings,
    }
    print(json.dumps(info, indent=2, default=str))


def _request_shutdown() -> None:
    """Send a shutdown signal to the running runner via the bus."""
    pid_str = _get_runner_state("pid")
    if pid_str is None:
        print("No runner PID found.")
        return
    pid = int(pid_str)
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to runner (pid={pid})")
    except ProcessLookupError:
        print(f"Runner process (pid={pid}) is not running")
        _set_runner_state("status", "stopped")
    except PermissionError:
        print(f"Permission denied sending signal to pid={pid}")


def main() -> None:
    """CLI entry point."""
    global _runner_instance
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Multi-team runner for Proto A coordination",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--config", type=str, metavar="PATH",
        help="Path to team config JSON file",
    )
    group.add_argument(
        "--status", action="store_true",
        help="Show current runner status",
    )
    group.add_argument(
        "--shutdown", action="store_true",
        help="Request runner shutdown",
    )
    parser.add_argument(
        "--teams", type=int, default=3,
        help="Number of teams to create (when not using --config)",
    )
    parser.add_argument(
        "--agents-per-team", type=int, default=2,
        help="Number of agents per team (when not using --config)",
    )
    parser.add_argument(
        "--foreground", action="store_true", default=True,
        help="Run in foreground (block until shutdown signal).  This is the "
             "default; pass --no-foreground to detach (requires an external "
             "process manager to keep the process alive).",
    )
    parser.add_argument(
        "--no-foreground", action="store_false", dest="foreground",
        help="Run in non-foreground mode.  The caller must keep the process "
             "alive (e.g. by importing this module as a library).",
    )
    parser.add_argument(
        "--compaction-interval", type=float, default=0,
        help="Interval in seconds for periodic bus compaction (0 = disabled, "
             "default: 0).  Requires prototype.agent_comm.compactor.",
    )

    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    if args.shutdown:
        _request_shutdown()
        return

    # Build config
    config = _load_config(
        args.config, args.teams, args.agents_per_team,
    )
    # Apply CLI compaction interval override
    if args.compaction_interval > 0:
        config["compaction_interval"] = args.compaction_interval

    # Create and start runner
    runner = MultiTeamRunner(config)
    _runner_instance = runner

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    runner.init_environment()
    count = runner.register_all_agents()
    runner.start()

    print(f"Multi-team runner started: {len(config['teams'])} teams, "
          f"{count} agents registered")
    print(f"  Bus directory: {_BUS_DIR}")
    print(f"  Database: {_DB_PATH}")
    print(f"  PID: {os.getpid()}")

    if args.foreground:
        print("Running in foreground.  Press Ctrl+C to stop.")
        try:
            while not runner._stop_event.is_set():
                runner._stop_event.wait(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            runner.stop()
    else:
        print("Runner is active.  Use --status to check, --shutdown to stop.")
        # In non-foreground mode, the daemon threads keep running until
        # the process exits.  The caller is expected to keep the process
        # alive (e.g. by using this module as a library).


if __name__ == "__main__":
    main()
