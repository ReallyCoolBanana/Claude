"""
Direct team-to-team channels, presence tracking, and work progress broadcasting.

Extends the Proto A communication system with:
- Named channels (direct, topic, team, broadcast)
- Team presence indicators
- Progress broadcasting with bottleneck detection

Uses SQLite WAL mode for concurrent access and the existing JSONL bus for
real-time message delivery.  Stdlib only.
"""

import functools
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_MSG_TYPES = frozenset({
    "info", "blocker", "phase-signal", "heartbeat", "request", "response",
})

MAX_MESSAGE_BYTES = 4096
PIPE_BUF = 4096  # FIX: BUG-DC-002 — POSIX guarantees atomic writes up to this size

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1

# FIX: BUG-DC-002 — lock for serializing large bus writes that exceed PIPE_BUF
_bus_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT UNIQUE NOT NULL,
    channel_type TEXT NOT NULL,
    created_by TEXT NOT NULL,
    participants TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL,
    last_activity REAL
);

CREATE TABLE IF NOT EXISTS presence (
    team TEXT PRIMARY KEY,
    status TEXT DEFAULT 'available',
    current_channels TEXT,
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    team TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    progress_pct REAL DEFAULT 0,
    items_total INTEGER DEFAULT 0,
    items_done INTEGER DEFAULT 0,
    estimated_completion_ts REAL,
    current_bottleneck TEXT,
    last_updated REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS read_offsets (
    team TEXT NOT NULL,
    channel TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team, channel)
);
"""

VALID_PRESENCE = frozenset({"available", "busy", "helping", "away", "offline", "idle"})  # FIX: BUG-DC-003 — added 'offline' and 'idle'
VALID_CHANNEL_TYPES = frozenset({"direct", "team", "topic", "broadcast"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _retry_on_busy(func):
    """Decorator: retry a method on sqlite3.OperationalError (SQLITE_BUSY)."""
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


def _direct_channel_name(team_a: str, team_b: str) -> str:
    """Build a deterministic direct channel name (alphabetical order)."""
    a, b = sorted([team_a, team_b])
    return f"direct-{a}-{b}"


def _safe_channel(name: str) -> str:
    """Sanitize a channel name for filesystem use."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)


def _bus_publish(bus_dir: str, channel: str, agent_id: str, team: str,
                 msg_type: str, body: dict, ttl: int = 3600) -> Optional[str]:
    """Append a message to a JSONL bus channel file.  Returns the message id.

    This is fire-and-forget: file I/O errors are logged but do not propagate.
    Returns None if the write fails.  ValueError for oversized messages still raises.
    """
    msg = {
        "id": str(uuid.uuid4()),
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
            f"Serialized message is {len(raw)} bytes, exceeds {MAX_MESSAGE_BYTES} byte limit"
        )
    safe = _safe_channel(channel)
    filepath = os.path.join(bus_dir, f"{safe}.jsonl")
    try:
        os.makedirs(bus_dir, exist_ok=True)
        if len(raw) <= PIPE_BUF:
            # FIX: BUG-DC-002 — POSIX guarantees atomic append for writes <= PIPE_BUF
            fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)
        else:
            # FIX: BUG-DC-002 — large writes use temp file + rename to avoid torn writes
            with _bus_write_lock:
                tmp_fd, tmp_path = tempfile.mkstemp(dir=bus_dir, suffix=".tmp")
                try:
                    os.write(tmp_fd, raw)
                    os.close(tmp_fd)
                    # Append temp file contents to the target file
                    with open(tmp_path, "rb") as tmp_f:
                        data = tmp_f.read()
                    fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                    try:
                        os.write(fd, data)
                    finally:
                        os.close(fd)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
    except OSError as e:
        logger.error("Failed to publish message to %s: %s", filepath, e)
        return None
    return msg["id"]


