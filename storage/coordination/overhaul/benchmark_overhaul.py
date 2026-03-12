#!/usr/bin/env python3
"""Comprehensive benchmark runner for the data storage overhaul operation.

Measures performance across all coordination subsystems:
- Bus throughput (read/write at 1, 4, 8, 16 simulated agents)
- Rate limiter accuracy under load
- Index generation speed
- SQLite contention (ops/sec at 1/4/8/16 writers)
- End-to-end multi-agent scenario

Outputs results as JSON compatible with storage/data/unified_benchmarks.json.
Compares against baseline numbers and reports regressions.

Usage:
    python benchmark_overhaul.py [--output results.json] [--baseline baseline.json]
                                  [--repo-root /path/to/repo] [--iterations 5]
"""

from __future__ import annotations

import argparse
import json
import math
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

def _find_repo_root(start: str | None = None) -> str:
    """Walk up from *start* until we find CLAUDE.md."""
    d = start or os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isfile(os.path.join(d, "CLAUDE.md")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError("Could not locate repository root (no CLAUDE.md found)")


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def compute_stats(samples: list[float]) -> dict:
    """Compute mean, median, p50, p95, p99, min, max, sample_count from a list of values (ms)."""
    if not samples:
        return {
            "mean_ms": 0, "median_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0,
            "min_ms": 0, "max_ms": 0, "sample_count": 0
        }
    s = sorted(samples)
    n = len(s)
    return {
        "mean_ms": round(statistics.mean(s), 4),
        "median_ms": round(statistics.median(s), 4),
        "p50_ms": round(s[int(n * 0.50)] if n > 1 else s[0], 4),
        "p95_ms": round(s[min(int(n * 0.95), n - 1)], 4),
        "p99_ms": round(s[min(int(n * 0.99), n - 1)], 4),
        "min_ms": round(s[0], 4),
        "max_ms": round(s[-1], 4),
        "sample_count": n,
    }


def timer_ms(fn) -> float:
    """Execute fn() and return elapsed time in milliseconds."""
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


# ===================================================================
# 1. BUS THROUGHPUT BENCHMARK
# ===================================================================

def benchmark_bus_throughput(repo_root: str, iterations: int = 5) -> dict:
    """Measure bus read/write messages/sec at 1, 4, 8, 16 simulated agents."""
    sys.path.insert(0, repo_root)
    try:
        from storage.coordination.bus_core import bus_write, bus_read, sanitize_channel
    except ImportError as exc:
        return {"error": f"Cannot import bus_core: {exc}", "benchmarks": {}}

    results = {}
    agent_counts = [1, 4, 8, 16]

    for num_agents in agent_counts:
        with tempfile.TemporaryDirectory(prefix=f"bench_bus_{num_agents}_") as tmpdir:
            bus_dir = os.path.join(tmpdir, "bus")
            channel = "bench"
            msgs_per_agent = 50

            # --- Write benchmark ---
            write_times: list[float] = []
            write_errors = 0

            for iteration in range(iterations):
                barrier = threading.Barrier(num_agents)
                local_times: list[float] = []
                local_errors = [0]
                lock = threading.Lock()

                def writer(agent_id: int):
                    try:
                        barrier.wait(timeout=10)
                    except threading.BrokenBarrierError:
                        return
                    for j in range(msgs_per_agent):
                        start = time.perf_counter()
                        mid = bus_write(bus_dir, channel, "info",
                                       {"agent": agent_id, "seq": j, "iter": iteration},
                                       "bench", f"agent-{agent_id}")
                        elapsed = (time.perf_counter() - start) * 1000
                        with lock:
                            local_times.append(elapsed)
                            if mid is None:
                                local_errors[0] += 1

                threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_agents)]
                t0 = time.perf_counter()
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=30)
                wall_ms = (time.perf_counter() - t0) * 1000

                write_times.extend(local_times)
                write_errors += local_errors[0]

            total_written = num_agents * msgs_per_agent * iterations
            write_stats = compute_stats(write_times)
            write_throughput = (total_written / (sum(write_times) / 1000)) if sum(write_times) > 0 else 0

            # --- Read benchmark ---
            read_times: list[float] = []
            for iteration in range(iterations):
                offset = 0
                start = time.perf_counter()
                msgs, new_offset = bus_read(bus_dir, channel, offset)
                elapsed = (time.perf_counter() - start) * 1000
                read_times.append(elapsed)
                # Read in chunks to simulate incremental reads
                while new_offset > offset:
                    offset = new_offset
                    start = time.perf_counter()
                    msgs, new_offset = bus_read(bus_dir, channel, offset)
                    elapsed = (time.perf_counter() - start) * 1000
                    if elapsed > 0.001:
                        read_times.append(elapsed)

            read_stats = compute_stats(read_times)

            results[f"{num_agents}_agents"] = {
                "write": {
                    **write_stats,
                    "total_messages": total_written,
                    "errors": write_errors,
                    "throughput_msgs_per_sec": round(write_throughput, 2),
                },
                "read": {
                    **read_stats,
                },
            }

    return {"benchmarks": results, "error": None}


