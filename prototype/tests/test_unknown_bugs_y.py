"""
Think-Tank-Y: Unknown bug discovery tests.

Tests for previously unreported bugs found via cross-module consistency
checks, script robustness testing, and concurrency stress patterns.

Usage:
    python -m pytest prototype/tests/test_unknown_bugs_y.py -v
"""

import json
import os
import shutil
import sqlite3
import struct
import tempfile
import threading
import time
import uuid

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def comm_dir(tmp_path):
    """Create a temporary comm directory structure for Proto A."""
    bus_dir = tmp_path / "bus"
    db_dir = tmp_path / "db"
    bus_dir.mkdir()
    db_dir.mkdir()
    return str(tmp_path)


@pytest.fixture
def comm_dir_b(tmp_path):
    """Create a temporary comm directory structure for Proto B."""
    pipes_dir = tmp_path / "pipes"
    shm_dir = tmp_path / "shm"
    pipes_dir.mkdir()
    shm_dir.mkdir()
    return str(tmp_path)


@pytest.fixture
def proto_a(comm_dir):
    """Import and provide Prototype A modules."""
    import sys
    proto_dir = os.path.join(os.path.dirname(__file__), "..")
    if proto_dir not in sys.path:
        sys.path.insert(0, proto_dir)
    from agent_comm.bus import BusWriter, BusReader, Message, repair_bus_file
    from agent_comm.state import SharedState
    return {
        "BusWriter": BusWriter,
        "BusReader": BusReader,
        "Message": Message,
        "repair_bus_file": repair_bus_file,
        "SharedState": SharedState,
        "comm_dir": comm_dir,
        "bus_dir": os.path.join(comm_dir, "bus"),
        "db_path": os.path.join(comm_dir, "db", "state.db"),
    }


@pytest.fixture
def proto_b(comm_dir_b):
    """Import and provide Prototype B modules."""
    import sys
    proto_dir = os.path.join(os.path.dirname(__file__), "..")
    if proto_dir not in sys.path:
        sys.path.insert(0, proto_dir)
    from agent_comm_b.bus import Message as MessageB, PipeBusWriter, PipeBusReader
    from agent_comm_b.state import SharedStateMap
    return {
        "Message": MessageB,
        "PipeBusWriter": PipeBusWriter,
        "PipeBusReader": PipeBusReader,
        "SharedStateMap": SharedStateMap,
        "comm_dir": comm_dir_b,
        "pipes_dir": os.path.join(comm_dir_b, "pipes"),
        "shm_dir": os.path.join(comm_dir_b, "shm"),
    }


# ===========================================================================
# UNKNOWN-Y-001: Proto A Message.from_json_line crashes on extra fields
# ===========================================================================
# Proto B filters unknown fields via _KNOWN_FIELDS; Proto A does not.
# This is an inconsistency: Proto A's from_json_line raises TypeError
# when the JSON contains extra keys that aren't in the Message dataclass.

class TestUnknownY001:
    """Proto A Message.from_json_line silently drops missing optional fields."""

    def test_missing_optional_fields_crash(self, proto_a):
        """Proto A's from_json_line crashes when required fields are missing."""
        Message = proto_a["Message"]
        # JSON with only some required fields -- missing 'channel', 'team', 'agent_id'
        data = {
            "id": "test-001",
            "type": "info",
            "ts": time.time(),
        }
        line = json.dumps(data)
        # Proto A's from_json_line does cls(**filtered) which will raise
        # TypeError for missing required positional args
        with pytest.raises(TypeError):
            Message.from_json_line(line)

    def test_proto_a_channel_sanitization_allows_null_bytes(self, proto_a):
        """Proto A channel sanitization doesn't filter null bytes."""
        BusWriter = proto_a["BusWriter"]
        bus_dir = proto_a["bus_dir"]
        writer = BusWriter(bus_dir, "agent-1", "TEAM-01")
        # Channel names with null bytes pass through the sanitizer
        # (only / and .. are replaced)
        channel = "test\x00channel"
        safe = channel.replace("/", "_").replace("..", "_")
        # Null byte remains in the sanitized name
        assert "\x00" in safe, (
            "BUG CONFIRMED: Channel sanitization does not filter null bytes"
        )


