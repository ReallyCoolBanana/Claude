"""Tests for direct_channels module.

Covers:
- Channel creation and lifecycle (direct, topic, join, leave, archive)
- Direct messaging between teams
- Presence tracking
- Progress broadcasting and bottleneck detection
- Multiple concurrent channels
"""

import json
import os
import shutil
import tempfile
import unittest

# Adjust path so we can import direct_channels from the parent package
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from direct_channels import DirectChannels, _direct_channel_name


class _TestBase(unittest.TestCase):
    """Common setup: temp directory with db and bus subdirs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dc_test_")
        self.db_path = os.path.join(self.tmpdir, "db", "channels.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        os.makedirs(os.path.join(self.tmpdir, "db"), exist_ok=True)
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_dc(self, team="team-a", agent_id="agent-a"):
        return DirectChannels(self.db_path, self.bus_dir, team, agent_id)


# ===================================================================
# Channel creation and lifecycle
# ===================================================================


class TestChannelLifecycle(_TestBase):

    def test_create_direct_channel(self):
        dc = self._make_dc()
        name = dc.create_direct_channel("team-b")
        self.assertEqual(name, "direct-team-a-team-b")
        dc.close()

    def test_direct_channel_name_alphabetical(self):
        """Channel name is the same regardless of creation order."""
        self.assertEqual(
            _direct_channel_name("team-b", "team-a"),
            _direct_channel_name("team-a", "team-b"),
        )
        self.assertEqual(_direct_channel_name("team-b", "team-a"), "direct-team-a-team-b")

    def test_create_topic_channel(self):
        dc = self._make_dc()
        name = dc.create_topic_channel("blockers", ["team-b", "team-c"])
        self.assertEqual(name, "topic-blockers")
        channels = dc.list_active_channels()
        self.assertEqual(len(channels), 1)
        self.assertIn("team-a", channels[0]["participants"])
        self.assertIn("team-b", channels[0]["participants"])
        self.assertIn("team-c", channels[0]["participants"])
        dc.close()

    def test_join_channel(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        dc_a.create_topic_channel("design", ["team-a"])
        dc_b.join_channel("topic-design")

        # team-b should now see the channel
        channels_b = dc_b.list_active_channels()
        self.assertEqual(len(channels_b), 1)
        self.assertIn("team-b", channels_b[0]["participants"])

        dc_a.close()
        dc_b.close()

    def test_join_nonexistent_channel_raises(self):
        dc = self._make_dc()
        with self.assertRaises(ValueError):
            dc.join_channel("topic-does-not-exist")
        dc.close()

    def test_leave_channel(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        dc_a.create_topic_channel("review", ["team-a", "team-b"])
        dc_b.leave_channel("topic-review")

        # team-b should no longer see it
        channels_b = dc_b.list_active_channels()
        self.assertEqual(len(channels_b), 0)

        dc_a.close()
        dc_b.close()

    def test_archive_channel(self):
        dc = self._make_dc()
        dc.create_direct_channel("team-b")
        channels = dc.list_active_channels()
        self.assertEqual(len(channels), 1)

        dc.archive_channel("direct-team-a-team-b")
        channels = dc.list_active_channels()
        self.assertEqual(len(channels), 0)
        dc.close()

    def test_reactivate_archived_direct_channel(self):
        dc = self._make_dc()
        name = dc.create_direct_channel("team-b")
        dc.archive_channel(name)
        self.assertEqual(len(dc.list_active_channels()), 0)

        # Creating again should reactivate
        name2 = dc.create_direct_channel("team-b")
        self.assertEqual(name, name2)
        self.assertEqual(len(dc.list_active_channels()), 1)
        dc.close()


# ===================================================================
# Direct messaging
# ===================================================================


class TestDirectMessaging(_TestBase):

    def test_send_and_read(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        msg_id = dc_a.send_direct("team-b", "info", {"text": "hello from A"})
        self.assertIsInstance(msg_id, str)

        msgs = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["body"]["text"], "hello from A")
        self.assertEqual(msgs[0]["team"], "team-a")

        dc_a.close()
        dc_b.close()

    def test_read_tracks_offset(self):
        """Subsequent reads should only return new messages."""
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        dc_a.send_direct("team-b", "info", {"n": 1})
        msgs1 = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs1), 1)

        dc_a.send_direct("team-b", "info", {"n": 2})
        msgs2 = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs2), 1)
        self.assertEqual(msgs2[0]["body"]["n"], 2)

        dc_a.close()
        dc_b.close()

    def test_bidirectional_messaging(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        dc_a.send_direct("team-b", "info", {"from": "a"})
        dc_b.send_direct("team-a", "info", {"from": "b"})

        # Both should be on the same channel file
        msgs_b = dc_b.read_direct("team-a")
        self.assertEqual(len(msgs_b), 2)  # both messages on same channel

        dc_a.close()
        dc_b.close()

    def test_invalid_msg_type_raises(self):
        dc = self._make_dc()
        with self.assertRaises(ValueError):
            dc.send_direct("team-b", "invalid-type", {})
        dc.close()


# ===================================================================
# Presence tracking
# ===================================================================


class TestPresence(_TestBase):

    def test_set_and_get_presence(self):
        dc = self._make_dc()
        dc.set_presence("busy")
        p = dc.get_presence("team-a")
        self.assertEqual(p["status"], "busy")
        self.assertEqual(p["team"], "team-a")
        dc.close()

    def test_invalid_presence_raises(self):
        dc = self._make_dc()
        with self.assertRaises(ValueError):
            dc.set_presence("invalid-status")
        dc.close()

    def test_get_all_presence(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        dc_a.set_presence("available")
        dc_b.set_presence("busy")

        all_p = dc_a.get_presence()
        self.assertIsInstance(all_p, list)
        self.assertEqual(len(all_p), 2)

        statuses = {p["team"]: p["status"] for p in all_p}
        self.assertEqual(statuses["team-a"], "available")
        self.assertEqual(statuses["team-b"], "busy")

        dc_a.close()
        dc_b.close()

    def test_get_available_teams(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")
        dc_c = self._make_dc("team-c", "agent-c")

        dc_a.set_presence("available")
        dc_b.set_presence("busy")
        dc_c.set_presence("available")

        available = dc_a.get_available_teams()
        teams = {t["team"] for t in available}
        self.assertIn("team-a", teams)
        self.assertIn("team-c", teams)
        self.assertNotIn("team-b", teams)

        dc_a.close()
        dc_b.close()
        dc_c.close()

    def test_unknown_team_presence(self):
        dc = self._make_dc()
        p = dc.get_presence("nonexistent")
        self.assertEqual(p["status"], "unknown")
        dc.close()

    def test_presence_tracks_channels(self):
        dc = self._make_dc()
        dc.create_direct_channel("team-b")
        dc.set_presence("available")
        p = dc.get_presence("team-a")
        self.assertIn("direct-team-a-team-b", p["current_channels"])
        dc.close()


# ===================================================================
# Progress broadcasting and bottleneck detection
# ===================================================================


class TestProgress(_TestBase):

    def test_update_and_get_progress(self):
        dc = self._make_dc()
        dc.update_progress("research", 50.0, 10, 5)
        p = dc.get_team_progress("team-a")
        self.assertEqual(p["phase"], "research")
        self.assertEqual(p["progress_pct"], 50.0)
        self.assertEqual(p["items_total"], 10)
        self.assertEqual(p["items_done"], 5)
        dc.close()

    def test_get_all_progress(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")

        dc_a.update_progress("coding", 80.0, 20, 16)
        dc_b.update_progress("testing", 30.0, 10, 3)

        all_p = dc_a.get_all_progress()
        self.assertEqual(len(all_p), 2)
        # Sorted by progress_pct ascending
        self.assertEqual(all_p[0]["team"], "team-b")
        self.assertEqual(all_p[1]["team"], "team-a")

        dc_a.close()
        dc_b.close()

    def test_get_slowest_team(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")
        dc_c = self._make_dc("team-c", "agent-c")

        dc_a.update_progress("coding", 80.0, 10, 8)
        dc_b.update_progress("coding", 20.0, 10, 2, bottleneck="API rate limit")
        dc_c.update_progress("coding", 60.0, 10, 6)

        slowest = dc_a.get_slowest_team()
        self.assertEqual(slowest["team"], "team-b")
        self.assertEqual(slowest["progress_pct"], 20.0)
        self.assertEqual(slowest["current_bottleneck"], "API rate limit")

        dc_a.close()
        dc_b.close()
        dc_c.close()

    def test_get_teams_below_progress(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")
        dc_c = self._make_dc("team-c", "agent-c")

        dc_a.update_progress("impl", 90.0, 10, 9)
        dc_b.update_progress("impl", 40.0, 10, 4)
        dc_c.update_progress("impl", 10.0, 10, 1)

        below_50 = dc_a.get_teams_below_progress(50.0)
        teams = {t["team"] for t in below_50}
        self.assertEqual(teams, {"team-b", "team-c"})

        dc_a.close()
        dc_b.close()
        dc_c.close()

    def test_progress_update_overwrites(self):
        dc = self._make_dc()
        dc.update_progress("phase1", 10.0, 10, 1)
        dc.update_progress("phase2", 50.0, 20, 10)
        p = dc.get_team_progress("team-a")
        self.assertEqual(p["phase"], "phase2")
        self.assertEqual(p["progress_pct"], 50.0)
        dc.close()

    def test_unknown_team_progress(self):
        dc = self._make_dc()
        p = dc.get_team_progress("nonexistent")
        self.assertEqual(p["phase"], "unknown")
        self.assertEqual(p["progress_pct"], 0)
        dc.close()

    def test_slowest_team_empty(self):
        dc = self._make_dc()
        result = dc.get_slowest_team()
        self.assertEqual(result, {})
        dc.close()

    def test_progress_with_bottleneck_and_estimate(self):
        dc = self._make_dc()
        est = 1700000000.0
        dc.update_progress("deploy", 75.0, 4, 3,
                           est_completion_ts=est, bottleneck="CI pipeline")
        p = dc.get_team_progress("team-a")
        self.assertEqual(p["estimated_completion_ts"], est)
        self.assertEqual(p["current_bottleneck"], "CI pipeline")
        dc.close()


# ===================================================================
# Multiple concurrent channels
# ===================================================================


class TestMultipleChannels(_TestBase):

    def test_multiple_direct_channels(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")
        dc_c = self._make_dc("team-c", "agent-c")

        ch_ab = dc_a.create_direct_channel("team-b")
        ch_ac = dc_a.create_direct_channel("team-c")
        ch_bc = dc_b.create_direct_channel("team-c")

        self.assertNotEqual(ch_ab, ch_ac)
        self.assertNotEqual(ch_ab, ch_bc)

        # team-a is in 2 channels, team-b in 2, team-c in 2
        self.assertEqual(len(dc_a.list_active_channels()), 2)
        self.assertEqual(len(dc_b.list_active_channels()), 2)
        self.assertEqual(len(dc_c.list_active_channels()), 2)

        dc_a.close()
        dc_b.close()
        dc_c.close()

    def test_mixed_channel_types(self):
        dc = self._make_dc()
        dc.create_direct_channel("team-b")
        dc.create_topic_channel("design", ["team-a", "team-b", "team-c"])

        channels = dc.list_active_channels()
        self.assertEqual(len(channels), 2)
        types = {ch["channel_type"] for ch in channels}
        self.assertEqual(types, {"direct", "topic"})
        dc.close()

    def test_messages_isolated_per_channel(self):
        """Messages on different direct channels don't leak."""
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")
        dc_c = self._make_dc("team-c", "agent-c")

        dc_a.send_direct("team-b", "info", {"for": "b"})
        dc_a.send_direct("team-c", "info", {"for": "c"})

        msgs_b = dc_b.read_direct("team-a")
        msgs_c = dc_c.read_direct("team-a")

        self.assertEqual(len(msgs_b), 1)
        self.assertEqual(msgs_b[0]["body"]["for"], "b")
        self.assertEqual(len(msgs_c), 1)
        self.assertEqual(msgs_c[0]["body"]["for"], "c")

        dc_a.close()
        dc_b.close()
        dc_c.close()


