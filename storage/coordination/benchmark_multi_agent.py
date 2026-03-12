#!/usr/bin/env python3
"""Comprehensive Multi-Agent System Benchmark Suite.

Benchmarks every layer of the coordination infrastructure:

1. Coordinator Hub    - Registration, status updates, dashboard reads
2. Help Protocol      - Work items, help requests, auto-assignment, idle detection
3. Work Stealing      - Enqueue, steal, pipeline stages, scratchpad
4. Direct Channels    - Messaging, presence, progress broadcasting
5. Bus Core           - Read/write throughput at 1/4/8/16 agents
6. Circuit Breaker    - call_allowed latency, state transitions
7. Capability Matching - find_best_match scoring at scale
8. Load Balancer      - update_load, get_least_loaded under contention
9. Quality Gates      - Validation throughput
10. SQLite Contention  - ops/sec at 1/4/8/16/32 writers
11. End-to-End Scenario - Full multi-team lifecycle

Outputs JSON report with pass/fail per SOP-035 thresholds.

Usage:
    python benchmark_multi_agent.py                    # Run all benchmarks
    python benchmark_multi_agent.py --suite hub        # Single suite
    python benchmark_multi_agent.py --output report.json
    python benchmark_multi_agent.py --baseline prev.json --threshold 20
    python benchmark_multi_agent.py --iterations 10 --agents 8
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Repository root discovery
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def compute_stats(samples: list[float]) -> dict:
    """Compute descriptive statistics from a list of latency values (ms)."""
    if not samples:
        return {"mean_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0,
                "min_ms": 0, "max_ms": 0, "count": 0}
    s = sorted(samples)
    n = len(s)
    return {
        "mean_ms": round(statistics.mean(s), 4),
        "p50_ms": round(s[int(n * 0.50)] if n > 1 else s[0], 4),
        "p95_ms": round(s[min(int(n * 0.95), n - 1)], 4),
        "p99_ms": round(s[min(int(n * 0.99), n - 1)], 4),
        "min_ms": round(s[0], 4),
        "max_ms": round(s[-1], 4),
        "count": n,
    }


def timer_ms(fn) -> float:
    """Execute fn() and return elapsed milliseconds."""
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


# ===================================================================
# 1. COORDINATOR HUB BENCHMARK
# ===================================================================

def bench_coordinator_hub(iterations: int = 5, num_agents: int = 8) -> dict:
    """Benchmark AgentReporter and CoordinatorDashboard operations."""
    from storage.coordination.coordinator_hub import AgentReporter, CoordinatorDashboard

    reg_times, update_times, dash_times, instruction_times = [], [], [], []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_hub_") as tmpdir:
            db_path = os.path.join(tmpdir, "hub.db")
            bus_dir = os.path.join(tmpdir, "bus")

            # Registration
            agents = []
            for i in range(num_agents):
                t0 = time.perf_counter()
                agent = AgentReporter(db_path, f"agent-{i}", "team-alpha",
                                      f"role-{i % 3}", bus_dir=bus_dir)
                reg_times.append((time.perf_counter() - t0) * 1000)
                agents.append(agent)

            # Status updates
            for i, agent in enumerate(agents):
                t0 = time.perf_counter()
                agent.update_status("working", i * 10, f"Task-{i}")
                update_times.append((time.perf_counter() - t0) * 1000)

            # Dashboard reads
            dashboard = CoordinatorDashboard(db_path, bus_dir=bus_dir)
            for _ in range(5):
                t0 = time.perf_counter()
                dashboard.get_all_status()
                dashboard.get_active_agents()
                dashboard.get_summary()
                dash_times.append((time.perf_counter() - t0) * 1000)

            # Instruction delivery (payload must be a JSON string)
            for i in range(num_agents):
                t0 = time.perf_counter()
                dashboard.send_instruction(f"agent-{i}", "redirect",
                                           json.dumps({"task": "new-task"}))
                instruction_times.append((time.perf_counter() - t0) * 1000)

            # Cleanup
            for a in agents:
                a.close()
            dashboard.close()

    return {
        "registration": compute_stats(reg_times),
        "status_update": compute_stats(update_times),
        "dashboard_read": compute_stats(dash_times),
        "instruction_delivery": compute_stats(instruction_times),
    }


# ===================================================================
# 2. HELP PROTOCOL BENCHMARK
# ===================================================================

def bench_help_protocol(iterations: int = 5, num_teams: int = 8) -> dict:
    """Benchmark help protocol: work items, requests, auto-assignment."""
    from storage.coordination.help_protocol import HelpProtocol

    add_times, request_times, assign_times, idle_times = [], [], [], []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_help_") as tmpdir:
            db_path = os.path.join(tmpdir, "help.db")
            bus_dir = os.path.join(tmpdir, "bus")

            teams = []
            for i in range(num_teams):
                hp = HelpProtocol(db_path, bus_dir, f"team-{i}", f"agent-{i}")
                hp.register_capabilities([f"cap-{i % 4}", "common"])
                if i < num_teams // 2:
                    hp.update_status("idle", 0.0, "waiting")
                else:
                    hp.update_status("needs_help", 60.0, "blocked")
                teams.append(hp)

            # Add work items
            work_ids = []
            for i in range(num_teams):
                t0 = time.perf_counter()
                wid = teams[i % num_teams].add_work_item(
                    f"work-{i}", required_caps=["common"])
                add_times.append((time.perf_counter() - t0) * 1000)
                work_ids.append(wid)

            # Request help
            for i, wid in enumerate(work_ids[:num_teams // 2]):
                t0 = time.perf_counter()
                teams[num_teams // 2 + (i % (num_teams // 2))].request_help(
                    wid, f"help needed #{i}")
                request_times.append((time.perf_counter() - t0) * 1000)

            # Auto-assign idle teams
            coordinator = teams[0]
            t0 = time.perf_counter()
            coordinator.auto_assign_idle_teams()
            assign_times.append((time.perf_counter() - t0) * 1000)

            # Idle detection
            t0 = time.perf_counter()
            coordinator.get_idle_teams()
            idle_times.append((time.perf_counter() - t0) * 1000)

            for hp in teams:
                hp.close()

    return {
        "add_work_item": compute_stats(add_times),
        "request_help": compute_stats(request_times),
        "auto_assign": compute_stats(assign_times),
        "idle_detection": compute_stats(idle_times),
    }


# ===================================================================
# 3. WORK STEALING BENCHMARK
# ===================================================================

def bench_work_stealing(iterations: int = 5, num_agents: int = 8) -> dict:
    """Benchmark work queue operations and pipeline stages."""
    from storage.coordination.work_stealing import WorkStealing

    enqueue_times, steal_times, complete_times, pipeline_times = [], [], [], []
    thundering_herd_times = []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_ws_") as tmpdir:
            db_path = os.path.join(tmpdir, "ws.db")
            bus_dir = os.path.join(tmpdir, "bus")

            # Enqueue work
            leader = WorkStealing(db_path, bus_dir, "team-lead", "lead-agent")
            work_ids = []
            for i in range(20):
                t0 = time.perf_counter()
                wid = leader.enqueue_work(f"task-{i}", f"desc-{i}", priority=i % 5 + 1)
                enqueue_times.append((time.perf_counter() - t0) * 1000)
                work_ids.append(wid)

            # Steal work (sequential)
            for i in range(min(num_agents, 20)):
                stealer = WorkStealing(db_path, bus_dir, f"team-{i}", f"agent-{i}")
                t0 = time.perf_counter()
                item = stealer.steal_work()
                steal_times.append((time.perf_counter() - t0) * 1000)
                if item:
                    t0 = time.perf_counter()
                    stealer.complete_work(item["id"], {"result": f"done-{i}"})
                    complete_times.append((time.perf_counter() - t0) * 1000)
                stealer.close()

            # Thundering herd: multiple threads stealing simultaneously
            # Re-enqueue items
            for i in range(10):
                leader.enqueue_work(f"herd-{i}", f"desc-herd-{i}", priority=3)

            barrier = threading.Barrier(num_agents)
            herd_results = []
            lock = threading.Lock()

            def herd_stealer(tid):
                ws = WorkStealing(db_path, bus_dir, f"herd-{tid}", f"hagent-{tid}")
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    ws.close()
                    return
                t0 = time.perf_counter()
                item = ws.steal_work()
                elapsed = (time.perf_counter() - t0) * 1000
                with lock:
                    herd_results.append(elapsed)
                ws.close()

            threads = [threading.Thread(target=herd_stealer, args=(i,))
                       for i in range(num_agents)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)
            thundering_herd_times.extend(herd_results)

            # Pipeline benchmark
            try:
                pid = leader.create_pipeline("bench-pipeline", [
                    {"name": "extract", "description": "Extract data"},
                    {"name": "transform", "description": "Transform data"},
                    {"name": "load", "description": "Load data"},
                ])
                if pid:
                    t0 = time.perf_counter()
                    leader.advance_pipeline(pid, "extract",
                                            {"output": "extracted data"})
                    pipeline_times.append((time.perf_counter() - t0) * 1000)
            except Exception:
                pass

            leader.close()

    return {
        "enqueue": compute_stats(enqueue_times),
        "steal_sequential": compute_stats(steal_times),
        "complete": compute_stats(complete_times),
        "steal_thundering_herd": compute_stats(thundering_herd_times),
        "pipeline_advance": compute_stats(pipeline_times),
    }


# ===================================================================
# 4. DIRECT CHANNELS BENCHMARK
# ===================================================================

def bench_direct_channels(iterations: int = 5, num_agents: int = 8) -> dict:
    """Benchmark direct messaging, presence, and progress broadcasting."""
    from storage.coordination.direct_channels import DirectChannels

    send_times, read_times, presence_times, progress_times = [], [], [], []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_dc_") as tmpdir:
            db_path = os.path.join(tmpdir, "channels.db")
            bus_dir = os.path.join(tmpdir, "bus")

            channels = []
            for i in range(num_agents):
                dc = DirectChannels(db_path, bus_dir, f"team-{i}", f"agent-{i}")
                channels.append(dc)

            # Send direct messages
            for i in range(num_agents):
                target = (i + 1) % num_agents
                t0 = time.perf_counter()
                channels[i].send_direct(f"team-{target}", "info",
                                        {"msg": f"hello from {i}", "seq": i})
                send_times.append((time.perf_counter() - t0) * 1000)

            # Read direct messages
            for i in range(num_agents):
                sender = (i - 1) % num_agents
                t0 = time.perf_counter()
                channels[i].read_direct(f"team-{sender}")
                read_times.append((time.perf_counter() - t0) * 1000)

            # Presence updates
            for i in range(num_agents):
                t0 = time.perf_counter()
                channels[i].set_presence("available")
                presence_times.append((time.perf_counter() - t0) * 1000)

            # Progress updates
            for i in range(min(3, num_agents)):
                t0 = time.perf_counter()
                channels[i].update_progress(
                    "benchmarking", i * 30.0, 100, i * 30)
                progress_times.append((time.perf_counter() - t0) * 1000)

            for dc in channels:
                dc.close()

    return {
        "send_message": compute_stats(send_times),
        "read_messages": compute_stats(read_times),
        "update_presence": compute_stats(presence_times),
        "broadcast_progress": compute_stats(progress_times),
    }


# ===================================================================
# 5. BUS CORE THROUGHPUT BENCHMARK
# ===================================================================

def bench_bus_throughput(iterations: int = 3, agent_counts: list[int] | None = None) -> dict:
    """Measure bus read/write msgs/sec at various concurrency levels."""
    from storage.coordination.bus_core import bus_write, bus_read

    if agent_counts is None:
        agent_counts = [1, 4, 8, 16]

    results = {}
    msgs_per_agent = 50

    for num_agents in agent_counts:
        write_times: list[float] = []

        for _ in range(iterations):
            with tempfile.TemporaryDirectory(prefix=f"bench_bus_{num_agents}_") as tmpdir:
                bus_dir = os.path.join(tmpdir, "bus")
                barrier = threading.Barrier(num_agents)
                lock = threading.Lock()
                local_times: list[float] = []

                def writer(agent_id: int):
                    try:
                        barrier.wait(timeout=10)
                    except threading.BrokenBarrierError:
                        return
                    for j in range(msgs_per_agent):
                        t0 = time.perf_counter()
                        bus_write(bus_dir, "bench", "info",
                                  {"agent": agent_id, "seq": j},
                                  "bench-team", f"agent-{agent_id}")
                        elapsed = (time.perf_counter() - t0) * 1000
                        with lock:
                            local_times.append(elapsed)

                threads = [threading.Thread(target=writer, args=(i,))
                           for i in range(num_agents)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=30)
                write_times.extend(local_times)

                # Read benchmark
                read_times_local: list[float] = []
                offset = 0
                t0 = time.perf_counter()
                msgs, new_offset = bus_read(bus_dir, "bench", offset)
                read_times_local.append((time.perf_counter() - t0) * 1000)

        total_written = num_agents * msgs_per_agent * iterations
        throughput = (total_written / (sum(write_times) / 1000)) if sum(write_times) > 0 else 0

        results[f"{num_agents}_agents"] = {
            "write": {
                **compute_stats(write_times),
                "total_messages": total_written,
                "throughput_msgs_per_sec": round(throughput, 2),
            },
        }

    return results


# ===================================================================
# 6. CIRCUIT BREAKER BENCHMARK
# ===================================================================

def bench_circuit_breaker(iterations: int = 5) -> dict:
    """Benchmark circuit breaker call_allowed latency and state transitions."""
    from storage.coordination.circuit_breaker import CircuitBreaker

    allowed_times, record_times, transition_times = [], [], []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_cb_") as tmpdir:
            db_path = os.path.join(tmpdir, "cb.db")
            cb = CircuitBreaker(db_path, failure_threshold=3, recovery_timeout=1.0)

            # call_allowed latency (hot path) - endpoints auto-register
            for i in range(100):
                ep = f"endpoint-{i % 10}"
                t0 = time.perf_counter()
                cb.call_allowed(ep, f"agent-{i % 5}")
                allowed_times.append((time.perf_counter() - t0) * 1000)

            # Record success/failure
            for i in range(50):
                ep = f"endpoint-{i % 10}"
                t0 = time.perf_counter()
                if i % 3 == 0:
                    cb.record_failure(ep, f"agent-{i % 5}")
                else:
                    cb.record_success(ep, f"agent-{i % 5}")
                record_times.append((time.perf_counter() - t0) * 1000)

            # Force state transition (trip a breaker with enough failures)
            for i in range(5):
                ep = f"tripped-{i}"
                for _ in range(5):
                    cb.record_failure(ep, "agent-stress")
                t0 = time.perf_counter()
                cb.call_allowed(ep, "agent-check")
                transition_times.append((time.perf_counter() - t0) * 1000)

            cb.close()

    return {
        "call_allowed": compute_stats(allowed_times),
        "record_outcome": compute_stats(record_times),
        "state_transition_check": compute_stats(transition_times),
    }


# ===================================================================
# 7. CAPABILITY MATCHING BENCHMARK
# ===================================================================

def bench_capability_matching(iterations: int = 5, num_teams: int = 20) -> dict:
    """Benchmark find_best_match with many teams and capabilities."""
    from storage.coordination.capability_discovery import CapabilityRegistry

    register_times, match_times = [], []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_cap_") as tmpdir:
            db_path = os.path.join(tmpdir, "caps.db")
            cr = CapabilityRegistry(db_path)

            # Register teams with diverse capabilities
            caps_pool = ["coding", "testing", "research", "data-gathering",
                         "market-analysis", "debugging", "documentation",
                         "api-integration", "security", "devops"]

            for i in range(num_teams):
                team_caps = caps_pool[i % len(caps_pool):i % len(caps_pool) + 3]
                if not team_caps:
                    team_caps = caps_pool[:3]
                cap_dicts = [{"name": c, "proficiency": (i % 5) + 1}
                             for c in team_caps]
                t0 = time.perf_counter()
                cr.register_agent(f"agent-{i}", f"team-{i}", cap_dicts)
                register_times.append((time.perf_counter() - t0) * 1000)

            # Benchmark find_best_match
            queries = [
                ["coding", "testing"],
                ["research"],
                ["coding", "debugging", "security"],
                ["data-gathering", "api-integration"],
                ["market-analysis", "documentation"],
            ]
            for query in queries:
                t0 = time.perf_counter()
                cr.find_best_match(query)
                match_times.append((time.perf_counter() - t0) * 1000)

            cr.close()

    return {
        "register_capability": compute_stats(register_times),
        "find_best_match": compute_stats(match_times),
    }


# ===================================================================
# 8. LOAD BALANCER BENCHMARK
# ===================================================================

def bench_load_balancer(iterations: int = 5, num_teams: int = 16) -> dict:
    """Benchmark load tracking and distribution decisions."""
    from storage.coordination.load_balancer import LoadBalancer

    update_times, query_times = [], []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_lb_") as tmpdir:
            db_path = os.path.join(tmpdir, "lb.db")
            lb = LoadBalancer(db_path)

            # Update load for many teams
            for i in range(num_teams):
                t0 = time.perf_counter()
                lb.update_load(f"team-{i}", active_tasks=i % 5,
                               queue_depth=i % 10)
                update_times.append((time.perf_counter() - t0) * 1000)

            # Query least loaded
            capable_teams = [f"team-{i}" for i in range(num_teams)]
            for _ in range(20):
                t0 = time.perf_counter()
                lb.get_least_loaded(capable_teams)
                query_times.append((time.perf_counter() - t0) * 1000)

            lb.close()

    return {
        "update_load": compute_stats(update_times),
        "get_least_loaded": compute_stats(query_times),
    }


# ===================================================================
# 9. QUALITY GATES BENCHMARK
# ===================================================================

def bench_quality_gates(iterations: int = 5) -> dict:
    """Benchmark quality gate validation throughput."""
    from storage.coordination.quality_gates import QualityGate

    define_times, validate_times = [], []

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_qg_") as tmpdir:
            db_path = os.path.join(tmpdir, "qg.db")
            qg = QualityGate(db_path)

            # Define gates for a pipeline
            for i in range(5):
                t0 = time.perf_counter()
                qg.define_gate(
                    pipeline_id=1,
                    stage_name=f"stage-{i}",
                    validators=[
                        {"name": "has_required_fields",
                         "params": {"fields": ["id", "name"]}, "weight": 1.0},
                        {"name": "min_item_count",
                         "params": {"min_count": 1, "key": "items"}, "weight": 1.0},
                    ],
                    threshold=0.7,
                )
                define_times.append((time.perf_counter() - t0) * 1000)

            # Validate outputs
            for i in range(20):
                test_data = {
                    "items": [{"id": j, "name": f"item-{j}"} for j in range(5)],
                    "id": i,
                    "name": f"output-{i}",
                }
                t0 = time.perf_counter()
                qg.validate_output(
                    pipeline_id=1,
                    stage_name=f"stage-{i % 5}",
                    output_data=test_data,
                )
                validate_times.append((time.perf_counter() - t0) * 1000)

            qg.close()

    return {
        "define_gate": compute_stats(define_times),
        "validate_output": compute_stats(validate_times),
    }


# ===================================================================
# 10. SQLITE CONTENTION BENCHMARK
# ===================================================================

def bench_sqlite_contention(iterations: int = 3,
                            writer_counts: list[int] | None = None) -> dict:
    """Measure raw SQLite write ops/sec at various concurrency levels."""
    from storage.coordination.bus_core import init_db

    if writer_counts is None:
        writer_counts = [1, 4, 8, 16, 32]

    ops_per_writer = 50
    results = {}

    for num_writers in writer_counts:
        all_times: list[float] = []
        total_errors = 0

        for _ in range(iterations):
            with tempfile.TemporaryDirectory(prefix=f"bench_sql_{num_writers}_") as tmpdir:
                db_path = os.path.join(tmpdir, "contention.db")
                conn = init_db(db_path)
                conn.execute("""CREATE TABLE IF NOT EXISTS bench (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    writer TEXT, seq INTEGER, data TEXT, ts REAL)""")
                conn.commit()
                conn.close()

                barrier = threading.Barrier(num_writers)
                lock = threading.Lock()
                local_times: list[float] = []
                local_errors = [0]

                def writer(wid: int):
                    c = init_db(db_path)
                    try:
                        barrier.wait(timeout=10)
                    except threading.BrokenBarrierError:
                        c.close()
                        return
                    for seq in range(ops_per_writer):
                        t0 = time.perf_counter()
                        retries = 0
                        ok = False
                        while retries < 10:
                            try:
                                c.execute(
                                    "INSERT INTO bench (writer,seq,data,ts) VALUES (?,?,?,?)",
                                    (f"w-{wid}", seq, f"d-{wid}-{seq}", time.time()))
                                c.commit()
                                ok = True
                                break
                            except sqlite3.OperationalError as e:
                                if "busy" in str(e).lower() or "locked" in str(e).lower():
                                    retries += 1
                                    time.sleep(0.005 * retries)
                                else:
                                    break
                        elapsed = (time.perf_counter() - t0) * 1000
                        with lock:
                            local_times.append(elapsed)
                            if not ok:
                                local_errors[0] += 1
                    c.close()

                threads = [threading.Thread(target=writer, args=(i,))
                           for i in range(num_writers)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=30)
                all_times.extend(local_times)
                total_errors += local_errors[0]

        total_ops = len(all_times)
        wall_s = sum(all_times) / 1000 if all_times else 1
        ops_sec = total_ops / wall_s if wall_s > 0 else 0

        results[f"{num_writers}_writers"] = {
            **compute_stats(all_times),
            "total_ops": total_ops,
            "errors": total_errors,
            "ops_per_sec": round(ops_sec, 2),
            "efficiency_pct": round((ops_sec / num_writers) /
                                    (results.get("1_writers", {}).get("ops_per_sec", ops_sec) or ops_sec)
                                    * 100, 1) if "1_writers" in results else 100.0,
        }

    return results


# ===================================================================
# 11. END-TO-END MULTI-TEAM SCENARIO
# ===================================================================

def bench_end_to_end(iterations: int = 3, num_teams: int = 4,
                     agents_per_team: int = 3) -> dict:
    """Full lifecycle: register teams, distribute work, steal, help, converge."""
    from storage.coordination.coordinator_hub import AgentReporter, CoordinatorDashboard
    from storage.coordination.work_stealing import WorkStealing
    from storage.coordination.help_protocol import HelpProtocol
    from storage.coordination.bus_core import bus_write, bus_read

    scenario_times: list[float] = []
    phase_times: dict[str, list[float]] = {
        "setup": [], "registration": [], "work_distribution": [],
        "work_execution": [], "help_flow": [], "communication": [],
        "dashboard": [], "teardown": [],
    }

    total_agents = num_teams * agents_per_team

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_e2e_") as tmpdir:
            hub_db = os.path.join(tmpdir, "hub.db")
            ws_db = os.path.join(tmpdir, "ws.db")
            hp_db = os.path.join(tmpdir, "hp.db")
            bus_dir = os.path.join(tmpdir, "bus")

            scenario_start = time.perf_counter()

            # Phase 1: Setup
            t0 = time.perf_counter()
            dashboard = CoordinatorDashboard(hub_db, bus_dir=bus_dir)
            phase_times["setup"].append((time.perf_counter() - t0) * 1000)

            # Phase 2: Registration
            t0 = time.perf_counter()
            agents = []
            for team_idx in range(num_teams):
                for agent_idx in range(agents_per_team):
                    aid = f"team{team_idx}-agent{agent_idx}"
                    agent = AgentReporter(hub_db, aid, f"team-{team_idx}",
                                          f"role-{agent_idx}", bus_dir=bus_dir)
                    agents.append(agent)
            phase_times["registration"].append((time.perf_counter() - t0) * 1000)

            # Phase 3: Work distribution
            t0 = time.perf_counter()
            ws = WorkStealing(ws_db, bus_dir, "coordinator", "coord-agent")
            work_ids = []
            for i in range(total_agents * 2):
                wid = ws.enqueue_work(f"e2e-task-{i}", f"desc-{i}",
                                      priority=i % 5 + 1)
                work_ids.append(wid)
            phase_times["work_distribution"].append((time.perf_counter() - t0) * 1000)

            # Phase 4: Work execution (steal + complete)
            t0 = time.perf_counter()
            for team_idx in range(num_teams):
                stealer = WorkStealing(ws_db, bus_dir, f"team-{team_idx}",
                                       f"team{team_idx}-agent0")
                for _ in range(agents_per_team):
                    item = stealer.steal_work()
                    if item:
                        stealer.complete_work(item["id"], {"done": True})
                stealer.close()
            phase_times["work_execution"].append((time.perf_counter() - t0) * 1000)

            # Phase 5: Help flow
            t0 = time.perf_counter()
            hp = HelpProtocol(hp_db, bus_dir, "team-0", "team0-agent0")
            for i in range(3):
                wi = hp.add_work_item(f"help-{i}", priority="high")
                hp.request_help(wi, f"Need assistance #{i}")
            hp.close()
            phase_times["help_flow"].append((time.perf_counter() - t0) * 1000)

            # Phase 6: Communication
            t0 = time.perf_counter()
            for i in range(total_agents):
                bus_write(bus_dir, "findings", "info",
                          {"finding": f"result-{i}", "confidence": 0.9},
                          f"team-{i % num_teams}",
                          f"team{i % num_teams}-agent{i % agents_per_team}")
            msgs, _ = bus_read(bus_dir, "findings", 0)
            phase_times["communication"].append((time.perf_counter() - t0) * 1000)

            # Phase 7: Dashboard
            t0 = time.perf_counter()
            dashboard.get_all_status()
            dashboard.get_active_agents()
            dashboard.get_summary()
            phase_times["dashboard"].append((time.perf_counter() - t0) * 1000)

            # Phase 8: Teardown
            t0 = time.perf_counter()
            for a in agents:
                a.report_complete("output.json")
                a.close()
            dashboard.close()
            ws.close()
            phase_times["teardown"].append((time.perf_counter() - t0) * 1000)

            scenario_times.append((time.perf_counter() - scenario_start) * 1000)

    return {
        "scenario_total": compute_stats(scenario_times),
        "phases": {name: compute_stats(times) for name, times in phase_times.items()},
        "config": {
            "num_teams": num_teams,
            "agents_per_team": agents_per_team,
            "total_agents": total_agents,
            "work_items": total_agents * 2,
        },
    }


# ===================================================================
# Regression detection (per SOP-035)
# ===================================================================

SOP_035_THRESHOLDS = {
    "latency_p95_increase_pct": 20.0,
    "throughput_decrease_pct": 15.0,
    "token_efficiency_increase_pct": 10.0,
}


def detect_regressions(current: dict, baseline: dict,
                       threshold_pct: float = 20.0) -> list[dict]:
    """Compare current results against baseline, flagging regressions."""
    regressions = []

    def _walk(prefix: str, cur: Any, base: Any):
        if isinstance(cur, dict) and isinstance(base, dict):
            for key in cur:
                if key in base:
                    _walk(f"{prefix}.{key}" if prefix else key, cur[key], base[key])
        elif isinstance(cur, (int, float)) and isinstance(base, (int, float)):
            if any(s in prefix for s in ["_ms", "latency", "duration"]):
                if base > 0:
                    change = ((cur - base) / base) * 100
                    if change > threshold_pct:
                        regressions.append({
                            "metric": prefix,
                            "baseline": round(base, 4),
                            "current": round(cur, 4),
                            "change_pct": round(change, 2),
                            "severity": "critical" if change > 50 else "warning",
                        })
            elif any(s in prefix for s in ["ops_per_sec", "throughput"]):
                if cur > 0:
                    change = ((base - cur) / base) * 100
                    if change > threshold_pct:
                        regressions.append({
                            "metric": prefix,
                            "baseline": round(base, 4),
                            "current": round(cur, 4),
                            "change_pct": round(-change, 2),
                            "severity": "critical" if change > 50 else "warning",
                        })

    _walk("", current, baseline)
    return regressions


# ===================================================================
# Main runner
# ===================================================================

SUITES = {
    "hub": ("Coordinator Hub", bench_coordinator_hub),
    "help": ("Help Protocol", bench_help_protocol),
    "stealing": ("Work Stealing", bench_work_stealing),
    "channels": ("Direct Channels", bench_direct_channels),
    "bus": ("Bus Throughput", bench_bus_throughput),
    "circuit": ("Circuit Breaker", bench_circuit_breaker),
    "capability": ("Capability Matching", bench_capability_matching),
    "loadbalancer": ("Load Balancer", bench_load_balancer),
    "quality": ("Quality Gates", bench_quality_gates),
    "sqlite": ("SQLite Contention", bench_sqlite_contention),
    "e2e": ("End-to-End Scenario", bench_end_to_end),
}


def run_benchmarks(suites: list[str] | None = None, iterations: int = 5,
                   num_agents: int = 8, baseline_path: str | None = None,
                   threshold_pct: float = 20.0) -> dict:
    """Run selected (or all) benchmark suites."""
    selected = suites or list(SUITES.keys())
    start_time = time.time()

    results = {}
    errors = []

    for suite_key in selected:
        if suite_key not in SUITES:
            errors.append(f"Unknown suite: {suite_key}")
            continue
        name, runner = SUITES[suite_key]
        print(f"  Running {name}...", end=" ", flush=True)
        try:
            # Pass appropriate kwargs based on the runner's signature
            import inspect
            sig = inspect.signature(runner)
            kwargs = {}
            if "iterations" in sig.parameters:
                kwargs["iterations"] = iterations
            if "num_agents" in sig.parameters:
                kwargs["num_agents"] = num_agents
            if "num_teams" in sig.parameters:
                kwargs["num_teams"] = num_agents
            result = runner(**kwargs)
            results[suite_key] = {"name": name, "benchmarks": result, "error": None}
            print("OK")
        except Exception as exc:
            results[suite_key] = {"name": name, "benchmarks": {}, "error": str(exc)}
            errors.append(f"{name}: {exc}")
            print(f"FAIL ({exc})")

    end_time = time.time()

    # Regression detection
    regressions = []
    if baseline_path and os.path.isfile(baseline_path):
        try:
            with open(baseline_path) as f:
                baseline = json.load(f)
            regressions = detect_regressions(
                results, baseline.get("results", {}), threshold_pct)
        except Exception as exc:
            regressions = [{"error": f"Baseline load failed: {exc}"}]

    report = {
        "benchmark_suite": "multi_agent_system_benchmark",
        "version": "2.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "iterations": iterations,
            "num_agents": num_agents,
            "suites_run": selected,
        },
        "total_duration_ms": round((end_time - start_time) * 1000, 3),
        "results": results,
        "regressions": regressions,
        "regression_count": len([r for r in regressions if "error" not in r]),
        "errors": errors,
        "all_pass": len(errors) == 0,
        "sop_035_thresholds": SOP_035_THRESHOLDS,
    }

    return report


def print_report(report: dict, verbose: bool = False):
    """Pretty-print the benchmark report to console."""
    print(f"\n{'=' * 70}")
    print(f"  MULTI-AGENT SYSTEM BENCHMARK REPORT")
    print(f"{'=' * 70}")
    print(f"  Timestamp:  {report['timestamp']}")
    print(f"  Duration:   {report['total_duration_ms']:.1f} ms")
    print(f"  Iterations: {report['config']['iterations']}")
    print(f"  Agents:     {report['config']['num_agents']}")
    print(f"  Suites:     {len(report['config']['suites_run'])}")
    print(f"{'=' * 70}")

    for key, data in report["results"].items():
        status = "PASS" if not data.get("error") else "FAIL"
        print(f"\n  [{status}] {data['name']}")
        if data.get("error"):
            print(f"        Error: {data['error']}")
        elif verbose:
            _print_nested(data["benchmarks"], indent=8)

    if report["regressions"]:
        print(f"\n{'=' * 70}")
        print(f"  REGRESSIONS: {report['regression_count']}")
        for reg in report["regressions"]:
            if "error" not in reg:
                print(f"    [{reg['severity'].upper()}] {reg['metric']}: "
                      f"{reg['baseline']} -> {reg['current']} ({reg['change_pct']:+.1f}%)")

    if report["errors"]:
        print(f"\n  ERRORS:")
        for err in report["errors"]:
            print(f"    - {err}")

    print(f"\n{'=' * 70}")
    print(f"  Result: {'ALL PASS' if report['all_pass'] else 'FAILURES DETECTED'}")
    print(f"{'=' * 70}\n")


def _print_nested(data: Any, indent: int = 0):
    """Recursively print nested dict data."""
    prefix = " " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                print(f"{prefix}{k}:")
                _print_nested(v, indent + 4)
            elif isinstance(v, float):
                print(f"{prefix}{k}: {v:.4f}")
            else:
                print(f"{prefix}{k}: {v}")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Agent System Benchmark Suite")
    parser.add_argument("--suite", "-s", action="append", default=None,
                        help="Benchmark suite to run (repeatable). Default: all")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSON report path")
    parser.add_argument("--baseline", "-b", default=None,
                        help="Baseline JSON for regression detection")
    parser.add_argument("--threshold", "-t", type=float, default=20.0,
                        help="Regression threshold %% (default: 20)")
    parser.add_argument("--iterations", "-n", type=int, default=5,
                        help="Iterations per benchmark (default: 5)")
    parser.add_argument("--agents", "-a", type=int, default=8,
                        help="Number of simulated agents (default: 8)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed per-benchmark results")
    parser.add_argument("--list", action="store_true",
                        help="List available benchmark suites")
    args = parser.parse_args()

    if args.list:
        print("Available benchmark suites:")
        for key, (name, _) in SUITES.items():
            print(f"  {key:15s} - {name}")
        return

    print(f"Multi-Agent System Benchmark v2.0.0")
    print(f"Running {len(args.suite or SUITES)} suites, "
          f"{args.iterations} iterations, {args.agents} agents\n")

    report = run_benchmarks(
        suites=args.suite,
        iterations=args.iterations,
        num_agents=args.agents,
        baseline_path=args.baseline,
        threshold_pct=args.threshold,
    )

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report written to {args.output}")

    print_report(report, verbose=args.verbose)

    sys.exit(0 if report["all_pass"] else 1)


if __name__ == "__main__":
    main()
