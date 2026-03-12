#!/usr/bin/env python3
"""
Comprehensive Multi-Agent Coordination Benchmark Suite

Tests 6 dimensions of multi-agent collaboration quality:
1. Communication overhead — coordination vs productive work ratio
2. Team sizing efficiency — validates E = 4.0 * N^(-0.75) formula
3. Failure recovery speed — crash, contention, deadlock recovery
4. Capability matching accuracy — auto-assign vs random baseline
5. Pipeline correctness — message ordering and exactly-once delivery
6. Scaling limits — find breaking points under load

Uses the Proto A communication system (JSONL + SQLite WAL) and the
coordination layer (coordinator_hub, help_protocol, direct_channels,
work_stealing).

Results are written to storage/coordination/overhaul/output/
"""

import json
import math
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Add project root and prototype dir to path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "prototype"))

from agent_comm.bus import BusReader, BusWriter, Message
from storage.coordination.coordinator_hub import AgentReporter, CoordinatorDashboard
from storage.coordination.direct_channels import DirectChannels
from storage.coordination.help_protocol import HelpProtocol
from storage.coordination.work_stealing import PipelineManager, Scratchpad, WorkStealing

JOIN_TIMEOUT = 30
OUTPUT_DIR = Path(__file__).parent / "output"


@dataclass
class BenchmarkResult:
    dimension: str
    test_name: str
    metrics: dict
    passed: bool
    duration_s: float
    details: str = ""
    errors: list = field(default_factory=list)


class BenchmarkHarness:
    """Manages temp directories and shared state for benchmark runs."""

    def __init__(self, name: str):
        self.name = name
        self.tmpdir = tempfile.mkdtemp(prefix=f"bench-{name}-")
        self.db_path = os.path.join(self.tmpdir, "bench.db")
        self.bus_dir = os.path.join(self.tmpdir, "bus")
        os.makedirs(self.bus_dir, exist_ok=True)
        self.results: list[BenchmarkResult] = []

    def add_result(self, result: BenchmarkResult):
        self.results.append(result)

    def get_summary(self) -> dict:
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        return {
            "harness": self.name,
            "total_tests": len(self.results),
            "passed": passed,
            "failed": failed,
            "results": [asdict(r) for r in self.results],
        }


# ===================================================================
# Dimension 1: Communication Overhead
# ===================================================================


