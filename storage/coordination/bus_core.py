"""Canonical shared primitives for the Proto A coordination bus and SQLite layer.

This module extracts commonly duplicated patterns from across the coordination
codebase into a single, well-tested source of truth.  Other modules should
import from here rather than defining their own copies.

Extracted patterns
------------------
- ``retry_on_busy`` -- decorator for SQLITE_BUSY resilience (was defined 3+
  times independently in help_protocol, direct_channels, coordinator_hub,
  work_stealing).
- ``init_db`` -- standard SQLite WAL-mode connection setup (was duplicated 5+
  times with minor variations).
- ``bus_write`` -- POSIX-atomic JSONL bus append (was duplicated 6+ times).
- ``bus_read`` -- binary-mode bus reader with correct byte offsets (was
  duplicated 3+ times, some with text-mode bugs).
- ``sanitize_channel`` -- strict regex channel-name sanitizer (was
  inconsistent: some used ``re.sub``, others used ``str.replace``).
- ``VALID_MSG_TYPES`` -- canonical set of valid message types (was redefined
  in multiple modules).
- ``is_busy_or_locked`` -- helper to detect SQLITE_BUSY / SQLITE_LOCKED
  errors portably across Python versions.

All functions include comprehensive docstrings and type hints.  Only uses
the Python standard library.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_MSG_TYPES: frozenset[str] = frozenset({
    "info",
    "blocker",
    "phase-signal",
    "heartbeat",
    "request",
    "response",
})
"""Canonical set of valid message types accepted by the JSONL bus.

Modules should validate against this set rather than defining their own.
"""

MAX_MESSAGE_BYTES: int = 4096
"""Maximum size (in bytes) for a single serialized JSONL bus message.

Messages under this threshold are guaranteed to be written atomically on
POSIX systems via a single ``os.write`` call.
"""

DEFAULT_MAX_RETRIES: int = 5
"""Default number of retry attempts for the ``retry_on_busy`` decorator."""

DEFAULT_RETRY_BACKOFF: float = 0.1
"""Default initial backoff delay (seconds) for the ``retry_on_busy`` decorator.

