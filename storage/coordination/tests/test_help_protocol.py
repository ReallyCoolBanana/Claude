#!/usr/bin/env python3
"""Tests for the Team Help Protocol and Idle Detection system.

Verifies:
- Team registration and capability matching
- Help request -> offer -> accept flow
- Idle detection
- Auto-assignment
- Concurrent help offers (only one wins)
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

# Add parent directory to path so we can import help_protocol
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from help_protocol import HelpProtocol


class HelpProtocolTestBase(unittest.TestCase):
    """Base class that creates a temporary DB and bus directory."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="help_proto_test_")
        self.db_path = os.path.join(self._tmpdir, "state.db")
        self.bus_dir = os.path.join(self._tmpdir, "bus")
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_hp(self, team: str = "team-a", agent: str = "agent-1") -> HelpProtocol:
        return HelpProtocol(self.db_path, self.bus_dir, team, agent)


class TestTeamRegistration(HelpProtocolTestBase):
    """Test team status and capability registration."""

    def test_register_capabilities(self):
        hp = self._make_hp("team-alpha", "lead-1")
        hp.register_capabilities(["coding", "testing", "research"])
        hp.close()

        # Read back via a new connection
        hp2 = self._make_hp("_reader", "_reader")
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM team_capabilities WHERE team = 'team-alpha'"
        ).fetchall()
        conn.close()
        hp2.close()

        caps = {r["capability"] for r in rows}
        self.assertEqual(caps, {"coding", "testing", "research"})

    def test_update_status(self):
        hp = self._make_hp("team-beta", "lead-2")
        hp.update_status("working", 45.0, "Implementing feature X", time.time() + 600)
        hp.close()

        hp2 = self._make_hp("_reader", "_reader")
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM team_status WHERE team = 'team-beta'"
        ).fetchone()
        conn.close()
        hp2.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "working")
        self.assertAlmostEqual(row["progress_pct"], 45.0, places=1)
        self.assertEqual(row["current_task"], "Implementing feature X")


class TestWorkItems(HelpProtocolTestBase):
    """Test work item CRUD operations."""

    def test_add_and_complete_work_item(self):
        hp = self._make_hp("team-a", "dev-1")
        wid = hp.add_work_item("Build module", description="Build the help module",
                               priority="high", est_minutes=30,
                               required_caps=["coding"])
        self.assertIsInstance(wid, int)
        self.assertGreater(wid, 0)

        # Complete it
        hp.complete_work_item(wid)

        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        conn.close()
        hp.close()

        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["title"], "Build module")
        self.assertEqual(row["priority"], "high")

    def test_mark_work_available(self):
        hp = self._make_hp("team-a", "dev-1")
        wid = hp.add_work_item("Test feature")
        hp.mark_work_available(wid)

        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
        conn.close()
        hp.close()

        self.assertEqual(row["status"], "available_for_help")


class TestHelpRequestFlow(HelpProtocolTestBase):
    """Test the full help request -> offer -> accept -> fulfill flow."""

    def test_full_flow(self):
        # Team A creates work and requests help
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("Analyze data", description="Need help analyzing dataset",
                                 required_caps=["data-analysis"])
        rid = hp_a.request_help(wid, "Need help analyzing a large dataset")
        self.assertIsInstance(rid, int)

        # Verify the request is open
        open_reqs = hp_a.get_open_help_requests()
        self.assertEqual(len(open_reqs), 1)
        self.assertEqual(open_reqs[0]["id"], rid)
        self.assertEqual(open_reqs[0]["status"], "open")

        # Team B offers help
        hp_b = self._make_hp("team-b", "lead-b")
        won = hp_b.offer_help(rid)
        self.assertTrue(won)

        # Verify the request is accepted
        open_reqs = hp_a.get_open_help_requests()
        self.assertEqual(len(open_reqs), 0)

        # Fulfill
        hp_b.fulfill_help(rid)

        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        req = conn.execute("SELECT * FROM help_requests WHERE id = ?", (rid,)).fetchone()
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        conn.close()

        self.assertEqual(req["status"], "fulfilled")
        self.assertEqual(wi["status"], "completed")
        self.assertEqual(req["accepted_by_team"], "team-b")

        hp_a.close()
        hp_b.close()

    def test_offer_on_nonexistent_request(self):
        hp = self._make_hp("team-x", "agent-x")
        won = hp.offer_help(99999)
        self.assertFalse(won)
        hp.close()

    def test_double_offer_fails(self):
        """Second offer on an already-accepted request should fail."""
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("Task")
        rid = hp_a.request_help(wid, "Help needed")

        hp_b = self._make_hp("team-b", "lead-b")
        hp_c = self._make_hp("team-c", "lead-c")

        won_b = hp_b.offer_help(rid)
        won_c = hp_c.offer_help(rid)

        self.assertTrue(won_b)
        self.assertFalse(won_c)

        hp_a.close()
        hp_b.close()
        hp_c.close()


