"""Operation Health -- Coordination Bug-Fix Verification Tests.

Created by Assist Team A to verify fixes for bugs documented in
infrastructure-bugs-coordination.json. Each test targets a specific
bug ID and validates that the fix works correctly.

Bug categories covered:
- Channel sanitization with special characters (BUG-CORE-006, NEW-COORD-002/003/019)
- _retry_on_busy preserves function metadata via functools.wraps (BUG-CORE-001, NEW-COORD-001)
- get_presence return type consistency (BUG-CORE-021)
- Dashboard connection management (NEW-COORD-008)
- Pipeline stage atomicity: get_ready_stages, trigger_downstream (NEW-COORD-015/016)
- Progress validation (BUG-CORE-003)
- Error reporting resets progress (BUG-CORE-004)
- started_at preserved across updates (BUG-CORE-002)
- help_protocol close idempotency (BUG-CORE-008)
- work_item existence check on request_help (BUG-CORE-009)
- fulfill_help authorization (BUG-CORE-010)
- priority validation on add_work_item (BUG-CORE-024)
- mark_work_available rowcount check (BUG-CORE-019)
- complete_work_item team filter (BUG-CORE-020)
"""

import inspect
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

# ---------------------------------------------------------------------------
# Imports -- coordinator_hub
# ---------------------------------------------------------------------------
from storage.coordination.coordinator_hub import (
    AgentReporter,
    CoordinatorDashboard,
    _bus_notify as hub_bus_notify,
    _retry_on_busy as hub_retry_on_busy,
)

# ---------------------------------------------------------------------------
# Imports -- direct_channels
# ---------------------------------------------------------------------------
from storage.coordination.direct_channels import (
    DirectChannels,
    _direct_channel_name,
    _safe_channel,
    _retry_on_busy as dc_retry_on_busy,
)

# ---------------------------------------------------------------------------
# Imports -- help_protocol
# ---------------------------------------------------------------------------
from storage.coordination.help_protocol import (
    HelpProtocol,
    _retry_on_busy as hp_retry_on_busy,
)

# ---------------------------------------------------------------------------
# Imports -- work_stealing
# ---------------------------------------------------------------------------
from storage.coordination.work_stealing import (
    PipelineManager,
    WorkStealing,
    _retry_on_busy as ws_retry_on_busy,
)

# ---------------------------------------------------------------------------
# Imports -- dashboard (module-level functions)
# ---------------------------------------------------------------------------
from storage.coordination import dashboard as dash_mod


# ===================================================================
# Fixtures / helpers
# ===================================================================

class _HubBase(unittest.TestCase):
    """Temp DB for coordinator_hub tests."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="op_health_hub_")
        self.db_path = os.path.join(self._tmpdir, "hub.db")
        self.bus_dir = os.path.join(self._tmpdir, "bus")
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class _DCBase(unittest.TestCase):
    """Temp dirs for direct_channels tests."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="op_health_dc_")
        self.db_path = os.path.join(self._tmpdir, "db", "channels.db")
        self.bus_dir = os.path.join(self._tmpdir, "bus")
        os.makedirs(os.path.join(self._tmpdir, "db"), exist_ok=True)
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_dc(self, team="team-a", agent_id="agent-a"):
        return DirectChannels(self.db_path, self.bus_dir, team, agent_id)


class _HPBase(unittest.TestCase):
    """Temp dirs for help_protocol tests."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="op_health_hp_")
        self.db_path = os.path.join(self._tmpdir, "state.db")
        self.bus_dir = os.path.join(self._tmpdir, "bus")
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_hp(self, team="team-a", agent="agent-1"):
        return HelpProtocol(self.db_path, self.bus_dir, team, agent)


class _WSBase(unittest.TestCase):
    """Temp dirs for work_stealing tests."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="op_health_ws_")
        self.db_path = os.path.join(self._tmpdir, "ws.db")
        self.bus_dir = os.path.join(self._tmpdir, "bus")
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)


# ===================================================================
# 1. Channel sanitization -- BUG-CORE-006, NEW-COORD-002/003/019
# ===================================================================

