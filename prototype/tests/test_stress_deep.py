"""
TEAM-0019 Subagent E: Advanced stress tests for Prototype A.

These go beyond the basic regression suite in test_known_bugs.py to find
NEW bugs through extreme concurrency, edge cases, and adversarial scenarios.

Usage:
    python -m pytest prototype/tests/test_stress_deep.py -v --tb=short
"""

import json
import os
import sqlite3
import sys
import threading
import time
import uuid

import pytest

# ---------------------------------------------------------------------------
# Fixtures
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
def proto(comm_dir):
    """Import and configure Prototype A components."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from agent_comm.bus import BusWriter, BusReader, Message, MAX_MESSAGE_BYTES
    from agent_comm.state import SharedState
    from agent_comm.coordinator import Coordinator, Worker, CoordinatorWatchdog, EpochRotator

    db_path = os.path.join(comm_dir, "db", "state.db")
    bus_path = os.path.join(comm_dir, "bus")

    return {
        "BusWriter": BusWriter,
        "BusReader": BusReader,
        "Message": Message,
        "MAX_MESSAGE_BYTES": MAX_MESSAGE_BYTES,
        "SharedState": lambda: SharedState(db_path),
        "Coordinator": lambda **kw: Coordinator(comm_dir, config=kw or None),
        "Worker": lambda aid, team: Worker(aid, team, comm_dir),
        "CoordinatorWatchdog": lambda: CoordinatorWatchdog(comm_dir),
        "EpochRotator": lambda: EpochRotator(bus_path),
        "comm_dir": comm_dir,
        "db_path": db_path,
        "bus_path": bus_path,
    }


# ===================================================================
# 1. Rapid coordinator restart
# ===================================================================

class TestRapidCoordinatorRestart:
    """Start coordinator, stop it, start a new one immediately.
    Verify state is recovered correctly (CR-3 stateless restart)."""

    def test_rapid_restart_preserves_agents(self, proto):
        """Workers registered under coordinator #1 must be visible to coordinator #2."""
        coord1 = proto["Coordinator"](heartbeat_interval=0.5, dead_agent_timeout=120.0)
        coord1.start()
        time.sleep(0.3)

        # Register a few workers through SharedState directly (simulating workers)
        state = proto["SharedState"]()
        for i in range(3):
            state.register_agent(f"worker-{i}", "team-A", "worker", os.getpid())
        state.close()

        # Stop coordinator #1
        coord1.stop()

        # Immediately start coordinator #2 on the same comm_dir
        coord2 = proto["Coordinator"](heartbeat_interval=0.5, dead_agent_timeout=120.0)
        coord2.start()
        time.sleep(0.3)

        status = coord2.get_status()
        agent_ids = [a["agent_id"] for a in status["agents"]]

        # All 3 workers + coordinator should be visible
        for i in range(3):
            assert f"worker-{i}" in agent_ids, (
                f"BUG: worker-{i} lost after coordinator restart. "
                f"Visible agents: {agent_ids}"
            )

        coord2.stop()

    def test_rapid_restart_phase_signals_preserved(self, proto):
        """Phase signals written by coordinator #1 survive restart."""
        coord1 = proto["Coordinator"](heartbeat_interval=0.5)
        coord1.start()
        coord1.advance_phase("research")
        coord1.advance_phase("synthesis")
        coord1.stop()

        # Re-read state
        state = proto["SharedState"]()
        signals = state.get_phase_signals("research")
        assert len(signals) >= 1, "BUG: Phase signal 'research' lost after restart"
        signals2 = state.get_phase_signals("synthesis")
        assert len(signals2) >= 1, "BUG: Phase signal 'synthesis' lost after restart"
        state.close()

    def test_rapid_restart_five_cycles(self, proto):
        """5 rapid start/stop cycles must not corrupt the DB."""
        for cycle in range(5):
            coord = proto["Coordinator"](heartbeat_interval=0.2)
            coord.start()
            coord.advance_phase(f"phase-{cycle}")
            time.sleep(0.1)
            coord.stop()

        # Verify DB is readable after all cycles
        state = proto["SharedState"]()
        for cycle in range(5):
            signals = state.get_phase_signals(f"phase-{cycle}")
            assert len(signals) >= 1, f"BUG: Phase signal phase-{cycle} lost after 5 restart cycles"
        state.close()