# ===================================================================
# 2. RATE LIMITER ACCURACY BENCHMARK
# ===================================================================

def benchmark_rate_limiter(repo_root: str, iterations: int = 5) -> dict:
    """Measure denial rate under load. Target: <1% false denials."""
    sys.path.insert(0, repo_root)

    try:
        from prototype.agent_comm.rate_limiter import RateLimiter
    except ImportError as exc:
        return {"error": f"Cannot import RateLimiter from prototype.agent_comm.rate_limiter: {exc}", "benchmarks": {}}

    results = {}
    thread_counts = [1, 4, 8, 16]

    for num_threads in thread_counts:
        check_times: list[float] = []
        total_checks = 0
        total_denials = 0
        false_denials = 0  # denials when under the limit

        for iteration in range(iterations):
            with tempfile.TemporaryDirectory(prefix="bench_rl_") as tmpdir:
                db_path = os.path.join(tmpdir, "rate_limiter.db")
                rl = RateLimiter(db_path)
                # Set limit high enough that requests WITHIN limit should never be denied
                # 20 requests/thread * 16 threads = 320, well under 1000 limit
                rl.configure("bench_endpoint", max_calls=1000, window_seconds=60)

                requests_per_thread = 20
                barrier = threading.Barrier(num_threads)
                lock = threading.Lock()
                local_times: list[float] = []
                local_denials = [0]
                local_total = [0]

                def checker(tid: int):
                    try:
                        barrier.wait(timeout=10)
                    except threading.BrokenBarrierError:
                        return
                    for j in range(requests_per_thread):
                        start = time.perf_counter()
                        allowed = rl.check_and_reserve("bench_endpoint", f"agent-{tid}")
                        elapsed = (time.perf_counter() - start) * 1000
                        with lock:
                            local_times.append(elapsed)
                            local_total[0] += 1
                            if not allowed:
                                local_denials[0] += 1

                threads = [threading.Thread(target=checker, args=(i,)) for i in range(num_threads)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=15)

                check_times.extend(local_times)
                total_checks += local_total[0]
                false_denials += local_denials[0]
                total_denials += local_denials[0]
                rl.close()

        denial_rate = (false_denials / total_checks * 100) if total_checks > 0 else 0
        stats = compute_stats(check_times)

        results[f"{num_threads}_threads"] = {
            **stats,
            "total_checks": total_checks,
            "total_denials": total_denials,
            "false_denial_rate_pct": round(denial_rate, 4),
            "target_met": denial_rate < 1.0,
        }

    return {"benchmarks": results, "error": None}


# ===================================================================
# 3. INDEX GENERATION SPEED BENCHMARK
# ===================================================================