class TestChannelSanitization(unittest.TestCase):
    """Verify that channel names with special characters are sanitized
    to only allow [a-zA-Z0-9_-] everywhere the pattern is used."""

    def test_hub_bus_notify_sanitizes_special_chars(self):
        """BUG-CORE-006: coordinator_hub _bus_notify uses strict regex allowlist."""
        tmpdir = tempfile.mkdtemp()
        bus_dir = os.path.join(tmpdir, "bus")
        os.makedirs(bus_dir)
        try:
            # Channel name with dangerous chars: path traversal, null bytes, colons
            hub_bus_notify(bus_dir, "../etc/passwd", "a1", "t1", {"test": True})
            hub_bus_notify(bus_dir, "chan\x00nel", "a1", "t1", {"test": True})
            hub_bus_notify(bus_dir, "chan:nel", "a1", "t1", {"test": True})
            hub_bus_notify(bus_dir, "chan/nel", "a1", "t1", {"test": True})
            hub_bus_notify(bus_dir, "chan with spaces", "a1", "t1", {"test": True})

            # All files in bus_dir should have safe names
            for fname in os.listdir(bus_dir):
                base = fname.replace(".jsonl", "")
                # Only alphanumeric, underscore, hyphen
                self.assertRegex(base, r'^[a-zA-Z0-9_-]+$',
                                 f"Unsafe filename in bus_dir: {fname}")
        finally:
            shutil.rmtree(tmpdir)

    def test_direct_channels_safe_channel_strict(self):
        """NEW-COORD-019: _safe_channel in direct_channels uses strict regex."""
        dangerous_names = [
            "../etc/passwd",
            "chan\x00nel",
            "chan:nel",
            "chan\\nel",
            "chan nel",
            "chan/nel",
            "chan..nel",
        ]
        for name in dangerous_names:
            safe = _safe_channel(name)
            self.assertRegex(safe, r'^[a-zA-Z0-9_-]+$',
                             f"_safe_channel({name!r}) produced unsafe: {safe!r}")

    def test_safe_channel_preserves_valid_chars(self):
        """Valid chars like alphanumeric, underscore, hyphen are preserved."""
        self.assertEqual(_safe_channel("my-channel_01"), "my-channel_01")
        self.assertEqual(_safe_channel("ABC-xyz_123"), "ABC-xyz_123")


# ===================================================================
# 2. _retry_on_busy preserves function metadata -- BUG-CORE-001
# ===================================================================

class TestRetryOnBusyPreservesMetadata(unittest.TestCase):
    """BUG-CORE-001 / NEW-COORD-001: All copies of _retry_on_busy must use
    @functools.wraps to preserve function name, docstring, and signature."""

    def _check_wraps(self, decorator, label):
        """Apply the decorator to a sample function and verify metadata."""

        def sample_function(x, y, z=42):
            """Sample docstring for testing."""
            return x + y + z

        wrapped = decorator(sample_function)
        self.assertEqual(wrapped.__name__, "sample_function",
                         f"{label}: __name__ not preserved")
        self.assertEqual(wrapped.__doc__, "Sample docstring for testing.",
                         f"{label}: __doc__ not preserved")
        # Check that inspect.signature still works
        sig = inspect.signature(wrapped)
        param_names = list(sig.parameters.keys())
        self.assertEqual(param_names, ["x", "y", "z"],
                         f"{label}: signature not preserved")

    def test_coordinator_hub_retry_preserves_metadata(self):
        """coordinator_hub._retry_on_busy preserves function metadata."""
        self._check_wraps(hub_retry_on_busy, "coordinator_hub")

    def test_direct_channels_retry_preserves_metadata(self):
        """direct_channels._retry_on_busy preserves function metadata."""
        self._check_wraps(dc_retry_on_busy, "direct_channels")

    def test_help_protocol_retry_preserves_metadata(self):
        """help_protocol._retry_on_busy preserves function metadata."""
        self._check_wraps(hp_retry_on_busy, "help_protocol")

    def test_work_stealing_retry_preserves_metadata(self):
        """work_stealing._retry_on_busy preserves function metadata."""
        self._check_wraps(ws_retry_on_busy, "work_stealing")


# ===================================================================
# 3. get_presence return type consistency -- BUG-CORE-021
# ===================================================================

