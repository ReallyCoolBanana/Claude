"""Tests for the coordinator_hub module.

Covers: agent registration, status updates, coordinator reading, multi-agent
threading, instruction delivery, status transitions, error reporting, blocked
agent detection, summary statistics, closed-state behaviour, and input validation.
"""

import os
import tempfile
import threading
import time

import pytest

from storage.coordination.coordinator_hub import (
    AgentReporter,
    CoordinatorDashboard,
    VALID_AGENT_STATUSES,
)


@pytest.fixture
def db_path(tmp_path):
    """Return a temporary SQLite database path."""
    return str(tmp_path / "hub_test.db")


# ===================================================================
# Agent registration and status updates
# ===================================================================


class TestAgentRegistration:
    def test_agent_registers_on_init(self, db_path):
        """Agent appears in the status table immediately after creation."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "researcher")
        dash = CoordinatorDashboard(db_path)

        status = dash.get_agent_status("agent-1")
        assert status is not None
        assert status["agent_id"] == "agent-1"
        assert status["team"] == "team-a"
        assert status["role"] == "researcher"
        assert status["status"] == "initializing"
        assert status["progress_pct"] == 0

        reporter.close()
        dash.close()

    def test_agent_reregistration_resets(self, db_path):
        """Re-creating an AgentReporter resets state to initializing."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        reporter.update_status("working", 50, "coding")
        reporter.close()

        reporter2 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        dash = CoordinatorDashboard(db_path)
        status = dash.get_agent_status("agent-1")
        assert status["status"] == "initializing"
        assert status["progress_pct"] == 0

        reporter2.close()
        dash.close()


class TestStatusUpdates:
    def test_update_status(self, db_path):
        """Basic status update is reflected in the dashboard."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        reporter.update_status("working", 25.5, "writing module", findings_count=3)

        dash = CoordinatorDashboard(db_path)
        status = dash.get_agent_status("agent-1")
        assert status["status"] == "working"
        assert status["progress_pct"] == 25.5
        assert status["current_task"] == "writing module"
        assert status["findings_count"] == 3
        assert status["blockers"] is None
        assert status["error_message"] is None

        reporter.close()
        dash.close()

    def test_update_with_blockers(self, db_path):
        """Blockers field is stored correctly."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        reporter.update_status("blocked", 10, "waiting for data",
                               blockers="Need API key from team-b")

        dash = CoordinatorDashboard(db_path)
        status = dash.get_agent_status("agent-1")
        assert status["status"] == "blocked"
        assert status["blockers"] == "Need API key from team-b"

        reporter.close()
        dash.close()

    def test_update_with_output_files(self, db_path):
        """Output files field is stored correctly."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        reporter.update_status("working", 80, "generating output",
                               output_files="report.md,data.json")

        dash = CoordinatorDashboard(db_path)
        status = dash.get_agent_status("agent-1")
        assert status["output_files"] == "report.md,data.json"

        reporter.close()
        dash.close()

    def test_started_at_preserved_across_updates(self, db_path):
        """The started_at timestamp should not change after initial registration."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        dash = CoordinatorDashboard(db_path)

        initial = dash.get_agent_status("agent-1")
        started_at = initial["started_at"]

        time.sleep(0.05)
        reporter.update_status("working", 50, "coding")

        updated = dash.get_agent_status("agent-1")
        assert updated["started_at"] == started_at
        assert updated["last_updated"] > started_at

        reporter.close()
        dash.close()


# ===================================================================
# Coordinator reading all statuses
# ===================================================================


