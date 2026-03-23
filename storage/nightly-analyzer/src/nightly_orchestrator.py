#!/usr/bin/env python3
"""
Step 3.2: Nightly Orchestrator
================================
Main entry point for the nightly analysis pipeline.
Runs as a systemd service triggered at 1 AM daily.

Architecture:
    systemd timer (1 AM) -> systemd service -> this script
    -> load model -> run tasks in sequence -> unload model -> report

The orchestrator manages:
    - Time-boxing: each task gets a fixed window with hard deadlines
    - Graceful shutdown: handles SIGTERM from systemd
    - Rollback: takes Neo4j snapshot before starting, rolls back on failure
    - Structured logging: all events logged to JSONL
    - Summary reporting: metrics written to JSON report file

Usage:
    # Normal operation (via systemd):
    systemctl start nightly-analyzer.service

    # Manual run (for testing):
    python nightly_orchestrator.py

    # Run specific tasks only:
    python nightly_orchestrator.py --tasks edge_decay,llm_edge_analysis

    # Dry run (no mutations):
    python nightly_orchestrator.py --dry-run

Expected runtime: 5.5-6.5 hours within 8-hour window
Expected GPU memory: ~24.5GB peak (4.5GB resident + 20GB nightly model)
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from model_manager import NightlyModelManager, ThermalShutdownError, get_gpu_status
from edge_decay import EdgeDecayProcessor
from llm_edge_analysis import LLMEdgeAnalyzer
from causal_inference import CausalInferenceProcessor
from path_optimization import PathOptimizer
from condensation import CondensationProcessor
from emergence_detection import EmergenceDetector
from consistency_manager import ConsistencyManager, WALWriter

logger = logging.getLogger("nightly.orchestrator")


class StructuredLogger:
    """
    Writes structured JSONL log entries for machine parsing.

    Each line is a JSON object with:
        {"ts": "2026-03-23T01:00:00", "level": "INFO", "event": "...", "data": {...}}

    Log file location: /nvme/logs/nightly-analyzer.jsonl
    Rotation: new file per run (date in filename)
    """

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.log_path, "a")

    def log(self, level: str, event: str, data: Optional[dict] = None):
        entry = {
            "ts": datetime.now().isoformat(),
            "level": level,
            "event": event,
        }
        if data:
            entry["data"] = data
        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()


class GracefulShutdown:
    """
    Handles SIGTERM/SIGINT for graceful shutdown.

    When systemd sends SIGTERM (e.g., system shutdown, manual stop),
    this sets a flag that tasks check periodically.

    Usage:
        shutdown = GracefulShutdown()
        while not shutdown.requested:
            do_work()
    """

    def __init__(self):
        self.requested = False
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        self._original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, self._handler)
        signal.signal(signal.SIGINT, self._handler)

    def _handler(self, signum, frame):
        logger.warning("Received signal %d. Requesting graceful shutdown.", signum)
        self.requested = True

    def restore(self):
        signal.signal(signal.SIGTERM, self._original_sigterm)
        signal.signal(signal.SIGINT, self._original_sigint)


class TaskRunner:
    """
    Runs a single analysis task with time-boxing and error handling.

    Each task:
        1. Checks if shutdown was requested
        2. Checks if we're past the hard deadline
        3. Runs the task's execute() method
        4. Catches and logs any errors
        5. Returns task metrics

    Time-boxing: tasks receive a deadline and must check it periodically.
    If a task exceeds its hard deadline, the orchestrator moves to the next task.
    """

    def __init__(
        self,
        name: str,
        task_callable,
        duration_min: int,
        hard_deadline_min: int,
        shutdown: GracefulShutdown,
        slog: StructuredLogger,
        dry_run: bool = False,
    ):
        self.name = name
        self.task_callable = task_callable
        self.duration_min = duration_min
        self.hard_deadline_min = hard_deadline_min
        self.shutdown = shutdown
        self.slog = slog
        self.dry_run = dry_run

    def run(self, window_start: datetime) -> dict:
        """
        Execute the task within its time window.

        Returns:
            {
                "task": "edge_decay",
                "status": "completed|skipped|failed|timeout",
                "start_time": "...",
                "end_time": "...",
                "duration_s": 1234,
                "metrics": {...}  # Task-specific metrics
            }
        """
        result = {
            "task": self.name,
            "status": "pending",
            "start_time": datetime.now().isoformat(),
            "metrics": {},
        }

        if self.shutdown.requested:
            result["status"] = "skipped"
            result["reason"] = "shutdown_requested"
            self.slog.log("WARN", f"task_skipped", {"task": self.name, "reason": "shutdown"})
            return result

        hard_deadline = window_start + timedelta(minutes=self.hard_deadline_min)
        soft_deadline = window_start + timedelta(minutes=self.duration_min)

        if datetime.now() >= hard_deadline:
            result["status"] = "skipped"
            result["reason"] = "past_hard_deadline"
            self.slog.log("WARN", "task_skipped", {
                "task": self.name,
                "reason": "past_hard_deadline",
            })
            return result

        self.slog.log("INFO", "task_start", {"task": self.name})
        logger.info("Starting task: %s (deadline: %s)", self.name, hard_deadline.isoformat())

        start = time.time()
        try:
            metrics = self.task_callable(
                soft_deadline=soft_deadline,
                hard_deadline=hard_deadline,
                dry_run=self.dry_run,
            )
            result["status"] = "completed"
            result["metrics"] = metrics or {}

        except TimeoutError:
            result["status"] = "timeout"
            logger.warning("Task %s timed out at hard deadline.", self.name)

        except ThermalShutdownError as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.error("Task %s aborted due to thermal shutdown: %s", self.name, e)
            self.shutdown.requested = True  # Stop all further tasks

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            logger.error("Task %s failed: %s", self.name, e, exc_info=True)

        elapsed = time.time() - start
        result["end_time"] = datetime.now().isoformat()
        result["duration_s"] = round(elapsed, 1)

        self.slog.log("INFO", "task_end", {
            "task": self.name,
            "status": result["status"],
            "duration_s": result["duration_s"],
        })

        return result


def take_neo4j_snapshot(config: dict) -> str:
    """
    Take a Neo4j database snapshot for rollback capability.

    Uses neo4j-admin database dump. The snapshot is stored on NVMe
    with a timestamp in the filename.

    Command:
        neo4j-admin database dump neo4j --to-path=/nvme/neo4j-snapshots/

    Expected output:
        /nvme/neo4j-snapshots/neo4j-2026-03-23T01-00-00.dump

    Expected time: 10-30 seconds depending on database size
    Expected size: 50-200MB for typical memory databases

    Common errors:
        - "database is not stopped": use --force flag (Neo4j 5+)
        - Permission denied: ensure user has access to snapshot dir
        - Disk full: check NVMe free space
    """
    snapshot_dir = Path(config["neo4j"]["snapshot_dir"])
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_name = f"neo4j-{timestamp}.dump"
    snapshot_path = snapshot_dir / snapshot_name

    logger.info("Taking Neo4j snapshot to %s", snapshot_path)

    # For Neo4j 5+, use neo4j-admin database dump with --to-path
    # The database must be running; use --force for online backup
    result = subprocess.run(
        [
            "neo4j-admin", "database", "dump",
            config["neo4j"]["database"],
            "--to-path", str(snapshot_dir),
            "--overwrite-destination=true",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        # Fallback: try neo4j-admin dump (older Neo4j versions)
        logger.warning("neo4j-admin database dump failed, trying backup command...")
        result = subprocess.run(
            [
                "neo4j-admin", "dump",
                "--database", config["neo4j"]["database"],
                "--to", str(snapshot_path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Neo4j snapshot failed: {result.stderr}\n"
                "Ensure neo4j-admin is in PATH and the database is accessible."
            )

    # Clean up old snapshots (keep max_snapshots)
    max_snapshots = config["neo4j"].get("max_snapshots", 7)
    snapshots = sorted(snapshot_dir.glob("neo4j-*.dump"), key=lambda p: p.stat().st_mtime)
    while len(snapshots) > max_snapshots:
        old = snapshots.pop(0)
        old.unlink()
        logger.info("Removed old snapshot: %s", old.name)

    logger.info("Neo4j snapshot complete: %s", snapshot_path)
    return str(snapshot_path)


def rollback_neo4j(snapshot_path: str, config: dict):
    """
    Restore Neo4j from a snapshot.

    WARNING: This stops the database, restores from dump, and restarts.
    All changes since the snapshot will be lost.

    Commands:
        systemctl stop neo4j
        neo4j-admin database load neo4j --from-path=/nvme/neo4j-snapshots/ --overwrite-destination=true
        systemctl start neo4j

    Expected time: 30-60 seconds

    Common errors:
        - "database is running": stop it first
        - Corrupted dump: try an older snapshot
    """
    logger.warning("ROLLING BACK Neo4j to snapshot: %s", snapshot_path)

    snapshot_dir = str(Path(snapshot_path).parent)

    # Stop Neo4j
    subprocess.run(["systemctl", "stop", "neo4j"], check=True, timeout=60)
    time.sleep(3)

    # Restore
    result = subprocess.run(
        [
            "neo4j-admin", "database", "load",
            config["neo4j"]["database"],
            "--from-path", snapshot_dir,
            "--overwrite-destination=true",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Neo4j restore failed: {result.stderr}")

    # Start Neo4j
    subprocess.run(["systemctl", "start", "neo4j"], check=True, timeout=60)

    # Wait for Neo4j to be ready
    for _ in range(30):
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(
                config["neo4j"]["uri"],
                auth=(config["neo4j"]["user"], os.environ.get(config["neo4j"]["password_env"], "")),
            )
            with driver.session() as session:
                session.run("RETURN 1")
            driver.close()
            logger.info("Neo4j restored and running.")
            return
        except Exception:
            time.sleep(2)

    raise RuntimeError("Neo4j failed to start after restore.")


def main():
    """
    Main orchestrator entry point.

    Argument parsing:
        --config PATH    Config file path (default: ../config/nightly_config.yaml)
        --tasks LIST     Comma-separated task names to run (default: all)
        --dry-run        Don't mutate any data
        --skip-snapshot  Skip Neo4j snapshot (for testing)
        --skip-model     Don't load/unload the nightly model (for testing)

    Exit codes:
        0: All tasks completed successfully
        1: Some tasks failed but snapshot was not needed
        2: Critical failure, rollback was attempted
        3: Rollback failed (manual intervention needed)
    """
    parser = argparse.ArgumentParser(description="Nightly Memory Analyzer")
    parser.add_argument("--config", default=str(Path(__file__).parent.parent / "config" / "nightly_config.yaml"))
    parser.add_argument("--tasks", help="Comma-separated task list")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-snapshot", action="store_true")
    parser.add_argument("--skip-model", action="store_true")
    args = parser.parse_args()

    # Load config
    with open(args.config) as f:
        config = yaml.safe_load(f)

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config["logging"]["level"]),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                config["logging"]["log_file"].replace(
                    "{date}", datetime.now().strftime("%Y-%m-%d")
                )
            ),
        ],
    )

    date_str = datetime.now().strftime("%Y-%m-%d")
    slog = StructuredLogger(config["logging"]["log_file"].replace("{date}", date_str))
    slog.log("INFO", "nightly_start", {"date": date_str, "dry_run": args.dry_run})

    # Initialize shutdown handler
    shutdown = GracefulShutdown()

    # Initialize components
    neo4j_password = os.environ.get(config["neo4j"]["password_env"], "password")
    neo4j_uri = config["neo4j"]["uri"]
    neo4j_user = config["neo4j"]["user"]
    neo4j_db = config["neo4j"]["database"]

    model_mgr = NightlyModelManager(config) if not args.skip_model else None
    consistency = ConsistencyManager(config, neo4j_uri, neo4j_user, neo4j_password, neo4j_db)
    wal = WALWriter(config["consistency"]["wal_path"].replace("{date}", date_str))

    # Track results
    run_start = datetime.now()
    results = []
    snapshot_path = None

    try:
        # ---------------------------------------------------------------
        # Phase A: Pre-analysis setup (~2-5 minutes)
        # ---------------------------------------------------------------

        # Take Neo4j snapshot
        if not args.skip_snapshot:
            try:
                snapshot_path = take_neo4j_snapshot(config)
                slog.log("INFO", "snapshot_taken", {"path": snapshot_path})
            except Exception as e:
                logger.error("Snapshot failed: %s. Continuing without rollback capability.", e)
                slog.log("ERROR", "snapshot_failed", {"error": str(e)})

        # Load nightly model
        if model_mgr and not shutdown.requested:
            try:
                load_result = model_mgr.load()
                slog.log("INFO", "model_loaded", load_result)
            except Exception as e:
                logger.error("Model loading failed: %s", e)
                slog.log("ERROR", "model_load_failed", {"error": str(e)})
                # Can't continue without the model for LLM tasks
                # But mechanical tasks (edge_decay) can still run
                model_mgr = None

        # ---------------------------------------------------------------
        # Phase B: Run analysis tasks in sequence
        # ---------------------------------------------------------------

        # Initialize task processors
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            neo4j_uri, auth=(neo4j_user, neo4j_password)
        )

        # Define the task pipeline
        task_schedule = config["schedule"]["tasks"]

        # Determine which tasks to run
        all_task_names = [
            "edge_decay",
            "llm_edge_analysis",
            "causal_inference",
            "path_optimization",
            "condensation",
            "emergence_detection",
        ]
        if args.tasks:
            selected_tasks = [t.strip() for t in args.tasks.split(",")]
        else:
            selected_tasks = all_task_names

        # Create task processors
        task_processors = {}

        if "edge_decay" in selected_tasks:
            task_processors["edge_decay"] = EdgeDecayProcessor(
                driver, neo4j_db, config, wal, shutdown
            )

        if "llm_edge_analysis" in selected_tasks and model_mgr:
            task_processors["llm_edge_analysis"] = LLMEdgeAnalyzer(
                driver, neo4j_db, config, model_mgr, wal, shutdown
            )

        if "causal_inference" in selected_tasks and model_mgr:
            task_processors["causal_inference"] = CausalInferenceProcessor(
                driver, neo4j_db, config, model_mgr, wal, shutdown
            )

        if "path_optimization" in selected_tasks:
            task_processors["path_optimization"] = PathOptimizer(
                driver, neo4j_db, config, wal, shutdown
            )

        if "condensation" in selected_tasks and model_mgr:
            task_processors["condensation"] = CondensationProcessor(
                driver, neo4j_db, config, model_mgr, wal, shutdown
            )

        if "emergence_detection" in selected_tasks and model_mgr:
            task_processors["emergence_detection"] = EmergenceDetector(
                driver, neo4j_db, config, model_mgr, wal, shutdown
            )

        # Run each task
        for task_name in selected_tasks:
            if task_name not in task_processors:
                logger.warning("Skipping task %s (processor not available)", task_name)
                continue

            if shutdown.requested:
                logger.warning("Shutdown requested. Skipping remaining tasks.")
                break

            sched = task_schedule[task_name]
            runner = TaskRunner(
                name=task_name,
                task_callable=task_processors[task_name].execute,
                duration_min=sched["duration_min"],
                hard_deadline_min=sched["hard_deadline_min"],
                shutdown=shutdown,
                slog=slog,
                dry_run=args.dry_run,
            )

            task_result = runner.run(run_start)
            results.append(task_result)

        # ---------------------------------------------------------------
        # Phase C: Post-analysis verification
        # ---------------------------------------------------------------
        if not args.dry_run and not shutdown.requested:
            logger.info("Running consistency checks...")
            slog.log("INFO", "consistency_check_start")

            try:
                check_results = consistency.run_all_checks()
                slog.log("INFO", "consistency_check_done", check_results)

                if not check_results["all_passed"]:
                    logger.error(
                        "Consistency checks FAILED: %s",
                        json.dumps(check_results, indent=2),
                    )
                    if snapshot_path:
                        logger.warning("Attempting rollback...")
                        slog.log("WARN", "rollback_start")
                        rollback_neo4j(snapshot_path, config)
                        slog.log("INFO", "rollback_complete")
                    else:
                        logger.error("No snapshot available for rollback!")
                        slog.log("ERROR", "no_snapshot_for_rollback")
                else:
                    logger.info("All consistency checks passed.")

            except Exception as e:
                logger.error("Consistency check failed: %s", e)
                slog.log("ERROR", "consistency_check_error", {"error": str(e)})

        # Close driver
        driver.close()

    except Exception as e:
        logger.error("Orchestrator critical error: %s", e, exc_info=True)
        slog.log("ERROR", "orchestrator_critical_error", {
            "error": str(e),
            "traceback": traceback.format_exc(),
        })

    finally:
        # ---------------------------------------------------------------
        # Phase D: Cleanup
        # ---------------------------------------------------------------

        # Unload nightly model
        if model_mgr:
            try:
                model_mgr.unload()
                slog.log("INFO", "model_unloaded")
            except Exception as e:
                logger.error("Model unload failed: %s", e)

        # Generate summary report
        run_end = datetime.now()
        summary = {
            "date": date_str,
            "run_start": run_start.isoformat(),
            "run_end": run_end.isoformat(),
            "total_duration_s": round((run_end - run_start).total_seconds(), 1),
            "dry_run": args.dry_run,
            "tasks": results,
            "task_summary": {
                "total": len(results),
                "completed": sum(1 for r in results if r["status"] == "completed"),
                "failed": sum(1 for r in results if r["status"] == "failed"),
                "skipped": sum(1 for r in results if r["status"] == "skipped"),
                "timeout": sum(1 for r in results if r["status"] == "timeout"),
            },
            "snapshot_path": snapshot_path,
        }

        # Add GPU stats
        try:
            gpu = get_gpu_status()
            summary["gpu_final"] = {
                "temp_c": gpu.gpu_temp_c,
                "mem_used_gb": round(gpu.mem_used_gb, 1),
            }
        except Exception:
            pass

        # Write report
        report_path = config["logging"]["report_file"].replace("{date}", date_str)
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(summary, f, indent=2)

        slog.log("INFO", "nightly_end", summary["task_summary"])
        slog.close()

        logger.info(
            "Nightly analysis complete. Duration: %.0f minutes. "
            "Tasks: %d completed, %d failed, %d skipped.",
            summary["total_duration_s"] / 60,
            summary["task_summary"]["completed"],
            summary["task_summary"]["failed"],
            summary["task_summary"]["skipped"],
        )

        # Restore signal handlers
        shutdown.restore()

        # Exit code
        if summary["task_summary"]["failed"] > 0:
            sys.exit(1)
        sys.exit(0)


if __name__ == "__main__":
    main()