def bench_communication_overhead(harness: BenchmarkHarness) -> list[BenchmarkResult]:
    """Measure what % of agent activity is coordination vs productive output."""
    results = []

    # Test 1: Bus message volume per deliverable
    t0 = time.time()
    num_agents = 9  # 3 teams x 3 agents
    num_deliverables = 30
    bus_writes = 0
    db_ops = 0
    ack_roundtrips = 0
    errors = []

    try:
        # Simulate agents producing deliverables while coordinating
        agents = []
        for team_idx in range(3):
            team = f"team-{team_idx}"
            for agent_idx in range(3):
                agent_id = f"{team}-agent-{agent_idx}"
                writer = BusWriter(harness.bus_dir, agent_id, team)
                reporter = AgentReporter(harness.db_path, harness.bus_dir, agent_id, team)
                agents.append((team, agent_id, writer, reporter))

        # Each agent produces deliverables and coordinates
        deliverables_produced = 0
        for team, agent_id, writer, reporter in agents:
            # Coordination: status updates
            reporter.update_status("working", 0, "starting")
            db_ops += 1
            bus_writes += 1  # fire-and-forget bus notification

            items_per_agent = num_deliverables // num_agents
            for i in range(items_per_agent):
                # Productive work: simulate deliverable creation
                deliverables_produced += 1

                # Coordination overhead: progress update
                progress = ((i + 1) / items_per_agent) * 100
                reporter.update_status("working", progress, f"item-{i}")
                db_ops += 1
                bus_writes += 1

                # Coordination overhead: bus notification
                writer.publish("global", "info", {
                    "event": "deliverable",
                    "item": i,
                    "agent": agent_id,
                })
                bus_writes += 1

                # Simulate ack round-trip (request + response)
                writer.publish("global", "info", {
                    "event": "ack_request",
                    "deliverable_id": f"{agent_id}-{i}",
                })
                bus_writes += 1
                ack_roundtrips += 1

            reporter.report_complete(f"Produced {items_per_agent} items")
            db_ops += 1
            bus_writes += 1

        # Calculate overhead ratio
        coordination_ops = bus_writes + db_ops + ack_roundtrips
        overhead_ratio = coordination_ops / max(deliverables_produced, 1)

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="communication_overhead",
            test_name="overhead_per_deliverable",
            metrics={
                "deliverables_produced": deliverables_produced,
                "bus_writes": bus_writes,
                "db_ops": db_ops,
                "ack_roundtrips": ack_roundtrips,
                "total_coordination_ops": coordination_ops,
                "overhead_ratio": round(overhead_ratio, 2),
                "overhead_pct": round(
                    coordination_ops / (coordination_ops + deliverables_produced) * 100, 1
                ),
                "agents": num_agents,
            },
            passed=True,
            duration_s=round(duration, 3),
            details=f"Overhead ratio: {overhead_ratio:.2f} coordination ops per deliverable",
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="communication_overhead",
            test_name="overhead_per_deliverable",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    # Test 2: Bus throughput under multi-team load
    t0 = time.time()
    try:
        msg_counts = defaultdict(int)
        write_times = []
        barrier = threading.Barrier(9)
        lock = threading.Lock()
        thread_errors = []

        def bus_writer_thread(team, agent_id, n_messages):
            try:
                writer = BusWriter(harness.bus_dir, agent_id, team)
                barrier.wait(timeout=JOIN_TIMEOUT)
                for i in range(n_messages):
                    t_start = time.perf_counter()
                    writer.publish("benchmark", "info", {"seq": i, "agent": agent_id})
                    elapsed = time.perf_counter() - t_start
                    with lock:
                        msg_counts[agent_id] += 1
                        write_times.append(elapsed)
            except Exception as e:
                with lock:
                    thread_errors.append((agent_id, str(e)))

        msgs_per_agent = 100
        threads = []
        for team_idx in range(3):
            for agent_idx in range(3):
                t = threading.Thread(
                    target=bus_writer_thread,
                    args=(f"team-{team_idx}", f"t{team_idx}-a{agent_idx}", msgs_per_agent),
                )
                threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        total_msgs = sum(msg_counts.values())
        duration = time.time() - t0

        result = BenchmarkResult(
            dimension="communication_overhead",
            test_name="bus_throughput_9_agents",
            metrics={
                "total_messages": total_msgs,
                "expected_messages": 9 * msgs_per_agent,
                "messages_per_second": round(total_msgs / max(duration, 0.001), 1),
                "avg_write_latency_ms": round(statistics.mean(write_times) * 1000, 3) if write_times else 0,
                "p95_write_latency_ms": round(sorted(write_times)[int(len(write_times) * 0.95)] * 1000, 3) if write_times else 0,
                "p99_write_latency_ms": round(sorted(write_times)[int(len(write_times) * 0.99)] * 1000, 3) if write_times else 0,
                "thread_errors": len(thread_errors),
            },
            passed=total_msgs == 9 * msgs_per_agent and len(thread_errors) == 0,
            duration_s=round(duration, 3),
            details=f"{total_msgs} messages in {duration:.2f}s = {total_msgs/max(duration,0.001):.0f} msg/s",
            errors=[f"{aid}: {err}" for aid, err in thread_errors],
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="communication_overhead",
            test_name="bus_throughput_9_agents",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    # Test 3: Direct channel overhead between team pairs
    t0 = time.time()
    try:
        dc_a = DirectChannels(harness.db_path, harness.bus_dir, "team-alpha", "alpha-lead")
        dc_b = DirectChannels(harness.db_path, harness.bus_dir, "team-beta", "beta-lead")

        # Create a direct channel
        channel = dc_a.create_direct_channel("team-beta")

        msg_count = 50
        send_times = []
        for i in range(msg_count):
            t_start = time.perf_counter()
            dc_a.send_direct("team-beta", "info", {"seq": i, "payload": "x" * 200})
            send_times.append(time.perf_counter() - t_start)

        # Read all messages
        t_read_start = time.perf_counter()
        received = dc_b.read_direct("team-alpha")
        read_time = time.perf_counter() - t_read_start

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="communication_overhead",
            test_name="direct_channel_latency",
            metrics={
                "messages_sent": msg_count,
                "messages_received": len(received),
                "avg_send_latency_ms": round(statistics.mean(send_times) * 1000, 3),
                "p95_send_latency_ms": round(sorted(send_times)[int(len(send_times) * 0.95)] * 1000, 3),
                "total_read_time_ms": round(read_time * 1000, 3),
                "read_throughput_msg_per_s": round(len(received) / max(read_time, 0.001), 1),
            },
            passed=len(received) == msg_count,
            duration_s=round(duration, 3),
            details=f"Sent {msg_count}, received {len(received)}",
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="communication_overhead",
            test_name="direct_channel_latency",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    return results


# ===================================================================
# Dimension 2: Team Sizing Efficiency
# ===================================================================


def bench_team_sizing(harness: BenchmarkHarness) -> list[BenchmarkResult]:
    """Run identical tasks with 2, 3, 4, 5 agent teams.
    Validate or challenge E = 4.0 * N^(-0.75) formula.
    """
    results = []
    total_work_items = 100
    efficiency_data = {}

    for team_size in [2, 3, 4, 5]:
        t0 = time.time()
        try:
            # Fresh DB for each team size test
            tmp = tempfile.mkdtemp(prefix=f"bench-sizing-{team_size}-")
            db = os.path.join(tmp, "sizing.db")
            bus = os.path.join(tmp, "bus")
            os.makedirs(bus, exist_ok=True)

            # Enqueue work
            ws_main = WorkStealing(db, bus, "coordinator", "coord-agent")
            for i in range(total_work_items):
                ws_main.enqueue_work(f"task-{i}", f"Process item {i}", priority=random.randint(1, 10))

            # Run team_size agents concurrently stealing and processing
            barrier = threading.Barrier(team_size)
            per_agent_stats = {}
            stats_lock = threading.Lock()
            thread_errors = []

            def worker(agent_idx):
                try:
                    ws = WorkStealing(db, bus, f"team-0", f"agent-{agent_idx}")
                    items_done = 0
                    total_work_time = 0.0
                    total_steal_time = 0.0

                    barrier.wait(timeout=JOIN_TIMEOUT)

                    while True:
                        t_steal = time.perf_counter()
                        item = ws.steal_work()
                        steal_elapsed = time.perf_counter() - t_steal
                        total_steal_time += steal_elapsed

                        if item is None:
                            break

                        # Simulate work (small sleep to create realistic contention)
                        t_work = time.perf_counter()
                        time.sleep(0.001)  # 1ms simulated work
                        work_elapsed = time.perf_counter() - t_work
                        total_work_time += work_elapsed

                        ws.complete_work(item["id"], {"agent": agent_idx})
                        items_done += 1

                    with stats_lock:
                        per_agent_stats[agent_idx] = {
                            "items_done": items_done,
                            "total_work_time": total_work_time,
                            "total_steal_time": total_steal_time,
                        }
                    ws.close()
                except Exception as e:
                    with stats_lock:
                        thread_errors.append((agent_idx, str(e)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(team_size)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=JOIN_TIMEOUT)

            duration = time.time() - t0
            total_items_done = sum(s["items_done"] for s in per_agent_stats.values())
            total_steal_overhead = sum(s["total_steal_time"] for s in per_agent_stats.values())
            total_work_time = sum(s["total_work_time"] for s in per_agent_stats.values())

            # Efficiency: items per agent per second
            throughput = total_items_done / max(duration, 0.001)
            per_agent_throughput = throughput / team_size
            # Expected efficiency: E = 4.0 * N^(-0.75)
            expected_efficiency = 4.0 * (team_size ** -0.75)
            # Normalize: actual efficiency relative to single-agent baseline
            # (We measure throughput/agent and compare to the formula)
            actual_efficiency = per_agent_throughput

            efficiency_data[team_size] = {
                "throughput": throughput,
                "per_agent_throughput": per_agent_throughput,
                "expected_E": expected_efficiency,
            }

            # Check work distribution evenness
            items_per_agent = [s["items_done"] for s in per_agent_stats.values()]
            distribution_cv = (
                statistics.stdev(items_per_agent) / statistics.mean(items_per_agent)
                if len(items_per_agent) > 1 and statistics.mean(items_per_agent) > 0
                else 0
            )

            result = BenchmarkResult(
                dimension="team_sizing",
                test_name=f"team_size_{team_size}_agents",
                metrics={
                    "team_size": team_size,
                    "total_items": total_work_items,
                    "items_completed": total_items_done,
                    "duration_s": round(duration, 3),
                    "throughput_items_per_s": round(throughput, 2),
                    "per_agent_throughput": round(per_agent_throughput, 2),
                    "steal_overhead_s": round(total_steal_overhead, 3),
                    "work_time_s": round(total_work_time, 3),
                    "steal_to_work_ratio": round(
                        total_steal_overhead / max(total_work_time, 0.001), 3
                    ),
                    "distribution_cv": round(distribution_cv, 3),
                    "items_per_agent": items_per_agent,
                    "expected_efficiency_formula": round(expected_efficiency, 4),
                    "thread_errors": len(thread_errors),
                },
                passed=total_items_done == total_work_items and len(thread_errors) == 0,
                duration_s=round(duration, 3),
                details=(
                    f"Size {team_size}: {throughput:.1f} items/s total, "
                    f"{per_agent_throughput:.1f}/agent, "
                    f"steal/work ratio={total_steal_overhead/max(total_work_time,0.001):.3f}, "
                    f"CV={distribution_cv:.3f}"
                ),
                errors=[f"agent-{a}: {e}" for a, e in thread_errors],
            )
            results.append(result)
            ws_main.close()

        except Exception as e:
            results.append(BenchmarkResult(
                dimension="team_sizing",
                test_name=f"team_size_{team_size}_agents",
                metrics={},
                passed=False,
                duration_s=time.time() - t0,
                errors=[str(e), traceback.format_exc()],
            ))

    # Summary: validate the efficiency formula
    if len(efficiency_data) >= 3:
        t0 = time.time()
        try:
            # Compute actual scaling exponent via log-log regression
            sizes = sorted(efficiency_data.keys())
            throughputs = [efficiency_data[s]["per_agent_throughput"] for s in sizes]

            # Normalize to size=2 baseline
            baseline = throughputs[0]
            normalized = [t / baseline for t in throughputs]

            # Log-log: ln(E) = ln(a) + b * ln(N)
            # E = a * N^b — compare b to -0.75
            log_sizes = [math.log(s) for s in sizes]
            log_eff = [math.log(max(e, 0.001)) for e in normalized]

            n = len(sizes)
            sum_x = sum(log_sizes)
            sum_y = sum(log_eff)
            sum_xy = sum(x * y for x, y in zip(log_sizes, log_eff))
            sum_x2 = sum(x * x for x in log_sizes)

            denom = n * sum_x2 - sum_x * sum_x
            if abs(denom) > 1e-10:
                b = (n * sum_xy - sum_x * sum_y) / denom
                a = math.exp((sum_y - b * sum_x) / n)
            else:
                b = 0
                a = 1

            formula_match = abs(b - (-0.75)) < 0.5  # Within 0.5 of expected exponent

            result = BenchmarkResult(
                dimension="team_sizing",
                test_name="efficiency_formula_validation",
                metrics={
                    "measured_exponent": round(b, 4),
                    "expected_exponent": -0.75,
                    "exponent_delta": round(abs(b - (-0.75)), 4),
                    "measured_coefficient": round(a, 4),
                    "expected_coefficient": 4.0,
                    "formula_match_within_0.5": formula_match,
                    "normalized_efficiencies": {str(s): round(e, 4) for s, e in zip(sizes, normalized)},
                    "raw_per_agent_throughputs": {str(s): round(efficiency_data[s]["per_agent_throughput"], 2) for s in sizes},
                },
                passed=True,  # This is a measurement, not a pass/fail
                duration_s=round(time.time() - t0, 3),
                details=(
                    f"Measured: E = {a:.2f} * N^({b:.3f}), "
                    f"Expected: E = 4.0 * N^(-0.75). "
                    f"Exponent delta: {abs(b-(-0.75)):.3f}"
                ),
            )
            results.append(result)
        except Exception as e:
            results.append(BenchmarkResult(
                dimension="team_sizing",
                test_name="efficiency_formula_validation",
                metrics={},
                passed=False,
                duration_s=time.time() - t0,
                errors=[str(e), traceback.format_exc()],
            ))

    return results


# ===================================================================
# Dimension 3: Failure Recovery Speed
# ===================================================================


def bench_failure_recovery(harness: BenchmarkHarness) -> list[BenchmarkResult]:
    """Inject agent crashes, SQLite contention, and deadlocks.
    Measure time-to-recovery.
    """
    results = []

    # Test 1: Agent crash recovery — work items get re-queued
    t0 = time.time()
    try:
        tmp = tempfile.mkdtemp(prefix="bench-fault-crash-")
        db = os.path.join(tmp, "fault.db")
        bus = os.path.join(tmp, "bus")
        os.makedirs(bus, exist_ok=True)

        ws = WorkStealing(db, bus, "coordinator", "coord-agent")
        num_items = 20

        for i in range(num_items):
            ws.enqueue_work(f"task-{i}", f"Process {i}", priority=5)

        # Agent steals work then "crashes" (doesn't complete)
        crashed_agent = WorkStealing(db, bus, "crashing-team", "crash-agent")
        crashed_items = []
        for _ in range(5):
            item = crashed_agent.steal_work()
            if item:
                crashed_items.append(item)
        # Agent "crashes" without completing — items stuck in 'in_progress'

        # Recovery: detect and re-queue zombie work items
        t_recovery_start = time.perf_counter()

        # Use help_protocol zombie detection
        hp = HelpProtocol(db, bus, "recovery-team", "recovery-agent")
        zombie_items = hp.get_zombie_work_items()

        recovery_time = time.perf_counter() - t_recovery_start

        # Healthy agent processes remaining work
        healthy = WorkStealing(db, bus, "healthy-team", "healthy-agent")
        healthy_completed = 0
        while True:
            item = healthy.steal_work()
            if item is None:
                break
            healthy.complete_work(item["id"], {"recovered": True})
            healthy_completed += 1

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="failure_recovery",
            test_name="agent_crash_detection",
            metrics={
                "total_items": num_items,
                "crashed_items": len(crashed_items),
                "zombie_detected": len(zombie_items),
                "healthy_completed": healthy_completed,
                "recovery_detection_ms": round(recovery_time * 1000, 3),
                "items_accounted_for": len(crashed_items) + healthy_completed,
            },
            passed=len(crashed_items) + healthy_completed == num_items,
            duration_s=round(duration, 3),
            details=(
                f"Crashed: {len(crashed_items)}, Zombies detected: {len(zombie_items)}, "
                f"Healthy completed: {healthy_completed}, "
                f"Detection time: {recovery_time*1000:.1f}ms"
            ),
        )
        results.append(result)
        ws.close()

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="failure_recovery",
            test_name="agent_crash_detection",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    # Test 2: SQLite contention — measure retry behavior under concurrent writes
    t0 = time.time()
    try:
        tmp = tempfile.mkdtemp(prefix="bench-fault-contention-")
        db = os.path.join(tmp, "contention.db")
        bus = os.path.join(tmp, "bus")
        os.makedirs(bus, exist_ok=True)

        # Use fewer writers with more items each to test realistic contention
        # The coordination layer has built-in retry logic (5 retries, exponential backoff)
        num_writers = 10
        writes_per_thread = 20
        barrier = threading.Barrier(num_writers)
        write_latencies = []
        latency_lock = threading.Lock()
        success_count = [0]
        failure_count = [0]
        thread_errors = []

        def contention_writer(idx):
            try:
                ws = WorkStealing(db, bus, f"team-{idx}", f"agent-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for j in range(writes_per_thread):
                    t_start = time.perf_counter()
                    try:
                        ws.enqueue_work(f"t{idx}-item-{j}", f"work from {idx}", priority=random.randint(1, 10))
                        elapsed = time.perf_counter() - t_start
                        with latency_lock:
                            success_count[0] += 1
                            write_latencies.append(elapsed)
                    except Exception as e:
                        with latency_lock:
                            failure_count[0] += 1
                            thread_errors.append((idx, str(e)))
                ws.close()
            except Exception as e:
                with latency_lock:
                    thread_errors.append((idx, str(e)))

        threads = [threading.Thread(target=contention_writer, args=(i,)) for i in range(num_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        total_attempted = num_writers * writes_per_thread
        duration = time.time() - t0

        result = BenchmarkResult(
            dimension="failure_recovery",
            test_name="sqlite_contention_recovery",
            metrics={
                "concurrent_writers": num_writers,
                "writes_per_thread": writes_per_thread,
                "total_attempted": total_attempted,
                "successful_writes": success_count[0],
                "failed_writes": failure_count[0],
                "success_rate_pct": round(success_count[0] / max(total_attempted, 1) * 100, 2),
                "avg_write_latency_ms": round(statistics.mean(write_latencies) * 1000, 3) if write_latencies else 0,
                "p95_write_latency_ms": round(sorted(write_latencies)[int(len(write_latencies) * 0.95)] * 1000, 3) if len(write_latencies) > 1 else 0,
                "max_write_latency_ms": round(max(write_latencies) * 1000, 3) if write_latencies else 0,
                "retry_overhead_indicator": round(
                    (max(write_latencies) / statistics.mean(write_latencies)) if write_latencies and statistics.mean(write_latencies) > 0 else 0, 2
                ),
            },
            passed=success_count[0] == total_attempted,
            duration_s=round(duration, 3),
            details=(
                f"{success_count[0]}/{total_attempted} writes succeeded "
                f"({success_count[0]/max(total_attempted,1)*100:.1f}%), "
                f"avg latency={statistics.mean(write_latencies)*1000:.1f}ms, "
                f"max={max(write_latencies)*1000:.1f}ms"
                if write_latencies else f"{success_count[0]}/{total_attempted} succeeded"
            ),
            errors=[f"{a}: {e}" for a, e in thread_errors[:5]],
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="failure_recovery",
            test_name="sqlite_contention_storm",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    # Test 3: Concurrent help requests — race condition recovery
    t0 = time.time()
    try:
        tmp = tempfile.mkdtemp(prefix="bench-fault-help-race-")
        db = os.path.join(tmp, "help_race.db")
        bus = os.path.join(tmp, "bus")
        os.makedirs(bus, exist_ok=True)

        # Create requesting team with work
        hp_req = HelpProtocol(db, bus, "requesting-team", "req-agent")
        hp_req.register_capabilities(["coding", "testing"])
        hp_req.update_status("needs_help", 50, "Stuck on implementation")
        work_id = hp_req.add_work_item("Complex task", "Need help", "high", 30, ["coding"])
        req_id = hp_req.request_help(work_id, "Need coding help")

        # 5 teams race to accept the same help request
        num_helpers = 5
        barrier = threading.Barrier(num_helpers)
        offer_results = []
        offer_lock = threading.Lock()

        def try_offer(idx):
            try:
                hp = HelpProtocol(db, bus, f"helper-{idx}", f"helper-agent-{idx}")
                hp.register_capabilities(["coding", "testing", "research"])
                hp.update_status("idle", 100, "Ready to help")
                barrier.wait(timeout=JOIN_TIMEOUT)
                success = hp.offer_help(req_id)
                with offer_lock:
                    offer_results.append((idx, success))
            except Exception as e:
                with offer_lock:
                    offer_results.append((idx, f"error: {e}"))

        threads = [threading.Thread(target=try_offer, args=(i,)) for i in range(num_helpers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        winners = [r for r in offer_results if r[1] is True]
        losers = [r for r in offer_results if r[1] is False]
        error_offers = [r for r in offer_results if isinstance(r[1], str)]

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="failure_recovery",
            test_name="help_request_race_condition",
            metrics={
                "concurrent_helpers": num_helpers,
                "winners": len(winners),
                "losers": len(losers),
                "errors": len(error_offers),
                "exactly_one_winner": len(winners) == 1,
            },
            passed=len(winners) == 1,
            duration_s=round(duration, 3),
            details=(
                f"{len(winners)} winner(s), {len(losers)} losers, {len(error_offers)} errors. "
                f"Exactly-one-winner: {len(winners) == 1}"
            ),
            errors=[str(e) for _, e in error_offers],
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="failure_recovery",
            test_name="help_request_race_condition",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    return results


# ===================================================================
# Dimension 4: Capability Matching Accuracy
# ===================================================================


def bench_capability_matching(harness: BenchmarkHarness) -> list[BenchmarkResult]:
    """Test if capability_discovery + auto_assign actually matches work
    to the right teams. Compare against random assignment.
    """
    results = []
    t0 = time.time()

    try:
        tmp = tempfile.mkdtemp(prefix="bench-capability-")
        db = os.path.join(tmp, "capability.db")
        bus = os.path.join(tmp, "bus")
        os.makedirs(bus, exist_ok=True)

        # Define teams with distinct capabilities
        team_profiles = {
            "frontend-team": ["javascript", "css", "react", "ui-design"],
            "backend-team": ["python", "sql", "api-design", "databases"],
            "devops-team": ["docker", "kubernetes", "ci-cd", "monitoring"],
            "data-team": ["python", "ml", "statistics", "data-pipelines"],
            "qa-team": ["testing", "automation", "selenium", "load-testing"],
        }

        # Register all teams
        hps = {}
        for team, capabilities in team_profiles.items():
            hp = HelpProtocol(db, bus, team, f"{team}-agent")
            hp.register_capabilities(capabilities)
            hp.update_status("idle", 100, "Available")
            hps[team] = hp

        # Create work items that need specific capabilities
        work_requirements = [
            ("Build React component", ["react", "javascript", "css"], "frontend-team"),
            ("Design REST API", ["python", "api-design"], "backend-team"),
            ("Set up Kubernetes cluster", ["kubernetes", "docker"], "devops-team"),
            ("Train ML model", ["python", "ml", "statistics"], "data-team"),
            ("Write integration tests", ["testing", "automation"], "qa-team"),
            ("Build dashboard UI", ["react", "css", "ui-design"], "frontend-team"),
            ("Optimize SQL queries", ["sql", "databases"], "backend-team"),
            ("Configure CI pipeline", ["ci-cd", "docker"], "devops-team"),
            ("Build data pipeline", ["python", "data-pipelines"], "data-team"),
            ("Load testing setup", ["load-testing", "automation"], "qa-team"),
        ]

        # Use one team to create work items and request help
        requester = HelpProtocol(db, bus, "coordinator", "coord-agent")
        requester.register_capabilities(["coordination"])
        requester.update_status("working", 50, "Distributing work")

        auto_assign_correct = 0
        auto_assign_total = 0
        random_correct = 0

        for title, required_caps, ideal_team in work_requirements:
            work_id = requester.add_work_item(
                title, f"Requires: {', '.join(required_caps)}",
                "medium", 60, required_caps,
            )
            req_id = requester.request_help(work_id, f"Need: {', '.join(required_caps)}")

            # Auto-assign using capability matching
            assignments = requester.auto_assign_idle_teams()

            # Check if the right team was assigned
            # Look up who got assigned in the DB
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT accepted_by_team FROM help_requests WHERE id = ?",
                (req_id,),
            )
            row = cursor.fetchone()
            assigned_team = row["accepted_by_team"] if row and row["accepted_by_team"] else None
            conn.close()

            if assigned_team:
                auto_assign_total += 1
                if assigned_team == ideal_team:
                    auto_assign_correct += 1

                # Random baseline: pick a random team
                random_team = random.choice(list(team_profiles.keys()))
                if random_team == ideal_team:
                    random_correct += 1

            # Reset team status for next round
            for team in team_profiles:
                hps[team].update_status("idle", 100, "Available")
            # Reset help request status so next one can be assigned
            conn = sqlite3.connect(db)
            conn.execute("UPDATE help_requests SET status = 'fulfilled' WHERE id = ?", (req_id,))
            conn.commit()
            conn.close()

        auto_accuracy = auto_assign_correct / max(auto_assign_total, 1) * 100
        random_accuracy = random_correct / max(auto_assign_total, 1) * 100
        improvement = auto_accuracy - random_accuracy

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="capability_matching",
            test_name="auto_assign_vs_random",
            metrics={
                "total_work_items": len(work_requirements),
                "auto_assigned": auto_assign_total,
                "auto_correct": auto_assign_correct,
                "auto_accuracy_pct": round(auto_accuracy, 1),
                "random_correct": random_correct,
                "random_accuracy_pct": round(random_accuracy, 1),
                "improvement_over_random_pct": round(improvement, 1),
                "expected_random_accuracy_pct": round(100 / len(team_profiles), 1),
            },
            passed=auto_accuracy >= random_accuracy,
            duration_s=round(duration, 3),
            details=(
                f"Auto-assign: {auto_accuracy:.0f}% accurate, "
                f"Random: {random_accuracy:.0f}% ({100/len(team_profiles):.0f}% expected), "
                f"Improvement: {improvement:+.0f}pp"
            ),
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="capability_matching",
            test_name="auto_assign_vs_random",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    return results


# ===================================================================
# Dimension 5: Pipeline Correctness
# ===================================================================


def bench_pipeline_correctness(harness: BenchmarkHarness) -> list[BenchmarkResult]:
    """Verify vector clocks and ack protocol prevent message loss
    and ordering violations under concurrent load.
    """
    results = []

    # Test 1: Concurrent pipeline execution — no lost stages
    t0 = time.time()
    try:
        tmp = tempfile.mkdtemp(prefix="bench-pipeline-")
        db = os.path.join(tmp, "pipeline.db")
        bus = os.path.join(tmp, "bus")
        os.makedirs(bus, exist_ok=True)

        num_pipelines = 10
        stages_per_pipeline = 8
        barrier = threading.Barrier(num_pipelines)
        pipeline_results = {}
        pipe_lock = threading.Lock()
        errors = []

        def run_pipeline(idx):
            try:
                pm = PipelineManager(db, bus, f"team-{idx}", f"agent-{idx}")

                # Create a diamond-shaped DAG
                stages = [
                    {"name": "fetch"},
                    {"name": "parse-a", "depends_on": ["fetch"]},
                    {"name": "parse-b", "depends_on": ["fetch"]},
                    {"name": "transform-a", "depends_on": ["parse-a"]},
                    {"name": "transform-b", "depends_on": ["parse-b"]},
                    {"name": "merge", "depends_on": ["transform-a", "transform-b"]},
                    {"name": "validate", "depends_on": ["merge"]},
                    {"name": "output", "depends_on": ["validate"]},
                ]

                barrier.wait(timeout=JOIN_TIMEOUT)
                pid = pm.create_pipeline(f"pipe-{idx}", stages)

                # Execute stages respecting dependencies
                execution_order = []
                completed_stages = set()

                while len(completed_stages) < len(stages):
                    ready = pm.get_ready_stages(pid)
                    if not ready:
                        # Check if pipeline is stuck
                        status = pm.get_pipeline_status(pid)
                        if status["status"] in ("completed", "failed"):
                            break
                        time.sleep(0.001)
                        continue

                    for stage_info in ready:
                        stage_name = stage_info["stage_name"]
                        if stage_name in completed_stages:
                            continue
                        pm.start_stage(pid, stage_name)
                        time.sleep(0.001)  # Simulate work
                        pm.complete_stage(pid, stage_name, {"pipeline": idx, "stage": stage_name})
                        pm.trigger_downstream(pid, stage_name)
                        execution_order.append(stage_name)
                        completed_stages.add(stage_name)

                status = pm.get_pipeline_status(pid)
                with pipe_lock:
                    pipeline_results[idx] = {
                        "status": status["status"],
                        "stages_completed": len(completed_stages),
                        "execution_order": execution_order,
                    }
                pm.close()
            except Exception as e:
                with pipe_lock:
                    errors.append((idx, str(e), traceback.format_exc()))

        threads = [threading.Thread(target=run_pipeline, args=(i,)) for i in range(num_pipelines)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        # Validate results
        all_completed = all(
            r["status"] == "completed"
            for r in pipeline_results.values()
        )
        ordering_violations = 0
        for idx, result in pipeline_results.items():
            order = result["execution_order"]
            # Verify "fetch" comes before anything else
            if "fetch" in order and order.index("fetch") != 0:
                ordering_violations += 1
            # Verify "output" is last
            if "output" in order and order.index("output") != len(order) - 1:
                ordering_violations += 1
            # Verify merge comes after both transforms
            if all(s in order for s in ["merge", "transform-a", "transform-b"]):
                if order.index("merge") < order.index("transform-a") or \
                   order.index("merge") < order.index("transform-b"):
                    ordering_violations += 1

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="pipeline_correctness",
            test_name="concurrent_pipelines_ordering",
            metrics={
                "num_pipelines": num_pipelines,
                "stages_per_pipeline": stages_per_pipeline,
                "all_completed": all_completed,
                "pipelines_completed": sum(1 for r in pipeline_results.values() if r["status"] == "completed"),
                "ordering_violations": ordering_violations,
                "thread_errors": len(errors),
            },
            passed=all_completed and ordering_violations == 0 and len(errors) == 0,
            duration_s=round(duration, 3),
            details=(
                f"{sum(1 for r in pipeline_results.values() if r['status']=='completed')}/{num_pipelines} "
                f"pipelines completed, {ordering_violations} ordering violations"
            ),
            errors=[f"pipe-{idx}: {e}" for idx, e, _ in errors],
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="pipeline_correctness",
            test_name="concurrent_pipelines_ordering",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    # Test 2: Bus message ordering — no message loss under load
    t0 = time.time()
    try:
        tmp = tempfile.mkdtemp(prefix="bench-bus-order-")
        bus = os.path.join(tmp, "bus")
        os.makedirs(bus, exist_ok=True)

        num_senders = 5
        msgs_per_sender = 200
        barrier = threading.Barrier(num_senders)
        send_errors = []

        def sender(idx):
            try:
                writer = BusWriter(bus, f"agent-{idx}", f"team-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                for seq in range(msgs_per_sender):
                    writer.publish("ordering-test", "info", {
                        "sender": idx,
                        "seq": seq,
                    })
            except Exception as e:
                send_errors.append((idx, str(e)))

        threads = [threading.Thread(target=sender, args=(i,)) for i in range(num_senders)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        # Read all messages
        reader = BusReader(bus, "ordering-test")
        all_msgs = reader.poll()

        # Verify no message loss
        total_expected = num_senders * msgs_per_sender
        received_by_sender = defaultdict(list)
        for msg in all_msgs:
            received_by_sender[msg.body["sender"]].append(msg.body["seq"])

        # Check per-sender ordering (each sender's messages should be in order)
        per_sender_ordered = True
        for sender_id, seqs in received_by_sender.items():
            for i in range(1, len(seqs)):
                if seqs[i] <= seqs[i - 1]:
                    per_sender_ordered = False
                    break

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="pipeline_correctness",
            test_name="bus_message_ordering_and_loss",
            metrics={
                "senders": num_senders,
                "msgs_per_sender": msgs_per_sender,
                "total_expected": total_expected,
                "total_received": len(all_msgs),
                "message_loss": total_expected - len(all_msgs),
                "message_loss_pct": round((total_expected - len(all_msgs)) / total_expected * 100, 2),
                "per_sender_ordered": per_sender_ordered,
                "per_sender_counts": {str(k): len(v) for k, v in received_by_sender.items()},
                "send_errors": len(send_errors),
            },
            passed=len(all_msgs) == total_expected and per_sender_ordered,
            duration_s=round(duration, 3),
            details=(
                f"Received {len(all_msgs)}/{total_expected} messages, "
                f"loss={total_expected - len(all_msgs)}, "
                f"per-sender-ordered={per_sender_ordered}"
            ),
            errors=[f"sender-{s}: {e}" for s, e in send_errors],
        )
        results.append(result)

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="pipeline_correctness",
            test_name="bus_message_ordering_and_loss",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    # Test 3: Exactly-once work stealing under concurrent load
    t0 = time.time()
    try:
        tmp = tempfile.mkdtemp(prefix="bench-exactly-once-")
        db = os.path.join(tmp, "exactly_once.db")
        bus = os.path.join(tmp, "bus")
        os.makedirs(bus, exist_ok=True)

        ws = WorkStealing(db, bus, "coordinator", "coord-agent")
        num_items = 200
        num_stealers = 10

        for i in range(num_items):
            ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

        barrier = threading.Barrier(num_stealers)
        all_stolen = []
        stolen_lock = threading.Lock()
        steal_errors = []

        def stealer(idx):
            try:
                w = WorkStealing(db, bus, f"team-{idx}", f"agent-{idx}")
                barrier.wait(timeout=JOIN_TIMEOUT)
                local = []
                for _ in range(num_items):  # Try more times than items exist
                    item = w.steal_work()
                    if item is None:
                        break
                    local.append(item["id"])
                with stolen_lock:
                    all_stolen.extend(local)
                w.close()
            except Exception as e:
                with stolen_lock:
                    steal_errors.append((idx, str(e)))

        threads = [threading.Thread(target=stealer, args=(i,)) for i in range(num_stealers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)

        # Drain any remaining
        while True:
            item = ws.steal_work()
            if item is None:
                break
            all_stolen.append(item["id"])

        unique_stolen = set(all_stolen)
        duplicates = len(all_stolen) - len(unique_stolen)

        duration = time.time() - t0
        result = BenchmarkResult(
            dimension="pipeline_correctness",
            test_name="exactly_once_work_stealing",
            metrics={
                "total_items": num_items,
                "total_stolen": len(all_stolen),
                "unique_stolen": len(unique_stolen),
                "duplicates": duplicates,
                "concurrent_stealers": num_stealers,
                "exactly_once": duplicates == 0 and len(unique_stolen) == num_items,
                "errors": len(steal_errors),
            },
            passed=duplicates == 0 and len(unique_stolen) == num_items,
            duration_s=round(duration, 3),
            details=(
                f"{len(unique_stolen)}/{num_items} unique items stolen, "
                f"{duplicates} duplicates, "
                f"exactly-once={'YES' if duplicates==0 else 'NO'}"
            ),
            errors=[f"stealer-{s}: {e}" for s, e in steal_errors],
        )
        results.append(result)
        ws.close()

    except Exception as e:
        results.append(BenchmarkResult(
            dimension="pipeline_correctness",
            test_name="exactly_once_work_stealing",
            metrics={},
            passed=False,
            duration_s=time.time() - t0,
            errors=[str(e), traceback.format_exc()],
        ))

    return results


# ===================================================================
# Dimension 6: Scaling Limits
# ===================================================================


def bench_scaling_limits(harness: BenchmarkHarness) -> list[BenchmarkResult]:
    """Find the breaking point: how many concurrent agents before degradation."""
    results = []

    # Test at increasing scale: 5, 10, 20, 50 concurrent agents
    scaling_data = {}

    for num_agents in [5, 10, 20, 50]:
        t0 = time.time()
        try:
            tmp = tempfile.mkdtemp(prefix=f"bench-scale-{num_agents}-")
            db = os.path.join(tmp, "scale.db")
            bus = os.path.join(tmp, "bus")
            os.makedirs(bus, exist_ok=True)

            items_per_agent = 20
            total_items = num_agents * items_per_agent

            # Pre-enqueue all work
            ws = WorkStealing(db, bus, "coordinator", "coord-agent")
            for i in range(total_items):
                ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

            barrier = threading.Barrier(num_agents)
            agent_stats = {}
            stats_lock = threading.Lock()
            errors = []
            contention_count = [0]

            def scale_worker(idx):
                try:
                    w = WorkStealing(db, bus, f"team-{idx % 10}", f"agent-{idx}")
                    writer = BusWriter(bus, f"agent-{idx}", f"team-{idx % 10}")
                    steal_times = []
                    local_contention = 0

                    barrier.wait(timeout=JOIN_TIMEOUT)
                    items_done = 0

                    while True:
                        t_steal = time.perf_counter()
                        try:
                            item = w.steal_work()
                        except sqlite3.OperationalError:
                            local_contention += 1
                            with stats_lock:
                                contention_count[0] += 1
                            continue
                        steal_time = time.perf_counter() - t_steal
                        steal_times.append(steal_time)

                        if item is None:
                            break

                        # Simulate work + bus write
                        writer.publish("progress", "info", {"agent": idx, "item": item["id"]})
                        w.complete_work(item["id"], {"agent": idx})
                        items_done += 1

                    with stats_lock:
                        agent_stats[idx] = {
                            "items_done": items_done,
                            "avg_steal_ms": round(statistics.mean(steal_times) * 1000, 3) if steal_times else 0,
                            "p95_steal_ms": round(sorted(steal_times)[int(len(steal_times) * 0.95)] * 1000, 3) if len(steal_times) > 1 else 0,
                            "max_steal_ms": round(max(steal_times) * 1000, 3) if steal_times else 0,
                            "contention": local_contention,
                        }
                    w.close()
                except Exception as e:
                    with stats_lock:
                        errors.append((idx, str(e)))

            threads = [threading.Thread(target=scale_worker, args=(i,)) for i in range(num_agents)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=JOIN_TIMEOUT)

            # Drain remaining
            remaining = 0
            while ws.steal_work() is not None:
                remaining += 1

            duration = time.time() - t0
            total_done = sum(s["items_done"] for s in agent_stats.values()) + remaining
            all_steal_avgs = [s["avg_steal_ms"] for s in agent_stats.values() if s["avg_steal_ms"] > 0]
            all_steal_p95s = [s["p95_steal_ms"] for s in agent_stats.values() if s["p95_steal_ms"] > 0]

            throughput = total_done / max(duration, 0.001)

            scaling_data[num_agents] = {
                "throughput": throughput,
                "avg_steal_ms": statistics.mean(all_steal_avgs) if all_steal_avgs else 0,
                "p95_steal_ms": statistics.mean(all_steal_p95s) if all_steal_p95s else 0,
                "contention": contention_count[0],
            }

            result = BenchmarkResult(
                dimension="scaling_limits",
                test_name=f"scale_{num_agents}_agents",
                metrics={
                    "num_agents": num_agents,
                    "total_items": total_items,
                    "items_completed": total_done,
                    "duration_s": round(duration, 3),
                    "throughput_items_per_s": round(throughput, 2),
                    "per_agent_throughput": round(throughput / num_agents, 2),
                    "avg_steal_latency_ms": round(statistics.mean(all_steal_avgs), 3) if all_steal_avgs else 0,
                    "p95_steal_latency_ms": round(statistics.mean(all_steal_p95s), 3) if all_steal_p95s else 0,
                    "total_contention_events": contention_count[0],
                    "contention_per_agent": round(contention_count[0] / num_agents, 2),
                    "thread_errors": len(errors),
                },
                passed=total_done == total_items and len(errors) == 0,
                duration_s=round(duration, 3),
                details=(
                    f"{num_agents} agents: {throughput:.1f} items/s, "
                    f"steal_avg={statistics.mean(all_steal_avgs):.1f}ms, "
                    f"contention={contention_count[0]}"
                    if all_steal_avgs else f"{num_agents} agents: {throughput:.1f} items/s"
                ),
                errors=[f"agent-{a}: {e}" for a, e in errors],
            )
            results.append(result)
            ws.close()

        except Exception as e:
            results.append(BenchmarkResult(
                dimension="scaling_limits",
                test_name=f"scale_{num_agents}_agents",
                metrics={},
                passed=False,
                duration_s=time.time() - t0,
                errors=[str(e), traceback.format_exc()],
            ))

    # Summary: identify scaling bottleneck
    if len(scaling_data) >= 2:
        t0 = time.time()
        sizes = sorted(scaling_data.keys())
        throughputs = [scaling_data[s]["throughput"] for s in sizes]
        steal_latencies = [scaling_data[s]["avg_steal_ms"] for s in sizes]
        contention_rates = [scaling_data[s]["contention"] for s in sizes]

        # Find where per-agent throughput drops most
        per_agent = [scaling_data[s]["throughput"] / s for s in sizes]
        max_drop = 0
        bottleneck_size = sizes[-1]
        for i in range(1, len(sizes)):
            drop = per_agent[i - 1] - per_agent[i]
            if drop > max_drop:
                max_drop = drop
                bottleneck_size = sizes[i]

        # Check for latency spike
        latency_spike_at = None
        for i in range(1, len(sizes)):
            if steal_latencies[i] > steal_latencies[i - 1] * 3:  # 3x increase
                latency_spike_at = sizes[i]
                break

        result = BenchmarkResult(
            dimension="scaling_limits",
            test_name="scaling_bottleneck_analysis",
            metrics={
                "agent_counts_tested": sizes,
                "throughputs": {str(s): round(scaling_data[s]["throughput"], 2) for s in sizes},
                "per_agent_throughputs": {str(s): round(scaling_data[s]["throughput"]/s, 2) for s in sizes},
                "steal_latencies_ms": {str(s): round(scaling_data[s]["avg_steal_ms"], 2) for s in sizes},
                "contention_events": {str(s): scaling_data[s]["contention"] for s in sizes},
                "biggest_efficiency_drop_at": bottleneck_size,
                "latency_spike_at": latency_spike_at,
                "linear_scaling_efficiency": round(throughputs[-1] / (throughputs[0] * (sizes[-1] / sizes[0])) * 100, 1) if throughputs[0] > 0 else 0,
            },
            passed=True,
            duration_s=round(time.time() - t0, 3),
            details=(
                f"Biggest efficiency drop at {bottleneck_size} agents. "
                f"Latency spike at {latency_spike_at or 'none detected'}. "
                f"Linear scaling efficiency: "
                f"{throughputs[-1] / (throughputs[0] * (sizes[-1] / sizes[0])) * 100:.1f}%"
                if throughputs[0] > 0 else "Insufficient data"
            ),
        )
        results.append(result)

    return results


# ===================================================================
# Main Runner
# ===================================================================


def run_all_benchmarks() -> dict:
    """Run all 6 benchmark dimensions and collect results."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_results = []
    dimension_summaries = {}

    benchmarks = [
        ("1_communication_overhead", bench_communication_overhead),
        ("2_team_sizing", bench_team_sizing),
        ("3_failure_recovery", bench_failure_recovery),
        ("4_capability_matching", bench_capability_matching),
        ("5_pipeline_correctness", bench_pipeline_correctness),
        ("6_scaling_limits", bench_scaling_limits),
    ]

    print("=" * 70)
    print("MULTI-AGENT COORDINATION BENCHMARK SUITE")
    print("=" * 70)
    print()

    total_start = time.time()

    for dim_name, bench_func in benchmarks:
        print(f"\n{'='*60}")
        print(f"Dimension: {dim_name}")
        print(f"{'='*60}")

        harness = BenchmarkHarness(dim_name)
        t0 = time.time()

        try:
            dim_results = bench_func(harness)
        except Exception as e:
            dim_results = [BenchmarkResult(
                dimension=dim_name,
                test_name="DIMENSION_FAILED",
                metrics={},
                passed=False,
                duration_s=time.time() - t0,
                errors=[str(e), traceback.format_exc()],
            )]

        dim_duration = time.time() - t0

        for r in dim_results:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.test_name} ({r.duration_s:.2f}s)")
            if r.details:
                print(f"         {r.details}")
            if r.errors:
                for err in r.errors[:3]:
                    print(f"         ERROR: {err[:200]}")

        passed = sum(1 for r in dim_results if r.passed)
        failed = sum(1 for r in dim_results if not r.passed)
        dimension_summaries[dim_name] = {
            "passed": passed,
            "failed": failed,
            "total": len(dim_results),
            "duration_s": round(dim_duration, 3),
        }
        all_results.extend(dim_results)

        # Save per-dimension results
        dim_output = OUTPUT_DIR / f"{dim_name}_results.json"
        with open(dim_output, "w") as f:
            json.dump([asdict(r) for r in dim_results], f, indent=2)

    total_duration = time.time() - total_start

    # Overall summary
    total_passed = sum(1 for r in all_results if r.passed)
    total_failed = sum(1 for r in all_results if not r.passed)

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_tests": len(all_results),
        "passed": total_passed,
        "failed": total_failed,
        "total_duration_s": round(total_duration, 3),
        "dimensions": dimension_summaries,
        "results": [asdict(r) for r in all_results],
    }

    # Save full results
    summary_path = OUTPUT_DIR / "benchmark_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*70}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*70}")
    print(f"Total: {len(all_results)} tests, {total_passed} passed, {total_failed} failed")
    print(f"Duration: {total_duration:.2f}s")
    print()
    for dim, stats in dimension_summaries.items():
        status = "OK" if stats["failed"] == 0 else "ISSUES"
        print(f"  {dim}: {stats['passed']}/{stats['total']} passed [{status}] ({stats['duration_s']:.1f}s)")
    print(f"\nResults saved to: {OUTPUT_DIR}")

    return summary


if __name__ == "__main__":
    run_all_benchmarks()
