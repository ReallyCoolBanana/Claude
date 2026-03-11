#!/usr/bin/env python3
"""coord_stress_bench.py - Coordination infrastructure stress test & benchmark.

Empirically tests the coordination layer under realistic multi-agent conditions.
Measures throughput, latency, contention, and failure modes for:
- CoordinatorHub (agent registration, status updates, instruction delivery)
- HelpProtocol (work items, help requests, idle detection, auto-assignment)
- DirectChannels (messaging, presence, progress broadcasting)
- WorkStealing (queue operations, pipeline stages, scratchpad)

Also simulates realistic team configurations to find optimal team sizes
and identify weak links in the coordination chain.

Usage:
    python coord_stress_bench.py all              # Run all benchmarks
    python coord_stress_bench.py hub              # Just coordinator hub
    python coord_stress_bench.py help             # Just help protocol
    python coord_stress_bench.py channels         # Just direct channels
    python coord_stress_bench.py stealing         # Just work stealing
    python coord_stress_bench.py team-sizing      # Team size optimization
    python coord_stress_bench.py contention       # SQLite contention analysis
    python coord_stress_bench.py pipeline         # Pipeline throughput
    python coord_stress_bench.py --json           # JSON output
    python coord_stress_bench.py --output FILE    # Write to file
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "storage" / "coordination"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def timed(func):
    """Measure wall-clock time of a function call."""
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        return result, elapsed
    return wrapper


class BenchResult:
    """Collects benchmark measurements."""
    def __init__(self, name):
        self.name = name
        self.measurements = []
        self.errors = []
        self.metadata = {}

    def add(self, label, value_ms, detail=None):
        self.measurements.append({"label": label, "ms": round(value_ms, 3), "detail": detail})

    def add_error(self, label, error):
        self.errors.append({"label": label, "error": str(error)})

    def to_dict(self):
        times = [m["ms"] for m in self.measurements]
        stats = {}
        if times:
            sorted_t = sorted(times)
            n = len(sorted_t)
            stats = {
                "count": n,
                "min_ms": sorted_t[0],
                "max_ms": sorted_t[-1],
                "mean_ms": round(sum(sorted_t) / n, 3),
                "median_ms": sorted_t[n // 2],
                "p95_ms": sorted_t[int(n * 0.95)] if n >= 20 else sorted_t[-1],
                "total_ms": round(sum(sorted_t), 3),
            }
        return {
            "name": self.name,
            "stats": stats,
            "measurements": self.measurements,
            "errors": self.errors,
            "metadata": self.metadata,
            "passed": len(self.errors) == 0,
        }


# ---------------------------------------------------------------------------
# Coordinator Hub Benchmarks
# ---------------------------------------------------------------------------

def bench_hub(tmp_dir):
    """Benchmark CoordinatorHub operations."""
    from coordinator_hub import AgentReporter, CoordinatorDashboard

    db_path = os.path.join(tmp_dir, "hub_bench.db")
    results = []

    # 1. Agent registration throughput
    br = BenchResult("hub_registration_throughput")
    agents = []
    for i in range(50):
        start = time.perf_counter()
        r = AgentReporter(db_path, f"agent-{i}", f"team-{i % 5}", "worker")
        elapsed = (time.perf_counter() - start) * 1000
        br.add(f"register-agent-{i}", elapsed)
        agents.append(r)
    br.metadata["agent_count"] = 50
    br.metadata["team_count"] = 5
    results.append(br)

    # 2. Status update throughput
    br2 = BenchResult("hub_status_update_throughput")
    for i, agent in enumerate(agents):
        start = time.perf_counter()
        agent.update_status("working", i * 2, f"task-{i}")
        elapsed = (time.perf_counter() - start) * 1000
        br2.add(f"update-{i}", elapsed)
    results.append(br2)

    # 3. Dashboard read performance under load
    br3 = BenchResult("hub_dashboard_reads")
    dash = CoordinatorDashboard(db_path)
    for label, func in [
        ("get_all_status", dash.get_all_status),
        ("get_active_agents", dash.get_active_agents),
        ("get_blocked_agents", dash.get_blocked_agents),
        ("get_summary", dash.get_summary),
    ]:
        times = []
        for _ in range(100):
            start = time.perf_counter()
            func()
            times.append((time.perf_counter() - start) * 1000)
        br3.add(label, sum(times) / len(times), detail=f"mean of 100 calls")
    br3.metadata["agents_in_db"] = 50
    results.append(br3)

    # 4. Instruction delivery throughput
    br4 = BenchResult("hub_instruction_throughput")
    for i in range(100):
        start = time.perf_counter()
        dash.send_instruction(f"agent-{i % 50}", "task", json.dumps({"data": f"payload-{i}"}))
        elapsed = (time.perf_counter() - start) * 1000
        br4.add(f"instruction-{i}", elapsed)
    results.append(br4)

    # 5. Concurrent status updates (contention test)
    br5 = BenchResult("hub_concurrent_updates")
    barrier = threading.Barrier(10)
    thread_times = []
    thread_errors = []
    lock = threading.Lock()

    def concurrent_update(agent_idx):
        try:
            barrier.wait(timeout=5)
            start = time.perf_counter()
            agents[agent_idx].update_status("working", 50 + agent_idx, f"concurrent-{agent_idx}")
            elapsed = (time.perf_counter() - start) * 1000
            with lock:
                thread_times.append(elapsed)
        except Exception as e:
            with lock:
                thread_errors.append(str(e))

    threads = [threading.Thread(target=concurrent_update, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    for i, ms in enumerate(thread_times):
        br5.add(f"concurrent-{i}", ms)
    for e in thread_errors:
        br5.add_error("concurrent", e)
    br5.metadata["threads"] = 10
    br5.metadata["errors"] = len(thread_errors)
    results.append(br5)

    # Cleanup
    for a in agents:
        a.close()
    dash.close()

    return results


# ---------------------------------------------------------------------------
# Help Protocol Benchmarks
# ---------------------------------------------------------------------------

def bench_help(tmp_dir):
    """Benchmark HelpProtocol operations."""
    from help_protocol import HelpProtocol

    db_path = os.path.join(tmp_dir, "help_bench.db")
    bus_dir = os.path.join(tmp_dir, "bus")
    os.makedirs(bus_dir, exist_ok=True)
    results = []

    # Setup teams
    teams = []
    for i in range(10):
        hp = HelpProtocol(db_path, bus_dir, f"team-{i}", f"agent-{i}")
        hp.register_capabilities([f"cap-{i}", f"cap-{i % 3}", "general"])
        hp.update_status("idle", 0, "waiting")
        teams.append(hp)

    # 1. Work item creation throughput
    br = BenchResult("help_work_item_throughput")
    work_ids = []
    for i in range(100):
        start = time.perf_counter()
        wid = teams[0].add_work_item(f"task-{i}", f"description-{i}", "medium", 10)
        elapsed = (time.perf_counter() - start) * 1000
        br.add(f"add-work-{i}", elapsed)
        work_ids.append(wid)
    results.append(br)

    # 2. Help request creation
    br2 = BenchResult("help_request_throughput")
    request_ids = []
    for i in range(20):
        teams[0].update_status("needs_help", 30, "overloaded")
        start = time.perf_counter()
        rid = teams[0].request_help(work_ids[i], f"Need help with task {i}")
        elapsed = (time.perf_counter() - start) * 1000
        br2.add(f"request-{i}", elapsed)
        request_ids.append(rid)
    results.append(br2)

    # 3. Help offer race (multiple teams competing)
    br3 = BenchResult("help_offer_race")
    winners = 0
    losers = 0
    for rid in request_ids[:5]:
        # Reset: create a fresh request for fair race
        wid = teams[0].add_work_item(f"race-task-{rid}", "race", "high", 5)
        fresh_rid = teams[0].request_help(wid, "race test")

        barrier = threading.Barrier(5)
        offer_results = []
        lock = threading.Lock()

        def try_offer(team_idx, request_id):
            try:
                barrier.wait(timeout=5)
                start = time.perf_counter()
                won = teams[team_idx].offer_help(request_id)
                elapsed = (time.perf_counter() - start) * 1000
                with lock:
                    offer_results.append((team_idx, won, elapsed))
            except Exception as e:
                with lock:
                    offer_results.append((team_idx, False, 0))

        threads = [threading.Thread(target=try_offer, args=(i + 1, fresh_rid)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for idx, won, ms in offer_results:
            br3.add(f"offer-team-{idx}-req-{rid}", ms, detail="won" if won else "lost")
            if won:
                winners += 1
            else:
                losers += 1

    br3.metadata["total_winners"] = winners
    br3.metadata["total_losers"] = losers
    br3.metadata["races"] = 5
    results.append(br3)

    # 4. Idle detection speed
    br4 = BenchResult("help_idle_detection")
    for i in range(5):
        teams[i + 5].update_status("idle", 100, "done")
    for _ in range(50):
        start = time.perf_counter()
        idle = teams[0].get_idle_teams()
        elapsed = (time.perf_counter() - start) * 1000
        br4.add("get_idle_teams", elapsed, detail=f"found {len(idle)}")
    results.append(br4)

    # 5. Auto-assignment throughput
    br5 = BenchResult("help_auto_assignment")
    # Create requests needing help
    for i in range(5):
        wid = teams[0].add_work_item(f"auto-task-{i}", "auto", "medium", 5, ["general"])
        teams[0].request_help(wid, f"auto help {i}")
    start = time.perf_counter()
    assignments = teams[0].auto_assign_idle_teams()
    elapsed = (time.perf_counter() - start) * 1000
    br5.add("auto_assign", elapsed, detail=f"{len(assignments)} assignments")
    br5.metadata["assignments"] = len(assignments)
    results.append(br5)

    for t in teams:
        t.close()
    return results


# ---------------------------------------------------------------------------
# Direct Channels Benchmarks
# ---------------------------------------------------------------------------

def bench_channels(tmp_dir):
    """Benchmark DirectChannels operations."""
    from direct_channels import DirectChannels

    db_path = os.path.join(tmp_dir, "channels_bench.db")
    bus_dir = os.path.join(tmp_dir, "bus")
    os.makedirs(bus_dir, exist_ok=True)
    results = []

    # Setup teams
    channels = []
    for i in range(10):
        dc = DirectChannels(db_path, bus_dir, f"team-{i}", f"agent-{i}")
        dc.set_presence("available")
        channels.append(dc)

    # 1. Channel creation throughput
    br = BenchResult("channels_creation_throughput")
    for i in range(1, 10):
        start = time.perf_counter()
        channels[0].create_direct_channel(f"team-{i}")
        elapsed = (time.perf_counter() - start) * 1000
        br.add(f"create-channel-{i}", elapsed)
    results.append(br)

    # 2. Message send throughput
    br2 = BenchResult("channels_message_throughput")
    for i in range(100):
        target = f"team-{(i % 9) + 1}"
        start = time.perf_counter()
        channels[0].send_direct(target, "info", {"data": f"message-{i}", "seq": i})
        elapsed = (time.perf_counter() - start) * 1000
        br2.add(f"send-{i}", elapsed)
    results.append(br2)

    # 3. Message read throughput
    br3 = BenchResult("channels_message_read")
    for i in range(1, 10):
        start = time.perf_counter()
        msgs = channels[i].read_direct(f"team-0")
        elapsed = (time.perf_counter() - start) * 1000
        br3.add(f"read-team-{i}", elapsed, detail=f"{len(msgs)} messages")
    results.append(br3)

    # 4. Progress broadcasting
    br4 = BenchResult("channels_progress_broadcast")
    for i in range(10):
        start = time.perf_counter()
        channels[i].update_progress("phase-1", i * 10, 100, i * 10)
        elapsed = (time.perf_counter() - start) * 1000
        br4.add(f"progress-team-{i}", elapsed)
    results.append(br4)

    # 5. Bottleneck detection
    br5 = BenchResult("channels_bottleneck_detection")
    for _ in range(50):
        start = time.perf_counter()
        slowest = channels[0].get_slowest_team()
        elapsed = (time.perf_counter() - start) * 1000
        br5.add("get_slowest", elapsed)
    results.append(br5)

    # 6. Concurrent messaging (contention)
    br6 = BenchResult("channels_concurrent_messaging")
    barrier = threading.Barrier(8)
    msg_times = []
    msg_errors = []
    lock = threading.Lock()

    def concurrent_send(sender_idx):
        try:
            barrier.wait(timeout=5)
            times = []
            for j in range(10):
                target = f"team-{(sender_idx + j + 1) % 10}"
                start = time.perf_counter()
                channels[sender_idx].send_direct(target, "info", {"from": sender_idx, "seq": j})
                times.append((time.perf_counter() - start) * 1000)
            with lock:
                msg_times.extend(times)
        except Exception as e:
            with lock:
                msg_errors.append(str(e))

    threads = [threading.Thread(target=concurrent_send, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    for i, ms in enumerate(msg_times):
        br6.add(f"concurrent-msg-{i}", ms)
    for e in msg_errors:
        br6.add_error("concurrent", e)
    br6.metadata["threads"] = 8
    br6.metadata["messages_per_thread"] = 10
    br6.metadata["total_messages"] = len(msg_times)
    br6.metadata["errors"] = len(msg_errors)
    results.append(br6)

    for c in channels:
        c.close()
    return results


# ---------------------------------------------------------------------------
# Work Stealing Benchmarks
# ---------------------------------------------------------------------------

def bench_stealing(tmp_dir):
    """Benchmark WorkStealing, Pipeline, and Scratchpad."""
    from work_stealing import WorkStealing, PipelineManager, Scratchpad

    db_path = os.path.join(tmp_dir, "ws_bench.db")
    bus_dir = os.path.join(tmp_dir, "bus")
    os.makedirs(bus_dir, exist_ok=True)
    results = []

    # 1. Work queue enqueue throughput
    ws = WorkStealing(db_path, bus_dir, "team-0", "agent-0")
    br = BenchResult("stealing_enqueue_throughput")
    for i in range(200):
        start = time.perf_counter()
        ws.enqueue_work(f"task-{i}", f"description-{i}", priority=i % 10)
        elapsed = (time.perf_counter() - start) * 1000
        br.add(f"enqueue-{i}", elapsed)
    results.append(br)

    # 2. Work steal throughput (single thread)
    br2 = BenchResult("stealing_steal_single")
    stolen = 0
    for i in range(200):
        start = time.perf_counter()
        item = ws.steal_work()
        elapsed = (time.perf_counter() - start) * 1000
        if item:
            stolen += 1
            br2.add(f"steal-{i}", elapsed)
            ws.complete_work(item["id"], {"result": "done"})
    br2.metadata["items_stolen"] = stolen
    results.append(br2)

    # 3. Concurrent work stealing (thundering herd)
    br3 = BenchResult("stealing_thundering_herd")
    # Re-enqueue
    for i in range(50):
        ws.enqueue_work(f"herd-task-{i}", "herd", priority=5)

    barrier = threading.Barrier(10)
    steal_results = []
    lock = threading.Lock()

    stealers = []
    for i in range(10):
        s = WorkStealing(db_path, bus_dir, f"stealer-{i}", f"agent-{i}")
        stealers.append(s)

    def steal_race(stealer_idx):
        try:
            barrier.wait(timeout=5)
            local_stolen = 0
            times = []
            for _ in range(10):
                start = time.perf_counter()
                item = stealers[stealer_idx].steal_work()
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
                if item:
                    local_stolen += 1
                    stealers[stealer_idx].complete_work(item["id"], {"done": True})
            with lock:
                steal_results.append({"stealer": stealer_idx, "stolen": local_stolen, "times": times})
        except Exception as e:
            with lock:
                steal_results.append({"stealer": stealer_idx, "stolen": 0, "error": str(e)})

    threads = [threading.Thread(target=steal_race, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    total_stolen = sum(r["stolen"] for r in steal_results)
    all_times = []
    for r in steal_results:
        for ms in r.get("times", []):
            all_times.append(ms)
            br3.add(f"steal-{r['stealer']}", ms)
    br3.metadata["threads"] = 10
    br3.metadata["items_available"] = 50
    br3.metadata["total_stolen"] = total_stolen
    br3.metadata["no_double_steal"] = total_stolen <= 50
    results.append(br3)

    for s in stealers:
        s.close()

    # 4. Pipeline creation + stage progression
    br4 = BenchResult("pipeline_throughput")
    pm = PipelineManager(db_path, bus_dir, "team-0", "agent-0")
    stages = [
        {"name": "gather", "depends_on": []},
        {"name": "process-a", "depends_on": ["gather"]},
        {"name": "process-b", "depends_on": ["gather"]},
        {"name": "merge", "depends_on": ["process-a", "process-b"]},
        {"name": "output", "depends_on": ["merge"]},
    ]
    start = time.perf_counter()
    pid = pm.create_pipeline("bench-pipeline", stages)
    br4.add("create_pipeline", (time.perf_counter() - start) * 1000)

    for stage_name in ["gather", "process-a", "process-b", "merge", "output"]:
        start = time.perf_counter()
        pm.start_stage(pid, stage_name)
        br4.add(f"start-{stage_name}", (time.perf_counter() - start) * 1000)

        start = time.perf_counter()
        pm.complete_stage(pid, stage_name, {"data": f"{stage_name}-output"})
        br4.add(f"complete-{stage_name}", (time.perf_counter() - start) * 1000)

        start = time.perf_counter()
        pm.trigger_downstream(pid, stage_name)
        br4.add(f"trigger-{stage_name}", (time.perf_counter() - start) * 1000)

    results.append(br4)

    # 5. Scratchpad throughput
    br5 = BenchResult("scratchpad_throughput")
    sp = Scratchpad(db_path, "team-0", "agent-0")
    for i in range(100):
        start = time.perf_counter()
        sp.write(f"key-{i}", {"data": f"value-{i}", "index": i}, namespace="global")
        elapsed = (time.perf_counter() - start) * 1000
        br5.add(f"write-{i}", elapsed)

    for i in range(100):
        start = time.perf_counter()
        val = sp.read(f"key-{i}", namespace="global")
        elapsed = (time.perf_counter() - start) * 1000
        br5.add(f"read-{i}", elapsed)

    results.append(br5)

    ws.close()
    pm.close()
    sp.close()
    return results


# ---------------------------------------------------------------------------
# Team Sizing Optimization
# ---------------------------------------------------------------------------

def bench_team_sizing(tmp_dir):
    """Simulate different team sizes and measure coordination overhead."""
    from coordinator_hub import AgentReporter, CoordinatorDashboard
    from help_protocol import HelpProtocol

    results = []

    for team_size in [1, 2, 3, 5, 8, 10, 15, 20, 30, 50]:
        br = BenchResult(f"team_size_{team_size}")
        db_path = os.path.join(tmp_dir, f"sizing_{team_size}.db")
        bus_dir = os.path.join(tmp_dir, f"bus_{team_size}")
        os.makedirs(bus_dir, exist_ok=True)

        # Simulate: register agents, each updates status 10 times, coordinator reads
        agents = []
        reg_start = time.perf_counter()
        for i in range(team_size):
            a = AgentReporter(db_path, f"agent-{i}", "team-main", "worker")
            agents.append(a)
        reg_time = (time.perf_counter() - reg_start) * 1000
        br.add("registration_total", reg_time, detail=f"{team_size} agents")

        # Each agent does 10 status updates
        update_start = time.perf_counter()
        for a in agents:
            for j in range(10):
                a.update_status("working", j * 10, f"step-{j}")
        update_time = (time.perf_counter() - update_start) * 1000
        br.add("all_updates", update_time, detail=f"{team_size * 10} updates")

        # Coordinator reads
        dash = CoordinatorDashboard(db_path)
        read_start = time.perf_counter()
        for _ in range(10):
            dash.get_all_status()
            dash.get_summary()
            dash.get_stale_agents()
        read_time = (time.perf_counter() - read_start) * 1000
        br.add("coordinator_reads", read_time, detail="30 queries")

        # Instructions to all agents
        instr_start = time.perf_counter()
        dash.broadcast_instruction("check", json.dumps({"msg": "status check"}))
        instr_time = (time.perf_counter() - instr_start) * 1000
        br.add("broadcast_instruction", instr_time, detail=f"to {team_size} agents")

        # Help protocol overhead
        hp = HelpProtocol(db_path, bus_dir, "team-main", "coord")
        hp.register_capabilities(["general"])
        help_start = time.perf_counter()
        for i in range(min(team_size, 10)):
            hp.add_work_item(f"task-{i}", "work", "medium", 5)
        help_time = (time.perf_counter() - help_start) * 1000
        br.add("work_item_creation", help_time, detail=f"{min(team_size, 10)} items")

        # Compute per-agent overhead
        total_overhead = reg_time + update_time + read_time + instr_time + help_time
        br.metadata["team_size"] = team_size
        br.metadata["total_overhead_ms"] = round(total_overhead, 3)
        br.metadata["per_agent_overhead_ms"] = round(total_overhead / team_size, 3)
        br.metadata["updates_per_agent"] = 10
        br.metadata["overhead_pct_of_1s_work"] = round(total_overhead / (team_size * 1000) * 100, 3)

        for a in agents:
            a.close()
        dash.close()
        hp.close()

        results.append(br)

    return results


# ---------------------------------------------------------------------------
# SQLite Contention Analysis
# ---------------------------------------------------------------------------

def bench_contention(tmp_dir):
    """Measure SQLite WAL contention at different thread counts."""
    from coordinator_hub import AgentReporter

    results = []

    for n_threads in [1, 2, 4, 8, 16, 32]:
        br = BenchResult(f"contention_{n_threads}_threads")
        db_path = os.path.join(tmp_dir, f"contention_{n_threads}.db")

        agents = [AgentReporter(db_path, f"agent-{i}", "team-0", "worker") for i in range(n_threads)]

        barrier = threading.Barrier(n_threads)
        all_times = []
        all_errors = []
        lock = threading.Lock()

        def contend(agent_idx):
            try:
                barrier.wait(timeout=10)
                local_times = []
                for j in range(50):
                    start = time.perf_counter()
                    agents[agent_idx].update_status("working", j * 2, f"task-{j}")
                    local_times.append((time.perf_counter() - start) * 1000)
                with lock:
                    all_times.extend(local_times)
            except Exception as e:
                with lock:
                    all_errors.append(str(e))

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        for i, ms in enumerate(all_times):
            br.add(f"update-{i}", ms)

        sorted_t = sorted(all_times) if all_times else [0]
        n = len(sorted_t)
        br.metadata["threads"] = n_threads
        br.metadata["total_ops"] = n
        br.metadata["errors"] = len(all_errors)
        br.metadata["ops_per_second"] = round(n / (sum(sorted_t) / 1000) if sum(sorted_t) > 0 else 0, 1)
        br.metadata["p50_ms"] = round(sorted_t[n // 2], 3) if n else 0
        br.metadata["p95_ms"] = round(sorted_t[int(n * 0.95)], 3) if n >= 20 else 0
        br.metadata["p99_ms"] = round(sorted_t[int(n * 0.99)], 3) if n >= 100 else 0

        for a in agents:
            a.close()
        results.append(br)

    return results


# ---------------------------------------------------------------------------
# Pipeline Throughput
# ---------------------------------------------------------------------------

def bench_pipeline(tmp_dir):
    """Benchmark pipeline creation and stage progression at scale."""
    from work_stealing import PipelineManager

    db_path = os.path.join(tmp_dir, "pipeline_bench.db")
    bus_dir = os.path.join(tmp_dir, "bus")
    os.makedirs(bus_dir, exist_ok=True)

    results = []

    for n_stages in [3, 5, 10, 20]:
        br = BenchResult(f"pipeline_{n_stages}_stages")
        pm = PipelineManager(db_path, bus_dir, "team-0", "agent-0")

        # Linear pipeline
        stages = [{"name": f"stage-{i}", "depends_on": [f"stage-{i-1}"] if i > 0 else []} for i in range(n_stages)]

        start = time.perf_counter()
        pid = pm.create_pipeline(f"linear-{n_stages}", stages)
        br.add("create", (time.perf_counter() - start) * 1000)

        for i in range(n_stages):
            start = time.perf_counter()
            pm.start_stage(pid, f"stage-{i}")
            pm.complete_stage(pid, f"stage-{i}", {"out": i})
            pm.trigger_downstream(pid, f"stage-{i}")
            elapsed = (time.perf_counter() - start) * 1000
            br.add(f"stage-{i}-cycle", elapsed)

        start = time.perf_counter()
        status = pm.get_pipeline_status(pid)
        br.add("get_status", (time.perf_counter() - start) * 1000)

        br.metadata["stages"] = n_stages
        br.metadata["pipeline_type"] = "linear"
        pm.close()
        results.append(br)

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_human(all_results):
    lines = ["=" * 70, "COORDINATION STRESS TEST & BENCHMARK RESULTS", "=" * 70, ""]

    for category_results in all_results:
        for br_dict in category_results:
            name = br_dict["name"]
            stats = br_dict.get("stats", {})
            meta = br_dict.get("metadata", {})
            passed = br_dict.get("passed", True)

            status = "PASS" if passed else "FAIL"
            lines.append(f"[{status}] {name}")
            if stats:
                lines.append(f"  ops={stats.get('count', 0)}  "
                             f"mean={stats.get('mean_ms', 0)}ms  "
                             f"median={stats.get('median_ms', 0)}ms  "
                             f"p95={stats.get('p95_ms', 0)}ms  "
                             f"total={stats.get('total_ms', 0)}ms")
            if meta:
                meta_str = "  " + "  ".join(f"{k}={v}" for k, v in meta.items())
                lines.append(meta_str)
            if br_dict.get("errors"):
                for e in br_dict["errors"][:3]:
                    lines.append(f"  ! {e['label']}: {e['error']}")
            lines.append("")

    # Team sizing summary
    sizing_results = [r for cat in all_results for r in cat if r["name"].startswith("team_size_")]
    if sizing_results:
        lines.append("=" * 70)
        lines.append("TEAM SIZING ANALYSIS")
        lines.append(f"{'Size':>6} {'Total OH(ms)':>14} {'Per-Agent(ms)':>14} {'OH% of 1s':>12}")
        lines.append("-" * 50)
        for r in sizing_results:
            m = r["metadata"]
            lines.append(f"{m['team_size']:>6} {m['total_overhead_ms']:>14.1f} "
                         f"{m['per_agent_overhead_ms']:>14.1f} {m['overhead_pct_of_1s_work']:>12.3f}%")
        lines.append("")

    # Contention summary
    contention_results = [r for cat in all_results for r in cat if r["name"].startswith("contention_")]
    if contention_results:
        lines.append("=" * 70)
        lines.append("SQLITE CONTENTION ANALYSIS")
        lines.append(f"{'Threads':>8} {'Ops/sec':>10} {'p50(ms)':>10} {'p95(ms)':>10} {'p99(ms)':>10} {'Errors':>8}")
        lines.append("-" * 60)
        for r in contention_results:
            m = r["metadata"]
            lines.append(f"{m['threads']:>8} {m['ops_per_second']:>10.0f} "
                         f"{m['p50_ms']:>10.3f} {m['p95_ms']:>10.3f} "
                         f"{m['p99_ms']:>10.3f} {m['errors']:>8}")
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

BENCH_MAP = {
    "hub": bench_hub,
    "help": bench_help,
    "channels": bench_channels,
    "stealing": bench_stealing,
    "team-sizing": bench_team_sizing,
    "contention": bench_contention,
    "pipeline": bench_pipeline,
}


def main():
    parser = argparse.ArgumentParser(description="Coordination infrastructure stress test & benchmark")
    parser.add_argument("targets", nargs="*", default=["all"],
                        help="Benchmark targets: all, hub, help, channels, stealing, team-sizing, contention, pipeline")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--output", "-o", help="Write results to file")
    args = parser.parse_args()

    targets = args.targets
    if "all" in targets:
        targets = list(BENCH_MAP.keys())

    all_results = []
    with tempfile.TemporaryDirectory(prefix="coord_bench_") as tmp_dir:
        for target in targets:
            if target not in BENCH_MAP:
                print(f"Unknown target: {target}. Available: {', '.join(BENCH_MAP)}", file=sys.stderr)
                continue
            print(f"Running {target}...", file=sys.stderr)
            try:
                bench_results = BENCH_MAP[target](tmp_dir)
                all_results.append([r.to_dict() for r in bench_results])
            except Exception as e:
                print(f"ERROR in {target}: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
                all_results.append([{"name": target, "error": str(e), "passed": False}])

    # Output
    if args.json:
        flat = [r for cat in all_results for r in cat]
        output = json.dumps({"benchmarks": flat, "targets": targets}, indent=2)
    else:
        output = format_human(all_results)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
        print(f"Results written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
