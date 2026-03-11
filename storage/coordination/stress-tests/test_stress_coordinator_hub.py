"""Stress tests for coordinator_hub module.

Pushes the module to its limits with high concurrency, rapid state changes,
stale agent detection under load, instruction races, and mixed operations.
"""

import functools
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coordinator_hub import (
    AgentReporter,
    CoordinatorDashboard,
    VALID_AGENT_STATUSES,
    _open_db,
)


# ---------------------------------------------------------------------------
# Retry decorator for SQLite resilience in test assertions
# ---------------------------------------------------------------------------

_MAX_TEST_RETRIES = 5
_TEST_RETRY_BACKOFF = 0.05


def _retry_on_busy(func):
    """Retry a test helper on sqlite3.OperationalError (SQLITE_BUSY)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        delay = _TEST_RETRY_BACKOFF
        last_err = None
        for attempt in range(_MAX_TEST_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    last_err = e
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class _StressBase(unittest.TestCase):
    """Common setup: temp directory with db and bus subdirs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="hub_stress_")
        self.db_path = os.path.join(self.tmpdir, "db", "hub.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        os.makedirs(os.path.join(self.tmpdir, "db"), exist_ok=True)
        os.makedirs(self.bus_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_reporter(self, agent_id, team="team-a", role="worker"):
        return AgentReporter(
            self.db_path, agent_id, team, role, bus_dir=self.bus_dir,
        )

    def _make_dashboard(self):
        return CoordinatorDashboard(self.db_path, bus_dir=self.bus_dir)


# ===================================================================
# 1. Mass Agent Registration
# ===================================================================


class TestMassRegistration(_StressBase):
    """20+ agents registering simultaneously via threading."""

    def test_25_agents_register_simultaneously(self):
        """25 agents all register at the same instant using a barrier."""
        num_agents = 25
        barrier = threading.Barrier(num_agents, timeout=30)
        errors = []
        reporters = [None] * num_agents

        def register(idx):
            try:
                barrier.wait()
                r = self._make_reporter(
                    f"agent-{idx}", team=f"team-{idx % 5}", role="worker",
                )
                reporters[idx] = r
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=register, args=(i,))
                   for i in range(num_agents)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Registration errors: {errors}")

        # Verify all agents visible from the dashboard
        dash = self._make_dashboard()
        all_status = dash.get_all_status()
        self.assertEqual(len(all_status), num_agents)

        agent_ids = {s["agent_id"] for s in all_status}
        for i in range(num_agents):
            self.assertIn(f"agent-{i}", agent_ids)

        # Cleanup
        for r in reporters:
            if r is not None:
                r.close()
        dash.close()


# ===================================================================
# 2. Concurrent Status Updates
# ===================================================================


class TestConcurrentStatusUpdates(_StressBase):
    """Many agents updating status concurrently."""

    def test_20_agents_update_status_simultaneously(self):
        """20 agents update their status at the same time via barrier."""
        num_agents = 20
        reporters = []
        for i in range(num_agents):
            reporters.append(self._make_reporter(f"agent-{i}", team="team-a"))

        barrier = threading.Barrier(num_agents, timeout=30)
        errors = []

        def update(idx):
            try:
                barrier.wait()
                reporters[idx].update_status(
                    "working", float(idx * 5), f"task-{idx}",
                    findings_count=idx,
                )
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=update, args=(i,))
                   for i in range(num_agents)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Update errors: {errors}")

        dash = self._make_dashboard()
        all_status = dash.get_all_status()
        self.assertEqual(len(all_status), num_agents)

        for s in all_status:
            self.assertEqual(s["status"], "working")

        for r in reporters:
            r.close()
        dash.close()

    def test_rapid_status_transitions_single_agent(self):
        """One agent cycles through all statuses rapidly 50 times."""
        reporter = self._make_reporter("rapid-agent", team="team-a")
        statuses_cycle = ["working", "blocked", "working", "blocked", "working"]

        for i in range(50):
            status = statuses_cycle[i % len(statuses_cycle)]
            reporter.update_status(status, float(i * 2), f"task-cycle-{i}")

        dash = self._make_dashboard()
        agent = dash.get_agent_status("rapid-agent")
        self.assertIsNotNone(agent)
        # Final status should be the last one written
        expected_status = statuses_cycle[49 % len(statuses_cycle)]
        self.assertEqual(agent["status"], expected_status)

        reporter.close()
        dash.close()


# ===================================================================
# 3. Stale Agent Detection Under Load
# ===================================================================


