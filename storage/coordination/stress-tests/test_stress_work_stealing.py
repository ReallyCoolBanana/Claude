"""Stress tests for WorkStealing, PipelineManager, and Scratchpad.

Pushes all three coordination primitives to their limits with high
concurrency, large data volumes, and adversarial timing scenarios.
Uses threading.Barrier for synchronized starts and 10-second timeouts
on all thread joins.
"""

import json
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

JOIN_TIMEOUT = 10


# ===================================================================
# WorkStealing Stress Tests
# ===================================================================


class TestWorkStealingStress(unittest.TestCase):
    """Stress tests for the WorkStealing shared queue."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "stress.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        self.ws = WorkStealing(self.db_path, self.bus_dir, "team-main", "agent-main")

    def tearDown(self):
        self.ws.close()

    def test_thundering_herd_20_threads_5_items(self):
        """20 threads all call steal_work() on a queue with 5 items.

        Exactly 5 threads should get a work item; the other 15 get None.
        """
        num_threads = 20
        num_items = 5

        for i in range(num_items):
            self.ws.enqueue_work(f"item-{i}", f"desc-{i}", priority=5)

        barrier = threading.Barrier(num_threads)
        results = []
        results_lock = threading.Lock()
        errors = []

        def steal(team_name):
            try:
                ws = WorkStealing(self.db_path, self.bus_dir, team_name, f"{team_name}-agent")
                barrier.wait(timeout=JOIN_TIMEOUT)
                result = ws.steal_work()
                with results_lock:
                    results.append((team_name, result))
                ws.close()
            except Exception as e:
                with results_lock:
                    errors.append((team_name, e))

        threads = [threading.Thread(target=steal, args=(f"team-{i}",)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors during thundering herd: {errors}")
        winners = [r for r in results if r[1] is not None]
        losers = [r for r in results if r[1] is None]
        self.assertEqual(len(winners), num_items, f"Expected {num_items} winners, got {len(winners)}")
        self.assertEqual(len(losers), num_threads - num_items,
                         f"Expected {num_threads - num_items} losers, got {len(losers)}")

        # Verify no duplicate claims
        claimed_ids = [r[1]["id"] for r in winners]
        self.assertEqual(len(set(claimed_ids)), num_items, "Duplicate work item claims detected")

    def test_rapid_enqueue_steal_interleaving(self):
        """10 producers + 10 consumers running simultaneously.

        All produced items should be consumed exactly once.
        """
        num_producers = 10
        num_consumers = 10
        items_per_producer = 10
        total_items = num_producers * items_per_producer

        barrier = threading.Barrier(num_producers + num_consumers)
        stolen_items = []
        stolen_lock = threading.Lock()
        errors = []

        def producer(idx):
            try:
                ws = WorkStealing(self.db_path, self.bus_dir, f"producer-{idx}", f"prod-agent-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for j in range(items_per_producer):
                    ws.enqueue_work(f"p{idx}-item-{j}", f"from producer {idx}", priority=5)
                ws.close()
            except Exception as e:
                with stolen_lock:
                    errors.append(("producer", idx, e))

        def consumer(idx):
            try:
                ws = WorkStealing(self.db_path, self.bus_dir, f"consumer-{idx}", f"cons-agent-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                local_stolen = []
                # Keep trying until we've collectively stolen enough
                for _ in range(total_items):
                    result = ws.steal_work()
                    if result is not None:
                        local_stolen.append(result)
                with stolen_lock:
                    stolen_items.extend(local_stolen)
                ws.close()
            except Exception as e:
                with stolen_lock:
                    errors.append(("consumer", idx, e))

        threads = []
        for i in range(num_producers):
            threads.append(threading.Thread(target=producer, args=(i,)))
        for i in range(num_consumers):
            threads.append(threading.Thread(target=consumer, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Drain remaining items
        while True:
            item = self.ws.steal_work()
            if item is None:
                break
            stolen_items.append(item)

        # All items should be consumed, no duplicates
        stolen_ids = [item["id"] for item in stolen_items]
        self.assertEqual(len(stolen_ids), len(set(stolen_ids)), "Duplicate steals detected")
        self.assertEqual(len(stolen_ids), total_items,
                         f"Expected {total_items} items stolen, got {len(stolen_ids)}")

    def test_priority_correctness_under_concurrent_load(self):
        """Items enqueued with different priorities are stolen in priority order
        even under concurrent enqueue pressure.
        """
        # Enqueue items with priorities 1-10 (multiple items per priority)
        for priority in range(1, 11):
            for j in range(5):
                self.ws.enqueue_work(f"pri-{priority}-{j}", "desc", priority=priority)

        # Steal all 50 items sequentially and verify ordering
        stolen_priorities = []
        while True:
            item = self.ws.steal_work()
            if item is None:
                break
            stolen_priorities.append(item["priority"])

        self.assertEqual(len(stolen_priorities), 50)
        # Priorities should be non-decreasing (ascending order)
        for i in range(1, len(stolen_priorities)):
            self.assertLessEqual(stolen_priorities[i - 1], stolen_priorities[i],
                                 f"Priority order violated at index {i}: "
                                 f"{stolen_priorities[i-1]} > {stolen_priorities[i]}")

    def test_1000_items_enqueued_and_stolen_in_order(self):
        """Enqueue 1000+ items and verify they are stolen in priority/creation order."""
        num_items = 1000
        for i in range(num_items):
            # All same priority so creation order (FIFO) should be maintained
            self.ws.enqueue_work(f"item-{i:04d}", "desc", priority=5)

        stolen_titles = []
        while True:
            item = self.ws.steal_work()
            if item is None:
                break
            stolen_titles.append(item["title"])

        self.assertEqual(len(stolen_titles), num_items)
        # With same priority, creation order should be maintained
        expected = [f"item-{i:04d}" for i in range(num_items)]
        self.assertEqual(stolen_titles, expected)

    def test_complete_fail_while_others_steal(self):
        """Complete/fail work items while other threads are stealing."""
        num_items = 20
        ids = []
        for i in range(num_items):
            wid = self.ws.enqueue_work(f"item-{i}", "desc", priority=5)
            ids.append(wid)

        barrier = threading.Barrier(3)
        errors = []
        stolen_items = []
        stolen_lock = threading.Lock()

        def stealer():
            try:
                ws = WorkStealing(self.db_path, self.bus_dir, "stealer", "stealer-agent")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for _ in range(num_items):
                    item = ws.steal_work()
                    if item:
                        with stolen_lock:
                            stolen_items.append(item)
                ws.close()
            except Exception as e:
                errors.append(("stealer", e))

        def completer():
            try:
                ws = WorkStealing(self.db_path, self.bus_dir, "completer", "completer-agent")
                barrier.wait(timeout=JOIN_TIMEOUT)
                # Steal and complete items
                for _ in range(num_items // 2):
                    item = ws.steal_work()
                    if item:
                        ws.complete_work(item["id"], {"done": True})
                        with stolen_lock:
                            stolen_items.append(item)
                ws.close()
            except Exception as e:
                errors.append(("completer", e))

        def failer():
            try:
                ws = WorkStealing(self.db_path, self.bus_dir, "failer", "failer-agent")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for _ in range(num_items // 2):
                    item = ws.steal_work()
                    if item:
                        ws.fail_work(item["id"], "intentional failure")
                        with stolen_lock:
                            stolen_items.append(item)
                ws.close()
            except Exception as e:
                errors.append(("failer", e))

        threads = [
            threading.Thread(target=stealer),
            threading.Thread(target=completer),
            threading.Thread(target=failer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Drain any remaining
        while True:
            item = self.ws.steal_work()
            if item is None:
                break
            stolen_items.append(item)

        # No duplicate steals
        stolen_ids = [item["id"] for item in stolen_items]
        self.assertEqual(len(stolen_ids), len(set(stolen_ids)), "Duplicate steals detected")
        # All items should be accounted for
        self.assertEqual(len(stolen_ids), num_items)

    def test_queue_depth_accuracy_during_concurrent_ops(self):
        """Queue depth should be accurate during concurrent enqueue/steal."""
        num_items = 50
        for i in range(num_items):
            self.ws.enqueue_work(f"item-{i}", "desc")

        self.assertEqual(self.ws.get_queue_depth(), num_items)

        # Steal half concurrently
        barrier = threading.Barrier(5)
        stolen_count = []
        stolen_lock = threading.Lock()

        def steal_some(idx):
            ws = WorkStealing(self.db_path, self.bus_dir, f"team-{idx}", f"agent-{idx}")
            barrier.wait(timeout=JOIN_TIMEOUT)
            count = 0
            for _ in range(num_items // 5):
                item = ws.steal_work()
                if item is not None:
                    count += 1
            with stolen_lock:
                stolen_count.append(count)
            ws.close()

        threads = [threading.Thread(target=steal_some, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        total_stolen = sum(stolen_count)
        remaining = self.ws.get_queue_depth()
        self.assertEqual(remaining, num_items - total_stolen,
                         f"Queue depth mismatch: {remaining} remaining but "
                         f"{num_items} - {total_stolen} = {num_items - total_stolen}")


# ===================================================================
# PipelineManager Stress Tests
# ===================================================================


class TestPipelineManagerStress(unittest.TestCase):
    """Stress tests for the PipelineManager."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "stress.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        self.pm = PipelineManager(self.db_path, self.bus_dir, "team-main", "agent-main")

    def tearDown(self):
        self.pm.close()

    def test_large_dag_20_stages(self):
        """Create a DAG with 20+ stages and complex dependency chains."""
        # Build a layered DAG:
        # Layer 0: stages 0-4 (no deps)
        # Layer 1: stages 5-9 (each depends on one from layer 0)
        # Layer 2: stages 10-14 (each depends on two from layer 1)
        # Layer 3: stages 15-19 (each depends on all of layer 2)
        # Layer 4: stage 20 (depends on all of layer 3)
        stages = []
        for i in range(5):
            stages.append({"name": f"s{i}"})
        for i in range(5, 10):
            stages.append({"name": f"s{i}", "depends_on": [f"s{i-5}"]})
        for i in range(10, 15):
            dep1 = f"s{5 + (i - 10)}"
            dep2 = f"s{5 + ((i - 10 + 1) % 5)}"
            stages.append({"name": f"s{i}", "depends_on": [dep1, dep2]})
        for i in range(15, 20):
            stages.append({"name": f"s{i}", "depends_on": [f"s{j}" for j in range(10, 15)]})
        stages.append({"name": "s20", "depends_on": [f"s{j}" for j in range(15, 20)]})

        pid = self.pm.create_pipeline("large-dag", stages)
        status = self.pm.get_pipeline_status(pid)
        self.assertEqual(len(status["stages"]), 21)

        # Layer 0 should be ready
        ready = self.pm.get_ready_stages(pid)
        ready_names = {s["stage_name"] for s in ready}
        for i in range(5):
            self.assertIn(f"s{i}", ready_names)

        # Complete all stages layer by layer
        for layer_start, layer_end in [(0, 5), (5, 10), (10, 15), (15, 20), (20, 21)]:
            for i in range(layer_start, layer_end):
                self.pm.start_stage(pid, f"s{i}")
                self.pm.complete_stage(pid, f"s{i}", {"layer": layer_start // 5})
                self.pm.trigger_downstream(pid, f"s{i}")

        final_status = self.pm.get_pipeline_status(pid)
        self.assertEqual(final_status["status"], "completed")

    def test_fan_out_1_triggers_10(self):
        """1 root stage triggers 10 downstream stages simultaneously."""
        stages = [{"name": "root"}]
        for i in range(10):
            stages.append({"name": f"leaf-{i}", "depends_on": ["root"]})

        pid = self.pm.create_pipeline("fan-out", stages)

        # Complete root
        self.pm.start_stage(pid, "root")
        self.pm.complete_stage(pid, "root", {"data": "seed"})
        newly_ready = self.pm.trigger_downstream(pid, "root")

        self.assertEqual(len(newly_ready), 10)
        for i in range(10):
            self.assertIn(f"leaf-{i}", newly_ready)

    def test_fan_in_10_to_1(self):
        """10 stages must complete before 1 final stage becomes ready."""
        stages = []
        dep_names = []
        for i in range(10):
            stages.append({"name": f"source-{i}"})
            dep_names.append(f"source-{i}")
        stages.append({"name": "sink", "depends_on": dep_names})

        pid = self.pm.create_pipeline("fan-in", stages)

        # Complete sources one by one
        for i in range(9):
            self.pm.start_stage(pid, f"source-{i}")
            self.pm.complete_stage(pid, f"source-{i}", {"idx": i})
            newly_ready = self.pm.trigger_downstream(pid, f"source-{i}")
            # Sink should NOT be ready until all 10 sources complete
            self.assertEqual(newly_ready, [], f"sink triggered early after source-{i}")

        # Complete last source
        self.pm.start_stage(pid, "source-9")
        self.pm.complete_stage(pid, "source-9", {"idx": 9})
        newly_ready = self.pm.trigger_downstream(pid, "source-9")
        self.assertEqual(newly_ready, ["sink"])

    def test_diamond_dependency(self):
        """A->B, A->C, B+C->D diamond pattern."""
        stages = [
            {"name": "A"},
            {"name": "B", "depends_on": ["A"]},
            {"name": "C", "depends_on": ["A"]},
            {"name": "D", "depends_on": ["B", "C"]},
        ]
        pid = self.pm.create_pipeline("diamond", stages)

        # Complete A, should unlock B and C
        self.pm.start_stage(pid, "A")
        self.pm.complete_stage(pid, "A", {})
        newly_ready = self.pm.trigger_downstream(pid, "A")
        self.assertEqual(sorted(newly_ready), ["B", "C"])

        # Complete B only - D should NOT be ready
        self.pm.start_stage(pid, "B")
        self.pm.complete_stage(pid, "B", {})
        newly_ready = self.pm.trigger_downstream(pid, "B")
        self.assertEqual(newly_ready, [])

        # Complete C - now D should be ready
        self.pm.start_stage(pid, "C")
        self.pm.complete_stage(pid, "C", {})
        newly_ready = self.pm.trigger_downstream(pid, "C")
        self.assertEqual(newly_ready, ["D"])

        # Complete D - pipeline done
        self.pm.start_stage(pid, "D")
        self.pm.complete_stage(pid, "D", {"final": True})
        status = self.pm.get_pipeline_status(pid)
        self.assertEqual(status["status"], "completed")

    def test_concurrent_stage_completions(self):
        """Multiple threads complete stages simultaneously, triggering downstream."""
        # Fan-in: 5 sources -> 1 sink
        stages = []
        for i in range(5):
            stages.append({"name": f"src-{i}"})
        stages.append({"name": "sink", "depends_on": [f"src-{i}" for i in range(5)]})

        pid = self.pm.create_pipeline("concurrent-complete", stages)
        barrier = threading.Barrier(5)
        all_newly_ready = []
        ready_lock = threading.Lock()
        errors = []

        def complete_stage(idx):
            try:
                pm = PipelineManager(self.db_path, self.bus_dir, f"team-{idx}", f"agent-{idx}")
                pm.start_stage(pid, f"src-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                pm.complete_stage(pid, f"src-{idx}", {"idx": idx})
                newly = pm.trigger_downstream(pid, f"src-{idx}")
                with ready_lock:
                    all_newly_ready.extend(newly)
                pm.close()
            except Exception as e:
                with ready_lock:
                    errors.append(e)

        threads = [threading.Thread(target=complete_stage, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        # Sink should appear exactly once in newly_ready across all threads
        self.assertEqual(all_newly_ready.count("sink"), 1,
                         f"sink triggered {all_newly_ready.count('sink')} times (expected 1)")

    def test_multiple_pipelines_simultaneously(self):
        """Create and process multiple pipelines concurrently."""
        num_pipelines = 5
        barrier = threading.Barrier(num_pipelines)
        completed_pipelines = []
        completed_lock = threading.Lock()
        errors = []

        def run_pipeline(idx):
            try:
                pm = PipelineManager(self.db_path, self.bus_dir, f"team-{idx}", f"agent-{idx}")
                stages = [
                    {"name": "start"},
                    {"name": "middle", "depends_on": ["start"]},
                    {"name": "end", "depends_on": ["middle"]},
                ]
                barrier.wait(timeout=JOIN_TIMEOUT)
                pid = pm.create_pipeline(f"pipe-{idx}", stages)

                for stage_name in ["start", "middle", "end"]:
                    pm.start_stage(pid, stage_name)
                    pm.complete_stage(pid, stage_name, {"stage": stage_name})
                    pm.trigger_downstream(pid, stage_name)

                status = pm.get_pipeline_status(pid)
                with completed_lock:
                    completed_pipelines.append((idx, status["status"]))
                pm.close()
            except Exception as e:
                with completed_lock:
                    errors.append((idx, e))

        threads = [threading.Thread(target=run_pipeline, args=(i,)) for i in range(num_pipelines)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(completed_pipelines), num_pipelines)
        for idx, status in completed_pipelines:
            self.assertEqual(status, "completed", f"Pipeline {idx} not completed: {status}")

    def test_very_long_pipeline_chain_50_stages(self):
        """A linear chain of 50+ sequential stages."""
        num_stages = 50
        stages = [{"name": "stage-0"}]
        for i in range(1, num_stages):
            stages.append({"name": f"stage-{i}", "depends_on": [f"stage-{i-1}"]})

        pid = self.pm.create_pipeline("long-chain", stages)

        # Walk through the entire chain
        for i in range(num_stages):
            stage_name = f"stage-{i}"
            if i > 0:
                self.assertTrue(self.pm.check_stage_ready(pid, stage_name),
                                f"{stage_name} should be ready")
            self.pm.start_stage(pid, stage_name)
            self.pm.complete_stage(pid, stage_name, {"step": i})
            if i < num_stages - 1:
                newly_ready = self.pm.trigger_downstream(pid, stage_name)
                self.assertEqual(newly_ready, [f"stage-{i+1}"])

        status = self.pm.get_pipeline_status(pid)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(len(status["stages"]), num_stages)


# ===================================================================
# Scratchpad Stress Tests
# ===================================================================


class TestScratchpadStress(unittest.TestCase):
    """Stress tests for the Scratchpad key-value store."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "stress.db")
        self.sp = Scratchpad(self.db_path, "team-main", "agent-main")

    def tearDown(self):
        self.sp.close()

    def test_20_threads_writing_same_key(self):
        """20 threads writing to the same key simultaneously. Last writer wins."""
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        errors = []

        def writer(idx):
            try:
                sp = Scratchpad(self.db_path, f"team-{idx}", f"agent-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                sp.write("contested-key", {"writer": idx}, namespace="global")
                sp.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Key should have exactly one value (from some writer)
        val = self.sp.read("contested-key", namespace="global")
        self.assertIsNotNone(val)
        self.assertIn("writer", val)
        self.assertIn(val["writer"], list(range(num_threads)))

    def test_write_1000_keys_read_all(self):
        """Write 1000+ keys rapidly and verify read_all correctness."""
        num_keys = 1000
        for i in range(num_keys):
            self.sp.write(f"key-{i:04d}", {"idx": i, "data": f"value-{i}"})

        result = self.sp.read_all()
        self.assertEqual(len(result), num_keys)
        for i in range(num_keys):
            key = f"key-{i:04d}"
            self.assertIn(key, result)
            self.assertEqual(result[key]["idx"], i)

    def test_ttl_expiration_during_concurrent_reads(self):
        """Write keys with short TTL and read them concurrently as they expire."""
        # Write keys with 1-second TTL
        for i in range(10):
            self.sp.write(f"ephemeral-{i}", f"value-{i}", ttl=1)

        # Immediately, all should be readable
        for i in range(10):
            val = self.sp.read(f"ephemeral-{i}")
            self.assertIsNotNone(val, f"ephemeral-{i} should be readable immediately")

        # Wait for expiry
        time.sleep(1.1)

        # Now start concurrent reads -- all should return None
        barrier = threading.Barrier(5)
        results = []
        results_lock = threading.Lock()

        def reader(idx):
            sp = Scratchpad(self.db_path, "team-main", f"reader-{idx}")
            barrier.wait(timeout=JOIN_TIMEOUT)
            local_results = []
            for i in range(10):
                val = sp.read(f"ephemeral-{i}")
                local_results.append((f"ephemeral-{i}", val))
            with results_lock:
                results.extend(local_results)
            sp.close()

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        # All reads should return None (expired)
        for key, val in results:
            self.assertIsNone(val, f"{key} should have expired but got {val}")

    def test_cross_team_reads_during_writes(self):
        """One team writes to global namespace while another reads concurrently."""
        num_keys = 100
        barrier = threading.Barrier(2)
        write_done = threading.Event()
        errors = []
        read_results = []
        read_lock = threading.Lock()

        def writer():
            try:
                sp = Scratchpad(self.db_path, "writer-team", "writer-agent")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for i in range(num_keys):
                    sp.write(f"shared-{i}", {"idx": i}, namespace="global")
                write_done.set()
                sp.close()
            except Exception as e:
                errors.append(("writer", e))
                write_done.set()

        def reader():
            try:
                sp = Scratchpad(self.db_path, "reader-team", "reader-agent")
                barrier.wait(timeout=JOIN_TIMEOUT)
                # Read while writes are happening
                while not write_done.is_set():
                    all_data = sp.read_all(namespace="global")
                    with read_lock:
                        read_results.append(len(all_data))
                # Final read after all writes done
                final = sp.read_all(namespace="global")
                with read_lock:
                    read_results.append(len(final))
                sp.close()
            except Exception as e:
                errors.append(("reader", e))

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join(timeout=JOIN_TIMEOUT)
        t_read.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        # read_results should be non-decreasing (we're only adding keys)
        for i in range(1, len(read_results)):
            self.assertGreaterEqual(read_results[i], read_results[i - 1],
                                    "Read-all count went backwards during concurrent writes")
        # Final read should have all keys
        self.assertEqual(read_results[-1], num_keys)

    def test_cleanup_expired_during_active_writes(self):
        """Run cleanup_expired while other threads are actively writing."""
        # Write some short-lived and long-lived entries
        for i in range(20):
            self.sp.write(f"short-{i}", f"val-{i}", ttl=1)
        for i in range(20):
            self.sp.write(f"long-{i}", f"val-{i}", ttl=3600)

        time.sleep(1.1)  # Let short-lived entries expire

        barrier = threading.Barrier(3)
        errors = []
        cleanup_count = [0]

        def write_new():
            try:
                sp = Scratchpad(self.db_path, "team-main", "writer")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for i in range(50):
                    sp.write(f"new-{i}", f"new-val-{i}", ttl=3600)
                sp.close()
            except Exception as e:
                errors.append(("writer", e))

        def do_cleanup():
            try:
                sp = Scratchpad(self.db_path, "team-main", "cleaner")
                barrier.wait(timeout=JOIN_TIMEOUT)
                deleted = sp.cleanup_expired()
                cleanup_count[0] = deleted
                sp.close()
            except Exception as e:
                errors.append(("cleaner", e))

        def read_existing():
            try:
                sp = Scratchpad(self.db_path, "team-main", "reader")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for i in range(20):
                    sp.read(f"long-{i}")
                sp.close()
            except Exception as e:
                errors.append(("reader", e))

        threads = [
            threading.Thread(target=write_new),
            threading.Thread(target=do_cleanup),
            threading.Thread(target=read_existing),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        # Cleanup should have deleted at least some expired entries
        self.assertGreaterEqual(cleanup_count[0], 0)

        # Long-lived entries should still be present
        for i in range(20):
            val = self.sp.read(f"long-{i}")
            self.assertEqual(val, f"val-{i}", f"long-{i} was incorrectly cleaned up")

    def test_large_values_1mb_json(self):
        """Write and read back large JSON values (~1MB each)."""
        # Create a ~1MB JSON object
        large_data = {
            "payload": "x" * (1024 * 1024),  # ~1MB string
            "metadata": {"size": "1MB", "test": True},
        }

        self.sp.write("big-key", large_data)
        retrieved = self.sp.read("big-key")

        self.assertIsNotNone(retrieved)
        self.assertEqual(len(retrieved["payload"]), 1024 * 1024)
        self.assertEqual(retrieved["metadata"]["size"], "1MB")

        # Write multiple large values
        for i in range(5):
            self.sp.write(f"big-{i}", {"data": "y" * (512 * 1024), "idx": i})

        all_data = self.sp.read_all()
        # 1 big-key + 5 big-N keys = 6 total
        self.assertEqual(len(all_data), 6)

    def test_namespace_isolation_under_concurrent_access(self):
        """Multiple teams write to their own namespaces concurrently.
        Verify no cross-contamination.
        """
        num_teams = 10
        keys_per_team = 50
        barrier = threading.Barrier(num_teams)
        errors = []

        def team_writer(team_idx):
            try:
                sp = Scratchpad(self.db_path, f"team-{team_idx}", f"agent-{team_idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for j in range(keys_per_team):
                    sp.write(f"key-{j}", {"team": team_idx, "key": j})
                sp.close()
            except Exception as e:
                errors.append((team_idx, e))

        threads = [threading.Thread(target=team_writer, args=(i,)) for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Verify isolation: each team's namespace has only its own data
        for team_idx in range(num_teams):
            sp = Scratchpad(self.db_path, f"team-{team_idx}", f"agent-{team_idx}")
            all_data = sp.read_all()
            self.assertEqual(len(all_data), keys_per_team,
                             f"team-{team_idx} has {len(all_data)} keys, expected {keys_per_team}")
            for key, val in all_data.items():
                self.assertEqual(val["team"], team_idx,
                                 f"Cross-contamination: team-{team_idx} namespace has data from team-{val['team']}")
            sp.close()


# ===================================================================
# Combined Stress Tests
# ===================================================================


class TestCombinedStress(unittest.TestCase):
    """Stress tests combining multiple primitives."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "stress.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")

    def test_work_stealing_plus_scratchpad(self):
        """Steal work items and write results to scratchpad. Verify all results."""
        ws = WorkStealing(self.db_path, self.bus_dir, "coordinator", "coord-agent")
        num_items = 30
        for i in range(num_items):
            ws.enqueue_work(f"compute-{i}", f"compute square of {i}", priority=5)

        barrier = threading.Barrier(5)
        errors = []

        def worker(idx):
            try:
                w = WorkStealing(self.db_path, self.bus_dir, f"worker-{idx}", f"worker-agent-{idx}")
                sp = Scratchpad(self.db_path, f"worker-{idx}", f"worker-agent-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                while True:
                    item = w.steal_work()
                    if item is None:
                        break
                    # Parse the item number from title "compute-N"
                    n = int(item["title"].split("-")[1])
                    result = {"input": n, "output": n * n}
                    w.complete_work(item["id"], result)
                    sp.write(f"result-{n}", result, namespace="global")
                w.close()
                sp.close()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Verify all results in scratchpad
        sp = Scratchpad(self.db_path, "verifier", "verify-agent")
        all_results = sp.read_all(namespace="global")
        self.assertEqual(len(all_results), num_items)
        for i in range(num_items):
            key = f"result-{i}"
            self.assertIn(key, all_results, f"Missing result for {key}")
            self.assertEqual(all_results[key]["output"], i * i,
                             f"Wrong result for {key}: expected {i*i}, got {all_results[key]['output']}")
        sp.close()
        ws.close()

    def test_pipeline_plus_work_stealing(self):
        """Pipeline stages enqueue work items for workers to process."""
        pm = PipelineManager(self.db_path, self.bus_dir, "orchestrator", "orch-agent")
        ws = WorkStealing(self.db_path, self.bus_dir, "orchestrator", "orch-agent")

        # Create a 3-stage pipeline
        pid = pm.create_pipeline("data-pipeline", [
            {"name": "generate"},
            {"name": "process", "depends_on": ["generate"]},
            {"name": "aggregate", "depends_on": ["process"]},
        ])

        # Stage 1: generate - enqueue 10 work items
        pm.start_stage(pid, "generate")
        for i in range(10):
            ws.enqueue_work(f"gen-{i}", f"generate data {i}", priority=5)
        pm.complete_stage(pid, "generate", {"items_generated": 10})
        pm.trigger_downstream(pid, "generate")

        # Stage 2: process - steal and process all items
        pm.start_stage(pid, "process")
        processed = 0
        while True:
            item = ws.steal_work()
            if item is None:
                break
            ws.complete_work(item["id"], {"processed": True})
            processed += 1

        self.assertEqual(processed, 10)
        pm.complete_stage(pid, "process", {"items_processed": processed})
        pm.trigger_downstream(pid, "process")

        # Stage 3: aggregate
        pm.start_stage(pid, "aggregate")
        pm.complete_stage(pid, "aggregate", {"total": processed})

        status = pm.get_pipeline_status(pid)
        self.assertEqual(status["status"], "completed")

        pm.close()
        ws.close()


if __name__ == "__main__":
    unittest.main()
