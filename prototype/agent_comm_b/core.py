"""Core infrastructure for Prototype B multi-agent communication.

Same CommDir concept as Prototype A but configured for Prototype B's
mmap + named-pipe approach.  Subdirectories are ``pipes/`` and ``shm/``
instead of ``bus/`` and ``db/``.

Bug-fix references (shared with Prototype A):
- SB-1 / SB-2: worktree split-brain fixed by ``os.path.realpath()``
- EN-1 / EN-4 / EN-5: env validation (write perms, reject /tmp, NFS)
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field

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
_SUBDIRS = ("pipes", "shm")  # pipes/ = FIFOs, shm/ = mmap files


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CommDirError(Exception):
    """Raised when the communication directory fails validation."""


# ---------------------------------------------------------------------------
# CommDir
# ---------------------------------------------------------------------------


class CommDir:
    """Resolves, creates, and validates the shared communication directory.

    Resolution order:
        1. ``AGENT_COMM_DIR`` environment variable.
        2. ``~/.claude-agent-comm`` (default).

    The resolved path is always canonicalised with ``os.path.realpath()``
    to prevent split-brain issues across git worktrees (SB-1 / SB-2).

    Startup validation (EN-1, EN-4, EN-5):
        * Directory must be writable by the current user.
        * Paths under ``/tmp`` are rejected (systemd-tmpfiles may purge).
        * The canonical path must match the requested path.
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
    def pipes_dir(self) -> str:
        """Path to the ``pipes/`` subdirectory (named FIFOs + spill files)."""
        return os.path.join(self._path, "pipes")

    @property
    def shm_dir(self) -> str:
        """Path to the ``shm/`` subdirectory (mmap shared state files)."""
        return os.path.join(self._path, "shm")

    # -- lifecycle ----------------------------------------------------------

    def ensure_dirs(self) -> None:
        """Create the communication directory tree if it does not exist."""
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
        """Reject paths under ``/tmp`` (EN-4: systemd-tmpfiles cleanup).

        Checks both the canonical path and the originally-requested path
        so that symlinks pointing into /tmp are also caught.
        """
        for p in (self._path, self._requested):
            if p.startswith("/tmp"):
                raise CommDirError(
                    f"Communication directory must not reside under /tmp "
                    f"(systemd-tmpfiles may purge it): {p}"
                )

    def _check_canonical_match(self) -> None:
        """Verify canonical path matches requested path (SB-1/SB-2)."""
        if self._requested != self._path:
            raise CommDirError(
                f"Canonical path mismatch -- requested {self._requested!r} "
                f"resolves to {self._path!r}.  Remove the intermediate "
                f"symlink to avoid split-brain."
            )

    def _check_writable(self) -> None:
        """Ensure the directory is writable (EN-1)."""
        if not os.path.isdir(self._path):
            raise CommDirError(
                f"Communication directory does not exist: {self._path}"
            )
        if not os.access(self._path, os.W_OK):
            raise CommDirError(
                f"Communication directory is not writable: {self._path}"
            )

    def _check_nfs(self) -> None:
        """Warn (do not block) if the directory is on NFS (EN-5)."""
        try:
            vfs = os.statvfs(self._path)
        except (AttributeError, OSError):
            return

        f_type = getattr(vfs, "f_type", None)
        if f_type is None:
            return

        _NFS_MAGIC = {0x6969, 0x6E667364}
        if f_type in _NFS_MAGIC:
            warnings.warn(
                f"Communication directory appears to be on NFS "
                f"(f_type=0x{f_type:x}).  File-locking semantics may be "
                f"unreliable: {self._path}",
                stacklevel=2,
            )

    def __repr__(self) -> str:
        return f"CommDir(path={self._path!r})"

    def __str__(self) -> str:
        return self._path


# ---------------------------------------------------------------------------
# AgentIdentity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Immutable identity of a running agent."""

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
# CommConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommConfig:
    """Configuration knobs for the Prototype B communication subsystem.

    All timing values are in seconds unless otherwise noted.
    """

    comm_dir: str
    heartbeat_interval: float = 30.0
    poll_interval: float = 0.15
    message_max_bytes: int = 4000
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
                "message_max_bytes must not exceed 4096 (PIPE_BUF atomic guarantee)"
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
    3. Returns a default CommConfig bound to the resolved directory.
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