def _bus_read(bus_dir: str, channel: str, since_offset: int = 0):
    """Read messages from a JSONL bus channel file.  Returns (msgs, new_offset).

    Only advances the offset past complete lines that parse as valid JSON.
    If the file ends with a partial (incomplete) line, the offset is left
    before that line so it can be re-read once the write completes.

    Uses binary mode ('rb') for consistent byte-offset tracking regardless
    of unicode content.  Reads line-by-line to avoid loading the entire
    remaining file into memory (INEFF-PERF-010).
    """
    safe = _safe_channel(channel)
    filepath = os.path.join(bus_dir, f"{safe}.jsonl")
    if not os.path.exists(filepath):
        return [], 0
    msgs = []
    now = time.time()
    current_offset = since_offset
    with open(filepath, "rb") as f:
        f.seek(since_offset)
        while True:
            line_start = f.tell()
            raw_line = f.readline()
            if not raw_line:
                # EOF
                break
            # readline() returns the line including the trailing \n if present.
            # A line without \n at EOF is a partial/incomplete write.
            if not raw_line.endswith(b"\n"):
                # Partial line — don't advance past it; leave offset before it
                break
            # Complete line (ends with \n)
            stripped = raw_line.rstrip(b"\n")
            line_byte_len = len(raw_line)  # includes the \n
            if not stripped.strip():
                current_offset += line_byte_len
                continue
            try:
                line_str = stripped.decode("utf-8")
            except UnicodeDecodeError:
                # Corrupt data in a complete line; skip it
                current_offset += line_byte_len
                continue
            try:
                m = json.loads(line_str)
            except json.JSONDecodeError:
                # Corrupt complete line; skip it
                current_offset += line_byte_len
                continue
            if m.get("ts", 0) + m.get("ttl", 300) > now:
                msgs.append(m)
            current_offset += line_byte_len
    return msgs, current_offset


# ===========================================================================
# DirectChannels
# ===========================================================================


