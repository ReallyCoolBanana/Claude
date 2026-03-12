"""Stress tests for MultiTeamRunner and its supporting functions.

Pushes the multi-team runner through rapid initialization, concurrent
agent registration, phase advancement under load, heartbeat stress,
cleanup with large expired datasets, graceful shutdown during operations,
start/stop/restart cycles, and status queries during heavy updates.
Uses threading.Barrier for synchronized starts and 10-second timeouts
on all thread joins.
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest

# ---------------------------------------------------------------------------
# We need to monkey-patch the module-level paths BEFORE importing the runner,
# so that each test uses its own temp directory instead of the production
# coordination directory.
# ---------------------------------------------------------------------------
import storage.coordination.multi_team_runner as mtr

JOIN_TIMEOUT = 10


def _retry_on_busy(fn, max_retries=5, initial_delay=0.05):
    """Retry a callable on SQLITE_BUSY / SQLITE_LOCKED errors."""
    delay = initial_delay
    last_err = None
    for _ in range(max_retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "busy" in msg or "locked" in msg:
                last_err = e
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_err  # type: ignore[misc]


def _make_config(num_teams=3, agents_per_team=2, **overrides):
    """Build a runner config dict for testing."""
    cfg = mtr._load_config(None, num_teams, agents_per_team)
    cfg.update(overrides)
    return cfg


class _RunnerTestBase(unittest.TestCase):
    """Base class that redirects all module-level paths to a temp directory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Patch module-level paths
        self._orig_bus_dir = mtr._BUS_DIR
        self._orig_db_dir = mtr._DB_DIR
        self._orig_db_path = mtr._DB_PATH
        self._orig_teams_dir = mtr._TEAMS_DIR
        self._orig_log_file = mtr._LOG_FILE

        mtr._BUS_DIR = os.path.join(self.tmpdir, "bus")
        mtr._DB_DIR = os.path.join(self.tmpdir, "db")
        mtr._DB_PATH = os.path.join(self.tmpdir, "db", "state.db")
        mtr._TEAMS_DIR = os.path.join(self.tmpdir, "teams")
        mtr._LOG_FILE = os.path.join(self.tmpdir, "runner.log")

        # Reset the shared connection so each test starts fresh
        mtr._close_connection()

    def tearDown(self):
        # Ensure the shared connection is closed
        mtr._close_connection()
        # Restore original paths
        mtr._BUS_DIR = self._orig_bus_dir
        mtr._DB_DIR = self._orig_db_dir
        mtr._DB_PATH = self._orig_db_path
        mtr._TEAMS_DIR = self._orig_teams_dir
        mtr._LOG_FILE = self._orig_log_file


# ===================================================================
# MultiTeamRunner Stress Tests
# ===================================================================


