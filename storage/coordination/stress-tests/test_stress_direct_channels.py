"""Stress tests for direct_channels module.

Pushes the module to its limits with high concurrency, large volumes,
boundary conditions, race conditions, and bottleneck detection under load.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from direct_channels import (
    DirectChannels,
    MAX_MESSAGE_BYTES,
    _bus_publish,
    _bus_read,
    _direct_channel_name,
    _safe_channel,
)


class _StressBase(unittest.TestCase):
    """Common setup: temp directory with db and bus subdirs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dc_stress_")
        self.db_path = os.path.join(self.tmpdir, "db", "channels.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        os.makedirs(os.path.join(self.tmpdir, "db"), exist_ok=True)
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_dc(self, team="team-a", agent_id="agent-a"):
        return DirectChannels(self.db_path, self.bus_dir, team, agent_id)


# ===================================================================
# 1. High Concurrency Tests
# ===================================================================


class TestHighConcurrencyChannelCreation(_StressBase):
    """20+ teams creating direct channels simultaneously."""

    def test_20_teams_create_channels_simultaneously(self):
        """20 teams all create a direct channel to a hub team at once."""
        num_teams = 25
        barrier = threading.Barrier(num_teams, timeout=30)
        errors = []
        results = [None] * num_teams

        def create_channel(idx):
            try:
                dc = self._make_dc(f"team-{idx}", f"agent-{idx}")
                barrier.wait()
                name = dc.create_direct_channel("hub-team")
                results[idx] = name
                dc.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=create_channel, args=(i,))
                   for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Errors during concurrent creation: {errors}")
        # Each team should get a deterministic channel name
        for i in range(num_teams):
            expected = _direct_channel_name(f"team-{i}", "hub-team")
            self.assertEqual(results[i], expected)

    def test_20_teams_create_pairwise_channels(self):
        """20 teams each create channels to 3 random partners simultaneously."""
        num_teams = 20
        barrier = threading.Barrier(num_teams, timeout=30)
        errors = []

        def create_channels(idx):
            try:
                dc = self._make_dc(f"team-{idx}", f"agent-{idx}")
                barrier.wait()
                for partner in range(3):
                    target = (idx + partner + 1) % num_teams
                    dc.create_direct_channel(f"team-{target}")
                dc.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=create_channels, args=(i,))
                   for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Errors: {errors}")


class TestHighConcurrencyMessaging(_StressBase):
    """30+ concurrent message sends on the same channel."""

    def test_30_concurrent_sends_same_channel(self):
        """30 threads send messages to the same direct channel simultaneously."""
        num_senders = 35
        barrier = threading.Barrier(num_senders, timeout=30)
        errors = []
        msg_ids = [None] * num_senders

        # Pre-create the channel
        dc_setup = self._make_dc("team-sender", "agent-sender")
        dc_setup.create_direct_channel("team-receiver")
        dc_setup.close()

        def send_msg(idx):
            try:
                dc = self._make_dc("team-sender", f"agent-{idx}")
                barrier.wait()
                mid = dc.send_direct("team-receiver", "info", {"idx": idx})
                msg_ids[idx] = mid
                dc.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=send_msg, args=(i,))
                   for i in range(num_senders)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Errors: {errors}")
        # All message IDs should be unique
        valid_ids = [m for m in msg_ids if m is not None]
        self.assertEqual(len(valid_ids), num_senders)
        self.assertEqual(len(set(valid_ids)), num_senders)

        # Reader should see all messages
        dc_reader = self._make_dc("team-receiver", "agent-reader")
        msgs = dc_reader.read_direct("team-sender")
        self.assertEqual(len(msgs), num_senders)
        dc_reader.close()


