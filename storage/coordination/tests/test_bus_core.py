"""Tests for bus_core.py -- the shared JSONL bus and SQLite retry utilities.

These tests validate the interface of bus_core.py, the canonical shared module
extracted from duplicated patterns across coordinator_hub.py, help_protocol.py,
direct_channels.py, work_stealing.py, and multi_team_runner.py.

Team 5 (efficiency/refactoring) owns bus_core.py.  These tests were written
by Assist Team C based on the efficiency reports and the consolidation plan
(S-01: "Extract a shared bus_core module").

bus_core.py public interface:
    - sanitize_channel(name: str) -> str
    - bus_write(bus_dir, channel, msg_type, body, team, agent_id, *, ttl=3600) -> str|None
    - bus_read(bus_dir, channel, offset=0) -> tuple[list[dict], int]
    - retry_on_busy  (decorator, supports @retry_on_busy and @retry_on_busy(...))
    - VALID_MSG_TYPES  (frozenset)
    - init_db(path, *, busy_timeout_ms=5000, row_factory=sqlite3.Row) -> Connection
    - is_busy_or_locked(exc) -> bool
    - MAX_MESSAGE_BYTES (int constant, 4096)
"""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from storage.coordination.bus_core import (
        sanitize_channel,
        bus_write,
        bus_read,
        retry_on_busy,
        VALID_MSG_TYPES,
        init_db,
        is_busy_or_locked,
        MAX_MESSAGE_BYTES,
        DEFAULT_BUSY_TIMEOUT_MS,
    )
    _BUS_CORE_AVAILABLE = True
except ImportError:
    _BUS_CORE_AVAILABLE = False


@unittest.skipUnless(_BUS_CORE_AVAILABLE, "bus_core.py not yet created by Team 5")
class TestSanitizeChannel(unittest.TestCase):
    """Tests for sanitize_channel() -- filesystem-safe channel name conversion."""

    def test_simple_name_unchanged(self):
        """Plain alphanumeric names should pass through unmodified."""
        self.assertEqual(sanitize_channel("global"), "global")
        self.assertEqual(sanitize_channel("team-1"), "team-1")
        self.assertEqual(sanitize_channel("my_channel"), "my_channel")

    def test_slashes_replaced(self):
        """Forward slashes must be replaced (path traversal prevention)."""
        result = sanitize_channel("team/subchannel")
        self.assertNotIn("/", result)

    def test_double_dots_replaced(self):
        """Double dots must be neutralized (path traversal prevention)."""
        result = sanitize_channel("../../../etc/passwd")
        self.assertNotIn("..", result)

    def test_special_characters_replaced(self):
        """Characters outside [a-zA-Z0-9_-] should be replaced with '_'."""
        result = sanitize_channel("hello world!@#$%")
        self.assertTrue(
            all(c.isalnum() or c in ('_', '-') for c in result),
            f"Unexpected characters in sanitized channel: {result!r}",
        )

    def test_unicode_replaced(self):
        """Unicode characters should be replaced."""
        result = sanitize_channel("chännel-über-grüß")
        self.assertTrue(
            all(c.isalnum() or c in ('_', '-') for c in result),
            f"Unicode leak in sanitized channel: {result!r}",
        )

    def test_empty_string(self):
        """Empty channel name should return empty or raise ValueError."""
        # Either behaviour is acceptable; the module may choose to raise
        try:
            result = sanitize_channel("")
            self.assertIsInstance(result, str)
        except (ValueError, TypeError):
            pass  # Also acceptable

    def test_very_long_name(self):
        """Very long channel names should be handled (no crash)."""
        long_name = "a" * 1000
        result = sanitize_channel(long_name)
        self.assertIsInstance(result, str)
        # Optionally truncated, but must not crash
        self.assertGreater(len(result), 0)

    def test_hyphen_and_underscore_preserved(self):
        """Hyphens and underscores are valid and should be preserved."""
        self.assertEqual(sanitize_channel("my-channel_v2"), "my-channel_v2")


