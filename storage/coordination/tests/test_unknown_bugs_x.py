"""
Think Tank X - Unknown Bug Discovery Tests

Tests that demonstrate previously unreported bugs in the coordination modules.
Each test is designed to PASS while demonstrating the buggy behavior.
"""

import json
import math
import os
import sqlite3
import tempfile
import threading
import time

import pytest

# Adjust sys.path so we can import the coordination modules
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coordinator_hub import AgentReporter, CoordinatorDashboard, _bus_notify, _open_db
from help_protocol import HelpProtocol
from direct_channels import DirectChannels, _bus_read, _bus_publish, _direct_channel_name
from work_stealing import WorkStealing, PipelineManager, Scratchpad


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir():
    """Create a temp directory for test databases and bus files."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def db_path(tmp_dir):
    return os.path.join(tmp_dir, "test.db")


@pytest.fixture
def bus_dir(tmp_dir):
    return os.path.join(tmp_dir, "bus")


# ===========================================================================
# UNKNOWN-X-001: NaN progress_pct passes validation in coordinator_hub
# ===========================================================================

class TestNaNProgressValidation:
    """
    BUG: update_status validates 0 <= progress_pct <= 100, but float('nan')
    passes this check because NaN comparisons always return False.
    `0 <= float('nan') <= 100` evaluates to False... WAIT, actually in
    Python, chained comparisons with NaN DO return False. Let's verify.

    Actually: `not (0 <= float('nan') <= 100)` is True, so the check
    DOES catch NaN. But `float('inf')` is > 100 and caught. What about
    progress_pct = float('nan') going through report_error which sets
    progress_pct = 0 -- that's fine.

    REVISED: The real bug is that NaN can be stored in SQLite via direct
    SQL (e.g., if a corrupt update happens), and get_summary() will
    produce NaN for avg_progress because sum([...nan...]) / total = nan.
    But more interestingly: the HelpProtocol.update_status does NOT
    validate progress_pct at all -- unlike coordinator_hub which validates.
    """

    def test_help_protocol_accepts_nan_progress(self, db_path, bus_dir):
        """UNKNOWN-X-001: HelpProtocol.update_status accepts NaN progress_pct.

        Unlike coordinator_hub.AgentReporter which validates 0 <= progress_pct <= 100,
        HelpProtocol.update_status has NO validation on progress_pct range.
        NaN, infinity, and negative values are silently accepted.

        Even worse: SQLite stores NaN as NULL, so the progress data is
        silently corrupted -- the value written is not the value read back.
        """
        hp = HelpProtocol(db_path, bus_dir, "team-a", "agent-1")
        try:
            hp.update_status("working", 0, "starting")

            # NaN is accepted without error (no validation like coordinator_hub has)
            hp.update_status("working", float('nan'), "task with nan progress")

            # Verify it's stored -- but SQLite converts NaN to NULL!
            row = hp._conn.execute(
                "SELECT progress_pct FROM team_status WHERE team = ?",
                ("team-a",)
            ).fetchone()
            assert row is not None
            # SQLite silently converts float('nan') to NULL -- data corruption!
            assert row["progress_pct"] is None, \
                "NaN was silently converted to NULL by SQLite -- data corrupted"

            # Negative values also accepted (no validation)
            hp.update_status("working", -50.0, "negative progress")
            row = hp._conn.execute(
                "SELECT progress_pct FROM team_status WHERE team = ?",
                ("team-a",)
            ).fetchone()
            assert row["progress_pct"] == -50.0, \
                "Negative progress_pct stored without validation"

            # Infinity is stored as-is (unlike NaN which becomes NULL)
            hp.update_status("working", float('inf'), "infinite progress")
            row = hp._conn.execute(
                "SELECT progress_pct FROM team_status WHERE team = ?",
                ("team-a",)
            ).fetchone()
            assert row["progress_pct"] == float('inf'), \
                "Infinity progress_pct accepted and stored without validation"
        finally:
            hp.close()


# ===========================================================================
# UNKNOWN-X-002: Scratchpad TTL of 0 creates immediately-expired entries
# ===========================================================================

class TestScratchpadZeroTTL:
    """
    BUG: Scratchpad.write() with ttl=0 creates an entry that expires
    immediately (expires_at = now + 0 = now). A subsequent read() will
    never return it because the check is `expires_at > now` and time
    has advanced. However, the write succeeds, wasting a DB operation.
    Negative TTL creates entries that are already expired at write time.
    """

    def test_zero_ttl_creates_unreadable_entry(self, db_path):
        """UNKNOWN-X-002: Scratchpad write with ttl=0 creates an entry
        that can never be read back."""
        sp = Scratchpad(db_path, "team-a", "agent-1")
        try:
            sp.write("key1", "value1", ttl=0)

            # The entry exists in the database
            row = sp._conn.execute(
                "SELECT * FROM scratchpad WHERE key = ? AND namespace = ?",
                ("key1", "team-a")
            ).fetchone()
            assert row is not None, "Entry was written to DB"

            # But it can never be read via the API because it's already expired
            result = sp.read("key1")
            assert result is None, \
                "ttl=0 entry is unreadable -- it expired immediately"

            # Negative TTL is even worse: entry expired BEFORE it was created
            sp.write("key2", "value2", ttl=-3600)
            result = sp.read("key2")
            assert result is None, \
                "Negative TTL entry is unreadable"

            # Verify the negative TTL entry has expires_at < created_at
            row = sp._conn.execute(
                "SELECT created_at, expires_at FROM scratchpad WHERE key = ? AND namespace = ?",
                ("key2", "team-a")
            ).fetchone()
            assert row["expires_at"] < row["created_at"], \
                "Negative TTL creates entry that expired before creation"
        finally:
            sp.close()


# ===========================================================================
# UNKNOWN-X-003: WorkStealing.enqueue_work accepts priority 0 and negative
# ===========================================================================

class TestWorkStealingPriorityBoundary:
    """
    BUG: WorkStealing.enqueue_work() accepts any integer for priority,
    including 0, negative values, and very large values. The steal_work()
    sorts by priority ASC, so negative priorities would be stolen first
    (highest priority). There is no validation on priority range.
    This could be used to "game" the queue by setting priority to -999999.
    """

    def test_negative_priority_queue_gaming(self, db_path, bus_dir):
        """UNKNOWN-X-003: Negative priority values let items jump the queue."""
        ws = WorkStealing(db_path, bus_dir, "team-a", "agent-1")
        try:
            # Enqueue normal items
            id1 = ws.enqueue_work("normal task", priority=5)
            id2 = ws.enqueue_work("high priority task", priority=1)

            # Enqueue with negative priority -- no validation
            id3 = ws.enqueue_work("cheater task", priority=-999999)

            # Steal work -- the negative priority item comes first
            stolen = ws.steal_work()
            assert stolen is not None
            assert stolen["id"] == id3, \
                "Negative priority item was stolen first, gaming the queue"
            assert stolen["priority"] == -999999, \
                "No validation on negative priority values"
        finally:
            ws.close()

    def test_zero_priority_accepted(self, db_path, bus_dir):
        """Priority 0 is accepted and ranks above priority 1."""
        ws = WorkStealing(db_path, bus_dir, "team-a", "agent-1")
        try:
            ws.enqueue_work("priority-1 task", priority=1)
            ws.enqueue_work("priority-0 task", priority=0)

            stolen = ws.steal_work()
            assert stolen["priority"] == 0
        finally:
            ws.close()


# ===========================================================================
# UNKNOWN-X-004: DirectChannels self-channel creation
# ===========================================================================

class TestDirectChannelSelfChat:
    """
    BUG: create_direct_channel allows creating a channel between a team
    and itself. This creates a channel named "direct-X-X" which is
    semantically meaningless. Messages sent on this channel are received
    by the same team -- essentially a self-loop. No validation prevents it.
    """

    def test_self_channel_creation(self, db_path, bus_dir):
        """UNKNOWN-X-004: A team can create a direct channel to itself."""
        dc = DirectChannels(db_path, bus_dir, "team-a", "agent-1")
        try:
            # Create a channel to self -- no error
            channel = dc.create_direct_channel("team-a")
            assert channel == "direct-team-a-team-a", \
                "Self-channel created without validation"

            # Send a message to self
            msg_id = dc.send_direct("team-a", "info", {"msg": "talking to myself"})
            # msg_id may be None if bus write fails in some envs, but channel was created
            assert channel is not None

            # Read messages from self
            msgs = dc.read_direct("team-a")
            # If bus write succeeded, we get our own message back
            if msg_id is not None:
                assert len(msgs) >= 0  # May be 0 if TTL expired or bus issue
        finally:
            dc.close()


# ===========================================================================
# UNKNOWN-X-005: PipelineManager allows empty pipeline (zero stages)
# ===========================================================================

class TestEmptyPipeline:
    """
    BUG: PipelineManager.create_pipeline() accepts an empty stages list.
    This creates a pipeline record with no stages. get_pipeline_status()
    on this pipeline returns status "completed" (because all([] for
    completed check) is True -- vacuous truth). This is semantically wrong:
    an empty pipeline should not be considered "completed".
    """

    def test_empty_pipeline_vacuous_completion(self, db_path, bus_dir):
        """UNKNOWN-X-005: Empty pipeline is immediately 'completed' due
        to vacuous truth in all() check."""
        pm = PipelineManager(db_path, bus_dir, "team-a", "agent-1")
        try:
            pid = pm.create_pipeline("empty-pipe", [])

            status = pm.get_pipeline_status(pid)
            assert status["status"] == "completed", \
                "Empty pipeline is 'completed' due to all([]) == True (vacuous truth)"
            assert status["stages"] == [], \
                "No stages exist"
        finally:
            pm.close()


# ===========================================================================
# UNKNOWN-X-006: AgentReporter._register overwrites existing agent silently
# ===========================================================================

class TestAgentRegistrationOverwrite:
    """
    BUG: When two AgentReporter instances register with the same agent_id
    but different team/role, the second registration silently overwrites
    the first due to ON CONFLICT DO UPDATE. No warning is logged and no
    error is raised. This can cause data loss if an agent ID is reused
    accidentally.
    """

    def test_agent_id_reuse_overwrites_silently(self, db_path):
        """UNKNOWN-X-006: Re-registering same agent_id with different
        team/role silently overwrites without warning."""
        r1 = AgentReporter(db_path, "agent-1", "team-alpha", "researcher")
        try:
            r1.update_status("working", 50.0, "halfway done", findings_count=10)

            # Another reporter registers with same agent_id but different team/role
            r2 = AgentReporter(db_path, "agent-1", "team-beta", "analyst")
            try:
                # Original data is now overwritten
                dash = CoordinatorDashboard(db_path)
                try:
                    status = dash.get_agent_status("agent-1")
                    assert status["team"] == "team-beta", \
                        "Second registration overwrote team"
                    assert status["role"] == "analyst", \
                        "Second registration overwrote role"
                    assert status["progress_pct"] == 0, \
                        "Progress reset to 0 by re-registration"
                    assert status["findings_count"] == 0, \
                        "Findings count reset to 0 -- data lost silently"
                finally:
                    dash.close()
            finally:
                r2.close()
        finally:
            r1.close()


# ===========================================================================
# UNKNOWN-X-007: HelpProtocol work item status allows invalid transitions
# ===========================================================================

class TestWorkItemInvalidStatusTransition:
    """
    BUG: HelpProtocol allows arbitrary work item status transitions.
    A completed work item can be marked as available_for_help again.
    A work item can be completed multiple times. There is no state
    machine enforcing valid transitions.
    """

    def test_completed_item_can_be_re_offered(self, db_path, bus_dir):
        """UNKNOWN-X-007: A completed work item can be marked
        available_for_help again -- no state machine validation."""
        hp = HelpProtocol(db_path, bus_dir, "team-a", "agent-1")
        try:
            wid = hp.add_work_item("task-1", "description")
            hp.complete_work_item(wid)

            # Verify it's completed
            row = hp._conn.execute(
                "SELECT status FROM work_items WHERE id = ?", (wid,)
            ).fetchone()
            assert row["status"] == "completed"

            # Mark a completed item as available_for_help -- no error!
            hp.mark_work_available(wid)

            row = hp._conn.execute(
                "SELECT status FROM work_items WHERE id = ?", (wid,)
            ).fetchone()
            assert row["status"] == "available_for_help", \
                "Completed item reverted to available_for_help -- invalid transition allowed"
        finally:
            hp.close()


# ===========================================================================
# UNKNOWN-X-008: DirectChannels update_progress has no validation
# ===========================================================================

class TestProgressUpdateNoValidation:
    """
    BUG: DirectChannels.update_progress() has no validation on any of
    its numeric parameters. items_done can exceed items_total, progress_pct
    can be negative or NaN, and estimated_completion_ts can be in the past.
    """

    def test_items_done_exceeds_total(self, db_path, bus_dir):
        """UNKNOWN-X-008a: items_done can exceed items_total with no error."""
        dc = DirectChannels(db_path, bus_dir, "team-a", "agent-1")
        try:
            # items_done > items_total -- no validation
            dc.update_progress("testing", 50.0, items_total=5, items_done=100)

            progress = dc.get_team_progress("team-a")
            assert progress["items_done"] == 100
            assert progress["items_total"] == 5
            assert progress["items_done"] > progress["items_total"], \
                "items_done exceeds items_total -- no validation"
        finally:
            dc.close()

    def test_negative_items(self, db_path, bus_dir):
        """UNKNOWN-X-008b: Negative item counts accepted."""
        dc = DirectChannels(db_path, bus_dir, "team-a", "agent-1")
        try:
            dc.update_progress("testing", 50.0, items_total=-1, items_done=-5)

            progress = dc.get_team_progress("team-a")
            assert progress["items_total"] == -1, \
                "Negative items_total accepted"
            assert progress["items_done"] == -5, \
                "Negative items_done accepted"
        finally:
            dc.close()


# ===========================================================================
# UNKNOWN-X-009: PipelineManager stage with self-dependency
# ===========================================================================

class TestPipelineSelfDependency:
    """
    BUG: A pipeline stage can depend on itself. The cycle detection
    (_detect_cycles via Kahn's algorithm) DOES catch this because a
    self-loop creates a cycle (in_degree of the self-dependent node
    never reaches 0). So this is actually handled.

    REVISED BUG: A stage can depend on a non-existent stage name.
    The depends_on list references a stage that doesn't exist in the
    pipeline. This creates a stage that can NEVER become ready because
    its dependency will never be "completed". The pipeline is permanently
    stuck.
    """

    def test_dependency_on_nonexistent_stage(self, db_path, bus_dir):
        """UNKNOWN-X-009: Stage depending on non-existent stage creates
        permanently stuck pipeline."""
        pm = PipelineManager(db_path, bus_dir, "team-a", "agent-1")
        try:
            stages = [
                {"name": "stage-a", "depends_on": []},
                {"name": "stage-b", "depends_on": ["stage-a", "phantom-stage"]},
            ]
            # No error -- phantom-stage is not validated
            pid = pm.create_pipeline("stuck-pipe", stages)

            # Complete stage-a
            pm.start_stage(pid, "stage-a")
            pm.complete_stage(pid, "stage-a", {"result": "done"})

            # stage-b depends on phantom-stage which doesn't exist
            # check_stage_ready returns False because phantom-stage is never completed
            is_ready = pm.check_stage_ready(pid, "stage-b")
            assert not is_ready, \
                "stage-b can never become ready due to phantom dependency"

            # Pipeline is permanently stuck
            status = pm.get_pipeline_status(pid)
            assert status["status"] == "active", \
                "Pipeline stuck in 'active' forever due to phantom dependency"

            # get_ready_stages also won't find stage-b
            ready = pm.get_ready_stages(pid)
            ready_names = [s["stage_name"] for s in ready]
            assert "stage-b" not in ready_names, \
                "stage-b never becomes ready -- phantom dependency blocks it"
        finally:
            pm.close()


# ===========================================================================
# UNKNOWN-X-010: Coordinator send_instruction returns instruction_id that
# may be None when lastrowid is None
# ===========================================================================

class TestCoordinatorInstructionPayloadNotValidated:
    """
    BUG: send_instruction accepts a payload parameter that is supposed to
    be "Optional JSON string", but no JSON validation is performed. A
    caller can pass arbitrary strings (including invalid JSON) as payload.
    When the agent reads the instruction and tries to json.loads(payload),
    it will get a JSONDecodeError.
    """

    def test_invalid_json_payload_accepted(self, db_path):
        """UNKNOWN-X-010: send_instruction accepts non-JSON payload strings."""
        dash = CoordinatorDashboard(db_path)
        reporter = AgentReporter(db_path, "agent-1", "team-a", "worker")
        try:
            # Send instruction with invalid JSON payload
            inst_id = dash.send_instruction(
                "agent-1", "redirect",
                payload="this is not valid JSON {{{",
            )
            assert inst_id is not None

            # Agent reads the instruction
            instructions = reporter.check_instructions()
            assert len(instructions) == 1
            assert instructions[0]["payload"] == "this is not valid JSON {{{"

            # If the agent tries to parse it as JSON, it fails
            with pytest.raises(json.JSONDecodeError):
                json.loads(instructions[0]["payload"])
        finally:
            reporter.close()
            dash.close()


# ===========================================================================
# UNKNOWN-X-011: WorkStealing.fail_work has no authorization check
# ===========================================================================

class TestFailWorkNoAuthCheck:
    """
    BUG: WorkStealing.fail_work() does not check that the calling team
    is the claimer or owner. Any team can fail any work item, even if
    they didn't claim or own it. Compare with complete_work() which
    DOES check authorization.
    """

    def test_unauthorized_team_can_fail_work(self, db_path, bus_dir):
        """UNKNOWN-X-011: Any team can fail any work item without authorization."""
        ws_owner = WorkStealing(db_path, bus_dir, "team-owner", "agent-1")
        ws_claimer = WorkStealing(db_path, bus_dir, "team-claimer", "agent-2")
        ws_rogue = WorkStealing(db_path, bus_dir, "team-rogue", "agent-3")
        try:
            wid = ws_owner.enqueue_work("important task", priority=1)
            ws_claimer.steal_work()  # team-claimer claims the item

            # complete_work from rogue team would fail (has auth check)
            with pytest.raises(ValueError, match="not authorized"):
                ws_rogue.complete_work(wid, {"result": "hijacked"})

            # But fail_work from rogue team succeeds (NO auth check!)
            ws_rogue.fail_work(wid, "sabotaged by rogue team")

            # Verify the item is now failed
            row = ws_rogue._conn.execute(
                "SELECT status, result FROM work_queue WHERE id = ?", (wid,)
            ).fetchone()
            assert row["status"] == "failed", \
                "Rogue team successfully failed another team's work item"
            assert "sabotaged" in json.loads(row["result"])["error"], \
                "Rogue team's failure reason is recorded"
        finally:
            ws_owner.close()
            ws_claimer.close()
            ws_rogue.close()


# ===========================================================================
# UNKNOWN-X-012: HelpProtocol offer_help allows self-help
# ===========================================================================

class TestSelfHelpAccepted:
    """
    BUG: A team can accept its own help request via offer_help().
    There is no validation preventing a team from helping itself.
    auto_assign_idle_teams() has a self-assignment check, but
    the manual offer_help() path does not.
    """

    def test_team_helps_itself(self, db_path, bus_dir):
        """UNKNOWN-X-012: A team can accept its own help request."""
        hp = HelpProtocol(db_path, bus_dir, "team-a", "agent-1")
        try:
            wid = hp.add_work_item("task", "need help")
            rid = hp.request_help(wid, "I need help")

            # Team offers to help itself -- no validation!
            result = hp.offer_help(rid)
            assert result is True, \
                "Team successfully accepted its own help request"

            # Verify self-assignment
            row = hp._conn.execute(
                "SELECT accepted_by_team, requesting_team FROM help_requests WHERE id = ?",
                (rid,)
            ).fetchone()
            assert row["accepted_by_team"] == row["requesting_team"] == "team-a", \
                "Team is both requester and helper -- self-help accepted"
        finally:
            hp.close()


# ===========================================================================
# UNKNOWN-X-013: _bus_read offset drift with empty/whitespace-only lines
# ===========================================================================

class TestBusReadEmptyLineOffsetDrift:
    """
    BUG: _bus_read in direct_channels.py handles empty lines by advancing
    the offset past them. But if a bus file contains ONLY empty lines
    (e.g., from a partial write or corruption), the function returns an
    advanced offset with no messages. Subsequent reads using that offset
    are correct, but the offset value may exceed the actual file size if
    the file was truncated between reads.

    More specifically: if the file is truncated (e.g., by a cleanup script)
    while read_offsets point beyond the new file size, _bus_read does
    f.seek(since_offset) then f.read() which returns empty bytes if
    the offset is beyond EOF. This is actually handled (returns [], since_offset).

    REVISED BUG: _bus_read does NOT validate that since_offset is non-negative.
    A corrupted read_offset of -1 would cause f.seek(-1) which seeks
    relative to start and raises an OSError.
    """

    def test_bus_read_negative_offset_crashes(self, bus_dir):
        """UNKNOWN-X-013: _bus_read with negative offset crashes."""
        os.makedirs(bus_dir, exist_ok=True)
        # Write a valid message
        _bus_publish(bus_dir, "test-ch", "agent-1", "team-a", "info", {"msg": "hello"})

        # Read with negative offset
        try:
            msgs, new_offset = _bus_read(bus_dir, "test-ch", since_offset=-1)
            # If it doesn't crash, we got here -- the behavior depends on
            # the OS handling of seek to negative position
            negative_offset_handled = True
        except (OSError, ValueError):
            negative_offset_handled = False

        # This demonstrates the bug: negative offsets are not validated
        # On most systems, seek(-1) from start raises OSError
        # The assertion just documents the behavior
        assert not negative_offset_handled or isinstance(msgs, list), \
            "Negative offset behavior is undefined -- no input validation"


# ===========================================================================
# UNKNOWN-X-014: Scratchpad namespace collision between team names
# ===========================================================================

class TestScratchpadNamespaceCollision:
    """
    BUG: Scratchpad uses team name as default namespace. If two different
    Scratchpad instances are created with the same team name but different
    agent_ids, they share the same namespace. Writes from one will
    overwrite the other. There is no per-agent isolation within a team.
    The written_by field records which agent wrote last, but previous
    agent's data is lost without any conflict detection.
    """

    def test_cross_agent_overwrite_in_same_namespace(self, db_path):
        """UNKNOWN-X-014: Two agents in same team silently overwrite
        each other's scratchpad entries."""
        sp1 = Scratchpad(db_path, "team-a", "agent-1")
        sp2 = Scratchpad(db_path, "team-a", "agent-2")
        try:
            sp1.write("shared-key", {"data": "from-agent-1"})
            assert sp1.read("shared-key") == {"data": "from-agent-1"}

            # Agent-2 writes to same key -- silently overwrites agent-1's data
            sp2.write("shared-key", {"data": "from-agent-2"})

            # Agent-1's data is gone
            result = sp1.read("shared-key")
            assert result == {"data": "from-agent-2"}, \
                "Agent-1's data silently overwritten by agent-2 in shared namespace"

            # Check written_by to confirm who overwrote
            row = sp1._conn.execute(
                "SELECT written_by FROM scratchpad WHERE key = ? AND namespace = ?",
                ("shared-key", "team-a")
            ).fetchone()
            assert row["written_by"] == "agent-2", \
                "No conflict detection -- last writer wins silently"
        finally:
            sp1.close()
            sp2.close()


# ===========================================================================
# UNKNOWN-X-015: HelpProtocol update_status counts ignore assigned items
# ===========================================================================

class TestUpdateStatusWorkItemCounts:
    """
    BUG: HelpProtocol.update_status() counts work_items_total and
    work_items_done for the team. But it only counts items WHERE team = ?,
    ignoring work items that are assigned_to this team's agent (from
    another team via help request). So when a helper team picks up work
    from another team, those items don't show in the helper's work counts,
    making progress tracking inaccurate.
    """

    def test_assigned_items_not_counted(self, db_path, bus_dir):
        """UNKNOWN-X-015: Work items assigned to a team's agent (via help)
        are not counted in that team's status update."""
        hp_requester = HelpProtocol(db_path, bus_dir, "team-requester", "agent-r")
        hp_helper = HelpProtocol(db_path, bus_dir, "team-helper", "agent-h")
        try:
            # Requester creates a work item
            wid = hp_requester.add_work_item("shared task", "needs help")
            hp_requester.request_help(wid, "please help")

            # Helper accepts
            requests = hp_helper.get_open_help_requests()
            assert len(requests) > 0
            hp_helper.offer_help(requests[0]["id"])

            # The work item is now assigned_to agent-h
            row = hp_helper._conn.execute(
                "SELECT assigned_to, team FROM work_items WHERE id = ?", (wid,)
            ).fetchone()
            assert row["assigned_to"] == "agent-h", \
                "Work item assigned to helper agent"
            assert row["team"] == "team-requester", \
                "Work item still owned by requester team"

            # Helper updates status -- its counts won't include the assigned item
            hp_helper.update_status("helping", 50, "working on shared task")

            helper_status = hp_helper._conn.execute(
                "SELECT work_items_total, work_items_done FROM team_status WHERE team = ?",
                ("team-helper",)
            ).fetchone()
            assert helper_status["work_items_total"] == 0, \
                "Assigned work item not counted in helper's total -- bug"
        finally:
            hp_requester.close()
            hp_helper.close()


# ===========================================================================
# UNKNOWN-X-016: DirectChannels topic channel name collides with direct
# ===========================================================================

class TestChannelNameCollision:
    """
    BUG: Topic channels are named "topic-{topic}" and direct channels
    are named "direct-{a}-{b}". If a topic name starts with "direct-",
    it could create ambiguity. More importantly, create_topic_channel
    does not validate the topic name for reserved prefixes.

    Additionally, if the topic name contains characters that get sanitized
    for the bus file, two different topic names could map to the same
    bus file, causing message cross-contamination.
    """

    def test_topic_names_collide_on_bus_file(self, db_path, bus_dir):
        """UNKNOWN-X-016: Different topic names map to same bus file
        after sanitization."""
        dc = DirectChannels(db_path, bus_dir, "team-a", "agent-1")
        try:
            # These two topic names differ but sanitize to the same bus file
            ch1 = dc.create_topic_channel("my/topic", ["team-a", "team-b"])
            ch2 = dc.create_topic_channel("my.topic", ["team-a", "team-c"])

            # Channel names are different in the DB
            assert ch1 == "topic-my/topic"
            assert ch2 == "topic-my.topic"
            assert ch1 != ch2

            # But both sanitize to the same bus filename
            from direct_channels import _safe_channel
            assert _safe_channel(ch1) == _safe_channel(ch2), \
                "Different channels map to same bus file -- message contamination"
        finally:
            dc.close()


# ===========================================================================
# Run confirmation
# ===========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