def benchmark_index_generation(repo_root: str, iterations: int = 5) -> dict:
    """Time to scan and validate all index.json files."""
    index_paths = [
        "knowledge-base/index.json",
        "storage/scripts/index.json",
        "storage/api-tools/index.json",
        "storage/sources/index.json",
        "storage/data/index.json",
        "storage/coordination/sops/index.json",
        "teams/sessions/index.json",
        "market-research/picks/index.json",
    ]

    scan_times: list[float] = []
    parse_times: list[float] = []
    total_entries = 0

    for iteration in range(iterations):
        # Scan phase: find all index files
        start = time.perf_counter()
        found_indexes = []
        for rel_path in index_paths:
            full_path = os.path.join(repo_root, rel_path)
            if os.path.isfile(full_path):
                found_indexes.append(full_path)
        scan_elapsed = (time.perf_counter() - start) * 1000
        scan_times.append(scan_elapsed)

        # Parse phase: load and count entries
        start = time.perf_counter()
        iter_entries = 0
        for idx_path in found_indexes:
            try:
                with open(idx_path, "r") as f:
                    data = json.load(f)
                # Count entries at various levels
                if isinstance(data, list):
                    iter_entries += len(data)
                elif isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            iter_entries += len(v)
                        elif isinstance(v, dict):
                            for vv in v.values():
                                if isinstance(vv, list):
                                    iter_entries += len(vv)
            except Exception:
                pass
        parse_elapsed = (time.perf_counter() - start) * 1000
        parse_times.append(parse_elapsed)
        total_entries = max(total_entries, iter_entries)

    # Also try the auto_index tool if available
    auto_index_times: list[float] = []
    try:
        sys.path.insert(0, repo_root)
        from storage.scripts.auto_index import generate_all_indexes
        for iteration in range(min(iterations, 3)):
            start = time.perf_counter()
            generate_all_indexes(repo_root)
            elapsed = (time.perf_counter() - start) * 1000
            auto_index_times.append(elapsed)
    except ImportError:
        pass
    except Exception:
        pass

    return {
        "benchmarks": {
            "scan": compute_stats(scan_times),
            "parse": compute_stats(parse_times),
            "auto_index": compute_stats(auto_index_times) if auto_index_times else None,
            "indexes_found": len(found_indexes) if 'found_indexes' in dir() else 0,
            "total_entries": total_entries,
        },
        "error": None,
    }


# ===================================================================
# 4. SQLITE CONTENTION BENCHMARK
# ===================================================================

def benchmark_sqlite_contention(repo_root: str, iterations: int = 3) -> dict:
    """Measure ops/sec and p50/p95/p99 per partition at 1/4/8/16 writers."""
    sys.path.insert(0, repo_root)
    try:
        from storage.coordination.bus_core import init_db
    except ImportError:
        return {"error": "Cannot import init_db", "benchmarks": {}}

    writer_counts = [1, 4, 8, 16]
    ops_per_writer = 50
    results = {}

    for num_writers in writer_counts:
        all_times: list[float] = []
        total_ops = 0
        total_errors = 0

        for iteration in range(iterations):
            with tempfile.TemporaryDirectory(prefix=f"bench_sqlite_{num_writers}_") as tmpdir:
                db_path = os.path.join(tmpdir, "contention.db")
                setup_conn = init_db(db_path)
                setup_conn.execute("""
                    CREATE TABLE IF NOT EXISTS bench (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        writer TEXT, seq INTEGER, data TEXT, ts REAL
                    )
                """)
                setup_conn.commit()
                setup_conn.close()

                barrier = threading.Barrier(num_writers)
                lock = threading.Lock()
                local_times: list[float] = []
                local_errors = [0]

                def writer(wid: int):
                    conn = init_db(db_path)
                    try:
                        barrier.wait(timeout=10)
                    except threading.BrokenBarrierError:
                        conn.close()
                        return
                    for seq in range(ops_per_writer):
                        start = time.perf_counter()
                        retries = 0
                        success = False
                        while retries < 10:
                            try:
                                conn.execute(
                                    "INSERT INTO bench (writer, seq, data, ts) VALUES (?, ?, ?, ?)",
                                    (f"w-{wid}", seq, f"data-{wid}-{seq}", time.time())
                                )
                                conn.commit()
                                success = True
                                break
                            except sqlite3.OperationalError as exc:
                                if "busy" in str(exc).lower() or "locked" in str(exc).lower():
                                    retries += 1
                                    time.sleep(0.005 * retries)
                                else:
                                    with lock:
                                        local_errors[0] += 1
                                    break
                        elapsed = (time.perf_counter() - start) * 1000
                        with lock:
                            local_times.append(elapsed)
                            if not success:
                                local_errors[0] += 1

                    conn.close()

                wall_start = time.perf_counter()
                threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_writers)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=30)
                wall_ms = (time.perf_counter() - wall_start) * 1000

                all_times.extend(local_times)
                total_ops += len(local_times)
                total_errors += local_errors[0]

        stats = compute_stats(all_times)
        wall_total_s = sum(all_times) / 1000
        ops_per_sec = total_ops / wall_total_s if wall_total_s > 0 else 0

        results[f"{num_writers}_writers"] = {
            **stats,
            "total_ops": total_ops,
            "errors": total_errors,
            "ops_per_sec": round(ops_per_sec, 2),
        }

    return {"benchmarks": results, "error": None}


