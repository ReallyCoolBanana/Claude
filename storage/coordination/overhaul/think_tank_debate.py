#!/usr/bin/env python3
"""
Think Tank Debate: Multi-Agent Coordination Strategy Optimization

5 specialized think tank teams debate alternative coordination strategies,
incorporating external research on work-stealing algorithms, distributed
task allocation, and lock-free queue designs.

Team Structure (optimized per SOP-011 guidelines):
  TT-A: Theory Team (3 agents) — CS theory, algorithm analysis, complexity bounds
  TT-B: Systems Team (3 agents) — Practical implementation, SQLite constraints, Proto A
  TT-C: Challenger Team (2 agents) — Devil's advocate, finds failure modes
  TT-D: Synthesis Team (2 agents) — Merges proposals, resolves conflicts
  TT-E: Validation Team (3 agents) — Benchmarks each proposed strategy empirically

Each team proposes solutions, debates trade-offs, and the validation team
runs benchmarks to settle disputes with data.

External Research Integrated:
  - Chase-Lev work-stealing deque pattern (per-worker deques + stealing)
  - Contract Net Protocol (task announcement → bidding → assignment)
  - Cilk/Go scheduler patterns (local run queues + global fallback)
  - Exponential backoff with jitter for contention avoidance
  - Two-level scheduling (coarse then fine-grained)
"""

import json
import math
import os
import queue
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

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "prototype"))

from agent_comm.bus import BusWriter, BusReader
from storage.coordination.coordinator_hub import AgentReporter
from storage.coordination.direct_channels import DirectChannels
from storage.coordination.work_stealing import WorkStealing, Scratchpad

OUTPUT_DIR = Path(__file__).parent / "output"
JOIN_TIMEOUT = 60
WORK_ITEMS = 500
NUM_WORKERS = 10
SIMULATED_WORK_MS = 1


@dataclass
class Proposal:
    """A strategy proposal from a think tank team."""
    team: str
    strategy_name: str
    description: str
    rationale: str
    expected_improvement: str
    trade_offs: list
    implementation_complexity: str  # low/medium/high
    inspiration: str  # external research source


@dataclass
class DebatePoint:
    """A point raised during the debate."""
    team: str
    target_proposal: str
    point_type: str  # support/challenge/question/synthesis
    argument: str
    evidence: str


@dataclass
class BenchmarkResult:
    strategy: str
    throughput: float
    distribution_cv: float
    steal_ratio: float
    coordination_ops: int
    items_completed: int
    duration_s: float
    items_per_worker: list
    extra: dict = field(default_factory=dict)


def compute_cv(values):
    if len(values) < 2 or statistics.mean(values) == 0:
        return 0.0
    return statistics.stdev(values) / statistics.mean(values)


# ===================================================================
# Benchmark Harness (used by TT-E Validation Team)
# ===================================================================

def _batch_steal(db_path, team, batch_size):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, owner_team, title, description, priority, created_at "
            "FROM work_queue WHERE status='queued' "
            "ORDER BY priority ASC, created_at ASC LIMIT ?",
            (batch_size,),
        ).fetchall()
        if not rows:
            conn.execute("ROLLBACK")
            conn.close()
            return []
        items = []
        now = time.time()
        for row in rows:
            conn.execute(
                "UPDATE work_queue SET status='claimed', claimed_by=?, claimed_at=? WHERE id=?",
                (team, now, row["id"]),
            )
            items.append(dict(row))
        conn.execute("COMMIT")
        conn.close()
        return items
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        return []


def run_benchmark(strategy_name, strategy_fn) -> BenchmarkResult:
    """Run a strategy and return standardized results."""
    try:
        return strategy_fn()
    except Exception as e:
        return BenchmarkResult(
            strategy=strategy_name, throughput=0, distribution_cv=1.0,
            steal_ratio=999, coordination_ops=0, items_completed=0,
            duration_s=0, items_per_worker=[], extra={"error": str(e)},
        )


# ===================================================================
# Strategy Implementations (from proposals)
# ===================================================================

# --- Proposal 1: Chase-Lev Per-Worker Deques ---
# Inspired by: Cilk work-stealing, Chase-Lev deque paper
# Each worker has a local deque. Work is initially distributed.
# When a worker's deque is empty, it steals from a random other worker's deque.
# No shared DB needed for the hot path.

