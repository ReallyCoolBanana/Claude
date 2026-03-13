#!/usr/bin/env python3
"""SDK Launcher -- spawn and manage Claude Code CLI agents as subprocesses.

Builds on the existing coordination infrastructure (multi_team_runner.py,
coordinator_hub.py, direct_channels.py) to provide a production-ready
multi-agent launcher that:

- Spawns Claude Code CLI agents as managed subprocesses
- Monitors agent health via heartbeats and process polling
- Integrates with the JSONL bus for inter-agent messaging
- Writes agent status to the coordinator hub's SQLite DB
- Supports graceful shutdown via SIGTERM/SIGINT
- Provides agent restart on crash

Usage:
    python sdk_launcher.py --config teams.json
    python sdk_launcher.py --teams research --agents 3 --prompt "You are a researcher"
    python sdk_launcher.py --status
    python sdk_launcher.py --shutdown

Config JSON format:
    {
        "teams": [
            {
                "team_name": "research",
                "num_agents": 3,
                "system_prompt": "You are a research agent...",
                "tools_allowed": ["Read", "Grep", "Glob", "WebSearch"],
                "work_queue": "research-tasks"
            }
        ],
        "heartbeat_interval": 15,
        "health_check_interval": 30,
        "max_restarts": 3,
        "restart_backoff": 5.0
    }

Uses only the Python standard library.  Python 3.10+.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BUS_DIR = os.path.join(_SCRIPT_DIR, "bus")
_DB_DIR = os.path.join(_SCRIPT_DIR, "db")
_DB_PATH = os.path.join(_DB_DIR, "state.db")
_LOG_DIR = os.path.join(_SCRIPT_DIR, "logs")
_LOG_FILE = os.path.join(_SCRIPT_DIR, "sdk_launcher.log")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("sdk_launcher")

_logging_configured = False


def _setup_logging() -> None:
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True
    os.makedirs(os.path.dirname(_LOG_FILE) or ".", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(_LOG_FILE, mode="a"),
        ],
    )


# ---------------------------------------------------------------------------
# Bus writer (matches multi_team_runner.py / bus_cli.py patterns)
# ---------------------------------------------------------------------------

def _bus_write(channel: str, agent_id: str, team: str, msg_type: str,
               body: dict, ttl: int = 3600) -> str:
    """Append a message to a JSONL bus channel.  Returns the message ID."""
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
# SQLite helpers -- reuses the schema from multi_team_runner.py
# ---------------------------------------------------------------------------

_EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS sdk_agents (
    agent_id TEXT PRIMARY KEY,
    team TEXT NOT NULL,
    role TEXT NOT NULL,
    pid INTEGER,
    status TEXT DEFAULT 'starting',
    exit_code INTEGER,
    restart_count INTEGER DEFAULT 0,
    last_heartbeat REAL NOT NULL,
    started_at REAL NOT NULL,
    stopped_at REAL,
    error_message TEXT
);
"""


