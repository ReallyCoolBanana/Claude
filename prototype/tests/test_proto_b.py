"""Comprehensive test suite for Prototype B (agent_comm_b).

Tests organized into:
1. Basic functionality tests
2. Known bug regression tests (KB-0017 categories)
3. Stress tests specific to Prototype B

Author: Subagent D, TEAM-0019
"""

from __future__ import annotations

import concurrent.futures
import json
import mmap
import os
import shutil
import stat
import struct
import sys
import tempfile
import threading
import time

import pytest

# ---------------------------------------------------------------------------
# Ensure prototype package is importable
# ---------------------------------------------------------------------------
_PROTO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROTO_ROOT not in sys.path:
    sys.path.insert(0, _PROTO_ROOT)

from agent_comm_b.core import (
    CommDir,
    CommDirError,
    AgentIdentity,
    CommConfig,
    setup_comm_environment,
)
from agent_comm_b.bus import (
    Message,
    PipeBusWriter,
    PipeBusReader,
    VALID_MSG_TYPES,
    MAX_MESSAGE_BYTES,
)
from agent_comm_b.state import (
    SharedStateMap,
    _STATUS_ALIVE,
    _STATUS_DEAD,
    _STATUS_EMPTY,
    _TOTAL_SIZE,
    _MAGIC,
)
from agent_comm_b.coordinator import (
    Coordinator,
    CoordinatorWatchdog,
    Worker,
    SpilloverCleaner,
)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory that is NOT under /tmp on Linux.

    On systems where tmp_path resolves under /tmp, we create a subdir
    in the user's home to avoid CommDir's /tmp rejection (EN-4).
    """
    real = os.path.realpath(str(tmp_path))
    if real.startswith("/tmp"):
        # Create a safe temp dir outside /tmp
        safe_dir = os.path.join(os.path.expanduser("~"), ".pytest_proto_b_tmp")
        os.makedirs(safe_dir, exist_ok=True)
        d = tempfile.mkdtemp(dir=safe_dir)
        yield d
        shutil.rmtree(d, ignore_errors=True)
    else:
        yield str(tmp_path)


@pytest.fixture
def comm_dir(tmp_dir):
    """Create and validate a CommDir."""
    cd = CommDir(requested_path=tmp_dir)
    cd.ensure_dirs()
    # Skip validate() since it rejects /tmp paths; dirs are already ensured
    return cd


@pytest.fixture
def pipes_dir(comm_dir):
    return comm_dir.pipes_dir


@pytest.fixture
def shm_dir(comm_dir):
    return comm_dir.shm_dir


@pytest.fixture
def state_map(shm_dir):
    sm = SharedStateMap(shm_dir)
    yield sm
    sm.close()


# ===================================================================
# 1. BASIC FUNCTIONALITY TESTS
# ===================================================================


class TestCommDir:
    """CommDir creation and validation."""

    def test_creation_and_subdirs(self, tmp_dir):
        cd = CommDir(requested_path=tmp_dir)
        cd.ensure_dirs()
        assert os.path.isdir(cd.pipes_dir)
        assert os.path.isdir(cd.shm_dir)

    def test_path_is_canonical(self, tmp_dir):
        cd = CommDir(requested_path=tmp_dir)
        assert cd.path == os.path.realpath(tmp_dir)

    def test_pipes_dir_property(self, tmp_dir):
        cd = CommDir(requested_path=tmp_dir)
        assert cd.pipes_dir.endswith("/pipes")

    def test_shm_dir_property(self, tmp_dir):
        cd = CommDir(requested_path=tmp_dir)
        assert cd.shm_dir.endswith("/shm")

    def test_str_repr(self, tmp_dir):
        cd = CommDir(requested_path=tmp_dir)
        assert str(cd) == cd.path
        assert "CommDir" in repr(cd)


class TestAgentIdentity:
    """AgentIdentity dataclass validation."""

    def test_valid_identity(self):
        ai = AgentIdentity(agent_id="test-1", team="TEAM-01", role="worker")
        assert ai.agent_id == "test-1"
        assert ai.team == "TEAM-01"
        assert ai.role == "worker"
        assert ai.pid == os.getpid()

    def test_coordinator_role(self):
        ai = AgentIdentity(agent_id="coord", team="TEAM-01", role="coordinator")
        assert ai.role == "coordinator"

    def test_empty_agent_id_rejected(self):
        with pytest.raises(ValueError, match="agent_id"):
            AgentIdentity(agent_id="", team="TEAM-01", role="worker")

    def test_empty_team_rejected(self):
        with pytest.raises(ValueError, match="team"):
            AgentIdentity(agent_id="test", team="", role="worker")

    def test_invalid_role_rejected(self):
        with pytest.raises(ValueError, match="role"):
            AgentIdentity(agent_id="test", team="TEAM", role="manager")


class TestCommConfig:
    """CommConfig validation."""

    def test_valid_config(self, tmp_dir):
        cfg = CommConfig(comm_dir=tmp_dir)
        assert cfg.heartbeat_interval == 30.0
        assert cfg.message_max_bytes == 4000

    def test_negative_heartbeat_rejected(self, tmp_dir):
        with pytest.raises(ValueError, match="heartbeat_interval"):
            CommConfig(comm_dir=tmp_dir, heartbeat_interval=-1.0)

    def test_oversized_message_bytes_rejected(self, tmp_dir):
        with pytest.raises(ValueError, match="message_max_bytes"):
            CommConfig(comm_dir=tmp_dir, message_max_bytes=5000)


class TestMessage:
    """Message serialization and deserialization."""

    def test_round_trip(self):
        msg = Message(
            id="test-id",
            type="info",
            channel="global",
            team="T1",
            agent_id="a1",
            ts=1000.0,
            ttl=300,
            body={"key": "value"},
        )
        line = msg.to_json_line()
        assert line.endswith(b"\n")
        restored = Message.from_json_line(line.decode("utf-8"))
        assert restored.id == msg.id
        assert restored.body == msg.body

    def test_valid_message_types(self):
        for t in VALID_MSG_TYPES:
            msg = Message(id="x", type=t, channel="c", team="t", agent_id="a", ts=0.0)
            assert msg.type == t


class TestPipeBusWriterReader:
    """PipeBusWriter.publish() / PipeBusReader.poll() basic tests."""

    def test_publish_creates_fifo(self, pipes_dir):
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        # No reader => spills to file
        msg = writer.publish("test-chan", "info", {"hello": "world"})
        assert msg.type == "info"
        assert msg.agent_id == "agent-1"
        # Spillover file should exist
        spill = os.path.join(pipes_dir, "test-chan.spill")
        assert os.path.exists(spill)

    def test_reader_drains_spillover(self, pipes_dir):
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        writer.publish("test-chan", "info", {"msg": 1})
        writer.publish("test-chan", "info", {"msg": 2})

        reader = PipeBusReader(pipes_dir, "test-chan")
        reader.open()
        messages = reader.poll()
        assert len(messages) == 2
        assert messages[0].body["msg"] == 1
        assert messages[1].body["msg"] == 2
        reader.close()

    def test_write_then_read_via_fifo(self, pipes_dir):
        """Writer writes to FIFO when reader has it open."""
        reader = PipeBusReader(pipes_dir, "fifo-test")
        reader.open()

        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        writer.publish("fifo-test", "info", {"via": "fifo"})

        time.sleep(0.05)
        messages = reader.poll()
        assert len(messages) >= 1
        assert messages[0].body["via"] == "fifo"
        reader.close()

    def test_expired_messages_filtered(self, pipes_dir):
        writer = PipeBusWriter(pipes_dir, "ttl-test", "team-1")
        # Publish with TTL=0 (already expired)
        msg = Message(
            id="expired-1",
            type="info",
            channel="ttl-test",
            team="team-1",
            agent_id="agent-1",
            ts=time.time() - 1000,
            ttl=1,
            body={"expired": True},
        )
        raw = msg.to_json_line()
        spill = os.path.join(pipes_dir, "ttl-test.spill")
        with open(spill, "wb") as f:
            f.write(raw)

        reader = PipeBusReader(pipes_dir, "ttl-test")
        reader.open()
        messages = reader.poll()
        assert len(messages) == 0
        reader.close()

    def test_invalid_msg_type_rejected(self, pipes_dir):
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        with pytest.raises(ValueError, match="Invalid message type"):
            writer.publish("test", "invalid-type", {})

    def test_oversized_message_rejected(self, pipes_dir):
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        big_body = {"data": "x" * 5000}
        with pytest.raises(ValueError, match="PIPE_BUF"):
            writer.publish("test", "info", big_body)


class TestSharedStateMap:
    """SharedStateMap register/heartbeat/get_dead_agents/reserve_api_call."""

    def test_register_agent(self, state_map):
        slot = state_map.register_agent("agent-1", "team-1", "worker", os.getpid())
        assert slot >= 0
        agents = state_map.get_all_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == "agent-1"
        assert agents[0].status == _STATUS_ALIVE

    def test_register_coordinator(self, state_map):
        slot = state_map.register_agent("coord", "team-1", "coordinator", os.getpid())
        assert slot >= 0

    def test_heartbeat(self, state_map):
        state_map.register_agent("agent-1", "team-1", "worker", os.getpid())
        result = state_map.heartbeat("agent-1")
        assert result is True

    def test_heartbeat_nonexistent(self, state_map):
        result = state_map.heartbeat("nonexistent")
        assert result is False

    def test_get_dead_agents(self, state_map):
        state_map.register_agent("agent-1", "team-1", "worker", os.getpid())
        # Initially no dead agents
        dead = state_map.get_dead_agents(timeout=120.0)
        assert len(dead) == 0

        # Manually write an old heartbeat
        slot = state_map._find_slot_by_agent_id("agent-1")
        sv = state_map._read_slot(slot)
        state_map._write_slot(
            slot, sv.agent_id, sv.team, sv.role, sv.pid,
            _STATUS_ALIVE, time.time() - 200, sv.registered_at,
        )
        dead = state_map.get_dead_agents(timeout=120.0)
        assert len(dead) == 1
        assert dead[0].agent_id == "agent-1"

    def test_mark_agent_dead(self, state_map):
        state_map.register_agent("agent-1", "team-1", "worker", os.getpid())
        result = state_map.mark_agent_dead("agent-1")
        assert result is True
        agents = state_map.get_all_agents()
        assert agents[0].status == _STATUS_DEAD

    def test_unregister_agent(self, state_map):
        state_map.register_agent("agent-1", "team-1", "worker", os.getpid())
        result = state_map.unregister_agent("agent-1")
        assert result is True
        agents = state_map.get_all_agents()
        assert len(agents) == 0

    def test_phase_set_get(self, state_map):
        state_map.set_phase("research")
        assert state_map.get_phase() == "research"

    def test_configure_rate_limit(self, state_map):
        state_map.configure_rate_limit("api.example.com", 10, 60)
        # Should be able to reserve
        result = state_map.reserve_api_call("api.example.com", "agent-1")
        assert result is True

    def test_rate_limit_enforced(self, state_map):
        state_map.configure_rate_limit("api.test.com", 3, 60)
        assert state_map.reserve_api_call("api.test.com", "a1") is True
        assert state_map.reserve_api_call("api.test.com", "a2") is True
        assert state_map.reserve_api_call("api.test.com", "a3") is True
        # 4th call should be rejected
        assert state_map.reserve_api_call("api.test.com", "a4") is False

    def test_rate_limit_unconfigured_allows(self, state_map):
        result = state_map.reserve_api_call("unconfigured.api", "agent-1")
        assert result is True

    def test_rate_limit_window_reset(self, state_map):
        state_map.configure_rate_limit("api.window.com", 2, 1)
        assert state_map.reserve_api_call("api.window.com", "a1") is True
        assert state_map.reserve_api_call("api.window.com", "a2") is True
        assert state_map.reserve_api_call("api.window.com", "a3") is False
        # Wait for window to expire
        time.sleep(1.1)
        assert state_map.reserve_api_call("api.window.com", "a4") is True

    def test_register_reregister(self, state_map):
        """Re-registering same agent should update, not duplicate."""
        slot1 = state_map.register_agent("agent-1", "team-1", "worker", os.getpid())
        slot2 = state_map.register_agent("agent-1", "team-1", "worker", os.getpid())
        assert slot1 == slot2
        agents = state_map.get_all_agents()
        assert len(agents) == 1

    def test_max_agents_exceeded(self, state_map):
        """Registering more than 32 agents should raise."""
        for i in range(32):
            state_map.register_agent(f"agent-{i}", "team", "worker", os.getpid())
        with pytest.raises(RuntimeError, match="32"):
            state_map.register_agent("agent-overflow", "team", "worker", os.getpid())


class TestSpilloverCleaner:
    """SpilloverCleaner basic functionality."""

    def test_cleanup_old_spills(self, pipes_dir):
        cleaner = SpilloverCleaner(pipes_dir, retention_seconds=1)
        # Create a spillover file
        spill = os.path.join(pipes_dir, "old-chan.spill")
        with open(spill, "w") as f:
            f.write('{"test": true}\n')
        # Set mtime to old
        old_time = time.time() - 100
        os.utime(spill, (old_time, old_time))

        deleted = cleaner.cleanup_old_spills()
        assert deleted == 1
        assert not os.path.exists(spill)

    def test_get_spill_size(self, pipes_dir):
        cleaner = SpilloverCleaner(pipes_dir)
        spill = os.path.join(pipes_dir, "size-test.spill")
        data = "x" * 1000
        with open(spill, "w") as f:
            f.write(data)
        size = cleaner.get_spill_size()
        assert size >= 1000


# ===================================================================
# 2. KNOWN BUG REGRESSION TESTS (KB-0017 categories)
# ===================================================================


class TestRC4_RateLimitTOCTOU:
    """RC-4: Rate limit TOCTOU race.

    Prototype A used BEGIN IMMEDIATE for atomicity.
    Prototype B uses fcntl.flock() — check if it actually prevents the race.
    """

    def test_concurrent_rate_limit_reservation(self, shm_dir):
        """Hammer reserve_api_call from multiple threads to detect TOCTOU."""
        sm = SharedStateMap(shm_dir)
        sm.configure_rate_limit("api.race.com", 10, 60)

        results = []
        barrier = threading.Barrier(20)

        def attempt_reserve(agent_id):
            barrier.wait()
            result = sm.reserve_api_call("api.race.com", agent_id)
            results.append(result)

        threads = []
        for i in range(20):
            t = threading.Thread(target=attempt_reserve, args=(f"agent-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        sm.close()

        # Exactly 10 should succeed, 10 should fail
        granted = sum(1 for r in results if r is True)
        denied = sum(1 for r in results if r is False)

        # NOTE: flock() within a single process uses the same fd, so
        # intra-process flock is a no-op (flock is per-fd, per-process).
        # This means the lock is INEFFECTIVE for threads sharing the same fd.
        # We expect MORE than 10 grants (the bug).
        assert granted == 10, (
            f"TOCTOU RACE BUG: Expected exactly 10 grants but got {granted}. "
            f"flock() does NOT provide intra-process locking between threads "
            f"sharing the same file descriptor."
        )


class TestSB1_PathCanonicalization:
    """SB-1: Path canonicalization to prevent split-brain."""

    def test_symlink_resolved(self, tmp_dir):
        """CommDir should resolve symlinks to canonical path."""
        real_dir = os.path.join(tmp_dir, "real")
        os.makedirs(real_dir)
        link_dir = os.path.join(tmp_dir, "link")
        os.symlink(real_dir, link_dir)

        cd = CommDir(requested_path=link_dir)
        assert cd.path == os.path.realpath(link_dir)
        assert cd.path == real_dir

    def test_canonical_mismatch_raises(self, tmp_dir):
        """CommDir.validate() should reject symlinked paths."""
        real_dir = os.path.join(tmp_dir, "real_comm")
        os.makedirs(real_dir)
        link_dir = os.path.join(tmp_dir, "link_comm")
        os.symlink(real_dir, link_dir)

        cd = CommDir(requested_path=link_dir)
        cd.ensure_dirs()
        # validate() should raise because link_dir != realpath(link_dir)
        with pytest.raises(CommDirError, match="[Cc]anonical"):
            cd.validate()


class TestCR3_CoordinatorDeathDetection:
    """CR-3: Coordinator death detection via CoordinatorWatchdog.

    The watchdog uses SharedStateMap.get_heartbeat() — which does not exist.
    This tests whether the watchdog actually works.
    """

    def test_watchdog_get_heartbeat_method_exists(self, shm_dir):
        """SharedStateMap must have a get_heartbeat() method for
        CoordinatorWatchdog to work."""
        sm = SharedStateMap(shm_dir)
        has_method = hasattr(sm, "get_heartbeat")
        sm.close()
        assert has_method, (
            "BUG: SharedStateMap has no get_heartbeat() method, "
            "but CoordinatorWatchdog.is_coordinator_alive() calls it. "
            "This means coordinator death detection is BROKEN."
        )

    def test_watchdog_is_coordinator_alive(self, tmp_dir):
        """CoordinatorWatchdog.is_coordinator_alive() should work."""
        cd = CommDir(requested_path=tmp_dir)
        cd.ensure_dirs()

        # Register a coordinator in the state map
        sm = SharedStateMap(cd.shm_dir)
        sm.register_agent("coordinator", "system", "coordinator", os.getpid())
        sm.close()

        try:
            wd = CoordinatorWatchdog(tmp_dir, coordinator_timeout=300.0)
            alive = wd.is_coordinator_alive()
            wd._state.close()
            assert alive is True
        except AttributeError as e:
            pytest.fail(
                f"BUG: CoordinatorWatchdog.is_coordinator_alive() raised "
                f"AttributeError: {e}. SharedStateMap is missing get_heartbeat()."
            )


class TestEN4_TmpRejection:
    """EN-4: /tmp rejection."""

    def test_tmp_path_rejected(self):
        """CommDir.validate() must reject paths under /tmp."""
        cd = CommDir(requested_path="/tmp/test-comm-dir")
        cd._path = "/tmp/test-comm-dir"  # Force canonical to /tmp
        with pytest.raises(CommDirError, match="/tmp"):
            cd._check_no_tmp()

    def test_non_tmp_path_accepted(self, tmp_dir):
        """Non-/tmp paths should pass the /tmp check."""
        cd = CommDir(requested_path=tmp_dir)
        real = os.path.realpath(tmp_dir)
        if not real.startswith("/tmp"):
            cd._check_no_tmp()  # Should not raise


# ===================================================================
# 3. COORDINATOR / WORKER BUG TESTS
# ===================================================================


class TestCoordinatorAPIBugs:
    """Test that Coordinator can actually start (API compatibility)."""

    def test_coordinator_register_method(self, tmp_dir):
        """Coordinator.start() calls self._state.register() but
        SharedStateMap has register_agent(). This should fail."""
        cd = CommDir(requested_path=tmp_dir)
        cd.ensure_dirs()

        coord = Coordinator(tmp_dir)
        try:
            coord.start()
            # If we get here, start() succeeded
            coord.stop()
        except AttributeError as e:
            pytest.fail(
                f"BUG: Coordinator.start() failed with AttributeError: {e}. "
                f"Coordinator calls self._state.register() but SharedStateMap "
                f"only has register_agent()."
            )

    def test_coordinator_check_agents_return_type(self, tmp_dir):
        """Coordinator.check_agents() treats get_dead_agents() results as dicts
        but they are AgentSlotView dataclass instances."""
        cd = CommDir(requested_path=tmp_dir)
        cd.ensure_dirs()

        coord = Coordinator(tmp_dir, config={"dead_agent_timeout": 0.001})
        # Manually register an agent with old heartbeat
        coord._state.register_agent("stale-agent", "team", "worker", 99999)
        time.sleep(0.01)

        try:
            dead = coord.check_agents()
            # If check_agents returned without error, great
        except TypeError as e:
            pytest.fail(
                f"BUG: Coordinator.check_agents() raised TypeError: {e}. "
                f"It accesses agent['agent_id'] but get_dead_agents() returns "
                f"AgentSlotView dataclass instances, not dicts."
            )
        finally:
            coord._state.close()
            coord._global_reader.close()
            coord._blocker_reader.close()


class TestWorkerAPIBugs:
    """Test that Worker can actually start."""

    def test_worker_register_method(self, tmp_dir):
        """Worker.start() calls self._state.register() but
        SharedStateMap has register_agent()."""
        cd = CommDir(requested_path=tmp_dir)
        cd.ensure_dirs()

        worker = Worker("test-worker", "team-1", tmp_dir)
        try:
            worker.start()
            worker.stop()
        except AttributeError as e:
            pytest.fail(
                f"BUG: Worker.start() failed with AttributeError: {e}. "
                f"Worker calls self._state.register() but SharedStateMap "
                f"only has register_agent()."
            )


# ===================================================================
# 4. STRESS TESTS SPECIFIC TO PROTOTYPE B
# ===================================================================


class TestConcurrentFIFOWriters:
    """Test PIPE_BUF atomicity with concurrent FIFO writers."""

    def test_concurrent_writes_no_interleaving(self, pipes_dir):
        """Multiple threads writing to same FIFO should not interleave.

        PIPE_BUF on Linux is 4096 bytes. Messages under this threshold
        should be atomic with a single write() syscall.
        """
        reader = PipeBusReader(pipes_dir, "concurrent-test")
        reader.open()

        errors = []
        msg_count = 50

        def writer_fn(agent_id, count):
            w = PipeBusWriter(pipes_dir, agent_id, "team")
            for i in range(count):
                try:
                    w.publish("concurrent-test", "info", {
                        "agent": agent_id,
                        "seq": i,
                    })
                except Exception as e:
                    errors.append(str(e))

        threads = []
        for tid in range(5):
            t = threading.Thread(
                target=writer_fn, args=(f"writer-{tid}", msg_count)
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        time.sleep(0.2)
        messages = reader.poll()
        reader.close()

        # All 250 messages should be parseable (no interleaving)
        assert len(errors) == 0, f"Write errors: {errors}"
        # We may not get all 250 due to timing, but those we got should be valid
        for msg in messages:
            assert msg.type == "info"
            assert "agent" in msg.body


class TestMmapConcurrentWrites:
    """Test mmap corruption under concurrent writes."""

    def test_concurrent_register_heartbeat(self, shm_dir):
        """Multiple threads registering and heartbeating simultaneously."""
        sm = SharedStateMap(shm_dir)
        errors = []

        def agent_lifecycle(agent_id):
            try:
                sm.register_agent(agent_id, "team", "worker", os.getpid())
                for _ in range(10):
                    sm.heartbeat(agent_id)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"{agent_id}: {e}")

        threads = []
        for i in range(10):
            t = threading.Thread(target=agent_lifecycle, args=(f"agent-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        sm.close()
        assert len(errors) == 0, f"Concurrent mmap errors: {errors}"

    def test_flock_ineffective_for_threads(self, shm_dir):
        """fcntl.flock() is per-fd per-process. Multiple threads sharing
        the same fd will NOT be serialized by flock.

        This is a fundamental design flaw if multi-threaded access is expected.
        """
        sm = SharedStateMap(shm_dir)
        sm.configure_rate_limit("thread-test-api", 5, 60)

        results = []
        barrier = threading.Barrier(15)

        def attempt(agent_id):
            barrier.wait()
            r = sm.reserve_api_call("thread-test-api", agent_id)
            results.append(r)

        threads = []
        for i in range(15):
            t = threading.Thread(target=attempt, args=(f"a-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        sm.close()

        granted = sum(1 for r in results if r is True)
        # If flock works properly between threads, exactly 5 should be granted.
        # If flock is ineffective (same fd), more than 5 may be granted.
        if granted > 5:
            pytest.fail(
                f"FLOCK THREADING BUG: {granted} grants instead of 5. "
                f"fcntl.flock() is per-fd per-process and does NOT serialize "
                f"threads sharing the same file descriptor. This means the "
                f"rate limiter is broken under multi-threaded access."
            )


class TestFIFOBlocking:
    """Test FIFO behavior when no reader exists."""

    def test_write_without_reader_spills(self, pipes_dir):
        """When no reader has the FIFO open, writes should go to spillover."""
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        msg = writer.publish("no-reader-chan", "info", {"test": True})
        assert msg is not None

        # Should have spilled
        spill = os.path.join(pipes_dir, "no-reader-chan.spill")
        assert os.path.exists(spill)

    def test_fifo_nonblocking_write_doesnt_hang(self, pipes_dir):
        """Publishing without a reader should not block/hang."""
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        start = time.time()
        for i in range(10):
            writer.publish("hang-test", "info", {"i": i})
        elapsed = time.time() - start
        # Should complete very quickly (well under 1 second)
        assert elapsed < 2.0, f"Write took {elapsed}s — possible blocking"


class TestSpilloverAccumulation:
    """Test that spillover files accumulate and can be managed."""

    def test_spillover_grows_without_reader(self, pipes_dir):
        """Without a reader, spillover files should accumulate messages."""
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        for i in range(100):
            writer.publish("spill-grow", "info", {"i": i})

        spill = os.path.join(pipes_dir, "spill-grow.spill")
        assert os.path.exists(spill)
        size = os.path.getsize(spill)
        assert size > 0
        # Each message is roughly 100-200 bytes, so 100 should be >5KB
        assert size > 5000, f"Spillover too small: {size} bytes"

    def test_spillover_drained_on_reader_poll(self, pipes_dir):
        """Reader.poll() should drain and delete the spillover file."""
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        for i in range(10):
            writer.publish("drain-test", "info", {"i": i})

        spill = os.path.join(pipes_dir, "drain-test.spill")
        assert os.path.exists(spill)

        reader = PipeBusReader(pipes_dir, "drain-test")
        reader.open()
        messages = reader.poll()
        reader.close()

        assert len(messages) == 10
        # Spillover should be deleted after drain
        assert not os.path.exists(spill)

    def test_multiple_channels_spillover(self, pipes_dir):
        """Multiple channels each create their own spillover files."""
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        channels = ["chan-a", "chan-b", "chan-c"]
        for ch in channels:
            writer.publish(ch, "info", {"channel": ch})

        for ch in channels:
            spill = os.path.join(pipes_dir, f"{ch}.spill")
            assert os.path.exists(spill)


class TestMmapIntegrity:
    """Verify mmap file integrity under various conditions."""

    def test_magic_bytes_correct(self, shm_dir):
        sm = SharedStateMap(shm_dir)
        assert sm._mm[:8] == _MAGIC
        sm.close()

    def test_close_and_reopen(self, shm_dir):
        """State should persist across close/reopen."""
        sm1 = SharedStateMap(shm_dir)
        sm1.register_agent("persist-test", "team", "worker", os.getpid())
        sm1.close()

        sm2 = SharedStateMap(shm_dir)
        agents = sm2.get_all_agents()
        assert len(agents) >= 1
        found = any(a.agent_id == "persist-test" for a in agents)
        assert found, "Agent data did not persist across close/reopen"
        sm2.close()

    def test_init_header_lock_bug(self, shm_dir):
        """_init_header() calls self._lock() which creates _lock(fd=None),
        resulting in a NO-OP lock. This is a bug — it should use self._flock().
        """
        sm = SharedStateMap(shm_dir)
        # The _lock class when called with no args (as self._lock())
        # creates _lock(fd=None) which silently does nothing in __enter__/__exit__.
        # Verify by checking the _lock class behavior:
        noop_lock = SharedStateMap._lock()  # fd=None
        noop_lock.__enter__()
        noop_lock.__exit__()
        # This doesn't actually lock anything!

        # The correct call would be self._flock() which passes self._fd.
        # Check if _init_header uses self._lock() (class call, broken)
        # vs self._flock() (instance method, correct).
        import inspect
        source = inspect.getsource(sm._init_header)
        uses_lock = "self._lock()" in source
        uses_flock = "self._flock()" in source

        sm.close()

        if uses_lock and not uses_flock:
            pytest.fail(
                "BUG: _init_header() uses self._lock() which creates a "
                "_lock(fd=None) — a NO-OP lock. It should use self._flock() "
                "which correctly passes self._fd for real locking."
            )


# ===================================================================
# 5. EDGE CASE TESTS
# ===================================================================


class TestEdgeCases:
    """Various edge cases."""

    def test_channel_sanitization(self, pipes_dir):
        """Channel names with / and .. should be sanitized."""
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        msg = writer.publish("path/traversal/../evil", "info", {"test": True})
        # Should create a safe filename
        assert msg is not None
        # The FIFO should not create files outside pipes_dir
        for f in os.listdir(pipes_dir):
            full = os.path.join(pipes_dir, f)
            assert os.path.dirname(os.path.realpath(full)) == os.path.realpath(pipes_dir)

    def test_empty_body_message(self, pipes_dir):
        writer = PipeBusWriter(pipes_dir, "agent-1", "team-1")
        msg = writer.publish("empty-test", "info", {})
        assert msg.body == {}

    def test_reader_open_close_idempotent(self, pipes_dir):
        reader = PipeBusReader(pipes_dir, "idempotent-test")
        reader.open()
        reader.open()  # Should not raise
        reader.close()
        reader.close()  # Should not raise

    def test_multiple_state_maps_same_file(self, shm_dir):
        """Two SharedStateMap instances on the same file should work
        (simulates multi-process access)."""
        sm1 = SharedStateMap(shm_dir)
        sm2 = SharedStateMap(shm_dir)
        sm1.register_agent("from-sm1", "team", "worker", os.getpid())
        # sm2 should see the agent (same mmap file)
        agents = sm2.get_all_agents()
        found = any(a.agent_id == "from-sm1" for a in agents)
        assert found, "Second SharedStateMap instance cannot see first's data"
        sm1.close()
        sm2.close()


# ===================================================================
# Run
# ===================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