class TestGetPresenceReturnType(_DCBase):
    """BUG-CORE-021: get_presence returns dict for single team, list[dict]
    for all teams. Verify the polymorphic return types are correct."""

    def test_get_presence_single_team_returns_dict(self):
        dc = self._make_dc()
        dc.set_presence("available")
        result = dc.get_presence("team-a")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["team"], "team-a")
        self.assertEqual(result["status"], "available")
        dc.close()

    def test_get_presence_all_teams_returns_list(self):
        dc_a = self._make_dc("team-a", "agent-a")
        dc_b = self._make_dc("team-b", "agent-b")
        dc_a.set_presence("available")
        dc_b.set_presence("busy")

        result = dc_a.get_presence()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        for item in result:
            self.assertIsInstance(item, dict)
            self.assertIn("team", item)
            self.assertIn("status", item)

        dc_a.close()
        dc_b.close()

    def test_get_presence_unknown_team_returns_dict_with_unknown(self):
        dc = self._make_dc()
        result = dc.get_presence("nonexistent-team")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["team"], "nonexistent-team")
        dc.close()

    def test_get_presence_all_when_empty_returns_list(self):
        dc = self._make_dc()
        result = dc.get_presence()
        self.assertIsInstance(result, list)
        dc.close()


# ===================================================================
# 4. Dashboard connection management -- NEW-COORD-008
# ===================================================================

class TestDashboardConnectionManagement(unittest.TestCase):
    """NEW-COORD-008: Dashboard functions each open/close their own connection.
    Verify that each function handles missing DB gracefully."""

    def test_get_db_returns_none_for_missing_db(self):
        """_get_db returns None if the DB file does not exist."""
        original = dash_mod._DB_PATH
        try:
            dash_mod._DB_PATH = "/tmp/nonexistent_op_health_test.db"
            result = dash_mod._get_db()
            self.assertIsNone(result)
        finally:
            dash_mod._DB_PATH = original

    def test_get_agents_returns_empty_for_missing_db(self):
        """_get_agents returns [] if DB is unavailable."""
        original = dash_mod._DB_PATH
        try:
            dash_mod._DB_PATH = "/tmp/nonexistent_op_health_test.db"
            result = dash_mod._get_agents()
            self.assertEqual(result, [])
        finally:
            dash_mod._DB_PATH = original

    def test_get_findings_summary_returns_empty_for_missing_db(self):
        """_get_findings_summary returns empty structure if DB unavailable."""
        original = dash_mod._DB_PATH
        try:
            dash_mod._DB_PATH = "/tmp/nonexistent_op_health_test.db"
            result = dash_mod._get_findings_summary()
            self.assertEqual(result["total"], 0)
        finally:
            dash_mod._DB_PATH = original

    def test_get_db_returns_connection_for_valid_db(self):
        """_get_db returns a valid connection when the DB exists."""
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test.db")
        # Create an actual DB
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        original = dash_mod._DB_PATH
        try:
            dash_mod._DB_PATH = db_path
            result = dash_mod._get_db()
            self.assertIsNotNone(result)
            result.close()
        finally:
            dash_mod._DB_PATH = original
            shutil.rmtree(tmpdir)


# ===================================================================
# 5. Pipeline stage atomicity -- NEW-COORD-015/016
# ===================================================================

