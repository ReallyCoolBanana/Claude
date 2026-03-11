"""mmap-based shared state for multi-agent coordination.

Completely different from Prototype A's SQLite approach.  Uses memory-mapped
files with ``struct`` packing for binary state and ``fcntl.flock()`` for
critical sections.  Deliberately simpler but potentially more prone to races
than Prototype A's BEGIN IMMEDIATE — we want to compare failure modes.

Layout of ``shm/state.mmap``::

    ┌─────────────────────────────────────────────────┐
    │  Header (128 bytes)                             │
    │    magic (8B), version (4B), coordinator_idx    │
    │    (4B), agent_count (4B), phase (64B),         │
    │    padding (44B)                                │
    ├─────────────────────────────────────────────────┤
    │  Agent Slots  (32 × 256 bytes = 8192 bytes)     │
    │    Each: agent_id (64B), team (32B), role (16B) │
    │    pid (4B), status (4B), last_heartbeat (8B),  │
    │    registered_at (8B), padding (120B)           │
    ├─────────────────────────────────────────────────┤
    │  Rate-Limit Section (2048 bytes)                │
    │    16 endpoint slots × 128 bytes each           │
    │    Each: name (64B), max_calls (4B),            │
    │    window_secs (4B), call_count (4B),           │
    │    window_start (8B), padding (44B)             │
    └─────────────────────────────────────────────────┘

Total file size: 128 + 8192 + 2048 = 10368 bytes.
"""

from __future__ import annotations

import fcntl
import logging
import mmap
import os
import struct
import threading
import time
from dataclasses import dataclass
from typing import Optional

__all__ = ["SharedStateMap"]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_MAGIC = b"AGCOMM_B"  # 8 bytes
_VERSION = 1

_HEADER_SIZE = 128
_SLOT_SIZE = 256
_MAX_AGENTS = 32
_AGENTS_SECTION_SIZE = _MAX_AGENTS * _SLOT_SIZE

_RATE_SLOT_SIZE = 128
_MAX_RATE_SLOTS = 16
_RATE_SECTION_SIZE = _MAX_RATE_SLOTS * _RATE_SLOT_SIZE

_TOTAL_SIZE = _HEADER_SIZE + _AGENTS_SECTION_SIZE + _RATE_SECTION_SIZE

# Offsets
_AGENTS_OFFSET = _HEADER_SIZE
_RATE_OFFSET = _HEADER_SIZE + _AGENTS_SECTION_SIZE

# Header struct: magic(8s) version(I) coordinator_idx(i) agent_count(I) phase(64s)
# = 8 + 4 + 4 + 4 + 64 = 84 bytes, rest is padding
_HEADER_FMT = "<8sIiI64s"
_HEADER_PACK_SIZE = struct.calcsize(_HEADER_FMT)

# Agent slot struct:
#   agent_id(64s) team(32s) role(16s) pid(I) status(I) last_heartbeat(d) registered_at(d)
# = 64 + 32 + 16 + 4 + 4 + 8 + 8 = 136 bytes, rest is padding
_SLOT_FMT = "<64s32s16sIIdd"
_SLOT_PACK_SIZE = struct.calcsize(_SLOT_FMT)

# Rate-limit slot struct:
#   name(64s) max_calls(I) window_secs(I) call_count(I) window_start(d)
# = 64 + 4 + 4 + 4 + 8 = 84 bytes, rest is padding
_RATE_FMT = "<64sIIId"
_RATE_PACK_SIZE = struct.calcsize(_RATE_FMT)

# Agent status codes
_STATUS_EMPTY = 0
_STATUS_ALIVE = 1
_STATUS_DEAD = 2


# ---------------------------------------------------------------------------
# Helper dataclasses (read-only views)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentSlotView:
    """Read-only view of an agent slot."""
    slot_index: int
    agent_id: str
    team: str
    role: str
    pid: int
    status: int
    last_heartbeat: float
    registered_at: float


# ---------------------------------------------------------------------------
# SharedStateMap
# ---------------------------------------------------------------------------