class TestStaleAgentDetection(_StressBase):
    """Stale agent detection while agents are actively updating."""

    def test_stale_detection_with_mixed_update_times(self):
        """Register agents, make some stale, verify detection is accurate."""
        num_agents = 20
        reporters = []
        for i in range(num_agents):
            reporters.append(self._make_reporter(f"agent-{i}", team="team-a"))

        # Update only even-numbered agents (odd ones become "stale")
        for i in range(0, num_agents, 2):
            reporters[i].update_status("working", 50.0, "active-task")

        # Manually backdate odd agents' last_updated to make them stale
        conn = _open_db(self.db_path)
        old_time = time.time() - 600  # 10 minutes ago
        for i in range(1, num_agents, 2):
            conn.execute(
                "UPDATE agent_status SET last_updated = ? WHERE agent_id = ?",
                (old_time, f"agent-{i}"),
            )
        conn.commit()
        conn.close()

        dash = self._make_dashboard()
        stale = dash.get_stale_agents(timeout_seconds=300)

        # Odd-numbered agents should be stale (they are still 'initializing',
        # which is active, and their last_updated is 10 min ago)
        stale_ids = {s["agent_id"] for s in stale}
        for i in range(1, num_agents, 2):
            self.assertIn(f"agent-{i}", stale_ids)
        for i in range(0, num_agents, 2):
            self.assertNotIn(f"agent-{i}", stale_ids)

        for r in reporters:
            r.close()
        dash.close()

    def test_stale_detection_excludes_completed_and_errored(self):
        """Complete and errored agents should never appear as stale."""
        r1 = self._make_reporter("agent-done", team="team-a")
        r2 = self._make_reporter("agent-err", team="team-a")
        r3 = self._make_reporter("agent-active", team="team-a")

        r1.report_complete()
        r2.report_error("something broke")

        # Backdate all agents
        conn = _open_db(self.db_path)
        old_time = time.time() - 1000
        conn.execute("UPDATE agent_status SET last_updated = ?", (old_time,))
        conn.commit()
        conn.close()

        dash = self._make_dashboard()
        stale = dash.get_stale_agents(timeout_seconds=60)
        stale_ids = {s["agent_id"] for s in stale}

        self.assertIn("agent-active", stale_ids)
        self.assertNotIn("agent-done", stale_ids)
        self.assertNotIn("agent-err", stale_ids)

        r1.close()
        r2.close()
        r3.close()
        dash.close()


# ===================================================================
# 4. Instructions While Agents Update
# ===================================================================


class TestInstructionsDuringUpdates(_StressBase):
    """Coordinator sends instructions while agents update status."""

    def test_send_instructions_while_agents_update(self):
        """Coordinator sends instructions concurrently with agent status updates."""
        num_agents = 15
        reporters = []
        for i in range(num_agents):
            reporters.append(self._make_reporter(f"agent-{i}", team="team-a"))

        barrier = threading.Barrier(num_agents + 1, timeout=30)
        errors = []
        instruction_ids = []
        instruction_lock = threading.Lock()

        def agent_work(idx):
            try:
                barrier.wait()
                for step in range(10):
                    reporters[idx].update_status(
                        "working", float(step * 10), f"step-{step}",
                    )
                    time.sleep(0.001)
            except Exception as e:
                errors.append(("agent", idx, e))

        def coordinator_work():
            try:
                dash = self._make_dashboard()
                barrier.wait()
                for i in range(num_agents):
                    inst_id = dash.send_instruction(
                        f"agent-{i}", "priority_change", '{"priority": "high"}',
                    )
                    with instruction_lock:
                        instruction_ids.append(inst_id)
                dash.close()
            except Exception as e:
                errors.append(("coordinator", e))

        threads = [threading.Thread(target=agent_work, args=(i,))
                   for i in range(num_agents)]
        threads.append(threading.Thread(target=coordinator_work))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Errors: {errors}")
        self.assertEqual(len(instruction_ids), num_agents)

        # Each agent should be able to read exactly one instruction
        for i in range(num_agents):
            instructions = reporters[i].check_instructions()
            self.assertEqual(len(instructions), 1)
            self.assertEqual(instructions[0]["instruction_type"], "priority_change")

        for r in reporters:
            r.close()


# ===================================================================
# 5. Instruction Read Races
# ===================================================================


class TestInstructionReadRaces(_StressBase):
    """Multiple reads of the same instruction -- only one should succeed."""

    def test_concurrent_instruction_reads(self):
        """Multiple threads read instructions for the same agent simultaneously."""
        reporter = self._make_reporter("agent-target", team="team-a")
        dash = self._make_dashboard()

        # Send several instructions
        num_instructions = 10
        for i in range(num_instructions):
            dash.send_instruction(
                "agent-target", "redirect", f'{{"step": {i}}}',
            )

        # Multiple threads try to read instructions at the same time
        # Each uses its own AgentReporter instance (same agent_id)
        num_readers = 5
        barrier = threading.Barrier(num_readers, timeout=15)
        all_results = [None] * num_readers
        errors = []

        def read_instructions(idx):
            try:
                r = self._make_reporter("agent-target", team="team-a")
                barrier.wait()
                result = r.check_instructions()
                all_results[idx] = result
                r.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=read_instructions, args=(i,))
                   for i in range(num_readers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"Read errors: {errors}")

        # Across all readers, total instructions read should equal num_instructions
        # (each instruction is read exactly once due to status='read' update)
        total_read = sum(len(r) for r in all_results if r is not None)
        self.assertEqual(total_read, num_instructions)

        reporter.close()
        dash.close()