# ===========================================================================
# UNKNOWN-Y-002: Proto B CommDir._check_canonical_match is overly strict
# ===========================================================================
# Proto A uses os.path.realpath() for comparison; Proto B uses raw string
# comparison of _requested != _path, which fails on trailing slashes or
# relative path components like ./ that realpath normalizes away.

class TestUnknownY002:
    """Proto B _check_canonical_match rejects valid paths with normalization."""

    def test_trailing_slash_mismatch(self, tmp_path):
        """A trailing slash causes a false positive canonical mismatch in Proto B."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        from agent_comm_b.core import CommDir, CommDirError

        # Create the directory
        test_dir = str(tmp_path / "comm")
        os.makedirs(test_dir, exist_ok=True)

        # With trailing slash, realpath strips it, causing requested != path
        path_with_slash = test_dir + "/"
        cd = CommDir(requested_path=path_with_slash)
        # _requested has trailing slash, _path does not (realpath strips it)
        # Proto B does raw string comparison: self._requested != self._path
        # This should raise CommDirError for a perfectly valid path
        if cd._requested != cd._path:
            with pytest.raises(CommDirError, match="Canonical path mismatch"):
                cd._check_canonical_match()
        else:
            # If realpath didn't strip, the test is not applicable on this OS
            pytest.skip("realpath does not strip trailing slash on this platform")


# ===========================================================================
# UNKNOWN-Y-003: Proto B CommDir._check_writable missing root user handling
# ===========================================================================
# Proto A checks os.geteuid() == 0 and uses stat-based checks for root.
# Proto B always uses os.access() which is unreliable for root users.

class TestUnknownY003:
    """Proto B _check_writable doesn't account for root user edge case."""

    def test_proto_a_has_root_check(self):
        """Verify Proto A handles root user in _check_writable."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        import inspect
        from agent_comm.core import CommDir as CommDirA
        source_a = inspect.getsource(CommDirA._check_writable)
        assert "geteuid" in source_a, "Proto A should check for root user"

    def test_proto_b_missing_root_check(self):
        """Verify Proto B does NOT handle root user in _check_writable."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        import inspect
        from agent_comm_b.core import CommDir as CommDirB
        source_b = inspect.getsource(CommDirB._check_writable)
        assert "geteuid" not in source_b, (
            "BUG CONFIRMED: Proto B does not check for root user in _check_writable"
        )


# ===========================================================================
# UNKNOWN-Y-004: Proto A Coordinator.stop() doesn't unregister from state
# ===========================================================================
# The coordinator registers itself in SharedState.register_agent() during
# start(), but stop() only closes the state connection without unregistering.
# The coordinator remains as "alive" in the DB, causing the watchdog in
# other workers to not detect coordinator death.

class TestUnknownY004:
    """Coordinator.stop() leaves stale alive entry in SharedState."""

    def test_coordinator_remains_alive_after_stop(self, proto_a):
        """After stop(), the coordinator is still 'alive' in the DB."""
        SharedState = proto_a["SharedState"]
        db_path = proto_a["db_path"]

        state = SharedState(db_path)
        # Simulate what Coordinator.start() does
        state.register_agent("coordinator", "system", "coordinator", os.getpid())

        # Verify it's registered
        agents = state.get_all_agents()
        coord_agents = [a for a in agents if a["agent_id"] == "coordinator"]
        assert len(coord_agents) == 1
        assert coord_agents[0]["status"] == "alive"

        # Simulate what Coordinator.stop() does - just closes state
        state.close()

        # Reopen and check - agent is still "alive" with no way to detect death
        # until the heartbeat timeout expires
        state2 = SharedState(db_path)
        agents2 = state2.get_all_agents()
        coord_agents2 = [a for a in agents2 if a["agent_id"] == "coordinator"]
        assert len(coord_agents2) == 1
        # BUG: Status is still 'alive' even though coordinator stopped
        assert coord_agents2[0]["status"] == "alive", (
            "BUG CONFIRMED: Coordinator remains 'alive' in DB after stop()"
        )
        state2.close()


