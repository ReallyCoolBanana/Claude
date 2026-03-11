"""Event-driven coordinator, worker, and support classes for Prototype B.

Key design difference from Prototype A:
- Prototype A uses threads with ``threading.Event.wait()`` polling loops.
- Prototype B uses ``select.select()`` on FIFO file descriptors for
  event-driven coordination, reacting to messages as they arrive rather
  than polling at fixed intervals.

Spillover files replace epoch-rotated JSONL files because FIFOs consume
data on read (no unbounded growth).

Uses only the Python standard library.
"""

from __future__ import annotations

import glob as _glob
import json
import logging
import os
import select
import threading
import time
from typing import Callable

from agent_comm_b.bus import Message, PipeBusReader, PipeBusWriter
from agent_comm_b.core import AgentIdentity, CommConfig, CommDir
from agent_comm_b.state import SharedStateMap

__all__ = [
    "Coordinator",
    "CoordinatorWatchdog",
    "Worker",
    "SpilloverCleaner",
]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration values
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "heartbeat_interval": 30.0,
    "dead_agent_timeout": 120.0,
    "coordinator_timeout": 300.0,
    "cleanup_interval": 600.0,
}

_COORDINATOR_AGENT_ID = "coordinator"
_COORDINATOR_ROLE = "coordinator"
_COORDINATOR_TEAM = "system"
_GLOBAL_CHANNEL = "global"
_BLOCKERS_CHANNEL = "topic-blockers"


# ===================================================================
# SpilloverCleaner — purge old spillover files (replaces EpochRotator)
# ===================================================================


class SpilloverCleaner:
    """Manages cleanup of spillover files in the pipes directory.

    Since Prototype B uses FIFOs (data is consumed on read), the main
    cleanup concern is spillover files that accumulate when no reader
    was attached to a FIFO at write time.

    Parameters
    ----------
    pipes_dir:
        Path to the ``pipes/`` directory containing FIFO and spill files.
    retention_seconds:
        Number of seconds to retain spillover files before deletion.
    """

    def __init__(self, pipes_dir: str, retention_seconds: int = 7200) -> None:
        self.pipes_dir = pipes_dir
        self.retention_seconds = retention_seconds
        os.makedirs(pipes_dir, exist_ok=True)

    def cleanup_old_spills(self) -> int:
        """Delete spillover files older than *retention_seconds*.

        Returns the number of files deleted.
        """
        cutoff = time.time() - self.retention_seconds
        deleted = 0
        pattern = os.path.join(self.pipes_dir, "*.spill")
        for fpath in _glob.glob(pattern):
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    os.remove(fpath)
                    deleted += 1
                    log.debug("Deleted old spillover file %s", fpath)
                except OSError as exc:
                    log.warning("Failed to delete %s: %s", fpath, exc)
        if deleted:
            log.info("Cleaned up %d old spillover file(s)", deleted)
        return deleted

    def get_spill_size(self) -> int:
        """Return total bytes consumed by all spillover files."""
        total = 0
        pattern = os.path.join(self.pipes_dir, "*.spill")
        for fpath in _glob.glob(pattern):
            try:
                total += os.path.getsize(fpath)
            except OSError:
                continue
        return total


# ===================================================================
# Coordinator — central event-driven orchestrator
# ===================================================================