def strategy_chase_lev_deques() -> BenchmarkResult:
    """Per-worker deques with random stealing from other workers' deques."""
    worker_deques = [[] for _ in range(NUM_WORKERS)]

    # Initial round-robin distribution
    for i in range(WORK_ITEMS):
        worker_deques[i % NUM_WORKERS].append(i)

    # Add locks per deque for thread safety
    deque_locks = [threading.Lock() for _ in range(NUM_WORKERS)]
    barrier = threading.Barrier(NUM_WORKERS)
    stats = {}
    stats_lock = threading.Lock()

    def worker(idx):
        items_done = 0
        steal_time = 0.0
        work_time = 0.0
        steal_ops = 0

        barrier.wait(timeout=JOIN_TIMEOUT)

        while True:
            # Try own deque first (no contention)
            item = None
            with deque_locks[idx]:
                if worker_deques[idx]:
                    item = worker_deques[idx].pop()

            if item is not None:
                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                work_time += time.perf_counter() - t0
                items_done += 1
                continue

            # Own deque empty — steal from random other worker
            stolen = False
            victims = list(range(NUM_WORKERS))
            random.shuffle(victims)
            for victim in victims:
                if victim == idx:
                    continue
                t0 = time.perf_counter()
                with deque_locks[victim]:
                    if worker_deques[victim]:
                        # Steal from the BOTTOM (opposite end) like Chase-Lev
                        item = worker_deques[victim].pop(0)
                        stolen = True
                steal_time += time.perf_counter() - t0
                steal_ops += 1
                if stolen:
                    break

            if not stolen:
                # Check if all deques are empty
                all_empty = True
                for d in range(NUM_WORKERS):
                    with deque_locks[d]:
                        if worker_deques[d]:
                            all_empty = False
                            break
                if all_empty:
                    break
                continue

            t0 = time.perf_counter()
            time.sleep(SIMULATED_WORK_MS / 1000)
            work_time += time.perf_counter() - t0
            items_done += 1

        with stats_lock:
            stats[idx] = {"items": items_done, "steal": steal_time,
                          "work": work_time, "ops": steal_ops}

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(NUM_WORKERS)]
    total = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return BenchmarkResult(
        strategy="chase_lev_deques",
        throughput=round(total / max(duration, 0.001), 2),
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        items_completed=total,
        duration_s=round(duration, 3),
        items_per_worker=items_per_worker,
        extra={"inspiration": "Chase-Lev deque, Cilk runtime"},
    )


# --- Proposal 2: Contract Net Protocol ---
# Inspired by: FIPA Contract Net, multi-agent systems research
# Coordinator announces tasks, workers bid based on capacity,
# coordinator selects best bid. Reduces contention by serializing
# assignment through a single coordinator.

def strategy_contract_net() -> BenchmarkResult:
    """Contract net: coordinator announces, workers bid, best bid wins."""
    task_queue = queue.Queue()
    for i in range(WORK_ITEMS):
        task_queue.put(i)

    # Bidding protocol: coordinator posts batches, workers bid
    bid_queue = queue.Queue()  # (worker_id, capacity)
    assignment_queues = [queue.Queue() for _ in range(NUM_WORKERS)]
    coordinator_done = threading.Event()
    barrier = threading.Barrier(NUM_WORKERS + 1)
    stats = {}
    stats_lock = threading.Lock()

    def coordinator():
        barrier.wait(timeout=JOIN_TIMEOUT)
        batch_size = 10
        while not task_queue.empty():
            # Collect bids (non-blocking, with timeout)
            bids = []
            deadline = time.time() + 0.005  # 5ms bidding window
            while time.time() < deadline:
                try:
                    bid = bid_queue.get_nowait()
                    bids.append(bid)
                except queue.Empty:
                    time.sleep(0.0001)

            if not bids:
                # No bids — assign to round-robin
                for wid in range(NUM_WORKERS):
                    items = []
                    for _ in range(batch_size // NUM_WORKERS + 1):
                        try:
                            items.append(task_queue.get_nowait())
                        except queue.Empty:
                            break
                    if items:
                        assignment_queues[wid].put(items)
                continue

            # Sort bids by capacity (highest first)
            bids.sort(key=lambda b: b[1], reverse=True)

            for worker_id, capacity in bids:
                items = []
                to_assign = min(capacity, batch_size)
                for _ in range(to_assign):
                    try:
                        items.append(task_queue.get_nowait())
                    except queue.Empty:
                        break
                if items:
                    assignment_queues[worker_id].put(items)

        coordinator_done.set()
        for wid in range(NUM_WORKERS):
            assignment_queues[wid].put(None)  # Sentinel

    def worker(idx):
        items_done = 0
        work_time = 0.0
        wait_time = 0.0

        barrier.wait(timeout=JOIN_TIMEOUT)

        while True:
            # Submit bid
            bid_queue.put((idx, 10))  # Always bid for 10

            t0 = time.perf_counter()
            try:
                assignment = assignment_queues[idx].get(timeout=2)
            except queue.Empty:
                if coordinator_done.is_set():
                    break
                continue
            wait_time += time.perf_counter() - t0

            if assignment is None:
                break

            for item in assignment:
                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                work_time += time.perf_counter() - t0
                items_done += 1

        with stats_lock:
            stats[idx] = {"items": items_done, "wait": wait_time,
                          "work": work_time}

    t_start = time.time()
    coord_thread = threading.Thread(target=coordinator)
    worker_threads = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_WORKERS)]
    coord_thread.start()
    for t in worker_threads:
        t.start()
    coord_thread.join(timeout=JOIN_TIMEOUT)
    for t in worker_threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(NUM_WORKERS)]
    total = sum(items_per_worker)
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_wait = sum(s.get("wait", 0) for s in stats.values())

    return BenchmarkResult(
        strategy="contract_net_protocol",
        throughput=round(total / max(duration, 0.001), 2),
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_ratio=round(total_wait / max(total_work, 0.001), 3),
        coordination_ops=0,
        items_completed=total,
        duration_s=round(duration, 3),
        items_per_worker=items_per_worker,
        extra={"inspiration": "FIPA Contract Net Protocol"},
    )