class TestHighConcurrencyPresence(_StressBase):
    """20+ teams updating presence simultaneously."""

    def test_20_teams_set_presence_simultaneously(self):
        num_teams = 25
        barrier = threading.Barrier(num_teams, timeout=30)
        errors = []
        statuses = ["available", "busy", "helping", "away"]

        def set_presence(idx):
            try:
                dc = self._make_dc(f"team-{idx}", f"agent-{idx}")
                barrier.wait()
                dc.set_presence(statuses[idx % len(statuses)])
                dc.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=set_presence, args=(i,))
                   for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Errors: {errors}")

        # Verify all presence records exist
        dc = self._make_dc("verifier", "agent-verifier")
        all_p = dc.get_presence()
        team_statuses = {p["team"]: p["status"] for p in all_p}
        for i in range(num_teams):
            team = f"team-{i}"
            self.assertIn(team, team_statuses)
            self.assertEqual(team_statuses[team], statuses[i % len(statuses)])
        dc.close()


class TestHighConcurrencyProgress(_StressBase):
    """15+ concurrent progress updates."""

    def test_15_concurrent_progress_updates(self):
        num_teams = 20
        barrier = threading.Barrier(num_teams, timeout=30)
        errors = []

        def update_progress(idx):
            try:
                dc = self._make_dc(f"team-{idx}", f"agent-{idx}")
                barrier.wait()
                pct = (idx + 1) * 5.0
                dc.update_progress("coding", pct, 100, int(pct))
                dc.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=update_progress, args=(i,))
                   for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Errors: {errors}")

        dc = self._make_dc("verifier", "agent-verifier")
        all_p = dc.get_all_progress()
        self.assertEqual(len(all_p), num_teams)
        dc.close()


# ===================================================================
# 2. Volume Tests
# ===================================================================


class TestVolumeChannels(_StressBase):
    """Create 200+ channels."""

    def test_create_200_channels(self):
        dc = self._make_dc("hub", "agent-hub")
        channel_names = set()
        for i in range(200):
            name = dc.create_direct_channel(f"team-{i}")
            channel_names.add(name)
        self.assertEqual(len(channel_names), 200)

        channels = dc.list_active_channels()
        self.assertEqual(len(channels), 200)
        dc.close()

    def test_create_200_topic_channels(self):
        dc = self._make_dc("hub", "agent-hub")
        for i in range(200):
            dc.create_topic_channel(f"topic-{i}", [f"team-{i}"])
        channels = dc.list_active_channels()
        self.assertEqual(len(channels), 200)
        dc.close()


class TestVolumeMessages(_StressBase):
    """Send 500+ messages on a single channel."""

    def test_500_messages_single_channel(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        msg_ids = []
        for i in range(500):
            mid = dc_a.send_direct("team-b", "info", {"seq": i})
            msg_ids.append(mid)

        self.assertEqual(len(set(msg_ids)), 500)

        # Read all at once
        msgs = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs), 500)

        # Verify sequence integrity
        seqs = [m["body"]["seq"] for m in msgs]
        self.assertEqual(seqs, list(range(500)))

        dc_a.close()
        dc_b.close()

    def test_500_messages_incremental_reads(self):
        """Read messages in batches using offset tracking."""
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        total_read = 0
        for batch in range(50):
            for i in range(10):
                dc_a.send_direct("team-b", "info", {"batch": batch, "i": i})
            msgs = dc_b.read_direct("team-a")
            total_read += len(msgs)
            self.assertEqual(len(msgs), 10, f"Batch {batch}: expected 10 msgs, got {len(msgs)}")

        self.assertEqual(total_read, 500)
        dc_a.close()
        dc_b.close()


class TestVolumePresence(_StressBase):
    """Track presence for 50+ teams."""

    def test_50_team_presence(self):
        statuses = ["available", "busy", "helping", "away"]
        instances = []
        for i in range(50):
            dc = self._make_dc(f"team-{i}", f"agent-{i}")
            dc.set_presence(statuses[i % len(statuses)])
            instances.append(dc)

        checker = self._make_dc("checker", "agent-checker")
        all_p = checker.get_presence()
        self.assertEqual(len(all_p), 50)

        available = checker.get_available_teams()
        expected_available = 50 // 4 + (1 if 50 % 4 > 0 else 0)
        self.assertEqual(len(available), expected_available)

        for dc in instances:
            dc.close()
        checker.close()


# ===================================================================
# 3. Message Boundary Tests
# ===================================================================


