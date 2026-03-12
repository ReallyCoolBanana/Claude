"""Multi-process integration tests using the sandbox framework.

These tests spawn real OS processes (not threads) to validate Proto A
coordination under true process isolation — the critical gap identified
by TEAM-0020, TEAM-0021, and TEAM-0022.

Usage:
    python -m pytest prototype/sandbox/test_sandbox.py -v --tb=short
"""

from __future__ import annotations

import os
import sys
import time
from multiprocessing import Queue

import pytest

# Ensure prototype is importable.
_PROTO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROTO_ROOT not in sys.path:
    sys.path.insert(0, _PROTO_ROOT)

from sandbox import AgentSpec, Sandbox


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sb():
    """Provide a fresh sandbox, cleaned up after the test."""
    sandbox = Sandbox(timeout=30)
    yield sandbox
    sandbox.stop_all()
    sandbox.cleanup()


# ===================================================================
# 1. Basic lifecycle: coordinator + workers in separate processes
# ===================================================================

class TestBasicLifecycle:
    """Verify agents start, communicate, and exit cleanly in real processes."""

    def test_single_team_lifecycle(self, sb):
        """One coordinator + 2 workers complete a full phase cycle."""
        sb.add_team("alpha", num_workers=2, coordinator_config={
            "phases": ["research", "done"],
            "phase_interval": 0.5,
            "heartbeat_interval": 1.0,
            "hold_final_phase": 1.0,
        }, worker_config={
            "run_duration": 3.0,
            "send_interval": 0.3,
        })

        sb.start_all()
        results = sb.collect_results(timeout=20)

        assert results.all_succeeded, (
            f"Agents failed: {[(a.agent_id, a.exit_code, a.errors) for a in results.error_agents]}"
        )
        assert len(results.agents) == 3
        assert results.total_messages > 0

        # Coordinator should have advanced through phases.
        coord_result = next(a for a in results.agents if a.role == "coordinator")
        assert "research" in coord_result.data.get("phases_advanced", [])

    def test_multi_team_lifecycle(self, sb):
        """Two independent teams run concurrently."""
        for team_name in ["alpha", "beta"]:
            sb.add_team(team_name, num_workers=1, coordinator_config={
                "phases": ["work", "done"],
                "phase_interval": 0.5,
                "heartbeat_interval": 1.0,
                "hold_final_phase": 1.0,
            }, worker_config={
                "run_duration": 2.5,
                "send_interval": 0.5,
            })

        sb.start_all()
        results = sb.collect_results(timeout=20)

        assert results.all_succeeded
        assert len(results.agents) == 4  # 2 coordinators + 2 workers

        teams = {a.team for a in results.agents}
        assert teams == {"alpha", "beta"}


# ===================================================================
# 2. Cross-process message passing
# ===================================================================

