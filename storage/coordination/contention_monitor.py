"""Real-time SQLite Contention Monitor for Partitioned Databases.

Monitors all partitioned databases for contention metrics and provides:
- ops/sec throughput measurement
- p50/p95/p99 latency percentiles
- SQLITE_BUSY retry counts
- WAL file size tracking
- Configurable alerting thresholds
- JSONL log output for time-series analysis
- Background monitoring thread
- One-shot diagnostic mode
- Self-test mode simulating N concurrent writers

Designed to work with db_partition.PartitionedStore but can also monitor
standalone SQLite databases.

Usage:
    # Background monitor
    python contention_monitor.py --db-dir ./db --interval 5

    # One-shot diagnostic
    python contention_monitor.py --db-dir ./db --one-shot

    # Self-test with N concurrent writers
    python contention_monitor.py --self-test --writers 8 --duration 10
"""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import os
import random
import sqlite3
import statistics
import sys
import threading
import time
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alert thresholds (configurable)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "ops_per_sec_min": 100,        # Alert if ops/sec drops below this
    "p99_latency_ms_max": 100,     # Alert if p99 latency exceeds this
    "busy_retry_pct_max": 5.0,     # Alert if >5% of ops hit SQLITE_BUSY
    "wal_size_mb_max": 50,         # Alert if WAL file exceeds 50MB
}


# ---------------------------------------------------------------------------
# Latency Tracker (lock-free for readers, threaded accumulation)
# ---------------------------------------------------------------------------