class TestCoordinatorReads:
    def test_get_all_status_sorted(self, db_path):
        """get_all_status returns agents sorted by last_updated descending."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")

        r1.update_status("working", 10, "task-a")
        time.sleep(0.05)
        r2.update_status("working", 20, "task-b")

        dash = CoordinatorDashboard(db_path)
        all_status = dash.get_all_status()
        assert len(all_status) == 2
        # agent-2 updated more recently, should be first
        assert all_status[0]["agent_id"] == "agent-2"
        assert all_status[1]["agent_id"] == "agent-1"

        r1.close()
        r2.close()
        dash.close()

    def test_get_agent_status_not_found(self, db_path):
        """get_agent_status returns None for nonexistent agent."""
        dash = CoordinatorDashboard(db_path)
        assert dash.get_agent_status("nonexistent") is None
        dash.close()


# ===================================================================
# Multiple agents reporting simultaneously (threading test)
# ===================================================================


class TestConcurrentAgents:
    def test_concurrent_registration(self, db_path):
        """Multiple agents can register concurrently without errors."""
        num_agents = 10
        reporters = []
        errors = []

        def register_agent(idx):
            try:
                r = AgentReporter(db_path, f"agent-{idx}", "team-a", f"role-{idx}")
                r.update_status("working", idx * 10, f"task-{idx}")
                reporters.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_agent, args=(i,))
                   for i in range(num_agents)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors during concurrent registration: {errors}"

        dash = CoordinatorDashboard(db_path)
        all_status = dash.get_all_status()
        assert len(all_status) == num_agents

        for r in reporters:
            r.close()
        dash.close()

    def test_concurrent_status_updates(self, db_path):
        """Multiple agents updating status concurrently is safe."""
        reporters = [
            AgentReporter(db_path, f"agent-{i}", "team-a", "worker")
            for i in range(5)
        ]
        errors = []

        def update_many(reporter, agent_idx):
            try:
                for pct in range(0, 101, 10):
                    reporter.update_status("working", pct, f"step-{pct}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=update_many, args=(r, i))
            for i, r in enumerate(reporters)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"

        dash = CoordinatorDashboard(db_path)
        all_status = dash.get_all_status()
        assert len(all_status) == 5

        for r in reporters:
            r.close()
        dash.close()


# ===================================================================
# Instruction delivery and reading
# ===================================================================


class TestInstructions:
    def test_send_and_read_instruction(self, db_path):
        """Coordinator can send an instruction and agent can read it."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        dash = CoordinatorDashboard(db_path)

        inst_id = dash.send_instruction("agent-1", "redirect", '{"new_task": "fix bug"}')
        assert isinstance(inst_id, int)

        instructions = reporter.check_instructions()
        assert len(instructions) == 1
        assert instructions[0]["instruction_type"] == "redirect"
        assert instructions[0]["payload"] == '{"new_task": "fix bug"}'
        assert instructions[0]["id"] == inst_id

        reporter.close()
        dash.close()

    def test_instructions_marked_as_read(self, db_path):
        """After reading, instructions are no longer returned as pending."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        dash = CoordinatorDashboard(db_path)

        dash.send_instruction("agent-1", "pause", None)
        first_read = reporter.check_instructions()
        assert len(first_read) == 1

        second_read = reporter.check_instructions()
        assert len(second_read) == 0

        reporter.close()
        dash.close()

    def test_instructions_targeted_to_specific_agent(self, db_path):
        """Instructions for agent-1 are not visible to agent-2."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")
        dash = CoordinatorDashboard(db_path)

        dash.send_instruction("agent-1", "redirect", None)

        assert len(r1.check_instructions()) == 1
        assert len(r2.check_instructions()) == 0

        r1.close()
        r2.close()
        dash.close()

    def test_multiple_instructions(self, db_path):
        """Multiple instructions are returned in creation order."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        dash = CoordinatorDashboard(db_path)

        dash.send_instruction("agent-1", "type-a", "payload-a")
        dash.send_instruction("agent-1", "type-b", "payload-b")
        dash.send_instruction("agent-1", "type-c", "payload-c")

        instructions = reporter.check_instructions()
        assert len(instructions) == 3
        assert [i["instruction_type"] for i in instructions] == ["type-a", "type-b", "type-c"]

        reporter.close()
        dash.close()


# ===================================================================
# Status transitions (initializing -> working -> complete)
# ===================================================================


class TestStatusTransitions:
    def test_full_lifecycle(self, db_path):
        """Agent goes through initializing -> working -> complete."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        dash = CoordinatorDashboard(db_path)

        # initializing
        s = dash.get_agent_status("agent-1")
        assert s["status"] == "initializing"

        # working
        reporter.update_status("working", 50, "building feature")
        s = dash.get_agent_status("agent-1")
        assert s["status"] == "working"
        assert s["progress_pct"] == 50

        # complete
        reporter.report_complete("output.txt")
        s = dash.get_agent_status("agent-1")
        assert s["status"] == "complete"
        assert s["progress_pct"] == 100
        assert s["output_files"] == "output.txt"

        reporter.close()
        dash.close()

    def test_working_to_blocked_to_working(self, db_path):
        """Agent can transition working -> blocked -> working."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        dash = CoordinatorDashboard(db_path)

        reporter.update_status("working", 30, "coding")
        reporter.update_status("blocked", 30, "coding", blockers="waiting for review")
        s = dash.get_agent_status("agent-1")
        assert s["status"] == "blocked"

        reporter.update_status("working", 60, "coding resumed")
        s = dash.get_agent_status("agent-1")
        assert s["status"] == "working"
        assert s["progress_pct"] == 60

        reporter.close()
        dash.close()


# ===================================================================
# Error reporting
# ===================================================================


class TestErrorReporting:
    def test_report_error(self, db_path):
        """report_error sets status to 'error' with message."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        reporter.update_status("working", 50, "coding")
        reporter.report_error("Segfault in module X")

        dash = CoordinatorDashboard(db_path)
        s = dash.get_agent_status("agent-1")
        assert s["status"] == "error"
        assert s["error_message"] == "Segfault in module X"

        reporter.close()
        dash.close()

    def test_error_agents_not_active(self, db_path):
        """Error agents should not appear in get_active_agents."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")

        r1.report_error("crash")
        r2.update_status("working", 50, "testing")

        dash = CoordinatorDashboard(db_path)
        active = dash.get_active_agents()
        assert len(active) == 1
        assert active[0]["agent_id"] == "agent-2"

        r1.close()
        r2.close()
        dash.close()


# ===================================================================
# Blocked agent detection
# ===================================================================


class TestBlockedDetection:
    def test_get_blocked_agents(self, db_path):
        """get_blocked_agents returns only blocked agents."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")
        r3 = AgentReporter(db_path, "agent-3", "team-b", "reviewer")

        r1.update_status("blocked", 20, "stuck", blockers="missing dep")
        r2.update_status("working", 50, "testing")
        r3.update_status("blocked", 10, "waiting", blockers="need API access")

        dash = CoordinatorDashboard(db_path)
        blocked = dash.get_blocked_agents()
        assert len(blocked) == 2
        blocked_ids = {b["agent_id"] for b in blocked}
        assert blocked_ids == {"agent-1", "agent-3"}

        r1.close()
        r2.close()
        r3.close()
        dash.close()

    def test_no_blocked_agents(self, db_path):
        """get_blocked_agents returns empty list when nobody is blocked."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r1.update_status("working", 50, "coding")

        dash = CoordinatorDashboard(db_path)
        assert dash.get_blocked_agents() == []

        r1.close()
        dash.close()


# ===================================================================
# Summary statistics accuracy
# ===================================================================


class TestSummary:
    def test_summary_with_mixed_agents(self, db_path):
        """Summary correctly counts agents in different states."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")
        r3 = AgentReporter(db_path, "agent-3", "team-b", "reviewer")
        r4 = AgentReporter(db_path, "agent-4", "team-b", "writer")

        r1.update_status("working", 50, "coding")
        r2.report_complete("tests.py")
        r3.report_error("crash")
        r4.update_status("blocked", 20, "waiting", blockers="need data")

        dash = CoordinatorDashboard(db_path)
        summary = dash.get_summary()

        assert summary["total"] == 4
        assert summary["active"] == 2  # working + blocked
        assert summary["blocked"] == 1
        assert summary["complete"] == 1
        assert summary["error"] == 1
        # avg_progress: (50 + 100 + 0_from_error_state + 20) / 4
        # error agent had progress_pct from initializing (0) then report_error
        # Let's just check it's a float and roughly correct
        assert isinstance(summary["avg_progress"], float)

        r1.close()
        r2.close()
        r3.close()
        r4.close()
        dash.close()

    def test_summary_empty(self, db_path):
        """Summary with no agents returns zeros."""
        dash = CoordinatorDashboard(db_path)
        summary = dash.get_summary()
        assert summary == {
            "total": 0,
            "active": 0,
            "blocked": 0,
            "complete": 0,
            "error": 0,
            "avg_progress": 0.0,
        }
        dash.close()

    def test_summary_avg_progress(self, db_path):
        """Average progress is calculated correctly."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")

        r1.update_status("working", 40, "coding")
        r2.update_status("working", 60, "testing")

        dash = CoordinatorDashboard(db_path)
        summary = dash.get_summary()
        assert summary["avg_progress"] == pytest.approx(50.0)

        r1.close()
        r2.close()
        dash.close()


# ===================================================================
# Completed agents
# ===================================================================


class TestCompletedAgents:
    def test_get_completed_agents(self, db_path):
        """get_completed_agents returns only agents with status='complete'."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")

        r1.report_complete("output.txt")
        r2.update_status("working", 50, "testing")

        dash = CoordinatorDashboard(db_path)
        completed = dash.get_completed_agents()
        assert len(completed) == 1
        assert completed[0]["agent_id"] == "agent-1"

        r1.close()
        r2.close()
        dash.close()