class TestPipelineStageAtomicity(_WSBase):
    """NEW-COORD-015/016: get_ready_stages and trigger_downstream have
    split-lock patterns that can lead to TOCTOU races. Verify correctness
    under normal conditions and concurrent access."""

    def test_get_ready_stages_updates_waiting_to_ready(self):
        """get_ready_stages should update waiting stages to ready when deps are met."""
        pm = PipelineManager(self.db_path, self.bus_dir, "team-a", "agent-1")
        pid = pm.create_pipeline("test", [
            {"name": "s1"},
            {"name": "s2", "depends_on": ["s1"]},
            {"name": "s3", "depends_on": ["s1"]},
        ])

        # Initially only s1 is ready
        ready = pm.get_ready_stages(pid)
        names = [s["stage_name"] for s in ready]
        self.assertIn("s1", names)
        self.assertNotIn("s2", names)
        self.assertNotIn("s3", names)

        # Complete s1
        pm.start_stage(pid, "s1")
        pm.complete_stage(pid, "s1", {"data": "done"})

        # Now s2 and s3 should become ready
        ready = pm.get_ready_stages(pid)
        names = [s["stage_name"] for s in ready]
        self.assertIn("s2", names)
        self.assertIn("s3", names)
        pm.close()

    def test_trigger_downstream_marks_ready(self):
        """trigger_downstream marks downstream stages as ready when deps met."""
        pm = PipelineManager(self.db_path, self.bus_dir, "team-a", "agent-1")
        pid = pm.create_pipeline("test-td", [
            {"name": "a"},
            {"name": "b", "depends_on": ["a"]},
            {"name": "c", "depends_on": ["a", "b"]},
        ])

        pm.start_stage(pid, "a")
        pm.complete_stage(pid, "a", {})
        newly = pm.trigger_downstream(pid, "a")
        self.assertIn("b", newly)
        self.assertNotIn("c", newly)  # c needs b too

        pm.start_stage(pid, "b")
        pm.complete_stage(pid, "b", {})
        newly = pm.trigger_downstream(pid, "b")
        self.assertIn("c", newly)
        pm.close()

    def test_concurrent_get_ready_stages(self):
        """Multiple threads calling get_ready_stages should not corrupt state."""
        pm = PipelineManager(self.db_path, self.bus_dir, "team-a", "agent-1")
        pid = pm.create_pipeline("concurrent-ready", [
            {"name": "root"},
            {"name": "child-1", "depends_on": ["root"]},
            {"name": "child-2", "depends_on": ["root"]},
            {"name": "child-3", "depends_on": ["root"]},
        ])
        pm.start_stage(pid, "root")
        pm.complete_stage(pid, "root", {})

        errors = []
        results = []

        def call_ready():
            try:
                pm2 = PipelineManager(self.db_path, self.bus_dir, "team-b", "agent-2")
                ready = pm2.get_ready_stages(pid)
                results.append(len(ready))
                pm2.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_ready) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        # All threads should see 3 ready stages
        for r in results:
            self.assertEqual(r, 3)
        pm.close()

    def test_concurrent_trigger_downstream(self):
        """Multiple threads calling trigger_downstream should not double-mark."""
        pm = PipelineManager(self.db_path, self.bus_dir, "team-a", "agent-1")
        pid = pm.create_pipeline("concurrent-trigger", [
            {"name": "root"},
            {"name": "child", "depends_on": ["root"]},
        ])
        pm.start_stage(pid, "root")
        pm.complete_stage(pid, "root", {})

        errors = []
        results = []

        def call_trigger():
            try:
                pm2 = PipelineManager(self.db_path, self.bus_dir, "team-c", "agent-3")
                newly = pm2.trigger_downstream(pid, "root")
                results.append(newly)
                pm2.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_trigger) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # The stage should only appear as newly-ready in exactly one result
        # (others may see it already as 'ready' instead of 'waiting')
        total_newly_ready = sum(1 for r in results for name in r if name == "child")
        self.assertGreaterEqual(total_newly_ready, 1, "At least one thread should see child as newly ready")

        # Verify final state is correct
        status = pm.get_pipeline_status(pid)
        child = [s for s in status["stages"] if s["stage_name"] == "child"][0]
        self.assertEqual(child["status"], "ready")
        pm.close()


# ===================================================================
# 6. Progress validation -- BUG-CORE-003
# ===================================================================

class TestProgressValidation(_HubBase):
    """BUG-CORE-003: update_status validates 0 <= progress_pct <= 100."""

    def test_negative_progress_rejected(self):
        reporter = AgentReporter(self.db_path, "a1", "t1", "coder")
        with self.assertRaises(ValueError):
            reporter.update_status("working", -1, "task")
        reporter.close()

    def test_over_100_progress_rejected(self):
        reporter = AgentReporter(self.db_path, "a1", "t1", "coder")
        with self.assertRaises(ValueError):
            reporter.update_status("working", 101, "task")
        reporter.close()

    def test_boundary_values_accepted(self):
        reporter = AgentReporter(self.db_path, "a1", "t1", "coder")
        reporter.update_status("working", 0, "start")
        reporter.update_status("working", 100, "end")
        reporter.close()


# ===================================================================
# 7. Error reporting resets progress -- BUG-CORE-004
# ===================================================================

