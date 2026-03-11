"""Tests for WorkStealing, PipelineManager, and Scratchpad.

Covers:
- Enqueue and steal work items
- Concurrent steal attempts (only one wins)
- Pipeline creation and stage progression
- Pipeline auto-trigger on upstream completion
- Scratchpad CRUD operations
- Scratchpad TTL expiration
"""

import os
import tempfile
import threading
import time
import unittest

from storage.coordination.work_stealing import (
    PipelineManager,
    Scratchpad,
    WorkStealing,
)


class TestWorkStealing(unittest.TestCase):
    """Tests for the WorkStealing shared queue."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        self.ws = WorkStealing(self.db_path, self.bus_dir, "team-a", "agent-1")

    def tearDown(self):
        self.ws.close()

    def test_enqueue_returns_id(self):
        wid = self.ws.enqueue_work("task-1", "do something", priority=3)
        self.assertIsInstance(wid, int)
        self.assertGreater(wid, 0)

    def test_enqueue_and_steal(self):
        wid = self.ws.enqueue_work("task-1", "desc")
        # Different team steals it
        ws2 = WorkStealing(self.db_path, self.bus_dir, "team-b", "agent-2")
        stolen = ws2.steal_work()
        ws2.close()

        self.assertIsNotNone(stolen)
        self.assertEqual(stolen["id"], wid)
        self.assertEqual(stolen["title"], "task-1")
        self.assertEqual(stolen["claimed_by"], "team-b")
        self.assertEqual(stolen["status"], "claimed")

    def test_steal_returns_none_when_empty(self):
        result = self.ws.steal_work()
        self.assertIsNone(result)

    def test_steal_respects_priority(self):
        self.ws.enqueue_work("low-pri", "desc", priority=10)
        self.ws.enqueue_work("high-pri", "desc", priority=1)
        self.ws.enqueue_work("mid-pri", "desc", priority=5)

        ws2 = WorkStealing(self.db_path, self.bus_dir, "team-b", "agent-2")
        stolen = ws2.steal_work()
        ws2.close()

        self.assertEqual(stolen["title"], "high-pri")
        self.assertEqual(stolen["priority"], 1)

    def test_concurrent_steal_only_one_wins(self):
        """Two threads try to steal the same work item; only one should succeed."""
        self.ws.enqueue_work("contested-task", "only one can win")

        results = []
        errors = []

        def try_steal(team_name):
            try:
                ws = WorkStealing(self.db_path, self.bus_dir, team_name, f"{team_name}-agent")
                result = ws.steal_work()
                results.append((team_name, result))
                ws.close()
            except Exception as e:
                errors.append((team_name, e))

        t1 = threading.Thread(target=try_steal, args=("team-x",))
        t2 = threading.Thread(target=try_steal, args=("team-y",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Errors during steal: {errors}")
        # Exactly one should have gotten the item, the other gets None
        winners = [r for r in results if r[1] is not None]
        losers = [r for r in results if r[1] is None]
        self.assertEqual(len(winners), 1, f"Expected 1 winner, got {len(winners)}")
        self.assertEqual(len(losers), 1, f"Expected 1 loser, got {len(losers)}")

    def test_complete_work(self):
        wid = self.ws.enqueue_work("task-1", "desc")
        stolen = self.ws.steal_work()
        self.ws.complete_work(wid, {"output": "done"})

        # Verify it's no longer stealable
        self.assertEqual(self.ws.get_queue_depth(), 0)

    def test_fail_work(self):
        wid = self.ws.enqueue_work("task-1", "desc")
        self.ws.steal_work()
        self.ws.fail_work(wid, "something broke")
        self.assertEqual(self.ws.get_queue_depth(), 0)

    def test_get_queue_depth(self):
        self.assertEqual(self.ws.get_queue_depth(), 0)
        self.ws.enqueue_work("t1", "d")
        self.ws.enqueue_work("t2", "d")
        self.assertEqual(self.ws.get_queue_depth(), 2)
        self.ws.steal_work()
        self.assertEqual(self.ws.get_queue_depth(), 1)

    def test_get_queue_depth_by_team(self):
        self.ws.enqueue_work("t1", "d")
        ws2 = WorkStealing(self.db_path, self.bus_dir, "team-b", "agent-2")
        ws2.enqueue_work("t2", "d")
        ws2.close()

        self.assertEqual(self.ws.get_queue_depth(team="team-a"), 1)
        self.assertEqual(self.ws.get_queue_depth(team="team-b"), 1)
        self.assertEqual(self.ws.get_queue_depth(), 2)

    def test_get_stealable_work(self):
        self.ws.enqueue_work("t1", "d", priority=5)
        self.ws.enqueue_work("t2", "d", priority=1)
        items = self.ws.get_stealable_work()
        self.assertEqual(len(items), 2)
        # Should be sorted by priority
        self.assertEqual(items[0]["title"], "t2")
        self.assertEqual(items[1]["title"], "t1")


class TestPipelineManager(unittest.TestCase):
    """Tests for the PipelineManager."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        self.pm = PipelineManager(self.db_path, self.bus_dir, "team-a", "agent-1")

    def tearDown(self):
        self.pm.close()

    def test_create_pipeline(self):
        pid = self.pm.create_pipeline("my-pipeline", [
            {"name": "collect", "team": "gamma"},
            {"name": "analyze", "team": "alpha", "depends_on": ["collect"]},
            {"name": "report", "team": "beta", "depends_on": ["analyze"]},
        ])
        self.assertIsInstance(pid, int)
        self.assertGreater(pid, 0)

    def test_initial_stage_status(self):
        pid = self.pm.create_pipeline("test-pipe", [
            {"name": "step1"},
            {"name": "step2", "depends_on": ["step1"]},
        ])
        status = self.pm.get_pipeline_status(pid)
        stages = {s["stage_name"]: s["status"] for s in status["stages"]}
        self.assertEqual(stages["step1"], "ready")
        self.assertEqual(stages["step2"], "waiting")

    def test_stage_progression(self):
        pid = self.pm.create_pipeline("test-pipe", [
            {"name": "step1"},
            {"name": "step2", "depends_on": ["step1"]},
        ])

        # Start step1
        self.pm.start_stage(pid, "step1")
        status = self.pm.get_pipeline_status(pid)
        s1 = [s for s in status["stages"] if s["stage_name"] == "step1"][0]
        self.assertEqual(s1["status"], "in_progress")

        # Complete step1
        self.pm.complete_stage(pid, "step1", {"result": "data"})
        status = self.pm.get_pipeline_status(pid)
        s1 = [s for s in status["stages"] if s["stage_name"] == "step1"][0]
        self.assertEqual(s1["status"], "completed")

    def test_check_stage_ready(self):
        pid = self.pm.create_pipeline("test-pipe", [
            {"name": "step1"},
            {"name": "step2", "depends_on": ["step1"]},
        ])

        # step2 is not ready yet
        self.assertFalse(self.pm.check_stage_ready(pid, "step2"))
        # step1 has no deps, so it's ready
        self.assertTrue(self.pm.check_stage_ready(pid, "step1"))

        # Complete step1
        self.pm.start_stage(pid, "step1")
        self.pm.complete_stage(pid, "step1", {"data": 1})

        # Now step2 should be ready
        self.assertTrue(self.pm.check_stage_ready(pid, "step2"))

    def test_trigger_downstream(self):
        pid = self.pm.create_pipeline("test-pipe", [
            {"name": "collect"},
            {"name": "analyze", "depends_on": ["collect"]},
            {"name": "report", "depends_on": ["analyze"]},
        ])

        # Complete collect
        self.pm.start_stage(pid, "collect")
        self.pm.complete_stage(pid, "collect", {"raw": "data"})

        # Trigger downstream
        newly_ready = self.pm.trigger_downstream(pid, "collect")
        self.assertEqual(newly_ready, ["analyze"])

        # report should NOT be ready (depends on analyze, not collect)
        self.assertNotIn("report", newly_ready)

    def test_trigger_downstream_multi_dep(self):
        """A stage with multiple dependencies only becomes ready when ALL are done."""
        pid = self.pm.create_pipeline("multi-dep", [
            {"name": "a"},
            {"name": "b"},
            {"name": "c", "depends_on": ["a", "b"]},
        ])

        # Complete only a
        self.pm.start_stage(pid, "a")
        self.pm.complete_stage(pid, "a", {})
        newly = self.pm.trigger_downstream(pid, "a")
        self.assertEqual(newly, [])  # c still waiting on b

        # Complete b
        self.pm.start_stage(pid, "b")
        self.pm.complete_stage(pid, "b", {})
        newly = self.pm.trigger_downstream(pid, "b")
        self.assertEqual(newly, ["c"])  # now both deps met

    def test_get_ready_stages(self):
        pid = self.pm.create_pipeline("test-pipe", [
            {"name": "s1"},
            {"name": "s2"},
            {"name": "s3", "depends_on": ["s1", "s2"]},
        ])
        ready = self.pm.get_ready_stages(pid)
        names = [s["stage_name"] for s in ready]
        self.assertIn("s1", names)
        self.assertIn("s2", names)
        self.assertNotIn("s3", names)

    def test_pipeline_completed_status(self):
        pid = self.pm.create_pipeline("simple", [
            {"name": "only-step"},
        ])
        self.pm.start_stage(pid, "only-step")
        self.pm.complete_stage(pid, "only-step", {"done": True})

        status = self.pm.get_pipeline_status(pid)
        self.assertEqual(status["status"], "completed")

    def test_nonexistent_pipeline(self):
        status = self.pm.get_pipeline_status(9999)
        self.assertEqual(status, {})

    def test_nonexistent_stage_ready(self):
        self.assertFalse(self.pm.check_stage_ready(9999, "nope"))