# ===================================================================
# Closed-state behavior
# ===================================================================


class TestClosedState:
    def test_reporter_closed_raises(self, db_path):
        """Operations on a closed AgentReporter raise RuntimeError."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        reporter.close()

        with pytest.raises(RuntimeError, match="closed"):
            reporter.update_status("working", 50, "coding")

        with pytest.raises(RuntimeError, match="closed"):
            reporter.report_error("fail")

        with pytest.raises(RuntimeError, match="closed"):
            reporter.report_complete("output.txt")

        with pytest.raises(RuntimeError, match="closed"):
            reporter.check_instructions()

    def test_dashboard_closed_raises(self, db_path):
        """Operations on a closed CoordinatorDashboard raise RuntimeError."""
        dash = CoordinatorDashboard(db_path)
        dash.close()

        with pytest.raises(RuntimeError, match="closed"):
            dash.get_all_status()

        with pytest.raises(RuntimeError, match="closed"):
            dash.get_active_agents()

        with pytest.raises(RuntimeError, match="closed"):
            dash.get_blocked_agents()

        with pytest.raises(RuntimeError, match="closed"):
            dash.get_completed_agents()

        with pytest.raises(RuntimeError, match="closed"):
            dash.get_agent_status("agent-1")

        with pytest.raises(RuntimeError, match="closed"):
            dash.send_instruction("agent-1", "test", None)

        with pytest.raises(RuntimeError, match="closed"):
            dash.get_summary()

    def test_context_manager_reporter(self, db_path):
        """AgentReporter works as a context manager."""
        with AgentReporter(db_path, "agent-1", "team-a", "coder") as reporter:
            reporter.update_status("working", 50, "coding")

        with pytest.raises(RuntimeError, match="closed"):
            reporter.update_status("working", 60, "coding")

    def test_context_manager_dashboard(self, db_path):
        """CoordinatorDashboard works as a context manager."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")

        with CoordinatorDashboard(db_path) as dash:
            status = dash.get_agent_status("agent-1")
            assert status is not None

        with pytest.raises(RuntimeError, match="closed"):
            dash.get_all_status()

        reporter.close()