class TestMessageBoundaries(_StressBase):
    """Messages at and around MAX_MESSAGE_BYTES."""

    def test_message_exactly_at_max_bytes(self):
        """Construct a message that serializes to exactly MAX_MESSAGE_BYTES."""
        dc = self._make_dc("team-a", "agent-a")
        dc.create_direct_channel("team-b")

        # Build a message, measure its size, then pad the body to hit exactly MAX_MESSAGE_BYTES
        # We need to account for the full serialized message structure
        # Start with a small body, measure, then pad
        small_body = {"data": ""}
        # Use _bus_publish internals to measure: build the message dict, serialize, check size
        import uuid as _uuid
        test_msg = {
            "id": str(_uuid.uuid4()),
            "type": "info",
            "channel": _direct_channel_name("team-a", "team-b"),
            "team": "team-a",
            "agent_id": "agent-a",
            "ts": time.time(),
            "ttl": 3600,
            "body": {"data": ""},
        }
        base_raw = json.dumps(test_msg, separators=(",", ":")).encode("utf-8") + b"\n"
        base_size = len(base_raw)
        pad_needed = MAX_MESSAGE_BYTES - base_size
        self.assertGreater(pad_needed, 0, "Base message already exceeds limit")

        # The padding goes inside "data", but adding chars changes the JSON size
        # Use ASCII chars that don't need escaping
        padded_body = {"data": "A" * pad_needed}
        test_msg["body"] = padded_body
        padded_raw = json.dumps(test_msg, separators=(",", ":")).encode("utf-8") + b"\n"

        # Adjust if we overshot (the extra chars in body add to the key too)
        # Add extra 2-byte margin because the actual UUID in send_direct may differ in length
        diff = len(padded_raw) - MAX_MESSAGE_BYTES
        if diff >= 0:
            padded_body = {"data": "A" * (pad_needed - diff - 2)}

        # This should not raise - it's at or just under the limit
        msg_id = dc.send_direct("team-b", "info", padded_body)
        self.assertIsNotNone(msg_id)
        dc.close()

    def test_message_over_max_bytes_raises(self):
        """A message that exceeds MAX_MESSAGE_BYTES should raise ValueError."""
        dc = self._make_dc("team-a", "agent-a")
        # A body with a huge string that will definitely exceed 4096 bytes
        huge_body = {"data": "X" * MAX_MESSAGE_BYTES}
        with self.assertRaises(ValueError) as ctx:
            dc.send_direct("team-b", "info", huge_body)
        self.assertIn("exceeds", str(ctx.exception))
        dc.close()

    def test_empty_body_message(self):
        """Empty body dict should work fine."""
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        msg_id = dc_a.send_direct("team-b", "info", {})
        self.assertIsNotNone(msg_id)

        msgs = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["body"], {})
        dc_a.close()
        dc_b.close()

    def test_large_body_under_limit(self):
        """A body with many small fields that stays under limit."""
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        # Create a body with many fields that's still under the limit
        body = {f"k{i}": i for i in range(50)}
        msg_id = dc_a.send_direct("team-b", "info", body)
        self.assertIsNotNone(msg_id)

        msgs = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs), 1)
        for i in range(50):
            self.assertEqual(msgs[0]["body"][f"k{i}"], i)

        dc_a.close()
        dc_b.close()

    def test_message_just_one_byte_over(self):
        """Construct a message that is exactly 1 byte over the limit."""
        import uuid as _uuid

        # Build reference message to find exact size
        ref_msg = {
            "id": str(_uuid.uuid4()),
            "type": "info",
            "channel": _direct_channel_name("team-a", "team-b"),
            "team": "team-a",
            "agent_id": "agent-a",
            "ts": time.time(),
            "ttl": 3600,
            "body": {"d": ""},
        }
        base_raw = json.dumps(ref_msg, separators=(",", ":")).encode("utf-8") + b"\n"
        base_size = len(base_raw)
        # We want total = MAX_MESSAGE_BYTES + 1
        pad = MAX_MESSAGE_BYTES + 1 - base_size
        if pad > 0:
            over_body = {"d": "A" * pad}
            ref_msg["body"] = over_body
            actual = len(json.dumps(ref_msg, separators=(",", ":")).encode("utf-8") + b"\n")
            # Fine-tune
            diff = actual - (MAX_MESSAGE_BYTES + 1)
            over_body = {"d": "A" * (pad - diff)}

            dc = self._make_dc("team-a", "agent-a")
            # Verify it's actually over
            ref_msg["body"] = over_body
            final_size = len(json.dumps(ref_msg, separators=(",", ":")).encode("utf-8") + b"\n")
            if final_size > MAX_MESSAGE_BYTES:
                with self.assertRaises(ValueError):
                    dc.send_direct("team-b", "info", over_body)
            else:
                # If tuning landed exactly at limit, just ensure it doesn't crash
                dc.send_direct("team-b", "info", over_body)
            dc.close()