# ===================================================================
# Bus event verification
# ===================================================================


class TestBusEvents(_TestBase):

    def _read_global(self):
        """Read all messages from the global bus channel."""
        filepath = os.path.join(self.bus_dir, "global.jsonl")
        if not os.path.exists(filepath):
            return []
        msgs = []
        with open(filepath, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        msgs.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return msgs

    def test_channel_created_event(self):
        dc = self._make_dc()
        dc.create_direct_channel("team-b")
        msgs = self._read_global()
        events = [m for m in msgs if m["body"].get("event") == "channel-created"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"]["channel"], "direct-team-a-team-b")
        self.assertEqual(events[0]["body"]["type"], "direct")
        dc.close()

    def test_presence_update_event(self):
        dc = self._make_dc()
        dc.set_presence("busy")
        msgs = self._read_global()
        events = [m for m in msgs if m["body"].get("event") == "presence-update"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"]["status"], "busy")
        dc.close()

    def test_progress_update_event(self):
        dc = self._make_dc()
        dc.update_progress("testing", 42.0, 100, 42)
        msgs = self._read_global()
        events = [m for m in msgs if m["body"].get("event") == "progress-update"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"]["progress_pct"], 42.0)
        dc.close()

    def test_channel_joined_event(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")
        dc_a.create_topic_channel("sync", ["team-a"])
        dc_b.join_channel("topic-sync")
        msgs = self._read_global()
        events = [m for m in msgs if m["body"].get("event") == "channel-joined"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["body"]["team"], "team-b")
        dc_a.close()
        dc_b.close()


if __name__ == "__main__":
    unittest.main()