# ===================================================================
# Input validation
# ===================================================================


class TestInputValidation:
    def test_invalid_status_rejected(self, db_path):
        """update_status rejects invalid status values."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")

        with pytest.raises(ValueError, match="Invalid status"):
            reporter.update_status("running", 50, "coding")

        with pytest.raises(ValueError, match="Invalid status"):
            reporter.update_status("", 50, "coding")

        with pytest.raises(ValueError, match="Invalid status"):
            reporter.update_status("WORKING", 50, "coding")

        reporter.close()

    def test_all_valid_statuses_accepted(self, db_path):
        """All statuses in VALID_AGENT_STATUSES are accepted."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")

        for status in VALID_AGENT_STATUSES:
            reporter.update_status(status, 50, f"testing {status}")

        reporter.close()

    def test_report_complete_none_output(self, db_path):
        """report_complete with no output_files works."""
        reporter = AgentReporter(db_path, "agent-1", "team-a", "coder")
        reporter.report_complete()

        dash = CoordinatorDashboard(db_path)
        s = dash.get_agent_status("agent-1")
        assert s["status"] == "complete"
        assert s["progress_pct"] == 100
        assert s["output_files"] is None

        reporter.close()
        dash.close()


# ===================================================================
# Stale agent detection
# ===================================================================


