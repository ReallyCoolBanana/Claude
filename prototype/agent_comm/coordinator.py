"""Coordinator, worker, and support classes for multi-agent orchestration.

Addresses:
- CR-3 / CR-4: coordinator single point of failure (CoordinatorWatchdog).
- RE-1: unbounded JSONL growth (EpochRotator).

Uses only the Python standard library.  Background loops run on daemon
threads controlled by ``threading.Event`` for clean shutdown.

**Import note (NEW-PROTO-012)**: The imports below (``agent_comm.bus``,
``agent_comm.core``, ``agent_comm.state``) are bare package-relative
imports.  They require that ``prototype/`` (or the directory containing
the ``agent_comm`` package) is on ``sys.path``.  When running from
outside the prototype directory, callers must ensure the path is
configured, e.g.::

    sys.path.insert(0, "/path/to/prototype")
"""

from __future__ import annotations

import glob as _glob
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from agent_comm.bus import BusReader, BusWriter, Message
from agent_comm.core import AgentIdentity, CommConfig, CommDir
from agent_comm.state import SharedState

__all__ = [
    "Coordinator",
    "CoordinatorWatchdog",
    "Worker",
    "EpochRotator",
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
# EpochRotator — JSONL epoch-based file rotation (RE-1 fix)
# ===================================================================


class EpochRotator:
    """Manages epoch-based JSONL rotation to prevent unbounded file growth.

    Each epoch spans one hour.  Files are named like::

        bus/global.2026-03-11T10.jsonl

    Old epoch files are deleted after *retention_hours*.

    Parameters
    ----------
    bus_dir:
        Path to the ``bus/`` directory containing JSONL channel files.
    retention_hours:
        Number of hours to retain old epoch files before deletion.
    """

    def __init__(self, bus_dir: str, retention_hours: int = 2) -> None:
        self.bus_dir = bus_dir
        self.retention_hours = retention_hours
        os.makedirs(bus_dir, exist_ok=True)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _epoch_tag() -> str:
        """Return the current epoch tag, e.g. ``2026-03-11T10``."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")

    @staticmethod
    def _safe_channel(channel: str) -> str:
        return channel.replace("/", "_").replace("..", "_")

    # -- public API ---------------------------------------------------------

    def current_epoch_file(self, channel: str) -> str:
        """Return the path of the current epoch file for *channel*.

        Example: ``bus/global.2026-03-11T10.jsonl``
        """
        safe = self._safe_channel(channel)
        return os.path.join(self.bus_dir, f"{safe}.{self._epoch_tag()}.jsonl")

    def rotate_if_needed(self, channel: str) -> str | None:
        """Create a new epoch file if the hour has changed.

        Returns the new file path if rotation occurred, else ``None``.
        """
        target = self.current_epoch_file(channel)
        if not os.path.exists(target):
            # Touch the file to signal epoch transition.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            os.close(fd)
            log.info("Rotated channel %r -> %s", channel, target)
            return target
        return None

    def cleanup_old_epochs(self) -> int:
        """Delete epoch files older than *retention_hours*.

        Returns the number of files deleted.
        """
        cutoff = time.time() - self.retention_hours * 3600
        deleted = 0
        # Match all epoch-tagged JSONL files: <channel>.<epoch>.jsonl
        pattern = os.path.join(self.bus_dir, "*.*.jsonl")
        for fpath in _glob.glob(pattern):
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    os.remove(fpath)
                    deleted += 1
                    log.debug("Deleted old epoch file %s", fpath)
                except OSError as exc:
                    log.warning("Failed to delete %s: %s", fpath, exc)
        if deleted:
            log.info("Cleaned up %d old epoch file(s)", deleted)
        return deleted

    def get_active_files(self, channel: str) -> list[str]:
        """Return the current and previous epoch files for *channel*.

        This ensures readers can still pick up messages written at the
        tail end of the previous epoch.
        """
        safe = self._safe_channel(channel)
        pattern = os.path.join(self.bus_dir, f"{safe}.*.jsonl")
        candidates = sorted(_glob.glob(pattern))
        if not candidates:
            return []
        # Return at most the last two (previous + current).
        return candidates[-2:] if len(candidates) >= 2 else candidates


# ===================================================================
# Coordinator — central orchestrator
# ===================================================================


class Coordinator:
    """Central orchestrator that manages agent teams.

    Registers itself in ``SharedState``, heartbeats on a background thread,
    monitors worker liveness, and handles phase transitions.

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

        db_path = os.path.join(self._comm.db_dir, "state.db")
        self._state = SharedState(db_path)

        self._writer = BusWriter(self._comm.bus_dir, self._agent_id, self._team)
        self._blocker_reader = BusReader(self._comm.bus_dir, _BLOCKERS_CHANNEL)
        self._rotator = EpochRotator(self._comm.bus_dir)

        self._stop_event = threading.Event()
        self._hb_thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._current_phase: str = "init"

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Initialise state, register as coordinator, and start heartbeat loop."""
        self._started_at = time.time()
        self._state.register_agent(
            self._agent_id, self._team, _COORDINATOR_ROLE, os.getpid(),
        )
        self._state.signal_phase(
            self._current_phase, "started", self._agent_id,
        )
        self._stop_event.clear()
        self._hb_thread = threading.Thread(
            target=self.heartbeat_loop, name="coordinator-hb", daemon=True,
        )
        self._hb_thread.start()
        self.broadcast("info", {"event": "coordinator-started", "pid": os.getpid()})
        log.info("Coordinator started (pid=%d)", os.getpid())

    def stop(self) -> None:
        """Graceful shutdown: broadcast stop message and cease heartbeats."""
        self.broadcast("info", {"event": "coordinator-stopping"})
        self._stop_event.set()
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=self.heartbeat_interval + 2)
            self._hb_thread = None
        self._state.close()
        log.info("Coordinator stopped")

    # -- heartbeat ----------------------------------------------------------

    def heartbeat_loop(self) -> None:
        """Background loop: heartbeat, check agents, periodic cleanup.

        Runs until ``_stop_event`` is set.
        """
        last_cleanup = time.time()
        while not self._stop_event.is_set():
            try:
                self._state.heartbeat(self._agent_id)
            except Exception:
                log.exception("Coordinator heartbeat failed")

            try:
                self.check_agents()
            except Exception:
                log.exception("Agent check failed")

            # Periodic cleanup
            if time.time() - last_cleanup >= self.cleanup_interval:
                try:
                    self.run_cleanup()
                except Exception:
                    log.exception("Cleanup failed")
                last_cleanup = time.time()

            self._stop_event.wait(self.heartbeat_interval)

    # -- agent health -------------------------------------------------------

    def check_agents(self) -> list[dict]:
        """Detect dead agents and broadcast warnings.

        Returns the list of dead-agent dicts from ``SharedState``.
        """
        dead = self._state.get_dead_agents(timeout=self.dead_agent_timeout)
        for agent in dead:
            log.warning(
                "Agent %s appears dead (last heartbeat %.1fs ago)",
                agent["agent_id"],
                time.time() - agent["last_heartbeat"],
            )
            self.broadcast(
                "info",
                {
                    "event": "agent-dead",
                    "agent_id": agent["agent_id"],
                    "last_heartbeat": agent["last_heartbeat"],
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
        self._state.signal_phase(
            new_phase, "transition", self._agent_id,
            data=json.dumps(data) if data else None,
        )
        body = {"event": "phase-transition", "from": old_phase, "to": new_phase}
        if data:
            body["data"] = data
        self.broadcast("phase-signal", body)
        log.info("Phase advanced: %s -> %s", old_phase, new_phase)

    # -- messaging ----------------------------------------------------------

    def broadcast(self, msg_type: str, body: dict, channel: str = _GLOBAL_CHANNEL) -> None:
        """Publish a message to the bus on *channel*."""
        try:
            self._writer.publish(channel, msg_type, body)
        except Exception:
            log.exception("Broadcast failed (type=%s, channel=%s)", msg_type, channel)

    # -- blockers -----------------------------------------------------------

    def handle_blockers(self) -> list[dict]:
        """Poll the ``topic-blockers`` channel and escalate unresolved items.

        Returns a list of blocker message bodies.
        """
        messages = self._blocker_reader.poll()
        blockers: list[dict] = []
        for msg in messages:
            if msg.type != "blocker":
                continue
            blockers.append(msg.body)
            log.warning("Blocker from %s: %s", msg.agent_id, msg.body.get("description", ""))
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

    # -- cleanup (RE-1) -----------------------------------------------------

    def run_cleanup(self) -> int:
        """Rotate JSONL epoch files and purge expired SQLite records.

        Returns total number of items cleaned.
        """
        rotated_count = self._rotator.cleanup_old_epochs()
        purged_count = self._state.cleanup_expired()
        total = rotated_count + purged_count
        if total:
            log.info("Cleanup: %d epoch files removed, %d DB rows purged", rotated_count, purged_count)
        return total

    # -- status -------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current coordinator state for diagnostics.

        Keys: ``agent_id``, ``phase``, ``uptime``, ``agents``, ``started_at``.
        """
        uptime = time.time() - self._started_at if self._started_at else 0.0
        # Fetch all known agents via public API.
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
        }