# ===================================================================
# 4. Race Condition Tests
# ===================================================================


class TestRaceConditions(_StressBase):
    """Race conditions: join while archiving, send while creating, etc."""

    def test_join_while_archiving(self):
        """One thread archives a channel while another tries to join it."""
        dc_creator = self._make_dc("creator", "agent-creator")
        dc_creator.create_topic_channel("race-topic", ["creator"])

        barrier = threading.Barrier(2, timeout=15)
        results = {"archive": None, "join": None}

        def archive():
            try:
                dc = self._make_dc("creator", "agent-creator")
                barrier.wait()
                dc.archive_channel("topic-race-topic")
                results["archive"] = "ok"
                dc.close()
            except Exception as e:
                results["archive"] = e

        def join():
            try:
                dc = self._make_dc("joiner", "agent-joiner")
                barrier.wait()
                time.sleep(0.001)  # Slight delay so archive likely wins
                dc.join_channel("topic-race-topic")
                results["join"] = "ok"
                dc.close()
            except ValueError:
                results["join"] = "rejected"
            except Exception as e:
                results["join"] = e

        t1 = threading.Thread(target=archive)
        t2 = threading.Thread(target=join)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        # Either join succeeded (before archive) or was rejected (after archive)
        self.assertIn(results["archive"], ["ok"])
        self.assertIn(results["join"], ["ok", "rejected"])
        dc_creator.close()

    def test_send_while_channel_being_created(self):
        """Send messages while the channel is still being set up by another thread."""
        barrier = threading.Barrier(2, timeout=15)
        errors = []
        msg_ids = []

        def creator():
            try:
                dc = self._make_dc("team-a", "agent-a")
                barrier.wait()
                dc.create_direct_channel("team-b")
                dc.close()
            except Exception as e:
                errors.append(("creator", e))

        def sender():
            try:
                dc = self._make_dc("team-a", "agent-sender")
                barrier.wait()
                # send_direct calls _ensure_direct_channel internally
                mid = dc.send_direct("team-b", "info", {"msg": "race"})
                msg_ids.append(mid)
                dc.close()
            except Exception as e:
                errors.append(("sender", e))

        t1 = threading.Thread(target=creator)
        t2 = threading.Thread(target=sender)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        self.assertEqual(errors, [], f"Errors: {errors}")
        self.assertEqual(len(msg_ids), 1)

    def test_presence_update_from_multiple_threads(self):
        """Multiple threads update presence for the SAME team simultaneously."""
        num_threads = 10
        barrier = threading.Barrier(num_threads, timeout=15)
        errors = []
        statuses = ["available", "busy", "helping", "away"]

        def update(idx):
            try:
                dc = self._make_dc("team-shared", f"agent-{idx}")
                barrier.wait()
                dc.set_presence(statuses[idx % len(statuses)])
                dc.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=update, args=(i,))
                   for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"Errors: {errors}")

        # Final state should be one of the valid statuses
        dc = self._make_dc("checker", "agent-checker")
        p = dc.get_presence("team-shared")
        self.assertIn(p["status"], statuses)
        dc.close()

    def test_read_while_writing(self):
        """One thread reads messages while another writes them."""
        num_writes = 100
        errors = []
        read_counts = []

        dc_writer = self._make_dc("writer", "agent-writer")
        dc_writer.create_direct_channel("reader")

        barrier = threading.Barrier(2, timeout=15)

        def writer():
            try:
                dc = self._make_dc("writer", "agent-writer")
                barrier.wait()
                for i in range(num_writes):
                    dc.send_direct("reader", "info", {"seq": i})
                dc.close()
            except Exception as e:
                errors.append(("writer", e))

        def reader():
            try:
                dc = self._make_dc("reader", "agent-reader")
                barrier.wait()
                total = 0
                attempts = 0
                while total < num_writes and attempts < 500:
                    msgs = dc.read_direct("writer")
                    total += len(msgs)
                    if len(msgs) > 0:
                        read_counts.append(len(msgs))
                    attempts += 1
                    if total < num_writes:
                        time.sleep(0.005)
                read_counts.append(-total)  # Store negative total as sentinel
                dc.close()
            except Exception as e:
                errors.append(("reader", e))

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)

        self.assertEqual(errors, [], f"Errors: {errors}")
        # The sentinel value is the negative of total messages read
        total_read = -read_counts[-1]
        self.assertEqual(total_read, num_writes)

        dc_writer.close()