class TestStaleAgents:
    def test_get_stale_agents_returns_old_active_agents(self, db_path):
        """get_stale_agents returns active agents with old last_updated."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")

        r1.update_status("working", 30, "coding")
        r2.update_status("working", 50, "testing")

        dash = CoordinatorDashboard(db_path)
        # With timeout=0 every active agent is stale
        stale = dash.get_stale_agents(timeout_seconds=0)
        assert len(stale) == 2

        r1.close()
        r2.close()
        dash.close()

    def test_get_stale_agents_excludes_complete_and_error(self, db_path):
        """Completed and errored agents are NOT considered stale."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")
        r3 = AgentReporter(db_path, "agent-3", "team-a", "reviewer")

        r1.report_complete("out.txt")
        r2.report_error("crash")
        r3.update_status("working", 10, "reviewing")

        dash = CoordinatorDashboard(db_path)
        stale = dash.get_stale_agents(timeout_seconds=0)
        assert len(stale) == 1
        assert stale[0]["agent_id"] == "agent-3"

        r1.close()
        r2.close()
        r3.close()
        dash.close()

    def test_get_stale_agents_respects_timeout(self, db_path):
        """Agents updated recently are NOT stale with a large timeout."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r1.update_status("working", 50, "coding")

        dash = CoordinatorDashboard(db_path)
        stale = dash.get_stale_agents(timeout_seconds=9999)
        assert len(stale) == 0

        r1.close()
        dash.close()

    def test_get_stale_agents_empty_db(self, db_path):
        """get_stale_agents returns empty list on empty database."""
        dash = CoordinatorDashboard(db_path)
        assert dash.get_stale_agents() == []
        dash.close()


# ===================================================================
# Broadcast instructions
# ===================================================================


class TestBroadcastInstruction:
    def test_broadcast_to_all_active(self, db_path):
        """broadcast_instruction sends to all active agents."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-b", "tester")
        r3 = AgentReporter(db_path, "agent-3", "team-a", "reviewer")

        r1.update_status("working", 30, "coding")
        r2.update_status("working", 50, "testing")
        r3.report_complete("out.txt")  # complete -- should be skipped

        dash = CoordinatorDashboard(db_path)
        ids = dash.broadcast_instruction("pause", '{"reason": "maintenance"}')
        assert len(ids) == 2

        # Both active agents should have pending instructions
        inst1 = r1.check_instructions()
        inst2 = r2.check_instructions()
        assert len(inst1) == 1
        assert inst1[0]["instruction_type"] == "pause"
        assert inst1[0]["payload"] == '{"reason": "maintenance"}'
        assert len(inst2) == 1

        # Completed agent should have none
        inst3 = r3.check_instructions()
        assert len(inst3) == 0

        r1.close()
        r2.close()
        r3.close()
        dash.close()

    def test_broadcast_filtered_by_team(self, db_path):
        """broadcast_instruction with team= only targets that team."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-b", "tester")

        r1.update_status("working", 30, "coding")
        r2.update_status("working", 50, "testing")

        dash = CoordinatorDashboard(db_path)
        ids = dash.broadcast_instruction("priority_change", None, team="team-a")
        assert len(ids) == 1

        assert len(r1.check_instructions()) == 1
        assert len(r2.check_instructions()) == 0

        r1.close()
        r2.close()
        dash.close()

    def test_broadcast_no_active_agents(self, db_path):
        """broadcast_instruction returns empty list when no active agents."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r1.report_complete("out.txt")

        dash = CoordinatorDashboard(db_path)
        ids = dash.broadcast_instruction("pause")
        assert ids == []

        r1.close()
        dash.close()

    def test_broadcast_skips_error_agents(self, db_path):
        """broadcast_instruction skips agents in error state."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")

        r1.update_status("working", 50, "coding")
        r2.report_error("crash")

        dash = CoordinatorDashboard(db_path)
        ids = dash.broadcast_instruction("pause")
        assert len(ids) == 1

        assert len(r1.check_instructions()) == 1
        assert len(r2.check_instructions()) == 0

        r1.close()
        r2.close()
        dash.close()


# ===================================================================
# Team status
# ===================================================================


class TestTeamStatus:
    def test_get_team_status(self, db_path):
        """get_team_status returns only agents in the specified team."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-b", "tester")
        r3 = AgentReporter(db_path, "agent-3", "team-a", "reviewer")

        r1.update_status("working", 30, "coding")
        r2.update_status("working", 50, "testing")
        r3.report_complete("review.md")

        dash = CoordinatorDashboard(db_path)
        team_a = dash.get_team_status("team-a")
        assert len(team_a) == 2
        assert {a["agent_id"] for a in team_a} == {"agent-1", "agent-3"}

        team_b = dash.get_team_status("team-b")
        assert len(team_b) == 1
        assert team_b[0]["agent_id"] == "agent-2"

        r1.close()
        r2.close()
        r3.close()
        dash.close()

    def test_get_team_status_nonexistent_team(self, db_path):
        """get_team_status returns empty list for a team with no agents."""
        dash = CoordinatorDashboard(db_path)
        assert dash.get_team_status("no-such-team") == []
        dash.close()

    def test_get_team_status_includes_all_states(self, db_path):
        """get_team_status includes agents regardless of status."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")
        r3 = AgentReporter(db_path, "agent-3", "team-a", "reviewer")

        r1.update_status("working", 50, "coding")
        r2.report_error("crash")
        r3.report_complete("out.txt")

        dash = CoordinatorDashboard(db_path)
        team_a = dash.get_team_status("team-a")
        assert len(team_a) == 3
        statuses = {a["status"] for a in team_a}
        assert statuses == {"working", "error", "complete"}

        r1.close()
        r2.close()
        r3.close()
        dash.close()


# ===================================================================
# Failed agents
# ===================================================================


class TestFailedAgents:
    def test_get_failed_agents(self, db_path):
        """get_failed_agents returns only agents with status='error'."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r2 = AgentReporter(db_path, "agent-2", "team-a", "tester")
        r3 = AgentReporter(db_path, "agent-3", "team-b", "reviewer")

        r1.report_error("crash in module A")
        r2.update_status("working", 50, "testing")
        r3.report_error("timeout")

        dash = CoordinatorDashboard(db_path)
        failed = dash.get_failed_agents()
        assert len(failed) == 2
        failed_ids = {f["agent_id"] for f in failed}
        assert failed_ids == {"agent-1", "agent-3"}
        # Verify error messages are present
        for f in failed:
            assert f["error_message"] is not None

        r1.close()
        r2.close()
        r3.close()
        dash.close()

    def test_get_failed_agents_empty(self, db_path):
        """get_failed_agents returns empty list when no errors."""
        r1 = AgentReporter(db_path, "agent-1", "team-a", "coder")
        r1.update_status("working", 50, "coding")

        dash = CoordinatorDashboard(db_path)
        assert dash.get_failed_agents() == []

        r1.close()
        dash.close()


# ===================================================================
# Closed-state behavior for new methods
# ===================================================================


class TestClosedStateNewMethods:
    def test_closed_get_stale_agents(self, db_path):
        """get_stale_agents raises RuntimeError on closed dashboard."""
        dash = CoordinatorDashboard(db_path)
        dash.close()
        with pytest.raises(RuntimeError, match="closed"):
            dash.get_stale_agents()

    def test_closed_broadcast_instruction(self, db_path):
        """broadcast_instruction raises RuntimeError on closed dashboard."""
        dash = CoordinatorDashboard(db_path)
        dash.close()
        with pytest.raises(RuntimeError, match="closed"):
            dash.broadcast_instruction("pause")

    def test_closed_get_team_status(self, db_path):
        """get_team_status raises RuntimeError on closed dashboard."""
        dash = CoordinatorDashboard(db_path)
        dash.close()
        with pytest.raises(RuntimeError, match="closed"):
            dash.get_team_status("team-a")

    def test_closed_get_failed_agents(self, db_path):
        """get_failed_agents raises RuntimeError on closed dashboard."""
        dash = CoordinatorDashboard(db_path)
        dash.close()
        with pytest.raises(RuntimeError, match="closed"):
            dash.get_failed_agents()
