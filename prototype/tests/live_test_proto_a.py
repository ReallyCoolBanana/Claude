#!/usr/bin/env python3
"""
Live Data Gathering Test — Prototype A (JSONL + SQLite WAL)

Simulates 3 agents (1 coordinator + 2 workers) communicating via the
Prototype A bus and shared state.  Workers make real HTTP requests to
free public APIs (OpenAlex, Hacker News Algolia).  The coordinator
manages phases, heartbeats, and rate limits.

Run:
    PYTHONPATH=prototype python prototype/tests/live_test_proto_a.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

from agent_comm.bus import BusWriter, BusReader
from agent_comm.state import SharedState

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEAM = "data-team-a"
GATHER_DURATION = 30          # seconds of active gathering
HEARTBEAT_INTERVAL = 5        # seconds between heartbeats
POLL_INTERVAL = 0.5           # seconds between bus polls
WORKER_FETCH_INTERVAL = 4     # seconds between API fetches per worker

RATE_LIMITS = {
    "openalex":   {"max_calls": 8, "window_seconds": 10},
    "hackernews": {"max_calls": 8, "window_seconds": 10},
}

OPENALEX_URL = (
    "https://api.openalex.org/works?search=multi+agent+systems&per_page=5"
)
HN_URL = (
    "https://hn.algolia.com/api/v1/search?query=distributed+systems&hitsPerPage=5"
)

# ---------------------------------------------------------------------------
# Metrics (thread-safe counters)
# ---------------------------------------------------------------------------

class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.messages_sent = 0
        self.messages_received = 0
        self.heartbeats_sent = 0
        self.data_points_collected = 0
        self.rate_limit_checks = 0
        self.rate_limit_denials = 0
        self.errors: list[str] = []
        self.api_responses: list[dict] = []

    def inc(self, attr, n=1):
        with self._lock:
            setattr(self, attr, getattr(self, attr) + n)

    def add_error(self, err: str):
        with self._lock:
            self.errors.append(err)

    def add_api_response(self, resp: dict):
        with self._lock:
            self.api_responses.append(resp)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "messages_sent": self.messages_sent,
                "messages_received": self.messages_received,
                "heartbeats_sent": self.heartbeats_sent,
                "data_points_collected": self.data_points_collected,
                "rate_limit_checks": self.rate_limit_checks,
                "rate_limit_denials": self.rate_limit_denials,
                "errors": list(self.errors),
                "api_responses": list(self.api_responses),
            }

metrics = Metrics()

# ---------------------------------------------------------------------------
# Shared stop event
# ---------------------------------------------------------------------------

stop_event = threading.Event()

# ---------------------------------------------------------------------------
# Helper: HTTP GET with timeout
# ---------------------------------------------------------------------------

def http_get_json(url: str, timeout: float = 15.0) -> dict:
    """Fetch JSON from url. Returns parsed dict or raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "ProtoA-LiveTest/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ---------------------------------------------------------------------------
# Coordinator agent
# ---------------------------------------------------------------------------