Doubles after each failed attempt (exponential backoff).
"""

DEFAULT_BUSY_TIMEOUT_MS: int = 5000
"""Default SQLite busy_timeout in milliseconds for ``init_db``."""

# Type variable for generic decorator typing
F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Channel sanitization
# ---------------------------------------------------------------------------

def sanitize_channel(name: str) -> str:
    """Sanitize a channel name for safe use as a filesystem path component.

    Applies a strict regex allowlist, replacing any character that is not
    alphanumeric, underscore, or hyphen with an underscore.  This prevents
    path-traversal attacks (``../``) and avoids filesystem-unsafe characters.

    Parameters
    ----------
    name:
        The raw channel name to sanitize.

    Returns
    -------
    str
        The sanitized channel name containing only ``[a-zA-Z0-9_-]``.

    Examples
    --------
    >>> sanitize_channel("team-1/global")
    'team-1_global'
    >>> sanitize_channel("direct..attack")
    'direct__attack'
    >>> sanitize_channel("normal_channel-1")
    'normal_channel-1'
    """
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)


# ---------------------------------------------------------------------------
# SQLITE_BUSY detection
# ---------------------------------------------------------------------------

def is_busy_or_locked(exc: sqlite3.OperationalError) -> bool:
    """Check if a ``sqlite3.OperationalError`` is SQLITE_BUSY or SQLITE_LOCKED.

    Uses the ``sqlite_errorcode`` attribute (Python 3.11+) when available,
    falling back to string matching for older Python versions.

    Parameters
    ----------
    exc:
        The caught ``sqlite3.OperationalError`` to inspect.

    Returns
    -------
    bool
        True if the error is SQLITE_BUSY (5) or SQLITE_LOCKED (6),
        including extended error codes (e.g. SQLITE_BUSY_SNAPSHOT = 517).
    """
    # Python 3.11+ exposes the SQLite error code directly
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        base_code = code & 0xFF
        return base_code in (5, 6)  # SQLITE_BUSY=5, SQLITE_LOCKED=6
    # Fallback for older Python versions
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry_on_busy(
    func: F | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_RETRY_BACKOFF,
) -> F | Callable[[F], F]:
    """Decorator: retry a function/method on SQLITE_BUSY / SQLITE_LOCKED.

    Implements exponential backoff, starting at *initial_delay* and doubling
    after each retry.  Only ``sqlite3.OperationalError`` exceptions that
    indicate BUSY or LOCKED are caught; all other exceptions propagate
    immediately.

    Can be used with or without parentheses::

        @retry_on_busy
        def my_func(): ...

        @retry_on_busy(max_retries=10, initial_delay=0.2)
        def my_func(): ...

    Parameters
    ----------
    func:
        The function to decorate (when used without parentheses).
    max_retries:
        Maximum number of attempts before re-raising the last error.
    initial_delay:
        Initial backoff delay in seconds.  Doubles after each retry.

    Returns
    -------
    Callable
        The decorated function, or a decorator if called with keyword args.

    Raises
    ------
    sqlite3.OperationalError
        Re-raised after *max_retries* exhausted, or for non-busy errors.
    """
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_err: sqlite3.OperationalError | None = None
            for attempt in range(max_retries):
                try:
                    return fn(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if is_busy_or_locked(e):
                        last_err = e
                        logger.debug(
                            "SQLITE_BUSY on %s (attempt %d/%d), retrying in %.2fs",
                            fn.__name__, attempt + 1, max_retries, delay,
                        )
                        time.sleep(delay)
                        delay *= 2
                    else:
                        raise
            raise last_err  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]

    # Support both @retry_on_busy and @retry_on_busy(...)
    if func is not None:
        return decorator(func)
    return decorator


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

def init_db(
    path: str,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    row_factory: Any = sqlite3.Row,
) -> sqlite3.Connection:
    """Open a SQLite connection with standard WAL-mode configuration.

    This is the canonical way to create a SQLite connection in the
    coordination layer.  It ensures:

    - Parent directories are created automatically.
    - WAL journal mode is enabled for concurrent read/write access.
    - ``busy_timeout`` is set to avoid immediate SQLITE_BUSY failures.
    - ``check_same_thread=False`` allows sharing across threads (callers
      must still provide their own locking).
    - ``row_factory`` defaults to ``sqlite3.Row`` for dict-like access.

    Parameters
    ----------
    path:
        Filesystem path to the SQLite database file.
    busy_timeout_ms:
        SQLite busy timeout in milliseconds.  The default (5000 ms) is
        suitable for most coordination workloads.
    row_factory:
        Row factory for the connection.  Defaults to ``sqlite3.Row``.

    Returns
    -------
    sqlite3.Connection
        A configured, ready-to-use connection.

    Notes
    -----
    Both ``sqlite3.connect(timeout=...)`` and ``PRAGMA busy_timeout`` are
    set intentionally.  The ``connect`` timeout governs Python-level retry
    behavior inside the ``sqlite3`` module, while the PRAGMA controls
    SQLite's internal busy handler.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=busy_timeout_ms / 1000,
        check_same_thread=False,
    )
    conn.row_factory = row_factory
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


# ---------------------------------------------------------------------------
# Bus write
# ---------------------------------------------------------------------------