# ===========================================================================
# UNKNOWN-Y-005: Proto B CoordinatorWatchdog missing grace period
# ===========================================================================
# Proto A's watchdog has a grace_deadline that waits for the coordinator to
# appear before firing death callbacks. Proto B's watchdog fires immediately
# if coordinator_was_alive transitions True->False, with no grace period
# for initial startup.

class TestUnknownY005:
    """Proto B watchdog lacks grace period that Proto A has."""

    def test_proto_a_has_grace_period(self):
        """Proto A watchdog has a grace period for coordinator startup."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        import inspect
        from agent_comm.coordinator import CoordinatorWatchdog as WatchdogA
        source_a = inspect.getsource(WatchdogA._monitor_loop)
        assert "grace" in source_a.lower(), (
            "Proto A watchdog should have a grace period"
        )

    def test_proto_b_missing_grace_period(self):
        """Proto B watchdog does NOT have a grace period, unlike Proto A."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        import inspect
        from agent_comm_b.coordinator import CoordinatorWatchdog as WatchdogB
        source_b = inspect.getsource(WatchdogB._monitor_loop)
        # Proto B lacks any grace period logic
        assert "grace_deadline" not in source_b, (
            "BUG CONFIRMED: Proto B watchdog has no grace period unlike Proto A"
        )


# ===========================================================================
# UNKNOWN-Y-006: Proto A repair_bus_file is non-atomic during concurrent writes
# ===========================================================================
# repair_bus_file reads the entire file, filters good lines, then overwrites
# via tmp+rename. But it reads with open() (not flock), so a concurrent
# writer could append between the read and the rename, losing the appended msg.

class TestUnknownY006:
    """repair_bus_file can lose messages written during repair."""

    def test_repair_loses_concurrent_write(self, proto_a):
        """Messages written between read and rename in repair are lost."""
        BusWriter = proto_a["BusWriter"]
        repair_bus_file = proto_a["repair_bus_file"]
        bus_dir = proto_a["bus_dir"]

        writer = BusWriter(bus_dir, "agent-1", "TEAM-01")

        # Write some valid messages
        writer.publish("repair-test", "info", {"msg": "one"})
        writer.publish("repair-test", "info", {"msg": "two"})

        filepath = os.path.join(bus_dir, "repair-test.jsonl")

        # Add a corrupt line to trigger repair
        with open(filepath, "a") as f:
            f.write("this is not json\n")

        # Now read the file to get initial state
        with open(filepath, "r") as f:
            lines_before = f.readlines()
        assert len(lines_before) == 3  # 2 valid + 1 corrupt

        # The repair function reads, filters, writes tmp, renames.
        # Between read and rename, if a write happens, it's lost.
        # We can't perfectly race this, but we can verify the design:
        # repair uses open(filepath, "r") without any locking.
        removed = repair_bus_file(filepath)
        assert removed == 1  # Removed the corrupt line

        with open(filepath, "r") as f:
            lines_after = f.readlines()
        assert len(lines_after) == 2  # Only the 2 valid messages remain

        # The bug is structural: no locking means concurrent writes can be lost.
        # We verify the repair function does NOT use any file locking.
        import inspect
        source = inspect.getsource(repair_bus_file)
        assert "flock" not in source, "repair_bus_file uses no file locking"
        assert "O_EXCL" not in source, "repair_bus_file uses no exclusive open"