class TestMultiTeamRunnerStress(_RunnerTestBase):
    """Stress tests for the MultiTeamRunner lifecycle and operations."""

    def test_rapid_init_many_teams(self):
        """Initialize a runner with 12 teams and 6 agents each (72 agents).

        All agents should be registered and the database should be consistent.
        """
        cfg = _make_config(num_teams=12, agents_per_team=6)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        count = runner.register_all_agents()

        self.assertEqual(count, 72)

        # Verify all agents are in the database
        conn = mtr._get_connection()
        with mtr._shared_conn_lock:
            rows = conn.execute("SELECT COUNT(*) as cnt FROM agents").fetchone()
        # 72 agents registered by register_all_agents
        self.assertEqual(rows["cnt"], 72)

        # Verify teams are correct
        with mtr._shared_conn_lock:
            teams = conn.execute(
                "SELECT DISTINCT team FROM agents WHERE team != 'system'"
            ).fetchall()
        team_names = {r["team"] for r in teams}
        self.assertEqual(len(team_names), 12)

    def test_concurrent_agent_registration_25_threads(self):
        """25 threads register agents simultaneously.

        All registrations should succeed without SQLite errors.
        """
        cfg = _make_config(num_teams=1, agents_per_team=1)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()

        num_threads = 25
        barrier = threading.Barrier(num_threads)
        errors = []
        errors_lock = threading.Lock()

        def register(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                _retry_on_busy(
                    lambda: mtr._register_agent(
                        f"concurrent-agent-{idx}", f"team-{idx % 5}", "worker"
                    )
                )
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=register, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors during registration: {errors}")

        # Verify all 25 agents are registered
        conn = mtr._get_connection()
        with mtr._shared_conn_lock:
            rows = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents WHERE agent_id LIKE 'concurrent-agent-%'"
            ).fetchone()
        self.assertEqual(rows["cnt"], 25)

    def test_phase_advancement_under_load(self):
        """Rapidly advance through 20 phases while agents are being registered.

        Phase signals table should contain all transitions.
        """
        cfg = _make_config(num_teams=3, agents_per_team=3)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        runner.register_all_agents()
        runner.start()

        try:
            num_phases = 20
            barrier = threading.Barrier(2)
            errors = []
            errors_lock = threading.Lock()

            def advance_phases():
                try:
                    barrier.wait(timeout=JOIN_TIMEOUT)
                    for i in range(num_phases):
                        _retry_on_busy(
                            lambda i=i: runner.advance_phase(
                                f"phase-{i}", {"step": i}
                            )
                        )
                except Exception as e:
                    with errors_lock:
                        errors.append(("phase", e))

            def register_more():
                try:
                    barrier.wait(timeout=JOIN_TIMEOUT)
                    for i in range(10):
                        _retry_on_busy(
                            lambda i=i: mtr._register_agent(
                                f"late-agent-{i}", "team-01", "worker"
                            )
                        )
                except Exception as e:
                    with errors_lock:
                        errors.append(("register", e))

            t1 = threading.Thread(target=advance_phases)
            t2 = threading.Thread(target=register_more)
            t1.start()
            t2.start()
            t1.join(timeout=JOIN_TIMEOUT)
            t2.join(timeout=JOIN_TIMEOUT)

            self.assertEqual(len(errors), 0, f"Errors: {errors}")

            # Verify all phase transitions were recorded
            conn = mtr._get_connection()
            with mtr._shared_conn_lock:
                phase_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM phase_signals"
                ).fetchone()["cnt"]
            self.assertEqual(phase_count, num_phases)

            # Final phase should be phase-19
            self.assertEqual(runner._phase, f"phase-{num_phases - 1}")
        finally:
            runner.stop()

    def test_heartbeat_loop_high_frequency(self):
        """Run the heartbeat loop at very high frequency (0.05s interval).

        After several beats, the runner agent should have a recent heartbeat.
        """
        cfg = _make_config(
            num_teams=2, agents_per_team=2, heartbeat_interval=0.05
        )
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        runner.register_all_agents()
        runner.start()

        try:
            # Let the heartbeat loop run for at least 0.5s
            time.sleep(0.5)

            # Check that runner has a recent heartbeat
            conn = mtr._get_connection()
            with mtr._shared_conn_lock:
                row = conn.execute(
                    "SELECT last_heartbeat FROM agents WHERE agent_id = 'runner'"
                ).fetchone()

            self.assertIsNotNone(row)
            elapsed = time.time() - row["last_heartbeat"]
            # Should be within a few heartbeat intervals
            self.assertLess(elapsed, 1.0, "Runner heartbeat is stale")
        finally:
            runner.stop()

    def test_cleanup_loop_large_expired_dataset(self):
        """Insert 500 expired messages and rate_limits, then trigger cleanup.

        All expired rows should be removed.
        """
        cfg = _make_config(num_teams=1, agents_per_team=1)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()

        conn = mtr._get_connection()
        now = time.time()

        # Insert 500 expired messages (expires_at in the past)
        with mtr._shared_conn_lock:
            for i in range(500):
                conn.execute(
                    "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
                    "VALUES (?, 'test', 'agent-1', 'info', '{}', ?, ?)",
                    (f"msg-{i}", now - 7200, now - 3600),
                )
            # Insert 500 expired rate_limits (called_at > 1 hour ago)
            for i in range(500):
                conn.execute(
                    "INSERT INTO rate_limits (api_endpoint, agent_id, called_at) "
                    "VALUES ('endpoint-1', 'agent-1', ?)",
                    (now - 7200,),
                )
            conn.commit()

        # Also insert some non-expired data that should survive
        with mtr._shared_conn_lock:
            for i in range(10):
                conn.execute(
                    "INSERT INTO messages (msg_id, channel, agent_id, msg_type, body, ts, expires_at) "
                    "VALUES (?, 'test', 'agent-1', 'info', '{}', ?, ?)",
                    (f"fresh-{i}", now, now + 3600),
                )
            conn.commit()

        # Run cleanup
        runner._run_cleanup()

        # Verify expired rows removed
        with mtr._shared_conn_lock:
            msg_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages"
            ).fetchone()["cnt"]
            rate_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM rate_limits"
            ).fetchone()["cnt"]

        self.assertEqual(msg_count, 10, "Non-expired messages should survive cleanup")
        self.assertEqual(rate_count, 0, "All expired rate_limits should be cleaned")

    def test_graceful_shutdown_during_operations(self):
        """Start a runner, begin operations, then shut down.

        Heartbeat and cleanup threads should terminate within timeout.
        """
        cfg = _make_config(
            num_teams=5, agents_per_team=3,
            heartbeat_interval=0.1, cleanup_interval=0.2,
        )
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        runner.register_all_agents()
        runner.start()

        # Do some operations concurrently with shutdown
        errors = []

        def do_operations():
            try:
                for i in range(5):
                    _retry_on_busy(
                        lambda i=i: runner.advance_phase(f"op-phase-{i}")
                    )
                    _retry_on_busy(
                        lambda: runner.get_status()
                    )
            except Exception as e:
                errors.append(e)

        op_thread = threading.Thread(target=do_operations)
        op_thread.start()

        # Let operations run briefly
        time.sleep(0.2)

        # Shutdown while operations may still be running
        runner.stop()
        op_thread.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(runner._phase, "shutdown")

        # Threads should have stopped
        if runner._hb_thread is not None:
            self.assertFalse(
                runner._hb_thread.is_alive(),
                "Heartbeat thread still alive after stop"
            )
        if runner._cleanup_thread is not None:
            self.assertFalse(
                runner._cleanup_thread.is_alive(),
                "Cleanup thread still alive after stop"
            )

    def test_start_stop_restart_cycles(self):
        """Start, stop, and restart the runner 5 times.

        Each cycle should complete without errors and the runner should
        function correctly after each restart.
        """
        cfg = _make_config(
            num_teams=3, agents_per_team=2,
            heartbeat_interval=0.05,
        )
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        runner.register_all_agents()

        for cycle in range(5):
            # Reset connection for clean state on restart
            mtr._close_connection()

            runner.start()
            self.assertEqual(runner._phase, "running")

            # Do a phase advancement
            _retry_on_busy(
                lambda cycle=cycle: runner.advance_phase(f"cycle-{cycle}")
            )

            # Brief operation
            time.sleep(0.1)

            status = runner.get_status()
            self.assertIn("agents", status)

            runner.stop()
            self.assertEqual(runner._phase, "shutdown")

        # After all cycles, verify the database still has the agents
        conn = mtr._get_connection()
        with mtr._shared_conn_lock:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents WHERE team != 'system'"
            ).fetchone()["cnt"]
        # Original 6 agents should still be there (re-registered on start)
        self.assertGreaterEqual(count, 6)

    def test_status_queries_during_heavy_updates(self):
        """10 threads query status while another 10 threads register agents.

        All status queries should return valid dicts; no crashes.
        """
        cfg = _make_config(num_teams=3, agents_per_team=2)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        runner.register_all_agents()
        runner.start()

        try:
            num_readers = 10
            num_writers = 10
            barrier = threading.Barrier(num_readers + num_writers)
            errors = []
            errors_lock = threading.Lock()
            statuses = []
            statuses_lock = threading.Lock()

            def status_reader(idx):
                try:
                    barrier.wait(timeout=JOIN_TIMEOUT)
                    for _ in range(20):
                        s = _retry_on_busy(lambda: runner.get_status())
                        with statuses_lock:
                            statuses.append(s)
                except Exception as e:
                    with errors_lock:
                        errors.append(("reader", idx, e))

            def agent_writer(idx):
                try:
                    barrier.wait(timeout=JOIN_TIMEOUT)
                    for j in range(10):
                        _retry_on_busy(
                            lambda idx=idx, j=j: mtr._register_agent(
                                f"heavy-agent-{idx}-{j}",
                                f"team-{idx % 3 + 1:02d}",
                                "worker",
                            )
                        )
                except Exception as e:
                    with errors_lock:
                        errors.append(("writer", idx, e))

            threads = []
            for i in range(num_readers):
                threads.append(threading.Thread(target=status_reader, args=(i,)))
            for i in range(num_writers):
                threads.append(threading.Thread(target=agent_writer, args=(i,)))

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=JOIN_TIMEOUT)

            self.assertEqual(len(errors), 0, f"Errors: {errors}")

            # All status dicts should be valid
            for s in statuses:
                self.assertIn("phase", s)
                self.assertIn("agents", s)
                self.assertIsInstance(s["agents"], list)

            # Final status should reflect all registered agents
            final = runner.get_status()
            # At least the original 6 + runner + some heavy-agents
            self.assertGreater(len(final["agents"]), 6)
        finally:
            runner.stop()

    def test_concurrent_heartbeats_from_many_agents(self):
        """20 threads all heartbeat their own agent concurrently.

        All heartbeats should be recorded without contention errors.
        """
        cfg = _make_config(num_teams=4, agents_per_team=5)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        runner.register_all_agents()

        num_threads = 20
        barrier = threading.Barrier(num_threads)
        errors = []
        errors_lock = threading.Lock()

        # Collect all agent IDs
        agent_ids = []
        for team in cfg["teams"]:
            for agent in team["agents"]:
                agent_ids.append(agent["agent_id"])

        def heartbeat_agent(idx):
            try:
                aid = agent_ids[idx % len(agent_ids)]
                barrier.wait(timeout=JOIN_TIMEOUT)
                for _ in range(50):
                    _retry_on_busy(lambda: mtr._heartbeat(aid))
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=heartbeat_agent, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Verify all agents have recent heartbeats
        conn = mtr._get_connection()
        now = time.time()
        with mtr._shared_conn_lock:
            rows = conn.execute(
                "SELECT agent_id, last_heartbeat FROM agents"
            ).fetchall()
        for row in rows:
            elapsed = now - row["last_heartbeat"]
            self.assertLess(
                elapsed, 5.0,
                f"Agent {row['agent_id']} heartbeat stale by {elapsed:.1f}s",
            )

    def test_dead_agent_detection_under_load(self):
        """Register agents with old heartbeats, then check health detection
        while other operations are running concurrently.
        """
        cfg = _make_config(num_teams=2, agents_per_team=2, dead_agent_timeout=1.0)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()
        runner.register_all_agents()

        # Manually set some agents to have old heartbeats
        conn = mtr._get_connection()
        old_time = time.time() - 10.0  # 10 seconds ago, well past 1s timeout
        with mtr._shared_conn_lock:
            conn.execute(
                "UPDATE agents SET last_heartbeat = ? WHERE team = 'team-01'",
                (old_time,),
            )
            conn.commit()

        barrier = threading.Barrier(2)
        errors = []
        errors_lock = threading.Lock()
        dead_agents_found = []

        def check_health():
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                dead = _retry_on_busy(
                    lambda: mtr._get_dead_agents(timeout=1.0)
                )
                dead_agents_found.extend(dead)
            except Exception as e:
                with errors_lock:
                    errors.append(("health", e))

        def register_new():
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for i in range(5):
                    _retry_on_busy(
                        lambda i=i: mtr._register_agent(
                            f"new-agent-{i}", "team-03", "worker"
                        )
                    )
            except Exception as e:
                with errors_lock:
                    errors.append(("register", e))

        t1 = threading.Thread(target=check_health)
        t2 = threading.Thread(target=register_new)
        t1.start()
        t2.start()
        t1.join(timeout=JOIN_TIMEOUT)
        t2.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Team-01 agents should be detected as dead
        dead_ids = {a["agent_id"] for a in dead_agents_found}
        self.assertIn("team-01-lead", dead_ids)

    def test_runner_state_persistence(self):
        """Rapidly read and write runner state from multiple threads.

        Final state should reflect the last write for each key.
        """
        cfg = _make_config(num_teams=1, agents_per_team=1)
        runner = mtr.MultiTeamRunner(cfg)
        runner.init_environment()

        num_threads = 10
        writes_per_thread = 20
        barrier = threading.Barrier(num_threads)
        errors = []
        errors_lock = threading.Lock()

        def state_writer(idx):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for j in range(writes_per_thread):
                    _retry_on_busy(
                        lambda idx=idx, j=j: mtr._set_runner_state(
                            f"key-{j % 5}", f"value-{idx}-{j}"
                        )
                    )
            except Exception as e:
                with errors_lock:
                    errors.append((idx, e))

        threads = [
            threading.Thread(target=state_writer, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # All 5 keys should exist with some value
        for j in range(5):
            val = mtr._get_runner_state(f"key-{j}")
            self.assertIsNotNone(val, f"key-{j} should have a value")
            self.assertTrue(val.startswith("value-"), f"Unexpected value: {val}")


if __name__ == "__main__":
    unittest.main()