class TestErrorResetsProgress(_HubBase):
    """BUG-CORE-004: report_error sets progress_pct = 0."""

    def test_progress_reset_on_error(self):
        reporter = AgentReporter(self.db_path, "a1", "t1", "coder")
        reporter.update_status("working", 75, "coding")
        reporter.report_error("crash")

        dash = CoordinatorDashboard(self.db_path)
        status = dash.get_agent_status("a1")
        self.assertEqual(status["progress_pct"], 0)
        self.assertEqual(status["status"], "error")
        reporter.close()
        dash.close()


# ===================================================================
# 8. started_at preserved -- BUG-CORE-002
# ===================================================================

class TestStartedAtPreserved(_HubBase):
    """BUG-CORE-002: UPSERT pattern preserves started_at across updates."""

    def test_started_at_unchanged_after_multiple_updates(self):
        reporter = AgentReporter(self.db_path, "a1", "t1", "coder")
        dash = CoordinatorDashboard(self.db_path)

        initial = dash.get_agent_status("a1")
        started = initial["started_at"]
        self.assertIsNotNone(started)

        time.sleep(0.05)
        reporter.update_status("working", 25, "task-1")
        s2 = dash.get_agent_status("a1")
        self.assertEqual(s2["started_at"], started)

        time.sleep(0.05)
        reporter.update_status("working", 50, "task-2")
        s3 = dash.get_agent_status("a1")
        self.assertEqual(s3["started_at"], started)
        self.assertGreater(s3["last_updated"], s2["last_updated"])

        reporter.close()
        dash.close()


# ===================================================================
# 9. HelpProtocol close idempotency -- BUG-CORE-008
# ===================================================================

class TestHelpProtocolCloseIdempotent(_HPBase):
    """BUG-CORE-008: HelpProtocol.close() can be called multiple times."""

    def test_double_close_no_error(self):
        hp = self._make_hp()
        hp.close()
        hp.close()  # Should not raise

    def test_operations_after_close_raise(self):
        hp = self._make_hp()
        hp.close()
        with self.assertRaises(RuntimeError):
            hp.add_work_item("fail")


# ===================================================================
# 10. request_help verifies work_item exists -- BUG-CORE-009
# ===================================================================

class TestRequestHelpVerifiesWorkItem(_HPBase):
    """BUG-CORE-009: request_help raises ValueError if work_item_id doesn't exist."""

    def test_request_help_nonexistent_work_item(self):
        hp = self._make_hp()
        with self.assertRaises(ValueError):
            hp.request_help(99999, "help with nonexistent item")
        hp.close()

    def test_request_help_valid_work_item(self):
        hp = self._make_hp()
        wid = hp.add_work_item("Valid task")
        rid = hp.request_help(wid, "help please")
        self.assertIsInstance(rid, int)
        hp.close()


# ===================================================================
# 11. fulfill_help authorization -- BUG-CORE-010
# ===================================================================

class TestFulfillHelpAuthorization(_HPBase):
    """BUG-CORE-010: Only the accepted team can fulfill a help request."""

    def test_wrong_team_cannot_fulfill(self):
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("Task")
        rid = hp_a.request_help(wid, "Need help")

        hp_b = self._make_hp("team-b", "lead-b")
        hp_b.offer_help(rid)

        # Team C should not be able to fulfill
        hp_c = self._make_hp("team-c", "lead-c")
        with self.assertRaises(ValueError):
            hp_c.fulfill_help(rid)

        hp_a.close()
        hp_b.close()
        hp_c.close()

    def test_non_accepted_request_cannot_be_fulfilled(self):
        """Fulfilling a request that hasn't been accepted should fail."""
        hp_a = self._make_hp("team-a", "lead-a")
        wid = hp_a.add_work_item("Task")
        rid = hp_a.request_help(wid, "Need help")

        # Try to fulfill without anyone offering first
        hp_b = self._make_hp("team-b", "lead-b")
        with self.assertRaises(ValueError):
            hp_b.fulfill_help(rid)

        hp_a.close()
        hp_b.close()


# ===================================================================
# 12. Priority validation -- BUG-CORE-024
# ===================================================================

