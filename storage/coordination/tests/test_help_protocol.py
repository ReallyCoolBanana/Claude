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


class TestBusWriteFailureDoesNotCrash(HelpProtocolTestBase):
    """Bug 1: _publish_bus() failures must not propagate to callers."""

    def test_bus_write_failure_does_not_crash_update_status(self):
        """If bus_dir is unwritable, update_status should still succeed (DB write)."""
        import stat
        bad_bus = os.path.join(self._tmpdir, "bad_bus")
        os.makedirs(bad_bus, exist_ok=True)
        # Make the directory read-only so bus writes fail
        os.chmod(bad_bus, stat.S_IRUSR | stat.S_IXUSR)
        try:
            hp = self._make_hp.__func__(self, "team-bustest", "agent-bt")
            # Override bus_dir to the unwritable directory
            hp = HelpProtocol(self.db_path, bad_bus, "team-bustest", "agent-bt")
            # This should NOT raise, even though the bus write will fail
            hp.update_status("working", 50.0, "Testing bus failure")

            # Verify the DB write succeeded
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status FROM team_status WHERE team = 'team-bustest'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "working")
            hp.close()
        finally:
            os.chmod(bad_bus, stat.S_IRWXU)

    def test_bus_write_failure_does_not_crash_request_help(self):
        """request_help should succeed in DB even if bus write fails."""
        import stat
        bad_bus = os.path.join(self._tmpdir, "bad_bus2")
        os.makedirs(bad_bus, exist_ok=True)
        # First create work item with working bus
        hp = self._make_hp("team-bustest2", "agent-bt2")
        wid = hp.add_work_item("Bus fail task")
        hp.close()

        os.chmod(bad_bus, stat.S_IRUSR | stat.S_IXUSR)
        try:
            hp2 = HelpProtocol(self.db_path, bad_bus, "team-bustest2", "agent-bt2")
            rid = hp2.request_help(wid, "Help with bus failure")
            self.assertIsInstance(rid, int)
            hp2.close()
        finally:
            os.chmod(bad_bus, stat.S_IRWXU)


class TestStatusValidation(HelpProtocolTestBase):
    """Bug 2: update_status() must validate status values."""

    def test_valid_statuses_accepted(self):
        hp = self._make_hp("team-valid", "agent-v")
        for status in ["idle", "working", "needs_help", "helping", "complete"]:
            hp.update_status(status, 50.0, f"Testing {status}")
        hp.close()

    def test_invalid_status_raises_valueerror(self):
        hp = self._make_hp("team-invalid", "agent-i")
        with self.assertRaises(ValueError) as ctx:
            hp.update_status("banana", 50.0, "Invalid")
        self.assertIn("banana", str(ctx.exception))
        hp.close()

    def test_empty_status_raises_valueerror(self):
        hp = self._make_hp("team-empty", "agent-e")
        with self.assertRaises(ValueError):
            hp.update_status("", 50.0, "Empty status")
        hp.close()


class TestClosedStateTracking(HelpProtocolTestBase):
    """Bug 3: Closed instances must raise RuntimeError on all public methods."""

    def test_closed_error_message(self):
        hp = self._make_hp("team-closed", "agent-c")
        hp.close()
        with self.assertRaises(RuntimeError) as ctx:
            hp.add_work_item("Should fail")
        self.assertEqual(str(ctx.exception), "HelpProtocol instance is closed")

    def test_all_public_methods_raise_after_close(self):
        hp = self._make_hp("team-closed2", "agent-c2")
        hp.close()

        with self.assertRaises(RuntimeError):
            hp.register_capabilities(["coding"])
        with self.assertRaises(RuntimeError):
            hp.update_status("idle", 0.0, "nope")
        with self.assertRaises(RuntimeError):
            hp.add_work_item("nope")
        with self.assertRaises(RuntimeError):
            hp.mark_work_available(1)
        with self.assertRaises(RuntimeError):
            hp.complete_work_item(1)
        with self.assertRaises(RuntimeError):
            hp.request_help(1, "nope")
        with self.assertRaises(RuntimeError):
            hp.get_open_help_requests()
        with self.assertRaises(RuntimeError):
            hp.offer_help(1)
        with self.assertRaises(RuntimeError):
            hp.fulfill_help(1)
        with self.assertRaises(RuntimeError):
            hp.get_idle_teams()
        with self.assertRaises(RuntimeError):
            hp.get_busy_teams()
        with self.assertRaises(RuntimeError):
            hp.get_teams_needing_help()
        with self.assertRaises(RuntimeError):
            hp.find_compatible_helpers(1)
        with self.assertRaises(RuntimeError):
            hp.auto_assign_idle_teams()


