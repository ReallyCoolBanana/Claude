"""Named-pipe (FIFO) based message bus for inter-agent communication.

Completely different from Prototype A's JSONL append approach.  Each channel
maps to a named pipe: ``pipes/{channel}.fifo``.  Pipes provide natural
ordering and backpressure.  Read data is consumed (removed), so there is no
need for offset tracking.

Fallback: when no reader has the FIFO open, writes go to a spillover file
``pipes/{channel}.spill`` so messages are not lost.

Messages are JSON, kept under 4096 bytes for the PIPE_BUF atomic guarantee.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import stat
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

__all__ = [
    "Message",
    "PipeBusWriter",
    "PipeBusReader",
]

log = logging.getLogger(__name__)

VALID_MSG_TYPES = frozenset({
    "info", "blocker", "phase-signal", "heartbeat", "request", "response",
})

MAX_MESSAGE_BYTES = 4096


# ---------------------------------------------------------------------------
# Message — same schema as Prototype A for cross-prototype compatibility
# ---------------------------------------------------------------------------


@dataclass
class Message:
    """A single message on the bus.

    Schema is identical to Prototype A so the two prototypes can be compared
    with the same test harness.
    """

    id: str
    type: str
    channel: str
    team: str
    agent_id: str
    ts: float
    ttl: int = 300
    body: dict = field(default_factory=dict)
    in_reply_to: Optional[str] = None

    def to_json_line(self) -> bytes:
        """Serialize to a newline-terminated JSON bytes line."""
        return (
            json.dumps(asdict(self), separators=(",", ":")).encode("utf-8")
            + b"\n"
        )

    @classmethod
    def from_json_line(cls, line: str) -> Message:
        """Deserialize from a single JSON line."""
        d = json.loads(line)
        return cls(**d)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_channel(channel: str) -> str:
    """Make a channel name filesystem-safe."""
    return channel.replace("/", "_").replace("..", "_")


def _ensure_fifo(path: str) -> None:
    """Create a named pipe (FIFO) at *path* if it does not already exist."""
    if os.path.exists(path):
        if stat.S_ISFIFO(os.stat(path).st_mode):
            return
        # Not a FIFO — remove and recreate (stale regular file, etc.)
        os.unlink(path)
    os.mkfifo(path, 0o644)


# ---------------------------------------------------------------------------
# PipeBusWriter
# ---------------------------------------------------------------------------


class PipeBusWriter:
    """Write messages to a named-pipe channel.

    If no reader has the FIFO open (ENXIO on non-blocking open), messages
    fall back to a spillover file so they are not lost.
    """

    def __init__(self, pipes_dir: str, agent_id: str, team: str) -> None:
        self.pipes_dir = pipes_dir
        self.agent_id = agent_id
        self.team = team
        os.makedirs(pipes_dir, exist_ok=True)

    def _fifo_path(self, channel: str) -> str:
        return os.path.join(self.pipes_dir, f"{_sanitize_channel(channel)}.fifo")

    def _spill_path(self, channel: str) -> str:
        return os.path.join(self.pipes_dir, f"{_sanitize_channel(channel)}.spill")

    def publish(
        self,
        channel: str,
        msg_type: str,
        body: dict,
        ttl: int = 300,
        in_reply_to: Optional[str] = None,
    ) -> Message:
        """Publish a message to *channel*.

        Tries the FIFO first (non-blocking).  Falls back to the spillover
        file when no reader is attached.
        """
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(
                f"Invalid message type {msg_type!r}; "
                f"must be one of {VALID_MSG_TYPES}"
            )

        msg = Message(
            id=str(uuid.uuid4()),
            type=msg_type,
            channel=channel,
            team=self.team,
            agent_id=self.agent_id,
            ts=time.time(),
            ttl=ttl,
            body=body,
            in_reply_to=in_reply_to,
        )

        raw = msg.to_json_line()
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError(
                f"Serialized message is {len(raw)} bytes, "
                f"exceeds {MAX_MESSAGE_BYTES} byte PIPE_BUF limit"
            )

        fifo = self._fifo_path(channel)
        _ensure_fifo(fifo)

        # Attempt non-blocking write to the FIFO.
        written = False
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, raw)
                written = True
            finally:
                os.close(fd)
        except OSError as exc:
            # ENXIO = no reader on the FIFO; EAGAIN = pipe full.
            if exc.errno not in (errno.ENXIO, errno.EAGAIN):
                raise
            log.debug(
                "FIFO write failed (errno %d) for channel %s, spilling",
                exc.errno, channel,
            )

        # Fallback: append to spillover file so messages are not lost.
        if not written:
            spill = self._spill_path(channel)
            fd = os.open(
                spill,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o644,
            )
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)

        return msg


# ---------------------------------------------------------------------------
# PipeBusReader
# ---------------------------------------------------------------------------


class PipeBusReader:
    """Read messages from a named-pipe channel.

    Opens the FIFO in non-blocking mode.  Also drains the spillover file
    for messages that arrived before the reader attached.
    """

    def __init__(self, pipes_dir: str, channel: str) -> None:
        self.pipes_dir = pipes_dir
        self.channel = channel
        self._fifo_path = os.path.join(
            pipes_dir, f"{_sanitize_channel(channel)}.fifo"
        )
        self._spill_path = os.path.join(
            pipes_dir, f"{_sanitize_channel(channel)}.spill"
        )
        self._fifo_fd: int | None = None
        self._buf: bytes = b""

        os.makedirs(pipes_dir, exist_ok=True)
        _ensure_fifo(self._fifo_path)

    # -- lifecycle ----------------------------------------------------------

    def open(self) -> None:
        """Open the FIFO for non-blocking reading.

        This call itself is non-blocking because we open with O_RDONLY |
        O_NONBLOCK.  On Linux this succeeds immediately even without a writer.
        """
        if self._fifo_fd is not None:
            return
        self._fifo_fd = os.open(
            self._fifo_path, os.O_RDONLY | os.O_NONBLOCK
        )

    def close(self) -> None:
        """Close the FIFO file descriptor."""
        if self._fifo_fd is not None:
            try:
                os.close(self._fifo_fd)
            except OSError:
                pass
            self._fifo_fd = None

    # -- reading ------------------------------------------------------------

    def poll(self) -> list[Message]:
        """Read all available messages (FIFO + spillover).

        Returns a list of non-expired ``Message`` objects.  Handles EAGAIN
        gracefully (no data available is not an error).
        """
        messages: list[Message] = []
        now = time.time()

        # 1. Drain the spillover file first (older messages).
        messages.extend(self._drain_spillover(now))

        # 2. Read from the FIFO.
        if self._fifo_fd is None:
            self.open()

        try:
            chunk = os.read(self._fifo_fd, 65536)  # type: ignore[arg-type]
            if chunk:
                self._buf += chunk
        except OSError as exc:
            if exc.errno != errno.EAGAIN:
                raise
            # EAGAIN — no data available right now, that is fine.

        # Parse complete lines out of the buffer.
        while b"\n" in self._buf:
            line_bytes, self._buf = self._buf.split(b"\n", 1)
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            msg = self._parse_line(line, now)
            if msg is not None:
                messages.append(msg)

        return messages

    # -- internal -----------------------------------------------------------

    def _drain_spillover(self, now: float) -> list[Message]:
        """Read and delete the spillover file atomically-ish."""
        if not os.path.exists(self._spill_path):
            return []

        messages: list[Message] = []
        try:
            with open(self._spill_path, "r", encoding="utf-8") as f:
                data = f.read()
            # Truncate / remove after reading.
            os.unlink(self._spill_path)
        except OSError as exc:
            log.debug("Spillover drain failed: %s", exc)
            return []

        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            msg = self._parse_line(line, now)
            if msg is not None:
                messages.append(msg)

        return messages

    @staticmethod
    def _parse_line(line: str, now: float) -> Message | None:
        """Parse a JSON line into a Message, filtering expired."""
        try:
            msg = Message.from_json_line(line)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            log.warning("Skipping malformed line: %.120s (error: %s)", line, exc)
            return None

        if msg.ts + msg.ttl < now:
            return None
        return msg