class LatencyTracker:
    """Accumulates latency samples and computes percentile statistics.

    Thread-safe for concurrent recording from multiple writer threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: list[float] = []
        self._busy_count = 0
        self._total_ops = 0
        self._errors = 0

    def record(self, latency_ms: float, was_busy: bool = False) -> None:
        """Record a single operation's latency."""
        with self._lock:
            self._samples.append(latency_ms)
            self._total_ops += 1
            if was_busy:
                self._busy_count += 1

    def record_error(self) -> None:
        """Record a failed operation."""
        with self._lock:
            self._errors += 1
            self._total_ops += 1

    def snapshot_and_reset(self) -> dict:
        """Take a snapshot of current metrics and reset counters.

        Returns a dict with: count, p50, p95, p99, min, max, mean,
        busy_count, busy_pct, errors.
        """
        with self._lock:
            samples = self._samples
            busy = self._busy_count
            total = self._total_ops
            errors = self._errors
            self._samples = []
            self._busy_count = 0
            self._total_ops = 0
            self._errors = 0

        if not samples:
            return {
                "count": 0,
                "ops_per_sec": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "min_ms": 0.0,
                "max_ms": 0.0,
                "mean_ms": 0.0,
                "busy_count": busy,
                "busy_pct": 0.0,
                "errors": errors,
            }

        samples.sort()
        count = len(samples)

        def percentile(p: float) -> float:
            idx = int(count * p / 100)
            idx = min(idx, count - 1)
            return samples[idx]

        return {
            "count": total,
            "ops_per_sec": 0.0,  # Filled in by caller with time context
            "p50_ms": percentile(50),
            "p95_ms": percentile(95),
            "p99_ms": percentile(99),
            "min_ms": samples[0],
            "max_ms": samples[-1],
            "mean_ms": statistics.mean(samples),
            "busy_count": busy,
            "busy_pct": (busy / total * 100) if total > 0 else 0.0,
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# Partition Monitor
# ---------------------------------------------------------------------------


class PartitionMonitor:
    """Monitors a single SQLite database partition for contention metrics.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file to monitor.
    name:
        Human-readable name for this partition.
    """

    def __init__(self, db_path: str, name: str) -> None:
        self.db_path = db_path
        self.name = name
        self.tracker = LatencyTracker()
        self._last_snapshot_time = time.time()

    def get_wal_size(self) -> int:
        """Return the WAL file size in bytes."""
        wal_path = self.db_path + "-wal"
        try:
            return os.path.getsize(wal_path)
        except OSError:
            return 0

    def get_db_size(self) -> int:
        """Return the main database file size in bytes."""
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0

    def get_table_counts(self) -> dict[str, int]:
        """Return row counts for all tables in this partition."""
        counts = {}
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            for t in tables:
                tname = t["name"]
                row = conn.execute(f"SELECT COUNT(*) as cnt FROM [{tname}]").fetchone()
                counts[tname] = row["cnt"] if row else 0
            conn.close()
        except Exception as e:
            logger.warning("Error counting tables in %s: %s", self.name, e)
        return counts

    def probe_write_latency(self) -> float:
        """Perform a probe write and return latency in ms.

        Creates a temporary table if needed, inserts a row, then deletes it.
        Measures the full write + commit cycle.
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _contention_probe "
                "(id INTEGER PRIMARY KEY, ts REAL)"
            )
            conn.commit()

            start = time.perf_counter()
            was_busy = False
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO _contention_probe (ts) VALUES (?)",
                    (time.time(),),
                )
                conn.execute("COMMIT")
            except sqlite3.OperationalError as e:
                if "busy" in str(e).lower() or "locked" in str(e).lower():
                    was_busy = True
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                else:
                    raise
            latency_ms = (time.perf_counter() - start) * 1000

            self.tracker.record(latency_ms, was_busy=was_busy)

            # Cleanup probe rows
            try:
                conn.execute("DELETE FROM _contention_probe")
                conn.commit()
            except Exception:
                pass

            return latency_ms
        except Exception as e:
            self.tracker.record_error()
            logger.warning("Probe write failed for %s: %s", self.name, e)
            return -1.0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def collect_metrics(self) -> dict:
        """Collect a full metrics snapshot for this partition."""
        now = time.time()
        elapsed = now - self._last_snapshot_time
        self._last_snapshot_time = now

        snapshot = self.tracker.snapshot_and_reset()
        if elapsed > 0 and snapshot["count"] > 0:
            snapshot["ops_per_sec"] = snapshot["count"] / elapsed

        return {
            "partition": self.name,
            "db_path": self.db_path,
            "db_size_bytes": self.get_db_size(),
            "wal_size_bytes": self.get_wal_size(),
            "wal_size_mb": self.get_wal_size() / (1024 * 1024),
            "table_counts": self.get_table_counts(),
            "latency": snapshot,
            "timestamp": now,
        }


# ---------------------------------------------------------------------------
# Alert Checker
# ---------------------------------------------------------------------------


class AlertChecker:
    """Checks metrics against thresholds and generates alerts.

    Parameters
    ----------
    thresholds:
        Dict of threshold values. See DEFAULT_THRESHOLDS for keys.
    """

    def __init__(self, thresholds: Optional[dict] = None) -> None:
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)
        self._alert_history: list[dict] = []

    def check(self, metrics: dict) -> list[dict]:
        """Check metrics against thresholds. Returns list of alert dicts."""
        alerts = []
        latency = metrics.get("latency", {})
        partition = metrics.get("partition", "unknown")

        # Check ops/sec (only if we have data)
        ops = latency.get("ops_per_sec", 0)
        if ops > 0 and ops < self.thresholds["ops_per_sec_min"]:
            alerts.append({
                "level": "warning",
                "partition": partition,
                "metric": "ops_per_sec",
                "value": ops,
                "threshold": self.thresholds["ops_per_sec_min"],
                "message": f"{partition}: ops/sec ({ops:.1f}) below minimum ({self.thresholds['ops_per_sec_min']})",
            })

        # Check p99 latency
        p99 = latency.get("p99_ms", 0)
        if p99 > self.thresholds["p99_latency_ms_max"]:
            alerts.append({
                "level": "warning",
                "partition": partition,
                "metric": "p99_latency_ms",
                "value": p99,
                "threshold": self.thresholds["p99_latency_ms_max"],
                "message": f"{partition}: p99 latency ({p99:.1f}ms) exceeds maximum ({self.thresholds['p99_latency_ms_max']}ms)",
            })

        # Check SQLITE_BUSY retry percentage
        busy_pct = latency.get("busy_pct", 0)
        if busy_pct > self.thresholds["busy_retry_pct_max"]:
            alerts.append({
                "level": "critical",
                "partition": partition,
                "metric": "busy_retry_pct",
                "value": busy_pct,
                "threshold": self.thresholds["busy_retry_pct_max"],
                "message": f"{partition}: SQLITE_BUSY rate ({busy_pct:.1f}%) exceeds maximum ({self.thresholds['busy_retry_pct_max']}%)",
            })

        # Check WAL size
        wal_mb = metrics.get("wal_size_mb", 0)
        if wal_mb > self.thresholds["wal_size_mb_max"]:
            alerts.append({
                "level": "warning",
                "partition": partition,
                "metric": "wal_size_mb",
                "value": wal_mb,
                "threshold": self.thresholds["wal_size_mb_max"],
                "message": f"{partition}: WAL size ({wal_mb:.1f}MB) exceeds maximum ({self.thresholds['wal_size_mb_max']}MB)",
            })

        self._alert_history.extend(alerts)
        return alerts

    @property
    def history(self) -> list[dict]:
        return list(self._alert_history)


# ---------------------------------------------------------------------------
# JSONL Logger
# ---------------------------------------------------------------------------


class JSONLLogger:
    """Appends metrics and alerts to a JSONL log file.

    Parameters
    ----------
    log_path:
        Path to the JSONL output file.
    """

    def __init__(self, log_path: str) -> None:
        self.log_path = log_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    def log_metrics(self, metrics: dict) -> None:
        """Append a metrics record to the log."""
        record = {
            "type": "metrics",
            "ts": time.time(),
            **metrics,
        }
        self._write(record)

    def log_alert(self, alert: dict) -> None:
        """Append an alert record to the log."""
        record = {
            "type": "alert",
            "ts": time.time(),
            **alert,
        }
        self._write(record)

    def _write(self, record: dict) -> None:
        with self._lock:
            try:
                with open(self.log_path, "a") as f:
                    f.write(json.dumps(record, separators=(",", ":")) + "\n")
            except OSError as e:
                logger.warning("Failed to write JSONL log: %s", e)


# ---------------------------------------------------------------------------
# Contention Monitor (main class)
# ---------------------------------------------------------------------------


class ContentionMonitor:
    """Monitors all partitioned databases for contention.

    Can run as a background thread or perform one-shot diagnostics.

    Parameters
    ----------
    db_dir:
        Directory containing partition database files.
    log_path:
        Path for the JSONL log file. Defaults to db_dir/contention.jsonl.
    thresholds:
        Custom alert thresholds (see DEFAULT_THRESHOLDS).
    partitions:
        List of partition names to monitor. Defaults to all 4.
    """

    def __init__(
        self,
        db_dir: str,
        log_path: Optional[str] = None,
        thresholds: Optional[dict] = None,
        partitions: Optional[list[str]] = None,
    ) -> None:
        self.db_dir = db_dir
        partition_names = partitions or ["hub", "queue", "comms", "findings"]

        self._monitors: dict[str, PartitionMonitor] = {}
        for name in partition_names:
            db_path = os.path.join(db_dir, f"{name}.db")
            if os.path.exists(db_path):
                self._monitors[name] = PartitionMonitor(db_path, name)

        if log_path is None:
            log_path = os.path.join(db_dir, "contention.jsonl")
        self._logger = JSONLLogger(log_path)
        self._alert_checker = AlertChecker(thresholds)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def collect_all_metrics(self) -> dict[str, dict]:
        """Collect metrics from all monitored partitions."""
        results = {}
        for name, monitor in self._monitors.items():
            results[name] = monitor.collect_metrics()
        return results

    def probe_all(self) -> dict[str, float]:
        """Run probe writes on all partitions. Returns {name: latency_ms}."""
        results = {}
        for name, monitor in self._monitors.items():
            results[name] = monitor.probe_write_latency()
        return results

    def run_diagnostic(self) -> dict:
        """One-shot diagnostic: probe + collect + check alerts."""
        # Run probes
        probe_results = self.probe_all()

        # Collect metrics
        all_metrics = self.collect_all_metrics()

        # Check alerts
        all_alerts = []
        for name, metrics in all_metrics.items():
            alerts = self._alert_checker.check(metrics)
            all_alerts.extend(alerts)
            self._logger.log_metrics(metrics)
            for alert in alerts:
                self._logger.log_alert(alert)

        return {
            "timestamp": time.time(),
            "partitions": all_metrics,
            "probe_latencies_ms": probe_results,
            "alerts": all_alerts,
            "thresholds": self._alert_checker.thresholds,
        }

    def start_background(self, interval_seconds: float = 5.0) -> None:
        """Start monitoring in a background daemon thread.

        Parameters
        ----------
        interval_seconds:
            How often to collect metrics and check alerts.
        """
        if self._running:
            logger.warning("Background monitor already running")
            return

        self._running = True

        def monitor_loop():
            logger.info("Contention monitor started (interval=%.1fs)", interval_seconds)
            while self._running:
                try:
                    self.probe_all()
                    all_metrics = self.collect_all_metrics()
                    for name, metrics in all_metrics.items():
                        alerts = self._alert_checker.check(metrics)
                        self._logger.log_metrics(metrics)
                        for alert in alerts:
                            self._logger.log_alert(alert)
                            if alert["level"] == "critical":
                                logger.warning("ALERT: %s", alert["message"])
                except Exception:
                    logger.exception("Error in contention monitor loop")
                time.sleep(interval_seconds)
            logger.info("Contention monitor stopped")

        self._thread = threading.Thread(
            target=monitor_loop, daemon=True, name="contention-monitor",
        )
        self._thread.start()

    def stop_background(self) -> None:
        """Stop the background monitoring thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    @property
    def alerts(self) -> list[dict]:
        """All alerts generated since creation."""
        return self._alert_checker.history


# ---------------------------------------------------------------------------
# Self-test: Concurrent writer simulation
# ---------------------------------------------------------------------------


def run_writer_stress_test(
    num_writers: int = 4,
    duration_seconds: float = 5.0,
    db_dir: Optional[str] = None,
) -> dict:
    """Simulate N concurrent writers against partitioned databases.

    Creates a temporary PartitionedStore, spawns N writer threads, and
    measures contention metrics.

    Parameters
    ----------
    num_writers:
        Number of concurrent writer threads.
    duration_seconds:
        How long to run the stress test.
    db_dir:
        Directory for test databases. Uses tempdir if None.

    Returns
    -------
    dict
        Test results with per-partition metrics and overall stats.
    """
    import tempfile
    import shutil
    from . import db_partition

    cleanup = False
    if db_dir is None:
        db_dir = tempfile.mkdtemp(prefix="contention_test_")
        cleanup = True

    try:
        store = db_partition.PartitionedStore(db_dir=db_dir)
        monitor = ContentionMonitor(db_dir)

        # Writer function: each writer does mixed operations
        errors = defaultdict(int)
        ops_count = defaultdict(int)
        busy_count = defaultdict(int)
        latencies: dict[str, list[float]] = defaultdict(list)
        stop_event = threading.Event()

        def writer_thread(writer_id: int):
            team = f"team-{writer_id}"
            agent = f"agent-{writer_id}"
            op_num = 0
            while not stop_event.is_set():
                op_num += 1
                partition = random.choice(["hub", "queue", "comms", "findings"])
                start = time.perf_counter()
                was_busy = False
                try:
                    if partition == "hub":
                        store.register_agent(
                            f"{agent}-{op_num}", team, "worker"
                        )
                    elif partition == "queue":
                        store.enqueue_work(
                            team, f"Task-{op_num}", "description", priority=random.randint(1, 10)
                        )
                    elif partition == "comms":
                        store.set_presence(team, "available")
                    elif partition == "findings":
                        store.scratchpad_write(
                            f"key-{writer_id}-{op_num}",
                            {"data": op_num},
                            "test",
                            agent,
                            ttl=60,
                        )
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "busy" in msg or "locked" in msg:
                        was_busy = True
                        busy_count[partition] += 1
                    else:
                        errors[partition] += 1
                except Exception:
                    errors[partition] += 1

                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies[partition].append(elapsed_ms)
                ops_count[partition] += 1

                # Record in monitor
                if partition in monitor._monitors:
                    monitor._monitors[partition].tracker.record(
                        elapsed_ms, was_busy=was_busy
                    )

        # Start writers
        threads = []
        start_time = time.time()
        for i in range(num_writers):
            t = threading.Thread(target=writer_thread, args=(i,), daemon=True)
            t.start()
            threads.append(t)

        # Let them run
        time.sleep(duration_seconds)
        stop_event.set()
        for t in threads:
            t.join(timeout=5)

        elapsed = time.time() - start_time

        # Collect final metrics
        all_metrics = monitor.collect_all_metrics()

        # Compute per-partition stats
        partition_stats = {}
        for pname in ["hub", "queue", "comms", "findings"]:
            lats = latencies.get(pname, [])
            if lats:
                lats.sort()
                count = len(lats)
                partition_stats[pname] = {
                    "ops_total": ops_count.get(pname, 0),
                    "ops_per_sec": ops_count.get(pname, 0) / elapsed,
                    "p50_ms": lats[int(count * 0.5)],
                    "p95_ms": lats[int(count * 0.95)],
                    "p99_ms": lats[min(int(count * 0.99), count - 1)],
                    "min_ms": lats[0],
                    "max_ms": lats[-1],
                    "mean_ms": statistics.mean(lats),
                    "busy_count": busy_count.get(pname, 0),
                    "busy_pct": (busy_count.get(pname, 0) / ops_count.get(pname, 1)) * 100,
                    "errors": errors.get(pname, 0),
                }
            else:
                partition_stats[pname] = {"ops_total": 0}

        total_ops = sum(ops_count.values())
        total_busy = sum(busy_count.values())
        total_errors = sum(errors.values())

        store.close()

        result = {
            "test_config": {
                "num_writers": num_writers,
                "duration_seconds": duration_seconds,
                "actual_duration": elapsed,
            },
            "overall": {
                "total_ops": total_ops,
                "total_ops_per_sec": total_ops / elapsed,
                "total_busy_retries": total_busy,
                "total_busy_pct": (total_busy / total_ops * 100) if total_ops > 0 else 0,
                "total_errors": total_errors,
            },
            "per_partition": partition_stats,
            "contention_monitor_metrics": all_metrics,
        }

        return result

    finally:
        if cleanup:
            shutil.rmtree(db_dir, ignore_errors=True)


def _format_test_results(results: dict) -> str:
    """Format stress test results as a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("CONTENTION STRESS TEST RESULTS")
    lines.append("=" * 70)

    cfg = results["test_config"]
    lines.append(f"Writers: {cfg['num_writers']}")
    lines.append(f"Duration: {cfg['actual_duration']:.1f}s")
    lines.append("")

    overall = results["overall"]
    lines.append(f"Total ops:        {overall['total_ops']:,}")
    lines.append(f"Total ops/sec:    {overall['total_ops_per_sec']:,.1f}")
    lines.append(f"BUSY retries:     {overall['total_busy_retries']} ({overall['total_busy_pct']:.1f}%)")
    lines.append(f"Errors:           {overall['total_errors']}")
    lines.append("")

    lines.append(f"{'Partition':<12} {'Ops':>8} {'Ops/s':>10} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8} {'Busy%':>8}")
    lines.append("-" * 70)
    for pname in ["hub", "queue", "comms", "findings"]:
        stats = results["per_partition"].get(pname, {})
        if stats.get("ops_total", 0) == 0:
            lines.append(f"{pname:<12} {'(no ops)':>8}")
            continue
        lines.append(
            f"{pname:<12} {stats['ops_total']:>8,} {stats['ops_per_sec']:>10.1f} "
            f"{stats['p50_ms']:>8.2f} {stats['p95_ms']:>8.2f} {stats['p99_ms']:>8.2f} "
            f"{stats['busy_pct']:>7.1f}%"
        )
    lines.append("=" * 70)

    # SOP-034 compliance check
    lines.append("")
    lines.append("SOP-034 COMPLIANCE CHECK:")
    max_writers = cfg["num_writers"]
    # With 4 partitions, effective writers per partition is roughly N/4
    eff_writers = max(1, max_writers // 4)
    if eff_writers <= 4:
        lines.append(f"  PASS: ~{eff_writers} effective writers per partition (<= 4 limit)")
    else:
        lines.append(f"  WARNING: ~{eff_writers} effective writers per partition (> 4 limit)")
        lines.append("  Consider further partitioning or reducing concurrent writers")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="SQLite Contention Monitor")
    parser.add_argument("--db-dir", default=None,
                        help="Directory containing partition .db files")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Monitoring interval in seconds (background mode)")
    parser.add_argument("--one-shot", action="store_true",
                        help="Run a one-shot diagnostic and exit")
    parser.add_argument("--self-test", action="store_true",
                        help="Run concurrent writer stress test")
    parser.add_argument("--writers", type=int, default=8,
                        help="Number of concurrent writers for self-test")
    parser.add_argument("--duration", type=float, default=5.0,
                        help="Duration in seconds for self-test")
    parser.add_argument("--log-path", default=None,
                        help="Path for JSONL log output")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON instead of human-readable")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.self_test:
        print(f"Running stress test: {args.writers} writers, {args.duration}s duration...")
        results = run_writer_stress_test(
            num_writers=args.writers,
            duration_seconds=args.duration,
            db_dir=args.db_dir,
        )
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(_format_test_results(results))
        return

    if args.db_dir is None:
        # Default to the coordination db directory
        args.db_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "db"
        )

    if not os.path.isdir(args.db_dir):
        print(f"Error: DB directory not found: {args.db_dir}", file=sys.stderr)
        sys.exit(1)

    monitor = ContentionMonitor(
        db_dir=args.db_dir,
        log_path=args.log_path,
    )

    if args.one_shot:
        diagnostic = monitor.run_diagnostic()
        if args.json:
            print(json.dumps(diagnostic, indent=2, default=str))
        else:
            print("=== One-Shot Contention Diagnostic ===")
            for name, metrics in diagnostic["partitions"].items():
                lat = metrics["latency"]
                print(f"\n{name}:")
                print(f"  DB size:     {metrics['db_size_bytes'] / 1024:.1f} KB")
                print(f"  WAL size:    {metrics['wal_size_mb']:.2f} MB")
                print(f"  Operations:  {lat['count']}")
                print(f"  p50/p95/p99: {lat['p50_ms']:.2f} / {lat['p95_ms']:.2f} / {lat['p99_ms']:.2f} ms")
                print(f"  BUSY rate:   {lat['busy_pct']:.1f}%")
            if diagnostic["alerts"]:
                print("\n--- ALERTS ---")
                for alert in diagnostic["alerts"]:
                    print(f"  [{alert['level']}] {alert['message']}")
            else:
                print("\nNo alerts triggered.")
        return

    # Background monitoring mode
    print(f"Starting background monitor (interval={args.interval}s)...")
    print(f"Logging to: {args.log_path or os.path.join(args.db_dir, 'contention.jsonl')}")
    print("Press Ctrl+C to stop.\n")

    monitor.start_background(interval_seconds=args.interval)

    try:
        while True:
            time.sleep(args.interval)
            # Print latest metrics summary
            all_metrics = monitor.collect_all_metrics()
            for name, metrics in all_metrics.items():
                lat = metrics["latency"]
                if lat["count"] > 0:
                    print(
                        f"[{name}] ops={lat['count']:>4} "
                        f"p50={lat['p50_ms']:.1f}ms "
                        f"p99={lat['p99_ms']:.1f}ms "
                        f"busy={lat['busy_pct']:.0f}% "
                        f"WAL={metrics['wal_size_mb']:.1f}MB"
                    )
    except KeyboardInterrupt:
        print("\nStopping monitor...")
        monitor.stop_background()
        print("Done.")


if __name__ == "__main__":
    main()