class SharedStateMap:
    """mmap-based shared state for multi-agent coordination.

    Uses a memory-mapped file for shared memory between processes.
    Critical sections are protected by ``fcntl.flock()`` on the
    backing file descriptor.

    This is deliberately less robust than Prototype A's SQLite
    BEGIN IMMEDIATE — we want to see if race conditions manifest
    during testing.
    """

    def __init__(self, shm_dir: str) -> None:
        self.shm_dir = shm_dir
        os.makedirs(shm_dir, exist_ok=True)

        self._state_path = os.path.join(shm_dir, "state.mmap")
        self._fd: int | None = None
        self._mm: mmap.mmap | None = None
        self._thread_lock = threading.Lock()

        self._open()

    # -- lifecycle ----------------------------------------------------------

    def _open(self) -> None:
        """Open (or create) the mmap file and map it into memory.

        Uses O_EXCL for atomic creation detection to avoid a TOCTOU race
        where two processes both see the file as missing, both create it,
        and both attempt to initialize the header (potentially clobbering
        each other's writes).  Only the process that successfully creates
        the file via O_EXCL will initialize the header.
        """
        created = False
        try:
            self._fd = os.open(
                self._state_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL,
                0o644,
            )
            created = True
        except FileExistsError:
            self._fd = os.open(
                self._state_path,
                os.O_RDWR,
                0o644,
            )

        # Ensure file is the correct size.
        file_size = os.fstat(self._fd).st_size
        if file_size < _TOTAL_SIZE:
            os.ftruncate(self._fd, _TOTAL_SIZE)

        self._mm = mmap.mmap(self._fd, _TOTAL_SIZE)

        if created:
            self._init_header()

        # Validate magic on existing files.
        magic = self._mm[:8]
        if magic != _MAGIC:
            self._init_header()

    def _init_header(self) -> None:
        """Write a fresh header to the mmap."""
        with self._flock():
            header = struct.pack(
                _HEADER_FMT,
                _MAGIC,
                _VERSION,
                -1,  # coordinator_idx: -1 = no coordinator
                0,   # agent_count
                b"\x00" * 64,  # phase
            )
            self._mm[:_HEADER_PACK_SIZE] = header
            # Zero the rest.
            self._mm[_HEADER_PACK_SIZE:_TOTAL_SIZE] = (
                b"\x00" * (_TOTAL_SIZE - _HEADER_PACK_SIZE)
            )
            self._mm.flush()

    def close(self) -> None:
        """Unmap and close the backing file."""
        if self._mm is not None:
            try:
                self._mm.flush()
                self._mm.close()
            except (ValueError, BufferError):
                pass
            self._mm = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    # -- locking ------------------------------------------------------------

    class _lock:
        """Context manager for flock-based critical sections.

        Can be used as ``SharedStateMap._lock()`` (class-level) or
        as ``self._lock()`` (instance-level); the instance form is a
        convenience wrapper that captures ``self._fd``.
        """

        def __init__(self, fd: int | None = None) -> None:
            self._fd = fd

        def __enter__(self) -> None:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_EX)

        def __exit__(self, *exc) -> None:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)

    class _combined_lock:
        """Context manager that acquires both a threading.Lock and an flock."""

        def __init__(self, thread_lock: threading.Lock, fd: int | None) -> None:
            self._thread_lock = thread_lock
            self._fd = fd

        def __enter__(self) -> None:
            self._thread_lock.acquire()
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_EX)

        def __exit__(self, *exc) -> None:
            try:
                if self._fd is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                self._thread_lock.release()

    def _flock(self):
        """Return a context manager that holds both a thread lock and an exclusive flock."""
        return self._combined_lock(self._thread_lock, self._fd)

    # -- header helpers -----------------------------------------------------

    def _read_header(self) -> tuple:
        """Read the header fields."""
        raw = self._mm[:_HEADER_PACK_SIZE]
        magic, version, coord_idx, agent_count, phase_raw = struct.unpack(
            _HEADER_FMT, raw
        )
        phase = phase_raw.rstrip(b"\x00").decode("utf-8", errors="replace")
        return magic, version, coord_idx, agent_count, phase

    def _write_agent_count(self, count: int) -> None:
        """Update the agent_count field in the header.

        NOTE: agent_count is maintained for potential future optimization
        (e.g. short-circuiting slot scans) but is not currently used for
        slot scanning.  All scan methods (_find_slot_by_agent_id,
        _find_empty_slot, get_all_agents, get_dead_agents) iterate over
        all _MAX_AGENTS slots regardless of this counter.
        """
        # agent_count is at offset 8+4+4 = 16, size 4 (unsigned int).
        self._mm[16:20] = struct.pack("<I", count)

    def _write_coordinator_idx(self, idx: int) -> None:
        """Update the coordinator_slot_idx field in the header."""
        # coordinator_idx is at offset 8+4 = 12, size 4 (signed int).
        self._mm[12:16] = struct.pack("<i", idx)

    # -- agent slot helpers -------------------------------------------------

    def _slot_offset(self, slot: int) -> int:
        """Byte offset of agent slot *slot*."""
        return _AGENTS_OFFSET + slot * _SLOT_SIZE

    def _read_slot(self, slot: int) -> AgentSlotView:
        """Read an agent slot into an AgentSlotView."""
        off = self._slot_offset(slot)
        raw = self._mm[off : off + _SLOT_PACK_SIZE]
        aid_raw, team_raw, role_raw, pid, status, hb, reg = struct.unpack(
            _SLOT_FMT, raw
        )
        return AgentSlotView(
            slot_index=slot,
            agent_id=aid_raw.rstrip(b"\x00").decode("utf-8", errors="replace"),
            team=team_raw.rstrip(b"\x00").decode("utf-8", errors="replace"),
            role=role_raw.rstrip(b"\x00").decode("utf-8", errors="replace"),
            pid=pid,
            status=status,
            last_heartbeat=hb,
            registered_at=reg,
        )

    def _write_slot(
        self,
        slot: int,
        agent_id: str,
        team: str,
        role: str,
        pid: int,
        status: int,
        last_heartbeat: float,
        registered_at: float,
    ) -> None:
        """Write data into an agent slot."""
        off = self._slot_offset(slot)
        packed = struct.pack(
            _SLOT_FMT,
            agent_id.encode("utf-8")[:64].ljust(64, b"\x00"),
            team.encode("utf-8")[:32].ljust(32, b"\x00"),
            role.encode("utf-8")[:16].ljust(16, b"\x00"),
            pid,
            status,
            last_heartbeat,
            registered_at,
        )
        self._mm[off : off + _SLOT_PACK_SIZE] = packed

    def _find_slot_by_agent_id(self, agent_id: str) -> int | None:
        """Return the slot index for *agent_id*, or None."""
        _, _, _, agent_count, _ = self._read_header()
        for i in range(_MAX_AGENTS):
            sv = self._read_slot(i)
            if sv.status != _STATUS_EMPTY and sv.agent_id == agent_id:
                return i
        return None

    def _find_empty_slot(self) -> int | None:
        """Return the first empty slot index, or None if full."""
        for i in range(_MAX_AGENTS):
            sv = self._read_slot(i)
            if sv.status == _STATUS_EMPTY:
                return i
        return None

    # -- public API ---------------------------------------------------------

    def register_agent(
        self,
        agent_id: str,
        team: str,
        role: str,
        pid: int,
    ) -> int:
        """Register an agent in the shared state map.

        Returns the slot index assigned to this agent.

        Raises ``RuntimeError`` if all 32 slots are occupied.
        """
        now = time.time()
        with self._flock():
            # Re-register if already present.
            existing = self._find_slot_by_agent_id(agent_id)
            if existing is not None:
                self._write_slot(
                    existing, agent_id, team, role, pid,
                    _STATUS_ALIVE, now, self._read_slot(existing).registered_at,
                )
                if role == "coordinator":
                    self._write_coordinator_idx(existing)
                self._mm.flush()
                return existing

            slot = self._find_empty_slot()
            if slot is None:
                raise RuntimeError(
                    f"All {_MAX_AGENTS} agent slots are occupied; "
                    f"cannot register {agent_id!r}"
                )

            self._write_slot(
                slot, agent_id, team, role, pid,
                _STATUS_ALIVE, now, now,
            )
            _, _, _, agent_count, _ = self._read_header()
            self._write_agent_count(agent_count + 1)

            if role == "coordinator":
                self._write_coordinator_idx(slot)

            self._mm.flush()
            log.info(
                "Registered agent %s in slot %d (pid=%d, role=%s)",
                agent_id, slot, pid, role,
            )
            return slot

    def heartbeat(self, agent_id: str) -> bool:
        """Update the last_heartbeat timestamp for *agent_id*.

        Returns True if the agent was found and updated, False otherwise.
        """
        now = time.time()
        with self._flock():
            slot = self._find_slot_by_agent_id(agent_id)
            if slot is None:
                return False
            sv = self._read_slot(slot)
            self._write_slot(
                slot, sv.agent_id, sv.team, sv.role, sv.pid,
                _STATUS_ALIVE, now, sv.registered_at,
            )
            self._mm.flush()
        return True

    def get_heartbeat(self, agent_id: str) -> float | None:
        """Return the last_heartbeat timestamp for *agent_id*, or None if not found."""
        with self._flock():
            slot = self._find_slot_by_agent_id(agent_id)
            if slot is None:
                return None
            sv = self._read_slot(slot)
            return sv.last_heartbeat

    def get_dead_agents(self, timeout: float = 120.0) -> list[AgentSlotView]:
        """Scan all slots and return agents with stale heartbeats.

        An agent is considered dead if its ``last_heartbeat`` is older
        than *timeout* seconds and its status is still ``ALIVE``.
        """
        cutoff = time.time() - timeout
        dead: list[AgentSlotView] = []
        with self._flock():
            for i in range(_MAX_AGENTS):
                sv = self._read_slot(i)
                if sv.status == _STATUS_ALIVE and sv.last_heartbeat < cutoff:
                    dead.append(sv)
        return dead

    def mark_agent_dead(self, agent_id: str) -> bool:
        """Mark an agent as dead. Returns True if found."""
        with self._flock():
            slot = self._find_slot_by_agent_id(agent_id)
            if slot is None:
                return False
            sv = self._read_slot(slot)
            self._write_slot(
                slot, sv.agent_id, sv.team, sv.role, sv.pid,
                _STATUS_DEAD, sv.last_heartbeat, sv.registered_at,
            )
            self._mm.flush()
        return True

    def unregister_agent(self, agent_id: str) -> bool:
        """Remove an agent from the shared state (free the slot).

        Returns True if the agent was found and removed.
        """
        with self._flock():
            slot = self._find_slot_by_agent_id(agent_id)
            if slot is None:
                return False
            # Zero out the slot.
            off = self._slot_offset(slot)
            self._mm[off : off + _SLOT_SIZE] = b"\x00" * _SLOT_SIZE

            _, _, coord_idx, agent_count, _ = self._read_header()
            if agent_count > 0:
                self._write_agent_count(agent_count - 1)
            if coord_idx == slot:
                self._write_coordinator_idx(-1)

            self._mm.flush()
        return True

    def get_all_agents(self) -> list[AgentSlotView]:
        """Return all non-empty agent slots."""
        agents: list[AgentSlotView] = []
        with self._flock():
            for i in range(_MAX_AGENTS):
                sv = self._read_slot(i)
                if sv.status != _STATUS_EMPTY:
                    agents.append(sv)
        return agents

    def set_phase(self, phase: str) -> None:
        """Set the current coordination phase in the header."""
        with self._flock():
            phase_bytes = phase.encode("utf-8")[:64].ljust(64, b"\x00")
            # phase field starts at offset 20 in the header.
            self._mm[20:84] = phase_bytes
            self._mm.flush()

    def get_phase(self) -> str:
        """Read the current coordination phase from the header."""
        _, _, _, _, phase = self._read_header()
        return phase

    # -- rate limiting (intentionally racy) ---------------------------------

    def _rate_slot_offset(self, slot: int) -> int:
        """Byte offset of rate-limit slot *slot*."""
        return _RATE_OFFSET + slot * _RATE_SLOT_SIZE

    def _read_rate_slot(self, slot: int) -> tuple[str, int, int, int, float]:
        """Read a rate-limit slot: (name, max_calls, window_secs, count, window_start)."""
        off = self._rate_slot_offset(slot)
        raw = self._mm[off : off + _RATE_PACK_SIZE]
        name_raw, max_calls, window_secs, count, window_start = struct.unpack(
            _RATE_FMT, raw
        )
        name = name_raw.rstrip(b"\x00").decode("utf-8", errors="replace")
        return name, max_calls, window_secs, count, window_start

    def _write_rate_slot(
        self,
        slot: int,
        name: str,
        max_calls: int,
        window_secs: int,
        count: int,
        window_start: float,
    ) -> None:
        """Write a rate-limit slot."""
        off = self._rate_slot_offset(slot)
        packed = struct.pack(
            _RATE_FMT,
            name.encode("utf-8")[:64].ljust(64, b"\x00"),
            max_calls,
            window_secs,
            count,
            window_start,
        )
        self._mm[off : off + _RATE_PACK_SIZE] = packed

    def _find_rate_slot(self, api_endpoint: str) -> int | None:
        """Find the rate-limit slot for *api_endpoint*."""
        for i in range(_MAX_RATE_SLOTS):
            name, _, _, _, _ = self._read_rate_slot(i)
            if name == api_endpoint:
                return i
        return None

    def _find_empty_rate_slot(self) -> int | None:
        """Find the first empty rate-limit slot."""
        for i in range(_MAX_RATE_SLOTS):
            name, _, _, _, _ = self._read_rate_slot(i)
            if not name:
                return i
        return None

    def configure_rate_limit(
        self, api_endpoint: str, max_calls: int, window_seconds: int
    ) -> None:
        """Configure a rate limit for an API endpoint.

        Creates or updates a rate-limit slot.
        """
        with self._flock():
            slot = self._find_rate_slot(api_endpoint)
            if slot is None:
                slot = self._find_empty_rate_slot()
                if slot is None:
                    raise RuntimeError(
                        f"All {_MAX_RATE_SLOTS} rate-limit slots are occupied"
                    )
            self._write_rate_slot(
                slot, api_endpoint, max_calls, window_seconds, 0, time.time()
            )
            self._mm.flush()

    def reserve_api_call(self, api_endpoint: str, agent_id: str) -> bool:
        """Attempt to reserve an API call slot.

        Returns True if the call is allowed under the rate limit, False
        if the limit has been reached.

        **NOTE**: This uses ``fcntl.flock()`` which is intentionally less
        robust than Prototype A's ``BEGIN IMMEDIATE``.  The read-modify-write
        cycle has a deliberate window where races can manifest — that is the
        point: we want to compare failure modes under concurrent access.
        """
        now = time.time()
        with self._flock():
            slot = self._find_rate_slot(api_endpoint)
            if slot is None:
                # No config — allow by default (no tracking).
                log.debug(
                    "No rate-limit config for %s, allowing call from %s",
                    api_endpoint, agent_id,
                )
                return True

            name, max_calls, window_secs, count, window_start = (
                self._read_rate_slot(slot)
            )

            # If the window has expired, reset the counter.
            if now - window_start >= window_secs:
                count = 0
                window_start = now

            if count >= max_calls:
                log.debug(
                    "Rate limit reached for %s: %d/%d in window",
                    api_endpoint, count, max_calls,
                )
                return False

            # Increment counter and write back.
            self._write_rate_slot(
                slot, name, max_calls, window_secs, count + 1, window_start
            )
            self._mm.flush()

        return True

    # -- repr ---------------------------------------------------------------

    def __repr__(self) -> str:
        return f"SharedStateMap(path={self._state_path!r})"