# --- Proposal 3: Go-style Local Run Queues ---
# Inspired by: Go runtime scheduler (GMP model)
# Each worker has a local run queue (bounded, size 256).
# New work goes to local queue first. If full, half is moved to global queue.
# Workers check: local → global → steal from others.

def strategy_go_scheduler() -> BenchmarkResult:
    """Go-style: local queues + global fallback + work stealing."""
    LOCAL_QUEUE_SIZE = 64
    global_queue = queue.Queue()
    local_queues = [queue.Queue() for _ in range(NUM_WORKERS)]
    local_locks = [threading.Lock() for _ in range(NUM_WORKERS)]

    # Initial distribution: fill local queues, overflow to global
    for i in range(WORK_ITEMS):
        target = i % NUM_WORKERS
        if local_queues[target].qsize() < LOCAL_QUEUE_SIZE:
            local_queues[target].put(i)
        else:
            global_queue.put(i)

    barrier = threading.Barrier(NUM_WORKERS)
    stats = {}
    stats_lock = threading.Lock()

    def worker(idx):
        items_done = 0
        steal_time = 0.0
        work_time = 0.0
        steal_ops = 0
        source_local = 0
        source_global = 0
        source_stolen = 0

        barrier.wait(timeout=JOIN_TIMEOUT)

        consecutive_misses = 0
        while consecutive_misses < 3:
            item = None

            # 1. Try local queue (fast path, no contention)
            try:
                item = local_queues[idx].get_nowait()
                source_local += 1
            except queue.Empty:
                pass

            # 2. Try global queue
            if item is None:
                t0 = time.perf_counter()
                try:
                    item = global_queue.get_nowait()
                    source_global += 1
                except queue.Empty:
                    pass
                steal_time += time.perf_counter() - t0
                steal_ops += 1

            # 3. Steal from random other worker
            if item is None:
                victims = list(range(NUM_WORKERS))
                random.shuffle(victims)
                for victim in victims:
                    if victim == idx:
                        continue
                    t0 = time.perf_counter()
                    try:
                        item = local_queues[victim].get_nowait()
                        source_stolen += 1
                        steal_time += time.perf_counter() - t0
                        steal_ops += 1
                        break
                    except queue.Empty:
                        steal_time += time.perf_counter() - t0
                        steal_ops += 1

            if item is None:
                consecutive_misses += 1
                time.sleep(0.0005)  # Brief backoff
                continue

            consecutive_misses = 0
            t0 = time.perf_counter()
            time.sleep(SIMULATED_WORK_MS / 1000)
            work_time += time.perf_counter() - t0
            items_done += 1

        with stats_lock:
            stats[idx] = {
                "items": items_done, "steal": steal_time, "work": work_time,
                "ops": steal_ops, "local": source_local, "global": source_global,
                "stolen": source_stolen,
            }

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(NUM_WORKERS)]
    total = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())
    total_local = sum(s.get("local", 0) for s in stats.values())
    total_global = sum(s.get("global", 0) for s in stats.values())
    total_stolen = sum(s.get("stolen", 0) for s in stats.values())

    return BenchmarkResult(
        strategy="go_scheduler_local_queues",
        throughput=round(total / max(duration, 0.001), 2),
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        items_completed=total,
        duration_s=round(duration, 3),
        items_per_worker=items_per_worker,
        extra={
            "inspiration": "Go GMP scheduler",
            "source_breakdown": {
                "local": total_local,
                "global": total_global,
                "stolen": total_stolen,
            },
        },
    )


# --- Proposal 4: Two-Level Scheduling ---
# Inspired by: Spark/Dask task graph scheduling
# Level 1: Coordinator partitions work into coarse chunks (sub-teams)
# Level 2: Within each sub-team, workers use lock-free local stealing