# ===================================================================
# 5. END-TO-END MULTI-AGENT SCENARIO BENCHMARK
# ===================================================================

def benchmark_end_to_end(repo_root: str, iterations: int = 3) -> dict:
    """Full scenario: register, heartbeat, work-steal, help-request, findings."""
    sys.path.insert(0, repo_root)

    try:
        from storage.coordination.coordinator_hub import AgentReporter, CoordinatorDashboard
        from storage.coordination.work_stealing import WorkStealing
        from storage.coordination.help_protocol import HelpProtocol
        from storage.coordination.bus_core import bus_write, bus_read
    except ImportError as exc:
        return {"error": f"Import failed: {exc}", "benchmarks": {}}

    scenario_times: list[float] = []
    phase_times: dict[str, list[float]] = {
        "registration": [],
        "status_updates": [],
        "work_enqueue": [],
        "work_steal": [],
        "help_request": [],
        "bus_communication": [],
        "dashboard_read": [],
        "completion": [],
    }

    for iteration in range(iterations):
        with tempfile.TemporaryDirectory(prefix="bench_e2e_") as tmpdir:
            db_path = os.path.join(tmpdir, "hub.db")
            ws_db = os.path.join(tmpdir, "ws.db")
            hp_db = os.path.join(tmpdir, "hp.db")
            bus_dir = os.path.join(tmpdir, "bus")

            scenario_start = time.perf_counter()

            # Phase 1: Registration
            t0 = time.perf_counter()
            dashboard = CoordinatorDashboard(db_path, bus_dir=bus_dir)
            agents = []
            for i in range(5):
                agent = AgentReporter(db_path, f"e2e-agent-{i}", "epsilon", f"role-{i}", bus_dir=bus_dir)
                agents.append(agent)
            reg_ms = (time.perf_counter() - t0) * 1000
            phase_times["registration"].append(reg_ms)

            # Phase 2: Status updates
            t0 = time.perf_counter()
            for i, agent in enumerate(agents):
                agent.update_status("working", i * 20, f"Task {i}")
            update_ms = (time.perf_counter() - t0) * 1000
            phase_times["status_updates"].append(update_ms)

            # Phase 3: Work enqueue
            t0 = time.perf_counter()
            ws = WorkStealing(ws_db, bus_dir, "epsilon", "ep-lead")
            work_ids = []
            for i in range(10):
                wid = ws.enqueue_work(f"Task {i}", f"Description {i}", priority=5)
                work_ids.append(wid)
            enqueue_ms = (time.perf_counter() - t0) * 1000
            phase_times["work_enqueue"].append(enqueue_ms)

            # Phase 4: Work steal
            t0 = time.perf_counter()
            stolen = []
            for i in range(5):
                stealer = WorkStealing(ws_db, bus_dir, f"team-{i}", f"agent-{i}")
                item = stealer.steal_work()
                if item:
                    stolen.append(item)
                stealer.close()
            steal_ms = (time.perf_counter() - t0) * 1000
            phase_times["work_steal"].append(steal_ms)

            # Phase 5: Help request
            t0 = time.perf_counter()
            hp = HelpProtocol(hp_db, bus_dir, "epsilon", "ep-lead")
            for i in range(3):
                wi = hp.add_work_item(f"Help task {i}", priority="high")
                hp.request_help(wi, f"Need help with task {i}")
            help_ms = (time.perf_counter() - t0) * 1000
            phase_times["help_request"].append(help_ms)

            # Phase 6: Bus communication
            t0 = time.perf_counter()
            for i in range(20):
                bus_write(bus_dir, "e2e-bench", "info",
                         {"finding": f"result-{i}", "confidence": 0.95},
                         "epsilon", f"e2e-agent-{i % 5}")
            msgs, _ = bus_read(bus_dir, "e2e-bench", 0)
            bus_ms = (time.perf_counter() - t0) * 1000
            phase_times["bus_communication"].append(bus_ms)

            # Phase 7: Dashboard read
            t0 = time.perf_counter()
            all_status = dashboard.get_all_status()
            active = dashboard.get_active_agents()
            summary = dashboard.get_summary()
            dash_ms = (time.perf_counter() - t0) * 1000
            phase_times["dashboard_read"].append(dash_ms)

            # Phase 8: Completion
            t0 = time.perf_counter()
            for agent in agents:
                agent.report_complete("output.json")
            for agent in agents:
                agent.close()
            dashboard.close()
            ws.close()
            hp.close()
            complete_ms = (time.perf_counter() - t0) * 1000
            phase_times["completion"].append(complete_ms)

            scenario_total = (time.perf_counter() - scenario_start) * 1000
            scenario_times.append(scenario_total)

    return {
        "benchmarks": {
            "scenario_total": compute_stats(scenario_times),
            "phases": {name: compute_stats(times) for name, times in phase_times.items()},
            "agents_per_scenario": 5,
            "work_items_per_scenario": 10,
            "bus_messages_per_scenario": 20,
            "help_requests_per_scenario": 3,
        },
        "error": None,
    }


