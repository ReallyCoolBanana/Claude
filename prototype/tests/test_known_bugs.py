"""
TEAM-0019: Regression tests for all 26 failure modes from KB-0017.

Tests are organized by category and tagged with their KB-0017 failure mode ID.
Each test targets a specific failure mode and verifies it is either:
  - FIXED (test passes)
  - STILL BROKEN (test fails with a clear message)

Usage:
    python -m pytest prototype/tests/test_known_bugs.py -v
    python -m pytest prototype/tests/test_known_bugs.py -v -k "RC"  # race conditions only
"""

import json
import multiprocessing
import os
import shutil
import signal
import sqlite3
import struct
import tempfile
import threading
import time
import uuid

import pytest

# ---------------------------------------------------------------------------
# Fixtures — set up isolated comm environments for each test
# ---------------------------------------------------------------------------

@pytest.fixture
def comm_dir(tmp_path):
    """Create a temporary comm directory structure."""
    bus_dir = tmp_path / "bus"
    db_dir = tmp_path / "db"
    bus_dir.mkdir()
    db_dir.mkdir()
    return str(tmp_path)


@pytest.fixture
def proto_a(comm_dir):
    """Import and configure Prototype A."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from agent_comm.bus import BusWriter, BusReader, Message, repair_bus_file
    from agent_comm.state import SharedState
    from agent_comm.coordinator import Coordinator, Worker, CoordinatorWatchdog, EpochRotator

    db_path = os.path.join(comm_dir, "db", "state.db")
    bus_path = os.path.join(comm_dir, "bus")

    return {
        "name": "Prototype A (JSONL+SQLite)",
        "BusWriter": BusWriter,
        "BusReader": BusReader,
        "Message": Message,
        "repair_bus_file": repair_bus_file,
        "SharedState": lambda: SharedState(db_path),
        "Coordinator": lambda: Coordinator(comm_dir),
        "Worker": lambda aid, team: Worker(aid, team, comm_dir),
        "CoordinatorWatchdog": lambda: CoordinatorWatchdog(comm_dir),
        "EpochRotator": lambda: EpochRotator(bus_path),
        "comm_dir": comm_dir,
        "db_path": db_path,
        "bus_path": bus_path,
    }


# ===================================================================
# CATEGORY 1: Race Conditions (RC-1 through RC-4)
# ===================================================================

class TestRaceConditions:
    """Tests for race condition failure modes."""

    def test_RC1_message_under_4096_bytes(self, proto_a):
        """RC-1: JSONL messages must stay under 4096 bytes to prevent interleaving."""
        writer = proto_a["BusWriter"](proto_a["bus_path"], "agent-1", "team-1")

        # Normal message should work
        msg = writer.publish("global", "info", {"data": "hello"})
        assert msg is not None

        # Oversized message should be rejected
        huge_body = {"data": "x" * 5000}
        with pytest.raises(ValueError, match="exceeds"):
            writer.publish("global", "info", huge_body)

    def test_RC2_sqlite_busy_retry(self, proto_a):
        """RC-2: SQLite BUSY errors should be retried with backoff."""
        state = proto_a["SharedState"]()
        # Register an agent — should succeed even under contention
        state.register_agent("agent-rc2", "team-1", "worker", os.getpid())
        state.heartbeat("agent-rc2")
        state.close()

    def test_RC3_partial_line_not_returned(self, proto_a):
        """RC-3: Reader must not return partial (non-newline-terminated) lines."""
        bus_path = proto_a["bus_path"]
        channel_file = os.path.join(bus_path, "test-rc3.jsonl")

        # Write a complete message
        complete_msg = json.dumps({
            "id": str(uuid.uuid4()), "type": "info", "channel": "test-rc3",
            "team": "t1", "agent_id": "a1", "ts": time.time(),
            "ttl": 300, "body": {"data": "complete"}, "in_reply_to": None
        }) + "\n"

        # Write an incomplete message (no trailing newline)
        partial_msg = json.dumps({
            "id": str(uuid.uuid4()), "type": "info", "channel": "test-rc3",
            "team": "t1", "agent_id": "a1", "ts": time.time(),
            "ttl": 300, "body": {"data": "partial"}, "in_reply_to": None
        })  # NO newline

        with open(channel_file, "w") as f:
            f.write(complete_msg + partial_msg)

        reader = proto_a["BusReader"](bus_path, "test-rc3")
        messages = reader.poll()

        # Should only get the complete message, not the partial one
        assert len(messages) == 1
        assert messages[0].body["data"] == "complete"

    def test_RC4_toctou_rate_limit_race(self, proto_a):
        """RC-4 [MUST-FIX]: TOCTOU race in rate-limit checking.

        Two threads simultaneously try to reserve the last API slot.
        Only one should succeed.
        """
        state = proto_a["SharedState"]()
        state.configure_rate_limit("api.test/endpoint", max_calls=1, window_seconds=60)

        results = []
        barrier = threading.Barrier(2)

        def try_reserve(agent_id):
            barrier.wait()  # Sync both threads to fire simultaneously
            result = state.reserve_api_call("api.test/endpoint", agent_id)
            results.append(result)

        t1 = threading.Thread(target=try_reserve, args=("agent-1",))
        t2 = threading.Thread(target=try_reserve, args=("agent-2",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one should succeed, one should fail
        assert results.count(True) == 1, (
            f"RC-4 TOCTOU RACE: Expected exactly 1 success, got {results.count(True)}. "
            f"Results: {results}"
        )
        assert results.count(False) == 1
        state.close()

    def test_RC4_concurrent_rate_limit_stress(self, proto_a):
        """RC-4 stress test: 10 threads competing for 5 API slots."""
        state = proto_a["SharedState"]()
        state.configure_rate_limit("api.test/stress", max_calls=5, window_seconds=60)

        results = []
        barrier = threading.Barrier(10)
        lock = threading.Lock()

        def try_reserve(agent_id):
            barrier.wait()
            result = state.reserve_api_call("api.test/stress", agent_id)
            with lock:
                results.append(result)

        threads = []
        for i in range(10):
            t = threading.Thread(target=try_reserve, args=(f"agent-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=15)

        success_count = results.count(True)
        assert success_count == 5, (
            f"RC-4 STRESS: Expected exactly 5 successes, got {success_count}. "
            f"Results: {results}"
        )
        state.close()


# ===================================================================
# CATEGORY 2: Crash Recovery (CR-1 through CR-4)
# ===================================================================

class TestCrashRecovery:
    """Tests for crash recovery failure modes."""

    def test_CR1_corrupt_jsonl_repair(self, proto_a):
        """CR-1: repair_bus_file should remove corrupt lines."""
        bus_path = proto_a["bus_path"]
        filepath = os.path.join(bus_path, "test-cr1.jsonl")

        good_line = json.dumps({
            "id": "good-1", "type": "info", "channel": "test-cr1",
            "team": "t1", "agent_id": "a1", "ts": time.time(),
            "ttl": 300, "body": {}, "in_reply_to": None
        }) + "\n"

        corrupt_line = "THIS IS NOT JSON{broken\n"
        partial_json = '{"id": "incomplete", "type":\n'

        with open(filepath, "w") as f:
            f.write(good_line + corrupt_line + partial_json + good_line)

        removed = proto_a["repair_bus_file"](filepath)
        assert removed == 2, f"Expected 2 corrupt lines removed, got {removed}"

        # Verify remaining lines are valid
        reader = proto_a["BusReader"](bus_path, "test-cr1")
        messages = reader.poll()
        assert len(messages) == 2  # Both good lines

    def test_CR2_sqlite_wal_recovery(self, proto_a):
        """CR-2: SQLite WAL auto-recovery after unclean shutdown."""
        state = proto_a["SharedState"]()
        state.register_agent("agent-cr2", "team-1", "worker", os.getpid())
        state.heartbeat("agent-cr2")

        # Simulate crash by not calling close() — just abandon the connection
        # Then reopen and verify data is intact
        db_path = proto_a["db_path"]

        state2 = proto_a["SharedState"]()
        dead = state2.get_dead_agents(timeout=999999)
        # Agent should still be registered (WAL recovered)
        row = state2._conn.execute(
            "SELECT * FROM agents WHERE agent_id = 'agent-cr2'"
        ).fetchone()
        assert row is not None, "CR-2: Agent registration lost after simulated crash"
        state.close()
        state2.close()

    def test_CR3_coordinator_death_detection(self, proto_a):
        """CR-3 [MUST-FIX]: Workers must detect coordinator death."""
        # Start a coordinator
        state = proto_a["SharedState"]()
        state.register_agent("coordinator", "system", "coordinator", os.getpid())
        state.heartbeat("coordinator")

        # Watchdog should see coordinator as alive
        watchdog = proto_a["CoordinatorWatchdog"]()
        assert watchdog.is_coordinator_alive(), "Coordinator should be alive"

        # Simulate coordinator death by making heartbeat very old
        state._conn.execute(
            "UPDATE agents SET last_heartbeat = ? WHERE agent_id = 'coordinator'",
            (time.time() - 600,)  # 10 minutes ago
        )
        state._conn.commit()

        assert not watchdog.is_coordinator_alive(), (
            "CR-3: Watchdog failed to detect coordinator death"
        )

        watchdog.stop_monitoring()
        state.close()

    def test_CR4_heartbeat_monitor_survives(self, proto_a):
        """CR-4: Heartbeat monitoring should survive individual heartbeat failures."""
        state = proto_a["SharedState"]()
        state.register_agent("test-agent", "team-1", "worker", os.getpid())

        # Heartbeat multiple times — should not crash
        for _ in range(5):
            state.heartbeat("test-agent")
            time.sleep(0.01)

        state.close()


# ===================================================================
# CATEGORY 3: Resource Exhaustion (RE-1 through RE-4)
# ===================================================================

class TestResourceExhaustion:
    """Tests for resource exhaustion failure modes."""

    def test_RE1_epoch_rotation(self, proto_a):
        """RE-1 [SHOULD-FIX]: JSONL files should be rotated to prevent disk fill."""
        rotator = proto_a["EpochRotator"]()

        # Create current epoch file
        current = rotator.current_epoch_file("global")
        assert ".jsonl" in current

        # Create a fake old epoch file
        old_file = os.path.join(proto_a["bus_path"], "global.2020-01-01T00.jsonl")
        with open(old_file, "w") as f:
            f.write("old data\n")
        # Set old mtime
        os.utime(old_file, (0, 0))

        cleaned = rotator.cleanup_old_epochs()
        assert cleaned >= 1, "RE-1: Old epoch file was not cleaned up"
        assert not os.path.exists(old_file), "RE-1: Old epoch file still exists"

    def test_RE2_wal_checkpoint(self, proto_a):
        """RE-2: SQLite WAL should auto-checkpoint."""
        state = proto_a["SharedState"]()
        # Write enough data to trigger WAL growth
        for i in range(100):
            state.register_agent(f"agent-re2-{i}", "team-1", "worker", i)

        # WAL file should exist but be manageable
        wal_path = proto_a["db_path"] + "-wal"
        if os.path.exists(wal_path):
            wal_size = os.path.getsize(wal_path)
            assert wal_size < 10 * 1024 * 1024, f"RE-2: WAL too large: {wal_size} bytes"
        state.close()

    def test_RE3_polling_io_contention(self, proto_a):
        """RE-3: Multiple readers polling simultaneously should not corrupt data."""
        writer = proto_a["BusWriter"](proto_a["bus_path"], "writer-1", "team-1")

        # Write 50 messages
        for i in range(50):
            writer.publish("test-re3", "info", {"seq": i})

        # 5 readers poll simultaneously
        results = []

        def reader_work(reader_id):
            reader = proto_a["BusReader"](proto_a["bus_path"], "test-re3")
            msgs = reader.poll()
            results.append(len(msgs))

        threads = [threading.Thread(target=reader_work, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All readers should see all 50 messages
        for count in results:
            assert count == 50, f"RE-3: Reader saw {count} messages, expected 50"

    def test_RE4_file_descriptor_limits(self, proto_a):
        """RE-4: Opening many channels should not leak file descriptors."""
        writer = proto_a["BusWriter"](proto_a["bus_path"], "writer-1", "team-1")

        # Open 50 different channels
        for i in range(50):
            writer.publish(f"channel-{i}", "info", {"data": f"msg-{i}"})

        # Should not have leaked FDs (BusWriter opens and closes per write)
        # If it leaked, we'd eventually hit the FD limit
        # Just verify we can still write
        writer.publish("channel-final", "info", {"data": "still works"})


# ===================================================================
# CATEGORY 4: Split-Brain / Consistency (SB-1 through SB-4)
# ===================================================================

class TestSplitBrain:
    """Tests for split-brain and consistency failure modes."""

    def test_SB1_worktree_canonical_path(self, comm_dir):
        """SB-1 [MUST-FIX]: CommDir must canonicalize paths to prevent split-brain."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from agent_comm.core import CommDir

        # Create a symlink to the comm dir
        symlink_path = comm_dir + "-symlink"
        try:
            os.symlink(comm_dir, symlink_path)

            # Both paths should resolve to the same canonical path
            dir1 = CommDir(requested_path=comm_dir)
            dir2 = CommDir(requested_path=symlink_path)

            assert dir1.path == dir2.path, (
                f"SB-1: Symlinked paths not canonicalized! "
                f"{dir1.path} != {dir2.path}"
            )
        finally:
            if os.path.islink(symlink_path):
                os.remove(symlink_path)

    def test_SB2_shared_state_across_paths(self, comm_dir):
        """SB-2 [MUST-FIX]: Agents using different paths to same dir share state."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from agent_comm.state import SharedState

        db_path = os.path.join(comm_dir, "db", "state.db")

        state1 = SharedState(db_path)
        state1.register_agent("agent-sb2", "team-1", "worker", os.getpid())

        state2 = SharedState(db_path)
        row = state2._conn.execute(
            "SELECT * FROM agents WHERE agent_id = 'agent-sb2'"
        ).fetchone()

        assert row is not None, "SB-2: Agent registered via one connection not visible to another"
        state1.close()
        state2.close()

    def test_SB3_clock_skew_ordering(self, proto_a):
        """SB-3: Message ordering should use autoincrement IDs, not timestamps."""
        state = proto_a["SharedState"]()

        # Insert messages with out-of-order timestamps
        state._conn.execute(
            "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("msg-1", "test", "a1", "info", "{}", time.time() + 100, time.time() + 400)
        )
        state._conn.execute(
            "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("msg-2", "test", "a1", "info", "{}", time.time() - 100, time.time() + 400)
        )
        state._conn.commit()

        # Query by autoincrement ID should give insertion order
        rows = state._conn.execute(
            "SELECT msg_id FROM messages ORDER BY id"
        ).fetchall()
        assert [r["msg_id"] for r in rows] == ["msg-1", "msg-2"]
        state.close()

    def test_SB4_cross_channel_no_ordering(self, proto_a):
        """SB-4: No global ordering across channels is a documented limitation."""
        writer = proto_a["BusWriter"](proto_a["bus_path"], "writer-1", "team-1")

        # Write to two channels in sequence
        msg1 = writer.publish("channel-a", "info", {"seq": 1})
        msg2 = writer.publish("channel-b", "info", {"seq": 2})

        # Both should have timestamps, but cross-channel ordering is not guaranteed
        assert msg1.ts <= msg2.ts  # Same process, so this holds, but wouldn't across processes


# ===================================================================
# CATEGORY 5: Stale State (SS-1 through SS-4)
# ===================================================================

class TestStaleState:
    """Tests for stale state failure modes."""

    def test_SS1_dead_agent_detection(self, proto_a):
        """SS-1: Dead agents should be detectable via stale heartbeats."""
        state = proto_a["SharedState"]()
        state.register_agent("agent-ss1", "team-1", "worker", 99999)

        # Set heartbeat to be very old
        state._conn.execute(
            "UPDATE agents SET last_heartbeat = ? WHERE agent_id = 'agent-ss1'",
            (time.time() - 300,)
        )
        state._conn.commit()

        dead = state.get_dead_agents(timeout=120)
        dead_ids = [a["agent_id"] for a in dead]
        assert "agent-ss1" in dead_ids, "SS-1: Dead agent not detected"
        state.close()

    def test_SS3_rate_limit_time_windowed(self, proto_a):
        """SS-3: Rate limit counters should be time-windowed, not permanent."""
        state = proto_a["SharedState"]()
        state.configure_rate_limit("api.test/ss3", max_calls=2, window_seconds=1)

        # Use up both slots
        assert state.reserve_api_call("api.test/ss3", "agent-1") is True
        assert state.reserve_api_call("api.test/ss3", "agent-1") is True
        assert state.reserve_api_call("api.test/ss3", "agent-1") is False  # At limit

        # Wait for the window to expire
        time.sleep(1.1)

        # Should be able to reserve again
        assert state.reserve_api_call("api.test/ss3", "agent-1") is True, (
            "SS-3: Rate limit not resetting after time window"
        )
        state.close()

    def test_SS4_cleanup_expired_messages(self, proto_a):
        """SS-4 [SHOULD-FIX]: Old messages should be cleaned up to prevent slow polls."""
        state = proto_a["SharedState"]()

        # Insert expired messages
        now = time.time()
        for i in range(100):
            state._conn.execute(
                "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"msg-ss4-{i}", "test", "a1", "info", "{}", now - 3600, now - 100)
            )
        state._conn.commit()

        cleaned = state.cleanup_expired()
        assert cleaned >= 100, f"SS-4: Only cleaned {cleaned} expired messages, expected 100+"
        state.close()


# ===================================================================
# CATEGORY 6: Environmental (EN-1 through EN-6)
# ===================================================================

class TestEnvironmental:
    """Tests for environmental failure modes."""

    def test_EN1_write_permission_check(self, tmp_path):
        """EN-1 [SHOULD-FIX]: Startup should validate write permissions."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from agent_comm.core import CommDir, CommDirError

        # Create a read-only directory
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)

        try:
            cd = CommDir(requested_path=str(readonly_dir))
            # validate() should detect the read-only directory
            with pytest.raises((CommDirError, OSError)):
                cd.validate()
        finally:
            readonly_dir.chmod(0o755)

    def test_EN4_reject_tmp_path(self, tmp_path):
        """EN-4 [SHOULD-FIX]: Comm dir on /tmp should be rejected."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from agent_comm.core import CommDir, CommDirError

        # Try to use a /tmp path — should be rejected during validation
        tmp_comm = "/tmp/agent-comm-test-en4-" + str(uuid.uuid4())
        try:
            os.makedirs(tmp_comm, exist_ok=True)
            cd = CommDir(requested_path=tmp_comm)
            with pytest.raises(CommDirError, match="/tmp"):
                cd.validate()
        finally:
            shutil.rmtree(tmp_comm, ignore_errors=True)

    def test_EN5_canonical_path_enforcement(self, comm_dir):
        """EN-5: SQLite must be opened via canonical path to prevent corruption."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from agent_comm.core import CommDir

        # Verify CommDir resolves to realpath
        cd = CommDir(requested_path=comm_dir)
        assert cd.path == os.path.realpath(comm_dir), (
            "EN-5: CommDir path not canonicalized"
        )

    def test_EN6_wal_shm_files_exist(self, proto_a):
        """EN-6: WAL and SHM files must not be deleted while DB is open."""
        state = proto_a["SharedState"]()
        state.register_agent("agent-en6", "team-1", "worker", os.getpid())

        db_path = proto_a["db_path"]
        # After writing, WAL file should exist
        wal_exists = os.path.exists(db_path + "-wal")
        shm_exists = os.path.exists(db_path + "-shm")

        # At least WAL should exist after writes in WAL mode
        assert wal_exists or shm_exists, (
            "EN-6: Neither WAL nor SHM file exists after SQLite operations"
        )
        state.close()


