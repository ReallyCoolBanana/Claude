"""Multi-process sandbox for testing Proto A coordination with real OS processes.

Each agent runs in its own subprocess via multiprocessing, communicating through
a shared temporary directory containing the JSONL bus and SQLite WAL database.

Pure stdlib — no external dependencies.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field
from multiprocessing import Process, Queue
from typing import Any, Callable

log = logging.getLogger(__name__)

# Ensure the prototype package is importable from child processes.
_PROTO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class AgentSpec:
    """Specification for an agent to run inside the sandbox.

    Parameters
    ----------
    agent_id : str
        Unique identifier.
    team : str
        Team name.
    role : str
        ``"coordinator"`` or ``"worker"``.
    behaviour : Callable | None
        Optional function ``(comm_dir, agent_id, team, role, config, result_queue) -> None``
        executed in the child process.  If ``None``, a default behaviour is used
        that registers, heartbeats, and waits for shutdown.
    config : dict
        Arbitrary config forwarded to the behaviour function.
    """

    agent_id: str
    team: str
    role: str = "worker"
    behaviour: Callable | None = None
    config: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result collected from a single agent process."""

    agent_id: str
    team: str
    role: str
    pid: int
    exit_code: int | None
    duration: float
    messages_sent: int = 0
    messages_received: int = 0
    errors: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class SandboxResult:
    """Aggregate result from a sandbox run."""

    comm_dir: str
    duration: float
    agents: list[AgentResult]
    bus_files: list[str]
    db_size_bytes: int
    total_messages: int = 0

    @property
    def all_succeeded(self) -> bool:
        return all(a.exit_code == 0 for a in self.agents)

    @property
    def error_agents(self) -> list[AgentResult]:
        return [a for a in self.agents if a.exit_code != 0]


# ---------------------------------------------------------------------------
# Default agent behaviours
# ---------------------------------------------------------------------------

def _default_coordinator_behaviour(
    comm_dir: str,
    agent_id: str,
    team: str,
    role: str,
    config: dict,
    result_queue: Queue,
) -> None:
    """Default coordinator: register, advance phases, heartbeat, wait for stop."""
    sys.path.insert(0, _PROTO_ROOT)
    from agent_comm.coordinator import Coordinator

    phases = config.get("phases", ["research", "synthesis", "done"])
    phase_interval = config.get("phase_interval", 1.0)
    hb_interval = config.get("heartbeat_interval", 1.0)

    result = {
        "messages_sent": 0,
        "messages_received": 0,
        "errors": [],
        "data": {"phases_advanced": []},
    }

    try:
        coord = Coordinator(comm_dir, config={
            "heartbeat_interval": hb_interval,
            "dead_agent_timeout": config.get("dead_agent_timeout", 30.0),
        })
        coord.start()

        for phase in phases:
            time.sleep(phase_interval)
            coord.advance_phase(phase)
            result["data"]["phases_advanced"].append(phase)
            result["messages_sent"] += 1

        # Hold the final phase for a bit so workers can observe it.
        time.sleep(config.get("hold_final_phase", 2.0))

        status = coord.get_status()
        result["data"]["final_status"] = {
            "phase": status["phase"],
            "agent_count": len(status["agents"]),
            "uptime": status["uptime"],
        }

        coord.stop()
    except Exception as e:
        result["errors"].append(f"{type(e).__name__}: {e}")

    result_queue.put((agent_id, result))


def _default_worker_behaviour(
    comm_dir: str,
    agent_id: str,
    team: str,
    role: str,
    config: dict,
    result_queue: Queue,
) -> None:
    """Default worker: register, heartbeat, send/receive messages, wait."""
    sys.path.insert(0, _PROTO_ROOT)
    from agent_comm.coordinator import Worker

    duration = config.get("run_duration", 5.0)
    send_interval = config.get("send_interval", 0.5)
    channel = config.get("channel", "global")

    result = {
        "messages_sent": 0,
        "messages_received": 0,
        "errors": [],
        "data": {},
    }

    try:
        worker = Worker(agent_id, team, comm_dir)
        worker.start()

        deadline = time.time() + duration
        seq = 0
        while time.time() < deadline:
            # Send
            worker.send(channel, "info", {
                "from": agent_id,
                "seq": seq,
                "ts": time.time(),
            })
            result["messages_sent"] += 1
            seq += 1

            # Receive
            msgs = worker.receive(channel)
            result["messages_received"] += len(msgs)

            time.sleep(send_interval)

        # Final drain
        msgs = worker.receive(channel)
        result["messages_received"] += len(msgs)

        worker.stop()
    except Exception as e:
        result["errors"].append(f"{type(e).__name__}: {e}")

    result_queue.put((agent_id, result))