# ===================================================================
# Regression detection
# ===================================================================

def detect_regressions(current: dict, baseline: dict, threshold_pct: float = 20.0) -> list[dict]:
    """Compare current benchmarks against baseline, flagging regressions > threshold_pct."""
    regressions = []

    def _compare(path: str, current_val: float, baseline_val: float):
        if baseline_val <= 0:
            return
        change_pct = ((current_val - baseline_val) / baseline_val) * 100
        if change_pct > threshold_pct:  # Higher is worse for latency
            regressions.append({
                "metric": path,
                "baseline": round(baseline_val, 4),
                "current": round(current_val, 4),
                "change_pct": round(change_pct, 2),
                "severity": "critical" if change_pct > 50 else "warning",
            })

    def _walk(prefix: str, cur: Any, base: Any):
        if isinstance(cur, dict) and isinstance(base, dict):
            for key in cur:
                if key in base:
                    _walk(f"{prefix}.{key}" if prefix else key, cur[key], base[key])
        elif isinstance(cur, (int, float)) and isinstance(base, (int, float)):
            # For latency metrics (mean_ms, p95_ms, p99_ms), higher is worse
            if any(suffix in prefix for suffix in ["_ms", "latency", "duration"]):
                _compare(prefix, cur, base)
            # For throughput (ops_per_sec, throughput), lower is worse
            elif any(suffix in prefix for suffix in ["ops_per_sec", "throughput"]):
                _compare(prefix, base, cur)  # invert: lower current is regression

    _walk("", current, baseline)
    return regressions


# ===================================================================
# Main runner
# ===================================================================