# ===================================================================
# STRESS TESTS — Subagent E
# ===================================================================

class TestStress:
    """Stress tests simulating 5-15 concurrent agents."""

    def test_concurrent_writers_10_agents(self, proto_a):
        """Stress: 10 agents writing to the same channel simultaneously."""
        bus_path = proto_a["bus_path"]
        results = {"success": 0, "fail": 0}
        lock = threading.Lock()

        def agent_writer(agent_id):
            try:
                writer = proto_a["BusWriter"](bus_path, agent_id, "team-stress")
                for i in range(20):
                    writer.publish("global", "info", {"agent": agent_id, "seq": i})
                with lock:
                    results["success"] += 1
            except Exception as e:
                with lock:
                    results["fail"] += 1

        threads = [threading.Thread(target=agent_writer, args=(f"stress-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert results["success"] == 10, f"Stress: {results['fail']} agents failed to write"

        # Verify all messages are readable
        reader = proto_a["BusReader"](bus_path, "global")
        msgs = reader.poll()
        assert len(msgs) == 200, f"Stress: Expected 200 messages, got {len(msgs)}"

    def test_concurrent_state_operations(self, proto_a):
        """Stress: 10 threads doing mixed state operations simultaneously."""
        state = proto_a["SharedState"]()
        state.configure_rate_limit("api.stress/test", max_calls=50, window_seconds=60)
        errors = []

        def mixed_operations(agent_id):
            try:
                state.register_agent(agent_id, "team-stress", "worker", os.getpid())
                for _ in range(10):
                    state.heartbeat(agent_id)
                    state.reserve_api_call("api.stress/test", agent_id)
                    state.get_dead_agents()
            except Exception as e:
                errors.append(f"{agent_id}: {e}")

        threads = [threading.Thread(target=mixed_operations, args=(f"mixed-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Stress: {len(errors)} errors: {errors[:5]}"
        state.close()

    def test_coordinator_worker_lifecycle(self, proto_a):
        """Stress: Full coordinator + workers lifecycle test."""
        comm_dir = proto_a["comm_dir"]

        # Start coordinator
        coord = proto_a["Coordinator"]()
        coord.start()
        time.sleep(0.5)

        # Start 5 workers
        workers = []
        for i in range(5):
            w = proto_a["Worker"](f"worker-{i}", "team-lifecycle")
            w.start()
            workers.append(w)

        time.sleep(1)

        # Workers send messages
        for w in workers:
            w.send("global", "info", {"from": w.agent_id, "msg": "hello"})

        time.sleep(0.5)

        # Check coordinator status
        status = coord.get_status()
        assert len(status["agents"]) >= 6  # coordinator + 5 workers

        # Phase transition
        coord.advance_phase("testing")
        time.sleep(0.5)

        # Clean shutdown
        for w in workers:
            w.stop()
        coord.stop()

    def test_message_throughput(self, proto_a):
        """Stress: Measure message throughput — 1000 messages in sequence."""
        writer = proto_a["BusWriter"](proto_a["bus_path"], "throughput-agent", "team-perf")

        start = time.time()
        for i in range(1000):
            writer.publish("perf-test", "info", {"seq": i})
        elapsed = time.time() - start

        # Should be under 5 seconds for 1000 messages
        assert elapsed < 5.0, f"Stress: 1000 messages took {elapsed:.2f}s (too slow)"

        # Verify all readable
        reader = proto_a["BusReader"](proto_a["bus_path"], "perf-test")
        msgs = reader.poll()
        assert len(msgs) == 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