# ===================================================================
# CoordinatorWatchdog — detect coordinator death (CR-3/CR-4)
# ===================================================================


class CoordinatorWatchdog:
    """Monitors the coordinator from a worker agent's perspective.

    Workers create one of these to detect coordinator death and trigger
    fallback behaviour (e.g. save work and exit cleanly).

    Parameters
    ----------
    comm_dir:
        Path to the shared communication directory.
    coordinator_timeout:
        Seconds without a coordinator heartbeat before it is declared dead.
    """

    def __init__(self, comm_dir: str, coordinator_timeout: float = 300.0) -> None:
        self._comm = CommDir(requested_path=comm_dir)
        db_path = os.path.join(self._comm.db_dir, "state.db")
        self._state = SharedState(db_path)
        self._timeout = coordinator_timeout
        self._callbacks: list[callable] = []
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        # Poll interval: check more often than the timeout so we react promptly.
        self._poll_interval = min(coordinator_timeout / 4, 15.0)

    # -- public API ---------------------------------------------------------

    def is_coordinator_alive(self) -> bool:
        """Check the coordinator's last heartbeat in SharedState.

        Returns ``True`` if a coordinator agent exists and its heartbeat
        is within the configured timeout window.
        """
        try:
            last_hb = self._state.get_coordinator_heartbeat()
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

    def on_coordinator_death(self, callback: callable) -> None:
        """Register a callback to be invoked when the coordinator dies.

        Multiple callbacks may be registered; they are called in order.
        Each receives no arguments.
        """
        self._callbacks.append(callback)

    def start_monitoring(self) -> None:
        """Start a background daemon thread that checks coordinator liveness."""
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="coord-watchdog", daemon=True,
        )
        self._monitor_thread.start()
        log.debug("CoordinatorWatchdog monitoring started (timeout=%.1fs)", self._timeout)

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
        """Background loop: periodically check coordinator liveness.

        A startup grace period (equal to the configured timeout) is applied
        so the watchdog does not fire if the coordinator has never heartbeated
        yet (e.g. it crashed before its first heartbeat, or it simply hasn't
        started yet).  During the grace period, the watchdog waits for the
        coordinator to appear; once the grace period expires with no
        heartbeat, the callbacks fire.
        """
        coordinator_was_alive = False
        # Grace period: don't fire callbacks until the coordinator has had
        # a chance to start and register its first heartbeat.
        grace_deadline = time.time() + self._timeout
        while not self._stop_event.is_set():
            alive = self.is_coordinator_alive()
            if alive:
                coordinator_was_alive = True
            if coordinator_was_alive and not alive:
                log.warning("Coordinator death detected!")
                self._fire_callbacks()
            elif not coordinator_was_alive and not alive and time.time() > grace_deadline:
                log.warning(
                    "Coordinator never appeared within grace period (%.1fs) — "
                    "firing death callbacks",
                    self._timeout,
                )
                self._fire_callbacks()
                # Treat as if it was alive then died, so we don't fire again
                # every poll interval.
                coordinator_was_alive = True
            self._stop_event.wait(self._poll_interval)

    def _fire_callbacks(self) -> None:
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                log.exception("Coordinator-death callback %r raised", cb)