@unittest.skipUnless(_BUS_CORE_AVAILABLE, "bus_core.py not yet created by Team 5")
class TestBusWrite(unittest.TestCase):
    """Tests for bus_write() -- atomic JSONL append."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bus_test_")
        self.bus_dir = os.path.join(self.tmpdir, "bus")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_bus_dir(self):
        """bus_write should create the bus directory if it doesn't exist."""
        self.assertFalse(os.path.exists(self.bus_dir))
        bus_write(self.bus_dir, "global", "info", {"hello": "world"}, "team-1", "agent-1")
        self.assertTrue(os.path.isdir(self.bus_dir))

    def test_creates_channel_file(self):
        """bus_write should create a <channel>.jsonl file."""
        bus_write(self.bus_dir, "global", "info", {"key": "val"}, "team-1", "agent-1")
        filepath = os.path.join(self.bus_dir, "global.jsonl")
        self.assertTrue(os.path.isfile(filepath))

    def test_message_is_valid_json(self):
        """Each line written must be valid JSON."""
        bus_write(self.bus_dir, "test-ch", "info", {"data": 42}, "team-1", "agent-1")
        filepath = os.path.join(self.bus_dir, "test-ch.jsonl")
        with open(filepath, "r") as f:
            line = f.readline()
        msg = json.loads(line)
        self.assertIsInstance(msg, dict)

    def test_message_contains_required_fields(self):
        """Each message must contain id, type, channel, team, agent_id, ts, ttl, body."""
        bus_write(self.bus_dir, "global", "info", {"x": 1}, "team-1", "agent-1")
        filepath = os.path.join(self.bus_dir, "global.jsonl")
        with open(filepath, "r") as f:
            msg = json.loads(f.readline())
        for field in ("id", "type", "channel", "team", "agent_id", "ts", "ttl", "body"):
            self.assertIn(field, msg, f"Missing required field: {field}")
        self.assertEqual(msg["type"], "info")
        self.assertEqual(msg["channel"], "global")
        self.assertEqual(msg["team"], "team-1")
        self.assertEqual(msg["agent_id"], "agent-1")
        self.assertEqual(msg["body"], {"x": 1})
        self.assertIsInstance(msg["id"], str)
        self.assertIsInstance(msg["ts"], (int, float))
        self.assertIsInstance(msg["ttl"], int)

    def test_returns_message_id(self):
        """bus_write should return a non-empty string message ID on success."""
        msg_id = bus_write(self.bus_dir, "global", "agent-1", "team-1", "info", {})
        self.assertIsInstance(msg_id, str)
        self.assertGreater(len(msg_id), 0)

    def test_multiple_writes_append(self):
        """Multiple writes to the same channel should append, not overwrite."""
        bus_write(self.bus_dir, "ch", "info", {"n": 1}, "t1", "a1")
        bus_write(self.bus_dir, "ch", "info", {"n": 2}, "t2", "a2")
        filepath = os.path.join(self.bus_dir, "ch.jsonl")
        with open(filepath, "r") as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 2)

    def test_sanitized_channel_in_filename(self):
        """Channel names with special chars should produce safe filenames."""
        bus_write(self.bus_dir, "team/special!", "info", {}, "t1", "a1")
        files = os.listdir(self.bus_dir)
        self.assertEqual(len(files), 1)
        self.assertNotIn("/", files[0])
        self.assertTrue(files[0].endswith(".jsonl"))

    def test_oversized_message_raises(self):
        """Messages exceeding MAX_MESSAGE_BYTES should raise ValueError."""
        huge_body = {"data": "x" * (MAX_MESSAGE_BYTES + 1000)}
        with self.assertRaises(ValueError):
            bus_write(self.bus_dir, "global", "info", huge_body, "t1", "a1")

    def test_io_error_returns_none(self):
        """If the bus directory is not writable, bus_write should return None (not raise)."""
        # Use a path under /proc or similar that will fail
        bad_dir = "/proc/nonexistent_bus_dir"
        result = bus_write(bad_dir, "global", "info", {"ok": True}, "t1", "a1")
        self.assertIsNone(result)


