"""Stress tests proving the 4 TOCTOU race conditions are fixed.

Tests:
  1. TOCTOU-WS-001: complete_work atomicity
  2. TOCTOU-HP-001: fulfill_help atomicity
  3. TOCTOU-DC-001: join_channel atomicity
  4. TOCTOU-DC-002: leave_channel atomicity

Each test spawns many threads that hit the same operation simultaneously
using threading.Barrier to maximize contention.  Tests are run multiple
times to increase race detection probability.
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from storage.coordination.work_stealing import WorkStealing
from storage.coordination.help_protocol import HelpProtocol
from storage.coordination.direct_channels import DirectChannels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tmpdir():
    """Create a fresh temp directory for test isolation."""
    return tempfile.mkdtemp(prefix="toctou_test_")


def _stress_run(worker_fn, n_threads, args_per_thread=None):
    """Run *worker_fn* in *n_threads* threads, synchronised by a barrier.

    Parameters
    ----------
    worker_fn : callable(barrier, thread_index, *args) -> result
        Each thread calls this.  It should call barrier.wait() before the
        critical section.
    n_threads : int
    args_per_thread : list[tuple] | None
        Per-thread extra args.  If None, each thread gets ``()``.

    Returns (successes: list, errors: list[Exception])
    """
    barrier = threading.Barrier(n_threads)
    successes = []
    errors = []
    lock = threading.Lock()

    def _target(idx):
        extra = args_per_thread[idx] if args_per_thread else ()
        try:
            result = worker_fn(barrier, idx, *extra)
            with lock:
                successes.append((idx, result))
        except Exception as e:
            with lock:
                errors.append((idx, e))

    threads = [threading.Thread(target=_target, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return successes, errors


# ===================================================================
# Test 1: complete_work atomicity (TOCTOU-WS-001)
# ===================================================================

class TestCompleteWorkAtomicity(unittest.TestCase):
    """TOCTOU-WS-001: Only one thread should successfully complete a work item.

    The fixed code uses an atomic UPDATE ... WHERE with a claimed_by check
    (or BEGIN IMMEDIATE) so that concurrent complete_work calls on the same
    work item result in exactly 1 success.
    """

    THREADS = 15
    ROUNDS = 5

    def _run_once(self):
        tmpdir = _make_tmpdir()
        db_path = os.path.join(tmpdir, "ws.db")
        bus_dir = os.path.join(tmpdir, "bus")

        # Set up: one work item claimed by team-A
        ws_setup = WorkStealing(db_path, bus_dir, "team-owner", "agent-0")
        work_id = ws_setup.enqueue_work("race-item", "testing", priority=1)

        ws_claimer = WorkStealing(db_path, bus_dir, "team-A", "agent-A")
        stolen = ws_claimer.steal_work()
        self.assertIsNotNone(stolen)
        self.assertEqual(stolen["id"], work_id)
        ws_claimer.close()
        ws_setup.close()

        # Each thread creates its own WorkStealing instance as team-A
        # (simulating concurrent calls from the same authorised team)
        instances = []
        for i in range(self.THREADS):
            ws = WorkStealing(db_path, bus_dir, "team-A", f"agent-{i}")
            instances.append(ws)

        def worker(barrier, idx):
            barrier.wait()
            instances[idx].complete_work(work_id, {"result": f"from-{idx}"})
            return idx

        successes, errors = _stress_run(worker, self.THREADS)

        # Clean up
        for ws in instances:
            ws.close()

        # Exactly 1 should succeed
        self.assertEqual(
            len(successes), 1,
            f"Expected exactly 1 success, got {len(successes)}: {successes}"
        )
        self.assertEqual(
            len(errors), self.THREADS - 1,
            f"Expected {self.THREADS - 1} errors, got {len(errors)}"
        )

        # All errors should be ValueError
        for idx, err in errors:
            self.assertIsInstance(err, ValueError, f"Thread {idx}: unexpected error type: {err!r}")

        # Verify the DB has the correct final result
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, result, claimed_by FROM work_queue WHERE id = ?", (work_id,)).fetchone()
        conn.close()

        self.assertEqual(row["status"], "completed")
        winner_idx = successes[0][0]
        result_data = json.loads(row["result"])
        self.assertEqual(result_data["result"], f"from-{winner_idx}")

    def test_complete_work_atomicity(self):
        for round_num in range(self.ROUNDS):
            with self.subTest(round=round_num):
                self._run_once()


# ===================================================================
# Test 2: fulfill_help atomicity (TOCTOU-HP-001)
# ===================================================================

class TestFulfillHelpAtomicity(unittest.TestCase):
    """TOCTOU-HP-001: Only one thread should successfully fulfill a help request.

    The fixed code wraps the SELECT + 2 UPDATEs in BEGIN IMMEDIATE so that
    concurrent fulfill_help calls result in exactly 1 success.
    """

    THREADS = 12
    ROUNDS = 5

    def _run_once(self):
        tmpdir = _make_tmpdir()
        db_path = os.path.join(tmpdir, "help.db")
        bus_dir = os.path.join(tmpdir, "bus")
        os.makedirs(bus_dir, exist_ok=True)

        # Set up: create a help request, accept it as team-helper
        hp_requester = HelpProtocol(db_path, bus_dir, "team-requester", "agent-req")
        work_id = hp_requester.add_work_item("help-task", "need help", priority="high")
        request_id = hp_requester.request_help(work_id, "please help me")
        hp_requester.close()

        # Accept the help request as team-helper
        hp_accepter = HelpProtocol(db_path, bus_dir, "team-helper", "agent-hlp")
        accepted = hp_accepter.offer_help(request_id)
        self.assertTrue(accepted)
        hp_accepter.close()

        # Each thread creates its own HelpProtocol instance as team-helper
        instances = []
        for i in range(self.THREADS):
            hp = HelpProtocol(db_path, bus_dir, "team-helper", f"agent-{i}")
            instances.append(hp)

        def worker(barrier, idx):
            barrier.wait()
            instances[idx].fulfill_help(request_id)
            return idx

        successes, errors = _stress_run(worker, self.THREADS)

        for hp in instances:
            hp.close()

        # Exactly 1 should succeed
        self.assertEqual(
            len(successes), 1,
            f"Expected exactly 1 success, got {len(successes)}: {successes}"
        )
        self.assertEqual(
            len(errors), self.THREADS - 1,
            f"Expected {self.THREADS - 1} errors, got {len(errors)}"
        )

        for idx, err in errors:
            self.assertIsInstance(err, ValueError, f"Thread {idx}: unexpected error type: {err!r}")

        # Verify consistent DB state
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        hr_row = conn.execute("SELECT status, resolved_at FROM help_requests WHERE id = ?", (request_id,)).fetchone()
        self.assertEqual(hr_row["status"], "fulfilled")
        self.assertIsNotNone(hr_row["resolved_at"])

        wi_row = conn.execute("SELECT status FROM work_items WHERE id = ?", (work_id,)).fetchone()
        self.assertEqual(wi_row["status"], "completed")

        conn.close()

    def test_fulfill_help_atomicity(self):
        for round_num in range(self.ROUNDS):
            with self.subTest(round=round_num):
                self._run_once()


# ===================================================================
# Test 3: join_channel atomicity (TOCTOU-DC-001)
# ===================================================================

class TestJoinChannelAtomicity(unittest.TestCase):
    """TOCTOU-DC-001: All concurrent join_channel calls must be reflected.

    The fixed code uses BEGIN IMMEDIATE around the read-modify-write on
    the participants JSON column, preventing lost joins.
    """

    THREADS = 25
    ROUNDS = 5

    def _run_once(self):
        tmpdir = _make_tmpdir()
        db_path = os.path.join(tmpdir, "dc.db")
        bus_dir = os.path.join(tmpdir, "bus")
        os.makedirs(bus_dir, exist_ok=True)

        channel_name = "topic-stress-join"

        # Create the channel with a creator team
        dc_creator = DirectChannels(db_path, bus_dir, "creator", "agent-creator")
        dc_creator.create_topic_channel("stress-join", [])
        dc_creator.close()

        # Each thread is a different team joining the channel
        team_names = [f"team-{i:03d}" for i in range(self.THREADS)]
        instances = []
        for name in team_names:
            dc = DirectChannels(db_path, bus_dir, name, f"agent-{name}")
            instances.append(dc)

        def worker(barrier, idx):
            barrier.wait()
            instances[idx].join_channel(channel_name)
            return team_names[idx]

        successes, errors = _stress_run(worker, self.THREADS)

        for dc in instances:
            dc.close()

        # All joins should succeed (no errors)
        self.assertEqual(
            len(errors), 0,
            f"Expected 0 errors, got {len(errors)}: {[(i, str(e)) for i, e in errors]}"
        )
        self.assertEqual(len(successes), self.THREADS)

        # Verify: all teams appear in the participants list
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT participants FROM channels WHERE channel_name = ?",
            (channel_name,),
        ).fetchone()
        conn.close()

        participants = json.loads(row["participants"])

        # The creator is always in the participants
        expected_teams = set(team_names) | {"creator"}
        actual_teams = set(participants)

        # No lost joins
        self.assertEqual(
            actual_teams, expected_teams,
            f"Lost joins! Missing: {expected_teams - actual_teams}, "
            f"Unexpected: {actual_teams - expected_teams}"
        )

        # No duplicates
        self.assertEqual(
            len(participants), len(set(participants)),
            f"Duplicate participants found: {participants}"
        )

    def test_join_channel_atomicity(self):
        for round_num in range(self.ROUNDS):
            with self.subTest(round=round_num):
                self._run_once()


# ===================================================================
# Test 4: leave_channel atomicity (TOCTOU-DC-002)
# ===================================================================

class TestLeaveChannelAtomicity(unittest.TestCase):
    """TOCTOU-DC-002: All concurrent leave_channel calls must be reflected.

    The fixed code uses BEGIN IMMEDIATE around the read-modify-write on
    the participants JSON column, preventing phantom re-additions.
    """

    THREADS = 20
    ROUNDS = 5

    def _run_once(self):
        tmpdir = _make_tmpdir()
        db_path = os.path.join(tmpdir, "dc.db")
        bus_dir = os.path.join(tmpdir, "bus")
        os.makedirs(bus_dir, exist_ok=True)

        channel_name = "topic-stress-leave"

        # Create channel with N teams as participants
        team_names = [f"team-{i:03d}" for i in range(self.THREADS)]
        all_participants = list(team_names)  # creator will also be added

        dc_creator = DirectChannels(db_path, bus_dir, "creator", "agent-creator")
        dc_creator.create_topic_channel("stress-leave", all_participants)
        dc_creator.close()

        # Verify initial state
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT participants FROM channels WHERE channel_name = ?",
            (channel_name,),
        ).fetchone()
        initial_participants = json.loads(row["participants"])
        conn.close()

        # All teams plus creator should be in the initial list
        expected_initial = set(team_names) | {"creator"}
        self.assertEqual(
            set(initial_participants), expected_initial,
            f"Initial participants mismatch: {initial_participants}"
        )

        # Each team leaves concurrently
        instances = []
        for name in team_names:
            dc = DirectChannels(db_path, bus_dir, name, f"agent-{name}")
            instances.append(dc)

        def worker(barrier, idx):
            barrier.wait()
            instances[idx].leave_channel(channel_name)
            return team_names[idx]

        successes, errors = _stress_run(worker, self.THREADS)

        for dc in instances:
            dc.close()

        # All leaves should succeed
        self.assertEqual(
            len(errors), 0,
            f"Expected 0 errors, got {len(errors)}: {[(i, str(e)) for i, e in errors]}"
        )

        # Verify: only the creator remains
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT participants FROM channels WHERE channel_name = ?",
            (channel_name,),
        ).fetchone()
        conn.close()

        final_participants = json.loads(row["participants"])

        # No phantom re-additions: only creator should remain
        self.assertEqual(
            final_participants, ["creator"],
            f"Expected only ['creator'], got {final_participants}. "
            f"Phantom teams still present: {set(final_participants) - {'creator'}}"
        )

    def test_leave_channel_atomicity(self):
        for round_num in range(self.ROUNDS):
            with self.subTest(round=round_num):
                self._run_once()


# ===================================================================
# Main entry point
# ===================================================================

def _run_test_class(cls):
    """Run a single test class and return (class_name, passed, total, failures)."""
    suite = unittest.TestLoader().loadTestsFromTestCase(cls)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures
    return cls.__name__, passed, total, result


if __name__ == "__main__":
    test_classes = [
        TestCompleteWorkAtomicity,
        TestFulfillHelpAtomicity,
        TestJoinChannelAtomicity,
        TestLeaveChannelAtomicity,
    ]

    overall_pass = True
    summary = []

    for cls in test_classes:
        name, passed, total, result = _run_test_class(cls)
        status = "PASS" if result.wasSuccessful() else "FAIL"
        if not result.wasSuccessful():
            overall_pass = False
        summary.append((name, status, passed, total))
        print()

    print("=" * 70)
    print("TOCTOU FIX STRESS TEST SUMMARY")
    print("=" * 70)
    for name, status, passed, total in summary:
        print(f"  [{status}] {name}: {passed}/{total} passed")
    print("=" * 70)
    print(f"Overall: {'PASS' if overall_pass else 'FAIL'}")
    print("=" * 70)

    sys.exit(0 if overall_pass else 1)