# ===================================================================
# 2. Message flood
# ===================================================================

class TestMessageFlood:
    """5000 messages from 5 writers in rapid succession.
    Verify none lost, none corrupted, all readable."""

    def test_5000_messages_5_writers(self, proto):
        bus_path = proto["bus_path"]
        channel = "flood-test"
        msgs_per_writer = 1000
        num_writers = 5
        total_expected = msgs_per_writer * num_writers

        errors = []
        lock = threading.Lock()

        def writer_fn(writer_id):
            try:
                w = proto["BusWriter"](bus_path, f"flood-{writer_id}", "team-flood")
                for seq in range(msgs_per_writer):
                    w.publish(channel, "info", {"w": writer_id, "s": seq})
            except Exception as e:
                with lock:
                    errors.append(f"writer-{writer_id}: {e}")

        threads = [threading.Thread(target=writer_fn, args=(i,)) for i in range(num_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"BUG: Writer errors: {errors}"

        # Read all messages back
        reader = proto["BusReader"](bus_path, channel)
        msgs = reader.poll()

        assert len(msgs) == total_expected, (
            f"BUG: Message flood lost messages! Expected {total_expected}, got {len(msgs)}. "
            f"Lost {total_expected - len(msgs)} messages."
        )

        # Verify no corruption: all messages should have valid body fields
        for m in msgs:
            assert "w" in m.body, f"BUG: Corrupt message body: {m.body}"
            assert "s" in m.body, f"BUG: Corrupt message body: {m.body}"

    def test_flood_unique_ids(self, proto):
        """All 5000 messages must have unique IDs."""
        bus_path = proto["bus_path"]
        channel = "flood-id-test"

        def writer_fn(writer_id):
            w = proto["BusWriter"](bus_path, f"id-{writer_id}", "team-flood")
            for seq in range(200):
                w.publish(channel, "info", {"w": writer_id, "s": seq})

        threads = [threading.Thread(target=writer_fn, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        reader = proto["BusReader"](bus_path, channel)
        msgs = reader.poll()
        ids = [m.id for m in msgs]
        assert len(ids) == len(set(ids)), (
            f"BUG: Duplicate message IDs! {len(ids)} total, {len(set(ids))} unique"
        )


# ===================================================================
# 3. Interleaved reader/writer
# ===================================================================

class TestInterleavedReaderWriter:
    """Writers and readers running simultaneously on the same channel.
    Verify readers never see partial/corrupt data."""

    def test_concurrent_read_write(self, proto):
        bus_path = proto["bus_path"]
        channel = "interleaved"
        stop_flag = threading.Event()
        write_count = 500
        corruptions = []
        read_messages = []
        lock = threading.Lock()

        def writer_fn():
            w = proto["BusWriter"](bus_path, "writer-il", "team-il")
            for seq in range(write_count):
                w.publish(channel, "info", {"seq": seq, "check": "valid"})
                time.sleep(0.0001)
            stop_flag.set()

        def reader_fn():
            r = proto["BusReader"](bus_path, channel)
            while not stop_flag.is_set() or True:
                msgs = r.poll()
                for m in msgs:
                    with lock:
                        read_messages.append(m)
                    # Check for corruption
                    if m.body.get("check") != "valid":
                        with lock:
                            corruptions.append(m.body)
                if stop_flag.is_set() and not msgs:
                    break
                time.sleep(0.001)

        wt = threading.Thread(target=writer_fn)
        rts = [threading.Thread(target=reader_fn) for _ in range(3)]

        wt.start()
        for rt in rts:
            rt.start()

        wt.join(timeout=30)
        for rt in rts:
            rt.join(timeout=10)

        assert not corruptions, f"BUG: Reader saw corrupt data: {corruptions[:5]}"

        # Each reader tracks its own offset, so each should get all messages
        # But since they share nothing, total across 3 readers = 3 * write_count
        # Just verify no corruption happened
        total_read = len(read_messages)
        assert total_read > 0, "BUG: Readers got zero messages"

    def test_reader_never_returns_partial_json(self, proto):
        """Artificially slow writes must not produce partial reads."""
        bus_path = proto["bus_path"]
        channel = "partial-check"
        filepath = os.path.join(bus_path, "partial-check.jsonl")

        # Write messages normally
        w = proto["BusWriter"](bus_path, "writer-p", "team-p")
        for i in range(100):
            w.publish(channel, "info", {"seq": i})

        # Now manually append a partial line (simulating mid-write)
        with open(filepath, "a") as f:
            f.write('{"id":"partial","type":"info","channel":"partial-check"')
            # No closing brace, no newline

        r = proto["BusReader"](bus_path, channel)
        msgs = r.poll()

        # Should get exactly 100 complete messages, not the partial
        assert len(msgs) == 100, (
            f"BUG: Expected 100, got {len(msgs)} — partial line may have been returned"
        )


# ===================================================================
# 4. Rate limit under extreme contention
# ===================================================================

class TestRateLimitExtremeContention:
    """20 threads competing for 10 API slots.
    Verify EXACTLY 10 succeed."""

    def test_20_threads_10_slots(self, proto):
        state = proto["SharedState"]()
        endpoint = "api.stress/contention"
        state.configure_rate_limit(endpoint, max_calls=10, window_seconds=60)

        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(20, timeout=30)

        def try_reserve(agent_id):
            barrier.wait()
            result = state.reserve_api_call(endpoint, agent_id)
            with lock:
                results.append(result)

        threads = [
            threading.Thread(target=try_reserve, args=(f"contender-{i}",))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        successes = results.count(True)
        failures = results.count(False)
        state.close()

        assert successes == 10, (
            f"BUG: Rate limit under contention — expected exactly 10 successes, "
            f"got {successes} ({failures} failures). "
            f"{'OVER-ADMIT: more than 10 passed!' if successes > 10 else 'UNDER-ADMIT: fewer than 10 passed.'}"
        )
        assert failures == 10, (
            f"BUG: Expected exactly 10 failures, got {failures}"
        )

    def test_contention_no_sqlite_crash(self, proto):
        """20 threads hammering reserve_api_call must not crash the DB."""
        state = proto["SharedState"]()
        endpoint = "api.stress/crash-test"
        state.configure_rate_limit(endpoint, max_calls=5, window_seconds=60)

        errors = []
        lock = threading.Lock()

        def hammer(agent_id):
            try:
                for _ in range(10):
                    state.reserve_api_call(endpoint, agent_id)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=hammer, args=(f"h-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        state.close()
        assert not errors, f"BUG: SQLite errors under contention: {errors[:5]}"


# ===================================================================
# 5. Heartbeat accuracy under load
# ===================================================================

class TestHeartbeatUnderLoad:
    """Workers heartbeating while heavy I/O is happening.
    Verify heartbeats don't get missed/delayed beyond dead-agent timeout."""

    def test_heartbeat_during_heavy_writes(self, proto):
        state = proto["SharedState"]()
        agent_id = "hb-load-test"
        state.register_agent(agent_id, "team-hb", "worker", os.getpid())
        dead_timeout = 5.0  # short timeout for testing

        stop_flag = threading.Event()
        hb_errors = []

        # Heartbeat thread: heartbeats every 0.5s
        def heartbeat_fn():
            while not stop_flag.is_set():
                try:
                    state.heartbeat(agent_id)
                except Exception as e:
                    hb_errors.append(str(e))
                stop_flag.wait(0.5)

        # Heavy I/O thread: continuous writes
        def io_load_fn():
            while not stop_flag.is_set():
                try:
                    state.register_agent(
                        f"load-{uuid.uuid4().hex[:8]}", "team-load", "worker", os.getpid()
                    )
                    state.cleanup_expired()
                except Exception:
                    pass

        hb_thread = threading.Thread(target=heartbeat_fn)
        io_threads = [threading.Thread(target=io_load_fn) for _ in range(5)]

        hb_thread.start()
        for t in io_threads:
            t.start()

        # Let it run for 3 seconds
        time.sleep(3.0)
        stop_flag.set()

        hb_thread.join(timeout=5)
        for t in io_threads:
            t.join(timeout=5)

        # Check the agent is NOT considered dead
        dead = state.get_dead_agents(timeout=dead_timeout)
        dead_ids = [a["agent_id"] for a in dead]
        state.close()

        assert not hb_errors, f"BUG: Heartbeat errors during load: {hb_errors[:5]}"
        assert agent_id not in dead_ids, (
            f"BUG: Agent '{agent_id}' incorrectly marked dead during heavy I/O. "
            f"Heartbeat was delayed beyond {dead_timeout}s timeout."
        )


# ===================================================================
# 6. Large message edge case
# ===================================================================

class TestLargeMessageEdgeCase:
    """Messages at exactly 4095, 4096, and 4097 bytes.
    Verify boundary behavior."""

    def _make_msg_of_size(self, proto, target_bytes, channel="size-test"):
        """Construct a message whose serialized form is exactly target_bytes."""
        # Build a base message to measure overhead
        base_body = {"d": ""}
        w = proto["BusWriter"](proto["bus_path"], "sizer", "team-sz")
        base_msg = proto["Message"](
            id=str(uuid.uuid4()), type="info", channel=channel,
            team="team-sz", agent_id="sizer", ts=time.time(),
            ttl=300, body=base_body, in_reply_to=None,
        )
        base_raw = base_msg.to_json_line()
        overhead = len(base_raw)
        # We need (target_bytes - overhead + len(base_body["d"])) chars for "d"
        # base_body["d"] is "" so overhead includes the empty string
        padding_needed = target_bytes - overhead
        if padding_needed < 0:
            return None  # Can't make it that small
        base_body["d"] = "x" * padding_needed
        msg = proto["Message"](
            id=base_msg.id, type="info", channel=channel,
            team="team-sz", agent_id="sizer", ts=base_msg.ts,
            ttl=300, body=base_body, in_reply_to=None,
        )
        raw = msg.to_json_line()
        # Fine-tune: adjust if off by a byte or two
        diff = len(raw) - target_bytes
        if diff != 0:
            new_padding = padding_needed - diff
            if new_padding >= 0:
                base_body["d"] = "x" * new_padding
                msg = proto["Message"](
                    id=base_msg.id, type="info", channel=channel,
                    team="team-sz", agent_id="sizer", ts=base_msg.ts,
                    ttl=300, body=base_body, in_reply_to=None,
                )
                raw = msg.to_json_line()
        return msg, raw

    def test_4095_bytes_accepted(self, proto):
        """Message at exactly 4095 bytes should be accepted (under limit)."""
        result = self._make_msg_of_size(proto, 4095)
        if result is None:
            pytest.skip("Cannot construct message of exactly 4095 bytes")
        msg, raw = result
        assert len(raw) == 4095, f"Setup error: message is {len(raw)} bytes, not 4095"

        w = proto["BusWriter"](proto["bus_path"], "sizer", "team-sz")
        # Should succeed (under 4096 limit)
        published = w.publish("size-4095", "info", msg.body)
        assert published is not None

    def test_4096_bytes_accepted(self, proto):
        """Message at exactly 4096 bytes should be accepted (at limit)."""
        result = self._make_msg_of_size(proto, 4096)
        if result is None:
            pytest.skip("Cannot construct message of exactly 4096 bytes")
        msg, raw = result
        assert len(raw) == 4096, f"Setup error: message is {len(raw)} bytes, not 4096"

        w = proto["BusWriter"](proto["bus_path"], "sizer", "team-sz")
        published = w.publish("size-4096", "info", msg.body)
        assert published is not None

    def test_4097_bytes_rejected(self, proto):
        """Message with body too large to fit in 4096 bytes should be REJECTED."""
        # publish() generates its own UUID and timestamp, so we use a body
        # large enough that the total serialization always exceeds 4096.
        huge_body = {"d": "x" * 4000}
        w = proto["BusWriter"](proto["bus_path"], "sizer", "team-sz")
        with pytest.raises(ValueError, match="exceeds"):
            w.publish("size-4097", "info", huge_body)

    def test_boundary_message_readable(self, proto):
        """A 4096-byte message must be fully readable after write."""
        result = self._make_msg_of_size(proto, 4096, channel="boundary-read")
        if result is None:
            pytest.skip("Cannot construct boundary message")
        msg, raw = result

        w = proto["BusWriter"](proto["bus_path"], "sizer", "team-sz")
        w.publish("boundary-read", "info", msg.body)

        r = proto["BusReader"](proto["bus_path"], "boundary-read")
        msgs = r.poll()
        assert len(msgs) == 1, f"BUG: Boundary message not readable (got {len(msgs)})"
        assert msgs[0].body["d"] == msg.body["d"], "BUG: Boundary message data corrupted"


# ===================================================================
# 7. Connection churn
# ===================================================================

class TestConnectionChurn:
    """Open/close SharedState connections rapidly (simulating agent restarts).
    Verify no WAL/SHM corruption."""

    def test_rapid_open_close_100_cycles(self, proto):
        db_path = proto["db_path"]
        errors = []

        for i in range(100):
            try:
                s = proto["SharedState"]()
                s.register_agent(f"churn-{i}", "team-churn", "worker", os.getpid())
                s.heartbeat(f"churn-{i}")
                s.close()
            except Exception as e:
                errors.append(f"cycle {i}: {e}")

        assert not errors, f"BUG: Connection churn errors: {errors[:10]}"

        # Verify DB is still valid after all churn
        s = proto["SharedState"]()
        row = s._conn.execute("SELECT COUNT(*) FROM agents").fetchone()
        count = row[0]
        s.close()
        assert count >= 100, (
            f"BUG: After 100 open/close cycles, only {count} agents found (expected >=100)"
        )

    def test_concurrent_open_close(self, proto):
        """10 threads opening/closing connections simultaneously."""
        db_path = proto["db_path"]
        errors = []
        lock = threading.Lock()

        def churn_fn(thread_id):
            try:
                for i in range(20):
                    s = proto["SharedState"]()
                    s.register_agent(
                        f"conc-churn-{thread_id}-{i}", "team-cc", "worker", os.getpid()
                    )
                    s.close()
            except Exception as e:
                with lock:
                    errors.append(f"thread-{thread_id}: {e}")

        threads = [threading.Thread(target=churn_fn, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"BUG: Concurrent connection churn errors: {errors[:10]}"

        # Final integrity check
        s = proto["SharedState"]()
        try:
            s._conn.execute("PRAGMA integrity_check")
        except Exception as e:
            pytest.fail(f"BUG: DB integrity check failed after connection churn: {e}")
        finally:
            s.close()

    def test_wal_shm_survive_churn(self, proto):
        """WAL/SHM files must not be corrupted by rapid churn."""
        db_path = proto["db_path"]

        # Do some writes to ensure WAL exists
        s = proto["SharedState"]()
        for i in range(50):
            s.register_agent(f"wal-churn-{i}", "team-wc", "worker", os.getpid())
        s.close()

        # Churn
        for _ in range(50):
            s = proto["SharedState"]()
            s.close()

        # Verify DB is intact
        s = proto["SharedState"]()
        result = s._conn.execute("PRAGMA integrity_check").fetchone()
        s.close()
        assert result[0] == "ok", f"BUG: WAL corruption after churn: {result[0]}"


# ===================================================================
# 8. Cleanup under active writes
# ===================================================================

class TestCleanupUnderActiveWrites:
    """Run cleanup_expired() while writers are actively writing.
    Verify no data corruption."""

    def test_cleanup_during_writes(self, proto):
        state = proto["SharedState"]()
        stop_flag = threading.Event()
        errors = []
        lock = threading.Lock()

        # Pre-populate with some expired data
        now = time.time()
        for i in range(100):
            state._conn.execute(
                "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"exp-{i}", "test", "a1", "info", "{}", now - 7200, now - 3600),
            )
            state._conn.execute(
                "INSERT INTO rate_limits (api_endpoint, agent_id, called_at) "
                "VALUES (?, ?, ?)",
                ("api.test/old", "a1", now - 7200),
            )
        state._conn.commit()

        # Writer thread: continuously writes new data
        def writer_fn():
            counter = 0
            while not stop_flag.is_set():
                try:
                    state.register_agent(
                        f"cleanup-w-{counter}", "team-cw", "worker", os.getpid()
                    )
                    counter += 1
                except Exception as e:
                    with lock:
                        errors.append(f"writer: {e}")

        # Cleanup thread: continuously runs cleanup
        def cleanup_fn():
            while not stop_flag.is_set():
                try:
                    state.cleanup_expired()
                except Exception as e:
                    with lock:
                        errors.append(f"cleanup: {e}")
                time.sleep(0.01)

        wt = threading.Thread(target=writer_fn)
        ct = threading.Thread(target=cleanup_fn)
        wt.start()
        ct.start()

        time.sleep(2.0)
        stop_flag.set()

        wt.join(timeout=5)
        ct.join(timeout=5)
        state.close()

        assert not errors, f"BUG: Errors during cleanup+write: {errors[:10]}"

    def test_cleanup_doesnt_delete_active_messages(self, proto):
        """Cleanup must not touch non-expired messages."""
        state = proto["SharedState"]()
        now = time.time()

        # Insert active (non-expired) messages
        for i in range(50):
            state._conn.execute(
                "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"active-{i}", "test", "a1", "info", '{"live":true}', now, now + 3600),
            )
        # Insert expired messages
        for i in range(50):
            state._conn.execute(
                "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"expired-{i}", "test", "a1", "info", '{"live":false}', now - 7200, now - 3600),
            )
        state._conn.commit()

        cleaned = state.cleanup_expired()

        # Active messages must survive
        active_count = state._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE msg_id LIKE 'active-%'"
        ).fetchone()[0]

        state.close()
        assert active_count == 50, (
            f"BUG: Cleanup deleted {50 - active_count} active messages!"
        )


# ===================================================================
# 9. Multi-channel stress
# ===================================================================

class TestMultiChannelStress:
    """20 channels, 10 writers, 10 readers, all running simultaneously for 3 seconds."""

    def test_multi_channel_simultaneous(self, proto):
        bus_path = proto["bus_path"]
        num_channels = 20
        num_writers = 10
        num_readers = 10
        duration = 3.0
        stop_flag = threading.Event()

        channels = [f"multi-ch-{i}" for i in range(num_channels)]
        write_counts = {}
        read_counts = {}
        errors = []
        lock = threading.Lock()

        def writer_fn(writer_id):
            w = proto["BusWriter"](bus_path, f"mw-{writer_id}", "team-mc")
            count = 0
            while not stop_flag.is_set():
                ch = channels[count % num_channels]
                try:
                    w.publish(ch, "info", {"w": writer_id, "c": count})
                    count += 1
                except Exception as e:
                    with lock:
                        errors.append(f"writer-{writer_id}: {e}")
                    break
            with lock:
                write_counts[writer_id] = count

        def reader_fn(reader_id):
            # Each reader monitors a subset of channels
            ch = channels[reader_id % num_channels]
            r = proto["BusReader"](bus_path, ch)
            count = 0
            while not stop_flag.is_set():
                msgs = r.poll()
                count += len(msgs)
                time.sleep(0.01)
            # Final drain
            msgs = r.poll()
            count += len(msgs)
            with lock:
                read_counts[reader_id] = count

        threads = []
        for i in range(num_writers):
            t = threading.Thread(target=writer_fn, args=(i,))
            threads.append(t)
        for i in range(num_readers):
            t = threading.Thread(target=reader_fn, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()

        time.sleep(duration)
        stop_flag.set()

        for t in threads:
            t.join(timeout=10)

        total_written = sum(write_counts.values())
        total_read = sum(read_counts.values())

        assert not errors, f"BUG: Multi-channel errors: {errors[:10]}"
        assert total_written > 0, "BUG: No messages were written"
        assert total_read > 0, "BUG: No messages were read"

        # Verify no corruption: re-read all channels and parse every message
        corrupt_count = 0
        for ch in channels:
            r = proto["BusReader"](bus_path, ch)
            msgs = r.poll()
            for m in msgs:
                if "w" not in m.body or "c" not in m.body:
                    corrupt_count += 1

        assert corrupt_count == 0, (
            f"BUG: Found {corrupt_count} corrupt messages across {num_channels} channels"
        )


# ===================================================================
# 10. Phase transition during active work
# ===================================================================

class TestPhaseTransitionDuringWork:
    """Coordinator advances phases while workers are mid-operation.
    Verify no crashes."""

    def test_phase_advance_during_worker_writes(self, proto):
        comm_dir = proto["comm_dir"]
        coord = proto["Coordinator"](heartbeat_interval=0.5, dead_agent_timeout=120.0)
        coord.start()
        time.sleep(0.2)

        stop_flag = threading.Event()
        errors = []
        lock = threading.Lock()

        # Worker threads: continuously write + heartbeat
        def worker_fn(worker_id):
            try:
                w = proto["Worker"](f"phase-worker-{worker_id}", "team-pt")
                w.start()
                while not stop_flag.is_set():
                    w.send("global", "info", {"from": w.agent_id, "work": "busy"})
                    time.sleep(0.01)
                w.stop()
            except Exception as e:
                with lock:
                    errors.append(f"worker-{worker_id}: {e}")

        workers = [threading.Thread(target=worker_fn, args=(i,)) for i in range(5)]
        for w in workers:
            w.start()

        # Rapidly advance phases while workers are active
        phases = ["init", "research", "analysis", "synthesis", "review", "done"]
        for phase in phases:
            time.sleep(0.3)
            try:
                coord.advance_phase(phase)
            except Exception as e:
                errors.append(f"phase-advance to {phase}: {e}")

        stop_flag.set()
        for w in workers:
            w.join(timeout=10)

        coord.stop()
        assert not errors, f"BUG: Errors during phase transitions: {errors[:10]}"

    def test_phase_signals_complete_after_transitions(self, proto):
        """All phase transitions must be recorded even under load."""
        coord = proto["Coordinator"](heartbeat_interval=0.5)
        coord.start()

        phases = ["alpha", "beta", "gamma", "delta", "epsilon"]
        for p in phases:
            coord.advance_phase(p)
            time.sleep(0.05)

        coord.stop()

        state = proto["SharedState"]()
        for p in phases:
            signals = state.get_phase_signals(p)
            assert len(signals) >= 1, (
                f"BUG: Phase signal for '{p}' missing after rapid transitions"
            )
        state.close()

    def test_coordinator_status_consistent_during_transitions(self, proto):
        """get_status() must not crash during phase transitions."""
        coord = proto["Coordinator"](heartbeat_interval=0.5)
        coord.start()

        errors = []

        def poll_status():
            for _ in range(50):
                try:
                    status = coord.get_status()
                    assert "phase" in status
                    assert "agents" in status
                except Exception as e:
                    errors.append(str(e))
                time.sleep(0.02)

        status_thread = threading.Thread(target=poll_status)
        status_thread.start()

        for i in range(10):
            coord.advance_phase(f"rapid-{i}")
            time.sleep(0.05)

        status_thread.join(timeout=10)
        coord.stop()

        assert not errors, f"BUG: get_status() errors during transitions: {errors[:5]}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