@unittest.skipUnless(_BUS_CORE_AVAILABLE, "bus_core.py not yet created by Team 5")
class TestBusRead(unittest.TestCase):
    """Tests for bus_read() -- JSONL reading with offset tracking."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="bus_test_")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_raw(self, channel, messages):
        """Helper: write raw JSON messages to a channel file."""
        safe = channel  # assume already sanitized for test
        filepath = os.path.join(self.bus_dir, f"{safe}.jsonl")
        with open(filepath, "ab") as f:
            for msg in messages:
                f.write(json.dumps(msg).encode("utf-8") + b"\n")

    def test_read_nonexistent_channel(self):
        """Reading a channel that has no file should return empty list and offset 0."""
        msgs, offset = bus_read(self.bus_dir, "nonexistent")
        self.assertEqual(msgs, [])
        self.assertEqual(offset, 0)

    def test_read_returns_messages(self):
        """bus_read should parse and return valid messages."""
        now = time.time()
        self._write_raw("test", [
            {"id": "1", "type": "info", "channel": "test", "team": "t1",
             "agent_id": "a1", "ts": now, "ttl": 3600, "body": {"v": 1}},
        ])
        msgs, offset = bus_read(self.bus_dir, "test")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["body"]["v"], 1)
        self.assertGreater(offset, 0)

    def test_offset_tracking(self):
        """Reading from a previous offset should only return new messages."""
        now = time.time()
        self._write_raw("test", [
            {"id": "1", "type": "info", "channel": "test", "team": "t1",
             "agent_id": "a1", "ts": now, "ttl": 3600, "body": {"v": 1}},
        ])
        _, offset1 = bus_read(self.bus_dir, "test")

        self._write_raw("test", [
            {"id": "2", "type": "info", "channel": "test", "team": "t1",
             "agent_id": "a1", "ts": now, "ttl": 3600, "body": {"v": 2}},
        ])
        msgs, offset2 = bus_read(self.bus_dir, "test", offset=offset1)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["body"]["v"], 2)
        self.assertGreater(offset2, offset1)

    def test_expired_messages_filtered(self):
        """Messages past their TTL should not be returned."""
        old_ts = time.time() - 7200  # 2 hours ago
        self._write_raw("test", [
            {"id": "1", "type": "info", "channel": "test", "team": "t1",
             "agent_id": "a1", "ts": old_ts, "ttl": 3600, "body": {"old": True}},
        ])
        msgs, _ = bus_read(self.bus_dir, "test")
        self.assertEqual(len(msgs), 0)

    def test_binary_mode_offset_consistency(self):
        """Offsets should be byte-based for consistency with binary mode reads."""
        now = time.time()
        msg1 = {"id": "1", "type": "info", "channel": "test", "team": "t1",
                "agent_id": "a1", "ts": now, "ttl": 3600, "body": {"v": 1}}
        msg2 = {"id": "2", "type": "info", "channel": "test", "team": "t1",
                "agent_id": "a1", "ts": now, "ttl": 3600, "body": {"v": 2}}
        self._write_raw("test", [msg1, msg2])

        msgs_all, offset_all = bus_read(self.bus_dir, "test", offset=0)
        self.assertEqual(len(msgs_all), 2)

        # Verify offset equals file size
        filepath = os.path.join(self.bus_dir, "test.jsonl")
        file_size = os.path.getsize(filepath)
        self.assertEqual(offset_all, file_size)

    def test_partial_line_not_consumed(self):
        """A partial (incomplete) line at EOF should not advance the offset."""
        now = time.time()
        filepath = os.path.join(self.bus_dir, "test.jsonl")
        full_msg = json.dumps({"id": "1", "type": "info", "channel": "test",
                               "team": "t1", "agent_id": "a1", "ts": now,
                               "ttl": 3600, "body": {}}).encode("utf-8")
        partial = b'{"id":"2","type":"info","channel":"test"'  # incomplete
        with open(filepath, "wb") as f:
            f.write(full_msg + b"\n")
            f.write(partial)  # no trailing newline

        msgs, offset = bus_read(self.bus_dir, "test")
        self.assertEqual(len(msgs), 1)
        # Offset should NOT have consumed the partial line
        self.assertEqual(offset, len(full_msg) + 1)  # +1 for newline


@unittest.skipUnless(_BUS_CORE_AVAILABLE, "bus_core.py not yet created by Team 5")
class TestRetryOnBusy(unittest.TestCase):
    """Tests for the retry_on_busy decorator."""

    def test_passes_through_on_success(self):
        """Decorated function should return normally when no error occurs."""
        @retry_on_busy
        def good_func():
            return 42
        self.assertEqual(good_func(), 42)

    def test_retries_on_sqlite_busy(self):
        """Should retry when sqlite3.OperationalError with 'database is locked'."""
        call_count = 0

        @retry_on_busy
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        with patch("storage.coordination.bus_core.time.sleep"):  # skip actual sleeps
            result = flaky_func()
        self.assertEqual(result, "ok")
        self.assertEqual(call_count, 3)

    def test_raises_after_max_retries(self):
        """Should raise the last error after exhausting retries."""
        @retry_on_busy
        def always_busy():
            raise sqlite3.OperationalError("database is locked")

        with patch("storage.coordination.bus_core.time.sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                always_busy()

    def test_does_not_retry_non_busy_errors(self):
        """Non-BUSY OperationalError should propagate immediately."""
        call_count = 0

        @retry_on_busy
        def bad_sql():
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("no such table: foo")

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            bad_sql()
        self.assertIn("no such table", str(ctx.exception))
        self.assertEqual(call_count, 1)  # no retry

    def test_exponential_backoff(self):
        """Sleep durations should increase exponentially."""
        sleep_times = []

        @retry_on_busy
        def always_busy():
            raise sqlite3.OperationalError("database is locked")

        with patch("storage.coordination.bus_core.time.sleep", side_effect=lambda t: sleep_times.append(t)):
            with self.assertRaises(sqlite3.OperationalError):
                always_busy()

        # Verify exponential growth: each sleep should be ~2x the previous
        for i in range(1, len(sleep_times)):
            self.assertAlmostEqual(
                sleep_times[i] / sleep_times[i - 1], 2.0, places=1,
                msg=f"Backoff not exponential: {sleep_times}",
            )

    def test_preserves_function_name(self):
        """The decorator should preserve the wrapped function's __name__."""
        @retry_on_busy
        def my_special_func():
            pass
        self.assertEqual(my_special_func.__name__, "my_special_func")

    def test_detects_busy_via_errorcode(self):
        """Should detect SQLITE_BUSY via sqlite_errorcode attribute (Python 3.11+)."""
        call_count = 0

        @retry_on_busy
        def busy_with_code():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                err = sqlite3.OperationalError("something")
                err.sqlite_errorcode = 5  # SQLITE_BUSY
                raise err
            return "recovered"

        with patch("storage.coordination.bus_core.time.sleep"):
            result = busy_with_code()
        self.assertEqual(result, "recovered")