class TestEmptyListRequiredCaps(HelpProtocolTestBase):
    """Bug 4: Empty list [] should be stored as '[]', not None."""

    def test_empty_list_stored_as_json(self):
        hp = self._make_hp("team-caps", "agent-caps")
        wid = hp.add_work_item("Empty caps item", required_caps=[])

        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT required_capabilities FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()
        hp.close()

        # Empty list should be stored as JSON "[]", not None
        self.assertEqual(row["required_capabilities"], "[]")

    def test_none_caps_stored_as_null(self):
        hp = self._make_hp("team-caps2", "agent-caps2")
        wid = hp.add_work_item("None caps item", required_caps=None)

        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT required_capabilities FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()
        hp.close()

        self.assertIsNone(row["required_capabilities"])


class TestFulfillHelpAuthorization(HelpProtocolTestBase):
    """Bug 5: Only the accepted team should be able to fulfill a help request."""

    def test_wrong_team_cannot_fulfill(self):
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("Auth task")
        rid = hp_a.request_help(wid, "Auth test")

        hp_b = self._make_hp("team-b", "lead-b")
        hp_b.offer_help(rid)

        # Team C (not the accepted helper) should NOT be able to fulfill
        hp_c = self._make_hp("team-c", "lead-c")
        with self.assertRaises(ValueError) as ctx:
            hp_c.fulfill_help(rid)
        self.assertIn("team-c", str(ctx.exception))
        self.assertIn("team-b", str(ctx.exception))

        hp_a.close()
        hp_b.close()
        hp_c.close()

    def test_accepted_team_can_fulfill(self):
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("Auth task 2")
        rid = hp_a.request_help(wid, "Auth test 2")

        hp_b = self._make_hp("team-b", "lead-b")
        hp_b.offer_help(rid)

        # Team B (the accepted helper) should be able to fulfill
        hp_b.fulfill_help(rid)

        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM help_requests WHERE id = ?", (rid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "fulfilled")

        hp_a.close()
        hp_b.close()


class TestRetryOnBusyErrorCodes(HelpProtocolTestBase):
    """Bug 6: _retry_on_busy should use error codes, not string matching."""

    def test_is_busy_or_locked_with_error_code(self):
        """Verify _is_busy_or_locked detects busy/locked via error codes."""
        import sqlite3
        from help_protocol import _is_busy_or_locked

        # Create an OperationalError with sqlite_errorcode attribute
        busy_err = sqlite3.OperationalError("database is busy")
        busy_err.sqlite_errorcode = 5  # SQLITE_BUSY
        self.assertTrue(_is_busy_or_locked(busy_err))

        locked_err = sqlite3.OperationalError("database table is locked")
        locked_err.sqlite_errorcode = 6  # SQLITE_LOCKED
        self.assertTrue(_is_busy_or_locked(locked_err))

        # Extended error code (e.g., SQLITE_BUSY_SNAPSHOT = 517)
        extended_err = sqlite3.OperationalError("busy snapshot")
        extended_err.sqlite_errorcode = 517  # 517 & 0xFF = 5
        self.assertTrue(_is_busy_or_locked(extended_err))

        # Non-busy error should not match
        other_err = sqlite3.OperationalError("disk I/O error")
        other_err.sqlite_errorcode = 10  # SQLITE_IOERR
        self.assertFalse(_is_busy_or_locked(other_err))

    def test_is_busy_or_locked_fallback_string_matching(self):
        """Fallback to string matching when sqlite_errorcode is not available."""
        import sqlite3
        from help_protocol import _is_busy_or_locked

        # No sqlite_errorcode attribute (older Python)
        busy_err = sqlite3.OperationalError("database is busy")
        self.assertTrue(_is_busy_or_locked(busy_err))

        locked_err = sqlite3.OperationalError("database table is locked")
        self.assertTrue(_is_busy_or_locked(locked_err))

        other_err = sqlite3.OperationalError("disk I/O error")
        self.assertFalse(_is_busy_or_locked(other_err))


if __name__ == "__main__":
    unittest.main()
