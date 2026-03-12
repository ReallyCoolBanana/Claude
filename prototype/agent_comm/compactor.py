"""
Bus compaction and read offset persistence for the JSONL message bus.

Solves two critical problems:
1. JSONL files grow unbounded (318 bytes/message, no TTL enforcement on disk)
2. Read offsets are lost on restart, causing full re-processing (7.5x amplification)

Compaction strategy:
- Read the JSONL file, discard messages older than TTL
- Write surviving messages to a temp file
- Atomic rename (os.replace) to swap in the compacted file
- Track compaction stats for observability

Offset persistence:
- SQLite table maps (agent_id, channel) -> byte offset
- BusReader can restore its position after restart
- Uses BEGIN IMMEDIATE + retry on SQLITE_BUSY per SOP-034
"""

import functools
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_OFFSET_SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_offsets (
    agent_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    byte_offset INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (agent_id, channel)
);

CREATE TABLE IF NOT EXISTS compaction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    ts REAL NOT NULL,
    messages_before INTEGER NOT NULL,
    messages_after INTEGER NOT NULL,
    bytes_before INTEGER NOT NULL,
    bytes_after INTEGER NOT NULL,
    elapsed_seconds REAL NOT NULL
);
"""

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1


def _retry_on_busy(func):
    """Decorator: retry on sqlite3.OperationalError (SQLITE_BUSY)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        delay = _RETRY_BACKOFF
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    last_err = e
                    logger.debug(
                        "SQLITE_BUSY on %s (attempt %d/%d), retrying in %.2fs",
                        func.__name__, attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


class OffsetStore:
    """Persists per-agent per-channel byte offsets in SQLite.

    Allows BusReader to resume from where it left off after restart,
    eliminating the 7.5x read amplification from re-reading entire files.
    """

    def __init__(self, db_path: str, busy_timeout_ms: int = 30000) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path, timeout=busy_timeout_ms / 1000, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._conn.executescript(_OFFSET_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @_retry_on_busy
    def save_offset(self, agent_id: str, channel: str, byte_offset: int) -> None:
        """Persist the current read offset for an agent+channel pair."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bus_offsets (agent_id, channel, byte_offset, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id, channel) DO UPDATE SET
                    byte_offset=excluded.byte_offset,
                    updated_at=excluded.updated_at
                """,
                (agent_id, channel, byte_offset, time.time()),
            )
            self._conn.commit()

    @_retry_on_busy
    def load_offset(self, agent_id: str, channel: str) -> Optional[int]:
        """Load the persisted read offset, or None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT byte_offset FROM bus_offsets WHERE agent_id = ? AND channel = ?",
                (agent_id, channel),
            ).fetchone()
        if row is None:
            return None
        return row["byte_offset"]

    @_retry_on_busy
    def adjust_offsets_after_compaction(
        self, channel: str, old_size: int, new_size: int
    ) -> None:
        """After compaction shrinks a file, adjust all reader offsets.

        Readers that were at or past the old file end get set to the new end.
        Readers that were partway through get reset to 0 (they need to re-read
        the compacted file since line positions changed).
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise

            try:
                # Readers who had fully consumed the file: set to new end
                self._conn.execute(
                    """
                    UPDATE bus_offsets SET byte_offset = ?, updated_at = ?
                    WHERE channel = ? AND byte_offset >= ?
                    """,
                    (new_size, time.time(), channel, old_size),
                )
                # Readers partway through: reset to 0 (positions are invalid
                # after compaction rearranges lines)
                self._conn.execute(
                    """
                    UPDATE bus_offsets SET byte_offset = 0, updated_at = ?
                    WHERE channel = ? AND byte_offset < ? AND byte_offset > 0
                    """,
                    (time.time(), channel, old_size),
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    @_retry_on_busy
    def log_compaction(
        self,
        channel: str,
        messages_before: int,
        messages_after: int,
        bytes_before: int,
        bytes_after: int,
        elapsed_seconds: float,
    ) -> None:
        """Record compaction statistics."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO compaction_log
                    (channel, ts, messages_before, messages_after,
                     bytes_before, bytes_after, elapsed_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel, time.time(), messages_before, messages_after,
                    bytes_before, bytes_after, elapsed_seconds,
                ),
            )
            self._conn.commit()

    @_retry_on_busy
    def get_compaction_stats(self, channel: Optional[str] = None, limit: int = 20) -> list[dict]:
        """Return recent compaction log entries."""
        with self._lock:
            if channel:
                rows = self._conn.execute(
                    "SELECT * FROM compaction_log WHERE channel = ? ORDER BY ts DESC LIMIT ?",
                    (channel, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM compaction_log ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]


class BusCompactor:
    """Compacts JSONL bus files by removing expired messages.

    Usage:
        store = OffsetStore("/path/to/offsets.db")
        compactor = BusCompactor("/path/to/comm_dir", store, default_ttl=300)
        stats = compactor.compact_channel("my-channel")
    """

    def __init__(
        self,
        comm_dir: str,
        offset_store: OffsetStore,
        default_ttl: int = 300,
    ) -> None:
        self.comm_dir = comm_dir
        self.offset_store = offset_store
        self.default_ttl = default_ttl

    def _channel_path(self, channel: str) -> str:
        safe = channel.replace("/", "_").replace("..", "_")
        return os.path.join(self.comm_dir, f"{safe}.jsonl")

    def compact_channel(self, channel: str, ttl_override: Optional[int] = None) -> dict:
        """Compact a single channel file, removing expired messages.

        Returns a dict with compaction stats:
            {channel, messages_before, messages_after, bytes_before, bytes_after,
             elapsed_seconds, error}
        """
        filepath = self._channel_path(channel)
        ttl = ttl_override if ttl_override is not None else self.default_ttl
        start_time = time.time()
        now = start_time

        result = {
            "channel": channel,
            "messages_before": 0,
            "messages_after": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "elapsed_seconds": 0.0,
            "error": None,
        }

        if not os.path.exists(filepath):
            return result

        # Step 1: Read the current file
        try:
            with open(filepath, "rb") as f:
                raw = f.read()
        except OSError as e:
            result["error"] = str(e)
            return result

        bytes_before = len(raw)
        result["bytes_before"] = bytes_before

        if bytes_before == 0:
            return result

        # Step 2: Parse lines and filter
        lines = raw.split(b"\n")
        surviving_lines: list[bytes] = []
        total_messages = 0

        for line_raw in lines:
            line = line_raw.strip()
            if not line:
                continue
            total_messages += 1

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # Drop corrupt lines during compaction
                continue

            # Check TTL: message expires at ts + ttl
            msg_ts = data.get("ts", 0)
            msg_ttl = data.get("ttl", ttl)
            if msg_ts + msg_ttl < now:
                # Expired -- skip
                continue

            surviving_lines.append(line)

        result["messages_before"] = total_messages
        result["messages_after"] = len(surviving_lines)

        # Step 3: Write compacted content to temp file, then atomic rename
        tmp_path = filepath + f".compact.{os.getpid()}.tmp"
        try:
            compacted = b"\n".join(surviving_lines)
            if compacted:
                compacted += b"\n"
            bytes_after = len(compacted)

            with open(tmp_path, "wb") as f:
                f.write(compacted)

            # Atomic replace
            os.replace(tmp_path, filepath)

            result["bytes_after"] = bytes_after

        except OSError as e:
            result["error"] = str(e)
            # Clean up temp file if it exists
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return result

        elapsed = time.time() - start_time
        result["elapsed_seconds"] = round(elapsed, 4)

        # Step 4: Adjust reader offsets
        try:
            self.offset_store.adjust_offsets_after_compaction(
                channel, bytes_before, bytes_after
            )
        except Exception as e:
            logger.warning("Failed to adjust offsets after compaction of %s: %s", channel, e)

        # Step 5: Log compaction stats
        try:
            self.offset_store.log_compaction(
                channel=channel,
                messages_before=total_messages,
                messages_after=len(surviving_lines),
                bytes_before=bytes_before,
                bytes_after=bytes_after,
                elapsed_seconds=elapsed,
            )
        except Exception as e:
            logger.warning("Failed to log compaction stats for %s: %s", channel, e)

        logger.info(
            "Compacted %s: %d -> %d messages, %d -> %d bytes (%.3fs)",
            channel, total_messages, len(surviving_lines),
            bytes_before, bytes_after, elapsed,
        )

        return result

    def compact_all(self, ttl_override: Optional[int] = None) -> list[dict]:
        """Compact all .jsonl files in the comm directory.

        Returns a list of per-channel compaction stats.
        """
        results = []
        try:
            entries = os.listdir(self.comm_dir)
        except OSError as e:
            logger.warning("Cannot list comm_dir %s: %s", self.comm_dir, e)
            return results

        for entry in sorted(entries):
            if entry.endswith(".jsonl") and not entry.startswith("."):
                channel = entry[:-6]  # strip .jsonl
                stats = self.compact_channel(channel, ttl_override=ttl_override)
                results.append(stats)

        return results