def run_coordinator(bus_dir: str, db_path: str):
    agent_id = "coord-1"
    writer = BusWriter(bus_dir, agent_id, TEAM)
    state = SharedState(db_path)

    # Register self
    state.register_agent(agent_id, TEAM, "coordinator", os.getpid())

    # Configure rate limits
    for endpoint, cfg in RATE_LIMITS.items():
        state.configure_rate_limit(endpoint, cfg["max_calls"], cfg["window_seconds"])

    # Set up bus readers
    reader_global = BusReader(bus_dir, "global")
    reader_heartbeat = BusReader(bus_dir, "heartbeat")
    reader_results = BusReader(bus_dir, "topic-data-results")

    # Signal: init phase
    writer.publish("global", "phase-signal", {"phase": "init", "action": "start"})
    metrics.inc("messages_sent")
    state.signal_phase("init", "start", agent_id)

    print(f"[COORD] Registered. Waiting for workers...")

    # Wait for both workers to register (up to 10s)
    deadline = time.time() + 10
    while time.time() < deadline:
        rows = state._conn.execute(
            "SELECT agent_id FROM agents WHERE status='alive'"
        ).fetchall()
        alive_ids = {r[0] for r in rows}
        if {"worker-1", "worker-2"}.issubset(alive_ids):
            break
        time.sleep(0.3)
    else:
        metrics.add_error("Timeout waiting for workers to register")

    # Phase 1: assign work
    writer.publish("global", "phase-signal", {"phase": "phase_1", "action": "assign"})
    metrics.inc("messages_sent")
    state.signal_phase("phase_1", "start", agent_id)
    print(f"[COORD] Phase 1 — assignments sent")

    # Phase 2: gathering
    writer.publish("global", "phase-signal", {"phase": "phase_2", "action": "gather"})
    metrics.inc("messages_sent")
    state.signal_phase("phase_2", "start", agent_id)
    print(f"[COORD] Phase 2 — gathering started (duration={GATHER_DURATION}s)")

    gather_start = time.time()
    dead_checks = 0

    while not stop_event.is_set() and (time.time() - gather_start) < GATHER_DURATION:
        # Heartbeat
        state.heartbeat(agent_id)
        writer.publish("heartbeat", "heartbeat", {
            "status": "active",
            "role": "coordinator",
            "uptime_sec": round(time.time() - gather_start, 1),
        }, ttl=30)
        metrics.inc("messages_sent")
        metrics.inc("heartbeats_sent")

        # Poll bus channels
        for reader in (reader_global, reader_heartbeat, reader_results):
            msgs = reader.poll()
            metrics.inc("messages_received", len(msgs))

        # Check for dead agents
        dead = state.get_dead_agents(timeout=20)
        dead_checks += 1
        if dead:
            for d in dead:
                msg = f"Dead agent detected: {d['agent_id']}"
                print(f"[COORD] WARNING: {msg}")
                metrics.add_error(msg)

        time.sleep(HEARTBEAT_INTERVAL)

    # Phase 3: validation
    writer.publish("global", "phase-signal", {"phase": "phase_3", "action": "validate"})
    metrics.inc("messages_sent")
    state.signal_phase("phase_3", "start", agent_id)
    print(f"[COORD] Phase 3 — validation")

    # Drain remaining result messages
    final_msgs = reader_results.poll()
    metrics.inc("messages_received", len(final_msgs))

    # Phase 4: done
    writer.publish("global", "phase-signal", {"phase": "phase_4", "action": "complete"})
    metrics.inc("messages_sent")
    state.signal_phase("phase_4", "complete", agent_id)
    print(f"[COORD] Phase 4 — complete. Dead-agent checks performed: {dead_checks}")

    # Signal workers to stop
    stop_event.set()
    state.close()


# ---------------------------------------------------------------------------
# Worker agent
# ---------------------------------------------------------------------------