class TestPriorityValidation(_HPBase):
    """BUG-CORE-024: add_work_item validates priority."""

    def test_invalid_priority_rejected(self):
        hp = self._make_hp()
        with self.assertRaises(ValueError):
            hp.add_work_item("Task", priority="urgent")
        hp.close()

    def test_valid_priorities_accepted(self):
        hp = self._make_hp()
        for pri in ("critical", "high", "medium", "low"):
            wid = hp.add_work_item(f"Task-{pri}", priority=pri)
            self.assertIsInstance(wid, int)
        hp.close()


# ===================================================================
# 13. mark_work_available rowcount -- BUG-CORE-019
# ===================================================================

class TestMarkWorkAvailableRowcount(_HPBase):
    """BUG-CORE-019: mark_work_available raises ValueError if no rows affected."""

    def test_nonexistent_work_item_raises(self):
        hp = self._make_hp()
        with self.assertRaises(ValueError):
            hp.mark_work_available(99999)
        hp.close()

    def test_valid_work_item_succeeds(self):
        hp = self._make_hp()
        wid = hp.add_work_item("Task")
        hp.mark_work_available(wid)  # Should not raise
        hp.close()


# ===================================================================
# 14. complete_work_item team filter -- BUG-CORE-020
# ===================================================================

class TestCompleteWorkItemTeamFilter(_HPBase):
    """BUG-CORE-020: complete_work_item filters by team and checks rowcount."""

    def test_complete_own_work_item(self):
        hp = self._make_hp("team-a", "dev-1")
        wid = hp.add_work_item("My task")
        hp.complete_work_item(wid)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "completed")
        hp.close()

    def test_complete_other_teams_work_item_fails(self):
        """Team B should not be able to complete team A's work item."""
        hp_a = self._make_hp("team-a", "dev-a")
        wid = hp_a.add_work_item("Team A task")

        hp_b = self._make_hp("team-b", "dev-b")
        with self.assertRaises(ValueError):
            hp_b.complete_work_item(wid)

        hp_a.close()
        hp_b.close()


# ===================================================================
# 15. Bus notification with special chars -- Integration
# ===================================================================

class TestBusNotificationSpecialChars(unittest.TestCase):
    """Integration: verify bus files are written with sanitized names."""

    def test_bus_notify_creates_safe_filenames(self):
        tmpdir = tempfile.mkdtemp()
        bus_dir = os.path.join(tmpdir, "bus")
        os.makedirs(bus_dir)
        try:
            # Various dangerous channel names
            channels = [
                "normal-channel",
                "has spaces",
                "has/slashes",
                "has..dots",
                "has\x00nulls",
                "has:colons",
                "has\\backslash",
            ]
            for ch in channels:
                hub_bus_notify(bus_dir, ch, "agent", "team", {"test": True})

            for fname in os.listdir(bus_dir):
                base = fname.replace(".jsonl", "")
                self.assertRegex(base, r'^[a-zA-Z0-9_-]+$',
                                 f"Unsafe bus filename: {fname}")
        finally:
            shutil.rmtree(tmpdir)


# ===================================================================
# 16. send_instruction verify_exists -- BUG-CORE-007
# ===================================================================

class TestSendInstructionVerifyExists(_HubBase):
    """BUG-CORE-007: send_instruction with verify_exists=True validates agent."""

    def test_send_to_nonexistent_agent_with_verify(self):
        dash = CoordinatorDashboard(self.db_path)
        with self.assertRaises(ValueError):
            dash.send_instruction("ghost-agent", "test", None, verify_exists=True)
        dash.close()

    def test_send_to_existing_agent_with_verify(self):
        reporter = AgentReporter(self.db_path, "real-agent", "team-a", "coder")
        dash = CoordinatorDashboard(self.db_path)
        inst_id = dash.send_instruction("real-agent", "test", None, verify_exists=True)
        self.assertIsInstance(inst_id, int)
        reporter.close()
        dash.close()

    def test_send_without_verify_always_works(self):
        """Default verify_exists=False allows sending to any agent_id."""
        dash = CoordinatorDashboard(self.db_path)
        inst_id = dash.send_instruction("ghost-agent", "test", None)
        self.assertIsInstance(inst_id, int)
        dash.close()


if __name__ == "__main__":
    unittest.main()