class DirectChannels:
    """Manages direct team-to-team channels, presence, and progress broadcasting.

    Parameters
    ----------
    db_path:
        Path to the SQLite database (will be created if missing).
    bus_dir:
        Path to the JSONL bus directory for real-time message delivery.
    team:
        The team identifier for this instance.
    agent_id:
        The agent identifier for this instance.
    """

    def __init__(self, db_path: str, bus_dir: str, team: str, agent_id: str) -> None:
        self.db_path = db_path
        self.bus_dir = bus_dir
        self.team = team
        self.agent_id = agent_id

        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        # Track read offsets per channel for bus polling — loaded from DB
        self._read_offsets: dict[str, int] = {}
        self._persisted_offsets: dict[str, int] = {}  # last offset written to DB
        self._closed = False
        self._load_read_offsets()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    def __del__(self) -> None:
        """Ensure the database connection is closed on garbage collection."""
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    @_retry_on_busy
    def create_direct_channel(self, target_team: str) -> str:
        """Create (or reactivate) a direct channel between this team and *target_team*.

        Returns the channel name.
        """
        self._check_closed()
        # FIX: BUG-DC-004 — prevent creating a direct channel to self
        if self.team == target_team:
            raise ValueError(f"Cannot create a direct channel to self (team={self.team!r})")
        channel_name = _direct_channel_name(self.team, target_team)
        participants = json.dumps(sorted([self.team, target_team]))
        now = time.time()

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO channels (channel_name, channel_type, created_by, participants,
                                      status, created_at, last_activity)
                VALUES (?, 'direct', ?, ?, 'active', ?, ?)
                ON CONFLICT(channel_name) DO UPDATE SET
                    status='active', last_activity=excluded.last_activity
                """,
                (channel_name, self.team, participants, now, now),
            )
            self._conn.commit()

        # Announce on bus
        _bus_publish(
            self.bus_dir, "global", self.agent_id, self.team, "info",
            {"event": "channel-created", "channel": channel_name,
             "type": "direct", "participants": sorted([self.team, target_team])},
        )
        logger.info("Direct channel created: %s", channel_name)
        return channel_name

    @_retry_on_busy
    def create_topic_channel(self, topic: str, participants: list[str]) -> str:
        """Create a topic-based channel with the given participants.

        Returns the channel name.
        """
        self._check_closed()
        channel_name = f"topic-{topic}"
        # Ensure creator's team is in participants
        all_participants = sorted(set(participants) | {self.team})
        participants_json = json.dumps(all_participants)
        now = time.time()

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO channels (channel_name, channel_type, created_by, participants,
                                      status, created_at, last_activity)
                VALUES (?, 'topic', ?, ?, 'active', ?, ?)
                ON CONFLICT(channel_name) DO UPDATE SET
                    participants=excluded.participants,
                    status='active', last_activity=excluded.last_activity
                """,
                (channel_name, self.team, participants_json, now, now),
            )
            self._conn.commit()

        _bus_publish(
            self.bus_dir, "global", self.agent_id, self.team, "info",
            {"event": "channel-created", "channel": channel_name,
             "type": "topic", "participants": all_participants},
        )
        logger.info("Topic channel created: %s", channel_name)
        return channel_name

    @_retry_on_busy
    def join_channel(self, channel_name: str) -> None:
        """Add this team to an existing channel's participant list."""
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT participants FROM channels WHERE channel_name = ? AND status = 'active'",
                (channel_name,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Channel {channel_name!r} does not exist or is archived")

            current = json.loads(row["participants"])
            if self.team not in current:
                current.append(self.team)
                current.sort()
            self._conn.execute(
                "UPDATE channels SET participants = ?, last_activity = ? WHERE channel_name = ?",
                (json.dumps(current), time.time(), channel_name),
            )
            self._conn.commit()

        _bus_publish(
            self.bus_dir, "global", self.agent_id, self.team, "info",
            {"event": "channel-joined", "channel": channel_name, "team": self.team},
        )

    @_retry_on_busy
    def leave_channel(self, channel_name: str) -> None:
        """Remove this team from a channel's participant list."""
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT participants FROM channels WHERE channel_name = ?",
                (channel_name,),
            ).fetchone()
            if row is None:
                return

            current = json.loads(row["participants"])
            if self.team in current:
                current.remove(self.team)
            self._conn.execute(
                "UPDATE channels SET participants = ?, last_activity = ? WHERE channel_name = ?",
                (json.dumps(current), time.time(), channel_name),
            )
            self._conn.commit()

    @_retry_on_busy
    def list_active_channels(self) -> list[dict]:
        """Return all active channels this team participates in."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM channels WHERE status = 'active'"
            ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            participants = json.loads(d["participants"])
            if self.team in participants:
                d["participants"] = participants
                results.append(d)
        return results

    @_retry_on_busy
    def archive_channel(self, channel_name: str) -> None:
        """Mark a channel as archived."""
        self._check_closed()
        with self._lock:
            self._conn.execute(
                "UPDATE channels SET status = 'archived', last_activity = ? WHERE channel_name = ?",
                (time.time(), channel_name),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Direct messaging
    # ------------------------------------------------------------------

    def send_direct(self, target_team: str, msg_type: str, body: dict) -> Optional[str]:
        """Send a message on the direct channel to *target_team*.

        Creates the direct channel if it does not yet exist.
        Returns the message id, or None if the bus publish failed.
        """
        self._check_closed()
        channel_name = _direct_channel_name(self.team, target_team)

        # Ensure channel exists in the registry
        self._ensure_direct_channel(target_team)

        # Update last_activity
        self._touch_channel(channel_name)

        # Publish to the channel-specific bus file
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(f"Invalid message type {msg_type!r}")

        msg_id = _bus_publish(
            self.bus_dir, channel_name, self.agent_id, self.team, msg_type, body,
        )
        if msg_id is None:
            logger.warning(
                "send_direct to %s failed: bus publish returned None", target_team,
            )
        return msg_id

    def read_direct(self, from_team: str) -> list[dict]:
        """Read new messages from the direct channel with *from_team*.

        Returns messages since the last read (tracked internally and persisted).
        """
        self._check_closed()
        with self._lock:
            channel_name = _direct_channel_name(self.team, from_team)
            offset = self._read_offsets.get(channel_name, 0)
            msgs, new_offset = _bus_read(self.bus_dir, channel_name, offset)
            self._read_offsets[channel_name] = new_offset
        self._save_read_offset(channel_name, new_offset)
        return msgs

    # ------------------------------------------------------------------
    # Presence
    # ------------------------------------------------------------------

    @_retry_on_busy
    def set_presence(self, status: str) -> None:
        """Set this team's presence status.

        Valid statuses: available, busy, helping, away.
        """
        self._check_closed()
        if status not in VALID_PRESENCE:
            raise ValueError(f"Invalid presence status {status!r}; must be one of {VALID_PRESENCE}")

        now = time.time()

        with self._lock:
            # Gather current channels inside the same lock acquisition to avoid
            # a TOCTOU gap where channels could change between queries.
            rows = self._conn.execute(
                "SELECT * FROM channels WHERE status = 'active'"
            ).fetchall()
            channels = []
            for row in rows:
                participants = json.loads(dict(row)["participants"])
                if self.team in participants:
                    channels.append(dict(row)["channel_name"])
            channels_json = json.dumps(channels)

            self._conn.execute(
                """
                INSERT INTO presence (team, status, current_channels, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(team) DO UPDATE SET
                    status=excluded.status,
                    current_channels=excluded.current_channels,
                    last_seen=excluded.last_seen
                """,
                (self.team, status, channels_json, now),
            )
            self._conn.commit()

        _bus_publish(
            self.bus_dir, "global", self.agent_id, self.team, "info",
            {"event": "presence-update", "team": self.team, "status": status},
        )

    @_retry_on_busy
    def get_presence(self, team: str = None) -> dict | list[dict]:
        """Get presence info for one team or all teams.

        Parameters
        ----------
        team:
            If provided, return presence for that specific team.
            If None, return presence for all teams.

        Returns
        -------
        dict
            When *team* is given: a single dict with keys ``team``,
            ``status``, ``current_channels``, ``last_seen``.  Returns a
            default dict with ``status="unknown"`` if the team is not found.
        list[dict]
            When *team* is None: a list of dicts, one per registered team,
            each with the same keys as above.
        """
        self._check_closed()
        with self._lock:
            if team is not None:
                row = self._conn.execute(
                    "SELECT * FROM presence WHERE team = ?", (team,),
                ).fetchone()
                if row is None:
                    return {"team": team, "status": "unknown", "current_channels": [], "last_seen": None}
                d = dict(row)
                d["current_channels"] = json.loads(d["current_channels"]) if d["current_channels"] else []
                return d
            else:
                rows = self._conn.execute("SELECT * FROM presence").fetchall()
                results = []
                for row in rows:
                    d = dict(row)
                    d["current_channels"] = json.loads(d["current_channels"]) if d["current_channels"] else []
                    results.append(d)
                return results

    @_retry_on_busy
    def get_available_teams(self) -> list[dict]:
        """Return all teams with 'available' presence status."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM presence WHERE status = 'available'"
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["current_channels"] = json.loads(d["current_channels"]) if d["current_channels"] else []
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Progress broadcasting
    # ------------------------------------------------------------------

    @_retry_on_busy
    def update_progress(self, phase: str, progress_pct: float,
                        items_total: int, items_done: int,
                        est_completion_ts: float = None,
                        bottleneck: str = None) -> None:
        """Update this team's progress and broadcast it."""
        self._check_closed()
        # FIX: BUG-DC-003 — validate progress_pct is within 0-100
        if not (0 <= progress_pct <= 100):
            raise ValueError(f"progress_pct must be between 0 and 100, got {progress_pct}")
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO progress (team, phase, progress_pct, items_total, items_done,
                                      estimated_completion_ts, current_bottleneck, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team) DO UPDATE SET
                    phase=excluded.phase,
                    progress_pct=excluded.progress_pct,
                    items_total=excluded.items_total,
                    items_done=excluded.items_done,
                    estimated_completion_ts=excluded.estimated_completion_ts,
                    current_bottleneck=excluded.current_bottleneck,
                    last_updated=excluded.last_updated
                """,
                (self.team, phase, progress_pct, items_total, items_done,
                 est_completion_ts, bottleneck, now),
            )
            self._conn.commit()

        body = {
            "event": "progress-update",
            "team": self.team,
            "phase": phase,
            "progress_pct": progress_pct,
            "items_total": items_total,
            "items_done": items_done,
        }
        if bottleneck:
            body["bottleneck"] = bottleneck
        if est_completion_ts:
            body["estimated_completion_ts"] = est_completion_ts

        _bus_publish(
            self.bus_dir, "global", self.agent_id, self.team, "info", body,
        )

    @_retry_on_busy
    def get_all_progress(self) -> list[dict]:
        """Return progress records for all teams."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM progress ORDER BY progress_pct ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    @_retry_on_busy
    def get_team_progress(self, team: str) -> dict:
        """Return progress for a specific team."""
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM progress WHERE team = ?", (team,),
            ).fetchone()
        if row is None:
            return {"team": team, "phase": "unknown", "progress_pct": 0,
                    "items_total": 0, "items_done": 0}
        return dict(row)

    @_retry_on_busy
    def get_slowest_team(self) -> dict:
        """Identify the team with the lowest progress percentage.

        Useful for identifying the overall bottleneck.
        """
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM progress ORDER BY progress_pct ASC LIMIT 1"
            ).fetchone()
        if row is None:
            return {}
        return dict(row)

    @_retry_on_busy
    def get_teams_below_progress(self, threshold_pct: float) -> list[dict]:
        """Return all teams with progress below the given threshold."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM progress WHERE progress_pct < ? ORDER BY progress_pct ASC",
                (threshold_pct,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_closed(self) -> None:
        """Raise RuntimeError if this instance has been closed."""
        if self._closed:
            raise RuntimeError("DirectChannels instance is closed")

    def _load_read_offsets(self) -> None:
        """Load persisted read offsets from the database."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT channel, offset FROM read_offsets WHERE team = ?",
                (self.team,),
            ).fetchall()
        for row in rows:
            self._read_offsets[row["channel"]] = row["offset"]
            self._persisted_offsets[row["channel"]] = row["offset"]

    @_retry_on_busy
    def _save_read_offset(self, channel: str, offset: int) -> None:
        """Persist a read offset to the database.

        Only writes when the offset has actually changed to reduce write
        amplification (INEFF-PERF-013).
        """
        # Check if the offset actually changed from what's persisted (INEFF-PERF-013)
        if self._persisted_offsets.get(channel) == offset:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO read_offsets (team, channel, offset)
                VALUES (?, ?, ?)
                ON CONFLICT(team, channel) DO UPDATE SET offset=excluded.offset
                """,
                (self.team, channel, offset),
            )
            self._conn.commit()
        self._persisted_offsets[channel] = offset

    @_retry_on_busy
    def _ensure_direct_channel(self, target_team: str) -> str:
        """Ensure a direct channel exists between this team and *target_team*."""
        channel_name = _direct_channel_name(self.team, target_team)
        participants = json.dumps(sorted([self.team, target_team]))
        now = time.time()

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO channels (channel_name, channel_type, created_by, participants,
                                      status, created_at, last_activity)
                VALUES (?, 'direct', ?, ?, 'active', ?, ?)
                ON CONFLICT(channel_name) DO UPDATE SET
                    status='active', last_activity=excluded.last_activity
                """,
                (channel_name, self.team, participants, now, now),
            )
            self._conn.commit()
        return channel_name

    @_retry_on_busy
    def _touch_channel(self, channel_name: str) -> None:
        """Update last_activity timestamp on a channel."""
        with self._lock:
            self._conn.execute(
                "UPDATE channels SET last_activity = ? WHERE channel_name = ?",
                (time.time(), channel_name),
            )
            self._conn.commit()