class TestConcurrentOffers(HelpProtocolTestBase):
    """Test that concurrent help offers are handled atomically."""

    def test_concurrent_offers_only_one_wins(self):
        """Launch multiple threads to offer help; exactly one should win."""
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("Concurrent task")
        rid = hp_a.request_help(wid, "Race condition test")
        hp_a.close()

        num_teams = 5
        results = [None] * num_teams
        barriers = threading.Barrier(num_teams)

        def offer(idx):
            hp = self._make_hp(f"team-{idx}", f"agent-{idx}")
            barriers.wait()  # Synchronize start
            results[idx] = hp.offer_help(rid)
            hp.close()

        threads = [threading.Thread(target=offer, args=(i,)) for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        winners = sum(1 for r in results if r is True)
        losers = sum(1 for r in results if r is False)

        self.assertEqual(winners, 1, f"Expected exactly 1 winner, got {winners}")
        self.assertEqual(losers, num_teams - 1)


class TestIdleDetection(HelpProtocolTestBase):
    """Test idle and busy team detection."""

    def test_idle_teams(self):
        hp_a = self._make_hp("team-a", "lead-a")
        hp_b = self._make_hp("team-b", "lead-b")
        hp_c = self._make_hp("team-c", "lead-c")

        hp_a.update_status("idle", 100.0, "Done")
        hp_b.update_status("working", 50.0, "Busy")
        hp_c.update_status("complete", 100.0, "All done")

        idle = hp_a.get_idle_teams()
        idle_names = {t["team"] for t in idle}
        self.assertIn("team-a", idle_names)
        self.assertIn("team-c", idle_names)
        self.assertNotIn("team-b", idle_names)

        busy = hp_a.get_busy_teams()
        busy_names = {t["team"] for t in busy}
        self.assertIn("team-b", busy_names)
        self.assertNotIn("team-a", busy_names)

        hp_a.close()
        hp_b.close()
        hp_c.close()

    def test_teams_needing_help(self):
        hp_a = self._make_hp("team-a", "lead-a")
        hp_a.update_status("needs_help", 30.0, "Stuck on analysis")

        hp_b = self._make_hp("team-b", "lead-b")
        hp_b.update_status("working", 60.0, "Making progress")

        needing = hp_a.get_teams_needing_help()
        needing_names = {t["team"] for t in needing}
        self.assertIn("team-a", needing_names)

        hp_a.close()
        hp_b.close()


class TestAutoAssignment(HelpProtocolTestBase):
    """Test automatic assignment of idle teams to help requests."""

    def test_auto_assign(self):
        # Team A needs help with coding
        hp_a = self._make_hp("team-a", "lead-a")
        hp_a.update_status("needs_help", 30.0, "Need coding help")
        wid = hp_a.add_work_item("Code review", required_caps=["coding"])
        rid = hp_a.request_help(wid, "Need someone to review code")

        # Team B is idle with coding capability
        hp_b = self._make_hp("team-b", "lead-b")
        hp_b.register_capabilities(["coding", "testing"])
        hp_b.update_status("idle", 100.0, "Done with our work")

        # Team C is idle but no coding capability
        hp_c = self._make_hp("team-c", "lead-c")
        hp_c.register_capabilities(["research"])
        hp_c.update_status("idle", 100.0, "Done")

        # Run auto-assignment
        assignments = hp_a.auto_assign_idle_teams()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["team"], "team-b")
        self.assertEqual(assignments[0]["request_id"], rid)

        # Verify request is accepted
        open_reqs = hp_a.get_open_help_requests()
        self.assertEqual(len(open_reqs), 0)

        hp_a.close()
        hp_b.close()
        hp_c.close()

    def test_auto_assign_no_idle_teams(self):
        hp_a = self._make_hp("team-a", "lead-a")
        hp_a.update_status("needs_help", 30.0, "Stuck")
        wid = hp_a.add_work_item("Task")
        hp_a.request_help(wid, "Help needed")

        # No other teams registered as idle
        assignments = hp_a.auto_assign_idle_teams()
        self.assertEqual(len(assignments), 0)

        hp_a.close()

    def test_auto_assign_fallback_no_caps(self):
        """When no capability match, fall back to any idle team."""
        hp_a = self._make_hp("team-a", "lead-a")
        hp_a.update_status("needs_help", 30.0, "Stuck")
        wid = hp_a.add_work_item("Generic task")  # No required caps
        rid = hp_a.request_help(wid, "Need generic help")

        hp_b = self._make_hp("team-b", "lead-b")
        hp_b.update_status("idle", 100.0, "Free")

        assignments = hp_a.auto_assign_idle_teams()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["team"], "team-b")

        hp_a.close()
        hp_b.close()