def strategy_two_level() -> BenchmarkResult:
    """Two-level: coarse partition into sub-teams, fine-grained local stealing."""
    NUM_SUBTEAMS = 3
    workers_per_subteam = [[] for _ in range(NUM_SUBTEAMS)]
    for w in range(NUM_WORKERS):
        workers_per_subteam[w % NUM_SUBTEAMS].append(w)

    # Level 1: Coarse partition
    subteam_queues = [[] for _ in range(NUM_SUBTEAMS)]
    for i in range(WORK_ITEMS):
        subteam_queues[i % NUM_SUBTEAMS].append(i)

    # Level 2: Within sub-team, distribute to local worker queues
    worker_queues = [[] for _ in range(NUM_WORKERS)]
    for st in range(NUM_SUBTEAMS):
        workers = workers_per_subteam[st]
        for i, item in enumerate(subteam_queues[st]):
            worker_queues[workers[i % len(workers)]].append(item)

    worker_locks = [threading.Lock() for _ in range(NUM_WORKERS)]
    barrier = threading.Barrier(NUM_WORKERS)
    stats = {}
    stats_lock = threading.Lock()

    def worker(idx):
        items_done = 0
        steal_time = 0.0
        work_time = 0.0
        steal_ops = 0
        my_subteam = idx % NUM_SUBTEAMS
        my_teammates = workers_per_subteam[my_subteam]

        barrier.wait(timeout=JOIN_TIMEOUT)

        while True:
            item = None

            # Try own queue
            with worker_locks[idx]:
                if worker_queues[idx]:
                    item = worker_queues[idx].pop()

            if item is None:
                # Steal from subteam first (locality)
                for mate in my_teammates:
                    if mate == idx:
                        continue
                    t0 = time.perf_counter()
                    with worker_locks[mate]:
                        if worker_queues[mate]:
                            item = worker_queues[mate].pop(0)
                    steal_time += time.perf_counter() - t0
                    steal_ops += 1
                    if item is not None:
                        break

            if item is None:
                # Steal from other sub-teams (last resort)
                for other_st in range(NUM_SUBTEAMS):
                    if other_st == my_subteam:
                        continue
                    for w in workers_per_subteam[other_st]:
                        t0 = time.perf_counter()
                        with worker_locks[w]:
                            if worker_queues[w]:
                                item = worker_queues[w].pop(0)
                        steal_time += time.perf_counter() - t0
                        steal_ops += 1
                        if item is not None:
                            break
                    if item is not None:
                        break

            if item is None:
                # Check if all queues are empty
                all_empty = True
                for w in range(NUM_WORKERS):
                    with worker_locks[w]:
                        if worker_queues[w]:
                            all_empty = False
                            break
                if all_empty:
                    break
                continue

            t0 = time.perf_counter()
            time.sleep(SIMULATED_WORK_MS / 1000)
            work_time += time.perf_counter() - t0
            items_done += 1

        with stats_lock:
            stats[idx] = {"items": items_done, "steal": steal_time,
                          "work": work_time, "ops": steal_ops}

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(NUM_WORKERS)]
    total = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return BenchmarkResult(
        strategy="two_level_scheduling",
        throughput=round(total / max(duration, 0.001), 2),
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        items_completed=total,
        duration_s=round(duration, 3),
        items_per_worker=items_per_worker,
        extra={
            "inspiration": "Spark/Dask two-level scheduling",
            "num_subteams": NUM_SUBTEAMS,
        },
    )


# --- Proposal 5: Exponential Backoff with Jitter + Batch ---
# Inspired by: AWS architecture blog, distributed systems best practices
# Batch stealing + exponential backoff with jitter when contention detected.
# Prevents thundering herd on the shared DB.

def strategy_backoff_jitter_batch() -> BenchmarkResult:
    """Batch steal with exponential backoff and jitter on contention."""
    tmp = tempfile.mkdtemp(prefix="strat-backoff-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coord", "coord")
    for i in range(WORK_ITEMS):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    barrier = threading.Barrier(NUM_WORKERS)
    stats = {}
    stats_lock = threading.Lock()

    def worker(idx):
        items_done = 0
        steal_time = 0.0
        work_time = 0.0
        steal_ops = 0
        backoff = 0.001  # 1ms initial backoff
        max_backoff = 0.05  # 50ms max

        barrier.wait(timeout=JOIN_TIMEOUT)
        batch_size = 5

        consecutive_empty = 0
        while consecutive_empty < 5:
            t0 = time.perf_counter()
            batch = _batch_steal(db, f"w-{idx}", batch_size)
            steal_time += time.perf_counter() - t0
            steal_ops += 1

            if not batch:
                consecutive_empty += 1
                # Exponential backoff with jitter
                jitter = random.uniform(0, backoff)
                time.sleep(backoff + jitter)
                backoff = min(backoff * 2, max_backoff)
                continue

            consecutive_empty = 0
            backoff = 0.001  # Reset backoff on success

            for item in batch:
                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                work_time += time.perf_counter() - t0
                items_done += 1

        with stats_lock:
            stats[idx] = {"items": items_done, "steal": steal_time,
                          "work": work_time, "ops": steal_ops}

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    ws.close()
    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(NUM_WORKERS)]
    total = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return BenchmarkResult(
        strategy="backoff_jitter_batch",
        throughput=round(total / max(duration, 0.001), 2),
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        items_completed=total,
        duration_s=round(duration, 3),
        items_per_worker=items_per_worker,
        extra={"inspiration": "AWS exponential backoff with jitter"},
    )