# ===================================================================
# 6. Rapid Register/Deregister Cycles
# ===================================================================


class TestRegisterDeregisterCycles(_StressBase):
    """Rapidly creating and closing AgentReporter instances."""

    def test_rapid_register_close_50_cycles(self):
        """Register and close the same agent_id 50 times in sequence."""
        for i in range(50):
            r = self._make_reporter("recycled-agent", team="team-a")
            r.update_status("working", float(i), f"cycle-{i}")
            r.close()

        # Final state should reflect the last cycle
        dash = self._make_dashboard()
        agent = dash.get_agent_status("recycled-agent")
        # After close + re-register, status resets to 'initializing'
        self.assertEqual(agent["status"], "initializing")
        dash.close()

    def test_concurrent_register_deregister_different_agents(self):
        """20 threads each register and deregister their own agent rapidly."""
        num_threads = 20
        cycles_per_thread = 10
        barrier = threading.Barrier(num_threads, timeout=30)
        errors = []

        def cycle(idx):
            try:
                barrier.wait()
                for c in range(cycles_per_thread):
                    r = self._make_reporter(
                        f"agent-{idx}", team=f"team-{idx % 3}",
                    )
                    r.update_status("working", float(c * 10), f"task-{c}")
                    r.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=cycle, args=(i,))
                   for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Cycle errors: {errors}")

        # All agents should exist (last re-register persists)
        dash = self._make_dashboard()
        all_status = dash.get_all_status()
        self.assertEqual(len(all_status), num_threads)
        dash.close()


# ===================================================================
# 7. High-Frequency Heartbeat Simulation
# ===================================================================


class TestHighFrequencyHeartbeat(_StressBase):
    """Simulate 100+ rapid heartbeat (status update) calls."""

    def test_single_agent_150_heartbeats(self):
        """One agent sends 150 rapid status updates simulating heartbeats."""
        reporter = self._make_reporter("heartbeat-agent", team="team-hb")

        for i in range(150):
            pct = min(float(i), 100.0)
            reporter.update_status("working", pct, f"heartbeat-{i}")

        dash = self._make_dashboard()
        agent = dash.get_agent_status("heartbeat-agent")
        self.assertIsNotNone(agent)
        self.assertEqual(agent["status"], "working")
        self.assertEqual(agent["progress_pct"], 100.0)
        self.assertEqual(agent["current_task"], "heartbeat-149")

        reporter.close()
        dash.close()

    def test_10_agents_100_heartbeats_each_concurrent(self):
        """10 agents each send 100 heartbeats concurrently."""
        num_agents = 10
        heartbeats = 100
        reporters = []
        for i in range(num_agents):
            reporters.append(self._make_reporter(f"hb-agent-{i}", team="team-hb"))

        barrier = threading.Barrier(num_agents, timeout=30)
        errors = []

        def heartbeat_loop(idx):
            try:
                barrier.wait()
                for h in range(heartbeats):
                    pct = min(float(h), 100.0)
                    reporters[idx].update_status(
                        "working", pct, f"hb-{h}",
                        findings_count=h,
                    )
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=heartbeat_loop, args=(i,))
                   for i in range(num_agents)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [], f"Heartbeat errors: {errors}")

        dash = self._make_dashboard()
        summary = dash.get_summary()
        self.assertEqual(summary["total"], num_agents)
        self.assertEqual(summary["active"], num_agents)

        for r in reporters:
            r.close()
        dash.close()


# ===================================================================
# 8. Mixed Operations Under Load
# ===================================================================


