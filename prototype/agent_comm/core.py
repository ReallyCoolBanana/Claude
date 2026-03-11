"""Core infrastructure for multi-agent communication.

Provides directory resolution, agent identity, configuration, and a
one-call setup function.  Uses only the Python standard library.

Bug-fix references
------------------
- SB-1 / SB-2: worktree split-brain caused by unsymlinked paths.
  Fixed by canonicalising every path through ``os.path.realpath()``.
- EN-1 / EN-4 / EN-5: environment validation gaps (write perms,
  tmpfiles cleanup, NFS staleness).  Fixed by startup checks in
  ``CommDir.validate()``.
"""

from __future__ import annotations

import logging
import os
import stat
import warnings
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "CommDir",
    "AgentIdentity",
    "CommConfig",
    "setup_comm_environment",
]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENV_VAR = "AGENT_COMM_DIR"
_DEFAULT_DIR = os.path.join("~", ".claude-agent-comm")
_DIR_PERMS = 0o755
_SUBDIRS = ("bus", "db")  # bus/ = JSONL channels, db/ = SQLite databases


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CommDirError(Exception):
    """Raised when the communication directory fails validation."""


# ---------------------------------------------------------------------------
# CommDir — resolve and validate the shared communication directory
# ---------------------------------------------------------------------------


class CommDir:
    """Resolves, creates, and validates the shared communication directory.

    Resolution order:
        1. ``AGENT_COMM_DIR`` environment variable.
        2. ``~/.claude-agent-comm`` (default).

    The resolved path is always canonicalised with ``os.path.realpath()`` to
    prevent split-brain issues across git worktrees (SB-1 / SB-2).

    Startup validation (EN-1, EN-4, EN-5):
        * Directory must be writable by the current user.
        * Paths under ``/tmp`` are rejected (systemd-tmpfiles may purge them).
        * NFS mounts are detected via ``os.statvfs`` and emit a warning.
        * The canonical path must match the requested path (no symlink
          confusion).
    """

    def __init__(self, requested_path: str | None = None) -> None:
        raw = requested_path or os.environ.get(_ENV_VAR) or _DEFAULT_DIR
        self._requested: str = os.path.expanduser(raw)
        self._path: str = os.path.realpath(self._requested)

    # -- public properties --------------------------------------------------

    @property
    def path(self) -> str:
        """Canonical, absolute path to the communication directory."""
        return self._path

    @property
    def bus_dir(self) -> str:
        """Path to the ``bus/`` subdirectory (JSONL message channels)."""
        return os.path.join(self._path, "bus")

    @property
    def db_dir(self) -> str:
        """Path to the ``db/`` subdirectory (SQLite databases)."""
        return os.path.join(self._path, "db")

    # -- lifecycle ----------------------------------------------------------

    def ensure_dirs(self) -> None:
        """Create the communication directory tree if it does not exist.

        Creates the root directory and the ``bus/`` and ``db/``
        subdirectories, all with ``0o755`` permissions.
        """
        for subdir in ("", *_SUBDIRS):
            dirpath = os.path.join(self._path, subdir)
            os.makedirs(dirpath, mode=_DIR_PERMS, exist_ok=True)
        log.debug("Communication directories ensured at %s", self._path)

    def validate(self) -> None:
        """Run all startup validation checks.

        Raises
        ------
        CommDirError
            If any hard requirement is violated.

        Warns
        -----
        UserWarning
            If the directory resides on an NFS mount (non-blocking).
        """
        self._check_no_tmp()
        self._check_canonical_match()
        self._check_writable()
        self._check_nfs()
        log.debug("CommDir validation passed for %s", self._path)

    # -- internal checks ----------------------------------------------------

    def _check_no_tmp(self) -> None:
        """Reject paths under ``/tmp`` (EN-4: systemd-tmpfiles cleanup)."""
        if self._requested.startswith("/tmp") or self._path.startswith("/tmp"):
            raise CommDirError(
                f"Communication directory must not reside under /tmp "
                f"(systemd-tmpfiles may purge it): {self._path}"
            )

    def _check_canonical_match(self) -> None:
        """Verify the canonical path matches the requested path (SB-1/SB-2).

        A mismatch indicates an intermediate symlink, which can cause
        split-brain when multiple worktrees resolve different physical
        paths.
        """
        if os.path.realpath(self._requested) != self._path:
            raise CommDirError(
                f"Canonical path mismatch — requested {self._requested!r} "
                f"resolves to {self._path!r}.  Remove the intermediate "
                f"symlink to avoid split-brain."
            )

    def _check_writable(self) -> None:
        """Ensure the directory is writable by the current process (EN-1)."""
        if not os.path.isdir(self._path):
            raise CommDirError(
                f"Communication directory does not exist: {self._path}"
            )
        # os.access() always returns True for root, so check permission bits
        # directly when running as root (euid == 0).
        if os.geteuid() == 0:
            st = os.stat(self._path)
            if not (st.st_mode & 0o200):
                raise CommDirError(
                    f"Communication directory is not writable: {self._path}"
                )
        elif not os.access(self._path, os.W_OK):
            raise CommDirError(
                f"Communication directory is not writable: {self._path}"
            )

    def _check_nfs(self) -> None:
        """Warn (do not block) if the directory is on NFS (EN-5).

        Detection uses ``os.statvfs`` where available.  NFS is heuristically
        identified by the ``f_type`` field on Linux (``0x6969`` = NFS_SUPER_MAGIC).
        On platforms without ``statvfs`` the check is silently skipped.
        """
        try:
            vfs = os.statvfs(self._path)
        except (AttributeError, OSError):
            # statvfs unavailable or inaccessible — skip silently.
            return

        # Linux exposes f_type via os.statvfs; CPython maps it but the
        # attribute may be absent on other Unixes.
        f_type = getattr(vfs, "f_type", None)
        if f_type is None:
            return

        # NFS_SUPER_MAGIC = 0x6969, NFS can also appear as 0x6E667364.
        _NFS_MAGIC = {0x6969, 0x6E667364}
        if f_type in _NFS_MAGIC:
            warnings.warn(
                f"Communication directory appears to be on NFS "
                f"(f_type=0x{f_type:x}).  File-locking semantics may be "
                f"unreliable: {self._path}",
                stacklevel=2,
            )

    # -- dunder -------------------------------------------------------------

    def __repr__(self) -> str:
        return f"CommDir(path={self._path!r})"

    def __str__(self) -> str:
        return self._path