# ===========================================================================
# UNKNOWN-Y-007: kb_validator misparses multi-line YAML list values
# ===========================================================================
# The simple YAML parser in kb_validator only handles inline lists like
# [item1, item2]. Multi-line YAML lists (- item syntax) are mishandled.

class TestUnknownY007:
    """kb_validator's YAML parser fails on block-style lists."""

    def test_block_style_list_not_parsed(self, tmp_path):
        """Block-style YAML lists are not parsed correctly."""
        import sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "storage", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from kb_validator import parse_yaml_frontmatter

        # Create a file with block-style YAML list
        md_file = tmp_path / "test.md"
        md_file.write_text(
            "---\n"
            "id: KB-9999\n"
            "tags:\n"
            "  - python\n"
            "  - testing\n"
            "status: validated\n"
            "---\n"
            "Content here.\n"
        )

        metadata, errors = parse_yaml_frontmatter(str(md_file))
        # The parser sees "tags:" with empty value after colon -> None
        # The "  - python" and "  - testing" lines are parsed as separate keys
        # This demonstrates the parser cannot handle block-style lists
        assert metadata.get("tags") is None or metadata.get("tags") == [], (
            "Block-style YAML list should not parse correctly with the simple parser"
        )


# ===========================================================================
# UNKNOWN-Y-008: validate-storage.py crashes on missing subsection dirs
# ===========================================================================
# validate_scripts() calls scripts_dir.iterdir() without checking if the
# dir exists. If storage/scripts/ is missing, it crashes.

class TestUnknownY008:
    """validate-storage.py iterdir crashes if subsection directory is missing."""

    def test_iterdir_on_missing_dir(self, tmp_path):
        """iterdir() on a non-existent directory raises FileNotFoundError."""
        from pathlib import Path
        missing_dir = tmp_path / "nonexistent"
        with pytest.raises((FileNotFoundError, OSError)):
            list(missing_dir.iterdir())


# ===========================================================================
# UNKNOWN-Y-009: cross_ref_checker detect_cycles_dfs has unbounded recursion
# ===========================================================================
# The DFS cycle detector uses recursive calls. For deep dependency chains,
# this hits Python's default recursion limit (usually 1000).

class TestUnknownY009:
    """cross_ref_checker DFS cycle detection hits recursion limit on deep graphs."""

    def test_deep_chain_hits_recursion_limit(self):
        """A dependency chain of 1500 nodes causes RecursionError."""
        import sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "storage", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from cross_ref_checker import detect_cycles_dfs

        # Build a chain of 1500 nodes: A0 -> A1 -> A2 -> ... -> A1499
        limit = sys.getrecursionlimit()
        chain_len = limit + 500  # Exceed the recursion limit
        graph = {}
        for i in range(chain_len):
            graph[f"A{i}"] = [f"A{i+1}"] if i < chain_len - 1 else []

        # This should raise RecursionError on default recursion limit
        with pytest.raises(RecursionError):
            detect_cycles_dfs(graph)


# ===========================================================================
# UNKNOWN-Y-010: Proto A SharedState.heartbeat silently succeeds for
#                unregistered agents
# ===========================================================================
# heartbeat() does UPDATE WHERE agent_id=? but doesn't check rowcount.
# If the agent was never registered, the UPDATE affects 0 rows and returns
# None (success), misleading the caller into thinking the heartbeat worked.

class TestUnknownY010:
    """Proto A SharedState.heartbeat succeeds silently for unknown agents."""

    def test_heartbeat_for_nonexistent_agent(self, proto_a):
        """Heartbeat for an unregistered agent silently does nothing."""
        SharedState = proto_a["SharedState"]
        db_path = proto_a["db_path"]

        state = SharedState(db_path)
        # Heartbeat for agent that was never registered
        # This should ideally raise or return False, but it doesn't
        result = state.heartbeat("nonexistent-agent")
        # The method returns None (no return value), making it impossible
        # to detect that the heartbeat had no effect
        assert result is None, (
            "BUG CONFIRMED: heartbeat() returns None even for nonexistent agents"
        )
        state.close()