def bus_write(
    bus_dir: str,
    channel: str,
    msg_type: str,
    body: dict,
    team: str,
    agent_id: str,
    *,
    ttl: int = 3600,
) -> str | None:
    """Write a message to the JSONL bus with POSIX atomic semantics.

    The message is JSON-serialized, newline-terminated, and appended to the
    channel file in a single ``os.write`` call.  On POSIX systems, writes
    under ``PIPE_BUF`` (4096 bytes) are guaranteed atomic, preventing
    interleaved messages from concurrent writers.

    Channel names are sanitized via ``sanitize_channel`` before use as
    filenames.  The bus directory is created automatically if it does not
    exist.

    Parameters
    ----------
    bus_dir:
        Path to the JSONL bus directory.
    channel:
        Logical channel name (will be sanitized for filesystem safety).
    msg_type:
        Message type; must be one of ``VALID_MSG_TYPES``.
    body:
        Message payload as a JSON-serializable dict.
    team:
        Team identifier of the sender.
    agent_id:
        Agent identifier of the sender.
    ttl:
        Time-to-live in seconds.  Readers should ignore messages whose
        ``ts + ttl < now``.

    Returns
    -------
    str or None
        The generated message UUID on success, or ``None`` if the write
        failed (errors are logged but not raised, since the caller's DB
        transaction has typically already committed).

    Raises
    ------
    ValueError
        If the serialized message exceeds ``MAX_MESSAGE_BYTES``.
    """
    try:
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
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError(
                f"Serialized message is {len(raw)} bytes, "
                f"exceeds {MAX_MESSAGE_BYTES} byte limit"
            )
        safe_ch = sanitize_channel(channel)
        filepath = os.path.join(bus_dir, f"{safe_ch}.jsonl")
        os.makedirs(bus_dir, exist_ok=True)
        fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
        return msg_id
    except ValueError:
        raise  # Re-raise size limit errors
    except Exception as exc:
        logger.error("Bus write failed on channel %s: %s", channel, exc)
        return None


# ---------------------------------------------------------------------------
# Bus read
# ---------------------------------------------------------------------------

def bus_read(
    bus_dir: str,
    channel: str,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Read messages from a JSONL bus channel file.

    Uses binary mode (``'rb'``) for consistent byte-offset tracking
    regardless of Unicode content.  Only advances the offset past complete
    lines that parse as valid JSON.  If the file ends with a partial
    (incomplete) line, the offset is left before that line so it can be
    re-read once the concurrent writer finishes.

    Expired messages (where ``ts + ttl < now``) are silently skipped.

    Parameters
    ----------
    bus_dir:
        Path to the JSONL bus directory.
    channel:
        Logical channel name (will be sanitized for filesystem safety).
    offset:
        Byte offset to start reading from.  Pass 0 to read from the
        beginning, or the ``new_offset`` returned by a previous call
        to resume.

    Returns
    -------
    tuple[list[dict], int]
        A 2-tuple of ``(messages, new_offset)`` where *messages* is a
        list of parsed, non-expired message dicts and *new_offset* is
        the byte position to pass as *offset* on the next call.
    """
    safe_ch = sanitize_channel(channel)
    filepath = os.path.join(bus_dir, f"{safe_ch}.jsonl")
    if not os.path.exists(filepath):
        return [], 0

    with open(filepath, "rb") as f:
        f.seek(offset)
        data = f.read()

    if not data:
        return [], offset

    msgs: list[dict] = []
    now = time.time()
    consumed_bytes = 0
    lines = data.split(b"\n")

    for i, raw_line in enumerate(lines):
        line_len = len(raw_line)
        # Account for the \n separator between splits (not after the last)
        sep_len = 1 if i < len(lines) - 1 else 0

        if not raw_line.strip():
            consumed_bytes += line_len + sep_len
            continue

        try:
            line_str = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            # Corrupt data; skip if it's a complete line
            if i == len(lines) - 1 and not data.endswith(b"\n"):
                break  # Partial line at end -- don't advance
            consumed_bytes += line_len + sep_len
            continue

        try:
            m = json.loads(line_str)
        except json.JSONDecodeError:
            # If this is the last chunk and doesn't end with \n, it's a
            # partial line -- don't advance past it.
            if i == len(lines) - 1 and not data.endswith(b"\n"):
                break
            # Otherwise it's a corrupt complete line; skip over it.
            consumed_bytes += line_len + sep_len
            continue

        # Only include non-expired messages
        if m.get("ts", 0) + m.get("ttl", 300) > now:
            msgs.append(m)
        consumed_bytes += line_len + sep_len

    new_offset = offset + consumed_bytes
    return msgs, new_offset