class Coordinator:
    """Central orchestrator using ``select.select()`` on FIFO file descriptors.

    Instead of polling with ``threading.Event.wait()`` (Prototype A), the
    coordinator's event loop blocks on ``select.select()`` until data arrives
    on one of its FIFO channels, or a timeout fires for periodic housekeeping
    (heartbeat, dead-agent checks, spillover cleanup).

    Parameters
    ----------
    comm_dir:
        Path to the shared communication directory (will be canonicalised).
    config:
        Optional overrides.  Recognised keys: ``heartbeat_interval``,
        ``dead_agent_timeout``, ``coordinator_timeout``, ``cleanup_interval``.
    """

    def __init__(self, comm_dir: str, config: dict | None = None) -> None:
        cfg = {**_DEFAULTS, **(config or {})}
        self.heartbeat_interval: float = cfg["heartbeat_interval"]
        self.dead_agent_timeout: float = cfg["dead_agent_timeout"]
        self.coordinator_timeout: float = cfg["coordinator_timeout"]
        self.cleanup_interval: float = cfg["cleanup_interval"]

        self._comm = CommDir(requested_path=comm_dir)
        self._comm.ensure_dirs()

        self._agent_id = _COORDINATOR_AGENT_ID
        self._team = _COORDINATOR_TEAM

        self._state = SharedStateMap(self._comm.shm_dir)

        self._writer = PipeBusWriter(
            self._comm.pipes_dir, self._agent_id, self._team,
        )
        self._global_reader = PipeBusReader(self._comm.pipes_dir, _GLOBAL_CHANNEL)
        self._blocker_reader = PipeBusReader(self._comm.pipes_dir, _BLOCKERS_CHANNEL)
        self._cleaner = SpilloverCleaner(self._comm.pipes_dir)

        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._current_phase: str = "init"

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Register self, open FIFOs, and begin the event loop on a thread."""
        self._started_at = time.time()
        self._state.register_agent(
            self._agent_id, self._team, _COORDINATOR_ROLE, os.getpid(),
        )
        self._state.set_phase(self._current_phase)

        # Open FIFO readers so we can obtain file descriptors for select().
        self._global_reader.open()
        self._blocker_reader.open()

        self._stop_event.clear()
        self._loop_thread = threading.Thread(
            target=self.event_loop, name="coordinator-event-loop", daemon=True,
        )
        self._loop_thread.start()
        self.broadcast("info", {"event": "coordinator-started", "pid": os.getpid()})
        log.info("Coordinator started (pid=%d)", os.getpid())

    def stop(self) -> None:
        """Graceful shutdown: broadcast stop message, cease event loop."""
        self.broadcast("info", {"event": "coordinator-stopping"})
        self._stop_event.set()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=self.heartbeat_interval + 2)
            self._loop_thread = None
        self._global_reader.close()
        self._blocker_reader.close()
        self._state.close()
        log.info("Coordinator stopped")

    # -- event loop (select-based) ------------------------------------------

    def event_loop(self) -> None:
        """Event loop driven by ``select.select()`` on FIFO file descriptors.

        Blocks until data is available on either the global or blocker
        FIFO, or until the select timeout expires (whichever comes first).
        On timeout, periodic housekeeping runs (heartbeat, agent checks,
        cleanup).
        """
        last_heartbeat = time.time()
        last_cleanup = time.time()

        while not self._stop_event.is_set():
            # Build the list of file descriptors to watch.
            read_fds: list[int] = []
            if self._global_reader._fifo_fd is not None:
                read_fds.append(self._global_reader._fifo_fd)
            if self._blocker_reader._fifo_fd is not None:
                read_fds.append(self._blocker_reader._fifo_fd)

            # Calculate the select timeout: wake up for the next periodic task.
            now = time.time()
            next_hb = last_heartbeat + self.heartbeat_interval - now
            next_cleanup = last_cleanup + self.cleanup_interval - now
            timeout = max(0.1, min(next_hb, next_cleanup))

            # Block on select — this is the key difference from Prototype A.
            try:
                if read_fds:
                    readable, _, _ = select.select(read_fds, [], [], timeout)
                else:
                    # No FDs available; fall back to a short sleep.
                    time.sleep(min(timeout, 1.0))
                    readable = []
            except (OSError, ValueError):
                # FD may have been closed during shutdown.
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)
                continue

            # Process readable FIFOs.
            if readable:
                blocker_fd = self._blocker_reader._fifo_fd
                if blocker_fd is not None and blocker_fd in readable:
                    try:
                        self.handle_blockers()
                    except Exception:
                        log.exception("Blocker handling failed")

                # Drain global channel (informational; coordinator may
                # want to log or react to global messages).
                global_fd = self._global_reader._fifo_fd
                if global_fd is not None and global_fd in readable:
                    try:
                        self._global_reader.poll()
                    except Exception:
                        log.exception("Global channel read failed")

            # Periodic heartbeat and agent checks.
            now = time.time()
            if now - last_heartbeat >= self.heartbeat_interval:
                try:
                    self._state.heartbeat(self._agent_id)
                except Exception:
                    log.exception("Coordinator heartbeat failed")
                try:
                    self.check_agents()
                except Exception:
                    log.exception("Agent check failed")
                last_heartbeat = now

            # Periodic cleanup.
            if now - last_cleanup >= self.cleanup_interval:
                try:
                    self.run_cleanup()
                except Exception:
                    log.exception("Cleanup failed")
                last_cleanup = now

    # -- agent health -------------------------------------------------------

    def check_agents(self) -> list[dict]:
        """Detect dead agents via SharedStateMap and broadcast warnings.

        Returns a list of dead-agent dicts.
        """
        dead = self._state.get_dead_agents(timeout=self.dead_agent_timeout)
        for agent in dead:
            log.warning(
                "Agent %s appears dead (last heartbeat %.1fs ago)",
                agent.agent_id,
                time.time() - agent.last_heartbeat,
            )
            self.broadcast(
                "info",
                {
                    "event": "agent-dead",
                    "agent_id": agent.agent_id,
                    "last_heartbeat": agent.last_heartbeat,
                },
            )
        return dead

    # -- phase transitions --------------------------------------------------

    def advance_phase(self, new_phase: str, data: dict | None = None) -> None:
        """Signal a phase transition to all agents.

        Parameters
        ----------
        new_phase:
            Name of the new phase (e.g. ``"research"``, ``"synthesis"``).
        data:
            Optional metadata to include in the phase-signal message.
        """
        old_phase = self._current_phase
        self._current_phase = new_phase
        self._state.set_phase(new_phase)
        body: dict = {
            "event": "phase-transition",
            "from": old_phase,
            "to": new_phase,
        }
        if data:
            body["data"] = data
        self.broadcast("phase-signal", body)
        log.info("Phase advanced: %s -> %s", old_phase, new_phase)

    # -- messaging ----------------------------------------------------------

    def broadcast(
        self, msg_type: str, body: dict, channel: str = _GLOBAL_CHANNEL,
    ) -> None:
        """Publish a message to the bus on *channel*."""
        try:
            self._writer.publish(channel, msg_type, body)
        except Exception:
            log.exception(
                "Broadcast failed (type=%s, channel=%s)", msg_type, channel,
            )

    # -- blockers -----------------------------------------------------------

    def handle_blockers(self) -> list[dict]:
        """Process blocker messages from the FIFO and escalate them.

        Returns a list of blocker message bodies.
        """
        messages = self._blocker_reader.poll()
        blockers: list[dict] = []
        for msg in messages:
            if msg.type != "blocker":
                continue
            blockers.append(msg.body)
            log.warning(
                "Blocker from %s: %s",
                msg.agent_id,
                msg.body.get("description", ""),
            )
            # Escalate by re-broadcasting on global channel.
            self.broadcast(
                "info",
                {
                    "event": "blocker-escalated",
                    "original_agent": msg.agent_id,
                    "blocker": msg.body,
                },
            )
        return blockers

    # -- cleanup ------------------------------------------------------------

    def run_cleanup(self) -> int:
        """Purge old spillover files.

        Returns the number of spillover files removed.
        """
        count = self._cleaner.cleanup_old_spills()
        if count:
            log.info("Cleanup: %d spillover files removed", count)
        return count

    # -- status -------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current coordinator state for diagnostics.

        Keys: ``agent_id``, ``phase``, ``uptime``, ``started_at``,
        ``agents``, ``spill_bytes``.
        """
        uptime = time.time() - self._started_at if self._started_at else 0.0
        try:
            agents = self._state.get_all_agents()
        except Exception:
            agents = []

        return {
            "agent_id": self._agent_id,
            "phase": self._current_phase,
            "uptime": round(uptime, 2),
            "started_at": self._started_at,
            "agents": agents,
            "spill_bytes": self._cleaner.get_spill_size(),
        }


