"""
JSONL Message Bus for inter-agent communication.

Uses append-only JSONL files with POSIX atomic writes (< 4096 bytes per message).
Each channel maps to a file: {comm_dir}/{channel}.jsonl
"""

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

VALID_MSG_TYPES = frozenset({
    "info", "blocker", "phase-signal", "heartbeat", "request", "response",
})

MAX_MESSAGE_BYTES = 4096


@dataclass
class Message:
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
        """Serialize to a single JSON line (bytes, newline-terminated)."""
        return json.dumps(asdict(self), separators=(",", ":")).encode("utf-8") + b"\n"

    # Known fields for filtering extra keys in from_json_line.
    _KNOWN_FIELDS = frozenset({
        "id", "type", "channel", "team", "agent_id", "ts", "ttl", "body", "in_reply_to",
    })

    @classmethod
    def from_json_line(cls, line: str) -> "Message":
        """Deserialize from a single JSON line.

        Extra fields not part of the Message schema are silently ignored
        so that forward-compatible producers do not break older readers.
        """
        d = json.loads(line)
        filtered = {k: v for k, v in d.items() if k in cls._KNOWN_FIELDS}
        return cls(**filtered)


class BusWriter:
    """Append messages to channel files with POSIX atomic writes."""

    def __init__(self, comm_dir: str, agent_id: str, team: str) -> None:
        self.comm_dir = comm_dir
        self.agent_id = agent_id
        self.team = team
        os.makedirs(comm_dir, exist_ok=True)

    def _channel_path(self, channel: str) -> str:
        # Sanitize channel name for filesystem safety
        safe = channel.replace("/", "_").replace("..", "_")
        return os.path.join(self.comm_dir, f"{safe}.jsonl")

    def publish(
        self,
        channel: str,
        msg_type: str,
        body: dict,
        ttl: int = 300,
        in_reply_to: Optional[str] = None,
    ) -> Message:
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(f"Invalid message type {msg_type!r}; must be one of {VALID_MSG_TYPES}")

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
                f"Serialized message is {len(raw)} bytes, exceeds {MAX_MESSAGE_BYTES} byte limit"
            )

        filepath = self._channel_path(channel)
        # O_WRONLY | O_APPEND | O_CREAT — POSIX guarantees atomic appends
        # for writes <= PIPE_BUF (typically 4096 on Linux).
        fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)

        return msg