def _get_db() -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection for the launcher."""
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_EXTRA_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# TeamConfig
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class TeamConfig:
    """Configuration for a single team of agents."""
    team_name: str
    num_agents: int = 2
    system_prompt: str = ""
    tools_allowed: list[str] = dataclasses.field(default_factory=list)
    work_queue: str = ""
    max_restarts: int = 3
    restart_backoff: float = 5.0
    working_directory: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> TeamConfig:
        return cls(
            team_name=d["team_name"],
            num_agents=d.get("num_agents", 2),
            system_prompt=d.get("system_prompt", ""),
            tools_allowed=d.get("tools_allowed", []),
            work_queue=d.get("work_queue", ""),
            max_restarts=d.get("max_restarts", 3),
            restart_backoff=d.get("restart_backoff", 5.0),
            working_directory=d.get("working_directory", ""),
        )

    def agent_id(self, index: int) -> str:
        """Generate a deterministic agent ID for the given index."""
        if index == 0:
            return f"{self.team_name}-lead"
        return f"{self.team_name}-worker-{index}"

    def agent_role(self, index: int) -> str:
        return "coordinator" if index == 0 else "worker"


# ---------------------------------------------------------------------------
# AgentProcess
# ---------------------------------------------------------------------------

class AgentProcess:
    """Wraps a spawned Claude Code CLI agent with health monitoring.

    Each AgentProcess manages one subprocess, tracks its health via process
    polling (and optional heartbeat checks from the bus), and supports
    restart on crash.
    """

    def __init__(
        self,
        agent_id: str,
        team: str,
        role: str,
        config: TeamConfig,
        db: sqlite3.Connection,
    ) -> None:
        self.agent_id = agent_id
        self.team = team
        self.role = role
        self.config = config
        self._db = db

        self._process: Optional[asyncio.subprocess.Process] = None
        self._pid: Optional[int] = None
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._exit_code: Optional[int] = None
        self._restart_count: int = 0
        self._status: str = "pending"
        self._log_path: str = os.path.join(
            _LOG_DIR, f"{agent_id}.log"
        )

    @property
    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.returncode is None

    @property
    def status(self) -> str:
        return self._status

    @property
    def pid(self) -> Optional[int]:
        return self._pid

    async def start(self) -> None:
        """Spawn the Claude Code CLI agent as a subprocess."""
        os.makedirs(_LOG_DIR, exist_ok=True)

        cmd = self._build_command()
        log.info(
            "Starting agent %s (team=%s, role=%s): %s",
            self.agent_id, self.team, self.role, " ".join(cmd),
        )

        log_fh = open(self._log_path, "a")
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=self.config.working_directory or None,
            )
        except FileNotFoundError:
            log_fh.close()
            self._status = "error"
            self._update_db(error_message="claude CLI not found in PATH")
            _bus_write("global", self.agent_id, self.team, "info", {
                "event": "agent-start-failed",
                "agent_id": self.agent_id,
                "error": "claude CLI not found in PATH",
            })
            raise RuntimeError(
                f"Could not start agent {self.agent_id}: 'claude' not found in PATH"
            )

        self._pid = self._process.pid
        self._started_at = time.time()
        self._status = "running"

        self._update_db()

        _bus_write("global", self.agent_id, self.team, "info", {
            "event": "agent-started",
            "agent_id": self.agent_id,
            "team": self.team,
            "role": self.role,
            "pid": self._pid,
        })
        log.info("Agent %s started (pid=%d)", self.agent_id, self._pid)

    def _build_command(self) -> list[str]:
        """Build the claude CLI command for this agent."""
        cmd = ["claude", "--print"]

        # Add system prompt with agent identity and bus integration context
        system_parts = []
        system_parts.append(
            f"You are agent '{self.agent_id}' on team '{self.team}' "
            f"with role '{self.role}'."
        )
        system_parts.append(
            f"Coordination bus directory: {_BUS_DIR}"
        )
        system_parts.append(
            f"Coordination database: {_DB_PATH}"
        )

        if self.config.work_queue:
            system_parts.append(
                f"Your work queue: {self.config.work_queue}"
            )

        if self.config.system_prompt:
            system_parts.append(self.config.system_prompt)

        full_prompt = "\n\n".join(system_parts)
        cmd.extend(["--system-prompt", full_prompt])

        # Allowlisted tools
        if self.config.tools_allowed:
            for tool in self.config.tools_allowed:
                cmd.extend(["--allowedTools", tool])

        # The prompt itself: instruct agent to pick up work
        work_instruction = (
            f"You are agent {self.agent_id} on team {self.team}. "
            f"Check the coordination bus at {_BUS_DIR} and database at "
            f"{_DB_PATH} for your work assignments. Begin working."
        )
        cmd.append(work_instruction)

        return cmd

    async def stop(self, timeout: float = 10.0) -> None:
        """Gracefully stop the agent subprocess."""
        if self._process is None or self._process.returncode is not None:
            self._status = "stopped"
            self._stopped_at = time.time()
            self._update_db()
            return

        pid = self._pid
        log.info("Stopping agent %s (pid=%s)", self.agent_id, pid)
        self._status = "stopping"

        # Try SIGTERM first
        try:
            self._process.terminate()
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(self._process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(
                "Agent %s did not exit after %.1fs, sending SIGKILL",
                self.agent_id, timeout,
            )
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                log.error("Agent %s could not be killed", self.agent_id)

        self._exit_code = self._process.returncode
        self._stopped_at = time.time()
        self._status = "stopped"
        self._update_db()

        _bus_write("global", self.agent_id, self.team, "info", {
            "event": "agent-stopped",
            "agent_id": self.agent_id,
            "pid": pid,
            "exit_code": self._exit_code,
        })
        log.info(
            "Agent %s stopped (exit_code=%s)", self.agent_id, self._exit_code,
        )

    async def restart(self) -> bool:
        """Restart a crashed agent.  Returns True if restart succeeded."""
        if self._restart_count >= self.config.max_restarts:
            log.error(
                "Agent %s exceeded max restarts (%d/%d)",
                self.agent_id, self._restart_count, self.config.max_restarts,
            )
            self._status = "failed"
            self._update_db(
                error_message=f"Exceeded max restarts ({self.config.max_restarts})"
            )
            _bus_write("global", self.agent_id, self.team, "info", {
                "event": "agent-restart-exhausted",
                "agent_id": self.agent_id,
                "restart_count": self._restart_count,
            })
            return False

        self._restart_count += 1
        backoff = self.config.restart_backoff * self._restart_count
        log.info(
            "Restarting agent %s (attempt %d/%d, backoff=%.1fs)",
            self.agent_id, self._restart_count, self.config.max_restarts,
            backoff,
        )

        _bus_write("global", self.agent_id, self.team, "info", {
            "event": "agent-restarting",
            "agent_id": self.agent_id,
            "restart_count": self._restart_count,
        })

        await asyncio.sleep(backoff)
        await self.start()
        return True

    def check_health(self) -> dict:
        """Check the health of this agent process.

        Returns a dict with status info.  Side-effect: updates status to
        'crashed' if the process has exited unexpectedly.
        """
        info = {
            "agent_id": self.agent_id,
            "team": self.team,
            "role": self.role,
            "status": self._status,
            "pid": self._pid,
            "restart_count": self._restart_count,
            "uptime": (
                time.time() - self._started_at
                if self._started_at else 0.0
            ),
        }

        if self._process is not None and self._process.returncode is not None:
            if self._status == "running":
                self._exit_code = self._process.returncode
                self._status = "crashed"
                self._stopped_at = time.time()
                self._update_db(
                    error_message=f"Process exited with code {self._exit_code}"
                )
                _bus_write("global", self.agent_id, self.team, "info", {
                    "event": "agent-crashed",
                    "agent_id": self.agent_id,
                    "exit_code": self._exit_code,
                })
                log.warning(
                    "Agent %s crashed (exit_code=%s)",
                    self.agent_id, self._exit_code,
                )
            info["status"] = self._status
            info["exit_code"] = self._exit_code

        return info

    def _update_db(self, error_message: Optional[str] = None) -> None:
        """Write current agent state to the SQLite database."""
        now = time.time()
        try:
            self._db.execute(
                """INSERT INTO sdk_agents
                   (agent_id, team, role, pid, status, exit_code,
                    restart_count, last_heartbeat, started_at, stopped_at,
                    error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET
                       team=excluded.team, role=excluded.role,
                       pid=excluded.pid, status=excluded.status,
                       exit_code=excluded.exit_code,
                       restart_count=excluded.restart_count,
                       last_heartbeat=excluded.last_heartbeat,
                       started_at=excluded.started_at,
                       stopped_at=excluded.stopped_at,
                       error_message=excluded.error_message
                """,
                (
                    self.agent_id, self.team, self.role,
                    self._pid, self._status, self._exit_code,
                    self._restart_count, now,
                    self._started_at or now,
                    self._stopped_at,
                    error_message,
                ),
            )
            self._db.commit()
        except sqlite3.OperationalError:
            log.warning(
                "Failed to update DB for agent %s", self.agent_id,
                exc_info=True,
            )

    # Also register in the multi_team_runner agents table for compatibility
    def _register_in_runner_db(self) -> None:
        """Register this agent in the existing agents table (compatibility)."""
        now = time.time()
        try:
            self._db.execute(
                """INSERT INTO agents
                   (agent_id, team, role, pid, status, last_heartbeat,
                    registered_at)
                   VALUES (?, ?, ?, ?, 'alive', ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET
                       team=excluded.team, role=excluded.role,
                       pid=excluded.pid, status='alive',
                       last_heartbeat=excluded.last_heartbeat
                """,
                (self.agent_id, self.team, self.role, self._pid, now, now),
            )
            self._db.commit()
        except sqlite3.OperationalError:
            # agents table may not exist yet if multi_team_runner hasn't run
            pass


# ---------------------------------------------------------------------------
# SDKLauncher
# ---------------------------------------------------------------------------

class SDKLauncher:
    """Manages the full lifecycle of multiple teams of Claude Code agents.

    Responsibilities:
    - Launch teams of agents as subprocesses
    - Monitor agent health with periodic checks
    - Auto-restart crashed agents (up to max_restarts)
    - Integrate with the JSONL bus for coordination messages
    - Write status to SQLite for dashboard/monitoring tools
    - Clean shutdown on SIGTERM/SIGINT
    """

    def __init__(
        self,
        teams: list[TeamConfig],
        heartbeat_interval: float = 15.0,
        health_check_interval: float = 30.0,
        auto_restart: bool = True,
    ) -> None:
        self.teams = teams
        self.heartbeat_interval = heartbeat_interval
        self.health_check_interval = health_check_interval
        self.auto_restart = auto_restart

        self._agents: dict[str, AgentProcess] = {}
        self._team_agents: dict[str, list[str]] = {}
        self._db: Optional[sqlite3.Connection] = None
        self._shutdown_event = asyncio.Event()
        self._started_at: Optional[float] = None
        self._health_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    def _init_db(self) -> sqlite3.Connection:
        """Initialize and return the database connection."""
        if self._db is None:
            self._db = _get_db()
        return self._db

    # -- Team launching -----------------------------------------------------

    async def launch_team(self, config: TeamConfig) -> list[str]:
        """Launch all agents for a single team.

        Returns a list of agent IDs that were successfully started.
        """
        db = self._init_db()
        started: list[str] = []
        team_agent_ids: list[str] = []

        for i in range(config.num_agents):
            agent_id = config.agent_id(i)
            role = config.agent_role(i)

            agent = AgentProcess(
                agent_id=agent_id,
                team=config.team_name,
                role=role,
                config=config,
                db=db,
            )
            self._agents[agent_id] = agent
            team_agent_ids.append(agent_id)

            try:
                await agent.start()
                agent._register_in_runner_db()
                started.append(agent_id)
            except RuntimeError as e:
                log.error("Failed to start agent %s: %s", agent_id, e)

        self._team_agents[config.team_name] = team_agent_ids

        _bus_write("global", "sdk-launcher", "system", "info", {
            "event": "team-launched",
            "team": config.team_name,
            "agents_requested": config.num_agents,
            "agents_started": len(started),
            "agent_ids": started,
        })
        log.info(
            "Team %s launched: %d/%d agents started",
            config.team_name, len(started), config.num_agents,
        )
        return started

    async def launch_all(self) -> dict[str, list[str]]:
        """Launch all configured teams.

        Returns a dict mapping team_name -> list of started agent IDs.
        """
        self._started_at = time.time()
        self._init_db()

        # Store launcher state
        self._store_launcher_state("running")

        _bus_write("global", "sdk-launcher", "system", "info", {
            "event": "launcher-starting",
            "pid": os.getpid(),
            "teams": [t.team_name for t in self.teams],
            "total_agents": sum(t.num_agents for t in self.teams),
        })

        results: dict[str, list[str]] = {}
        for config in self.teams:
            started = await self.launch_team(config)
            results[config.team_name] = started

        # Start background health monitoring
        self._health_task = asyncio.create_task(
            self._health_check_loop(),
            name="health-check",
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="heartbeat",
        )

        total_started = sum(len(v) for v in results.values())
        total_requested = sum(t.num_agents for t in self.teams)
        _bus_write("global", "sdk-launcher", "system", "info", {
            "event": "launcher-started",
            "pid": os.getpid(),
            "agents_started": total_started,
            "agents_requested": total_requested,
        })
        log.info(
            "SDK Launcher started: %d/%d agents across %d teams",
            total_started, total_requested, len(self.teams),
        )
        return results

    # -- Health monitoring --------------------------------------------------

    async def health_check(self) -> dict[str, list[dict]]:
        """Run a health check on all agents.

        Returns a dict mapping team_name -> list of agent health dicts.
        Also triggers auto-restart for crashed agents if enabled.
        """
        results: dict[str, list[dict]] = {}

        for team_name, agent_ids in self._team_agents.items():
            team_health: list[dict] = []
            for agent_id in agent_ids:
                agent = self._agents.get(agent_id)
                if agent is None:
                    continue

                health = agent.check_health()
                team_health.append(health)

                # Auto-restart crashed agents
                if (
                    health["status"] == "crashed"
                    and self.auto_restart
                    and not self._shutdown_event.is_set()
                ):
                    restarted = await agent.restart()
                    if restarted:
                        agent._register_in_runner_db()
                        health["status"] = "restarted"

            results[team_name] = team_health

        return results

    async def restart_agent(self, agent_id: str) -> bool:
        """Manually restart a specific agent.  Returns True on success."""
        agent = self._agents.get(agent_id)
        if agent is None:
            log.error("Cannot restart unknown agent: %s", agent_id)
            return False

        # Stop it first if still running
        if agent.is_alive:
            await agent.stop()

        # Reset restart count for manual restarts
        agent._restart_count = 0
        success = await agent.restart()
        if success:
            agent._register_in_runner_db()
        return success

    # -- Shutdown -----------------------------------------------------------

    async def shutdown(self, timeout: float = 15.0) -> None:
        """Gracefully shut down all agents and background tasks."""
        log.info("SDK Launcher shutdown initiated")
        self._shutdown_event.set()

        # Cancel background tasks
        for task in [self._health_task, self._heartbeat_task]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop all agents concurrently
        stop_tasks = []
        for agent in self._agents.values():
            if agent.is_alive:
                stop_tasks.append(agent.stop(timeout=timeout))

        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        self._store_launcher_state("stopped")

        _bus_write("global", "sdk-launcher", "system", "info", {
            "event": "launcher-stopped",
            "uptime": (
                time.time() - self._started_at
                if self._started_at else 0.0
            ),
            "agents_total": len(self._agents),
        })

        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

        log.info("SDK Launcher shutdown complete")

    # -- Status -------------------------------------------------------------

    def get_status(self) -> dict:
        """Return a summary of the launcher and all agents."""
        uptime = (
            time.time() - self._started_at if self._started_at else 0.0
        )
        agents_by_status: dict[str, int] = {}
        all_agents: list[dict] = []

        for agent in self._agents.values():
            health = agent.check_health()
            all_agents.append(health)
            s = health["status"]
            agents_by_status[s] = agents_by_status.get(s, 0) + 1

        return {
            "launcher_pid": os.getpid(),
            "uptime": round(uptime, 2),
            "started_at": self._started_at,
            "shutting_down": self._shutdown_event.is_set(),
            "teams": len(self._team_agents),
            "agents_total": len(self._agents),
            "agents_by_status": agents_by_status,
            "agents": all_agents,
        }

    # -- Background loops ---------------------------------------------------

    async def _health_check_loop(self) -> None:
        """Periodically check agent health and restart crashed agents."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.health_check_interval)
            except asyncio.CancelledError:
                return

            if self._shutdown_event.is_set():
                return

            try:
                results = await self.health_check()
                # Log summary
                total_running = 0
                total_crashed = 0
                for team_health in results.values():
                    for h in team_health:
                        if h["status"] == "running":
                            total_running += 1
                        elif h["status"] in ("crashed", "failed"):
                            total_crashed += 1

                if total_crashed > 0:
                    log.warning(
                        "Health check: %d running, %d crashed/failed",
                        total_running, total_crashed,
                    )
                else:
                    log.debug(
                        "Health check: %d running", total_running,
                    )
            except Exception:
                log.exception("Health check loop error")

    async def _heartbeat_loop(self) -> None:
        """Periodically update the heartbeat for all running agents in the DB."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                return

            if self._shutdown_event.is_set():
                return

            now = time.time()
            if self._db is None:
                continue

            for agent in self._agents.values():
                if agent.is_alive:
                    try:
                        # Update sdk_agents heartbeat
                        self._db.execute(
                            "UPDATE sdk_agents SET last_heartbeat = ? "
                            "WHERE agent_id = ?",
                            (now, agent.agent_id),
                        )
                        # Update multi_team_runner agents heartbeat
                        self._db.execute(
                            "UPDATE agents SET last_heartbeat = ?, "
                            "status = 'alive' WHERE agent_id = ?",
                            (now, agent.agent_id),
                        )
                    except sqlite3.OperationalError:
                        pass

            try:
                self._db.commit()
            except sqlite3.OperationalError:
                pass

    # -- Internal helpers ---------------------------------------------------

    def _store_launcher_state(self, status: str) -> None:
        """Write launcher state to the runner_state table."""
        if self._db is None:
            return
        now = time.time()
        try:
            # Ensure runner_state table exists
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS runner_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            for key, value in [
                ("sdk_status", status),
                ("sdk_pid", str(os.getpid())),
                ("sdk_started_at", str(self._started_at or now)),
                ("sdk_teams", json.dumps([t.team_name for t in self.teams])),
            ]:
                self._db.execute(
                    """INSERT INTO runner_state (key, value, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                           value=excluded.value,
                           updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
            self._db.commit()
        except sqlite3.OperationalError:
            log.warning("Failed to store launcher state", exc_info=True)

    async def wait(self) -> None:
        """Block until shutdown is signalled."""
        await self._shutdown_event.wait()


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

_launcher_instance: Optional[SDKLauncher] = None


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Install SIGTERM and SIGINT handlers for graceful shutdown."""

    def _handle_signal(sig: int) -> None:
        sig_name = signal.Signals(sig).name
        log.info("Received %s -- initiating shutdown", sig_name)
        if _launcher_instance is not None:
            loop.create_task(_launcher_instance.shutdown())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(path: str) -> dict:
    """Load and validate a launcher config JSON file."""
    with open(path, "r") as f:
        cfg = json.load(f)

    if "teams" not in cfg or not isinstance(cfg["teams"], list):
        raise ValueError("Config must have a 'teams' list")

    for t in cfg["teams"]:
        if "team_name" not in t:
            raise ValueError("Each team must have a 'team_name'")

    return cfg


def _print_status() -> None:
    """Print current launcher status from the database."""
    try:
        db = _get_db()
    except Exception:
        print("Cannot connect to database")
        return

    try:
        row = db.execute(
            "SELECT value FROM runner_state WHERE key = 'sdk_status'"
        ).fetchone()
        status = row["value"] if row else "unknown"
    except sqlite3.OperationalError:
        status = "unknown"

    try:
        agents = [
            dict(r) for r in db.execute(
                "SELECT * FROM sdk_agents ORDER BY team, role"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        agents = []

    info = {
        "sdk_status": status,
        "agents": agents,
    }

    # Add extra runner_state keys
    try:
        for key in ("sdk_pid", "sdk_started_at", "sdk_teams"):
            row = db.execute(
                "SELECT value FROM runner_state WHERE key = ?", (key,)
            ).fetchone()
            if row:
                info[key] = row["value"]
    except sqlite3.OperationalError:
        pass

    db.close()
    print(json.dumps(info, indent=2, default=str))


def _request_shutdown() -> None:
    """Send SIGTERM to the running launcher."""
    try:
        db = _get_db()
        row = db.execute(
            "SELECT value FROM runner_state WHERE key = 'sdk_pid'"
        ).fetchone()
        db.close()
    except Exception:
        print("Cannot read launcher PID from database")
        return

    if row is None:
        print("No launcher PID found")
        return

    pid = int(row["value"])
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to SDK launcher (pid={pid})")
    except ProcessLookupError:
        print(f"Launcher process (pid={pid}) is not running")
    except PermissionError:
        print(f"Permission denied sending signal to pid={pid}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def _async_main(teams: list[TeamConfig], cfg: dict) -> None:
    """Async entry point: launch teams, install signals, wait for shutdown."""
    global _launcher_instance

    launcher = SDKLauncher(
        teams=teams,
        heartbeat_interval=cfg.get("heartbeat_interval", 15.0),
        health_check_interval=cfg.get("health_check_interval", 30.0),
        auto_restart=cfg.get("auto_restart", True),
    )
    _launcher_instance = launcher

    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop)

    results = await launcher.launch_all()

    total_started = sum(len(v) for v in results.values())
    total_requested = sum(t.num_agents for t in teams)
    print(f"SDK Launcher started: {total_started}/{total_requested} agents "
          f"across {len(teams)} teams")
    print(f"  Bus directory: {_BUS_DIR}")
    print(f"  Database: {_DB_PATH}")
    print(f"  Log directory: {_LOG_DIR}")
    print(f"  PID: {os.getpid()}")
    print("Running. Press Ctrl+C to stop.")

    await launcher.wait()


def main() -> None:
    """CLI entry point."""
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="SDK Launcher for Claude Code agent teams",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--config", type=str, metavar="PATH",
        help="Path to launcher config JSON file",
    )
    group.add_argument(
        "--status", action="store_true",
        help="Show current launcher status",
    )
    group.add_argument(
        "--shutdown", action="store_true",
        help="Request launcher shutdown",
    )

    # Quick-launch flags (alternative to config file)
    parser.add_argument(
        "--team", type=str, default="default",
        help="Team name for quick-launch mode (default: 'default')",
    )
    parser.add_argument(
        "--agents", type=int, default=2,
        help="Number of agents for quick-launch mode (default: 2)",
    )
    parser.add_argument(
        "--prompt", type=str, default="",
        help="System prompt for quick-launch agents",
    )
    parser.add_argument(
        "--work-queue", type=str, default="",
        help="Work queue name for quick-launch agents",
    )
    parser.add_argument(
        "--working-dir", type=str, default="",
        help="Working directory for agents",
    )

    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    if args.shutdown:
        _request_shutdown()
        return

    # Build team configs
    if args.config:
        cfg = _load_config(args.config)
        teams = [TeamConfig.from_dict(t) for t in cfg["teams"]]
    else:
        cfg = {}
        teams = [
            TeamConfig(
                team_name=args.team,
                num_agents=args.agents,
                system_prompt=args.prompt,
                work_queue=args.work_queue,
                working_directory=args.working_dir,
            ),
        ]

    asyncio.run(_async_main(teams, cfg))


if __name__ == "__main__":
    main()
