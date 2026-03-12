"""
JSONL Message Bus for inter-agent communication.

Uses append-only JSONL files with POSIX atomic writes (< 4096 bytes per message).
Each channel maps to a file: {comm_dir}/{channel}.jsonl

Priority lanes: messages with priority >= 5 go to {channel}_urgent.jsonl
Batching: publish_batch() writes multiple messages in a single atomic append.
Filtering: subscribe() + poll() skip non-matching messages efficiently.
"""

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

VALID_MSG_TYPES = frozenset({
    "info", "blocker", "phase-signal", "heartbeat", "request", "response", "ack",
})

MAX_MESSAGE_BYTES = 4096
URGENT_PRIORITY_THRESHOLD = 5


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
    priority: int = 0

    def to_json_line(self) -> bytes:
        """Serialize to a single JSON line (bytes, newline-terminated)."""
        return json.dumps(asdict(self), separators=(",", ":")).encode("utf-8") + b"\n"

    # Known fields for filtering extra keys in from_json_line.
    _KNOWN_FIELDS = frozenset({
        "id", "type", "channel", "team", "agent_id", "ts", "ttl",
        "body", "in_reply_to", "priority",
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


# Pre-compiled patterns for fast line-level filtering (avoid full JSON parse).
_TYPE_RE = re.compile(rb'"type"\s*:\s*"([^"]+)"')
_PRIORITY_RE = re.compile(rb'"priority"\s*:\s*(-?\d+)')
_TEAM_RE = re.compile(rb'"team"\s*:\s*"([^"]+)"')


def _quick_match(line_raw: bytes, msg_types=None, priority_min=None, team_filter=None) -> bool:
    """Fast pre-filter using regex on raw bytes. Returns True if line MAY match.

    This is a cheap check to skip lines without full JSON decode.
    False positives are fine (will be caught after full decode); false negatives are not.
    """
    if msg_types is not None:
        m = _TYPE_RE.search(line_raw)
        if m and m.group(1).decode("utf-8") not in msg_types:
            return False

    if priority_min is not None:
        m = _PRIORITY_RE.search(line_raw)
        if m:
            if int(m.group(1)) < priority_min:
                return False
        # If no priority field found, default is 0 — skip if min > 0
        elif priority_min > 0:
            return False

    if team_filter is not None:
        m = _TEAM_RE.search(line_raw)
        if m and m.group(1).decode("utf-8") not in team_filter:
            return False

    return True


class BusWriter:
    """Append messages to channel files with POSIX atomic writes."""

    def __init__(self, comm_dir: str, agent_id: str, team: str) -> None:
        self.comm_dir = comm_dir
        self.agent_id = agent_id
        self.team = team
        os.makedirs(comm_dir, exist_ok=True)

    def _channel_path(self, channel: str, urgent: bool = False) -> str:
        safe = channel.replace("/", "_").replace("..", "_")
        suffix = "_urgent" if urgent else ""
        return os.path.join(self.comm_dir, f"{safe}{suffix}.jsonl")

    def publish(
        self,
        channel: str,
        msg_type: str,
        body: dict,
        ttl: int = 300,
        in_reply_to: Optional[str] = None,
        priority: int = 0,
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
            priority=priority,
        )

        raw = msg.to_json_line()
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError(
                f"Serialized message is {len(raw)} bytes, exceeds {MAX_MESSAGE_BYTES} byte limit"
            )

        urgent = priority >= URGENT_PRIORITY_THRESHOLD
        filepath = self._channel_path(channel, urgent=urgent)
        fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)

        return msg

    def publish_batch(
        self,
        channel: str,
        messages: list[dict],
        default_ttl: int = 300,
    ) -> list[Message]:
        """Write multiple messages in atomic appends, respecting the 4096-byte POSIX limit.

        Each element of `messages` is a dict with keys:
            msg_type (str), body (dict), priority (int, optional),
            ttl (int, optional), in_reply_to (str, optional).

        Messages are grouped by lane (normal vs urgent), then batched into
        chunks that stay under MAX_MESSAGE_BYTES for atomic writes.
        Returns the list of created Message objects.
        """
        normal_lines: list[tuple[bytes, Message]] = []
        urgent_lines: list[tuple[bytes, Message]] = []

        for spec in messages:
            msg_type = spec["msg_type"]
            if msg_type not in VALID_MSG_TYPES:
                raise ValueError(f"Invalid message type {msg_type!r}; must be one of {VALID_MSG_TYPES}")

            priority = spec.get("priority", 0)
            msg = Message(
                id=str(uuid.uuid4()),
                type=msg_type,
                channel=channel,
                team=self.team,
                agent_id=self.agent_id,
                ts=time.time(),
                ttl=spec.get("ttl", default_ttl),
                body=spec["body"],
                in_reply_to=spec.get("in_reply_to"),
                priority=priority,
            )
            raw = msg.to_json_line()
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError(
                    f"Single message is {len(raw)} bytes, exceeds {MAX_MESSAGE_BYTES} byte limit"
                )

            if priority >= URGENT_PRIORITY_THRESHOLD:
                urgent_lines.append((raw, msg))
            else:
                normal_lines.append((raw, msg))

        result_msgs: list[Message] = []

        for lane_lines, urgent in [(normal_lines, False), (urgent_lines, True)]:
            if not lane_lines:
                continue
            filepath = self._channel_path(channel, urgent=urgent)

            # Batch lines into chunks <= MAX_MESSAGE_BYTES
            chunks: list[bytes] = []
            current_chunk = b""
            for raw, msg in lane_lines:
                if current_chunk and len(current_chunk) + len(raw) > MAX_MESSAGE_BYTES:
                    chunks.append(current_chunk)
                    current_chunk = raw
                else:
                    current_chunk += raw
                result_msgs.append(msg)

            if current_chunk:
                chunks.append(current_chunk)

            # Write each chunk atomically
            fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                for chunk in chunks:
                    os.write(fd, chunk)
            finally:
                os.close(fd)

        return result_msgs


class BusReader:
    """Read new messages from a channel file, tracking position.

    Supports optional offset persistence via an OffsetStore (from compactor.py).
    When an OffsetStore and agent_id are provided, the reader:
    - Restores its byte offset from the database on construction
    - Persists the offset after each successful poll
    This eliminates the 7.5x read amplification caused by re-reading entire
    bus files after restarts.

    Supports message filtering via subscribe(). When filters are set, poll()
    skips non-matching lines using a fast regex pre-filter before JSON decode.
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
        self._urgent_filepath = os.path.join(comm_dir, f"{safe}_urgent.jsonl")
        self._agent_id = agent_id
        self._offset_store = offset_store
        self._offset: int = 0
        self._urgent_offset: int = 0

        # Subscription filters (None = no filter, accept all)
        self._filter_msg_types: Optional[set[str]] = None
        self._filter_priority_min: Optional[int] = None
        self._filter_team: Optional[set[str]] = None

        # Restore persisted offset if available
        if offset_store is not None and agent_id is not None:
            self._offset = self._restore_offset(channel, self.filepath)
            self._urgent_offset = self._restore_offset(
                f"{channel}__urgent", self._urgent_filepath
            )

    def _restore_offset(self, channel_key: str, filepath: str) -> int:
        """Restore a persisted offset, validating against current file size."""
        try:
            saved = self._offset_store.load_offset(self._agent_id, channel_key)
            if saved is not None:
                if os.path.exists(filepath):
                    file_size = os.path.getsize(filepath)
                    if saved <= file_size:
                        logger.debug(
                            "Restored offset %d for agent=%s channel=%s",
                            saved, self._agent_id, channel_key,
                        )
                        return saved
                    else:
                        logger.info(
                            "Offset %d exceeds file size %d for agent=%s "
                            "channel=%s; resetting to 0",
                            saved, file_size, self._agent_id, channel_key,
                        )
        except Exception as e:
            logger.warning(
                "Failed to restore offset for agent=%s channel=%s: %s",
                self._agent_id, channel_key, e,
            )
        return 0

    def subscribe(
        self,
        msg_types: Optional[set[str]] = None,
        priority_min: Optional[int] = None,
        team_filter: Optional[set[str]] = None,
    ) -> None:
        """Configure message filtering. Only matching messages returned by poll().

        Args:
            msg_types: Set of message types to accept (None = all).
            priority_min: Minimum priority to accept (None = all).
            team_filter: Set of team names to accept (None = all).
        """
        self._filter_msg_types = set(msg_types) if msg_types is not None else None
        self._filter_priority_min = priority_min
        self._filter_team = set(team_filter) if team_filter is not None else None

    def poll(self) -> list[Message]:
        """Read new messages, checking urgent lane first then normal lane.

        Applies subscription filters during parsing (skips non-matching lines
        using fast regex pre-filter before full JSON decode).

        If an OffsetStore is configured, byte offsets are persisted after
        each successful read so they survive restarts.
        """
        messages: list[Message] = []

        # Urgent lane first (high-priority messages)
        urgent_msgs, new_urgent_offset = self._read_lane(
            self._urgent_filepath, self._urgent_offset
        )
        if new_urgent_offset != self._urgent_offset:
            self._urgent_offset = new_urgent_offset
            self._persist_offset(f"{self.channel}__urgent", self._urgent_offset)
        messages.extend(urgent_msgs)

        # Normal lane
        normal_msgs, new_offset = self._read_lane(self.filepath, self._offset)
        if new_offset != self._offset:
            self._offset = new_offset
            self._persist_offset(self.channel, self._offset)
        messages.extend(normal_msgs)

        return messages

    def _read_lane(self, filepath: str, offset: int) -> tuple[list[Message], int]:
        """Read messages from a single lane file starting at offset.

        Returns (messages, new_offset).
        """
        if not os.path.exists(filepath):
            return [], offset

        try:
            with open(filepath, "rb") as f:
                f.seek(offset)
                raw = f.read()
        except OSError as e:
            logger.warning("Failed to read bus file %s: %s", filepath, e)
            return [], offset

        if not raw:
            return [], offset

        now = time.time()
        messages: list[Message] = []

        # Only process complete lines (ending with \n).
        if raw.endswith(b"\n"):
            line_bytes_list = raw.split(b"\n")
            line_bytes_list.pop()  # remove trailing empty bytes from split
            new_offset = offset + len(raw)
        else:
            parts = raw.rsplit(b"\n", 1)
            if len(parts) == 1:
                return [], offset
            complete_part = parts[0] + b"\n"
            line_bytes_list = parts[0].split(b"\n")
            new_offset = offset + len(complete_part)

        for line_raw in line_bytes_list:
            if not line_raw.strip():
                continue

            # Fast pre-filter: skip lines that definitely don't match
            if not _quick_match(
                line_raw,
                msg_types=self._filter_msg_types,
                priority_min=self._filter_priority_min,
                team_filter=self._filter_team,
            ):
                continue

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

            # Post-decode filter validation (catches regex false positives)
            if self._filter_msg_types and msg.type not in self._filter_msg_types:
                continue
            if self._filter_priority_min is not None and msg.priority < self._filter_priority_min:
                continue
            if self._filter_team and msg.team not in self._filter_team:
                continue

            messages.append(msg)

        return messages, new_offset

    def _persist_offset(self, channel_key: str = None, offset_value: int = None) -> None:
        """Save a byte offset to the OffsetStore if configured."""
        if self._offset_store is not None and self._agent_id is not None:
            key = channel_key or self.channel
            val = offset_value if offset_value is not None else self._offset
            try:
                self._offset_store.save_offset(self._agent_id, key, val)
            except Exception as e:
                logger.warning(
                    "Failed to persist offset for agent=%s channel=%s: %s",
                    self._agent_id, key, e,
                )

    @property
    def offset(self) -> int:
        """Current byte offset into the normal channel file."""
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