class TestScratchpad(unittest.TestCase):
    """Tests for the Scratchpad key-value store."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.sp = Scratchpad(self.db_path, "team-a", "agent-1")

    def tearDown(self):
        self.sp.close()

    def test_write_and_read(self):
        self.sp.write("key1", {"hello": "world"})
        val = self.sp.read("key1")
        self.assertEqual(val, {"hello": "world"})

    def test_read_missing_key(self):
        val = self.sp.read("nonexistent")
        self.assertIsNone(val)

    def test_overwrite(self):
        self.sp.write("key1", "v1")
        self.sp.write("key1", "v2")
        self.assertEqual(self.sp.read("key1"), "v2")

    def test_namespace_isolation(self):
        self.sp.write("key1", "team-a-value")
        self.sp.write("key1", "global-value", namespace="global")

        self.assertEqual(self.sp.read("key1"), "team-a-value")
        self.assertEqual(self.sp.read("key1", namespace="global"), "global-value")

    def test_delete(self):
        self.sp.write("key1", "value")
        self.sp.delete("key1")
        self.assertIsNone(self.sp.read("key1"))

    def test_read_all(self):
        self.sp.write("k1", "v1")
        self.sp.write("k2", "v2")
        self.sp.write("k3", "v3", namespace="global")  # different namespace

        result = self.sp.read_all()
        self.assertEqual(result, {"k1": "v1", "k2": "v2"})

    def test_list_keys(self):
        self.sp.write("alpha", 1)
        self.sp.write("beta", 2)
        keys = self.sp.list_keys()
        self.assertIn("alpha", keys)
        self.assertIn("beta", keys)
        self.assertEqual(len(keys), 2)

    def test_ttl_expiration(self):
        """Write with a very short TTL, wait, then verify it's expired."""
        self.sp.write("ephemeral", "gone-soon", ttl=1)
        # Should be readable immediately
        self.assertEqual(self.sp.read("ephemeral"), "gone-soon")
        # Wait for expiry
        time.sleep(1.1)
        self.assertIsNone(self.sp.read("ephemeral"))

    def test_cleanup_expired(self):
        self.sp.write("short-lived", "data", ttl=1)
        self.sp.write("long-lived", "data", ttl=3600)
        time.sleep(1.1)

        deleted = self.sp.cleanup_expired()
        self.assertEqual(deleted, 1)

        # long-lived should still be there
        self.assertEqual(self.sp.read("long-lived"), "data")
        self.assertIsNone(self.sp.read("short-lived"))

    def test_cross_team_read(self):
        """One team writes to global, another team reads it."""
        sp2 = Scratchpad(self.db_path, "team-b", "agent-2")
        self.sp.write("shared-key", "shared-value", namespace="global")
        val = sp2.read("shared-key", namespace="global")
        sp2.close()
        self.assertEqual(val, "shared-value")

    def test_various_value_types(self):
        self.sp.write("str_val", "hello")
        self.sp.write("int_val", 42)
        self.sp.write("list_val", [1, 2, 3])
        self.sp.write("bool_val", True)
        self.sp.write("null_val", None)

        self.assertEqual(self.sp.read("str_val"), "hello")
        self.assertEqual(self.sp.read("int_val"), 42)
        self.assertEqual(self.sp.read("list_val"), [1, 2, 3])
        self.assertEqual(self.sp.read("bool_val"), True)
        self.assertIsNone(self.sp.read("null_val"))  # JSON null -> None, same as missing


if __name__ == "__main__":
    unittest.main()