# ===================================================================
# Worker — a worker agent that participates in the comm system
# ===================================================================


class Worker:
    """A worker agent that registers with the coordinator and participates
    in the communication system.

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

    def __init__(self, agent_id: str, team: str, comm_dir: str, role: str = "worker") -> None:
        self.agent_id = agent_id
        self.team = team
        self.role = role

        self._comm = CommDir(requested_path=comm_dir)
        self._comm.ensure_dirs()

        db_path = os.path.join(self._comm.db_dir, "state.db")
        self._state = SharedState(db_path)

        self._writer = BusWriter(self._comm.bus_dir, agent_id, team)
        self._readers: dict[str, BusReader] = {}

        self._watchdog: CoordinatorWatchdog | None = None
        self._stop_event = threading.Event()
        self._hb_thread: threading.Thread | None = None
        self._heartbeat_interval = 30.0

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Register in SharedState, start heartbeat thread, start watchdog."""
        self._state.register_agent(self.agent_id, self.team, self.role, os.getpid())

        # Start heartbeat loop.
        self._stop_event.clear()
        self._hb_thread = threading.Thread(
            target=self.heartbeat_loop, name=f"worker-hb-{self.agent_id}", daemon=True,
        )
        self._hb_thread.start()

        # Start coordinator watchdog.
        self._watchdog = CoordinatorWatchdog(self._comm.path)
        self._watchdog.on_coordinator_death(self._on_coordinator_death)
        self._watchdog.start_monitoring()

        log.info("Worker %s started (team=%s, pid=%d)", self.agent_id, self.team, os.getpid())

    def stop(self) -> None:
        """Graceful shutdown: stop heartbeat, stop watchdog, close state."""
        self._stop_event.set()
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=self._heartbeat_interval + 2)
            self._hb_thread = None
        if self._watchdog is not None:
            self._watchdog.stop_monitoring()
            self._watchdog = None
        self._state.close()
        log.info("Worker %s stopped", self.agent_id)

    # -- messaging ----------------------------------------------------------

    def send(self, channel: str, msg_type: str, body: dict) -> Message:
        """Publish a message to the bus on *channel*."""
        return self._writer.publish(channel, msg_type, body)

    def receive(self, channel: str) -> list[Message]:
        """Poll *channel* for new messages since the last call."""
        if channel not in self._readers:
            self._readers[channel] = BusReader(self._comm.bus_dir, channel)
        return self._readers[channel].poll()

    def report_blocker(self, description: str, data: dict | None = None) -> None:
        """Post a blocker to the ``topic-blockers`` channel."""
        body: dict = {"description": description, "agent_id": self.agent_id}
        if data:
            body["data"] = data
        self._writer.publish(_BLOCKERS_CHANNEL, "blocker", body)
        log.warning("Worker %s reported blocker: %s", self.agent_id, description)

    # -- heartbeat ----------------------------------------------------------

    def heartbeat_loop(self) -> None:
        """Background loop: periodically heartbeat in SharedState."""
        while not self._stop_event.is_set():
            try:
                self._state.heartbeat(self.agent_id)
            except Exception:
                log.exception("Worker %s heartbeat failed", self.agent_id)
            self._stop_event.wait(self._heartbeat_interval)

    # -- coordinator death handler ------------------------------------------

    def _on_coordinator_death(self) -> None:
        """Default handler for coordinator death: log warning and stop.

        In production this would save in-progress work before exiting.
        """
        log.critical(
            "Worker %s detected coordinator death — saving work and shutting down",
            self.agent_id,
        )
        # Signal the worker to stop gracefully.
        self._stop_event.set()
