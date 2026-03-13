"""
End-to-end message acknowledgment protocol for the JSONL message bus.

Provides reliable delivery on top of the fire-and-forget bus by tracking
sent messages in SQLite and waiting for explicit 'ack' responses.

Features:
- send_with_ack(): Send a message and block until the target acknowledges it
- ack(): Acknowledge receipt of a message (sends an 'ack' type message back)
- get_unacked(): Query messages that haven't been acknowledged within a timeout
- retry_unacked(): Automatically resend unacknowledged messages

The ack tracking table uses SQLite WAL mode for concurrent access and follows
the SQLITE_BUSY retry pattern from SOP-034.
"""

import functools
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Optional

from agent_comm.bus import BusReader, BusWriter, Message

logger = logging.getLogger(__name__)

_ACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS ack_tracking (
    message_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    body TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    ttl INTEGER NOT NULL DEFAULT 300,
    sent_at REAL NOT NULL,
    acked_at REAL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_ack_status ON ack_tracking(status);
CREATE INDEX IF NOT EXISTS idx_ack_target ON ack_tracking(target_agent);
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


class AckProtocol:
    """End-to-end message acknowledgment with timeout and retry.

    Wraps a BusWriter (for sending) and a BusReader (for receiving acks).
    Tracks all sent-with-ack messages in a SQLite table so that unacknowledged
    messages can be queried and retried.

    Usage::

        writer = BusWriter(comm_dir, "agent-a", "TEAM-A")
        reader = BusReader(comm_dir, "my-channel")
        ack = AckProtocol(writer, reader, db_path="/tmp/ack.db")

        # Sender side
        msg = ack.send_with_ack("my-channel", "request", {"key": "val"}, "agent-b", timeout=10)

        # Receiver side (agent-b polls, sees the message, then acks it)
        ack.ack(msg.id)

        # Check for unacked messages
        unacked = ack.get_unacked(older_than=30)
        ack.retry_unacked(max_retries=3)
    """

    def __init__(
        self,
        writer: BusWriter,
        reader: BusReader,
        db_path: str,
        busy_timeout_ms: int = 30000,
    ) -> None:
        self.writer = writer
        self.reader = reader
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path, timeout=busy_timeout_ms / 1000, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._conn.executescript(_ACK_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    @_retry_on_busy
    def send_with_ack(
        self,
        channel: str,
        msg_type: str,
        body: dict,
        target_agent: str,
        timeout: float = 30.0,
        priority: int = 0,
        ttl: int = 300,
        max_retries: int = 3,
    ) -> Message:
        """Send a message and wait for acknowledgment.

        Publishes the message on the bus, records it in the ack tracking table,
        then polls the reader for an 'ack' response referencing this message.

        Args:
            channel: Bus channel to publish on.
            msg_type: Message type (e.g. 'request', 'info').
            body: Message body dict.
            target_agent: Agent ID expected to acknowledge.
            timeout: Seconds to wait for ack before raising TimeoutError.
            priority: Message priority (>= 5 goes to urgent lane).
            ttl: Time-to-live in seconds.
            max_retries: Max retry attempts if ack is not received.

        Returns:
            The published Message object.

        Raises:
            TimeoutError: If no ack is received within the timeout.
        """
        # Publish the message
        msg = self.writer.publish(
            channel=channel,
            msg_type=msg_type,
            body=body,
            ttl=ttl,
            priority=priority,
        )

        # Track it in SQLite
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ack_tracking
                    (message_id, channel, msg_type, target_agent, body,
                     priority, ttl, sent_at, status, max_retries)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    msg.id, channel, msg_type, target_agent,
                    json.dumps(body), priority, ttl, msg.ts, max_retries,
                ),
            )
            self._conn.commit()

        # Poll for ack
        deadline = time.time() + timeout
        poll_interval = min(0.1, timeout / 10)

        while time.time() < deadline:
            # Check if already acked (another thread/process may have processed it)
            if self._is_acked(msg.id):
                return msg

            # Poll for new messages looking for ack
            messages = self.reader.poll()
            for m in messages:
                if m.type == "ack" and m.in_reply_to == msg.id:
                    self._mark_acked(msg.id)
                    return msg

            time.sleep(poll_interval)
            # Exponential backoff on poll interval, capped at 2s
            poll_interval = min(poll_interval * 1.5, 2.0)

        raise TimeoutError(
            f"No ack received for message {msg.id} from {target_agent} "
            f"within {timeout}s"
        )

    def send_no_wait(
        self,
        channel: str,
        msg_type: str,
        body: dict,
        target_agent: str,
        priority: int = 0,
        ttl: int = 300,
        max_retries: int = 3,
    ) -> Message:
        """Send a message that expects an ack, but don't block waiting for it.

        The message is tracked in the ack table. Use get_unacked() and
        process_incoming_acks() to manage ack status asynchronously.

        Returns:
            The published Message object.
        """
        msg = self.writer.publish(
            channel=channel,
            msg_type=msg_type,
            body=body,
            ttl=ttl,
            priority=priority,
        )

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ack_tracking
                    (message_id, channel, msg_type, target_agent, body,
                     priority, ttl, sent_at, status, max_retries)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    msg.id, channel, msg_type, target_agent,
                    json.dumps(body), priority, ttl, msg.ts, max_retries,
                ),
            )
            self._conn.commit()

        return msg

    def ack(self, message_id: str, channel: Optional[str] = None) -> Message:
        """Acknowledge receipt of a message.

        Sends an 'ack' message on the bus with in_reply_to set to the
        original message_id.

        Args:
            message_id: The ID of the message to acknowledge.
            channel: Channel to send the ack on. If None, uses the reader's channel.

        Returns:
            The ack Message object.
        """
        ch = channel or self.reader.channel
        return self.writer.publish(
            channel=ch,
            msg_type="ack",
            body={"acked_message_id": message_id},
            in_reply_to=message_id,
            priority=0,
            ttl=60,  # Acks don't need long TTL
        )

    def process_incoming_acks(self, messages: Optional[list[Message]] = None) -> list[str]:
        """Process incoming messages and mark any acks in the tracking table.

        Args:
            messages: List of messages to check. If None, polls the reader.

        Returns:
            List of message IDs that were newly acknowledged.
        """
        if messages is None:
            messages = self.reader.poll()

        newly_acked: list[str] = []
        for m in messages:
            if m.type == "ack" and m.in_reply_to:
                if self._mark_acked(m.in_reply_to):
                    newly_acked.append(m.in_reply_to)

        return newly_acked

    @_retry_on_busy
    def get_unacked(self, older_than: float = 60.0) -> list[dict]:
        """Get messages that haven't been acknowledged within timeout.

        Args:
            older_than: Only return messages sent more than this many seconds ago.

        Returns:
            List of dicts with tracking info for unacked messages.
        """
        cutoff = time.time() - older_than
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT message_id, channel, msg_type, target_agent, body,
                       priority, ttl, sent_at, retry_count, max_retries
                FROM ack_tracking
                WHERE status = 'pending' AND sent_at < ?
                ORDER BY sent_at ASC
                """,
                (cutoff,),
            ).fetchall()

        return [
            {
                "message_id": r["message_id"],
                "channel": r["channel"],
                "msg_type": r["msg_type"],
                "target_agent": r["target_agent"],
                "body": json.loads(r["body"]),
                "priority": r["priority"],
                "ttl": r["ttl"],
                "sent_at": r["sent_at"],
                "retry_count": r["retry_count"],
                "max_retries": r["max_retries"],
                "age_seconds": round(time.time() - r["sent_at"], 1),
            }
            for r in rows
        ]

    @_retry_on_busy
    def retry_unacked(self, max_retries: int = 3, older_than: float = 60.0) -> list[Message]:
        """Retry sending unacknowledged messages.

        Re-publishes messages that haven't been acked and haven't exceeded
        their retry limit. Messages that exceed max_retries are marked as 'failed'.

        Args:
            max_retries: Override max retries (uses per-message max if lower).
            older_than: Only retry messages older than this many seconds.

        Returns:
            List of re-sent Message objects.
        """
        unacked = self.get_unacked(older_than=older_than)
        retried: list[Message] = []

        for entry in unacked:
            effective_max = min(max_retries, entry["max_retries"])

            if entry["retry_count"] >= effective_max:
                # Mark as failed
                self._mark_failed(entry["message_id"])
                logger.warning(
                    "Message %s to %s exceeded max retries (%d), marking failed",
                    entry["message_id"], entry["target_agent"], effective_max,
                )
                continue

            # Re-send with a new message ID but referencing the original
            new_msg = self.writer.publish(
                channel=entry["channel"],
                msg_type=entry["msg_type"],
                body={
                    **entry["body"],
                    "_retry_of": entry["message_id"],
                    "_retry_count": entry["retry_count"] + 1,
                },
                ttl=entry["ttl"],
                priority=entry["priority"],
            )

            # Update retry count
            self._increment_retry(entry["message_id"])
            retried.append(new_msg)

            logger.info(
                "Retried message %s (attempt %d/%d) as %s",
                entry["message_id"], entry["retry_count"] + 1,
                effective_max, new_msg.id,
            )

        return retried

    @_retry_on_busy
    def get_stats(self) -> dict:
        """Return summary statistics of the ack tracking table."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT status, COUNT(*) as cnt
                FROM ack_tracking
                GROUP BY status
                """
            ).fetchall()

        stats = {"pending": 0, "acked": 0, "failed": 0}
        for r in rows:
            stats[r["status"]] = r["cnt"]
        stats["total"] = sum(stats.values())
        return stats

    @_retry_on_busy
    def cleanup(self, older_than: float = 3600.0) -> int:
        """Remove old acked/failed entries from the tracking table.

        Args:
            older_than: Remove entries older than this many seconds.

        Returns:
            Number of rows deleted.
        """
        cutoff = time.time() - older_than
        with self._lock:
            cursor = self._conn.execute(
                """
                DELETE FROM ack_tracking
                WHERE status IN ('acked', 'failed') AND sent_at < ?
                """,
                (cutoff,),
            )
            self._conn.commit()
            return cursor.rowcount

    # -- Internal helpers --

    @_retry_on_busy
    def _is_acked(self, message_id: str) -> bool:
        """Check if a message has been acknowledged."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM ack_tracking WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return row is not None and row["status"] == "acked"

    @_retry_on_busy
    def _mark_acked(self, message_id: str) -> bool:
        """Mark a message as acknowledged. Returns True if status changed."""
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE ack_tracking
                SET status = 'acked', acked_at = ?
                WHERE message_id = ? AND status = 'pending'
                """,
                (time.time(), message_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    @_retry_on_busy
    def _mark_failed(self, message_id: str) -> None:
        """Mark a message as failed (exceeded retries)."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE ack_tracking
                SET status = 'failed'
                WHERE message_id = ? AND status = 'pending'
                """,
                (message_id,),
            )
            self._conn.commit()

    @_retry_on_busy
    def _increment_retry(self, message_id: str) -> None:
        """Increment the retry count for a tracked message."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE ack_tracking
                SET retry_count = retry_count + 1, sent_at = ?
                WHERE message_id = ?
                """,
                (time.time(), message_id),
            )
            self._conn.commit()