class TestMixedOperations(_StressBase):
    """Some agents updating, some being queried, some receiving instructions."""

    def test_mixed_update_query_instruct(self):
        """Concurrent mix: agents update, dashboard queries, coordinator instructs."""
        num_agents = 15
        reporters = []
        for i in range(num_agents):
            reporters.append(self._make_reporter(
                f"agent-{i}", team=f"team-{i % 3}", role="worker",
            ))

        barrier = threading.Barrier(num_agents + 2, timeout=30)
        errors = []
        query_results = []
        query_lock = threading.Lock()

        def agent_updater(idx):
            """Agent updates status in a loop."""
            try:
                barrier.wait()
                for step in range(20):
                    reporters[idx].update_status(
                        "working", float(step * 5), f"task-{step}",
                        findings_count=step,
                    )
                    # Periodically check for instructions
                    if step % 5 == 0:
                        reporters[idx].check_instructions()
                    time.sleep(0.001)
                reporters[idx].report_complete(output_files=f"output-{idx}.json")
            except Exception as e:
                errors.append(("agent", idx, e))

        def dashboard_querier():
            """Repeatedly queries the dashboard for status."""
            try:
                dash = self._make_dashboard()
                barrier.wait()
                for _ in range(30):
                    all_s = dash.get_all_status()
                    active = dash.get_active_agents()
                    blocked = dash.get_blocked_agents()
                    summary = dash.get_summary()
                    with query_lock:
                        query_results.append({
                            "total": len(all_s),
                            "active": len(active),
                            "blocked": len(blocked),
                            "summary_total": summary["total"],
                        })
                    time.sleep(0.005)
                dash.close()
            except Exception as e:
                errors.append(("querier", e))

        def instruction_sender():
            """Coordinator sends instructions to agents."""
            try:
                dash = self._make_dashboard()
                barrier.wait()
                time.sleep(0.01)  # Let agents register first
                for i in range(num_agents):
                    dash.send_instruction(
                        f"agent-{i}", "pause", '{"duration": 1}',
                    )
                # Also test broadcast
                dash.broadcast_instruction("status_check", team="team-0")
                dash.close()
            except Exception as e:
                errors.append(("instructor", e))

        threads = [threading.Thread(target=agent_updater, args=(i,))
                   for i in range(num_agents)]
        threads.append(threading.Thread(target=dashboard_querier))
        threads.append(threading.Thread(target=instruction_sender))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [], f"Mixed operation errors: {errors}")

        # Should have query results
        self.assertGreater(len(query_results), 0)

        # All agents should eventually be complete
        dash = self._make_dashboard()
        completed = dash.get_completed_agents()
        self.assertEqual(len(completed), num_agents)

        for r in reporters:
            r.close()
        dash.close()


# ===================================================================
# 9. Broadcast and Team Filtering
# ===================================================================


class TestBroadcastStress(_StressBase):
    """Broadcast instructions to many agents under load."""

    def test_broadcast_to_30_agents_across_teams(self):
        """Register 30 agents in 3 teams, broadcast to one team."""
        reporters = []
        for i in range(30):
            team = f"team-{i % 3}"
            r = self._make_reporter(f"agent-{i}", team=team)
            r.update_status("working", 50.0, "busy")
            reporters.append(r)

        dash = self._make_dashboard()

        # Broadcast to team-0 only
        ids_team0 = dash.broadcast_instruction("pause", team="team-0")
        # team-0 has agents 0, 3, 6, 9, 12, 15, 18, 21, 24, 27 => 10 agents
        self.assertEqual(len(ids_team0), 10)

        # Broadcast to all
        ids_all = dash.broadcast_instruction("shutdown")
        self.assertEqual(len(ids_all), 30)

        # Agents in team-0 should have 2 instructions (pause + shutdown)
        for i in range(0, 30, 3):
            instructions = reporters[i].check_instructions()
            self.assertEqual(len(instructions), 2)

        # Agents in other teams should have 1 instruction (shutdown only)
        for i in range(1, 30, 3):
            instructions = reporters[i].check_instructions()
            self.assertEqual(len(instructions), 1)
            self.assertEqual(instructions[0]["instruction_type"], "shutdown")

        for r in reporters:
            r.close()
        dash.close()

    def test_summary_accuracy_under_mixed_states(self):
        """Verify get_summary with agents in every possible state."""
        r_init = self._make_reporter("agent-init", team="team-a")
        # agent-init stays initializing

        r_work = self._make_reporter("agent-work", team="team-a")
        r_work.update_status("working", 50.0, "coding")

        r_block = self._make_reporter("agent-block", team="team-b")
        r_block.update_status("blocked", 30.0, "waiting", blockers="need data")

        r_done = self._make_reporter("agent-done", team="team-b")
        r_done.report_complete("out.json")

        r_err = self._make_reporter("agent-err", team="team-c")
        r_err.report_error("crash")

        dash = self._make_dashboard()
        summary = dash.get_summary()

        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["active"], 3)   # init, working, blocked
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["complete"], 1)
        self.assertEqual(summary["error"], 1)

        # Team filtering
        team_a = dash.get_team_status("team-a")
        self.assertEqual(len(team_a), 2)
        team_b = dash.get_team_status("team-b")
        self.assertEqual(len(team_b), 2)

        for r in [r_init, r_work, r_block, r_done, r_err]:
            r.close()
        dash.close()


if __name__ == "__main__":
    unittest.main()