def _run_agent(
    comm_dir: str,
    spec: AgentSpec,
    result_queue: Queue,
) -> None:
    """Entry point for each child process."""
    behaviour = spec.behaviour
    if behaviour is None:
        behaviour = (
            _default_coordinator_behaviour
            if spec.role == "coordinator"
            else _default_worker_behaviour
        )

    behaviour(comm_dir, spec.agent_id, spec.team, spec.role, spec.config, result_queue)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

class Sandbox:
    """Isolated multi-process sandbox for testing Proto A coordination.

    Creates a temporary directory with ``bus/`` and ``db/`` subdirectories,
    spawns agents as real OS processes, and collects results via queues.

    Can be used as a context manager::

        with Sandbox() as sb:
            sb.add_agent(AgentSpec("w1", "team-1"))
            sb.start_all()
            results = sb.collect_results()
    """

    def __init__(
        self,
        *,
        keep_dir: bool = False,
        comm_dir: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._keep_dir = keep_dir
        self._timeout = timeout
        self._specs: list[AgentSpec] = []
        self._processes: dict[str, Process] = {}
        self._result_queue: Queue = Queue()
        self._start_times: dict[str, float] = {}
        self._started = False

        if comm_dir:
            self._comm_dir = comm_dir
            self._owns_dir = False
        else:
            self._comm_dir = tempfile.mkdtemp(prefix="sandbox-proto-")
            self._owns_dir = True

        # Create subdirectories
        os.makedirs(os.path.join(self._comm_dir, "bus"), exist_ok=True)
        os.makedirs(os.path.join(self._comm_dir, "db"), exist_ok=True)

    @property
    def comm_dir(self) -> str:
        return self._comm_dir

    # -- agent management ---------------------------------------------------

    def add_agent(self, spec: AgentSpec) -> None:
        """Add an agent specification.  Must be called before ``start_all``."""
        if self._started:
            raise RuntimeError("Cannot add agents after start_all()")
        if spec.agent_id in {s.agent_id for s in self._specs}:
            raise ValueError(f"Duplicate agent_id: {spec.agent_id}")
        self._specs.append(spec)

    def add_team(
        self,
        team_name: str,
        num_workers: int = 2,
        coordinator: bool = True,
        worker_config: dict | None = None,
        coordinator_config: dict | None = None,
    ) -> list[AgentSpec]:
        """Convenience: add a full team with coordinator + N workers."""
        specs = []
        if coordinator:
            cs = AgentSpec(
                agent_id=f"{team_name}-coord",
                team=team_name,
                role="coordinator",
                config=coordinator_config or {},
            )
            self.add_agent(cs)
            specs.append(cs)

        for i in range(1, num_workers + 1):
            ws = AgentSpec(
                agent_id=f"{team_name}-worker-{i}",
                team=team_name,
                role="worker",
                config=worker_config or {},
            )
            self.add_agent(ws)
            specs.append(ws)

        return specs

    # -- lifecycle ----------------------------------------------------------

    def start_all(self) -> None:
        """Spawn all registered agents as child processes."""
        if self._started:
            raise RuntimeError("Already started")
        self._started = True

        # Start coordinators first so workers can find them.
        coordinators = [s for s in self._specs if s.role == "coordinator"]
        workers = [s for s in self._specs if s.role != "coordinator"]

        for spec in coordinators:
            self._spawn(spec)

        # Brief delay to let coordinators register.
        if coordinators and workers:
            time.sleep(0.5)

        for spec in workers:
            self._spawn(spec)

    def _spawn(self, spec: AgentSpec) -> None:
        """Spawn a single agent process."""
        p = Process(
            target=_run_agent,
            args=(self._comm_dir, spec, self._result_queue),
            name=f"sandbox-{spec.agent_id}",
            daemon=True,
        )
        p.start()
        self._processes[spec.agent_id] = p
        self._start_times[spec.agent_id] = time.time()
        log.info("Spawned %s (pid=%d, team=%s, role=%s)",
                 spec.agent_id, p.pid, spec.team, spec.role)

    def wait_all(self, timeout: float | None = None) -> None:
        """Wait for all agent processes to finish."""
        timeout = timeout or self._timeout
        deadline = time.time() + timeout
        for agent_id, proc in self._processes.items():
            remaining = max(0.1, deadline - time.time())
            proc.join(timeout=remaining)
            if proc.is_alive():
                log.warning("Agent %s did not exit in time, terminating", agent_id)
                proc.terminate()
                proc.join(timeout=5)

    def stop_all(self) -> None:
        """Terminate all running agent processes."""
        for agent_id, proc in self._processes.items():
            if proc.is_alive():
                proc.terminate()
        for proc in self._processes.values():
            proc.join(timeout=5)

    def collect_results(self, timeout: float | None = None) -> SandboxResult:
        """Wait for all agents and collect their results.

        Returns a ``SandboxResult`` with per-agent results and aggregate stats.
        """
        self.wait_all(timeout)

        # Drain the result queue.
        raw_results: dict[str, dict] = {}
        while not self._result_queue.empty():
            try:
                agent_id, result = self._result_queue.get_nowait()
                raw_results[agent_id] = result
            except Exception:
                break

        agent_results = []
        for spec in self._specs:
            proc = self._processes.get(spec.agent_id)
            raw = raw_results.get(spec.agent_id, {})
            start_t = self._start_times.get(spec.agent_id, time.time())

            agent_results.append(AgentResult(
                agent_id=spec.agent_id,
                team=spec.team,
                role=spec.role,
                pid=proc.pid if proc else 0,
                exit_code=proc.exitcode if proc else -1,
                duration=time.time() - start_t,
                messages_sent=raw.get("messages_sent", 0),
                messages_received=raw.get("messages_received", 0),
                errors=raw.get("errors", []),
                data=raw.get("data", {}),
            ))

        # Gather bus file info.
        bus_dir = os.path.join(self._comm_dir, "bus")
        bus_files = []
        total_messages = 0
        if os.path.isdir(bus_dir):
            for fname in os.listdir(bus_dir):
                if fname.endswith(".jsonl"):
                    fpath = os.path.join(bus_dir, fname)
                    bus_files.append(fname)
                    with open(fpath, "r") as f:
                        total_messages += sum(1 for line in f if line.strip())

        # DB size.
        db_path = os.path.join(self._comm_dir, "db", "state.db")
        db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

        start_min = min(self._start_times.values()) if self._start_times else time.time()
        return SandboxResult(
            comm_dir=self._comm_dir,
            duration=time.time() - start_min,
            agents=agent_results,
            bus_files=bus_files,
            db_size_bytes=db_size,
            total_messages=total_messages,
        )

    # -- inspection ---------------------------------------------------------

    def read_bus(self, channel: str) -> list[dict]:
        """Read all messages from a bus channel file (for post-hoc inspection)."""
        safe = channel.replace("/", "_").replace("..", "_")
        fpath = os.path.join(self._comm_dir, "bus", f"{safe}.jsonl")
        if not os.path.exists(fpath):
            return []
        messages = []
        with open(fpath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return messages

    def query_db(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a read-only SQL query against the shared state DB."""
        import sqlite3
        db_path = os.path.join(self._comm_dir, "db", "state.db")
        if not os.path.exists(db_path):
            return []
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop_all()
        if self._owns_dir and not self._keep_dir:
            shutil.rmtree(self._comm_dir, ignore_errors=True)

    def cleanup(self) -> None:
        """Manually clean up the sandbox directory."""
        self.stop_all()
        if self._owns_dir and os.path.isdir(self._comm_dir):
            shutil.rmtree(self._comm_dir, ignore_errors=True)