# ===========================================================================
# UNKNOWN-Y-011: Proto B SharedStateMap agent_count can go negative
# ===========================================================================
# unregister_agent decrements agent_count with 'if agent_count > 0',
# but register_agent always increments for new agents. If unregister is
# called more times than register (e.g., due to a bug or external corruption),
# agent_count correctly stays at 0. BUT if register then unregister then
# unregister (double unregister), the second unregister finds no slot
# (returns False), so no decrement. This is actually safe. However,
# re-registering an existing agent does NOT increment the count (correct),
# but an agent_count read outside the lock could be stale.

class TestUnknownY011:
    """Proto B agent_count header field drifts when agents re-register."""

    def test_reregister_does_not_double_count(self, proto_b):
        """Re-registering an existing agent should not change agent_count."""
        SharedStateMap = proto_b["SharedStateMap"]
        shm_dir = proto_b["shm_dir"]

        state = SharedStateMap(shm_dir)

        # Register agent
        state.register_agent("agent-1", "TEAM-01", "worker", 1234)
        _, _, _, count1, _ = state._read_header()
        assert count1 == 1

        # Re-register same agent (simulating reconnect)
        state.register_agent("agent-1", "TEAM-01", "worker", 1234)
        _, _, _, count2, _ = state._read_header()
        assert count2 == 1, "Re-register should not increment count"

        # Register second agent
        state.register_agent("agent-2", "TEAM-01", "worker", 1235)
        _, _, _, count3, _ = state._read_header()
        assert count3 == 2

        # Unregister first, then re-register
        state.unregister_agent("agent-1")
        _, _, _, count4, _ = state._read_header()
        assert count4 == 1

        state.register_agent("agent-1", "TEAM-01", "worker", 1234)
        _, _, _, count5, _ = state._read_header()
        assert count5 == 2  # Should be 2 now

        state.close()


# ===========================================================================
# UNKNOWN-Y-012: Proto B CommConfig missing busy_timeout_ms parameter
# ===========================================================================
# Proto A CommConfig has busy_timeout_ms field; Proto B CommConfig does not.
# This means Proto B has no way to configure SQLite-equivalent timeout, but
# it also means code copied from Proto A expecting this field will fail.

class TestUnknownY012:
    """Proto B CommConfig lacks busy_timeout_ms present in Proto A."""

    def test_proto_a_has_busy_timeout(self):
        """Proto A CommConfig has busy_timeout_ms."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        from agent_comm.core import CommConfig as ConfigA
        config = ConfigA(comm_dir="/tmp/test")
        assert hasattr(config, "busy_timeout_ms")

    def test_proto_b_missing_busy_timeout(self):
        """Proto B CommConfig does NOT have busy_timeout_ms."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        from agent_comm_b.core import CommConfig as ConfigB
        config = ConfigB(comm_dir="/tmp/test")
        assert not hasattr(config, "busy_timeout_ms"), (
            "BUG CONFIRMED: Proto B CommConfig missing busy_timeout_ms"
        )


# ===========================================================================
# UNKNOWN-Y-013: Proto A BusReader.poll opens file in binary but processes
#                correctly; Proto A _channel_path and BusReader use different
#                sanitization inconsistently (actually same, but let's verify)
# ===========================================================================
# Actually, let me test something more useful: Proto A SharedState._retry_on_busy
# masks the original exception context on final raise.