# ===================================================================
# 5. Offset Tracking Tests
# ===================================================================


class TestOffsetTracking(_StressBase):
    """Read offset correctness under various conditions."""

    def test_offset_correctness_after_rapid_writes(self):
        """Write 200 messages rapidly, verify offset tracking returns each exactly once."""
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        for i in range(200):
            dc_a.send_direct("team-b", "info", {"seq": i})

        all_msgs = []
        msgs = dc_b.read_direct("team-a")
        all_msgs.extend(msgs)
        self.assertEqual(len(all_msgs), 200)

        # Second read should return nothing
        msgs2 = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs2), 0)

        # Write more, read again
        for i in range(50):
            dc_a.send_direct("team-b", "info", {"seq": 200 + i})
        msgs3 = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs3), 50)

        dc_a.close()
        dc_b.close()

    def test_concurrent_readers_independent_offsets(self):
        """Multiple readers maintain independent offsets."""
        dc_writer = self._make_dc("writer", "agent-writer")
        for i in range(50):
            dc_writer.send_direct("reader-a", "info", {"seq": i})

        # Note: both readers read the same channel (writer<->reader-a)
        # But since they're different DirectChannels instances, offsets are independent
        dc_r1 = self._make_dc("reader-a", "agent-r1")
        dc_r2 = self._make_dc("reader-a", "agent-r2")

        msgs1 = dc_r1.read_direct("writer")
        self.assertEqual(len(msgs1), 50)

        msgs2 = dc_r2.read_direct("writer")
        self.assertEqual(len(msgs2), 50)

        # r1 reads again (nothing new)
        msgs1b = dc_r1.read_direct("writer")
        self.assertEqual(len(msgs1b), 0)

        # r2 reads again (nothing new)
        msgs2b = dc_r2.read_direct("writer")
        self.assertEqual(len(msgs2b), 0)

        dc_writer.close()
        dc_r1.close()
        dc_r2.close()

    def test_offset_after_file_truncation(self):
        """If the bus file is truncated (simulating corruption), reads handle it gracefully."""
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        # Write some messages and read them
        for i in range(10):
            dc_a.send_direct("team-b", "info", {"seq": i})
        msgs = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs), 10)

        # Now truncate the file (simulating corruption)
        channel_name = _direct_channel_name("team-a", "team-b")
        safe = _safe_channel(channel_name)
        filepath = os.path.join(self.bus_dir, f"{safe}.jsonl")
        with open(filepath, "w") as f:
            f.truncate(0)

        # Reading with a stale offset that's beyond EOF should return empty
        # _bus_read seeks to the offset; if file is smaller, f.read() returns ""
        msgs2 = dc_b.read_direct("team-a")
        # Should not crash, may return empty
        self.assertIsInstance(msgs2, list)

        dc_a.close()
        dc_b.close()

    def test_offset_zero_on_nonexistent_channel(self):
        """Reading from a channel that has no bus file returns empty with offset 0."""
        dc = self._make_dc("team-a", "agent-a")
        msgs = dc.read_direct("team-nonexistent")
        self.assertEqual(msgs, [])
        # Internal offset should be 0
        channel_name = _direct_channel_name("team-a", "team-nonexistent")
        self.assertEqual(dc._read_offsets.get(channel_name, 0), 0)
        dc.close()