@unittest.skipUnless(_BUS_CORE_AVAILABLE, "bus_core.py not yet created by Team 5")
class TestValidMsgTypes(unittest.TestCase):
    """Tests for VALID_MSG_TYPES enforcement."""

    def test_valid_msg_types_is_frozenset(self):
        """VALID_MSG_TYPES should be a frozenset."""
        self.assertIsInstance(VALID_MSG_TYPES, frozenset)

    def test_expected_types_present(self):
        """All expected message types should be present."""
        expected = {"info", "blocker", "phase-signal", "heartbeat", "request", "response"}
        self.assertTrue(
            expected.issubset(VALID_MSG_TYPES),
            f"Missing types: {expected - VALID_MSG_TYPES}",
        )

    def test_callers_should_validate_msg_type(self):
        """VALID_MSG_TYPES should be usable for caller-side validation."""
        # bus_write itself does not enforce msg_type (fire-and-forget design),
        # but callers (e.g. direct_channels.send_direct) should validate.
        self.assertNotIn("INVALID_TYPE", VALID_MSG_TYPES)
        self.assertIn("info", VALID_MSG_TYPES)
        self.assertIn("blocker", VALID_MSG_TYPES)

    def test_bus_write_with_all_valid_types(self):
        """bus_write should succeed with every type in VALID_MSG_TYPES."""
        tmpdir = tempfile.mkdtemp(prefix="bus_test_")
        try:
            for msg_type in VALID_MSG_TYPES:
                result = bus_write(tmpdir, "test", msg_type, {}, "t1", "a1")
                self.assertIsInstance(result, str,
                                     f"bus_write failed for msg_type={msg_type!r}")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


@unittest.skipUnless(_BUS_CORE_AVAILABLE, "bus_core.py not yet created by Team 5")
class TestInitDb(unittest.TestCase):
    """Tests for init_db() -- SQLite connection factory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="db_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_connection(self):
        """init_db should return a sqlite3.Connection."""
        conn = init_db(self.db_path)
        try:
            self.assertIsInstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    def test_wal_mode_enabled(self):
        """The connection should have WAL journal mode."""
        conn = init_db(self.db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
        finally:
            conn.close()

    def test_busy_timeout_set(self):
        """The connection should have a busy_timeout configured (default 5000ms)."""
        conn = init_db(self.db_path)
        try:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(timeout, DEFAULT_BUSY_TIMEOUT_MS)
        finally:
            conn.close()

    def test_row_factory_set(self):
        """The connection should have row_factory = sqlite3.Row."""
        conn = init_db(self.db_path)
        try:
            self.assertEqual(conn.row_factory, sqlite3.Row)
        finally:
            conn.close()

    def test_creates_parent_directory(self):
        """init_db should create parent directories if they don't exist."""
        deep_path = os.path.join(self.tmpdir, "a", "b", "c", "test.db")
        conn = init_db(deep_path)
        try:
            self.assertTrue(os.path.isfile(deep_path))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