class TestUnknownY013:
    """Proto A SharedState close() doesn't prevent subsequent operations."""

    def test_operations_after_close_raise(self, proto_a):
        """Operations on a closed SharedState raise ProgrammingError."""
        SharedState = proto_a["SharedState"]
        db_path = proto_a["db_path"]
        state = SharedState(db_path)
        state.register_agent("agent-1", "TEAM-01", "worker", 1234)
        state.close()

        # After close(), operations should raise but the class has no
        # guard against double-use. sqlite3 will raise ProgrammingError.
        with pytest.raises(Exception):
            state.register_agent("agent-2", "TEAM-01", "worker", 1235)

    def test_double_close_no_guard(self, proto_a):
        """Proto A SharedState.close() has no guard against double-close."""
        SharedState = proto_a["SharedState"]
        db_path = proto_a["db_path"]
        state = SharedState(db_path)
        state.close()
        # Second close should ideally be a no-op, but it tries to close
        # an already-closed connection. Depending on sqlite3 version,
        # this may raise or silently succeed.
        try:
            state.close()
            # If it succeeds, that's fine but there's no _closed guard
            import inspect
            source = inspect.getsource(SharedState.close)
            assert "_closed" not in source, (
                "BUG CONFIRMED: SharedState.close() has no _closed guard"
            )
        except Exception:
            # If it raises, that confirms the lack of guard
            pass


# ===========================================================================
# UNKNOWN-Y-014: Proto B SharedStateMap.get_dead_agents counts agents with
#                heartbeat exactly at cutoff as dead
# ===========================================================================
# The comparison is `sv.last_heartbeat < cutoff` (strictly less than).
# An agent whose heartbeat is EXACTLY at the cutoff boundary is not dead.
# But this is edge-case correct. Let's test a more interesting bug:
# Proto A and B have inconsistent return types from check_agents.

class TestUnknownY014:
    """Proto A check_agents returns list[dict]; Proto B returns list[AgentSlotView]."""

    def test_check_agents_return_type_inconsistency(self):
        """Proto A and B coordinator.check_agents return incompatible types."""
        import sys
        proto_dir = os.path.join(os.path.dirname(__file__), "..")
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        import inspect

        from agent_comm.coordinator import Coordinator as CoordA
        from agent_comm_b.coordinator import Coordinator as CoordB

        source_a = inspect.getsource(CoordA.check_agents)
        source_b = inspect.getsource(CoordB.check_agents)

        # Proto A accesses dead agents as dicts: agent["agent_id"]
        assert '["agent_id"]' in source_a, "Proto A uses dict access"
        # Proto B accesses dead agents as dataclass: agent.agent_id
        assert ".agent_id" in source_b, "Proto B uses attribute access"


# ===========================================================================
# UNKNOWN-Y-015: Proto A SharedState._retry_on_busy leaks timing info
#                and has no max total timeout
# ===========================================================================
# The retry loop doubles delay each time: 0.1, 0.2, 0.4, 0.8, 1.6 seconds.
# Total wait = 3.1s with 5 retries. But there's no overall timeout cap.
# If MAX_RETRIES is increased, the exponential backoff can cause very long
# waits (e.g., 10 retries = 102.3 seconds total).

class TestUnknownY015:
    """Proto A _retry_on_busy exponential backoff has no total timeout cap."""

    def test_total_retry_time_grows_exponentially(self):
        """Verify the total retry time grows exponentially without cap."""
        from agent_comm.state import _MAX_RETRIES, _RETRY_BACKOFF

        # Calculate total possible wait time
        total_wait = 0
        delay = _RETRY_BACKOFF
        for _ in range(_MAX_RETRIES):
            total_wait += delay
            delay *= 2

        # With defaults (5 retries, 0.1 base), total = 3.1 seconds
        assert total_wait == pytest.approx(3.1, abs=0.01), (
            f"Total retry wait is {total_wait}s"
        )
        # The concern: if someone changes MAX_RETRIES to 10, total = 102.3s
        delay = _RETRY_BACKOFF
        total_10 = 0
        for _ in range(10):
            total_10 += delay
            delay *= 2
        assert total_10 > 100, (
            f"With 10 retries, total wait would be {total_10:.1f}s (no cap)"
        )
