#!/usr/bin/env python3
"""
Baseline live data gathering test — Data Team C.

Simulates 3 agents (1 lead + 2 workers) using threads with NO communication
bus, NO shared state. Workers write results to individual JSONL files; the
lead polls those files to aggregate results. This establishes the baseline
for comparison with Proto A and Proto B.

APIs used (same as Teams A and B):
  - OpenAlex: multi agent systems
  - HN Algolia: distributed systems
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GATHER_DURATION_S = 30
POLL_INTERVAL_S = 1.0
WORKER_INTERVAL_S = 5.0  # delay between API calls per worker
MAX_RETRIES = 3
RETRY_DELAY_S = 2.0

OPENALEX_URL = "https://api.openalex.org/works?search=multi+agent+systems&per_page=5"
HN_URL = "https://hn.algolia.com/api/v1/search?query=distributed+systems&hitsPerPage=5"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts_ms():
    return int(time.time() * 1000)


def _fetch_json(url: str, retries: int = MAX_RETRIES) -> dict | None:
    """Fetch JSON from *url* with simple retry logic."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DataTeamC-Baseline/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
            if attempt < retries:
                time.sleep(RETRY_DELAY_S)
            else:
                return None


# ---------------------------------------------------------------------------
# Shared metrics (thread-safe via lock)
# ---------------------------------------------------------------------------
metrics_lock = threading.Lock()
metrics = {
    "file_io_ops": 0,
    "errors": [],
    "api_calls": 0,
    "api_responses": [],
}


def _inc(key, n=1):
    with metrics_lock:
        metrics[key] += n


def _append(key, val):
    with metrics_lock:
        metrics[key].append(val)


# ---------------------------------------------------------------------------
# Worker functions
# ---------------------------------------------------------------------------
def worker_openalex(outfile: str, stop_event: threading.Event):
    """Worker 1 — queries OpenAlex and writes JSONL to *outfile*."""
    while not stop_event.is_set():
        _inc("api_calls")
        data = _fetch_json(OPENALEX_URL)
        if data is None:
            _append("errors", {"agent": "worker-1-openalex", "error": "fetch failed", "ts": _ts_ms()})
            stop_event.wait(WORKER_INTERVAL_S)
            continue

        results = data.get("results", [])
        records = []
        for r in results:
            records.append({
                "source": "openalex",
                "id": r.get("id"),
                "title": r.get("display_name") or r.get("title"),
                "year": r.get("publication_year"),
                "cited_by": r.get("cited_by_count"),
                "ts": _ts_ms(),
            })

        _append("api_responses", {
            "agent": "worker-1-openalex",
            "url": OPENALEX_URL,
            "status": 200,
            "records": len(records),
            "ts": _ts_ms(),
        })

        # Write to output file (append JSONL)
        with open(outfile, "a") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        _inc("file_io_ops")

        stop_event.wait(WORKER_INTERVAL_S)


def worker_hn(outfile: str, stop_event: threading.Event):
    """Worker 2 — queries HN Algolia and writes JSONL to *outfile*."""
    while not stop_event.is_set():
        _inc("api_calls")
        data = _fetch_json(HN_URL)
        if data is None:
            _append("errors", {"agent": "worker-2-hn", "error": "fetch failed", "ts": _ts_ms()})
            stop_event.wait(WORKER_INTERVAL_S)
            continue

        hits = data.get("hits", [])
        records = []
        for h in hits:
            records.append({
                "source": "hn-algolia",
                "id": h.get("objectID"),
                "title": h.get("title"),
                "author": h.get("author"),
                "points": h.get("points"),
                "ts": _ts_ms(),
            })

        _append("api_responses", {
            "agent": "worker-2-hn",
            "url": HN_URL,
            "status": 200,
            "records": len(records),
            "ts": _ts_ms(),
        })

        with open(outfile, "a") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        _inc("file_io_ops")

        stop_event.wait(WORKER_INTERVAL_S)


def lead_poller(worker_files: list[str], stop_event: threading.Event):
    """Lead agent — polls worker output files and counts lines."""
    while not stop_event.is_set():
        for wf in worker_files:
            if os.path.exists(wf):
                _inc("file_io_ops")
        stop_event.wait(POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# Main test driver
# ---------------------------------------------------------------------------
def run_test() -> dict:
    tmpdir = tempfile.mkdtemp(prefix="baseline_test_")
    w1_file = os.path.join(tmpdir, "worker1_openalex.jsonl")
    w2_file = os.path.join(tmpdir, "worker2_hn.jsonl")
    worker_files = [w1_file, w2_file]

    # Touch files so lead can poll immediately
    for f in worker_files:
        open(f, "w").close()

    stop = threading.Event()
    t_start = _ts_ms()

    threads = [
        threading.Thread(target=worker_openalex, args=(w1_file, stop), name="worker-1-openalex"),
        threading.Thread(target=worker_hn, args=(w2_file, stop), name="worker-2-hn"),
        threading.Thread(target=lead_poller, args=(worker_files, stop), name="lead-poller"),
    ]
    for t in threads:
        t.daemon = True
        t.start()

    print(f"[baseline] started 3 agents, gathering for {GATHER_DURATION_S}s …")
    time.sleep(GATHER_DURATION_S)
    stop.set()

    for t in threads:
        t.join(timeout=5)

    t_end = _ts_ms()
    duration_ms = t_end - t_start

    # Aggregate results from worker files
    all_records = []
    for wf in worker_files:
        if os.path.exists(wf):
            with open(wf) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            all_records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            _inc("file_io_ops")

    data_points = len(all_records)

    result = {
        "prototype": "baseline",
        "test_id": "LIVE-TEST-BASELINE-001",
        "duration_ms": duration_ms,
        "agents": 3,
        "data_points_collected": data_points,
        "messages_sent": 0,
        "messages_received": 0,
        "heartbeats_sent": 0,
        "rate_limit_checks": 0,
        "rate_limit_denials": 0,
        "bus_file_bytes": 0,
        "file_io_ops": metrics["file_io_ops"],
        "api_calls": metrics["api_calls"],
        "errors": metrics["errors"],
        "api_responses": metrics["api_responses"],
        "tmpdir": tmpdir,
    }

    return result


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  BASELINE LIVE DATA GATHERING TEST  (Data Team C)")
    print("=" * 60)
    result = run_test()

    print(f"\n[baseline] done — {result['data_points_collected']} data points "
          f"in {result['duration_ms']} ms, {result['api_calls']} API calls, "
          f"{len(result['errors'])} errors")

    # Write results to canonical location
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "live_test_baseline_results.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[baseline] results written to {out_path}")