# --- Previous winners for comparison ---

def strategy_baseline() -> BenchmarkResult:
    """Current single-item work stealing."""
    tmp = tempfile.mkdtemp(prefix="strat-base-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coord", "coord")
    for i in range(WORK_ITEMS):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    barrier = threading.Barrier(NUM_WORKERS)
    stats = {}
    stats_lock = threading.Lock()

    def worker(idx):
        w = WorkStealing(db, bus, f"w-{idx}", f"a-{idx}")
        items_done = 0
        steal_time = 0.0
        work_time = 0.0
        ops = 0

        barrier.wait(timeout=JOIN_TIMEOUT)
        while True:
            t0 = time.perf_counter()
            item = w.steal_work()
            steal_time += time.perf_counter() - t0
            ops += 1
            if item is None:
                break
            t0 = time.perf_counter()
            time.sleep(SIMULATED_WORK_MS / 1000)
            work_time += time.perf_counter() - t0
            w.complete_work(item["id"], {"w": idx})
            ops += 1
            items_done += 1
        with stats_lock:
            stats[idx] = {"items": items_done, "steal": steal_time,
                          "work": work_time, "ops": ops}
        w.close()

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start
    ws.close()

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(NUM_WORKERS)]
    total = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return BenchmarkResult(
        strategy="baseline_single_steal",
        throughput=round(total / max(duration, 0.001), 2),
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        items_completed=total,
        duration_s=round(duration, 3),
        items_per_worker=items_per_worker,
    )


def strategy_hierarchical() -> BenchmarkResult:
    """Previous winner: hierarchical lead + in-memory queue."""
    tmp = tempfile.mkdtemp(prefix="strat-hier-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coord", "coord")
    for i in range(WORK_ITEMS):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    local_q = queue.Queue()
    lead_done = threading.Event()
    barrier = threading.Barrier(NUM_WORKERS + 1)
    stats = {}
    stats_lock = threading.Lock()
    lead_stats = {"steal": 0.0, "ops": 0}

    def lead():
        barrier.wait(timeout=JOIN_TIMEOUT)
        while True:
            t0 = time.perf_counter()
            batch = _batch_steal(db, "lead", 20)
            lead_stats["steal"] += time.perf_counter() - t0
            lead_stats["ops"] += 1
            if not batch:
                break
            for item in batch:
                local_q.put(item)
        lead_done.set()
        for _ in range(NUM_WORKERS):
            local_q.put(None)

    def worker(idx):
        items_done = 0
        work_time = 0.0
        wait_time = 0.0
        barrier.wait(timeout=JOIN_TIMEOUT)
        while True:
            t0 = time.perf_counter()
            try:
                item = local_q.get(timeout=5)
            except queue.Empty:
                if lead_done.is_set():
                    break
                continue
            wait_time += time.perf_counter() - t0
            if item is None:
                break
            t0 = time.perf_counter()
            time.sleep(SIMULATED_WORK_MS / 1000)
            work_time += time.perf_counter() - t0
            items_done += 1
        with stats_lock:
            stats[idx] = {"items": items_done, "work": work_time, "wait": wait_time}

    t_start = time.time()
    lt = threading.Thread(target=lead)
    wts = [threading.Thread(target=worker, args=(i,)) for i in range(NUM_WORKERS)]
    lt.start()
    for t in wts:
        t.start()
    lt.join(timeout=JOIN_TIMEOUT)
    for t in wts:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start
    ws.close()

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(NUM_WORKERS)]
    total = sum(items_per_worker)
    total_work = sum(s.get("work", 0) for s in stats.values())

    return BenchmarkResult(
        strategy="hierarchical_lead",
        throughput=round(total / max(duration, 0.001), 2),
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_ratio=round(lead_stats["steal"] / max(total_work, 0.001), 3),
        coordination_ops=lead_stats["ops"],
        items_completed=total,
        duration_s=round(duration, 3),
        items_per_worker=items_per_worker,
    )


# ===================================================================
# Think Tank Debate Engine
# ===================================================================