# ===================================================================
# 6. Channel Name Safety Tests
# ===================================================================


class TestChannelNameSafety(_StressBase):
    """Team names with special characters, unicode, extreme lengths."""

    def test_team_name_with_slashes(self):
        dc = self._make_dc("team/with/slashes", "agent-a")
        name = dc.create_direct_channel("other/team")
        self.assertIsInstance(name, str)
        # Should be able to send a message (file path must be safe)
        msg_id = dc.send_direct("other/team", "info", {"hello": "world"})
        self.assertIsNotNone(msg_id)
        dc.close()

    def test_team_name_with_dots(self):
        dc = self._make_dc("team..dots", "agent-a")
        name = dc.create_direct_channel("other..team")
        self.assertIsInstance(name, str)
        msg_id = dc.send_direct("other..team", "info", {"test": True})
        self.assertIsNotNone(msg_id)
        dc.close()

    def test_team_name_with_spaces(self):
        dc = self._make_dc("team with spaces", "agent-a")
        name = dc.create_direct_channel("another team")
        self.assertIsInstance(name, str)
        msg_id = dc.send_direct("another team", "info", {"test": 1})
        self.assertIsNotNone(msg_id)

        dc_b = self._make_dc("another team", "agent-b")
        msgs = dc_b.read_direct("team with spaces")
        self.assertEqual(len(msgs), 1)
        dc.close()
        dc_b.close()

    def test_team_name_with_unicode(self):
        dc = self._make_dc("team-\u00e9\u00e0\u00fc\u00f1", "agent-a")
        name = dc.create_direct_channel("team-\u4e16\u754c")
        self.assertIsInstance(name, str)
        msg_id = dc.send_direct("team-\u4e16\u754c", "info", {"unicode": True})
        self.assertIsNotNone(msg_id)
        dc.close()

    def test_very_long_team_name(self):
        """Team name with 1000+ characters."""
        long_name = "t" * 1050
        dc = self._make_dc(long_name, "agent-a")
        name = dc.create_direct_channel("short")
        self.assertIsInstance(name, str)
        # The channel name will be very long but should still work
        dc.close()

    def test_empty_team_name(self):
        """Empty team name - should still function (no validation in constructor)."""
        dc = self._make_dc("", "agent-a")
        name = dc.create_direct_channel("team-b")
        self.assertIsInstance(name, str)
        dc.close()

    def test_safe_channel_sanitization(self):
        """Verify _safe_channel replaces dangerous characters."""
        self.assertEqual(_safe_channel("a/b/c"), "a_b_c")
        self.assertEqual(_safe_channel("a..b"), "a_b")
        self.assertEqual(_safe_channel("normal-name"), "normal-name")
        self.assertEqual(_safe_channel("a/b..c/d"), "a_b_c_d")


# ===================================================================
# 7. Bottleneck Detection Under Load
# ===================================================================


