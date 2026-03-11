#!/usr/bin/env python3
"""Live data gathering test for Prototype B (Named pipes + mmap).

Simulates 3 agents (1 coordinator + 2 workers) communicating via
Prototype B's FIFO bus and mmap shared state. Workers fetch real data
from OpenAlex and HN Algolia APIs.

Data Team B — 2026-03-11
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.request
import urllib.error

from agent_comm_b.bus import PipeBusWriter, PipeBusReader
from agent_comm_b.state import SharedStateMap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-20s] %(levelname)s %(message)s",
)
log = logging.getLogger("live_test_proto_b")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEAM = "data-team-b"
GATHER_DURATION = 30        # seconds of data gathering
HEARTBEAT_INTERVAL = 3      # seconds between heartbeats
FETCH_INTERVAL = 5          # seconds between API fetches per worker
VALIDATION_DURATION = 5     # seconds for validation phase

WORKER1_URL = "https://api.openalex.org/works?search=multi+agent+systems&per_page=5"
WORKER2_URL = "https://hn.algolia.com/api/v1/search?query=distributed+systems&hitsPerPage=5"

# Channels — using coordinator-relay pattern (coordinator reads shared channels)
CH_GLOBAL = "global"
CH_HEARTBEAT = "heartbeat"
CH_RESULTS = "topic-data-results"

# ---------------------------------------------------------------------------
# Thread-safe metrics collector
# ---------------------------------------------------------------------------

class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.messages_sent = 0
        self.messages_received = 0
        self.heartbeats_sent = 0
        self.heartbeats_received = 0
        self.data_points_collected = 0
        self.api_calls = 0
        self.api_responses: list[dict] = []
        self.errors: list[str] = []
        self.mmap_operations = 0
        self.fifo_writes = 0
        self.fifo_reads = 0
        self.spill_writes = 0
        self.phase_transitions: list[dict] = []
        self.rate_limit_checks = 0
        self.rate_limit_denials = 0

    def inc(self, attr: str, n: int = 1):
        with self._lock:
            setattr(self, attr, getattr(self, attr) + n)

    def append_response(self, resp: dict):
        with self._lock:
            self.api_responses.append(resp)

    def append_error(self, err: str):
        with self._lock:
            self.errors.append(err)

    def append_phase(self, phase: dict):
        with self._lock:
            self.phase_transitions.append(phase)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "messages_sent": self.messages_sent,
                "messages_received": self.messages_received,
                "heartbeats_sent": self.heartbeats_sent,
                "heartbeats_received": self.heartbeats_received,
                "data_points_collected": self.data_points_collected,
                "api_calls": self.api_calls,
                "api_responses": list(self.api_responses),
                "errors": list(self.errors),
                "mmap_operations": self.mmap_operations,
                "fifo_writes": self.fifo_writes,
                "fifo_reads": self.fifo_reads,
                "spill_writes": self.spill_writes,
                "phase_transitions": list(self.phase_transitions),
                "rate_limit_checks": self.rate_limit_checks,
                "rate_limit_denials": self.rate_limit_denials,
            }


# ---------------------------------------------------------------------------
# Helper: HTTP fetch with timeout
# ---------------------------------------------------------------------------

def http_get_json(url: str, timeout: float = 10.0) -> tuple[int, dict | list | None]:
    """Fetch a URL and return (status_code, parsed_json)."""
    req = urllib.request.Request(url, headers={"User-Agent": "DataTeamB-ProtoTest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, None


# ---------------------------------------------------------------------------
# Wrapped bus writer that tracks metrics
# ---------------------------------------------------------------------------

class TrackedWriter(PipeBusWriter):
    """PipeBusWriter that counts FIFO vs spill writes."""

    def __init__(self, pipes_dir: str, agent_id: str, team: str, metrics: Metrics):
        super().__init__(pipes_dir, agent_id, team)
        self._metrics = metrics

    def publish(self, channel, msg_type, body, ttl=300, in_reply_to=None):
        msg = super().publish(channel, msg_type, body, ttl, in_reply_to)
        self._metrics.inc("messages_sent")
        self._metrics.inc("fifo_writes")
        # Check if a spill file was created/updated for this channel
        spill = self._spill_path(channel)
        if os.path.exists(spill):
            self._metrics.inc("spill_writes")
        return msg


# ---------------------------------------------------------------------------
# Agent threads
# ---------------------------------------------------------------------------

def coordinator_thread(
    pipes_dir: str,
    shm: SharedStateMap,
    metrics: Metrics,
    stop_event: threading.Event,
):
    """Coordinator agent: registers agents, monitors heartbeats, manages phases."""
    agent_id = "coordinator-b"
    writer = TrackedWriter(pipes_dir, agent_id, TEAM, metrics)

    # Register coordinator in mmap shared state
    shm.register_agent(agent_id, TEAM, "coordinator", os.getpid())
    metrics.inc("mmap_operations")
    log.info("[coordinator] Registered in mmap shared state")

    # Configure rate limits in mmap
    shm.configure_rate_limit("openalex", 10, 1)
    shm.configure_rate_limit("hackernews", 30, 60)
    metrics.inc("mmap_operations", 2)

    # Set up readers for channels the coordinator monitors
    # Reader must be opened BEFORE writers send (Proto B requirement)
    hb_reader = PipeBusReader(pipes_dir, CH_HEARTBEAT)
    hb_reader.open()
    results_reader = PipeBusReader(pipes_dir, CH_RESULTS)
    results_reader.open()
    global_reader = PipeBusReader(pipes_dir, CH_GLOBAL)
    global_reader.open()

    # Phase management
    phase = "init"
    shm.set_phase(phase)
    metrics.inc("mmap_operations")
    metrics.append_phase({"phase": phase, "ts": time.time()})

    # Broadcast init phase signal
    writer.publish(CH_GLOBAL, "phase-signal", {"phase": "init", "msg_type_hint": "phase-signal"}, ttl=30)

    # Wait briefly for workers to register
    time.sleep(1.5)

    # Transition to gathering phase
    phase = "gathering"
    shm.set_phase(phase)
    metrics.inc("mmap_operations")
    metrics.append_phase({"phase": phase, "ts": time.time()})
    writer.publish(CH_GLOBAL, "phase-signal", {"phase": "gathering", "msg_type_hint": "phase-signal"}, ttl=30)
    log.info("[coordinator] Phase -> gathering")

    gather_start = time.time()
    last_heartbeat = time.time()
    last_health_check = time.time()

    while not stop_event.is_set():
        now = time.time()

        # Send coordinator heartbeat
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            shm.heartbeat(agent_id)
            metrics.inc("mmap_operations")
            metrics.inc("heartbeats_sent")
            writer.publish(CH_HEARTBEAT, "heartbeat", {
                "status": "active",
                "role": "coordinator",
                "uptime_sec": round(now - gather_start, 1),
            }, ttl=30)
            last_heartbeat = now

        # Poll heartbeat channel
        hb_msgs = hb_reader.poll()
        if hb_msgs:
            metrics.inc("fifo_reads", len(hb_msgs))
            metrics.inc("messages_received", len(hb_msgs))
            metrics.inc("heartbeats_received", len(hb_msgs))

        # Poll results channel
        result_msgs = results_reader.poll()
        if result_msgs:
            metrics.inc("fifo_reads", len(result_msgs))
            metrics.inc("messages_received", len(result_msgs))
            for msg in result_msgs:
                dp = msg.body.get("data_points", 0)
                if dp:
                    log.info("[coordinator] Received %d data points from %s", dp, msg.agent_id)

        # Poll global channel (for any worker announcements)
        global_msgs = global_reader.poll()
        if global_msgs:
            metrics.inc("fifo_reads", len(global_msgs))
            metrics.inc("messages_received", len(global_msgs))

        # Health check via mmap
        if now - last_health_check >= 5.0:
            agents = shm.get_all_agents()
            metrics.inc("mmap_operations")
            dead = shm.get_dead_agents(timeout=15.0)
            metrics.inc("mmap_operations")
            if dead:
                for d in dead:
                    log.warning("[coordinator] Dead agent detected: %s", d.agent_id)
            last_health_check = now

        # Phase transition: gathering -> validation after GATHER_DURATION
        if phase == "gathering" and (now - gather_start) >= GATHER_DURATION:
            phase = "validation"
            shm.set_phase(phase)
            metrics.inc("mmap_operations")
            metrics.append_phase({"phase": phase, "ts": now})
            writer.publish(CH_GLOBAL, "phase-signal", {"phase": "validation", "msg_type_hint": "phase-signal"}, ttl=30)
            log.info("[coordinator] Phase -> validation")

        # Phase transition: validation -> done after VALIDATION_DURATION
        if phase == "validation" and (now - gather_start) >= (GATHER_DURATION + VALIDATION_DURATION):
            phase = "done"
            shm.set_phase(phase)
            metrics.inc("mmap_operations")
            metrics.append_phase({"phase": phase, "ts": now})
            writer.publish(CH_GLOBAL, "phase-signal", {"phase": "done", "msg_type_hint": "phase-signal"}, ttl=30)
            log.info("[coordinator] Phase -> done")
            stop_event.set()
            break

        time.sleep(0.2)

    # Final drain of channels
    for reader in (hb_reader, results_reader, global_reader):
        remaining = reader.poll()
        if remaining:
            metrics.inc("fifo_reads", len(remaining))
            metrics.inc("messages_received", len(remaining))

    # Cleanup
    shm.unregister_agent(agent_id)
    metrics.inc("mmap_operations")
    hb_reader.close()
    results_reader.close()
    global_reader.close()
    log.info("[coordinator] Shutdown complete")


def worker_thread(
    agent_id: str,
    api_name: str,
    url: str,
    pipes_dir: str,
    shm: SharedStateMap,
    metrics: Metrics,
    stop_event: threading.Event,
):
    """Worker agent: fetches data from an API and publishes results on the bus."""
    writer = TrackedWriter(pipes_dir, agent_id, TEAM, metrics)

    # Register in mmap
    shm.register_agent(agent_id, TEAM, "worker", os.getpid())
    metrics.inc("mmap_operations")
    log.info("[%s] Registered in mmap shared state", agent_id)

    last_heartbeat = time.time()
    last_fetch = 0.0  # fetch immediately on start
    fetch_count = 0
    start_time = time.time()

    while not stop_event.is_set():
        now = time.time()

        # Check current phase from mmap
        current_phase = shm.get_phase()
        metrics.inc("mmap_operations")

        # Send heartbeat
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            shm.heartbeat(agent_id)
            metrics.inc("mmap_operations")
            metrics.inc("heartbeats_sent")
            writer.publish(CH_HEARTBEAT, "heartbeat", {
                "status": "active",
                "role": "worker",
                "agent_id": agent_id,
                "api": api_name,
                "fetch_count": fetch_count,
                "uptime_sec": round(now - start_time, 1),
            }, ttl=30)
            last_heartbeat = now

        # Only fetch during gathering phase
        if current_phase == "gathering" and (now - last_fetch) >= FETCH_INTERVAL:
            # Rate limit check via mmap
            allowed = shm.reserve_api_call(api_name, agent_id)
            metrics.inc("mmap_operations")
            metrics.inc("rate_limit_checks")
            if not allowed:
                metrics.inc("rate_limit_denials")
                log.info("[%s] Rate limited for %s, waiting", agent_id, api_name)
                time.sleep(0.5)
                continue

            # Make the actual HTTP request
            metrics.inc("api_calls")
            fetch_start = time.time()
            status, data = http_get_json(url)
            fetch_ms = round((time.time() - fetch_start) * 1000)

            if status == 200 and data is not None:
                # Count data points
                if api_name == "openalex":
                    records = data.get("results", [])
                elif api_name == "hackernews":
                    records = data.get("hits", [])
                else:
                    records = []

                num_records = len(records)
                fetch_count += 1
                metrics.inc("data_points_collected", num_records)

                # Publish result on the bus
                # Keep body small (< 4096 bytes) — only summary, not full data
                result_body = {
                    "msg_type_hint": "data-result",
                    "request_id": f"dreq-{agent_id}-{fetch_count:03d}",
                    "source": api_name,
                    "status": "complete",
                    "data_points": num_records,
                    "summary": f"{num_records} records from {api_name}",
                    "duration_ms": fetch_ms,
                    "api_calls_made": 1,
                    "errors": [],
                }
                writer.publish(CH_RESULTS, "response", result_body, ttl=300)

                metrics.append_response({
                    "agent": agent_id,
                    "url": url,
                    "status": status,
                    "records": num_records,
                    "duration_ms": fetch_ms,
                    "ts": int(time.time() * 1000),
                })

                log.info("[%s] Fetched %d records from %s (%d ms)", agent_id, num_records, api_name, fetch_ms)
            else:
                err = f"[{agent_id}] HTTP {status} from {api_name}"
                metrics.append_error(err)
                log.warning(err)

                # Publish error result
                writer.publish(CH_RESULTS, "response", {
                    "msg_type_hint": "data-result",
                    "request_id": f"dreq-{agent_id}-{fetch_count:03d}",
                    "source": api_name,
                    "status": "failed",
                    "data_points": 0,
                    "errors": [err],
                }, ttl=300)

            last_fetch = time.time()

        # During validation phase, just keep sending heartbeats
        if current_phase == "done":
            break

        time.sleep(0.2)

    # Unregister from mmap
    shm.unregister_agent(agent_id)
    metrics.inc("mmap_operations")
    log.info("[%s] Shutdown complete", agent_id)


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_test() -> dict:
    """Run the full Prototype B live test and return results dict."""
    # Create temp directory for communication infrastructure
    # CommDir rejects /tmp, so we use a subdirectory that won't conflict
    tmpdir = tempfile.mkdtemp(prefix="proto_b_test_")
    pipes_dir = os.path.join(tmpdir, "pipes")
    shm_dir = os.path.join(tmpdir, "shm")
    os.makedirs(pipes_dir, exist_ok=True)
    os.makedirs(shm_dir, exist_ok=True)

    log.info("Test infrastructure at: %s", tmpdir)
    log.info("  pipes_dir: %s", pipes_dir)
    log.info("  shm_dir:   %s", shm_dir)

    # Initialize mmap shared state
    shm = SharedStateMap(shm_dir)
    log.info("SharedStateMap initialized (file size: %d bytes)", os.path.getsize(os.path.join(shm_dir, "state.mmap")))

    metrics = Metrics()
    stop_event = threading.Event()

    test_start = time.time()

    # Start coordinator first (it opens readers — Proto B requirement)
    coord = threading.Thread(
        target=coordinator_thread,
        args=(pipes_dir, shm, metrics, stop_event),
        name="coordinator",
        daemon=True,
    )
    coord.start()

    # Brief delay to let coordinator open its FIFO readers
    time.sleep(0.5)

    # Start workers
    w1 = threading.Thread(
        target=worker_thread,
        args=("worker-1-openalex", "openalex", WORKER1_URL, pipes_dir, shm, metrics, stop_event),
        name="worker-1",
        daemon=True,
    )
    w2 = threading.Thread(
        target=worker_thread,
        args=("worker-2-hn", "hackernews", WORKER2_URL, pipes_dir, shm, metrics, stop_event),
        name="worker-2",
        daemon=True,
    )

    w1.start()
    w2.start()

    # Wait for test to complete (stop_event set by coordinator)
    # Add a safety timeout of GATHER_DURATION + VALIDATION_DURATION + 30s
    max_wait = GATHER_DURATION + VALIDATION_DURATION + 30
    coord.join(timeout=max_wait)
    w1.join(timeout=5)
    w2.join(timeout=5)

    test_end = time.time()
    duration_ms = round((test_end - test_start) * 1000)

    # Calculate FIFO throughput
    snap = metrics.snapshot()
    total_fifo_ops = snap["fifo_writes"] + snap["fifo_reads"]
    duration_s = duration_ms / 1000.0
    fifo_throughput = round(total_fifo_ops / duration_s, 2) if duration_s > 0 else 0

    # Calculate total bus bytes (check pipe and spill files)
    bus_bytes = 0
    for f in os.listdir(pipes_dir):
        fpath = os.path.join(pipes_dir, f)
        if os.path.isfile(fpath) and not f.endswith(".fifo"):
            bus_bytes += os.path.getsize(fpath)

    # Build results in the same schema as baseline
    results = {
        "prototype": "B",
        "test_id": "LIVE-TEST-PROTO-B-001",
        "duration_ms": duration_ms,
        "agents": 3,
        "data_points_collected": snap["data_points_collected"],
        "messages_sent": snap["messages_sent"],
        "messages_received": snap["messages_received"],
        "heartbeats_sent": snap["heartbeats_sent"],
        "heartbeats_received": snap["heartbeats_received"],
        "rate_limit_checks": snap["rate_limit_checks"],
        "rate_limit_denials": snap["rate_limit_denials"],
        "bus_file_bytes": bus_bytes,
        "file_io_ops": total_fifo_ops,
        "api_calls": snap["api_calls"],
        "errors": snap["errors"],
        "api_responses": snap["api_responses"],
        "tmpdir": tmpdir,
        # Proto B specific metrics
        "proto_b_metrics": {
            "fifo_writes": snap["fifo_writes"],
            "fifo_reads": snap["fifo_reads"],
            "spill_writes": snap["spill_writes"],
            "mmap_operations": snap["mmap_operations"],
            "fifo_throughput_ops_per_sec": fifo_throughput,
            "phase_transitions": snap["phase_transitions"],
        },
    }

    # Close shared state
    shm.close()

    log.info("=" * 60)
    log.info("TEST COMPLETE")
    log.info("  Duration: %d ms", duration_ms)
    log.info("  Data points collected: %d", snap["data_points_collected"])
    log.info("  Messages sent: %d", snap["messages_sent"])
    log.info("  Messages received: %d", snap["messages_received"])
    log.info("  Heartbeats sent: %d", snap["heartbeats_sent"])
    log.info("  API calls: %d", snap["api_calls"])
    log.info("  FIFO writes: %d, reads: %d", snap["fifo_writes"], snap["fifo_reads"])
    log.info("  mmap operations: %d", snap["mmap_operations"])
    log.info("  FIFO throughput: %.2f ops/sec", fifo_throughput)
    log.info("  Errors: %d", len(snap["errors"]))
    log.info("=" * 60)

    return results


def main():
    results = run_test()

    # Write results JSON
    results_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "live_test_proto_b_results.json",
    )
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    log.info("Results written to: %s", results_path)


if __name__ == "__main__":
    main()
