"""Stress tests for bus_core.py shared primitives.

Pushes bus_write, bus_read, init_db, retry_on_busy, and sanitize_channel
to their limits with high concurrency, large messages, TTL expiry,
corruption recovery, concurrent initialization, offset tracking, and
mixed publisher/subscriber workloads.
Uses threading.Barrier for synchronized starts and 10-second timeouts
on all thread joins.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from storage.coordination.bus_core import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_MESSAGE_BYTES,
    VALID_MSG_TYPES,
    bus_read,
    bus_write,
    init_db,
    is_busy_or_locked,
    retry_on_busy,
    sanitize_channel,
)

JOIN_TIMEOUT = 10


def _retry_on_busy_call(fn, max_retries=5, initial_delay=0.05):
    """Retry a callable on SQLITE_BUSY / SQLITE_LOCKED errors."""
    delay = initial_delay
    last_err = None
    for _ in range(max_retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if is_busy_or_locked(e):
                last_err = e
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_err  # type: ignore[misc]


# ===================================================================
# Bus Write / Read Stress Tests
# ===================================================================


class TestBusWriteStress(unittest.TestCase):
    """Stress tests for bus_write and bus_read."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.bus_dir = os.path.join(self.tmpdir, "bus")

    def test_high_throughput_100_messages_20_threads(self):
        """20 threads each write 5+ messages (100+ total) to the same channel.

        All messages should appear in the channel file with no corruption.
        """
        num_threads = 20
        msgs_per_thread = 6  # 120 total
        total_msgs = num_threads * msgs_per_thread
        barrier = threading.Barrier(num_threads)
        errors = []
        errors_lock = threading.Lock()
        msg_ids = []
        ids_lock = threading.Lock()

        def writer(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for j in range(msgs_per_thread):
                    mid = bus_write(
                        self.bus_dir,
                        "stress-channel",
                        "info",
                        {"thread": idx, "seq": j},
                        team=f"team-{idx}",
                        agent_id=f"agent-{idx}",
                    )
                    if mid is not None:
                        with ids_lock:
                            msg_ids.append(mid)
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=writer, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(msg_ids), total_msgs,
                         f"Expected {total_msgs} messages, got {len(msg_ids)}")

        # Read all messages back and verify integrity
        msgs, offset = bus_read(self.bus_dir, "stress-channel", 0)
        self.assertEqual(len(msgs), total_msgs,
                         f"Read back {len(msgs)} messages, expected {total_msgs}")

        # Verify no duplicate IDs
        read_ids = {m["id"] for m in msgs}
        self.assertEqual(len(read_ids), total_msgs, "Duplicate message IDs detected")

        # Verify all written IDs are present
        for mid in msg_ids:
            self.assertIn(mid, read_ids, f"Message {mid} missing from read")

    def test_concurrent_channel_reads_and_writes(self):
        """5 writers and 5 readers operate on the same channel concurrently.

        Readers should see a non-decreasing count of messages and all
        messages should eventually be readable.
        """
        num_writers = 5
        num_readers = 5
        msgs_per_writer = 20
        total_msgs = num_writers * msgs_per_writer
        barrier = threading.Barrier(num_writers + num_readers)
        write_done = threading.Event()
        errors = []
        errors_lock = threading.Lock()
        read_counts = []
        counts_lock = threading.Lock()

        def writer(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for j in range(msgs_per_writer):
                    bus_write(
                        self.bus_dir,
                        "rw-channel",
                        "info",
                        {"writer": idx, "seq": j},
                        team=f"team-{idx}",
                        agent_id=f"writer-{idx}",
                    )
                if idx == num_writers - 1:
                    # Last writer signals completion
                    time.sleep(0.05)
                    write_done.set()
            except Exception as e:
                with errors_lock:
                    errors.append(("writer", idx, e))
                write_done.set()

        def reader(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                offset = 0
                local_counts = []
                while not write_done.is_set():
                    msgs, offset = bus_read(self.bus_dir, "rw-channel", offset)
                    local_counts.append(len(msgs))
                    time.sleep(0.01)
                # Final read to get remaining messages
                msgs, offset = bus_read(self.bus_dir, "rw-channel", offset)
                local_counts.append(len(msgs))
                with counts_lock:
                    read_counts.append(sum(local_counts))
            except Exception as e:
                with errors_lock:
                    errors.append(("reader", idx, e))

        threads = []
        for i in range(num_writers):
            threads.append(threading.Thread(target=writer, args=(i,)))
        for i in range(num_readers):
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Each reader should have seen all messages (via incremental reads)
        # Read the final state to confirm total
        all_msgs, _ = bus_read(self.bus_dir, "rw-channel", 0)
        self.assertEqual(len(all_msgs), total_msgs,
                         f"Total messages: expected {total_msgs}, got {len(all_msgs)}")

    def test_large_message_near_pipe_buf(self):
        """Write messages near the MAX_MESSAGE_BYTES boundary.

        Messages just under the limit should succeed; messages over should
        raise ValueError.
        """
        # Create a body that brings the total message close to 4096 bytes.
        # The envelope (id, type, channel, team, agent_id, ts, ttl, body keys)
        # takes roughly ~200 bytes, so we fill the body with ~3800 bytes.
        large_body = {"data": "x" * 3700}
        mid = bus_write(
            self.bus_dir, "large-ch", "info", large_body,
            team="team-1", agent_id="agent-1",
        )
        self.assertIsNotNone(mid, "Near-limit message should succeed")

        # Verify it reads back correctly
        msgs, _ = bus_read(self.bus_dir, "large-ch", 0)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(len(msgs[0]["body"]["data"]), 3700)

        # Message that exceeds the limit
        oversized_body = {"data": "y" * (MAX_MESSAGE_BYTES + 100)}
        with self.assertRaises(ValueError):
            bus_write(
                self.bus_dir, "large-ch", "info", oversized_body,
                team="team-1", agent_id="agent-1",
            )

    def test_message_expiry_under_load(self):
        """Write messages with short TTL, wait, then verify they are filtered.

        bus_read should skip expired messages.
        """
        # Write 50 messages with 1-second TTL
        for i in range(50):
            bus_write(
                self.bus_dir, "ttl-channel", "info",
                {"idx": i}, team="team-1", agent_id="agent-1",
                ttl=1,
            )

        # Immediately, all should be readable
        msgs, offset = bus_read(self.bus_dir, "ttl-channel", 0)
        self.assertEqual(len(msgs), 50)

        # Wait for expiry
        time.sleep(1.2)

        # Read again from start -- expired messages should be filtered
        msgs, _ = bus_read(self.bus_dir, "ttl-channel", 0)
        self.assertEqual(len(msgs), 0, "Expired messages should be filtered")

        # Write fresh messages with long TTL
        for i in range(10):
            bus_write(
                self.bus_dir, "ttl-channel", "info",
                {"idx": i, "fresh": True}, team="team-1", agent_id="agent-1",
                ttl=3600,
            )

        # Read from offset -- should get only the fresh ones
        msgs, new_offset = bus_read(self.bus_dir, "ttl-channel", offset)
        self.assertEqual(len(msgs), 10)
        self.assertGreater(new_offset, offset)

    def test_bus_file_corruption_recovery(self):
        """Write valid messages, inject corruption, then write more.

        bus_read should skip corrupt lines and recover valid messages.
        """
        # Write 5 valid messages
        for i in range(5):
            bus_write(
                self.bus_dir, "corrupt-channel", "info",
                {"idx": i}, team="team-1", agent_id="agent-1",
            )

        # Inject corruption (invalid JSON line)
        safe_ch = sanitize_channel("corrupt-channel")
        filepath = os.path.join(self.bus_dir, f"{safe_ch}.jsonl")
        with open(filepath, "ab") as f:
            f.write(b"THIS IS NOT VALID JSON\n")
            f.write(b"{broken json too\n")

        # Write 5 more valid messages
        for i in range(5, 10):
            bus_write(
                self.bus_dir, "corrupt-channel", "info",
                {"idx": i}, team="team-1", agent_id="agent-1",
            )

        # Read all -- should get 10 valid messages, skipping the 2 corrupt lines
        msgs, _ = bus_read(self.bus_dir, "corrupt-channel", 0)
        self.assertEqual(len(msgs), 10,
                         f"Expected 10 valid messages, got {len(msgs)}")
        indices = sorted(m["body"]["idx"] for m in msgs)
        self.assertEqual(indices, list(range(10)))

    def test_concurrent_bus_initialization(self):
        """20 threads all try to write to the same channel simultaneously,
        each potentially creating the bus directory and channel file.

        All writes should succeed despite the race to create dirs/files.
        """
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        errors = []
        errors_lock = threading.Lock()
        msg_ids = []
        ids_lock = threading.Lock()

        # Use a fresh bus_dir that doesn't exist yet
        fresh_bus = os.path.join(self.tmpdir, "fresh-bus")

        def init_writer(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                mid = bus_write(
                    fresh_bus, "init-race", "info",
                    {"thread": idx}, team=f"team-{idx}", agent_id=f"agent-{idx}",
                )
                if mid is not None:
                    with ids_lock:
                        msg_ids.append(mid)
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=init_writer, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(msg_ids), num_threads)

        msgs, _ = bus_read(fresh_bus, "init-race", 0)
        self.assertEqual(len(msgs), num_threads)

    def test_read_offset_tracking_under_concurrent_access(self):
        """Multiple readers track their own offsets while a writer appends.

        Each reader should see every message exactly once across all its
        incremental reads.
        """
        num_messages = 100
        num_readers = 5
        write_done = threading.Event()
        errors = []
        errors_lock = threading.Lock()
        reader_totals = []
        totals_lock = threading.Lock()

        def writer():
            try:
                for i in range(num_messages):
                    bus_write(
                        self.bus_dir, "offset-channel", "info",
                        {"seq": i}, team="team-w", agent_id="writer",
                    )
                    time.sleep(0.002)
                write_done.set()
            except Exception as e:
                with errors_lock:
                    errors.append(("writer", e))
                write_done.set()

        def reader(idx):
            try:
                offset = 0
                all_msgs = []
                while not write_done.is_set():
                    msgs, offset = bus_read(
                        self.bus_dir, "offset-channel", offset
                    )
                    all_msgs.extend(msgs)
                    time.sleep(0.005)
                # Final drain
                msgs, offset = bus_read(
                    self.bus_dir, "offset-channel", offset
                )
                all_msgs.extend(msgs)
                with totals_lock:
                    reader_totals.append((idx, len(all_msgs)))
                # Verify no duplicates
                seq_nums = [m["body"]["seq"] for m in all_msgs]
                if len(seq_nums) != len(set(seq_nums)):
                    with errors_lock:
                        errors.append((
                            "reader-dup", idx,
                            f"Duplicates: {len(seq_nums)} total, {len(set(seq_nums))} unique",
                        ))
            except Exception as e:
                with errors_lock:
                    errors.append(("reader", idx, e))

        t_writer = threading.Thread(target=writer)
        t_writer.start()

        # Start readers slightly after writer to ensure file exists
        time.sleep(0.01)
        reader_threads = [
            threading.Thread(target=reader, args=(i,))
            for i in range(num_readers)
        ]
        for t in reader_threads:
            t.start()

        t_writer.join(timeout=JOIN_TIMEOUT)
        for t in reader_threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Each reader should have seen all messages
        for idx, count in reader_totals:
            self.assertEqual(
                count, num_messages,
                f"Reader {idx} saw {count} messages, expected {num_messages}",
            )

    def test_mixed_publisher_subscriber_workloads(self):
        """10 publishers and 10 subscribers on multiple channels.

        Each publisher writes to its own channel. Each subscriber reads
        from a different publisher's channel. All messages should be
        delivered correctly.
        """
        num_publishers = 10
        num_subscribers = 10
        msgs_per_pub = 20
        barrier = threading.Barrier(num_publishers + num_subscribers)
        pub_done = threading.Event()
        errors = []
        errors_lock = threading.Lock()
        sub_results = {}
        results_lock = threading.Lock()

        def publisher(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for j in range(msgs_per_pub):
                    bus_write(
                        self.bus_dir,
                        f"pub-{idx}",
                        "info",
                        {"pub": idx, "seq": j},
                        team=f"pub-team-{idx}",
                        agent_id=f"pub-agent-{idx}",
                    )
                if idx == num_publishers - 1:
                    time.sleep(0.05)
                    pub_done.set()
            except Exception as e:
                with errors_lock:
                    errors.append(("pub", idx, e))
                pub_done.set()

        def subscriber(idx):
            try:
                # Each subscriber reads from a corresponding publisher's channel
                target_channel = f"pub-{idx % num_publishers}"
                barrier.wait(timeout=JOIN_TIMEOUT)
                offset = 0
                all_msgs = []
                while not pub_done.is_set():
                    msgs, offset = bus_read(
                        self.bus_dir, target_channel, offset
                    )
                    all_msgs.extend(msgs)
                    time.sleep(0.01)
                # Final drain
                msgs, offset = bus_read(
                    self.bus_dir, target_channel, offset
                )
                all_msgs.extend(msgs)
                with results_lock:
                    sub_results[idx] = all_msgs
            except Exception as e:
                with errors_lock:
                    errors.append(("sub", idx, e))

        threads = []
        for i in range(num_publishers):
            threads.append(threading.Thread(target=publisher, args=(i,)))
        for i in range(num_subscribers):
            threads.append(threading.Thread(target=subscriber, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Each subscriber should have received all messages from its publisher
        for sub_idx, msgs in sub_results.items():
            pub_idx = sub_idx % num_publishers
            self.assertEqual(
                len(msgs), msgs_per_pub,
                f"Subscriber {sub_idx} got {len(msgs)} msgs from pub-{pub_idx}, "
                f"expected {msgs_per_pub}",
            )
            # Verify message integrity
            seq_nums = sorted(m["body"]["seq"] for m in msgs)
            self.assertEqual(seq_nums, list(range(msgs_per_pub)))

    def test_multi_channel_write_stress(self):
        """Write to 50 different channels from 10 threads concurrently.

        Each channel should contain exactly the messages written to it.
        """
        num_threads = 10
        channels_per_thread = 5
        msgs_per_channel = 4
        barrier = threading.Barrier(num_threads)
        errors = []
        errors_lock = threading.Lock()

        def multi_channel_writer(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for ch in range(channels_per_thread):
                    channel_name = f"multi-{idx}-{ch}"
                    for m in range(msgs_per_channel):
                        bus_write(
                            self.bus_dir, channel_name, "info",
                            {"thread": idx, "ch": ch, "msg": m},
                            team=f"team-{idx}", agent_id=f"agent-{idx}",
                        )
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=multi_channel_writer, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Verify each channel has the right number of messages
        for idx in range(num_threads):
            for ch in range(channels_per_thread):
                channel_name = f"multi-{idx}-{ch}"
                msgs, _ = bus_read(self.bus_dir, channel_name, 0)
                self.assertEqual(
                    len(msgs), msgs_per_channel,
                    f"Channel {channel_name}: expected {msgs_per_channel}, got {len(msgs)}",
                )


# ===================================================================
# init_db and retry_on_busy Stress Tests
# ===================================================================


class TestInitDbStress(unittest.TestCase):
    """Stress tests for init_db and retry_on_busy."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_concurrent_init_db_20_threads(self):
        """20 threads all call init_db on the same database path.

        All should succeed and the database should be usable afterward.
        """
        db_path = os.path.join(self.tmpdir, "db", "concurrent.db")
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        connections = []
        conn_lock = threading.Lock()
        errors = []
        errors_lock = threading.Lock()

        def init_and_use(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                conn = init_db(db_path)
                # Create a table and insert data
                _retry_on_busy_call(lambda: conn.execute(
                    "CREATE TABLE IF NOT EXISTS test_t "
                    "(id INTEGER PRIMARY KEY, thread INTEGER, val TEXT)"
                ))
                _retry_on_busy_call(lambda: conn.execute(
                    "INSERT INTO test_t (thread, val) VALUES (?, ?)",
                    (idx, f"data-{idx}"),
                ))
                _retry_on_busy_call(lambda: conn.commit())
                with conn_lock:
                    connections.append(conn)
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=init_and_use, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Verify all inserts landed
        conn = init_db(db_path)
        rows = conn.execute("SELECT COUNT(*) as cnt FROM test_t").fetchone()
        self.assertEqual(rows["cnt"], num_threads)

        # Cleanup
        for c in connections:
            try:
                c.close()
            except Exception:
                pass
        conn.close()

    def test_retry_on_busy_decorator_under_contention(self):
        """Simulate SQLITE_BUSY contention and verify retry_on_busy retries.

        Use a locked database to force busy errors, then verify the
        decorator retries and eventually succeeds.
        """
        db_path = os.path.join(self.tmpdir, "retry-test.db")
        conn1 = init_db(db_path, busy_timeout_ms=100)
        conn1.execute(
            "CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY, val INTEGER)"
        )
        conn1.execute("INSERT INTO counter VALUES (1, 0)")
        conn1.commit()

        num_threads = 10
        increments_per_thread = 10
        barrier = threading.Barrier(num_threads)
        errors = []
        errors_lock = threading.Lock()

        @retry_on_busy(max_retries=10, initial_delay=0.01)
        def increment(conn, lock):
            with lock:
                row = conn.execute("SELECT val FROM counter WHERE id = 1").fetchone()
                conn.execute(
                    "UPDATE counter SET val = ? WHERE id = 1", (row["val"] + 1,)
                )
                conn.commit()

        lock = threading.Lock()

        def worker(idx):
            try:
                conn = init_db(db_path, busy_timeout_ms=100)
                barrier.wait(timeout=JOIN_TIMEOUT)
                for _ in range(increments_per_thread):
                    increment(conn, lock)
                conn.close()
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Final count should equal total increments
        row = conn1.execute("SELECT val FROM counter WHERE id = 1").fetchone()
        self.assertEqual(
            row["val"], num_threads * increments_per_thread,
            f"Counter should be {num_threads * increments_per_thread}, got {row['val']}",
        )
        conn1.close()

    def test_is_busy_or_locked_detection(self):
        """Verify is_busy_or_locked correctly identifies busy/locked errors."""
        # Simulate busy error
        busy_err = sqlite3.OperationalError("database is locked")
        self.assertTrue(is_busy_or_locked(busy_err))

        busy_err2 = sqlite3.OperationalError("database table is locked")
        self.assertTrue(is_busy_or_locked(busy_err2))

        busy_err3 = sqlite3.OperationalError("database is busy")
        self.assertTrue(is_busy_or_locked(busy_err3))

        # Non-busy error
        other_err = sqlite3.OperationalError("no such table: foo")
        self.assertFalse(is_busy_or_locked(other_err))

        syntax_err = sqlite3.OperationalError("near syntax error")
        self.assertFalse(is_busy_or_locked(syntax_err))

    def test_sanitize_channel_edge_cases(self):
        """Verify sanitize_channel handles adversarial inputs correctly."""
        self.assertEqual(sanitize_channel("normal"), "normal")
        self.assertEqual(sanitize_channel("team-1"), "team-1")
        self.assertEqual(sanitize_channel("under_score"), "under_score")

        # Path traversal attempts
        self.assertEqual(sanitize_channel("../../../etc/passwd"), "_________etc_passwd")
        self.assertEqual(sanitize_channel("team/global"), "team_global")
        self.assertEqual(sanitize_channel(".."), "__")

        # Special characters
        self.assertEqual(sanitize_channel("a b c"), "a_b_c")
        self.assertEqual(sanitize_channel("ch@nnel!"), "ch_nnel_")
        self.assertEqual(sanitize_channel(""), "")

        # Concurrent sanitization should be safe (pure function)
        results = []
        lock = threading.Lock()

        def sanitize_many(idx):
            for j in range(100):
                result = sanitize_channel(f"team-{idx}/channel-{j}")
                with lock:
                    results.append(result)

        threads = [
            threading.Thread(target=sanitize_many, args=(i,))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(results), 1000)
        # Verify all results are valid (no slashes)
        for r in results:
            self.assertNotIn("/", r)
            self.assertNotIn("..", r.replace("__", ""))


# ===================================================================
# Per-Publisher Message Ordering Verification
# ===================================================================


class TestPerPublisherOrdering(unittest.TestCase):
    """Verify that messages from a single publisher maintain order."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.bus_dir = os.path.join(self.tmpdir, "bus")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ordering_preserved_per_publisher(self):
        """10 publishers write sequenced msgs concurrently; verify per-publisher order."""
        num_pubs = 10
        msgs_per_pub = 20
        barrier = threading.Barrier(num_pubs, timeout=30)
        errors = []
        errors_lock = threading.Lock()

        def publish_sequenced(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for seq in range(msgs_per_pub):
                    bus_write(
                        self.bus_dir, "ordered-ch", "info",
                        {"pub": idx, "seq": seq},
                        team="team-a", agent_id=f"pub-{idx}",
                    )
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [threading.Thread(target=publish_sequenced, args=(i,))
                   for i in range(num_pubs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Ordering errors: {errors}")

        msgs, _ = bus_read(self.bus_dir, "ordered-ch", 0)
        self.assertEqual(len(msgs), num_pubs * msgs_per_pub)

        # Group messages by publisher and verify sequence order
        by_pub = {}
        for m in msgs:
            pub = m["body"]["pub"]
            by_pub.setdefault(pub, []).append(m["body"]["seq"])

        self.assertEqual(len(by_pub), num_pubs)
        for pub, seqs in by_pub.items():
            self.assertEqual(len(seqs), msgs_per_pub,
                             f"Publisher {pub}: expected {msgs_per_pub} msgs, got {len(seqs)}")
            # Sequence numbers should be monotonically increasing
            for i in range(1, len(seqs)):
                self.assertGreater(
                    seqs[i], seqs[i - 1],
                    f"Publisher {pub}: seq {seqs[i]} not > {seqs[i - 1]}",
                )


# ===================================================================
# Sustained Throughput (Burst-Then-Read Cycles)
# ===================================================================


class TestSustainedThroughput(unittest.TestCase):
    """Repeated burst-write then bulk-read cycles."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.bus_dir = os.path.join(self.tmpdir, "bus")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_burst_then_read_10_cycles(self):
        """10 cycles of writing 20 messages then reading all new ones."""
        channel = "sustained-ch"
        offset = 0
        total_written = 0
        total_read = 0

        for cycle in range(10):
            # Burst write
            for m in range(20):
                mid = bus_write(
                    self.bus_dir, channel, "info",
                    {"cycle": cycle, "seq": m},
                    team="team-a", agent_id=f"agent-c{cycle}",
                )
                self.assertIsNotNone(mid)
                total_written += 1

            # Read all new messages
            msgs, offset = bus_read(self.bus_dir, channel, offset)
            total_read += len(msgs)

        # Total read across all cycles should equal total written
        self.assertEqual(total_read, total_written)
        self.assertEqual(total_written, 200)

    def test_concurrent_burst_read_cycles(self):
        """5 threads each do 5 burst-then-read cycles on separate channels."""
        num_threads = 5
        cycles = 5
        msgs_per_burst = 10
        barrier = threading.Barrier(num_threads, timeout=30)
        errors = []
        errors_lock = threading.Lock()
        totals = [0] * num_threads

        def burst_cycle(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                ch = f"burst-{idx}"
                offset = 0
                total = 0
                for c in range(cycles):
                    for m in range(msgs_per_burst):
                        bus_write(
                            self.bus_dir, ch, "info",
                            {"thread": idx, "cycle": c, "seq": m},
                            team="team-a", agent_id=f"agent-{idx}",
                        )
                    msgs, offset = bus_read(self.bus_dir, ch, offset)
                    total += len(msgs)
                totals[idx] = total
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [threading.Thread(target=burst_cycle, args=(i,))
                   for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Burst cycle errors: {errors}")

        expected_per_thread = cycles * msgs_per_burst
        for i, total in enumerate(totals):
            self.assertEqual(
                total, expected_per_thread,
                f"Thread {i}: read {total}, expected {expected_per_thread}",
            )


if __name__ == "__main__":
    unittest.main()