def run_all_benchmarks(repo_root: str, iterations: int = 5, baseline_path: str | None = None) -> dict:
    """Run all benchmark categories and return the full report."""
    start_time = time.time()

    benchmarks = {}
    benchmark_runners = [
        ("bus_throughput", lambda: benchmark_bus_throughput(repo_root, iterations)),
        ("rate_limiter_accuracy", lambda: benchmark_rate_limiter(repo_root, iterations)),
        ("index_generation_speed", lambda: benchmark_index_generation(repo_root, iterations)),
        ("sqlite_contention", lambda: benchmark_sqlite_contention(repo_root, min(iterations, 3))),
        ("end_to_end_scenario", lambda: benchmark_end_to_end(repo_root, min(iterations, 3))),
    ]

    for name, runner in benchmark_runners:
        try:
            result = runner()
            benchmarks[name] = result
        except Exception as exc:
            benchmarks[name] = {"error": str(exc), "benchmarks": {}}

    end_time = time.time()

    # Load baseline for regression detection
    regressions = []
    if baseline_path and os.path.isfile(baseline_path):
        try:
            with open(baseline_path, "r") as f:
                baseline = json.load(f)
            regressions = detect_regressions(benchmarks, baseline.get("benchmarks", {}))
        except Exception as exc:
            regressions = [{"error": f"Could not load baseline: {exc}"}]

    report = {
        "benchmark_suite": "overhaul_benchmarks",
        "version": "1.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repo_root": repo_root,
        "iterations": iterations,
        "total_duration_ms": round((end_time - start_time) * 1000, 3),
        "benchmarks": benchmarks,
        "regressions": regressions,
        "regression_count": len([r for r in regressions if "error" not in r]),
        "all_benchmarks_pass": all(
            not b.get("error") for b in benchmarks.values()
        ),
    }

    return report


def main():
    parser = argparse.ArgumentParser(description="Overhaul Benchmark Runner")
    parser.add_argument("--repo-root", default=None,
                        help="Repository root path (auto-detected if omitted)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSON results path")
    parser.add_argument("--baseline", "-b", default=None,
                        help="Baseline JSON path for regression detection")
    parser.add_argument("--iterations", "-n", type=int, default=5,
                        help="Number of iterations per benchmark (default: 5)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed results")
    args = parser.parse_args()

    repo_root = args.repo_root or _find_repo_root()
    report = run_all_benchmarks(repo_root, args.iterations, args.baseline)

    # Output
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report written to {args.output}")

    # Console summary
    print(f"\n{'='*60}")
    print(f"OVERHAUL BENCHMARK REPORT")
    print(f"{'='*60}")
    print(f"Iterations: {args.iterations}")
    print(f"Duration: {report['total_duration_ms']:.1f}ms")
    print(f"Benchmarks run: {len(report['benchmarks'])}")

    errors = [name for name, b in report["benchmarks"].items() if b.get("error")]
    if errors:
        print(f"Errors: {', '.join(errors)}")

    if report["regressions"]:
        print(f"\nREGRESSIONS DETECTED: {report['regression_count']}")
        for reg in report["regressions"]:
            if "error" not in reg:
                print(f"  [{reg['severity'].upper()}] {reg['metric']}: "
                      f"{reg['baseline']} -> {reg['current']} ({reg['change_pct']:+.1f}%)")

    if args.verbose:
        for name, bench in report["benchmarks"].items():
            print(f"\n--- {name} ---")
            if bench.get("error"):
                print(f"  ERROR: {bench['error']}")
            else:
                _print_bench(bench.get("benchmarks", {}), indent=2)

    print(f"\n{'='*60}")
    sys.exit(0 if report["regression_count"] == 0 and not errors else 1)


def _print_bench(data: Any, indent: int = 0):
    """Pretty-print benchmark data."""
    prefix = " " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                print(f"{prefix}{k}:")
                _print_bench(v, indent + 2)
            elif isinstance(v, float):
                print(f"{prefix}{k}: {v:.4f}")
            else:
                print(f"{prefix}{k}: {v}")
    elif isinstance(data, list):
        for item in data[:5]:
            print(f"{prefix}- {item}")


if __name__ == "__main__":
    main()