class BusReader:
    """Read new messages from a channel file, tracking position.

    Supports optional offset persistence via an OffsetStore (from compactor.py).
    When an OffsetStore and agent_id are provided, the reader:
    - Restores its byte offset from the database on construction
    - Persists the offset after each successful poll
    This eliminates the 7.5x read amplification caused by re-reading entire
    bus files after restarts.
    """

    def __init__(
        self,
        comm_dir: str,
        channel: str,
        agent_id: Optional[str] = None,
        offset_store: "Optional[object]" = None,
    ) -> None:
        self.comm_dir = comm_dir
        self.channel = channel
        safe = channel.replace("/", "_").replace("..", "_")
        self.filepath = os.path.join(comm_dir, f"{safe}.jsonl")
        self._agent_id = agent_id
        self._offset_store = offset_store
        self._offset: int = 0

        # Restore persisted offset if available
        if offset_store is not None and agent_id is not None:
            try:
                saved = offset_store.load_offset(agent_id, channel)
                if saved is not None:
                    # Validate that the saved offset is still valid
                    # (file may have been truncated by compaction)
                    if os.path.exists(self.filepath):
                        file_size = os.path.getsize(self.filepath)
                        if saved <= file_size:
                            self._offset = saved
                            logger.debug(
                                "Restored offset %d for agent=%s channel=%s",
                                saved, agent_id, channel,
                            )
                        else:
                            # File was truncated past our offset -- reset
                            logger.info(
                                "Offset %d exceeds file size %d for agent=%s "
                                "channel=%s; resetting to 0",
                                saved, file_size, agent_id, channel,
                            )
                            self._offset = 0
            except Exception as e:
                logger.warning(
                    "Failed to restore offset for agent=%s channel=%s: %s",
                    agent_id, channel, e,
                )

    def poll(self) -> list[Message]:
        """Read new complete lines since last poll, filtering expired messages.

        If an OffsetStore is configured, the byte offset is persisted after
        each successful read so it survives restarts.
        """
        if not os.path.exists(self.filepath):
            return []

        messages: list[Message] = []
        now = time.time()

        try:
            with open(self.filepath, "rb") as f:
                f.seek(self._offset)
                raw = f.read()
        except OSError as e:
            logger.warning("Failed to read bus file %s: %s", self.filepath, e)
            return []

        if not raw:
            return []

        # Only process complete lines (ending with \n).
        # If the last chunk doesn't end with \n, keep it for next poll.
        if raw.endswith(b"\n"):
            line_bytes_list = raw.split(b"\n")
            line_bytes_list.pop()  # remove trailing empty bytes from split
            self._offset += len(raw)
        else:
            parts = raw.rsplit(b"\n", 1)
            if len(parts) == 1:
                # No complete line yet
                return []
            complete_part = parts[0] + b"\n"
            line_bytes_list = parts[0].split(b"\n")
            self._offset += len(complete_part)

        for line_raw in line_bytes_list:
            try:
                line = line_raw.decode("utf-8").strip()
            except UnicodeDecodeError as e:
                logger.warning("Skipping line with invalid UTF-8: %s", e)
                continue
            if not line:
                continue
            try:
                msg = Message.from_json_line(line)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning("Skipping malformed bus line: %s (error: %s)", line[:120], e)
                continue

            # Filter expired
            if msg.ts + msg.ttl < now:
                continue

            messages.append(msg)

        # Persist the new offset
        self._persist_offset()

        return messages

    def _persist_offset(self) -> None:
        """Save the current byte offset to the OffsetStore if configured."""
        if self._offset_store is not None and self._agent_id is not None:
            try:
                self._offset_store.save_offset(
                    self._agent_id, self.channel, self._offset
                )
            except Exception as e:
                logger.warning(
                    "Failed to persist offset for agent=%s channel=%s: %s",
                    self._agent_id, self.channel, e,
                )

    @property
    def offset(self) -> int:
        """Current byte offset into the channel file."""
        return self._offset


def repair_bus_file(filepath: str) -> int:
    """Remove corrupt (non-JSON) lines from a bus file. Returns count of lines removed.

    Uses rename-then-repair to avoid a TOCTOU race where a concurrent
    writer appends between our read and our replacement write.  The
    original file is renamed to a temporary name first; writers will
    create a fresh file while we repair the snapshot.
    """
    if not os.path.exists(filepath):
        return 0

    # Rename the file so concurrent writers create a new one instead of
    # appending to the file we are about to read/repair.
    tmp_read = filepath + f".repair-src.{os.getpid()}"
    try:
        os.rename(filepath, tmp_read)
    except FileNotFoundError:
        return 0

    try:
        with open(tmp_read, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except OSError:
        # Put the file back if we can't read the snapshot.
        try:
            os.rename(tmp_read, filepath)
        except OSError:
            pass
        return 0

    good_lines: list[str] = []
    removed = 0

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
            # Validate it has the required Message fields
            if not isinstance(data, dict) or "id" not in data or "type" not in data:
                removed += 1
                continue
            good_lines.append(stripped + "\n")
        except json.JSONDecodeError:
            removed += 1

    # Write repaired content to a temp file, then atomically replace.
    tmp_write = filepath + ".repair.tmp"
    with open(tmp_write, "w", encoding="utf-8") as f:
        f.writelines(good_lines)

    # If a new file was created by a writer in the meantime, prepend our
    # repaired lines to it.  Otherwise just rename.
    if os.path.exists(filepath):
        # A writer created a new file; append new data to our repaired copy.
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                new_data = f.read()
            with open(tmp_write, "a", encoding="utf-8") as f:
                f.write(new_data)
        except OSError:
            pass

    os.replace(tmp_write, filepath)

    # Clean up the snapshot.
    try:
        os.unlink(tmp_read)
    except OSError:
        pass

    if removed > 0:
        logger.info("Repaired %s: removed %d corrupt lines", filepath, removed)

    return removed