def run_worker(bus_dir: str, db_path: str, agent_id: str, api_name: str, api_url: str):
    writer = BusWriter(bus_dir, agent_id, TEAM)
    state = SharedState(db_path)

    # Register
    state.register_agent(agent_id, TEAM, "worker", os.getpid())
    print(f"[{agent_id.upper()}] Registered. Source: {api_name}")

    reader_global = BusReader(bus_dir, "global")

    # Wait for phase_2 signal (up to 15s)
    deadline = time.time() + 15
    while time.time() < deadline and not stop_event.is_set():
        msgs = reader_global.poll()
        metrics.inc("messages_received", len(msgs))
        for m in msgs:
            if m.type == "phase-signal" and m.body.get("phase") == "phase_2":
                break
        else:
            time.sleep(0.3)
            continue
        break

    print(f"[{agent_id.upper()}] Starting data gathering from {api_name}")

    fetch_count = 0
    gather_start = time.time()

    while not stop_event.is_set():
        elapsed = time.time() - gather_start
        if elapsed > GATHER_DURATION + 5:
            break

        # Heartbeat
        state.heartbeat(agent_id)
        writer.publish("heartbeat", "heartbeat", {
            "status": "active",
            "agent_id": agent_id,
            "api": api_name,
            "fetches": fetch_count,
            "uptime_sec": round(elapsed, 1),
        }, ttl=30)
        metrics.inc("messages_sent")
        metrics.inc("heartbeats_sent")

        # Rate limit check
        metrics.inc("rate_limit_checks")
        allowed = state.reserve_api_call(api_name, agent_id)
        if not allowed:
            metrics.inc("rate_limit_denials")
            writer.publish("global", "info", {
                "msg_type_hint": "rate-limit-alert",
                "api": api_name,
                "agent_id": agent_id,
                "severity": "warning",
            }, ttl=60)
            metrics.inc("messages_sent")
            time.sleep(1)
            continue

        # Fetch data
        t0 = time.time()
        try:
            data = http_get_json(api_url)
            duration_ms = round((time.time() - t0) * 1000)
            fetch_count += 1

            # Count data points
            if api_name == "openalex":
                results = data.get("results", [])
                dp_count = len(results)
                titles = [r.get("title", "?")[:60] for r in results[:3]]
            elif api_name == "hackernews":
                results = data.get("hits", [])
                dp_count = len(results)
                titles = [r.get("title", "?")[:60] for r in results[:3]]
            else:
                dp_count = 0
                titles = []

            metrics.inc("data_points_collected", dp_count)
            metrics.add_api_response({
                "api": api_name,
                "agent": agent_id,
                "status": "ok",
                "data_points": dp_count,
                "duration_ms": duration_ms,
                "sample_titles": titles,
            })

            # Publish result on bus
            writer.publish("topic-data-results", "response", {
                "msg_type_hint": "data-result",
                "request_id": f"fetch-{agent_id}-{fetch_count}",
                "source": api_name,
                "status": "complete",
                "data_points": dp_count,
                "duration_ms": duration_ms,
            })
            metrics.inc("messages_sent")

            # Progress update
            writer.publish("global", "info", {
                "msg_type_hint": "progress-update",
                "agent_id": agent_id,
                "fetches_done": fetch_count,
                "data_points_so_far": dp_count,
            })
            metrics.inc("messages_sent")

            print(f"  [{agent_id.upper()}] Fetch #{fetch_count}: {dp_count} points in {duration_ms}ms")

        except Exception as e:
            duration_ms = round((time.time() - t0) * 1000)
            err_msg = f"{agent_id} fetch error ({api_name}): {e}"
            metrics.add_error(err_msg)
            metrics.add_api_response({
                "api": api_name,
                "agent": agent_id,
                "status": "error",
                "error": str(e),
                "duration_ms": duration_ms,
            })
            writer.publish("global", "info", {
                "msg_type_hint": "error",
                "agent_id": agent_id,
                "error": str(e)[:200],
            })
            metrics.inc("messages_sent")
            print(f"  [{agent_id.upper()}] ERROR: {e}")

        time.sleep(WORKER_FETCH_INTERVAL)

    print(f"[{agent_id.upper()}] Done. Total fetches: {fetch_count}")
    state.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Use a non-/tmp directory to satisfy CommDir validation
    base = os.path.join(os.path.expanduser("~"), ".proto-a-live-test")
    os.makedirs(base, exist_ok=True)
    test_dir = tempfile.mkdtemp(prefix="live_test_", dir=base)

    bus_dir = os.path.join(test_dir, "bus")
    os.makedirs(bus_dir, exist_ok=True)
    db_path = os.path.join(test_dir, "state.db")

    print("=" * 65)
    print("  Prototype A — Live Data Gathering Test")
    print(f"  Dir: {test_dir}")
    print("=" * 65)

    t_start = time.time()

    # Start workers first (they wait for phase_2 signal)
    w1 = threading.Thread(
        target=run_worker,
        args=(bus_dir, db_path, "worker-1", "openalex", OPENALEX_URL),
        daemon=True,
    )
    w2 = threading.Thread(
        target=run_worker,
        args=(bus_dir, db_path, "worker-2", "hackernews", HN_URL),
        daemon=True,
    )

    w1.start()
    w2.start()
    time.sleep(0.5)  # let workers register before coordinator checks

    # Run coordinator in main-ish thread
    coord = threading.Thread(
        target=run_coordinator,
        args=(bus_dir, db_path),
        daemon=True,
    )
    coord.start()

    # Wait for completion
    coord.join(timeout=GATHER_DURATION + 30)
    stop_event.set()
    w1.join(timeout=5)
    w2.join(timeout=5)

    duration_ms = round((time.time() - t_start) * 1000)

    # Measure bus file sizes
    bus_bytes = 0
    for fname in os.listdir(bus_dir):
        fpath = os.path.join(bus_dir, fname)
        if os.path.isfile(fpath):
            bus_bytes += os.path.getsize(fpath)

    # Build results
    snap = metrics.snapshot()
    results = {
        "prototype": "A",
        "test_id": "LIVE-TEST-PROTO-A-001",
        "duration_ms": duration_ms,
        "agents": 3,
        "data_points_collected": snap["data_points_collected"],
        "messages_sent": snap["messages_sent"],
        "messages_received": snap["messages_received"],
        "heartbeats_sent": snap["heartbeats_sent"],
        "rate_limit_checks": snap["rate_limit_checks"],
        "rate_limit_denials": snap["rate_limit_denials"],
        "bus_file_bytes": bus_bytes,
        "errors": snap["errors"],
        "api_responses": snap["api_responses"],
    }

    # Write results
    results_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "live_test_proto_a_results.json",
    )
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    print()
    print("=" * 65)
    print("  RESULTS SUMMARY")
    print("=" * 65)
    print(f"  Duration:            {duration_ms} ms")
    print(f"  Data points:         {results['data_points_collected']}")
    print(f"  Messages sent:       {results['messages_sent']}")
    print(f"  Messages received:   {results['messages_received']}")
    print(f"  Heartbeats sent:     {results['heartbeats_sent']}")
    print(f"  Rate limit checks:   {results['rate_limit_checks']}")
    print(f"  Rate limit denials:  {results['rate_limit_denials']}")
    print(f"  Bus file bytes:      {results['bus_file_bytes']}")
    print(f"  Errors:              {len(results['errors'])}")
    print(f"  API responses:       {len(results['api_responses'])}")
    print(f"  Results written to:  {results_path}")
    print("=" * 65)

    return 0 if not results["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