def run_debate():
    """Execute the full think tank debate and validation."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Set up communication
    comm_dir = tempfile.mkdtemp(prefix="think-tank-")
    bus_dir = os.path.join(comm_dir, "bus")
    db_path = os.path.join(comm_dir, "debate.db")
    os.makedirs(bus_dir, exist_ok=True)

    writer = BusWriter(bus_dir, "debate-coordinator", "team-coordinator")
    scratchpad = Scratchpad(db_path, "team-coordinator", "debate-coordinator")

    print("=" * 70)
    print("THINK TANK DEBATE: MULTI-AGENT COORDINATION OPTIMIZATION")
    print("=" * 70)

    # ---------------------------------------------------------------
    # Phase 1: Proposals (TT-A Theory + TT-B Systems)
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("PHASE 1: PROPOSALS")
    print("=" * 60)

    proposals = [
        Proposal(
            team="TT-A (Theory)",
            strategy_name="Chase-Lev Per-Worker Deques",
            description="Each worker maintains a local deque. Work distributed round-robin initially. "
                        "When empty, steal from random victim's deque bottom.",
            rationale="Eliminates shared DB from hot path entirely. The Chase-Lev deque (1990s) "
                      "is proven optimal for fork-join parallelism. Used in Cilk, Java ForkJoinPool, "
                      "Tokio, and Rayon. O(1) local push/pop, stealing only occurs when idle.",
            expected_improvement="10-15x over baseline (no DB in hot path)",
            trade_offs=["No persistent queue (if process crashes, work lost)",
                        "No priority ordering across workers",
                        "Requires all work known upfront"],
            implementation_complexity="medium",
            inspiration="Chase-Lev '05 deque, Cilk-5 runtime, Blumofe & Leiserson '99",
        ),
        Proposal(
            team="TT-A (Theory)",
            strategy_name="Contract Net Protocol",
            description="Centralized coordinator announces task batches. Workers bid based on "
                        "current capacity. Coordinator assigns to highest bidder.",
            rationale="Classic multi-agent coordination from AI research (Smith, 1980). "
                      "Serializes assignment through one coordinator, eliminating worker-worker "
                      "contention entirely. Used in robotic fleet management.",
            expected_improvement="3-5x (coordinator is bottleneck but no contention)",
            trade_offs=["Coordinator is single point of failure",
                        "Bidding rounds add latency",
                        "Overkill for homogeneous workers"],
            implementation_complexity="medium",
            inspiration="FIPA Contract Net Protocol, Smith 1980",
        ),
        Proposal(
            team="TT-B (Systems)",
            strategy_name="Go-Style Local Run Queues",
            description="Each worker has bounded local queue (64 items). Overflow goes to global. "
                        "Workers check: local → global → steal from random peer.",
            rationale="The Go runtime scheduler (GMP model) uses this three-tier approach. "
                      "Local queue is contention-free. Global queue serializes overflow. "
                      "Stealing is last resort. Proven at massive scale in Go production.",
            expected_improvement="8-12x (most work from local queue, no DB)",
            trade_offs=["Global queue can bottleneck if many workers overflow",
                        "Initial distribution matters a lot",
                        "No persistent state"],
            implementation_complexity="low",
            inspiration="Go runtime scheduler (GMP model), Vyukov 2013",
        ),
        Proposal(
            team="TT-B (Systems)",
            strategy_name="Two-Level Scheduling",
            description="Level 1: partition work into sub-teams (locality groups). "
                        "Level 2: within each sub-team, workers steal from neighbors first.",
            rationale="Spark and Dask use two-level scheduling for data locality. "
                      "By restricting stealing to nearby workers first, we reduce "
                      "cross-sub-team contention. NUMA-aware approach.",
            expected_improvement="8-10x (locality reduces steal distance)",
            trade_offs=["More complex initial partitioning",
                        "Sub-team imbalance if work is uneven",
                        "Extra indirection layer"],
            implementation_complexity="medium",
            inspiration="Apache Spark scheduler, Dask distributed scheduler",
        ),
        Proposal(
            team="TT-B (Systems)",
            strategy_name="Backoff + Jitter + Batch",
            description="Batch steal from SQLite with exponential backoff and random jitter "
                        "when contention detected. Prevents thundering herd.",
            rationale="AWS architecture best practice for distributed systems. "
                      "Stays compatible with existing SQLite infrastructure while "
                      "reducing contention via decorrelated jitter backoff.",
            expected_improvement="4-6x (keeps SQLite, reduces contention)",
            trade_offs=["Still uses SQLite (fundamental throughput limit)",
                        "Backoff adds latency under low contention",
                        "Jitter makes performance less predictable"],
            implementation_complexity="low",
            inspiration="AWS Architecture Blog, Brooker 2015 'Exponential Backoff And Jitter'",
        ),
    ]

    for p in proposals:
        print(f"\n  [{p.team}] {p.strategy_name}")
        print(f"    {p.description}")
        print(f"    Rationale: {p.rationale[:120]}...")
        print(f"    Expected: {p.expected_improvement}")
        print(f"    Complexity: {p.implementation_complexity}")
        print(f"    Source: {p.inspiration}")

        writer.publish("proposals", "info", {
            "event": "proposal",
            "team": p.team,
            "strategy": p.strategy_name,
        })
        scratchpad.write(f"proposal-{p.strategy_name}", asdict(p), namespace="global")

    # ---------------------------------------------------------------
    # Phase 2: Debate (TT-C Challenger raises objections)
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("PHASE 2: DEBATE (TT-C Challenges, TT-D Synthesizes)")
    print("=" * 60)

    debate_points = [
        DebatePoint(
            team="TT-C (Challenger)",
            target_proposal="Chase-Lev Per-Worker Deques",
            point_type="challenge",
            argument="Chase-Lev requires all work known upfront. In real multi-agent AI systems, "
                     "work is discovered dynamically (e.g., research leads to new subtasks). "
                     "A pure push-based model can't handle work that arrives mid-execution.",
            evidence="KB-0023 shows help_protocol handles dynamic work injection. "
                     "Chase-Lev deque has no equivalent.",
        ),
        DebatePoint(
            team="TT-C (Challenger)",
            target_proposal="Contract Net Protocol",
            point_type="challenge",
            argument="The bidding window (5ms) adds unnecessary latency for homogeneous workers. "
                     "With identical LLM agents, there's nothing meaningful to bid on — "
                     "all workers have the same capacity. CNP shines when agents are heterogeneous.",
            evidence="Our workers are identical Python threads. CNP was designed for "
                     "heterogeneous robotic fleets with different capabilities.",
        ),
        DebatePoint(
            team="TT-C (Challenger)",
            target_proposal="Go-Style Local Run Queues",
            point_type="support",
            argument="This is the strongest proposal because it handles dynamic work via the global "
                     "queue while keeping the hot path lock-free via local queues. "
                     "It also degrades gracefully: if initial distribution is wrong, "
                     "stealing corrects it.",
            evidence="Go runtime handles millions of goroutines with this model. "
                     "It's battle-tested in production at Google scale.",
        ),
        DebatePoint(
            team="TT-C (Challenger)",
            target_proposal="Two-Level Scheduling",
            point_type="question",
            argument="What defines a 'sub-team' in our context? We don't have NUMA nodes or "
                     "data locality. Without a real locality dimension to exploit, "
                     "two-level scheduling is just pre-partitioning with extra steps.",
            evidence="In Spark, two-level scheduling exploits data locality (tasks run near "
                     "their data). Our agents don't have a locality concept.",
        ),
        DebatePoint(
            team="TT-D (Synthesis)",
            target_proposal="ALL",
            point_type="synthesis",
            argument="The key insight is separation of concerns: decouple WORK STORAGE "
                     "(persistent, in SQLite) from WORK DISTRIBUTION (transient, in-memory). "
                     "The best strategy keeps SQLite as the durable source of truth but "
                     "moves the hot path (worker→item assignment) to in-memory structures. "
                     "The Go-style model with a hierarchical lead achieves this naturally.",
            evidence="KB-0027 showed hierarchical (10.4x) and pre-partition (10.8x) both "
                     "avoid SQLite in the hot path. The Go model adds dynamic work injection.",
        ),
        DebatePoint(
            team="TT-D (Synthesis)",
            target_proposal="ALL",
            point_type="synthesis",
            argument="RECOMMENDED HYBRID: Hierarchical lead batch-steals from SQLite into an "
                     "in-memory Go-style multi-queue dispatcher. Workers pull from local queues. "
                     "When local is empty, steal from peers. When no peers have work, request "
                     "from the lead's global buffer. This combines the durability of SQLite "
                     "with the performance of in-memory distribution.",
            evidence="Combines hierarchical (10.4x), Go-style (locality), and Chase-Lev "
                     "(O(1) local ops). Expected: 10-12x improvement with dynamic work support.",
        ),
    ]

    for dp in debate_points:
        icon = {"support": "+", "challenge": "X", "question": "?", "synthesis": "*"}
        print(f"\n  [{dp.team}] [{icon.get(dp.point_type, '?')}] → {dp.target_proposal}")
        print(f"    {dp.argument}")
        if dp.evidence:
            print(f"    Evidence: {dp.evidence[:120]}...")

        writer.publish("debate", "info", {
            "event": "debate_point",
            "team": dp.team,
            "type": dp.point_type,
            "target": dp.target_proposal,
        })

    # ---------------------------------------------------------------
    # Phase 3: Validation (TT-E runs benchmarks)
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("PHASE 3: VALIDATION (TT-E Benchmarks)")
    print("=" * 60)
    print(f"  Workload: {WORK_ITEMS} items, {NUM_WORKERS} workers, {SIMULATED_WORK_MS}ms/item")

    strategies = [
        ("Baseline (current)", strategy_baseline),
        ("Hierarchical Lead (KB-0027 winner)", strategy_hierarchical),
        ("Chase-Lev Deques (TT-A)", strategy_chase_lev_deques),
        ("Contract Net Protocol (TT-A)", strategy_contract_net),
        ("Go-Style Local Queues (TT-B)", strategy_go_scheduler),
        ("Two-Level Scheduling (TT-B)", strategy_two_level),
        ("Backoff+Jitter+Batch (TT-B)", strategy_backoff_jitter_batch),
    ]

    results = []
    for name, fn in strategies:
        print(f"\n  Running: {name}...", end=" ", flush=True)
        result = run_benchmark(name, fn)
        results.append(result)

        status = "OK" if result.items_completed == WORK_ITEMS else "INCOMPLETE"
        print(f"[{status}] {result.throughput:.0f} items/s, "
              f"CV={result.distribution_cv:.4f}, "
              f"steal_ratio={result.steal_ratio:.3f}")

        scratchpad.write(f"benchmark-{result.strategy}", asdict(result), namespace="global")

    # ---------------------------------------------------------------
    # Phase 4: Final Analysis (TT-D Synthesis)
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("PHASE 4: FINAL RANKINGS AND SYNTHESIS")
    print("=" * 60)

    # Rank by composite score
    max_tp = max(r.throughput for r in results) or 1
    max_cv = max(r.distribution_cv for r in results) or 1
    max_sr = max(r.steal_ratio for r in results) or 1

    scored = []
    for r in results:
        tp_score = r.throughput / max_tp
        cv_score = 1 - (r.distribution_cv / max_cv)
        sr_score = 1 - (r.steal_ratio / max_sr)
        composite = tp_score * 0.4 + cv_score * 0.3 + sr_score * 0.3
        scored.append((r, composite, tp_score, cv_score, sr_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    baseline_tp = next(r.throughput for r in results if "baseline" in r.strategy)

    print("\nFinal Rankings (composite = throughput×0.4 + evenness×0.3 + low_overhead×0.3):")
    print(f"{'Rank':<5} {'Strategy':<35} {'Score':<7} {'Throughput':<12} {'vs Base':<9} "
          f"{'CV':<8} {'Steal/Work':<10}")
    print("-" * 90)
    for i, (r, composite, tp, cv, sr) in enumerate(scored, 1):
        ratio = r.throughput / max(baseline_tp, 1)
        print(f"  {i:<3} {r.strategy:<35} {composite:.3f}   {r.throughput:>8.0f}/s   "
              f"{ratio:>5.1f}x   {r.distribution_cv:.4f}   {r.steal_ratio:.3f}")

    # Debate verdict
    winner = scored[0][0]
    runner_up = scored[1][0]

    print(f"\n{'='*60}")
    print("THINK TANK VERDICT")
    print(f"{'='*60}")
    print(f"\n  WINNER: {winner.strategy}")
    print(f"    Throughput: {winner.throughput:.0f} items/s "
          f"({winner.throughput/max(baseline_tp,1):.1f}x baseline)")
    print(f"    Distribution: CV={winner.distribution_cv:.4f}")
    print(f"    Overhead: steal/work={winner.steal_ratio:.3f}")
    print(f"\n  Runner-up: {runner_up.strategy}")
    print(f"    Throughput: {runner_up.throughput:.0f} items/s")

    print(f"\n  TT-D SYNTHESIS RECOMMENDATION:")
    print(f"    For the coordination system, implement a hybrid approach:")
    print(f"    1. Keep SQLite as the durable work store (persistence, crash recovery)")
    print(f"    2. Add a LeadDistributor that batch-steals into in-memory queues")
    print(f"    3. Workers pull from local queue (O(1), no contention)")
    print(f"    4. Empty workers steal from peers before going back to lead")
    print(f"    5. Dynamic work goes to lead's buffer, not directly to SQLite")

    # Save results
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "work_items": WORK_ITEMS,
            "workers": NUM_WORKERS,
            "simulated_work_ms": SIMULATED_WORK_MS,
        },
        "proposals": [asdict(p) for p in proposals],
        "debate_points": [asdict(dp) for dp in debate_points],
        "benchmark_results": [asdict(r) for r in results],
        "rankings": [
            {
                "rank": i + 1,
                "strategy": r.strategy,
                "composite_score": round(composite, 3),
                "throughput": r.throughput,
                "vs_baseline": round(r.throughput / max(baseline_tp, 1), 1),
                "distribution_cv": r.distribution_cv,
                "steal_ratio": r.steal_ratio,
            }
            for i, (r, composite, _, _, _) in enumerate(scored)
        ],
        "winner": winner.strategy,
        "synthesis": (
            "Hybrid hierarchical+Go-style: SQLite for durability, "
            "lead batch-steals into in-memory multi-queue dispatcher, "
            "workers pull locally, steal from peers, fallback to lead buffer."
        ),
    }

    output_path = OUTPUT_DIR / "think_tank_debate_results.json"
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n  Results saved to: {output_path}")

    return summary


if __name__ == "__main__":
    run_debate()
