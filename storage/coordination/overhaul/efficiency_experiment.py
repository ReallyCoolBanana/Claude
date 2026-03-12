#!/usr/bin/env python3
"""
8-Team Efficiency Experiment: Finding Ways to Improve Large-Team Coordination

Previous benchmarks (KB-0026) identified three root causes of large-team inefficiency:
  1. Single-item steal creates "fast stealer" bias (work distribution CV=0.64 at 4 agents)
  2. Each steal requires BEGIN IMMEDIATE (steal overhead > work time at 4+ agents)
  3. Single SQLite DB serialization (throughput plateaus at ~650 items/s)

This experiment uses 8 communicating teams to test alternative strategies:

  Team 1 (baseline):       Single-item work stealing (current system)
  Team 2 (batch-steal):    Steal N items per transaction to amortize overhead
  Team 3 (pre-partition):  Round-robin assignment, no stealing at all
  Team 4 (hierarchical):   Team lead steals batches, distributes to workers locally
  Team 5 (sharded-queue):  Multiple DB shards, agents have preferred shards
  Team 6 (adaptive-batch): Start small, increase batch size as queue shrinks
  Team 7 (hybrid):         Pre-assign 80%, steal remaining 20%
  Team 8 (coordinator):    Monitors all teams, collects results, publishes findings

Each strategy team runs the SAME workload (500 items, 10 workers) using Proto A
communication to report progress and results to Team 8 (coordinator).
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

from agent_comm.bus import BusReader, BusWriter
from storage.coordination.coordinator_hub import AgentReporter, CoordinatorDashboard
from storage.coordination.direct_channels import DirectChannels
from storage.coordination.help_protocol import HelpProtocol
from storage.coordination.work_stealing import WorkStealing, Scratchpad

OUTPUT_DIR = Path(__file__).parent / "output"
JOIN_TIMEOUT = 60

# ---------------------------------------------------------------------------
# Shared workload definition
# ---------------------------------------------------------------------------

WORK_ITEMS_PER_STRATEGY = 500
WORKERS_PER_STRATEGY = 10
SIMULATED_WORK_MS = 1  # milliseconds of simulated work per item


@dataclass
class StrategyResult:
    """Results from a single strategy run."""
    strategy: str
    total_items: int
    items_completed: int
    duration_s: float
    throughput_items_per_s: float
    per_worker_throughput: float
    items_per_worker: list
    distribution_cv: float  # coefficient of variation (lower = more even)
    steal_overhead_s: float
    work_time_s: float
    steal_to_work_ratio: float
    coordination_ops: int
    errors: list = field(default_factory=list)
    extra_metrics: dict = field(default_factory=dict)


def compute_cv(values: list) -> float:
    """Coefficient of variation: stdev / mean (0 = perfectly even)."""
    if len(values) < 2 or statistics.mean(values) == 0:
        return 0.0
    return statistics.stdev(values) / statistics.mean(values)


# ---------------------------------------------------------------------------
# Strategy 1: Baseline single-item work stealing (current system)
# ---------------------------------------------------------------------------

def run_baseline_steal(comm_dir: str) -> StrategyResult:
    """Current approach: each worker steals 1 item at a time via BEGIN IMMEDIATE."""
    tmp = tempfile.mkdtemp(prefix="strat-baseline-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coordinator", "coord")
    for i in range(WORK_ITEMS_PER_STRATEGY):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    barrier = threading.Barrier(WORKERS_PER_STRATEGY)
    stats = {}
    stats_lock = threading.Lock()
    errors = []

    def worker(idx):
        try:
            w = WorkStealing(db, bus, f"worker-{idx}", f"agent-{idx}")
            items_done = 0
            total_steal = 0.0
            total_work = 0.0
            coord_ops = 0

            barrier.wait(timeout=JOIN_TIMEOUT)
            while True:
                t0 = time.perf_counter()
                item = w.steal_work()
                total_steal += time.perf_counter() - t0
                coord_ops += 1

                if item is None:
                    break

                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                total_work += time.perf_counter() - t0

                w.complete_work(item["id"], {"worker": idx})
                coord_ops += 1
                items_done += 1

            with stats_lock:
                stats[idx] = {"items": items_done, "steal": total_steal,
                              "work": total_work, "ops": coord_ops}
            w.close()
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS_PER_STRATEGY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    ws.close()
    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return StrategyResult(
        strategy="baseline_single_steal",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=round(total_steal, 3),
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        errors=[f"w{i}: {e}" for i, e in errors],
    )


# ---------------------------------------------------------------------------
# Strategy 2: Batch stealing — steal N items per transaction
# ---------------------------------------------------------------------------

def _batch_steal(db_path: str, team: str, batch_size: int) -> list:
    """Steal up to batch_size items in a single BEGIN IMMEDIATE transaction."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    now = time.time()

    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT id, owner_team, title, description, priority, created_at
               FROM work_queue WHERE status = 'queued'
               ORDER BY priority ASC, created_at ASC
               LIMIT ?""",
            (batch_size,),
        ).fetchall()

        if not rows:
            conn.execute("ROLLBACK")
            conn.close()
            return []

        items = []
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


def run_batch_steal(comm_dir: str) -> StrategyResult:
    """Steal items in batches of 10 to amortize transaction overhead."""
    tmp = tempfile.mkdtemp(prefix="strat-batch-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coordinator", "coord")
    for i in range(WORK_ITEMS_PER_STRATEGY):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    batch_size = 10
    barrier = threading.Barrier(WORKERS_PER_STRATEGY)
    stats = {}
    stats_lock = threading.Lock()
    errors = []

    def worker(idx):
        try:
            items_done = 0
            total_steal = 0.0
            total_work = 0.0
            coord_ops = 0

            barrier.wait(timeout=JOIN_TIMEOUT)
            while True:
                t0 = time.perf_counter()
                batch = _batch_steal(db, f"worker-{idx}", batch_size)
                total_steal += time.perf_counter() - t0
                coord_ops += 1  # one transaction for N items

                if not batch:
                    break

                for item in batch:
                    t0 = time.perf_counter()
                    time.sleep(SIMULATED_WORK_MS / 1000)
                    total_work += time.perf_counter() - t0
                    items_done += 1

            with stats_lock:
                stats[idx] = {"items": items_done, "steal": total_steal,
                              "work": total_work, "ops": coord_ops}
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS_PER_STRATEGY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    ws.close()
    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return StrategyResult(
        strategy="batch_steal_10",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=round(total_steal, 3),
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        errors=[f"w{i}: {e}" for i, e in errors],
        extra_metrics={"batch_size": batch_size},
    )


# ---------------------------------------------------------------------------
# Strategy 3: Pre-partitioned work (no stealing)
# ---------------------------------------------------------------------------

def run_pre_partition(comm_dir: str) -> StrategyResult:
    """Round-robin assign all work upfront, no stealing needed."""
    # Partition work items into per-worker queues
    worker_queues: list[list[int]] = [[] for _ in range(WORKERS_PER_STRATEGY)]
    for i in range(WORK_ITEMS_PER_STRATEGY):
        worker_queues[i % WORKERS_PER_STRATEGY].append(i)

    barrier = threading.Barrier(WORKERS_PER_STRATEGY)
    stats = {}
    stats_lock = threading.Lock()
    errors = []

    def worker(idx, items):
        try:
            total_work = 0.0
            barrier.wait(timeout=JOIN_TIMEOUT)
            for item_id in items:
                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                total_work += time.perf_counter() - t0

            with stats_lock:
                stats[idx] = {"items": len(items), "steal": 0.0,
                              "work": total_work, "ops": 0}
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i, worker_queues[i]))
               for i in range(WORKERS_PER_STRATEGY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_work = sum(s.get("work", 0) for s in stats.values())

    return StrategyResult(
        strategy="pre_partition_round_robin",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=0.0,
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=0.0,
        coordination_ops=0,
        errors=[f"w{i}: {e}" for i, e in errors],
    )


# ---------------------------------------------------------------------------
# Strategy 4: Hierarchical — team lead steals batches, distributes locally
# ---------------------------------------------------------------------------

def run_hierarchical(comm_dir: str) -> StrategyResult:
    """Team lead steals batches from shared queue, workers pull from local thread-safe queue."""
    tmp = tempfile.mkdtemp(prefix="strat-hier-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coordinator", "coord")
    for i in range(WORK_ITEMS_PER_STRATEGY):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    local_queue = queue.Queue()
    lead_done = threading.Event()
    barrier = threading.Barrier(WORKERS_PER_STRATEGY + 1)  # +1 for lead
    stats = {}
    stats_lock = threading.Lock()
    lead_stats = {"steal_time": 0.0, "ops": 0, "items_fetched": 0}
    errors = []

    def team_lead():
        """Steals batches and distributes to local queue."""
        try:
            barrier.wait(timeout=JOIN_TIMEOUT)
            batch_size = 20
            while True:
                t0 = time.perf_counter()
                batch = _batch_steal(db, "lead", batch_size)
                lead_stats["steal_time"] += time.perf_counter() - t0
                lead_stats["ops"] += 1

                if not batch:
                    break

                lead_stats["items_fetched"] += len(batch)
                for item in batch:
                    local_queue.put(item)

            lead_done.set()
            # Signal workers to stop
            for _ in range(WORKERS_PER_STRATEGY):
                local_queue.put(None)
        except Exception as e:
            lead_done.set()
            for _ in range(WORKERS_PER_STRATEGY):
                local_queue.put(None)
            with stats_lock:
                errors.append(("lead", str(e)))

    def worker(idx):
        try:
            items_done = 0
            total_work = 0.0
            total_wait = 0.0

            barrier.wait(timeout=JOIN_TIMEOUT)
            while True:
                t0 = time.perf_counter()
                try:
                    item = local_queue.get(timeout=5)
                except queue.Empty:
                    if lead_done.is_set():
                        break
                    continue
                total_wait += time.perf_counter() - t0

                if item is None:
                    break

                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                total_work += time.perf_counter() - t0
                items_done += 1

            with stats_lock:
                stats[idx] = {"items": items_done, "wait": total_wait,
                              "work": total_work}
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    lead_thread = threading.Thread(target=team_lead)
    worker_threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS_PER_STRATEGY)]

    lead_thread.start()
    for t in worker_threads:
        t.start()
    lead_thread.join(timeout=JOIN_TIMEOUT)
    for t in worker_threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    ws.close()
    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_wait = sum(s.get("wait", 0) for s in stats.values())

    return StrategyResult(
        strategy="hierarchical_lead_distribute",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=round(lead_stats["steal_time"], 3),
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=round(lead_stats["steal_time"] / max(total_work, 0.001), 3),
        coordination_ops=lead_stats["ops"],
        errors=[f"w{i}: {e}" for i, e in errors],
        extra_metrics={
            "lead_batch_size": 20,
            "lead_items_fetched": lead_stats["items_fetched"],
            "worker_wait_time_s": round(total_wait, 3),
        },
    )


# ---------------------------------------------------------------------------
# Strategy 5: Sharded queues — multiple DBs, preferred shard per worker
# ---------------------------------------------------------------------------

def run_sharded_queues(comm_dir: str) -> StrategyResult:
    """Split work across N DB shards. Workers prefer their shard, steal from others if empty."""
    num_shards = 3
    tmp = tempfile.mkdtemp(prefix="strat-shard-")
    shard_dbs = []
    shard_bus_dirs = []

    for s in range(num_shards):
        shard_db = os.path.join(tmp, f"shard-{s}.db")
        shard_bus = os.path.join(tmp, f"bus-{s}")
        os.makedirs(shard_bus, exist_ok=True)
        shard_dbs.append(shard_db)
        shard_bus_dirs.append(shard_bus)

    # Distribute work across shards (round-robin)
    shard_ws = []
    for s in range(num_shards):
        w = WorkStealing(shard_dbs[s], shard_bus_dirs[s], "coordinator", "coord")
        shard_ws.append(w)

    for i in range(WORK_ITEMS_PER_STRATEGY):
        shard_idx = i % num_shards
        shard_ws[shard_idx].enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    barrier = threading.Barrier(WORKERS_PER_STRATEGY)
    stats = {}
    stats_lock = threading.Lock()
    errors = []

    def worker(idx):
        try:
            preferred_shard = idx % num_shards
            items_done = 0
            total_steal = 0.0
            total_work = 0.0
            coord_ops = 0

            # Open connections to all shards
            ws_conns = []
            for s in range(num_shards):
                ws_conns.append(WorkStealing(shard_dbs[s], shard_bus_dirs[s],
                                             f"worker-{idx}", f"agent-{idx}"))

            barrier.wait(timeout=JOIN_TIMEOUT)

            empty_count = 0
            while empty_count < num_shards:
                empty_count = 0
                # Try preferred shard first, then others
                shard_order = [preferred_shard] + [s for s in range(num_shards) if s != preferred_shard]

                for shard_idx in shard_order:
                    t0 = time.perf_counter()
                    item = ws_conns[shard_idx].steal_work()
                    total_steal += time.perf_counter() - t0
                    coord_ops += 1

                    if item is None:
                        empty_count += 1
                        continue

                    empty_count = 0
                    t0 = time.perf_counter()
                    time.sleep(SIMULATED_WORK_MS / 1000)
                    total_work += time.perf_counter() - t0

                    ws_conns[shard_idx].complete_work(item["id"], {"worker": idx})
                    coord_ops += 1
                    items_done += 1
                    break  # go back to preferred shard

            for ws_conn in ws_conns:
                ws_conn.close()

            with stats_lock:
                stats[idx] = {"items": items_done, "steal": total_steal,
                              "work": total_work, "ops": coord_ops}
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS_PER_STRATEGY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    for w in shard_ws:
        w.close()

    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return StrategyResult(
        strategy="sharded_queues_3",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=round(total_steal, 3),
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        errors=[f"w{i}: {e}" for i, e in errors],
        extra_metrics={"num_shards": num_shards},
    )


# ---------------------------------------------------------------------------
# Strategy 6: Adaptive batch — increase batch size as queue shrinks
# ---------------------------------------------------------------------------

def run_adaptive_batch(comm_dir: str) -> StrategyResult:
    """Start with small batches (2), increase to 20 as contention grows."""
    tmp = tempfile.mkdtemp(prefix="strat-adaptive-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coordinator", "coord")
    for i in range(WORK_ITEMS_PER_STRATEGY):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    barrier = threading.Barrier(WORKERS_PER_STRATEGY)
    stats = {}
    stats_lock = threading.Lock()
    errors = []

    def worker(idx):
        try:
            items_done = 0
            total_steal = 0.0
            total_work = 0.0
            coord_ops = 0
            batch_size = 2  # start small
            consecutive_empty = 0

            barrier.wait(timeout=JOIN_TIMEOUT)
            while True:
                t0 = time.perf_counter()
                batch = _batch_steal(db, f"worker-{idx}", batch_size)
                total_steal += time.perf_counter() - t0
                coord_ops += 1

                if not batch:
                    consecutive_empty += 1
                    if consecutive_empty > 3:
                        break
                    time.sleep(0.001)  # Brief backoff
                    continue

                consecutive_empty = 0
                for item in batch:
                    t0 = time.perf_counter()
                    time.sleep(SIMULATED_WORK_MS / 1000)
                    total_work += time.perf_counter() - t0
                    items_done += 1

                # Adaptive: got a full batch = high contention, increase
                if len(batch) == batch_size:
                    batch_size = min(batch_size + 2, 25)
                else:
                    batch_size = max(batch_size - 1, 2)

            with stats_lock:
                stats[idx] = {"items": items_done, "steal": total_steal,
                              "work": total_work, "ops": coord_ops,
                              "final_batch_size": batch_size}
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS_PER_STRATEGY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    ws.close()
    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())
    avg_final_batch = statistics.mean(
        [s.get("final_batch_size", 2) for s in stats.values()]
    ) if stats else 0

    return StrategyResult(
        strategy="adaptive_batch",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=round(total_steal, 3),
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        errors=[f"w{i}: {e}" for i, e in errors],
        extra_metrics={"avg_final_batch_size": round(avg_final_batch, 1)},
    )


# ---------------------------------------------------------------------------
# Strategy 7: Hybrid — pre-assign 80%, steal remaining 20%
# ---------------------------------------------------------------------------

def run_hybrid(comm_dir: str) -> StrategyResult:
    """Pre-assign 80% of work round-robin, put 20% in steal queue for flexibility."""
    tmp = tempfile.mkdtemp(prefix="strat-hybrid-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    pre_assign_pct = 0.80
    pre_assigned = int(WORK_ITEMS_PER_STRATEGY * pre_assign_pct)
    steal_pool = WORK_ITEMS_PER_STRATEGY - pre_assigned

    # Pre-partition 80%
    worker_queues: list[list[int]] = [[] for _ in range(WORKERS_PER_STRATEGY)]
    for i in range(pre_assigned):
        worker_queues[i % WORKERS_PER_STRATEGY].append(i)

    # Put 20% in steal queue
    ws = WorkStealing(db, bus, "coordinator", "coord")
    for i in range(pre_assigned, WORK_ITEMS_PER_STRATEGY):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    barrier = threading.Barrier(WORKERS_PER_STRATEGY)
    stats = {}
    stats_lock = threading.Lock()
    errors = []

    def worker(idx, pre_items):
        try:
            w = WorkStealing(db, bus, f"worker-{idx}", f"agent-{idx}")
            items_done = 0
            total_steal = 0.0
            total_work = 0.0
            coord_ops = 0

            barrier.wait(timeout=JOIN_TIMEOUT)

            # Phase 1: Process pre-assigned work
            for item_id in pre_items:
                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                total_work += time.perf_counter() - t0
                items_done += 1

            # Phase 2: Steal remaining
            while True:
                t0 = time.perf_counter()
                item = w.steal_work()
                total_steal += time.perf_counter() - t0
                coord_ops += 1

                if item is None:
                    break

                t0 = time.perf_counter()
                time.sleep(SIMULATED_WORK_MS / 1000)
                total_work += time.perf_counter() - t0

                w.complete_work(item["id"], {"worker": idx})
                coord_ops += 1
                items_done += 1

            with stats_lock:
                stats[idx] = {"items": items_done, "steal": total_steal,
                              "work": total_work, "ops": coord_ops,
                              "pre_assigned": len(pre_items)}
            w.close()
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i, worker_queues[i]))
               for i in range(WORKERS_PER_STRATEGY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    ws.close()
    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return StrategyResult(
        strategy="hybrid_80_pre_20_steal",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=round(total_steal, 3),
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        errors=[f"w{i}: {e}" for i, e in errors],
        extra_metrics={
            "pre_assigned_pct": pre_assign_pct,
            "pre_assigned_items": pre_assigned,
            "steal_pool_items": steal_pool,
        },
    )


# ---------------------------------------------------------------------------
# Strategy 8 variant: Batch steal with varying sizes (to find optimal batch)
# ---------------------------------------------------------------------------

def run_batch_steal_size(comm_dir: str, batch_size: int) -> StrategyResult:
    """Test different batch sizes to find the optimal one."""
    tmp = tempfile.mkdtemp(prefix=f"strat-batch{batch_size}-")
    db = os.path.join(tmp, "work.db")
    bus = os.path.join(tmp, "bus")
    os.makedirs(bus, exist_ok=True)

    ws = WorkStealing(db, bus, "coordinator", "coord")
    for i in range(WORK_ITEMS_PER_STRATEGY):
        ws.enqueue_work(f"item-{i}", f"Process {i}", priority=random.randint(1, 10))

    barrier = threading.Barrier(WORKERS_PER_STRATEGY)
    stats = {}
    stats_lock = threading.Lock()
    errors = []

    def worker(idx):
        try:
            items_done = 0
            total_steal = 0.0
            total_work = 0.0
            coord_ops = 0

            barrier.wait(timeout=JOIN_TIMEOUT)
            while True:
                t0 = time.perf_counter()
                batch = _batch_steal(db, f"worker-{idx}", batch_size)
                total_steal += time.perf_counter() - t0
                coord_ops += 1

                if not batch:
                    break

                for item in batch:
                    t0 = time.perf_counter()
                    time.sleep(SIMULATED_WORK_MS / 1000)
                    total_work += time.perf_counter() - t0
                    items_done += 1

            with stats_lock:
                stats[idx] = {"items": items_done, "steal": total_steal,
                              "work": total_work, "ops": coord_ops}
        except Exception as e:
            with stats_lock:
                errors.append((idx, str(e)))

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS_PER_STRATEGY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    duration = time.time() - t_start

    ws.close()
    items_per_worker = [stats.get(i, {}).get("items", 0) for i in range(WORKERS_PER_STRATEGY)]
    total_done = sum(items_per_worker)
    total_steal = sum(s.get("steal", 0) for s in stats.values())
    total_work = sum(s.get("work", 0) for s in stats.values())
    total_ops = sum(s.get("ops", 0) for s in stats.values())

    return StrategyResult(
        strategy=f"batch_steal_{batch_size}",
        total_items=WORK_ITEMS_PER_STRATEGY,
        items_completed=total_done,
        duration_s=round(duration, 3),
        throughput_items_per_s=round(total_done / max(duration, 0.001), 2),
        per_worker_throughput=round(total_done / max(duration, 0.001) / WORKERS_PER_STRATEGY, 2),
        items_per_worker=items_per_worker,
        distribution_cv=round(compute_cv(items_per_worker), 4),
        steal_overhead_s=round(total_steal, 3),
        work_time_s=round(total_work, 3),
        steal_to_work_ratio=round(total_steal / max(total_work, 0.001), 3),
        coordination_ops=total_ops,
        errors=[f"w{i}: {e}" for i, e in errors],
        extra_metrics={"batch_size": batch_size},
    )


# ===================================================================
# Main Experiment Runner (Team 8 — Coordinator)
# ===================================================================


def run_experiment():
    """Run all 8 strategies and produce comparative analysis."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Set up shared communication directory for coordinator team
    comm_dir = tempfile.mkdtemp(prefix="efficiency-experiment-")
    bus_dir = os.path.join(comm_dir, "bus")
    db_path = os.path.join(comm_dir, "coord.db")
    os.makedirs(bus_dir, exist_ok=True)

    # Coordinator team reports overall experiment status
    coordinator = AgentReporter(db_path, bus_dir, "experiment-coordinator", "team-coordinator")
    coordinator.update_status("working", 0, "Starting efficiency experiment")

    # Use scratchpad for inter-team result sharing
    scratchpad = Scratchpad(db_path, "team-coordinator", "experiment-coordinator")

    # Use direct channels for team communication
    dc = DirectChannels(db_path, bus_dir, "team-coordinator", "experiment-coordinator")
    dc.set_presence("busy")
    dc.update_progress("initialization", 0, 8, 0, None)

    print("=" * 70)
    print("8-TEAM EFFICIENCY EXPERIMENT")
    print(f"Workload: {WORK_ITEMS_PER_STRATEGY} items, {WORKERS_PER_STRATEGY} workers per strategy")
    print(f"Simulated work: {SIMULATED_WORK_MS}ms per item")
    print("=" * 70)

    strategies = [
        ("Team 1: Baseline Single Steal", lambda: run_baseline_steal(comm_dir)),
        ("Team 2: Batch Steal (10)", lambda: run_batch_steal(comm_dir)),
        ("Team 3: Pre-Partition", lambda: run_pre_partition(comm_dir)),
        ("Team 4: Hierarchical Lead", lambda: run_hierarchical(comm_dir)),
        ("Team 5: Sharded Queues (3)", lambda: run_sharded_queues(comm_dir)),
        ("Team 6: Adaptive Batch", lambda: run_adaptive_batch(comm_dir)),
        ("Team 7: Hybrid 80/20", lambda: run_hybrid(comm_dir)),
        ("Team 8a: Batch Steal (5)", lambda: run_batch_steal_size(comm_dir, 5)),
    ]

    results: list[StrategyResult] = []
    total_start = time.time()

    for i, (name, func) in enumerate(strategies):
        print(f"\n{'='*60}")
        print(f"Running: {name}")
        print(f"{'='*60}")

        coordinator.update_status("working", (i / len(strategies)) * 100, f"Running {name}")
        dc.update_progress("benchmarking", (i / len(strategies)) * 100,
                           len(strategies), i, None)

        try:
            result = func()
            results.append(result)

            # Publish result to scratchpad
            scratchpad.write(f"result-{result.strategy}", asdict(result), namespace="global")

            # Report via bus
            writer = BusWriter(bus_dir, "experiment-coordinator", "team-coordinator")
            writer.publish("experiment", "info", {
                "event": "strategy_complete",
                "strategy": result.strategy,
                "throughput": result.throughput_items_per_s,
                "distribution_cv": result.distribution_cv,
                "steal_ratio": result.steal_to_work_ratio,
            })

            status = "PASS" if result.items_completed == result.total_items else "FAIL"
            print(f"  [{status}] {result.strategy}")
            print(f"    Throughput: {result.throughput_items_per_s:.1f} items/s "
                  f"({result.per_worker_throughput:.1f}/worker)")
            print(f"    Distribution CV: {result.distribution_cv:.4f} "
                  f"(lower=more even, 0=perfect)")
            print(f"    Steal/Work ratio: {result.steal_to_work_ratio:.3f} "
                  f"(lower=less overhead)")
            print(f"    Coordination ops: {result.coordination_ops}")
            print(f"    Items/worker: {result.items_per_worker}")
            if result.errors:
                for e in result.errors[:3]:
                    print(f"    ERROR: {e}")

        except Exception as e:
            print(f"  [CRASH] {name}: {e}")
            traceback.print_exc()

    total_duration = time.time() - total_start
    coordinator.update_status("working", 90, "Analyzing results")

    # ---------------------------------------------------------------
    # Comparative Analysis
    # ---------------------------------------------------------------
    print(f"\n{'='*70}")
    print("COMPARATIVE ANALYSIS")
    print(f"{'='*70}")

    # Rank by throughput
    ranked_throughput = sorted(results, key=lambda r: r.throughput_items_per_s, reverse=True)
    print("\nBy Throughput (items/s):")
    for i, r in enumerate(ranked_throughput, 1):
        baseline_ratio = r.throughput_items_per_s / max(results[0].throughput_items_per_s, 1)
        print(f"  {i}. {r.strategy:35s} {r.throughput_items_per_s:8.1f} items/s "
              f"({baseline_ratio:.2f}x baseline)")

    # Rank by distribution evenness
    ranked_cv = sorted(results, key=lambda r: r.distribution_cv)
    print("\nBy Distribution Evenness (CV, lower=better):")
    for i, r in enumerate(ranked_cv, 1):
        print(f"  {i}. {r.strategy:35s} CV={r.distribution_cv:.4f}")

    # Rank by coordination overhead
    ranked_overhead = sorted(results, key=lambda r: r.steal_to_work_ratio)
    print("\nBy Coordination Overhead (steal/work ratio, lower=better):")
    for i, r in enumerate(ranked_overhead, 1):
        print(f"  {i}. {r.strategy:35s} ratio={r.steal_to_work_ratio:.3f} "
              f"(steal={r.steal_overhead_s:.2f}s, work={r.work_time_s:.2f}s)")

    # Composite score: normalize each metric 0-1, weighted average
    # Higher throughput = better, Lower CV = better, Lower steal ratio = better
    max_tp = max(r.throughput_items_per_s for r in results)
    max_cv = max(r.distribution_cv for r in results) or 1
    max_sr = max(r.steal_to_work_ratio for r in results) or 1

    print("\nComposite Score (throughput×0.4 + evenness×0.3 + low_overhead×0.3):")
    scored = []
    for r in results:
        tp_score = r.throughput_items_per_s / max_tp
        cv_score = 1 - (r.distribution_cv / max_cv)  # invert: lower CV = higher score
        sr_score = 1 - (r.steal_to_work_ratio / max_sr)  # invert: lower ratio = higher score
        composite = tp_score * 0.4 + cv_score * 0.3 + sr_score * 0.3
        scored.append((r.strategy, composite, tp_score, cv_score, sr_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    for i, (name, composite, tp, cv, sr) in enumerate(scored, 1):
        print(f"  {i}. {name:35s} {composite:.3f} "
              f"(tp={tp:.2f} even={cv:.2f} low_oh={sr:.2f})")

    # ---------------------------------------------------------------
    # Key Findings
    # ---------------------------------------------------------------
    best = scored[0]
    worst = scored[-1]
    baseline_score = next(s for s in scored if "baseline" in s[0])

    print(f"\n{'='*70}")
    print("KEY FINDINGS")
    print(f"{'='*70}")
    print(f"  Best strategy:  {best[0]} (score={best[1]:.3f})")
    print(f"  Worst strategy: {worst[0]} (score={worst[1]:.3f})")
    print(f"  Baseline score: {baseline_score[1]:.3f}")
    print(f"  Best improves over baseline by: {((best[1] / max(baseline_score[1], 0.001)) - 1) * 100:.1f}%")

    # ---------------------------------------------------------------
    # Recommendations
    # ---------------------------------------------------------------
    print(f"\n{'='*70}")
    print("RECOMMENDATIONS FOR LARGE TEAMS")
    print(f"{'='*70}")

    # Find the batch steal results to compare batch sizes
    batch_results = [r for r in results if "batch_steal" in r.strategy]
    if batch_results:
        best_batch = max(batch_results, key=lambda r: r.throughput_items_per_s)
        print(f"  1. Best batch size: {best_batch.extra_metrics.get('batch_size', '?')} "
              f"({best_batch.throughput_items_per_s:.0f} items/s)")

    pre_part = next((r for r in results if "pre_partition" in r.strategy), None)
    if pre_part:
        print(f"  2. Pre-partition eliminates steal overhead entirely "
              f"(CV={pre_part.distribution_cv:.4f})")

    hier = next((r for r in results if "hierarchical" in r.strategy), None)
    if hier:
        print(f"  3. Hierarchical: {hier.extra_metrics.get('lead_items_fetched', 0)} items "
              f"fetched by lead, worker wait={hier.extra_metrics.get('worker_wait_time_s', 0):.2f}s")

    hybrid = next((r for r in results if "hybrid" in r.strategy), None)
    if hybrid:
        print(f"  4. Hybrid 80/20 combines pre-partition speed with steal flexibility")

    # ---------------------------------------------------------------
    # Save results
    # ---------------------------------------------------------------
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "work_items": WORK_ITEMS_PER_STRATEGY,
            "workers": WORKERS_PER_STRATEGY,
            "simulated_work_ms": SIMULATED_WORK_MS,
        },
        "total_duration_s": round(total_duration, 3),
        "results": [asdict(r) for r in results],
        "rankings": {
            "by_throughput": [r.strategy for r in ranked_throughput],
            "by_evenness": [r.strategy for r in ranked_cv],
            "by_low_overhead": [r.strategy for r in ranked_overhead],
            "composite": [(name, round(score, 3)) for name, score, _, _, _ in scored],
        },
        "best_strategy": best[0],
        "improvement_over_baseline_pct": round(((best[1] / max(baseline_score[1], 0.001)) - 1) * 100, 1),
    }

    output_path = OUTPUT_DIR / "efficiency_experiment_results.json"
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    # Publish final results to scratchpad
    scratchpad.write("experiment_summary", summary, namespace="global")

    coordinator.report_complete("Efficiency experiment completed")
    dc.update_progress("complete", 100, len(strategies), len(strategies), None)

    print(f"\nResults saved to: {output_path}")
    print(f"Total experiment duration: {total_duration:.2f}s")

    return summary


if __name__ == "__main__":
    run_experiment()
