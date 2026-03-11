#!/usr/bin/env python3
"""
Prototype Benchmark Runner — Live Testing Harness

Runs data gathering scenarios using Prototype A, Prototype B, and a baseline
(no communication layer) to measure coordination overhead, throughput, and reliability.

Usage:
    python proto_benchmark_runner.py --scenario S1 --prototype A
    python proto_benchmark_runner.py --scenario all --prototype all
    python proto_benchmark_runner.py --compare  # Run all and produce comparison

Test IDs: LIVE-TEST-{YYYYMMDD}-{sequence}
"""

import argparse
import json
import logging
import os
import resource
import statistics
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

# Add prototype to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("benchmark")
log.setLevel(logging.INFO)


# ===================================================================
# Data structures
# ===================================================================

@dataclass
class BenchmarkMetrics:
    """Raw metrics collected during a single test run."""
    duration_ms: float = 0.0
    messages_sent: int = 0
    messages_received: int = 0
    data_points_collected: int = 0
    heartbeats_sent: int = 0
    heartbeats_received: int = 0
    errors: int = 0
    bus_file_bytes: int = 0
    peak_memory_kb: int = 0
    rate_limit_checks: int = 0
    rate_limit_denials: int = 0
    dead_agent_detections: int = 0
    latency_samples_ms: list = field(default_factory=list)

    @property
    def avg_latency_ms(self) -> float:
        return statistics.mean(self.latency_samples_ms) if self.latency_samples_ms else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latency_samples_ms:
            return 0.0
        sorted_samples = sorted(self.latency_samples_ms)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]


@dataclass
class BenchmarkResult:
    """Complete result from a single benchmark run."""
    test_id: str
    scenario: str
    prototype: str  # "A", "B", or "baseline"
    timestamp: str
    run_number: int
    metrics: BenchmarkMetrics
    config: dict = field(default_factory=dict)
    errors_detail: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metrics"]["avg_latency_ms"] = self.metrics.avg_latency_ms
        d["metrics"]["p95_latency_ms"] = self.metrics.p95_latency_ms
        return d


@dataclass
class ScenarioConfig:
    """Configuration for a test scenario."""
    name: str
    description: str
    num_teams: int
    agents_per_team: int
    duration_seconds: int
    data_sources: list
    inject_failure: bool = False
    failure_after_seconds: float = 0.0


# ===================================================================
# Scenario Definitions
# ===================================================================

SCENARIOS = {
    "S1": ScenarioConfig(
        name="S1",
        description="Single-source baseline: 1 team, 3 agents, 1 data source",
        num_teams=1,
        agents_per_team=3,
        duration_seconds=15,
        data_sources=["synthetic-api-1"],
    ),
    "S2": ScenarioConfig(
        name="S2",
        description="Multi-source parallel: 3 teams, 3 agents each, 3 data sources",
        num_teams=3,
        agents_per_team=3,
        duration_seconds=20,
        data_sources=["synthetic-api-1", "synthetic-api-2", "synthetic-api-3"],
    ),
    "S3": ScenarioConfig(
        name="S3",
        description="Full coordinator loop: coordinator + 3 teams, heartbeats + phases",
        num_teams=3,
        agents_per_team=3,
        duration_seconds=25,
        data_sources=["synthetic-api-1", "synthetic-api-2", "synthetic-api-3"],
    ),
    "S4": ScenarioConfig(
        name="S4",
        description="Failure injection: kill 1 agent mid-run, measure recovery",
        num_teams=2,
        agents_per_team=3,
        duration_seconds=20,
        data_sources=["synthetic-api-1", "synthetic-api-2"],
        inject_failure=True,
        failure_after_seconds=8.0,
    ),
}


# ===================================================================
# Simulated Data Source (no real API calls needed for benchmarking)
# ===================================================================

class SyntheticDataSource:
    """Simulates an API data source with configurable latency and rate limits."""

    def __init__(self, name: str, latency_ms: float = 50, rate_limit_per_sec: float = 10):
        self.name = name
        self.latency_ms = latency_ms
        self.rate_limit_per_sec = rate_limit_per_sec
        self._call_count = 0
        self._lock = threading.Lock()

    def fetch(self, query: str) -> dict:
        """Simulate an API call with realistic latency."""
        time.sleep(self.latency_ms / 1000.0)
        with self._lock:
            self._call_count += 1
            count = self._call_count
        return {
            "source": self.name,
            "query": query,
            "result_id": count,
            "data": f"Synthetic data point #{count} for '{query}'",
            "timestamp": time.time(),
        }