class TestCapabilityMatching(HelpProtocolTestBase):
    """Test capability-based helper matching."""

    def test_find_compatible_helpers(self):
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("ML task", required_caps=["ml", "python"])
        rid = hp_a.request_help(wid, "Need ML expertise")

        # Team B: has ml and python (perfect match)
        hp_b = self._make_hp("team-b", "lead-b")
        hp_b.register_capabilities(["ml", "python", "data"])
        hp_b.update_status("idle", 100.0, "Free")

        # Team C: has python only (partial match)
        hp_c = self._make_hp("team-c", "lead-c")
        hp_c.register_capabilities(["python", "web"])
        hp_c.update_status("idle", 100.0, "Free")

        # Team D: has no matching caps
        hp_d = self._make_hp("team-d", "lead-d")
        hp_d.register_capabilities(["design"])
        hp_d.update_status("idle", 100.0, "Free")

        helpers = hp_a.find_compatible_helpers(rid)
        self.assertGreaterEqual(len(helpers), 2)
        # Team B should be first (better match)
        self.assertEqual(helpers[0]["team"], "team-b")
        self.assertAlmostEqual(helpers[0]["match_score"], 1.0)
        # Team C should be second (partial match)
        self.assertEqual(helpers[1]["team"], "team-c")
        self.assertAlmostEqual(helpers[1]["match_score"], 0.5)

        hp_a.close()
        hp_b.close()
        hp_c.close()
        hp_d.close()


class TestBusMessages(HelpProtocolTestBase):
    """Test that bus messages are published for key events."""

    def test_help_request_publishes_bus_message(self):
        hp = self._make_hp("team-a", "lead-a")
        wid = hp.add_work_item("Bus test task")
        hp.request_help(wid, "Testing bus messages")
        hp.close()

        # Check bus file exists and has a help-request event
        bus_file = os.path.join(self.bus_dir, "global.jsonl")
        self.assertTrue(os.path.exists(bus_file))
        with open(bus_file) as f:
            lines = f.readlines()
        # Should have at least one message with help-request event
        found = False
        for line in lines:
            msg = json.loads(line)
            if msg.get("body", {}).get("event") == "help-request":
                found = True
                break
        self.assertTrue(found, "Expected help-request bus message")

    def test_status_update_publishes_bus_message(self):
        hp = self._make_hp("team-x", "agent-x")
        hp.update_status("working", 75.0, "Building features")
        hp.close()

        bus_file = os.path.join(self.bus_dir, "global.jsonl")
        with open(bus_file) as f:
            lines = f.readlines()
        found = False
        for line in lines:
            msg = json.loads(line)
            if msg.get("body", {}).get("event") == "status-update":
                self.assertEqual(msg["body"]["team"], "team-x")
                self.assertEqual(msg["body"]["status"], "working")
                found = True
                break
        self.assertTrue(found, "Expected status-update bus message")


if __name__ == "__main__":
    unittest.main()