class TestCrossProcessMessaging:
    """Verify messages written in one process are readable in another."""

    def test_messages_cross_process_boundary(self, sb):
        """Worker messages must be readable by other workers."""
        sb.add_team("msg-test", num_workers=3, coordinator_config={
            "phases": ["work"],
            "phase_interval": 0.5,
            "hold_final_phase": 4.0,
            "heartbeat_interval": 1.0,
        }, worker_config={
            "run_duration": 3.0,
            "send_interval": 0.2,
            "channel": "team-channel",
        })

        sb.start_all()
        results = sb.collect_results(timeout=20)

        assert results.all_succeeded

        # Each worker should have received messages from other workers.
        workers = [a for a in results.agents if a.role == "worker"]
        for w in workers:
            assert w.messages_sent > 0, f"{w.agent_id} sent no messages"
            assert w.messages_received > 0, (
                f"{w.agent_id} received no messages — cross-process bus may be broken"
            )

    def test_bus_file_integrity(self, sb):
        """All bus messages must be valid JSON after multi-process writes."""
        sb.add_team("integrity", num_workers=2, coordinator_config={
            "phases": ["done"],
            "phase_interval": 0.3,
            "hold_final_phase": 1.0,
            "heartbeat_interval": 1.0,
        }, worker_config={
            "run_duration": 2.0,
            "send_interval": 0.1,
        })

        sb.start_all()
        results = sb.collect_results(timeout=15)

        # Read all bus files and verify every line is valid JSON.
        bus_dir = os.path.join(sb.comm_dir, "bus")
        corrupt_count = 0
        total_lines = 0
        for fname in os.listdir(bus_dir):
            if not fname.endswith(".jsonl"):
                continue
            with open(os.path.join(bus_dir, fname), "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total_lines += 1
                    try:
                        import json
                        data = json.loads(line)
                        assert "id" in data and "type" in data
                    except (json.JSONDecodeError, AssertionError):
                        corrupt_count += 1

        assert total_lines > 0, "No bus messages written"
        assert corrupt_count == 0, (
            f"BUG: {corrupt_count}/{total_lines} corrupt messages after multi-process writes"
        )


# ===================================================================
# 3. Shared SQLite state across processes
# ===================================================================

class TestSharedSQLiteState:
    """Verify SQLite WAL handles concurrent multi-process access."""

    def test_all_agents_registered(self, sb):
        """Every spawned process must appear in the agents table."""
        sb.add_team("db-test", num_workers=3, coordinator_config={
            "phases": ["done"],
            "phase_interval": 0.3,
            "hold_final_phase": 2.0,
            "heartbeat_interval": 1.0,
        }, worker_config={
            "run_duration": 2.0,
            "send_interval": 0.5,
        })

        sb.start_all()
        results = sb.collect_results(timeout=15)

        agents = sb.query_db("SELECT agent_id, team, role, pid FROM agents")
        agent_ids = {a["agent_id"] for a in agents}

        # Workers should be registered; the coordinator registers as "coordinator"
        # internally (Proto A Coordinator class hardcodes agent_id="coordinator").
        expected_workers = {"db-test-worker-1", "db-test-worker-2", "db-test-worker-3"}
        assert "coordinator" in agent_ids, (
            f"Coordinator not found in DB. Registered: {agent_ids}"
        )
        for eid in expected_workers:
            assert eid in agent_ids, (
                f"Agent {eid} not found in DB. Registered: {agent_ids}"
            )

        # Workers should have unique PIDs (real process isolation).
        worker_pids = [a["pid"] for a in agents if a["agent_id"].startswith("db-test-worker")]
        assert len(worker_pids) == len(set(worker_pids)), (
            f"BUG: Workers share PIDs — not truly separate processes! PIDs: {worker_pids}"
        )

    def test_phase_signals_persisted(self, sb):
        """Phase transitions from coordinator must be in the DB."""
        sb.add_team("phase-db", num_workers=1, coordinator_config={
            "phases": ["alpha", "beta", "gamma"],
            "phase_interval": 0.3,
            "hold_final_phase": 1.0,
            "heartbeat_interval": 1.0,
        }, worker_config={
            "run_duration": 3.0,
        })

        sb.start_all()
        results = sb.collect_results(timeout=15)

        signals = sb.query_db("SELECT phase, signal_type FROM phase_signals ORDER BY ts")
        phases = [s["phase"] for s in signals]

        for expected in ["alpha", "beta", "gamma"]:
            assert expected in phases, (
                f"Phase signal '{expected}' missing from DB. Found: {phases}"
            )


# ===================================================================
# 4. Custom agent behaviours
# ===================================================================

def _rate_limit_tester(
    comm_dir: str,
    agent_id: str,
    team: str,
    role: str,
    config: dict,
    result_queue: Queue,
) -> None:
    """Custom behaviour: attempt API reservations and report success/failure counts."""
    sys.path.insert(0, _PROTO_ROOT)
    from agent_comm.state import SharedState

    db_path = os.path.join(comm_dir, "db", "state.db")
    state = SharedState(db_path)
    endpoint = config.get("endpoint", "api.test/rate")
    attempts = config.get("attempts", 20)

    successes = 0
    failures = 0
    errors = []

    # Configure rate limit (only one agent should do this).
    if config.get("configure_limit"):
        state.configure_rate_limit(
            endpoint,
            max_calls=config.get("max_calls", 5),
            window_seconds=config.get("window_seconds", 60),
        )
        time.sleep(0.2)  # Let config propagate.

    for i in range(attempts):
        try:
            if state.reserve_api_call(endpoint, agent_id):
                successes += 1
            else:
                failures += 1
        except Exception as e:
            errors.append(f"attempt {i}: {type(e).__name__}: {e}")
        time.sleep(0.01)

    state.close()
    result_queue.put((agent_id, {
        "messages_sent": 0,
        "messages_received": 0,
        "errors": errors,
        "data": {"successes": successes, "failures": failures},
    }))


class TestCustomBehaviours:
    """Test scenarios using custom agent behaviour functions."""

    def test_rate_limiting_across_processes(self, sb):
        """Rate limits enforced correctly across multiple real processes."""
        max_calls = 5

        # One agent configures the limit.
        sb.add_agent(AgentSpec(
            agent_id="limiter",
            team="rate-test",
            role="worker",
            behaviour=_rate_limit_tester,
            config={
                "configure_limit": True,
                "max_calls": max_calls,
                "window_seconds": 60,
                "attempts": 10,
                "endpoint": "api.test/shared",
            },
        ))

        # Three more agents compete for the same endpoint.
        for i in range(3):
            sb.add_agent(AgentSpec(
                agent_id=f"contender-{i}",
                team="rate-test",
                role="worker",
                behaviour=_rate_limit_tester,
                config={
                    "configure_limit": False,
                    "attempts": 10,
                    "endpoint": "api.test/shared",
                },
            ))

        sb.start_all()
        results = sb.collect_results(timeout=20)

        assert results.all_succeeded

        total_successes = sum(
            a.data.get("successes", 0) for a in results.agents
        )

        # With 4 processes × 10 attempts each and a limit of 5,
        # total successes should be exactly 5.
        assert total_successes == max_calls, (
            f"BUG: Rate limit violated across processes! "
            f"Expected {max_calls} total successes, got {total_successes}. "
            f"Per-agent: {[(a.agent_id, a.data) for a in results.agents]}"
        )


# ===================================================================
# 5. Stress: many processes writing concurrently
# ===================================================================

class TestMultiProcessStress:
    """Stress tests with many concurrent real processes."""

    def test_10_workers_concurrent_writes(self, sb):
        """10 worker processes writing simultaneously must not corrupt the bus."""
        sb.add_agent(AgentSpec(
            agent_id="stress-coord",
            team="stress",
            role="coordinator",
            config={
                "phases": ["go"],
                "phase_interval": 0.2,
                "hold_final_phase": 5.0,
                "heartbeat_interval": 1.0,
            },
        ))

        for i in range(10):
            sb.add_agent(AgentSpec(
                agent_id=f"stress-w-{i}",
                team="stress",
                role="worker",
                config={
                    "run_duration": 3.0,
                    "send_interval": 0.05,
                    "channel": "stress-channel",
                },
            ))

        sb.start_all()
        results = sb.collect_results(timeout=30)

        assert results.all_succeeded

        # Verify bus file integrity.
        msgs = sb.read_bus("stress-channel")
        assert len(msgs) > 0, "No messages on stress channel"

        # Every message should have valid structure.
        for m in msgs:
            assert "id" in m and "type" in m and "body" in m

        # Check unique IDs.
        ids = [m["id"] for m in msgs]
        assert len(ids) == len(set(ids)), (
            f"BUG: Duplicate message IDs! {len(ids)} total, {len(set(ids))} unique"
        )

        total_sent = sum(a.messages_sent for a in results.agents if a.role == "worker")
        assert total_sent > 50, f"Expected >50 messages sent, got {total_sent}"

    def test_process_isolation_verified(self, sb):
        """Each agent must run in a distinct OS process."""
        sb.add_team("iso-check", num_workers=4, coordinator_config={
            "phases": ["done"],
            "phase_interval": 0.2,
            "hold_final_phase": 1.0,
            "heartbeat_interval": 1.0,
        }, worker_config={
            "run_duration": 1.5,
        })

        sb.start_all()
        results = sb.collect_results(timeout=15)

        pids = [a.pid for a in results.agents]
        # All 5 agents (1 coord + 4 workers) should have distinct PIDs.
        assert len(pids) == len(set(pids)), (
            f"BUG: Agents sharing PIDs — not real process isolation! PIDs: {pids}"
        )


# ===================================================================
# 6. Sandbox inspection APIs
# ===================================================================

class TestSandboxInspection:
    """Verify the sandbox provides useful post-hoc inspection."""

    def test_read_bus_returns_messages(self, sb):
        sb.add_team("inspect", num_workers=1, coordinator_config={
            "phases": ["done"],
            "phase_interval": 0.3,
            "hold_final_phase": 1.0,
            "heartbeat_interval": 1.0,
        }, worker_config={
            "run_duration": 1.5,
            "send_interval": 0.2,
            "channel": "inspect-ch",
        })

        sb.start_all()
        sb.collect_results(timeout=15)

        msgs = sb.read_bus("inspect-ch")
        assert len(msgs) > 0, "read_bus returned no messages"
        assert all("id" in m for m in msgs)

    def test_query_db_returns_agents(self, sb):
        sb.add_team("query", num_workers=1, coordinator_config={
            "phases": ["done"],
            "phase_interval": 0.3,
            "hold_final_phase": 1.0,
            "heartbeat_interval": 1.0,
        }, worker_config={
            "run_duration": 1.5,
        })

        sb.start_all()
        sb.collect_results(timeout=15)

        rows = sb.query_db("SELECT COUNT(*) as cnt FROM agents")
        assert rows[0]["cnt"] >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