SYNTHETIC_SOURCES = {
    "synthetic-api-1": SyntheticDataSource("api-1", latency_ms=30, rate_limit_per_sec=20),
    "synthetic-api-2": SyntheticDataSource("api-2", latency_ms=60, rate_limit_per_sec=10),
    "synthetic-api-3": SyntheticDataSource("api-3", latency_ms=100, rate_limit_per_sec=5),
}


# ===================================================================
# Worker Simulation (runs actual prototype code)
# ===================================================================

def get_memory_usage_kb() -> int:
    """Get current RSS memory usage in KB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def run_worker_prototype_a(
    worker_id: str,
    team: str,
    comm_dir: str,
    source_name: str,
    duration: float,
    metrics: BenchmarkMetrics,
    stop_event: threading.Event,
    queries: list,
):
    """Run a worker using Prototype A communication."""
    from agent_comm.bus import BusWriter, BusReader
    from agent_comm.state import SharedState

    db_path = os.path.join(comm_dir, "db", "state.db")
    bus_dir = os.path.join(comm_dir, "bus")
    os.makedirs(bus_dir, exist_ok=True)

    state = SharedState(db_path)
    writer = BusWriter(bus_dir, worker_id, team)
    reader = BusReader(bus_dir, "global")

    # Register
    state.register_agent(worker_id, team, "worker", os.getpid())
    metrics.messages_sent += 1

    source = SYNTHETIC_SOURCES.get(source_name, SYNTHETIC_SOURCES["synthetic-api-1"])
    start = time.time()
    hb_interval = 5.0
    last_hb = start

    query_idx = 0
    while not stop_event.is_set() and (time.time() - start) < duration:
        # Heartbeat
        if time.time() - last_hb >= hb_interval:
            state.heartbeat(worker_id)
            metrics.heartbeats_sent += 1
            last_hb = time.time()

        # Fetch data
        query = queries[query_idx % len(queries)]
        query_idx += 1

        # Check rate limit
        metrics.rate_limit_checks += 1
        allowed = state.reserve_api_call(source_name, worker_id)
        if not allowed:
            metrics.rate_limit_denials += 1
            time.sleep(0.1)
            continue

        try:
            result = source.fetch(query)
            metrics.data_points_collected += 1

            # Publish result to bus
            send_time = time.time()
            writer.publish("global", "info", {
                "event": "data-collected",
                "worker": worker_id,
                "source": source_name,
                "query": query,
                "result_id": result["result_id"],
            })
            metrics.messages_sent += 1

            # Measure receive latency
            msgs = reader.poll()
            recv_time = time.time()
            for msg in msgs:
                metrics.messages_received += 1
                if msg.body.get("worker") == worker_id:
                    latency = (recv_time - send_time) * 1000
                    metrics.latency_samples_ms.append(latency)

        except Exception as e:
            metrics.errors += 1

    state.close()


def run_worker_prototype_b(
    worker_id: str,
    team: str,
    comm_dir: str,
    source_name: str,
    duration: float,
    metrics: BenchmarkMetrics,
    stop_event: threading.Event,
    queries: list,
):
    """Run a worker using Prototype B communication."""
    from agent_comm_b.bus import PipeBusWriter, PipeBusReader
    from agent_comm_b.state import SharedStateMap

    pipes_dir = os.path.join(comm_dir, "pipes")
    shm_dir = os.path.join(comm_dir, "shm")
    os.makedirs(pipes_dir, exist_ok=True)
    os.makedirs(shm_dir, exist_ok=True)

    state = SharedStateMap(shm_dir)
    writer = PipeBusWriter(pipes_dir, worker_id, team)

    # Register
    state.register_agent(worker_id, team, "worker", os.getpid())
    metrics.messages_sent += 1

    source = SYNTHETIC_SOURCES.get(source_name, SYNTHETIC_SOURCES["synthetic-api-1"])
    start = time.time()
    hb_interval = 5.0
    last_hb = start

    query_idx = 0
    while not stop_event.is_set() and (time.time() - start) < duration:
        # Heartbeat
        if time.time() - last_hb >= hb_interval:
            state.heartbeat(worker_id)
            metrics.heartbeats_sent += 1
            last_hb = time.time()

        # Fetch data
        query = queries[query_idx % len(queries)]
        query_idx += 1

        # Check rate limit
        metrics.rate_limit_checks += 1
        allowed = state.reserve_api_call(source_name, worker_id)
        if not allowed:
            metrics.rate_limit_denials += 1
            time.sleep(0.1)
            continue

        try:
            result = source.fetch(query)
            metrics.data_points_collected += 1

            # Publish result via FIFO
            send_time = time.time()
            writer.publish("global", "info", {
                "event": "data-collected",
                "worker": worker_id,
                "source": source_name,
                "query": query,
                "result_id": result["result_id"],
            })
            metrics.messages_sent += 1

            # Measure send latency (no round-trip for FIFOs without reader)
            latency = (time.time() - send_time) * 1000
            metrics.latency_samples_ms.append(latency)

        except Exception as e:
            metrics.errors += 1

    state.close()


def run_worker_baseline(
    worker_id: str,
    team: str,
    comm_dir: str,
    source_name: str,
    duration: float,
    metrics: BenchmarkMetrics,
    stop_event: threading.Event,
    queries: list,
):
    """Run a worker with no communication prototype (baseline)."""
    source = SYNTHETIC_SOURCES.get(source_name, SYNTHETIC_SOURCES["synthetic-api-1"])
    start = time.time()
    results_file = os.path.join(comm_dir, f"{worker_id}_results.jsonl")

    query_idx = 0
    while not stop_event.is_set() and (time.time() - start) < duration:
        query = queries[query_idx % len(queries)]
        query_idx += 1

        try:
            result = source.fetch(query)
            metrics.data_points_collected += 1

            # Write result to file (simulating baseline file-polling)
            send_time = time.time()
            with open(results_file, "a") as f:
                f.write(json.dumps(result) + "\n")
            metrics.messages_sent += 1
            latency = (time.time() - send_time) * 1000
            metrics.latency_samples_ms.append(latency)

        except Exception as e:
            metrics.errors += 1


# ===================================================================
# Scenario Runner
# ===================================================================

def run_scenario(
    scenario: ScenarioConfig,
    prototype: str,
    run_number: int = 1,
) -> BenchmarkResult:
    """Execute a single scenario with the specified prototype."""

    test_id = f"LIVE-TEST-{time.strftime('%Y%m%d')}-{run_number:03d}"
    log.info("=== %s | Scenario %s | Prototype %s | Run %d ===",
             test_id, scenario.name, prototype, run_number)

    # Create temp directory for this run
    run_dir = tempfile.mkdtemp(prefix=f"proto-bench-{prototype}-{scenario.name}-")
    os.makedirs(os.path.join(run_dir, "bus"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "db"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "pipes"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "shm"), exist_ok=True)

    metrics = BenchmarkMetrics()
    stop_event = threading.Event()
    threads = []
    worker_metrics = []

    queries = [
        "multi-agent coordination",
        "distributed systems",
        "real-time communication",
        "data pipeline optimization",
        "consensus algorithms",
    ]

    # Select worker function
    worker_fn = {
        "A": run_worker_prototype_a,
        "B": run_worker_prototype_b,
        "baseline": run_worker_baseline,
    }[prototype]

    # Configure rate limits for Proto A
    if prototype == "A":
        from agent_comm.state import SharedState
        db_path = os.path.join(run_dir, "db", "state.db")
        state = SharedState(db_path)
        for src_name in scenario.data_sources:
            src = SYNTHETIC_SOURCES[src_name]
            state.configure_rate_limit(src_name, int(src.rate_limit_per_sec * 5), 5)
        state.close()
    elif prototype == "B":
        # Proto B uses mmap rate limits — configure after init
        pass

    # Pre-run memory
    mem_before = get_memory_usage_kb()

    # Launch workers
    start_time = time.time()
    worker_idx = 0
    for team_idx in range(scenario.num_teams):
        team_name = f"team-{team_idx + 1:02d}"
        source_name = scenario.data_sources[team_idx % len(scenario.data_sources)]

        for agent_idx in range(scenario.agents_per_team):
            worker_id = f"{team_name}-worker-{agent_idx + 1}"
            wm = BenchmarkMetrics()
            worker_metrics.append(wm)

            t = threading.Thread(
                target=worker_fn,
                args=(worker_id, team_name, run_dir, source_name,
                      scenario.duration_seconds, wm, stop_event, queries),
                name=f"worker-{worker_id}",
                daemon=True,
            )
            threads.append(t)
            worker_idx += 1

    # Start all workers
    for t in threads:
        t.start()

    # Failure injection for S4
    if scenario.inject_failure:
        def inject():
            time.sleep(scenario.failure_after_seconds)
            if threads:
                log.info("Injecting failure: stopping worker thread %s", threads[0].name)
                stop_event.set()
                time.sleep(0.5)
                stop_event.clear()
        failure_thread = threading.Thread(target=inject, daemon=True)
        failure_thread.start()

    # Wait for completion
    for t in threads:
        t.join(timeout=scenario.duration_seconds + 10)

    end_time = time.time()

    # Aggregate metrics
    metrics.duration_ms = (end_time - start_time) * 1000
    for wm in worker_metrics:
        metrics.messages_sent += wm.messages_sent
        metrics.messages_received += wm.messages_received
        metrics.data_points_collected += wm.data_points_collected
        metrics.heartbeats_sent += wm.heartbeats_sent
        metrics.heartbeats_received += wm.heartbeats_received
        metrics.errors += wm.errors
        metrics.rate_limit_checks += wm.rate_limit_checks
        metrics.rate_limit_denials += wm.rate_limit_denials
        metrics.latency_samples_ms.extend(wm.latency_samples_ms)

    # Measure bus file size
    bus_dir = os.path.join(run_dir, "bus")
    for f in os.listdir(bus_dir):
        fpath = os.path.join(bus_dir, f)
        if os.path.isfile(fpath):
            metrics.bus_file_bytes += os.path.getsize(fpath)

    # Memory delta
    metrics.peak_memory_kb = get_memory_usage_kb() - mem_before

    result = BenchmarkResult(
        test_id=test_id,
        scenario=scenario.name,
        prototype=prototype,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        run_number=run_number,
        metrics=metrics,
        config={
            "num_teams": scenario.num_teams,
            "agents_per_team": scenario.agents_per_team,
            "duration_seconds": scenario.duration_seconds,
            "data_sources": scenario.data_sources,
            "inject_failure": scenario.inject_failure,
        },
    )

    # Cleanup
    import shutil
    shutil.rmtree(run_dir, ignore_errors=True)

    log.info("  Duration: %.0f ms | Data points: %d | Messages: %d sent / %d recv | Errors: %d",
             metrics.duration_ms, metrics.data_points_collected,
             metrics.messages_sent, metrics.messages_received, metrics.errors)

    return result


# ===================================================================
# Comparison Report
# ===================================================================

def generate_comparison(results: list[BenchmarkResult]) -> dict:
    """Generate a comparison report from multiple benchmark results."""
    # Group by scenario and prototype
    groups = {}
    for r in results:
        key = (r.scenario, r.prototype)
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    comparison = {"scenarios": {}, "summary": {}}

    for scenario_name in sorted(set(r.scenario for r in results)):
        scenario_data = {}

        for proto in ["baseline", "A", "B"]:
            key = (scenario_name, proto)
            if key not in groups:
                continue

            runs = groups[key]
            durations = [r.metrics.duration_ms for r in runs]
            data_points = [r.metrics.data_points_collected for r in runs]
            errors = [r.metrics.errors for r in runs]
            latencies = []
            for r in runs:
                latencies.extend(r.metrics.latency_samples_ms)

            scenario_data[proto] = {
                "runs": len(runs),
                "duration_ms": {
                    "mean": statistics.mean(durations),
                    "median": statistics.median(durations),
                    "stdev": statistics.stdev(durations) if len(durations) > 1 else 0,
                },
                "data_points": {
                    "mean": statistics.mean(data_points),
                    "total": sum(data_points),
                },
                "errors_total": sum(errors),
                "latency_ms": {
                    "mean": statistics.mean(latencies) if latencies else 0,
                    "p95": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
                    "samples": len(latencies),
                },
                "messages_sent_total": sum(r.metrics.messages_sent for r in runs),
                "heartbeats_total": sum(r.metrics.heartbeats_sent for r in runs),
                "rate_limit_denials_total": sum(r.metrics.rate_limit_denials for r in runs),
            }

        # Calculate overhead vs baseline
        if "baseline" in scenario_data:
            baseline_duration = scenario_data["baseline"]["duration_ms"]["mean"]
            for proto in ["A", "B"]:
                if proto in scenario_data:
                    proto_duration = scenario_data[proto]["duration_ms"]["mean"]
                    overhead_pct = ((proto_duration - baseline_duration) / baseline_duration) * 100
                    scenario_data[proto]["overhead_pct"] = round(overhead_pct, 2)

        comparison["scenarios"][scenario_name] = scenario_data

    return comparison


def print_comparison_table(comparison: dict) -> str:
    """Format comparison as a readable table."""
    lines = []
    lines.append("=" * 90)
    lines.append("PROTOTYPE BENCHMARK COMPARISON REPORT")
    lines.append("=" * 90)

    for scenario_name, data in sorted(comparison["scenarios"].items()):
        scenario_cfg = SCENARIOS.get(scenario_name)
        desc = scenario_cfg.description if scenario_cfg else scenario_name
        lines.append(f"\n--- {scenario_name}: {desc} ---\n")

        header = f"{'Metric':<30} | {'Baseline':>14} | {'Proto A':>14} | {'Proto B':>14}"
        lines.append(header)
        lines.append("-" * len(header))

        def get_val(proto, path):
            d = data.get(proto, {})
            for key in path.split("."):
                if isinstance(d, dict):
                    d = d.get(key, "N/A")
                else:
                    return "N/A"
            if isinstance(d, float):
                return f"{d:.1f}"
            return str(d)

        rows = [
            ("Duration (ms, mean)", "duration_ms.mean"),
            ("Duration (ms, stdev)", "duration_ms.stdev"),
            ("Data Points (mean)", "data_points.mean"),
            ("Total Errors", "errors_total"),
            ("Latency (ms, mean)", "latency_ms.mean"),
            ("Latency (ms, p95)", "latency_ms.p95"),
            ("Messages Sent", "messages_sent_total"),
            ("Heartbeats", "heartbeats_total"),
            ("Rate Limit Denials", "rate_limit_denials_total"),
            ("Overhead vs Baseline (%)", "overhead_pct"),
        ]

        for label, path in rows:
            baseline = get_val("baseline", path)
            proto_a = get_val("A", path)
            proto_b = get_val("B", path)
            lines.append(f"{label:<30} | {baseline:>14} | {proto_a:>14} | {proto_b:>14}")

    lines.append("\n" + "=" * 90)

    # Scoring
    lines.append("\nSCORECARD:")
    lines.append("-" * 50)
    for scenario_name, data in sorted(comparison["scenarios"].items()):
        for proto in ["A", "B"]:
            if proto not in data:
                continue
            overhead = data[proto].get("overhead_pct", 0)
            errors = data[proto].get("errors_total", 0)
            if overhead <= 10 and errors == 0:
                verdict = "PASS"
            elif overhead <= 25:
                verdict = "CONDITIONAL PASS"
            else:
                verdict = "FAIL"
            lines.append(f"  {scenario_name} Proto {proto}: {verdict} (overhead={overhead:.1f}%, errors={errors})")

    return "\n".join(lines)


# ===================================================================
# Main
# ===================================================================

def main():
    parser = argparse.ArgumentParser(description="Prototype Benchmark Runner")
    parser.add_argument("--scenario", default="all", choices=["S1", "S2", "S3", "S4", "all"])
    parser.add_argument("--prototype", default="all", choices=["A", "B", "baseline", "all"])
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per scenario")
    parser.add_argument("--compare", action="store_true", help="Run all and produce comparison")
    parser.add_argument("--output", default=None, help="Output file for results JSON")
    args = parser.parse_args()

    if args.compare:
        args.scenario = "all"
        args.prototype = "all"

    scenarios = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]
    prototypes = ["baseline", "A", "B"] if args.prototype == "all" else [args.prototype]

    all_results = []

    for scenario_name in scenarios:
        scenario = SCENARIOS[scenario_name]
        for proto in prototypes:
            for run in range(1, args.runs + 1):
                try:
                    result = run_scenario(scenario, proto, run)
                    all_results.append(result)
                except Exception as e:
                    log.error("Failed: %s %s run %d: %s", scenario_name, proto, run, e)

    # Generate comparison
    if len(all_results) > 1:
        comparison = generate_comparison(all_results)
        report = print_comparison_table(comparison)
        print(report)

        # Save results
        output_path = args.output or os.path.join(
            os.path.dirname(__file__), "benchmark_results.json"
        )
        output_data = {
            "test_date": time.strftime("%Y-%m-%d"),
            "results": [r.to_dict() for r in all_results],
            "comparison": comparison,
        }
        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2)
        log.info("Results saved to %s", output_path)

    elif all_results:
        r = all_results[0]
        print(json.dumps(r.to_dict(), indent=2))


if __name__ == "__main__":
    main()
