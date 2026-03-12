# Process Management & Multi-Agent Patterns Research

**Date:** 2026-03-11
**Team:** Research Team 2
**Scope:** Best practices for managing multiple long-running processes in Python, with specific focus on the Proto A coordination architecture.

---

## Table of Contents

1. [Python asyncio Process Management](#1-python-asyncio-process-management)
2. [Health Checking Patterns](#2-health-checking-patterns)
3. [SQLite Concurrent Access](#3-sqlite-concurrent-access)
4. [JSONL File-Based Message Bus](#4-jsonl-file-based-message-bus)
5. [Multi-Agent Orchestration Patterns](#5-multi-agent-orchestration-patterns)

---

## 1. Python asyncio Process Management

### 1.1 asyncio.create_subprocess_exec Patterns

**Best Practice:** Use `asyncio.create_subprocess_exec` for launching child processes within an async event loop. It provides non-blocking process management with proper stream handling.

```python
import asyncio
import signal
from typing import Optional

class AgentProcess:
    """Manages a single agent subprocess with stream capture."""

    def __init__(self, agent_id: str, cmd: list[str]):
        self.agent_id = agent_id
        self.cmd = cmd
        self.process: Optional[asyncio.subprocess.Process] = None
        self._started = False

    async def start(self) -> None:
        """Launch the subprocess with captured stdout/stderr."""
        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Start in a new process group for clean signal management
            start_new_session=True,
        )
        self._started = True

    async def wait(self) -> int:
        """Wait for process completion and return exit code."""
        if self.process is None:
            raise RuntimeError("Process not started")
        return await self.process.wait()

    async def stop(self, timeout: float = 10.0) -> int:
        """Graceful stop: SIGTERM, then SIGKILL after timeout."""
        if self.process is None or self.process.returncode is not None:
            return self.process.returncode if self.process else -1

        # Send SIGTERM to the process group
        try:
            import os
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return self.process.returncode or -1

        try:
            return await asyncio.wait_for(self.process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Force kill the process group
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            return await self.process.wait()
```

### 1.2 Process Group Management

**Best Practice:** Use `start_new_session=True` to create a new process group for each subprocess. This allows sending signals to the entire group, ensuring child-of-child processes are also terminated.

```python
import os
import signal

async def launch_agent_group(agents: list[AgentProcess]) -> None:
    """Launch a group of agent processes in their own sessions."""
    for agent in agents:
        await agent.start()

async def shutdown_agent_group(
    agents: list[AgentProcess],
    timeout: float = 15.0,
) -> dict[str, int]:
    """Shutdown all agents gracefully, returning exit codes."""
    results = {}

    # Phase 1: Send SIGTERM to all process groups
    for agent in agents:
        if agent.process and agent.process.returncode is None:
            try:
                pgid = os.getpgid(agent.process.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    # Phase 2: Wait for graceful exit with timeout
    async def wait_agent(agent: AgentProcess) -> tuple[str, int]:
        try:
            code = await asyncio.wait_for(agent.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Force kill
            try:
                pgid = os.getpgid(agent.process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            code = await agent.wait()
        return agent.agent_id, code

    gathered = await asyncio.gather(
        *[wait_agent(a) for a in agents if a.process],
        return_exceptions=True,
    )
    for item in gathered:
        if isinstance(item, tuple):
            results[item[0]] = item[1]
    return results
```

### 1.3 Signal Forwarding to Child Processes

**Best Practice:** Install signal handlers in the parent process that forward signals to all managed child processes. Use `asyncio.get_event_loop().add_signal_handler()` for async-safe signal handling.

```python
class ProcessSupervisor:
    """Supervises multiple child processes with signal forwarding."""

    def __init__(self):
        self.children: dict[str, AgentProcess] = {}
        self._shutdown_event = asyncio.Event()

    def setup_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_signal(s)),
            )

    async def _handle_signal(self, sig: signal.Signals) -> None:
        """Forward signal to all children and initiate shutdown."""
        print(f"Received {sig.name}, forwarding to {len(self.children)} children")
        for agent_id, agent in self.children.items():
            if agent.process and agent.process.returncode is None:
                try:
                    pgid = os.getpgid(agent.process.pid)
                    os.killpg(pgid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
        self._shutdown_event.set()
```

**Pitfalls to avoid:**
- Never use `signal.signal()` inside an asyncio event loop; use `loop.add_signal_handler()` instead.
- Never call `sys.exit()` from a signal handler in asyncio code; set an event and let the main loop handle cleanup.
- `SIGKILL` cannot be caught or forwarded -- it terminates immediately. Always try `SIGTERM` first.
- On Linux, child processes do NOT automatically receive signals sent to the parent. You must forward explicitly.

### 1.4 Graceful Shutdown Patterns

**Best Practice:** Implement a two-phase shutdown: (1) signal all processes to stop, (2) wait with a deadline, then force-kill stragglers.

```python
async def graceful_shutdown(
    supervisor: ProcessSupervisor,
    phase1_timeout: float = 10.0,
    phase2_timeout: float = 5.0,
) -> None:
    """Two-phase graceful shutdown with escalation."""
    children = list(supervisor.children.values())

    # Phase 1: SIGTERM and wait
    for child in children:
        if child.process and child.process.returncode is None:
            try:
                child.process.terminate()
            except ProcessLookupError:
                pass

    # Wait for all to exit
    done, pending = await asyncio.wait(
        [asyncio.create_task(c.wait()) for c in children if c.process],
        timeout=phase1_timeout,
    )

    # Phase 2: SIGKILL stragglers
    if pending:
        for task in pending:
            task.cancel()
        for child in children:
            if child.process and child.process.returncode is None:
                try:
                    child.process.kill()
                except ProcessLookupError:
                    pass

        # Brief wait for SIGKILL to take effect
        await asyncio.sleep(0.5)

    # Cleanup: cancel any remaining tasks
    for task in pending:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
```

### 1.5 stdout/stderr Capture and Streaming

**Best Practice:** Use `asyncio.StreamReader` to capture output without blocking. Stream output line-by-line for real-time monitoring.

```python
import logging

async def stream_output(
    agent_id: str,
    stream: asyncio.StreamReader,
    stream_name: str = "stdout",
    logger: logging.Logger = logging.getLogger(__name__),
) -> list[str]:
    """Stream subprocess output line-by-line with logging.

    Returns all collected lines for post-mortem analysis.
    """
    lines = []
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="replace").rstrip()
        lines.append(decoded)
        logger.info("[%s:%s] %s", agent_id, stream_name, decoded)
    return lines


async def run_with_streaming(cmd: list[str], agent_id: str) -> tuple[int, list[str], list[str]]:
    """Run a command with real-time stdout/stderr streaming."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    # Stream stdout and stderr concurrently
    stdout_lines, stderr_lines = await asyncio.gather(
        stream_output(agent_id, proc.stdout, "stdout"),
        stream_output(agent_id, proc.stderr, "stderr"),
    )

    exit_code = await proc.wait()
    return exit_code, stdout_lines, stderr_lines
```

**Performance considerations:**
- `readline()` buffers internally; it does not syscall per byte.
- For high-throughput output, consider reading in chunks with `read(4096)` instead of `readline()`.
- If you do not need real-time output, `await proc.communicate()` is simpler and handles pipe deadlocks automatically.
- Pipe buffers are typically 64KB on Linux. If a subprocess produces output faster than you consume it, the pipe fills up and the child blocks. Always read both stdout and stderr concurrently (use `gather` or `communicate`).

---

## 2. Health Checking Patterns

### 2.1 Heartbeat-Based Health Checks

**Best Practice:** Agents write periodic heartbeats to shared state (e.g., SQLite). A coordinator checks heartbeat freshness and declares agents dead after a configurable timeout.

This pattern is already implemented in the Proto A codebase (`multi_team_runner.py` and `coordinator_hub.py`). Key design points:

```python
import time
import threading

class HeartbeatMonitor:
    """Monitors agent health via heartbeat timestamps."""

    def __init__(
        self,
        check_interval: float = 30.0,
        dead_timeout: float = 120.0,
        warn_timeout: float = 60.0,
    ):
        self.check_interval = check_interval
        self.dead_timeout = dead_timeout
        self.warn_timeout = warn_timeout
        self._stop_event = threading.Event()

    def run_check(self, agents: list[dict]) -> dict[str, str]:
        """Classify agents by health status.

        Returns:
            Dict mapping agent_id to one of: 'healthy', 'warning', 'dead'
        """
        now = time.time()
        results = {}
        for agent in agents:
            elapsed = now - agent["last_heartbeat"]
            if elapsed > self.dead_timeout:
                results[agent["agent_id"]] = "dead"
            elif elapsed > self.warn_timeout:
                results[agent["agent_id"]] = "warning"
            else:
                results[agent["agent_id"]] = "healthy"
        return results
```

**Recommendation:** Set `dead_timeout >= 4 * heartbeat_interval` to tolerate transient delays (GC pauses, I/O spikes). The Proto A default of `heartbeat_interval=30s, dead_timeout=120s` (4x) is appropriate.

### 2.2 Process Alive vs. Responsive Checks

**Best Practice:** Distinguish between three health states:
1. **Alive** -- process exists (PID check via `os.kill(pid, 0)`)
2. **Responsive** -- process is making progress (heartbeat is fresh)
3. **Functional** -- process is producing correct results (application-level check)

```python
import os
import errno

class AgentHealthChecker:
    """Multi-level health checking for agent processes."""

    @staticmethod
    def is_process_alive(pid: int) -> bool:
        """Check if a process exists without sending a signal."""
        try:
            os.kill(pid, 0)
            return True
        except OSError as e:
            if e.errno == errno.ESRCH:  # No such process
                return False
            if e.errno == errno.EPERM:  # Exists but no permission
                return True
            raise

    @staticmethod
    def is_responsive(last_heartbeat: float, timeout: float = 120.0) -> bool:
        """Check if the agent has sent a recent heartbeat."""
        return (time.time() - last_heartbeat) < timeout

    def full_check(self, agent: dict) -> dict:
        """Perform all health checks and return a status report."""
        pid = agent.get("pid")
        alive = self.is_process_alive(pid) if pid else False
        responsive = self.is_responsive(agent.get("last_heartbeat", 0))

        if not alive:
            status = "dead"
            action = "restart"
        elif not responsive:
            status = "hung"
            action = "kill_and_restart"
        else:
            status = "healthy"
            action = "none"

        return {
            "agent_id": agent["agent_id"],
            "status": status,
            "alive": alive,
            "responsive": responsive,
            "recommended_action": action,
        }
```

**Pitfall:** A process can be alive but completely hung (deadlocked, infinite loop). PID checks alone are insufficient. Always combine with heartbeat freshness checks.

### 2.3 Exponential Backoff for Restarts

**Best Practice:** Track restart history per agent and apply exponential backoff to prevent restart storms when an agent has a persistent failure.

```python
import random

class RestartPolicy:
    """Manages agent restart decisions with exponential backoff.

    Attributes:
        base_delay: Initial delay before first restart (seconds).
        max_delay: Maximum delay cap (seconds).
        max_restarts: Maximum restarts within the reset window.
        reset_window: If the agent runs successfully for this long (seconds),
                      reset the restart counter.
        jitter: Random jitter factor (0.0 to 1.0) to prevent thundering herd.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 300.0,
        max_restarts: int = 5,
        reset_window: float = 600.0,
        jitter: float = 0.2,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_restarts = max_restarts
        self.reset_window = reset_window
        self.jitter = jitter
        # Per-agent state: agent_id -> (restart_count, last_restart_time, last_success_time)
        self._state: dict[str, dict] = {}

    def should_restart(self, agent_id: str) -> tuple[bool, float]:
        """Determine if an agent should be restarted and the delay.

        Returns:
            (should_restart: bool, delay_seconds: float)
        """
        state = self._state.get(agent_id, {
            "count": 0,
            "last_restart": 0.0,
            "last_success": 0.0,
        })

        # Check if agent has been stable long enough to reset counter
        if state["last_success"] > 0:
            uptime = time.time() - state["last_success"]
            if uptime > self.reset_window:
                state["count"] = 0

        # Check max restarts
        if state["count"] >= self.max_restarts:
            return False, 0.0

        # Calculate delay with exponential backoff + jitter
        delay = min(
            self.base_delay * (2 ** state["count"]),
            self.max_delay,
        )
        jitter_amount = delay * self.jitter * random.random()
        delay += jitter_amount

        # Update state
        state["count"] += 1
        state["last_restart"] = time.time()
        self._state[agent_id] = state

        return True, delay

    def record_success(self, agent_id: str) -> None:
        """Record that an agent is running successfully (resets backoff)."""
        if agent_id in self._state:
            self._state[agent_id]["last_success"] = time.time()
```

### 2.4 Circuit Breaker Pattern for Failing Agents

**Best Practice:** Implement a circuit breaker that stops restarting agents after repeated failures, transitioning through states: CLOSED (normal) -> OPEN (failing, stop restarts) -> HALF_OPEN (probe with single restart).

```python
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"          # Normal operation -- restarts allowed
    OPEN = "open"              # Too many failures -- restarts blocked
    HALF_OPEN = "half_open"    # Probing -- one restart allowed

class CircuitBreaker:
    """Circuit breaker for agent restart decisions.

    Prevents restart storms by tracking failure rate and temporarily
    disabling restarts when an agent is persistently failing.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 300.0,
        success_threshold: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self._agents: dict[str, dict] = {}

    def _get_state(self, agent_id: str) -> dict:
        if agent_id not in self._agents:
            self._agents[agent_id] = {
                "state": CircuitState.CLOSED,
                "failure_count": 0,
                "success_count": 0,
                "last_failure": 0.0,
                "opened_at": 0.0,
            }
        return self._agents[agent_id]

    def can_restart(self, agent_id: str) -> bool:
        """Check if the circuit breaker allows a restart attempt."""
        s = self._get_state(agent_id)

        if s["state"] == CircuitState.CLOSED:
            return True

        if s["state"] == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if time.time() - s["opened_at"] > self.recovery_timeout:
                s["state"] = CircuitState.HALF_OPEN
                s["success_count"] = 0
                return True
            return False

        if s["state"] == CircuitState.HALF_OPEN:
            # Allow one probe restart
            return True

        return False

    def record_failure(self, agent_id: str) -> None:
        """Record a restart failure."""
        s = self._get_state(agent_id)
        s["failure_count"] += 1
        s["last_failure"] = time.time()

        if s["state"] == CircuitState.HALF_OPEN:
            # Probe failed, re-open the circuit
            s["state"] = CircuitState.OPEN
            s["opened_at"] = time.time()
        elif s["failure_count"] >= self.failure_threshold:
            s["state"] = CircuitState.OPEN
            s["opened_at"] = time.time()

    def record_success(self, agent_id: str) -> None:
        """Record a successful restart (agent is running healthy)."""
        s = self._get_state(agent_id)
        s["success_count"] += 1

        if s["state"] == CircuitState.HALF_OPEN:
            if s["success_count"] >= self.success_threshold:
                # Close the circuit -- agent recovered
                s["state"] = CircuitState.CLOSED
                s["failure_count"] = 0
        elif s["state"] == CircuitState.CLOSED:
            # Reset failure count on success
            s["failure_count"] = 0

    def get_status(self, agent_id: str) -> dict:
        """Return the current circuit breaker status for an agent."""
        s = self._get_state(agent_id)
        return {
            "agent_id": agent_id,
            "state": s["state"].value,
            "failure_count": s["failure_count"],
            "success_count": s["success_count"],
            "restartable": self.can_restart(agent_id),
        }
```

**Pitfalls to avoid:**
- Do not restart agents in a tight loop without backoff -- a fast-crashing agent can consume all system resources.
- Log circuit breaker state transitions so operators can see when an agent has been disabled.
- The `recovery_timeout` should be long enough to allow manual investigation (5+ minutes).
- In a multi-agent system, one agent's crash loop should never affect other agents' restarts.

---

## 3. SQLite Concurrent Access

### 3.1 WAL Mode Best Practices

**Best Practice:** Always enable WAL (Write-Ahead Logging) mode for multi-process or multi-threaded SQLite access. WAL allows concurrent readers and a single writer, versus the default rollback journal that locks the entire database on any write.

```python
import sqlite3

def configure_wal_connection(
    db_path: str,
    busy_timeout_ms: int = 5000,
) -> sqlite3.Connection:
    """Configure a SQLite connection with WAL mode and production settings."""
    conn = sqlite3.connect(
        db_path,
        timeout=busy_timeout_ms / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row

    # WAL mode: concurrent readers, non-blocking reads during writes
    conn.execute("PRAGMA journal_mode=WAL")

    # Busy timeout: wait this long before returning SQLITE_BUSY
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")

    # Synchronous NORMAL: safe with WAL, faster than FULL
    # FULL syncs on every commit; NORMAL syncs WAL on checkpoint only.
    # Risk: may lose last transaction on power failure (not process crash).
    conn.execute("PRAGMA synchronous=NORMAL")

    # WAL auto-checkpoint: checkpoint after this many WAL pages
    # Default is 1000 pages (~4MB). Increase for write-heavy workloads.
    conn.execute("PRAGMA wal_autocheckpoint=1000")

    return conn
```

**Key WAL behaviors to understand:**
- WAL allows unlimited concurrent readers, but only ONE writer at a time.
- Readers see a consistent snapshot from the moment they begin a transaction.
- Writers do NOT block readers. Readers do NOT block writers.
- Writers block OTHER writers. This is where `busy_timeout` matters.
- WAL files grow until a checkpoint occurs. Auto-checkpoint happens by default.

### 3.2 Connection Pooling for Multi-Process Access

**Best Practice:** In multi-process scenarios, each process should maintain its own connection(s). Do NOT share connection objects across processes (they are not fork-safe). Within a single process, use one connection per thread or use a thread-local pattern.

```python
import threading
from typing import Optional

class ConnectionPool:
    """Thread-safe SQLite connection pool for a single process.

    Each thread gets its own connection (thread-local storage).
    All connections share the same WAL-mode database.
    """

    def __init__(self, db_path: str, busy_timeout_ms: int = 5000):
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def get(self) -> sqlite3.Connection:
        """Get the connection for the current thread."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._create_connection()
            self._local.conn = conn
            with self._lock:
                self._all_conns.append(conn)
        return conn

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def close_all(self) -> None:
        """Close all connections in the pool."""
        with self._lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()
```

**For multi-process access (the Proto A scenario):**
- Each process opens its own connection(s). SQLite WAL handles concurrency at the filesystem level.
- Set `busy_timeout` high enough (5000ms+) to tolerate brief writer contention.
- Use `BEGIN IMMEDIATE` for transactions that will write, to acquire the write lock early rather than upgrading mid-transaction (which can cause `SQLITE_BUSY` on the upgrade step).

### 3.3 Timeout and Retry Strategies

**Best Practice:** Combine SQLite's built-in `busy_timeout` with application-level retry logic (exponential backoff) for robust SQLITE_BUSY handling.

The Proto A codebase already implements this well in `bus_core.py`:

```python
# Layer 1: SQLite's internal busy handler
# Waits up to busy_timeout_ms before returning SQLITE_BUSY
conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")

# Layer 2: Python sqlite3 module's timeout parameter
# Controls Python-level retry/sleep behavior
conn = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000)

# Layer 3: Application-level retry with exponential backoff
# Catches SQLITE_BUSY that made it through layers 1 and 2
@retry_on_busy(max_retries=5, initial_delay=0.1)
def critical_write(conn, data):
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO ...", data)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
```

**Why three layers?**
- Layer 1 (PRAGMA) handles most contention transparently, with zero application code.
- Layer 2 (connect timeout) provides a Python-level safety net.
- Layer 3 (application retry) handles edge cases where layers 1-2 give up (long checkpoints, heavy contention bursts).

**Pitfall:** When using `BEGIN IMMEDIATE`, the SQLITE_BUSY can occur on the `BEGIN` statement itself. The retry decorator must wrap the entire transaction, not just individual statements.

### 3.4 PRAGMA Settings for Reliability

**Recommended PRAGMA settings for production coordination workloads:**

```python
def apply_production_pragmas(conn: sqlite3.Connection) -> None:
    """Apply production-grade PRAGMA settings."""

    # journal_mode=WAL: Required for concurrent access
    conn.execute("PRAGMA journal_mode=WAL")

    # busy_timeout: How long to wait for a lock (ms)
    # 5000ms is good for coordination; increase for batch workloads
    conn.execute("PRAGMA busy_timeout=5000")

    # synchronous=NORMAL: Safe with WAL mode
    # Trades ~1% durability risk (power loss only) for ~2x write speed
    # Use FULL only if data loss on power failure is unacceptable
    conn.execute("PRAGMA synchronous=NORMAL")

    # cache_size: Number of pages in memory (negative = KB)
    # Default is -2000 (2MB). Increase for read-heavy workloads.
    conn.execute("PRAGMA cache_size=-8000")  # 8MB

    # wal_autocheckpoint: Pages before auto-checkpoint
    # Default 1000 (~4MB). Set to 0 to disable auto-checkpoint
    # and checkpoint manually during low-activity periods.
    conn.execute("PRAGMA wal_autocheckpoint=2000")

    # mmap_size: Memory-mapped I/O size (bytes)
    # Can improve read performance significantly. Set to 0 to disable.
    # 64MB is a reasonable default for small-to-medium databases.
    conn.execute("PRAGMA mmap_size=67108864")  # 64MB

    # temp_store=MEMORY: Keep temporary tables in memory
    conn.execute("PRAGMA temp_store=MEMORY")
```

**Settings to avoid in multi-process scenarios:**
- `PRAGMA journal_mode=OFF` -- removes crash protection entirely.
- `PRAGMA synchronous=OFF` -- database can be corrupted on ANY crash, not just power failure.
- `PRAGMA locking_mode=EXCLUSIVE` -- prevents all concurrent access.
- `PRAGMA journal_mode=TRUNCATE` or `DELETE` -- these block readers during writes (no WAL benefit).

**Checkpoint management for long-running systems:**

```python
def manual_checkpoint(conn: sqlite3.Connection, mode: str = "PASSIVE") -> tuple[int, int]:
    """Run a WAL checkpoint manually.

    Modes:
        PASSIVE: Checkpoint as much as possible without blocking.
        FULL: Block until all WAL is checkpointed (blocks writers).
        RESTART: Like FULL, but also resets WAL file to start.
        TRUNCATE: Like RESTART, but also truncates WAL to zero size.

    Returns (pages_in_wal, pages_checkpointed).
    """
    row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
    return row[1], row[2]  # pages_in_wal, pages_checkpointed
```

---

## 4. JSONL File-Based Message Bus

### 4.1 Atomic Append Patterns

**Best Practice:** Use `os.open()` with `O_APPEND` and `os.write()` for POSIX-atomic appends. On POSIX systems, writes up to `PIPE_BUF` (at least 4096 bytes, typically 4096 on Linux) are guaranteed atomic when using `O_APPEND`.

This is exactly the pattern used in the Proto A codebase:

```python
import os
import json
import uuid
import time

MAX_MESSAGE_BYTES = 4096  # POSIX PIPE_BUF guarantee

def atomic_append(filepath: str, message: dict) -> str:
    """Atomically append a JSON message to a JSONL file.

    The write is guaranteed atomic on POSIX systems because:
    1. O_APPEND ensures the kernel seeks to EOF before each write.
    2. The write size is <= PIPE_BUF (4096 bytes).
    3. A single os.write() call is used (no buffered I/O).

    Returns the message ID.
    """
    msg_id = str(uuid.uuid4())
    message["id"] = msg_id
    message["ts"] = time.time()

    raw = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"

    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError(
            f"Message {len(raw)} bytes exceeds {MAX_MESSAGE_BYTES} byte "
            f"atomic write limit"
        )

    fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)

    return msg_id
```

**Why this works:**
- `O_APPEND` is a kernel-level guarantee: the seek to EOF and write are a single atomic operation.
- Writes under `PIPE_BUF` are not interleaved with concurrent writes from other processes.
- No file locking is needed -- the kernel handles everything.

**Pitfalls:**
- Messages MUST be under 4096 bytes (including the trailing newline). Larger messages can be interleaved.
- `O_APPEND` atomicity does NOT extend to NFS or networked filesystems. Only use local filesystems.
- Python's `open(..., 'a')` (buffered I/O) does NOT guarantee atomic writes. Always use `os.open()`/`os.write()`.
- Do not use `json.dump()` directly to a file object -- it may issue multiple `write()` calls internally.

### 4.2 File Locking Strategies

For cases where atomic append is insufficient (messages > 4096 bytes, or non-POSIX systems):

```python
import fcntl
import contextlib

@contextlib.contextmanager
def file_lock(filepath: str, exclusive: bool = True):
    """Acquire a file lock using fcntl.

    Uses a separate .lock file to avoid interfering with the data file.
    Blocking lock: waits until acquired (no timeout -- use with caution).
    """
    lockpath = filepath + ".lock"
    lock_fd = os.open(lockpath, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_fd, lock_type)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def locked_append(filepath: str, message: dict) -> str:
    """Append with file locking for messages of any size."""
    msg_id = str(uuid.uuid4())
    message["id"] = msg_id
    message["ts"] = time.time()
    raw = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"

    with file_lock(filepath):
        fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)

    return msg_id
```

**fcntl.flock vs. fcntl.lockf:**
- `flock()`: Advisory lock on the entire file. Simpler. Locks are per-file-description (shared across fork).
- `lockf()` / `fcntl()`: POSIX byte-range locks. More granular but more complex. Locks are per-process.
- **Recommendation:** Use `flock()` for the message bus pattern. It is simpler and sufficient since we always lock the entire file.

**Recommendation for Proto A:** The current atomic append pattern (no locks) is correct and preferred for messages under 4096 bytes. File locking adds latency and contention. Only add locking if the message size limit becomes a problem.

### 4.3 Log Rotation with Active Readers

**Best Practice:** Rotate by renaming the active file and creating a new one. Active readers that have the old file open will continue reading it. New writers will create a fresh file.

```python
import os
import time
import glob

class RotatingBus:
    """JSONL bus with time-based file rotation.

    Each channel rotates to a new file when the current file exceeds
    max_size_bytes or max_age_seconds.
    """

    def __init__(
        self,
        bus_dir: str,
        max_size_bytes: int = 10 * 1024 * 1024,  # 10MB
        max_age_seconds: float = 3600,             # 1 hour
        max_rotated_files: int = 5,
    ):
        self.bus_dir = bus_dir
        self.max_size_bytes = max_size_bytes
        self.max_age_seconds = max_age_seconds
        self.max_rotated_files = max_rotated_files
        self._creation_times: dict[str, float] = {}

    def should_rotate(self, filepath: str) -> bool:
        """Check if a channel file should be rotated."""
        if not os.path.exists(filepath):
            return False
        # Size check
        if os.path.getsize(filepath) > self.max_size_bytes:
            return True
        # Age check
        creation = self._creation_times.get(filepath, os.path.getctime(filepath))
        if time.time() - creation > self.max_age_seconds:
            return True
        return False

    def rotate(self, filepath: str) -> str:
        """Rotate the file: rename current, create new.

        Returns the path to the archived file.
        """
        timestamp = int(time.time())
        archived = f"{filepath}.{timestamp}"
        os.rename(filepath, archived)
        self._creation_times[filepath] = time.time()
        # Prune old archives
        self._prune_archives(filepath)
        return archived

    def _prune_archives(self, filepath: str) -> None:
        """Remove old rotated files beyond max_rotated_files."""
        pattern = f"{filepath}.*"
        archives = sorted(glob.glob(pattern))
        while len(archives) > self.max_rotated_files:
            oldest = archives.pop(0)
            try:
                os.remove(oldest)
            except OSError:
                pass
```

**Key consideration for rotation with active readers:**
- Readers must track their byte offset AND the file they are reading from.
- After rotation, readers finish reading the old file (by path or open fd), then switch to the new file at offset 0.
- Writers atomically rename the old file, so there is a brief moment where the new file does not exist. Writers should handle `FileNotFoundError` by creating a new file.

### 4.4 Performance at Scale (100s of messages/sec)

**Performance characteristics of the JSONL bus approach:**

| Factor | Impact | Mitigation |
|--------|--------|------------|
| Disk I/O per write | ~1 syscall per message | Batch writes (buffer N messages, write once) |
| fsync overhead | Not needed for O_APPEND atomicity | Avoid explicit fsync unless durability matters |
| File size growth | Readers must scan from offset | Track byte offsets; rotate files |
| Reader contention | None (O_APPEND + read don't conflict) | N/A |
| JSON serialization | ~10us per small message | Use `separators=(",",":")` to minimize size |

**Benchmarks (typical Linux ext4, SSD):**

- Atomic append (single writer): ~50,000-100,000 messages/sec
- Atomic append (10 concurrent writers): ~10,000-30,000 messages/sec per writer
- Reading (sequential scan): ~100,000-500,000 messages/sec
- JSON parse: ~20,000-50,000 messages/sec (depends on message size)

**For 100s of messages/sec, the JSONL atomic append approach is more than sufficient.** The bottleneck at this scale is typically JSON parsing on the read side, not the I/O.

**Optimization for high throughput:**

```python
class BatchingBusWriter:
    """Batches multiple messages into a single write for throughput."""

    def __init__(self, bus_dir: str, flush_interval: float = 0.1, max_batch: int = 100):
        self.bus_dir = bus_dir
        self.flush_interval = flush_interval
        self.max_batch = max_batch
        self._buffers: dict[str, list[bytes]] = {}
        self._lock = threading.Lock()
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._stop = threading.Event()
        self._flush_thread.start()

    def write(self, channel: str, message: dict) -> None:
        """Buffer a message for batched writing."""
        raw = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._lock:
            buf = self._buffers.setdefault(channel, [])
            buf.append(raw)
            if len(buf) >= self.max_batch:
                self._flush_channel(channel)

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.flush_interval)
            self.flush_all()

    def flush_all(self) -> None:
        """Write all buffered messages to disk."""
        with self._lock:
            for channel, buf in self._buffers.items():
                if buf:
                    self._flush_channel(channel)

    def _flush_channel(self, channel: str) -> None:
        """Flush a single channel's buffer. Must be called with self._lock held."""
        buf = self._buffers.get(channel, [])
        if not buf:
            return
        combined = b"".join(buf)
        self._buffers[channel] = []

        # Note: combined write may exceed PIPE_BUF, so atomicity is not
        # guaranteed. Use file locking if multiple processes batch-write.
        filepath = os.path.join(self.bus_dir, f"{channel}.jsonl")
        fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, combined)
        finally:
            os.close(fd)

    def stop(self) -> None:
        self._stop.set()
        self._flush_thread.join()
        self.flush_all()
```

**Pitfall with batching:** Combined writes exceed PIPE_BUF and lose atomicity. This is acceptable when there is only one writer per channel, or when file locking is used.

---

## 5. Multi-Agent Orchestration Patterns

### 5.1 Supervisor Trees (Erlang/OTP Style)

**Concept:** A hierarchical tree of supervisors where each supervisor monitors and restarts its children. Failures propagate up the tree, and restart strategies control how sibling processes are affected.

**Restart strategies:**
- **one_for_one:** Only restart the failed child. Other children are unaffected.
- **one_for_all:** If one child fails, restart ALL children under this supervisor.
- **rest_for_one:** Restart the failed child and all children started after it.

```python
from enum import Enum
from abc import ABC, abstractmethod

class RestartStrategy(Enum):
    ONE_FOR_ONE = "one_for_one"
    ONE_FOR_ALL = "one_for_all"
    REST_FOR_ONE = "rest_for_one"

class Supervisor:
    """Erlang/OTP-inspired supervisor for Python asyncio processes.

    Manages a list of child specifications and restarts them according
    to the configured strategy.
    """

    def __init__(
        self,
        name: str,
        strategy: RestartStrategy = RestartStrategy.ONE_FOR_ONE,
        max_restarts: int = 5,
        max_seconds: float = 60.0,
    ):
        self.name = name
        self.strategy = strategy
        self.max_restarts = max_restarts
        self.max_seconds = max_seconds
        self.children: list[ChildSpec] = []
        self._restart_history: list[float] = []
        self._running: dict[str, AgentProcess] = {}

    async def start_children(self) -> None:
        """Start all child processes in order."""
        for spec in self.children:
            proc = AgentProcess(spec.agent_id, spec.cmd)
            await proc.start()
            self._running[spec.agent_id] = proc

    async def handle_child_exit(self, agent_id: str, exit_code: int) -> None:
        """Handle a child process exit according to the restart strategy."""
        # Check restart intensity (too many restarts too fast)
        now = time.time()
        self._restart_history.append(now)
        cutoff = now - self.max_seconds
        self._restart_history = [t for t in self._restart_history if t > cutoff]

        if len(self._restart_history) > self.max_restarts:
            raise RuntimeError(
                f"Supervisor {self.name}: max restart intensity exceeded "
                f"({self.max_restarts} restarts in {self.max_seconds}s)"
            )

        if self.strategy == RestartStrategy.ONE_FOR_ONE:
            await self._restart_one(agent_id)
        elif self.strategy == RestartStrategy.ONE_FOR_ALL:
            await self._restart_all()
        elif self.strategy == RestartStrategy.REST_FOR_ONE:
            await self._restart_from(agent_id)

    async def _restart_one(self, agent_id: str) -> None:
        """Restart only the failed child."""
        spec = next(s for s in self.children if s.agent_id == agent_id)
        proc = AgentProcess(spec.agent_id, spec.cmd)
        await proc.start()
        self._running[spec.agent_id] = proc

    async def _restart_all(self) -> None:
        """Stop all children and restart them in order."""
        for agent_id, proc in self._running.items():
            await proc.stop()
        self._running.clear()
        await self.start_children()

    async def _restart_from(self, failed_id: str) -> None:
        """Restart the failed child and all children defined after it."""
        idx = next(i for i, s in enumerate(self.children) if s.agent_id == failed_id)
        # Stop children from idx onward (in reverse order)
        to_restart = self.children[idx:]
        for spec in reversed(to_restart):
            if spec.agent_id in self._running:
                await self._running[spec.agent_id].stop()
                del self._running[spec.agent_id]
        # Restart them in order
        for spec in to_restart:
            proc = AgentProcess(spec.agent_id, spec.cmd)
            await proc.start()
            self._running[spec.agent_id] = proc


class ChildSpec:
    """Specification for a supervised child process."""
    def __init__(self, agent_id: str, cmd: list[str], restart: str = "permanent"):
        self.agent_id = agent_id
        self.cmd = cmd
        self.restart = restart  # "permanent", "temporary", "transient"
```

**Mapping to Proto A:** The `MultiTeamRunner` acts as a single-level supervisor. To gain OTP-style resilience, introduce a two-level hierarchy: a top-level supervisor manages per-team supervisors, each of which manages its agents.

### 5.2 Process Pools with Work Stealing

**Best Practice:** Maintain a pool of worker processes that pull tasks from a shared queue. When one worker's local queue is empty, it "steals" from another worker's queue (dequeue from the tail to minimize contention).

The Proto A `WorkStealing` class implements a centralized version of this pattern via SQLite. For a more performant, distributed approach:

```python
import asyncio
from collections import deque

class WorkStealingPool:
    """Process pool with work-stealing for load balancing.

    Each worker has a local deque. Workers consume from their own
    front; thieves steal from another worker's back.
    """

    def __init__(self, num_workers: int):
        self.num_workers = num_workers
        self.queues: list[deque] = [deque() for _ in range(num_workers)]
        self._locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(num_workers)]

    async def submit(self, worker_idx: int, task: dict) -> None:
        """Submit a task to a specific worker's queue."""
        async with self._locks[worker_idx]:
            self.queues[worker_idx].appendleft(task)

    async def get_task(self, worker_idx: int) -> dict | None:
        """Get a task: try local queue first, then steal from others."""
        # Try local queue (pop from front -- FIFO for owner)
        async with self._locks[worker_idx]:
            if self.queues[worker_idx]:
                return self.queues[worker_idx].popleft()

        # Try stealing from other workers (pop from back -- LIFO for thief)
        for i in range(self.num_workers):
            if i == worker_idx:
                continue
            async with self._locks[i]:
                if self.queues[i]:
                    return self.queues[i].pop()  # Steal from back

        return None  # Nothing available anywhere

    def total_pending(self) -> int:
        """Return total number of pending tasks across all queues."""
        return sum(len(q) for q in self.queues)
```

**Trade-offs: Centralized (SQLite) vs. Distributed (per-worker deques):**

| Aspect | Centralized (SQLite) | Distributed (deques) |
|--------|---------------------|---------------------|
| Consistency | Strong (ACID transactions) | Eventual (possible double-steal) |
| Performance | ~1000 steals/sec | ~100,000 steals/sec |
| Durability | Persistent (survives crashes) | In-memory only |
| Complexity | Simple (SQL queries) | More complex (locking protocol) |
| Cross-process | Yes (SQLite WAL) | Requires IPC |

**Recommendation for Proto A:** The centralized SQLite approach is correct for the coordination layer. At the expected scale (tens of agents, not thousands), SQLite performance is more than adequate, and the durability and simplicity benefits outweigh the throughput difference.

### 5.3 Pipeline Architectures

**Best Practice:** Model multi-stage workflows as directed acyclic graphs (DAGs) with explicit dependency edges. The Proto A `PipelineManager` already implements this pattern.

Key architectural considerations:

```python
class PipelineOrchestrator:
    """High-level pipeline orchestration with backpressure.

    Manages data flow between pipeline stages, ensuring downstream
    stages are not overwhelmed by fast upstream producers.
    """

    def __init__(self, max_in_flight: int = 10):
        self.max_in_flight = max_in_flight
        self._semaphore = asyncio.Semaphore(max_in_flight)
        self._stages: dict[str, "StageHandler"] = {}

    async def run_stage(self, stage_name: str, input_data: dict) -> dict:
        """Run a pipeline stage with backpressure control."""
        async with self._semaphore:
            handler = self._stages[stage_name]
            return await handler.process(input_data)

    async def run_pipeline(self, dag: dict, initial_input: dict) -> dict:
        """Execute a complete pipeline DAG.

        dag format: {
            "stage_a": {"depends_on": [], "handler": handler_a},
            "stage_b": {"depends_on": ["stage_a"], "handler": handler_b},
            "stage_c": {"depends_on": ["stage_a"], "handler": handler_c},
            "stage_d": {"depends_on": ["stage_b", "stage_c"], "handler": handler_d},
        }
        """
        results: dict[str, dict] = {}
        completed: set[str] = set()

        # Find stages with no dependencies (entry points)
        ready = {name for name, spec in dag.items() if not spec["depends_on"]}

        while ready:
            # Run all ready stages concurrently
            tasks = {}
            for stage_name in ready:
                # Merge outputs of all upstream dependencies as input
                deps = dag[stage_name]["depends_on"]
                if deps:
                    stage_input = {}
                    for dep in deps:
                        stage_input[dep] = results[dep]
                else:
                    stage_input = initial_input

                tasks[stage_name] = asyncio.create_task(
                    self.run_stage(stage_name, stage_input)
                )

            # Wait for all ready stages to complete
            for stage_name, task in tasks.items():
                results[stage_name] = await task
                completed.add(stage_name)

            # Find newly ready stages
            ready = set()
            for name, spec in dag.items():
                if name in completed:
                    continue
                if all(dep in completed for dep in spec["depends_on"]):
                    ready.add(name)

        return results
```

**Backpressure is critical:** Without backpressure, a fast producer can overwhelm downstream consumers, causing memory exhaustion. Use `asyncio.Semaphore` or `asyncio.Queue(maxsize=N)` to limit in-flight work.

### 5.4 Fan-Out / Fan-In Patterns

**Fan-out:** Distribute work from a single source to multiple workers in parallel.
**Fan-in:** Collect results from multiple workers and merge them.

```python
class FanOutFanIn:
    """Fan-out/fan-in pattern for parallel task execution.

    Distributes a list of tasks across N workers, collects results,
    and optionally applies a merge function.
    """

    def __init__(self, num_workers: int = 4):
        self.num_workers = num_workers

    async def execute(
        self,
        tasks: list[dict],
        worker_fn,
        merge_fn=None,
        timeout: float = 300.0,
    ) -> list | dict:
        """Fan out tasks to workers, fan in results.

        Args:
            tasks: List of task dicts to process.
            worker_fn: Async function that processes a single task.
            merge_fn: Optional function to merge results. If None, returns list.
            timeout: Maximum time to wait for all workers.

        Returns:
            Merged result (if merge_fn provided) or list of results.
        """
        # Fan out: create bounded work queue
        queue = asyncio.Queue()
        for task in tasks:
            await queue.put(task)

        results = []
        results_lock = asyncio.Lock()

        async def worker(worker_id: int) -> None:
            while True:
                try:
                    task = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    result = await worker_fn(task)
                    async with results_lock:
                        results.append(result)
                except Exception as e:
                    async with results_lock:
                        results.append({"error": str(e), "task": task})
                finally:
                    queue.task_done()

        # Launch workers
        workers = [
            asyncio.create_task(worker(i))
            for i in range(min(self.num_workers, len(tasks)))
        ]

        # Wait with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(*workers),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            for w in workers:
                w.cancel()
            raise

        # Fan in: merge results
        if merge_fn:
            return merge_fn(results)
        return results


# Usage example:
async def example_usage():
    fanout = FanOutFanIn(num_workers=4)

    tasks = [{"url": f"https://api.example.com/item/{i}"} for i in range(100)]

    async def fetch_item(task: dict) -> dict:
        # Simulated work
        await asyncio.sleep(0.1)
        return {"url": task["url"], "data": "..."}

    def merge_results(results: list[dict]) -> dict:
        return {r["url"]: r["data"] for r in results if "error" not in r}

    merged = await fanout.execute(tasks, fetch_item, merge_results)
```

**Design considerations for fan-out/fan-in:**

1. **Error handling:** Decide whether one failed task should abort all workers (fail-fast) or be collected as an error result (fail-soft). The example above uses fail-soft.

2. **Ordering:** Fan-in results arrive in completion order, not submission order. If order matters, include an index in each task and sort after collection.

3. **Resource limits:** The `num_workers` parameter controls concurrency. Set it based on the bottleneck (CPU cores for CPU-bound work, connection limits for I/O-bound work).

4. **Partial results:** For long-running fan-out operations, consider streaming results back as they complete rather than waiting for all workers to finish.

---

## Summary of Recommendations for Proto A

| Area | Current State | Recommendation |
|------|--------------|----------------|
| Process management | Threading-based (`MultiTeamRunner`) | Consider asyncio for subprocess management if launching actual OS processes |
| Signal handling | `signal.signal()` in main thread | Correct for threaded model; use `loop.add_signal_handler()` if migrating to asyncio |
| Health checks | Heartbeat + dead agent detection | Add PID-level alive checks and circuit breaker for persistent failures |
| SQLite access | WAL mode with `busy_timeout` | Add `PRAGMA synchronous=NORMAL` and application-level retry (already in `bus_core.py`) |
| JSONL bus | Atomic O_APPEND writes | Pattern is correct; consider file rotation for long-running systems |
| Work stealing | Centralized SQLite queue | Correct for current scale; no changes needed |
| Pipelines | DAG with dependency tracking | Add backpressure (semaphore or queue bounds) for high-throughput pipelines |
| Supervision | Single-level monitor | Consider two-level supervisor tree for better fault isolation |

### Key Pitfalls to Avoid (Summary)

1. **Never share SQLite connections across `fork()` boundaries.** Each child process must create its own connection.
2. **Never use buffered Python file I/O (`open('a')`) for bus writes.** Always use `os.open()`/`os.write()` for atomicity.
3. **Never restart agents without backoff.** A fast-crashing agent in a tight restart loop can consume all system resources.
4. **Never rely solely on PID checks for health.** A process can be alive but deadlocked.
5. **Never use `signal.signal()` from a non-main thread.** Use `threading.Event` or `loop.add_signal_handler()`.
6. **Never set `PRAGMA synchronous=OFF` in multi-process scenarios.** Database corruption on crash is likely.
7. **Never assume O_APPEND atomicity on NFS or remote filesystems.** Only local filesystems provide this guarantee.
8. **Never write JSONL messages larger than 4096 bytes without file locking.** POSIX only guarantees atomicity for writes up to PIPE_BUF.