# ===================================================================
# CoordinatorWatchdog — detect coordinator death via SharedStateMap
# ===================================================================


class CoordinatorWatchdog:
    """Monitors the coordinator from a worker agent's perspective.

    Same interface as Prototype A's watchdog, but uses mmap-based
    ``SharedStateMap`` instead of SQLite for reading coordinator
    heartbeat timestamps.

    Parameters
    ----------
    comm_dir:
        Path to the shared communication directory.
    coordinator_timeout:
        Seconds without a coordinator heartbeat before it is declared dead.
    """

    def __init__(
        self, comm_dir: str, coordinator_timeout: float = 300.0,
    ) -> None:
        self._comm = CommDir(requested_path=comm_dir)
        self._state = SharedStateMap(self._comm.shm_dir)
        self._timeout = coordinator_timeout
        self._callbacks: list[Callable[[], None]] = []
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        # Poll interval: check more often than the timeout for prompt reaction.
        self._poll_interval = min(coordinator_timeout / 4, 15.0)

    # -- public API ---------------------------------------------------------

    def is_coordinator_alive(self) -> bool:
        """Check the coordinator's last heartbeat in SharedStateMap.

        Returns ``True`` if a coordinator agent exists and its heartbeat
        is within the configured timeout window.
        """
        try:
            last_hb = self._state.get_heartbeat(_COORDINATOR_AGENT_ID)
        except Exception:
            return False
        if last_hb is None:
            return False
        return (time.time() - last_hb) < self._timeout

    def wait_for_coordinator(self, timeout: float = 60.0) -> bool:
        """Block until a coordinator appears or *timeout* seconds elapse.

        Returns ``True`` if a live coordinator was detected, ``False`` on
        timeout.
        """
        deadline = time.time() + timeout
        interval = min(timeout / 10, 2.0)
        while time.time() < deadline:
            if self.is_coordinator_alive():
                return True
            time.sleep(interval)
        return False

    def on_coordinator_death(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked when the coordinator dies.

        Multiple callbacks may be registered; they are called in order.
        Each receives no arguments.
        """
        self._callbacks.append(callback)

    def start_monitoring(self) -> None:
        """Start a background daemon thread that checks coordinator liveness."""
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="coord-watchdog",
            daemon=True,
        )
        self._monitor_thread.start()
        log.debug(
            "CoordinatorWatchdog monitoring started (timeout=%.1fs)",
            self._timeout,
        )

    def stop_monitoring(self) -> None:
        """Stop the background monitoring thread."""
        self._stop_event.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=self._poll_interval + 2)
            self._monitor_thread = None
        self._state.close()
        log.debug("CoordinatorWatchdog monitoring stopped")

    # -- internal -----------------------------------------------------------

    def _monitor_loop(self) -> None:
        """Background loop: periodically check coordinator liveness."""
        coordinator_was_alive = False
        while not self._stop_event.is_set():
            alive = self.is_coordinator_alive()
            if coordinator_was_alive and not alive:
                log.warning("Coordinator death detected!")
                self._fire_callbacks()
            coordinator_was_alive = alive
            self._stop_event.wait(self._poll_interval)

    def _fire_callbacks(self) -> None:
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                log.exception("Coordinator-death callback %r raised", cb)


# ===================================================================
# Worker — event-driven worker agent
# ===================================================================


class Worker:
    """A worker agent that registers with the coordinator and communicates
    via named pipes.

    Parameters
    ----------
    agent_id:
        Unique identifier for this worker.
    team:
        Team label (e.g. ``"TEAM-0018"``).
    comm_dir:
        Path to the shared communication directory.
    role:
        Agent role (default ``"worker"``).
    """

    def __init__(
        self,
        agent_id: str,
        team: str,
        comm_dir: str,
        role: str = "worker",
    ) -> None:
        self.agent_id = agent_id
        self.team = team
        self.role = role

        self._comm = CommDir(requested_path=comm_dir)
        self._comm.ensure_dirs()

        self._state = SharedStateMap(self._comm.shm_dir)

        self._writer = PipeBusWriter(self._comm.pipes_dir, agent_id, team)
        self._readers: dict[str, PipeBusReader] = {}

        self._watchdog: CoordinatorWatchdog | None = None
        self._stop_event = threading.Event()
        self._hb_thread: threading.Thread | None = None
        self._heartbeat_interval = 30.0

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Register in SharedStateMap, start heartbeat thread, start watchdog."""
        self._state.register_agent(
            self.agent_id, self.team, self.role, os.getpid(),
        )

        # Start heartbeat loop.
        self._stop_event.clear()
        self._hb_thread = threading.Thread(
            target=self.heartbeat_loop,
            name=f"worker-hb-{self.agent_id}",
            daemon=True,
        )
        self._hb_thread.start()

        # Start coordinator watchdog.
        self._watchdog = CoordinatorWatchdog(self._comm.path)
        self._watchdog.on_coordinator_death(self._on_coordinator_death)
        self._watchdog.start_monitoring()

        log.info(
            "Worker %s started (team=%s, pid=%d)",
            self.agent_id,
            self.team,
            os.getpid(),
        )

    def stop(self) -> None:
        """Graceful shutdown: stop heartbeat, stop watchdog, close state."""
        self._stop_event.set()
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=self._heartbeat_interval + 2)
            self._hb_thread = None
        if self._watchdog is not None:
            self._watchdog.stop_monitoring()
            self._watchdog = None
        # Close any open FIFO readers.
        for reader in self._readers.values():
            reader.close()
        self._readers.clear()
        self._state.close()
        log.info("Worker %s stopped", self.agent_id)

    # -- messaging ----------------------------------------------------------

    def send(self, channel: str, msg_type: str, body: dict) -> Message:
        """Publish a message to the bus on *channel*."""
        return self._writer.publish(channel, msg_type, body)

    def receive(self, channel: str) -> list[Message]:
        """Read all available messages from *channel*.

        Lazily creates and opens a ``PipeBusReader`` for channels not
        yet subscribed to.
        """
        if channel not in self._readers:
            reader = PipeBusReader(self._comm.pipes_dir, channel)
            reader.open()
            self._readers[channel] = reader
        return self._readers[channel].poll()

    def report_blocker(
        self, description: str, data: dict | None = None,
    ) -> None:
        """Post a blocker to the ``topic-blockers`` channel."""
        body: dict = {"description": description, "agent_id": self.agent_id}
        if data:
            body["data"] = data
        self._writer.publish(_BLOCKERS_CHANNEL, "blocker", body)
        log.warning(
            "Worker %s reported blocker: %s", self.agent_id, description,
        )

    # -- heartbeat ----------------------------------------------------------

    def heartbeat_loop(self) -> None:
        """Background loop: periodically heartbeat in SharedStateMap."""
        while not self._stop_event.is_set():
            try:
                self._state.heartbeat(self.agent_id)
            except Exception:
                log.exception("Worker %s heartbeat failed", self.agent_id)
            self._stop_event.wait(self._heartbeat_interval)

    # -- coordinator death handler ------------------------------------------

    def _on_coordinator_death(self) -> None:
        """Default handler for coordinator death: log and stop.

        In production this would save in-progress work before exiting.
        """
        log.critical(
            "Worker %s detected coordinator death — "
            "saving work and shutting down",
            self.agent_id,
        )
        # Signal the worker to stop gracefully.
        self._stop_event.set()