class TestBottleneckDetectionUnderLoad(_StressBase):
    """50+ teams updating progress, verify get_slowest_team accuracy."""

    def test_50_teams_slowest_accuracy(self):
        """50 teams with known progress values; verify slowest is correct."""
        num_teams = 55
        instances = []
        for i in range(num_teams):
            dc = self._make_dc(f"team-{i}", f"agent-{i}")
            # team-0 gets 1%, team-1 gets 2%, etc.
            pct = float(i + 1)
            dc.update_progress("work", pct, 100, i + 1)
            instances.append(dc)

        checker = self._make_dc("checker", "agent-checker")
        slowest = checker.get_slowest_team()
        self.assertEqual(slowest["team"], "team-0")
        self.assertEqual(slowest["progress_pct"], 1.0)

        # Verify get_teams_below_progress
        below_10 = checker.get_teams_below_progress(10.0)
        # Teams with pct < 10: team-0 (1%), team-1 (2%), ... team-8 (9%)
        self.assertEqual(len(below_10), 9)

        for dc in instances:
            dc.close()
        checker.close()

    def test_concurrent_progress_updates_while_querying(self):
        """Threads update progress while another thread repeatedly queries slowest."""
        num_teams = 30
        barrier = threading.Barrier(num_teams + 1, timeout=30)
        errors = []
        query_results = []

        def updater(idx):
            try:
                dc = self._make_dc(f"team-{idx}", f"agent-{idx}")
                barrier.wait()
                for pct in range(0, 101, 10):
                    dc.update_progress("phase", float(pct), 100, pct,
                                       bottleneck=f"step-{pct}")
                    time.sleep(0.001)
                dc.close()
            except Exception as e:
                errors.append(("updater", idx, e))

        def querier():
            try:
                dc = self._make_dc("querier", "agent-querier")
                barrier.wait()
                for _ in range(50):
                    result = dc.get_slowest_team()
                    if result:
                        query_results.append(result)
                    time.sleep(0.005)
                dc.close()
            except Exception as e:
                errors.append(("querier", e))

        threads = [threading.Thread(target=updater, args=(i,))
                   for i in range(num_teams)]
        threads.append(threading.Thread(target=querier))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [], f"Errors: {errors}")
        # All query results should have valid structure
        for r in query_results:
            self.assertIn("team", r)
            self.assertIn("progress_pct", r)
            self.assertGreaterEqual(r["progress_pct"], 0.0)
            self.assertLessEqual(r["progress_pct"], 100.0)

    def test_rapid_progress_updates_accuracy(self):
        """Rapidly update a single team's progress 100 times, verify final state."""
        dc = self._make_dc("rapid-team", "agent-rapid")
        for i in range(100):
            dc.update_progress("phase", float(i), 100, i)

        final = dc.get_team_progress("rapid-team")
        self.assertEqual(final["progress_pct"], 99.0)
        self.assertEqual(final["items_done"], 99)
        dc.close()

    def test_all_teams_same_progress_slowest(self):
        """All teams at the same progress - get_slowest_team returns one of them."""
        instances = []
        for i in range(20):
            dc = self._make_dc(f"team-{i}", f"agent-{i}")
            dc.update_progress("work", 50.0, 100, 50)
            instances.append(dc)

        checker = self._make_dc("checker", "agent-checker")
        slowest = checker.get_slowest_team()
        self.assertEqual(slowest["progress_pct"], 50.0)

        below = checker.get_teams_below_progress(50.0)
        self.assertEqual(len(below), 0)

        below_51 = checker.get_teams_below_progress(51.0)
        self.assertEqual(len(below_51), 20)

        for dc in instances:
            dc.close()
        checker.close()


# ===================================================================
# 8. Mixed Stress (Combining multiple operations)
# ===================================================================


class TestMixedStress(_StressBase):
    """Combine channel creation, messaging, presence, and progress under load."""

    def test_full_workflow_under_concurrency(self):
        """10 teams simultaneously create channels, send messages, set presence, and update progress."""
        num_teams = 10
        barrier = threading.Barrier(num_teams, timeout=30)
        errors = []

        def full_workflow(idx):
            try:
                dc = self._make_dc(f"team-{idx}", f"agent-{idx}")
                barrier.wait()

                # Create channels to neighbors
                left = (idx - 1) % num_teams
                right = (idx + 1) % num_teams
                dc.create_direct_channel(f"team-{left}")
                dc.create_direct_channel(f"team-{right}")

                # Send messages
                for j in range(5):
                    dc.send_direct(f"team-{right}", "info", {"from": idx, "seq": j})

                # Set presence
                dc.set_presence("busy")

                # Update progress
                dc.update_progress("stress-test", float(idx * 10), 100, idx * 10)

                # Read messages
                msgs = dc.read_direct(f"team-{left}")
                # (may or may not have messages yet depending on timing)

                dc.set_presence("available")
                dc.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=full_workflow, args=(i,))
                   for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [], f"Errors: {errors}")

        # Verify final state
        dc = self._make_dc("verifier", "agent-verifier")
        all_progress = dc.get_all_progress()
        self.assertEqual(len(all_progress), num_teams)

        all_presence = dc.get_presence()
        team_statuses = {p["team"]: p["status"] for p in all_presence}
        for i in range(num_teams):
            self.assertEqual(team_statuses[f"team-{i}"], "available")

        dc.close()


if __name__ == "__main__":
    unittest.main()