# ---------------------------------------------------------------------------
# AgentIdentity — lightweight identity for a single agent
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Immutable identity of a running agent.

    Parameters
    ----------
    agent_id:
        Unique identifier for this agent (e.g. ``"team-0018-worker-a"``).
    team:
        Team label (e.g. ``"TEAM-0018"``).
    role:
        Agent role — typically ``"coordinator"`` or ``"worker"``.
    pid:
        OS process ID.  Defaults to ``os.getpid()`` at construction time.
    """

    agent_id: str
    team: str
    role: str
    pid: int = field(default_factory=os.getpid)

    def __post_init__(self) -> None:
        if not self.agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if not self.team:
            raise ValueError("team must be a non-empty string")
        if self.role not in ("coordinator", "worker"):
            raise ValueError(
                f"role must be 'coordinator' or 'worker', got {self.role!r}"
            )


# ---------------------------------------------------------------------------
# CommConfig — tunable parameters for the communication layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommConfig:
    """Configuration knobs for the communication subsystem.

    All timing values are in **seconds** unless otherwise noted.

    Parameters
    ----------
    comm_dir:
        Canonical path to the shared communication directory.
    heartbeat_interval:
        How often agents publish a heartbeat (seconds).
    poll_interval:
        How often message channels are polled (seconds, 150 ms default).
    message_max_bytes:
        Maximum size of a single serialised message.  Kept under the 4096-
        byte POSIX atomic-write limit.
    busy_timeout_ms:
        SQLite busy-wait timeout in **milliseconds**.
    coordinator_timeout:
        Maximum wall-clock time a coordinator waits for a full round of
        worker responses (seconds, 5 min default).
    dead_agent_timeout:
        Time after which an agent with no heartbeat is considered dead
        (seconds, 2 min default).
    """

    comm_dir: str
    heartbeat_interval: float = 30.0
    poll_interval: float = 0.15
    message_max_bytes: int = 4000
    busy_timeout_ms: int = 30_000
    coordinator_timeout: float = 300.0
    dead_agent_timeout: float = 120.0

    def __post_init__(self) -> None:
        if self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if self.message_max_bytes <= 0:
            raise ValueError("message_max_bytes must be positive")
        if self.message_max_bytes > 4096:
            raise ValueError(
                "message_max_bytes must not exceed 4096 (POSIX atomic-write limit)"
            )


# ---------------------------------------------------------------------------
# setup_comm_environment — one-call bootstrap
# ---------------------------------------------------------------------------


def setup_comm_environment(
    agent_id: str,
    team: str,
    role: str,
    *,
    comm_dir_path: str | None = None,
) -> tuple[CommDir, AgentIdentity, CommConfig]:
    """Bootstrap the communication environment in a single call.

    1. Resolves and validates the communication directory.
    2. Creates the agent identity.
    3. Returns a default ``CommConfig`` bound to the resolved directory.

    Parameters
    ----------
    agent_id:
        Unique identifier for this agent.
    team:
        Team label.
    role:
        ``"coordinator"`` or ``"worker"``.
    comm_dir_path:
        Optional explicit path; overrides the ``AGENT_COMM_DIR`` env var.

    Returns
    -------
    tuple[CommDir, AgentIdentity, CommConfig]
        The validated directory handle, agent identity, and default config.

    Raises
    ------
    CommDirError
        If directory validation fails.
    ValueError
        If identity or config parameters are invalid.
    """
    comm = CommDir(requested_path=comm_dir_path)
    comm.ensure_dirs()
    comm.validate()

    identity = AgentIdentity(agent_id=agent_id, team=team, role=role)
    config = CommConfig(comm_dir=comm.path)

    log.info(
        "Agent %s (%s/%s, pid=%d) initialised comm at %s",
        identity.agent_id,
        identity.team,
        identity.role,
        identity.pid,
        comm.path,
    )

    return comm, identity, config
