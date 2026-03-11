# Think Tank Gamma: Benchmark Methodology Specification

> **Team:** Think Tank Gamma (Lead + 2 subagents)
> **Date:** 2026-03-11
> **Status:** Complete
> **Inputs:** KB-0018 (prototype test results), KB-0019 (architecture analysis), SOPs-prototype-testing.md, existing benchmark_runner.py
> **Deliverable:** Benchmark specification for comparing Proto A, Proto B, and Baseline during live data gathering tests

---

## 1. Benchmark Scenarios

### S1: Single-Source Baseline

| Parameter | Value |
|-----------|-------|
| Teams | 1 |
| Agents | 3 (1 lead + 2 workers) |
| API Sources | 1 (OpenAlex — fastest, most reliable per iteration 2 benchmarks: 0.5s, 15 pts) |
| Duration | 2 minutes |
| Bus Channels | `global.jsonl`, `team-01.jsonl` |
| Heartbeat Interval | 30s |
| Purpose | Measure base coordination overhead in the simplest useful configuration |

**Execution steps:**
1. Lead agent assigns each worker a non-overlapping query partition (e.g., worker-1: "AI trading", worker-2: "ML portfolio")
2. Workers fetch from OpenAlex, post results to `team-01` channel
3. Lead collects, deduplicates, writes final output
4. Measure: wall-clock time, messages on bus, data points gathered

**Expected agent names:** `data-a-lead`, `data-a-sub-1`, `data-a-sub-2`

---

### S2: Multi-Source Parallel

| Parameter | Value |
|-----------|-------|
| Teams | 3 |
| Agents | 9 (3 leads + 6 workers, 3 per team) |
| API Sources | 3 (OpenAlex, Hacker News, arXiv) |
| Duration | 3 minutes |
| Bus Channels | `global.jsonl`, `team-01.jsonl`, `team-02.jsonl`, `team-03.jsonl`, `topic-blockers.jsonl` |
| Heartbeat Interval | 30s |
| Rate Limit | Shared via SQLite `rate_limits` table: OpenAlex 10 req/min collective, HN 20 req/min, arXiv 5 req/min |
| Purpose | Test cross-team rate limit sharing and parallel coordination |

**Execution steps:**
1. Each team assigned one primary API source
2. All 3 teams run simultaneously, querying on the same topic
3. Rate limits enforced collectively through SQLite (Proto A) / mmap (Proto B) / none (Baseline)
4. Measure: rate limit violations, cross-team message latency, total throughput

**Expected agent names:** `data-{a,b,c}-lead`, `data-{a,b,c}-sub-{1,2}`

---

### S3: Full Coordinator Loop

| Parameter | Value |
|-----------|-------|
| Teams | 1 coordinator + 3 worker teams |
| Agents | 10 (1 coordinator + 3 leads + 6 workers) |
| API Sources | All available (OpenAlex, HN, arXiv, Wikipedia, DuckDuckGo) |
| Duration | 5 minutes |
| Bus Channels | `global.jsonl`, `team-01.jsonl` through `team-03.jsonl`, `topic-blockers.jsonl`, `topic-build.jsonl` |
| Heartbeat Interval | 30s |
| Coordinator Poll Interval | 1s |
| Phase Gating | Phase 1: fetch raw data (3 min). Phase 2: deduplicate + merge (2 min). Coordinator gates phase transition. |
| Purpose | End-to-end validation of the coordinator pattern with heartbeats, phase gating, and dynamic re-tasking |

**Execution steps:**
1. Coordinator initializes coordination directory, spawns 3 teams
2. Phase 1: Teams fetch data from assigned sources. Coordinator monitors heartbeats, rate limits.
3. Coordinator detects Phase 1 completion via `phase-signal` messages from all 3 leads
4. Coordinator broadcasts Phase 2 start on `global`
5. Phase 2: Leads merge and deduplicate. Workers idle (send heartbeats only).
6. Coordinator collects final output, writes summary

**Expected agent names:** `proto-test-coord`, `data-{a,b,c}-lead`, `data-{a,b,c}-sub-{1,2}`

---

### S4: Failure Injection

| Parameter | Value |
|-----------|-------|
| Teams | 1 coordinator + 2 worker teams |
| Agents | 7 initially (1 coordinator + 2 leads + 4 workers); 1 killed at T+60s |
| API Sources | OpenAlex, Hacker News |
| Duration | 3 minutes |
| Kill Target | `data-b-sub-2` (a non-lead worker) at T+60s via SIGKILL |
| Expected Recovery | Coordinator detects missing heartbeat within 120s (2 missed heartbeats). Coordinator sends re-task directive to `data-b-lead` via `team-02` channel. |
| Purpose | Validate dead agent detection, recovery path, and data integrity under failure |

**Execution steps:**
1. Normal operation for 60 seconds
2. At T+60s, the benchmark harness sends SIGKILL to `data-b-sub-2`
3. Coordinator's monitoring loop detects stale heartbeat (last heartbeat > 120s ago)
4. Coordinator publishes `agent-dead` alert to `global`, sends re-task to `team-02`
5. `data-b-lead` redistributes killed agent's remaining work to `data-b-sub-1`
6. Measure: detection latency, recovery latency, data completeness compared to no-failure run

**Metrics specific to S4:**

| Metric | Definition | Target |
|--------|-----------|--------|
| Detection latency | Time from SIGKILL to coordinator's `agent-dead` message | < 150s |
| Recovery latency | Time from `agent-dead` to first data point from redistributed work | < 30s |
| Data completeness | `data_points_with_failure / data_points_without_failure` | > 0.80 |

---

## 2. Metrics Framework

### 2.1 Communication Latency

**Definition:** Time elapsed from when an agent calls `bus.write()` to when the coordinator (or another agent) reads that message via `bus.poll()`.

**Measurement method:**
```
latency_ms = (reader_timestamp - message["timestamp"]) * 1000
```
- `message["timestamp"]` is set by the writer at `time.time()` before the `os.write()` call
- `reader_timestamp` is recorded by the reader immediately after `json.loads()` succeeds
- Clock skew: Not applicable (all agents share a single machine clock)

**Collection:** Every message read by any agent records its latency. Stored in a per-agent latency log: `latencies_{agent_id}.jsonl`.

**Aggregation:** Report p50, p95, p99, mean, max over all messages per run.

---

### 2.2 Coordination Overhead

**Definition:** The additional wall-clock time that prototype communication adds compared to the baseline (no communication layer).

**Formula:**
```
overhead_pct = ((proto_duration - baseline_duration) / baseline_duration) * 100
```

**Measurement method:**
- `baseline_duration`: Wall-clock time for identical scenario with no bus/SQLite, using direct file polling
- `proto_duration`: Wall-clock time for same scenario with prototype active
- Both measured from `time.monotonic()` at scenario start to scenario end

**Pass/fail thresholds (from SOPs section 8.3):**
- PASS: overhead < 10%
- CONDITIONAL: 10-25%
- FAIL: > 25%

---

### 2.3 Data Throughput

**Definition:** Data points successfully gathered per minute per active agent.

**Formula:**
```
throughput = total_data_points / (duration_minutes * active_agent_count)
```

**Measurement method:**
- `total_data_points`: Count of valid, deduplicated results in the final output JSON
- `duration_minutes`: Wall-clock duration of the data gathering phase only (excludes setup/teardown)
- `active_agent_count`: Number of agents that produced at least 1 data point (excludes coordinators and dead agents)

**Normalization:** Per-agent throughput allows comparison across scenarios with different agent counts.

---

### 2.4 Heartbeat Reliability

**Definition:** Ratio of heartbeats received by the coordinator to heartbeats expected.

**Formula:**
```
reliability = heartbeats_received / heartbeats_expected

heartbeats_expected = sum(
    floor(agent_active_duration / heartbeat_interval)
    for agent in all_agents
)
```

**Measurement method:**
- Coordinator counts each heartbeat message received on `global` channel with `type: heartbeat`
- Expected count computed from each agent's registration time to deregistration (or scenario end)
- For S4, the killed agent's expected count stops at kill time

**Target:** > 0.95 (PASS), > 0.85 (CONDITIONAL), < 0.85 (FAIL)

---

### 2.5 Rate Limit Effectiveness

**Definition:** Fraction of API calls that violated the collective rate limit.

**Formula:**
```
violation_rate = over_limit_calls / total_api_calls
effectiveness = 1 - violation_rate
```

**Measurement method:**
- Each agent logs every API call with timestamp and whether `check_rate_limit()` returned True (allowed) or False (denied-but-attempted-anyway)
- "Over-limit call" = a call that was made despite `check_rate_limit()` returning False, OR a call that caused the actual API to return 429/rate-limit error
- For Baseline: no shared rate limiting exists, so we count only actual 429 responses

**Collection:** API call log per agent: `api_calls_{agent_id}.jsonl` with fields `{timestamp, api, allowed, status_code}`.

**Target:** effectiveness > 0.98 (< 2% violations)

---

### 2.6 Memory Footprint

**Definition:** Change in Resident Set Size (RSS) attributable to the coordination layer.

**Formula:**
```
memory_delta_mb = (rss_after - rss_before) / (1024 * 1024)
```

**Measurement method:**
- `rss_before`: Read `/proc/{pid}/statm` field 1 (resident pages) * page_size, captured after agent process starts but before coordination initialization
- `rss_after`: Same reading captured at scenario end, before teardown
- Take the maximum RSS observed during the run as `peak_rss` (sample every 5s via a background thread reading `/proc/{pid}/statm`)

**Per-agent reporting:** Each agent reports its own delta. Aggregate as mean and max across all agents.

**Expected ranges (from KB-0019 section 7.3):** ~5 MB additional per agent for Proto A (SQLite connection + bus reader state). Proto B may be lower due to mmap shared memory.

---

### 2.7 Bus File Growth Rate

**Definition:** Rate at which JSONL bus files grow on disk, measured in bytes per minute.

**Formula:**
```
growth_rate_bytes_per_min = (file_size_end - file_size_start) / duration_minutes
```

**Measurement method:**
- Sample `os.path.getsize()` on each `.jsonl` file every 10 seconds
- Report per-channel and aggregate (all channels summed) growth rate
- For Proto B: measure FIFO spillover file sizes instead

**Projected bounds (from KB-0019):** For 10 agents at 30s heartbeat interval with ~100 messages/agent/operation: ~200 KB total. Growth should be well under 1 MB/min.

**Alert threshold:** Warn if any single channel exceeds 10 MB (per KB-0019 section 2.5).

---

## 3. Statistical Methodology

### 3.1 Number of Runs

**Minimum: 5 scored runs per scenario per prototype (plus 1 warmup = 6 total).**

Rationale: With N=5 scored runs, a two-sample t-test has 80% power to detect a 1.5-sigma effect size at alpha=0.05. Three runs (the SOP minimum) provides only ~55% power, which risks missing real differences. Five runs balances statistical rigor against time cost.

| Scenario | Duration | Runs (warmup+scored) | Total time per proto | Total time (3 protos) |
|----------|----------|---------------------|---------------------|-----------------------|
| S1 | 2 min | 1+5 = 6 | 12 min | 36 min |
| S2 | 3 min | 1+5 = 6 | 18 min | 54 min |
| S3 | 5 min | 1+5 = 6 | 30 min | 90 min |
| S4 | 3 min | 1+5 = 6 | 18 min | 54 min |
| **Total** | | | | **234 min (~4 hours)** |

If time is constrained, reduce to the SOP minimum of 3 scored runs (4 total including warmup), but flag results as "low-confidence" in the report.

### 3.2 Warmup Handling

**Discard the first run of each (scenario, prototype) combination.**

Rationale:
1. **Filesystem cache:** The first run populates the page cache for Python modules, SQLite, and JSONL files. Subsequent runs benefit from cached reads.
2. **JIT-like effects:** Python's import machinery, regex compilation, and SQLite query plan caching all have first-run overhead.
3. **Connection establishment:** First API calls to external services (OpenAlex, HN) may be slower due to TCP/TLS handshake and DNS resolution.

The warmup run is still recorded (for debugging) but excluded from all statistical calculations. It is tagged with `"warmup": true` in the results JSON.

### 3.3 Confidence Intervals

Compute 95% confidence intervals using the t-distribution (appropriate for small samples).

**Formula:**
```
CI_95 = mean +/- t(0.025, df=n-1) * (stddev / sqrt(n))
```

Where:
- `n` = number of scored runs (5 by default)
- `t(0.025, df=4)` = 2.776 (for n=5)
- `t(0.025, df=2)` = 4.303 (for n=3, if using minimum runs)

**Implementation:**
```python
from math import sqrt
from scipy.stats import t as t_dist  # or use a lookup table to avoid scipy dep

def confidence_interval(values, confidence=0.95):
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    stderr = sqrt(variance / n)
    alpha = 1 - confidence
    t_crit = t_dist.ppf(1 - alpha / 2, df=n - 1)
    margin = t_crit * stderr
    return mean, mean - margin, mean + margin
```

**stdlib-only fallback** (no scipy dependency, per KB-0019 section 7.1):

Use a hardcoded lookup table for t-critical values:
```python
T_CRITICAL_95 = {
    2: 4.303,   # df=2, n=3
    3: 3.182,   # df=3, n=4
    4: 2.776,   # df=4, n=5
    5: 2.571,   # df=5, n=6
    9: 2.262,   # df=9, n=10
    29: 2.045,  # df=29, n=30
}
```

### 3.4 Determining Statistical Significance

**Test:** Welch's two-sample t-test (does not assume equal variances).

**Comparison pairs:**
- Proto A vs Baseline
- Proto B vs Baseline
- Proto A vs Proto B

**Significance threshold:** p < 0.05 (two-tailed).

**Effect size:** Report Cohen's d alongside the p-value:
```
d = (mean_A - mean_B) / pooled_stddev
pooled_stddev = sqrt((var_A + var_B) / 2)
```

**Interpretation guide:**

| Cohen's d | Interpretation | Action |
|-----------|---------------|--------|
| < 0.2 | Negligible difference | Treat as equivalent |
| 0.2 - 0.5 | Small difference | Note but do not weight heavily |
| 0.5 - 0.8 | Medium difference | Consider in recommendation |
| > 0.8 | Large difference | Strong factor in recommendation |

**Multiple comparisons correction:** With 3 pairwise comparisons per metric, apply Bonferroni correction: adjusted alpha = 0.05 / 3 = 0.0167. A difference is "significant" only if p < 0.0167.

---

## 4. Comparison Matrix and Final Scorecard

### 4.1 Weighting Scheme

Weights reflect operational priorities established in KB-0018 (correctness first) and the SOPs (section 8.1):

| Dimension | Weight | Metrics Included | Justification |
|-----------|--------|-----------------|---------------|
| **Correctness** | 30% | Data completeness, zero corruption, zero duplicates | KB-0018: "SQLite > mmap for correctness" — correctness is the top priority |
| **Latency** | 20% | Communication latency (p95), coordination overhead | Real-time blocker detection is a primary motivation (KB-0019 sec 3.4) |
| **Throughput** | 15% | Data points/min/agent | Higher throughput = more data per operation |
| **Overhead** | 15% | Proto vs baseline time delta | Must not regress existing performance |
| **Reliability** | 10% | Heartbeat reliability, rate limit effectiveness | Important but secondary to core data gathering |
| **Resilience** | 10% | S4 detection latency, recovery latency, data completeness under failure | Only tested in S4; weighted lower since failures are uncommon |

### 4.2 Per-Dimension Scoring

Each dimension is scored 0-100 for each prototype:

**Correctness (30%):**
```
score = 100                                              # start at 100
score -= 50 * (corrupted_records / total_records)        # heavy penalty
score -= 20 * (duplicate_records / total_records)         # moderate penalty
score -= 30 * (1 - data_completeness_ratio)              # penalty for missing data
score = max(0, score)
```

**Latency (20%):**
```
# Normalized against baseline. Lower is better.
# If proto latency <= baseline: score = 100
# If proto latency > baseline: score decreases linearly
score = max(0, 100 - ((p95_latency_ms - baseline_p95_ms) / baseline_p95_ms) * 100)
```

**Throughput (15%):**
```
# Normalized: proto throughput as % of baseline throughput
score = min(100, (proto_throughput / baseline_throughput) * 100)
```

**Overhead (15%):**
```
# From SOPs section 8.3 thresholds
if overhead_pct <= 0:     score = 100   # proto is faster than baseline
elif overhead_pct <= 5:   score = 90
elif overhead_pct <= 10:  score = 75    # PASS threshold
elif overhead_pct <= 15:  score = 60
elif overhead_pct <= 25:  score = 40    # CONDITIONAL threshold
else:                     score = max(0, 25 - overhead_pct)  # FAIL
```

**Reliability (10%):**
```
heartbeat_score = heartbeat_reliability * 100   # 0.95 -> 95
rate_limit_score = rate_limit_effectiveness * 100
score = 0.6 * heartbeat_score + 0.4 * rate_limit_score
```

**Resilience (10%):** *(S4 only; for S1-S3, use reliability score as proxy)*
```
detection_score = max(0, 100 - (detection_latency_s - 120) * 2)  # 120s is ideal (2 missed HBs)
recovery_score = max(0, 100 - (recovery_latency_s - 5) * 3)
completeness_score = data_completeness_ratio * 100
score = 0.4 * detection_score + 0.3 * recovery_score + 0.3 * completeness_score
```

### 4.3 Final Scorecard Template

```
=======================================================================
PROTOTYPE BENCHMARK SCORECARD
Test ID: LIVE-TEST-YYYYMMDD-NNN
Date: YYYY-MM-DD
Runs per scenario: N (+ 1 warmup)
=======================================================================

SCENARIO RESULTS (mean +/- 95% CI)
-----------------------------------------------------------------------
Metric               | Proto A          | Proto B          | Baseline
-----------------------------------------------------------------------
S1 Duration (s)      | 120.3 +/- 2.1   | 119.8 +/- 1.9   | 118.5 +/- 1.4
S1 Data pts/min/agt  | 5.2 +/- 0.3     | 5.3 +/- 0.4     | 5.4 +/- 0.2
S2 Duration (s)      | ...              | ...              | ...
S2 Rate violations   | 0.2%             | 1.1%             | 8.5%
S3 Duration (s)      | ...              | ...              | ...
S3 Phase gate lag (s) | ...             | ...              | N/A
S4 Detection (s)     | ...              | ...              | N/A
S4 Recovery (s)      | ...              | ...              | N/A
S4 Completeness      | ...              | ...              | ...
-----------------------------------------------------------------------

DIMENSION SCORES (0-100, weighted)
-----------------------------------------------------------------------
Dimension     | Wt  | Proto A | Proto B | Baseline | Winner
-----------------------------------------------------------------------
Correctness   | 30% |   ?     |   ?     |   ?      |  ?
Latency       | 20% |   ?     |   ?     |   ?      |  ?
Throughput    | 15% |   ?     |   ?     |   ?      |  ?
Overhead      | 15% |   ?     |   ?     |   ?      |  ?
Reliability   | 10% |   ?     |   ?     |   ?      |  ?
Resilience    | 10% |   ?     |   ?     |   ?      |  ?
-----------------------------------------------------------------------
WEIGHTED TOTAL| 100%|   ?     |   ?     |   ?      |  ?
-----------------------------------------------------------------------

STATISTICAL SIGNIFICANCE (Welch's t-test, Bonferroni-corrected alpha=0.0167)
-----------------------------------------------------------------------
Comparison        | Metric            | p-value | Cohen's d | Significant?
-----------------------------------------------------------------------
A vs Baseline     | Duration          |  0.xxx  |   x.xx    | YES/NO
A vs B            | Duration          |  0.xxx  |   x.xx    | YES/NO
B vs Baseline     | Duration          |  0.xxx  |   x.xx    | YES/NO
... (repeat for each key metric)
-----------------------------------------------------------------------

PASS/FAIL DETERMINATION (per SOPs section 8.3)
-----------------------------------------------------------------------
Criterion                    | Proto A     | Proto B     | Baseline
-----------------------------------------------------------------------
Overhead < 10%               | PASS/FAIL   | PASS/FAIL   | N/A
Zero data corruption         | PASS/FAIL   | PASS/FAIL   | PASS/FAIL
Heartbeat accuracy > 95%     | PASS/FAIL   | PASS/FAIL   | N/A
-----------------------------------------------------------------------
OVERALL VERDICT              | PASS/COND/FAIL | PASS/COND/FAIL | REF
-----------------------------------------------------------------------

RECOMMENDATION: [Proto A / Proto B / Neither / Needs more data]
Rationale: [1-2 sentences citing the weighted total and any significant findings]
```

### 4.4 Tiebreaking Rules

If Proto A and Proto B have weighted totals within 3 points of each other:
1. Prefer the one with higher Correctness score (most critical dimension)
2. If still tied, prefer Proto A (simpler architecture, per KB-0018 recommendation)
3. If Proto A wins on correctness but Proto B wins on total, flag for manual review

---

## 5. Benchmark Runner Script Specification

### 5.1 File: `proto_benchmark_runner.py`

Location: `storage/scripts/data-gathering/methods/proto_benchmark_runner.py`

### 5.2 Command-Line Interface

```
Usage:
  python3 proto_benchmark_runner.py --scenario S1 [S2 S3 S4]
                                    --prototypes A B baseline
                                    [--runs 5]
                                    [--warmup 1]
                                    [--output-dir path/to/benchmarks/]
                                    [--test-id LIVE-TEST-YYYYMMDD-NNN]
                                    [--query "test query string"]
```

### 5.3 Pseudocode

```python
#!/usr/bin/env python3
"""
Prototype Benchmark Runner
===========================
Automates all benchmark scenarios (S1-S4), collects metrics,
produces the comparison scorecard.

Extends the existing benchmark_runner.py with prototype-specific
scenario orchestration, multi-run statistical analysis, and
scorecard generation.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path

# --- Constants ---

SCENARIOS = {
    "S1": {"teams": 1, "agents": 3, "sources": ["openalex"], "duration_s": 120},
    "S2": {"teams": 3, "agents": 9, "sources": ["openalex", "hackernews", "arxiv"], "duration_s": 180},
    "S3": {"teams": 4, "agents": 10, "sources": ["openalex", "hackernews", "arxiv", "wikipedia", "ddg"], "duration_s": 300},
    "S4": {"teams": 3, "agents": 7, "sources": ["openalex", "hackernews"], "duration_s": 180, "kill_agent": "data-b-sub-2", "kill_at_s": 60},
}

PROTOTYPES = ["A", "B", "baseline"]

WEIGHTS = {
    "correctness": 0.30,
    "latency": 0.20,
    "throughput": 0.15,
    "overhead": 0.15,
    "reliability": 0.10,
    "resilience": 0.10,
}

# t-critical values for 95% CI (stdlib-only, no scipy)
T_CRITICAL_95 = {2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 9: 2.262, 29: 2.045}


# --- Data Structures ---

@dataclass
class RunResult:
    scenario: str
    prototype: str
    run_number: int
    warmup: bool
    duration_s: float
    data_points: int
    messages_sent: int
    messages_received: int
    heartbeats_sent: int
    heartbeats_received: int
    api_calls_total: int
    api_calls_over_limit: int
    bus_file_bytes: int
    memory_rss_delta_mb: float
    peak_memory_mb: float
    latencies_ms: list          # all message latencies
    errors: int
    corrupted_records: int
    duplicate_records: int
    # S4-specific
    detection_latency_s: float = 0.0
    recovery_latency_s: float = 0.0
    data_completeness_ratio: float = 1.0


@dataclass
class ScenarioSummary:
    scenario: str
    prototype: str
    runs: list                  # list[RunResult], excluding warmup
    mean_duration: float = 0.0
    ci_duration: tuple = (0, 0, 0)  # (mean, lower, upper)
    mean_throughput: float = 0.0
    p95_latency_ms: float = 0.0
    heartbeat_reliability: float = 0.0
    rate_limit_effectiveness: float = 0.0
    overhead_pct: float = 0.0
    dimension_scores: dict = field(default_factory=dict)
    weighted_total: float = 0.0


# --- Core Functions (Pseudocode) ---

def main():
    args = parse_args()
    test_id = args.test_id or generate_test_id()
    all_results = defaultdict(list)     # key: (scenario, prototype)

    for scenario in args.scenarios:
        for prototype in args.prototypes:
            total_runs = args.warmup + args.runs

            for run_num in range(total_runs):
                is_warmup = (run_num < args.warmup)

                # 1. Setup coordination directory
                coord_dir = setup_coordination_dir(scenario, prototype)

                # 2. Initialize prototype (or skip for baseline)
                if prototype != "baseline":
                    init_prototype(prototype, coord_dir, SCENARIOS[scenario])

                # 3. Launch agents
                agent_procs = launch_agents(
                    scenario, prototype, coord_dir, args.query
                )

                # 4. Start metrics collectors (background threads)
                collectors = start_metric_collectors(agent_procs, coord_dir)

                # 5. Run for scenario duration
                scenario_start = time.monotonic()

                # 5a. S4: schedule kill event
                if scenario == "S4":
                    kill_config = SCENARIOS[scenario]
                    schedule_kill(
                        agent_procs[kill_config["kill_agent"]],
                        delay_s=kill_config["kill_at_s"],
                        start_time=scenario_start
                    )

                # 5b. Wait for duration or early completion
                wait_for_completion(
                    agent_procs,
                    timeout_s=SCENARIOS[scenario]["duration_s"],
                    start_time=scenario_start
                )

                scenario_end = time.monotonic()

                # 6. Collect metrics
                result = collect_metrics(
                    scenario, prototype, run_num, is_warmup,
                    scenario_start, scenario_end,
                    agent_procs, collectors, coord_dir
                )

                all_results[(scenario, prototype)].append(result)

                # 7. Teardown
                cleanup_agents(agent_procs)
                cleanup_coordination_dir(coord_dir)

                print(f"  [{scenario}/{prototype}] Run {run_num+1}/{total_runs}"
                      f" {'(warmup)' if is_warmup else ''}"
                      f" — {result.duration_s:.1f}s, {result.data_points} pts")

    # 8. Statistical analysis
    summaries = compute_summaries(all_results, args.runs)

    # 9. Generate scorecard
    scorecard = generate_scorecard(test_id, summaries, args)

    # 10. Write output
    output_path = os.path.join(args.output_dir, f"{test_id}_scorecard.json")
    with open(output_path, "w") as f:
        json.dump(scorecard, f, indent=2)

    # 11. Print human-readable report
    print_scorecard(scorecard)


def setup_coordination_dir(scenario, prototype):
    """Create a fresh temp directory with bus/ subdirectory."""
    coord_dir = tempfile.mkdtemp(prefix=f"proto-bench-{scenario}-{prototype}-")
    os.makedirs(os.path.join(coord_dir, "bus"))
    return coord_dir


def init_prototype(prototype, coord_dir, scenario_config):
    """Initialize the prototype's state backend."""
    if prototype == "A":
        # Import and init SQLite WAL database
        # from prototype.agent_comm.state import SharedState
        # SharedState(os.path.join(coord_dir, "state.db"))
        pass
    elif prototype == "B":
        # Init mmap shared state
        # from prototype.agent_comm_b.state import SharedStateMap
        # SharedStateMap(os.path.join(coord_dir, "shm"))
        pass


def launch_agents(scenario, prototype, coord_dir, query):
    """Spawn agent processes. Returns dict of {agent_name: subprocess.Popen}."""
    agent_procs = {}
    config = SCENARIOS[scenario]

    for team_idx in range(config["teams"]):
        agents_per_team = config["agents"] // config["teams"]
        team_id = f"team-{team_idx + 1:02d}"

        for agent_idx in range(agents_per_team):
            if agent_idx == 0:
                agent_name = f"data-{chr(97 + team_idx)}-lead"
                role = "lead"
            else:
                agent_name = f"data-{chr(97 + team_idx)}-sub-{agent_idx}"
                role = "worker"

            env = os.environ.copy()
            env["CLAUDE_COORD_DIR"] = coord_dir
            env["AGENT_NAME"] = agent_name
            env["AGENT_ROLE"] = role
            env["TEAM_ID"] = team_id
            env["PROTOTYPE"] = prototype
            env["QUERY"] = query
            env["SOURCES"] = ",".join(config["sources"])

            proc = subprocess.Popen(
                [sys.executable, "agent_worker.py"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            agent_procs[agent_name] = proc

    return agent_procs


def schedule_kill(proc, delay_s, start_time):
    """Schedule SIGKILL to a process after delay_s from start_time."""
    import threading
    def _kill():
        elapsed = time.monotonic() - start_time
        remaining = delay_s - elapsed
        if remaining > 0:
            time.sleep(remaining)
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already dead
    t = threading.Thread(target=_kill, daemon=True)
    t.start()


def start_metric_collectors(agent_procs, coord_dir):
    """Start background threads that sample RSS and bus file sizes."""
    import threading

    collectors = {"rss_samples": defaultdict(list), "bus_sizes": [], "stop": threading.Event()}

    def _collect():
        while not collectors["stop"].is_set():
            # Sample RSS for each agent
            for name, proc in agent_procs.items():
                try:
                    statm = Path(f"/proc/{proc.pid}/statm").read_text()
                    rss_pages = int(statm.split()[1])
                    rss_mb = (rss_pages * 4096) / (1024 * 1024)
                    collectors["rss_samples"][name].append(rss_mb)
                except (FileNotFoundError, ProcessLookupError):
                    pass

            # Sample bus file sizes
            bus_dir = os.path.join(coord_dir, "bus")
            total_bytes = sum(
                os.path.getsize(os.path.join(bus_dir, f))
                for f in os.listdir(bus_dir)
                if f.endswith(".jsonl")
            ) if os.path.isdir(bus_dir) else 0
            collectors["bus_sizes"].append((time.monotonic(), total_bytes))

            collectors["stop"].wait(timeout=5.0)  # sample every 5s

    t = threading.Thread(target=_collect, daemon=True)
    t.start()
    return collectors


def collect_metrics(scenario, prototype, run_num, is_warmup,
                    start_time, end_time, agent_procs, collectors, coord_dir):
    """Gather all metrics into a RunResult."""
    collectors["stop"].set()
    duration = end_time - start_time

    # Read agent output files from coord_dir
    # Each agent writes: results.json, latencies.jsonl, api_calls.jsonl
    data_points = 0
    messages_sent = 0
    messages_received = 0
    heartbeats_sent = 0
    heartbeats_received = 0
    api_calls_total = 0
    api_calls_over_limit = 0
    all_latencies = []
    errors = 0
    corrupted = 0
    duplicates = 0

    # ... (parse per-agent output files) ...

    # Bus file sizes
    bus_sizes = collectors["bus_sizes"]
    bus_bytes = bus_sizes[-1][1] if bus_sizes else 0

    # Memory
    rss_samples = collectors["rss_samples"]
    peak_memory = max(
        (max(samples) for samples in rss_samples.values() if samples),
        default=0
    )
    mean_rss_delta = 0  # computed from first vs last sample per agent

    return RunResult(
        scenario=scenario,
        prototype=prototype,
        run_number=run_num,
        warmup=is_warmup,
        duration_s=round(duration, 2),
        data_points=data_points,
        messages_sent=messages_sent,
        messages_received=messages_received,
        heartbeats_sent=heartbeats_sent,
        heartbeats_received=heartbeats_received,
        api_calls_total=api_calls_total,
        api_calls_over_limit=api_calls_over_limit,
        bus_file_bytes=bus_bytes,
        memory_rss_delta_mb=mean_rss_delta,
        peak_memory_mb=peak_memory,
        latencies_ms=all_latencies,
        errors=errors,
        corrupted_records=corrupted,
        duplicate_records=duplicates,
    )


def confidence_interval_95(values):
    """Compute 95% CI using t-distribution. Stdlib only."""
    n = len(values)
    if n < 2:
        mean = values[0] if values else 0
        return (mean, mean, mean)

    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    stderr = sqrt(variance / n)

    df = n - 1
    # Lookup t-critical; fall back to 2.0 for large df
    t_crit = T_CRITICAL_95.get(df, 2.0)
    margin = t_crit * stderr

    return (round(mean, 4), round(mean - margin, 4), round(mean + margin, 4))


def welch_t_test(sample_a, sample_b):
    """Welch's two-sample t-test. Returns (t_statistic, p_value_approx, cohens_d)."""
    n_a, n_b = len(sample_a), len(sample_b)
    mean_a = sum(sample_a) / n_a
    mean_b = sum(sample_b) / n_b
    var_a = sum((x - mean_a) ** 2 for x in sample_a) / (n_a - 1)
    var_b = sum((x - mean_b) ** 2 for x in sample_b) / (n_b - 1)

    se = sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return (0.0, 1.0, 0.0)

    t_stat = (mean_a - mean_b) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / n_a + var_b / n_b) ** 2
    denom = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    df = num / denom if denom > 0 else 1

    # Approximate p-value using lookup (or scipy if available)
    # For stdlib-only: use a conservative approximation
    p_value = approximate_p_value(abs(t_stat), df)

    # Cohen's d
    pooled_std = sqrt((var_a + var_b) / 2)
    cohens_d = abs(mean_a - mean_b) / pooled_std if pooled_std > 0 else 0

    return (round(t_stat, 4), round(p_value, 6), round(cohens_d, 4))


def approximate_p_value(t_abs, df):
    """Conservative p-value approximation without scipy.

    Uses the fact that for df >= 4, t > 2.776 implies p < 0.05.
    Returns conservative estimates using known critical values.
    """
    # Critical t values for two-tailed test at various alpha levels
    # (for df=4, the minimum expected)
    thresholds = [
        (4.604, 0.01),   # p < 0.01
        (2.776, 0.05),   # p < 0.05
        (2.132, 0.10),   # p < 0.10
        (1.533, 0.20),   # p < 0.20
    ]
    for t_crit, p in thresholds:
        if t_abs >= t_crit:
            return p
    return 0.50  # conservative fallback


def compute_summaries(all_results, num_scored_runs):
    """Compute ScenarioSummary for each (scenario, prototype) pair."""
    summaries = {}

    for (scenario, prototype), runs in all_results.items():
        scored = [r for r in runs if not r.warmup]

        durations = [r.duration_s for r in scored]
        mean_dur, ci_lo, ci_hi = confidence_interval_95(durations)

        # Throughput
        active_agents = SCENARIOS[scenario]["agents"]
        throughputs = [
            r.data_points / (r.duration_s / 60) / active_agents
            for r in scored if r.duration_s > 0
        ]
        mean_tp = sum(throughputs) / len(throughputs) if throughputs else 0

        # Latency p95
        all_lats = []
        for r in scored:
            all_lats.extend(r.latencies_ms)
        all_lats.sort()
        p95_idx = int(len(all_lats) * 0.95) if all_lats else 0
        p95_lat = all_lats[p95_idx] if all_lats else 0

        # Heartbeat reliability
        total_hb_sent = sum(r.heartbeats_sent for r in scored)
        total_hb_recv = sum(r.heartbeats_received for r in scored)
        hb_reliability = total_hb_recv / total_hb_sent if total_hb_sent > 0 else 1.0

        # Rate limit effectiveness
        total_api = sum(r.api_calls_total for r in scored)
        total_over = sum(r.api_calls_over_limit for r in scored)
        rl_effectiveness = 1 - (total_over / total_api) if total_api > 0 else 1.0

        summaries[(scenario, prototype)] = ScenarioSummary(
            scenario=scenario,
            prototype=prototype,
            runs=scored,
            mean_duration=mean_dur,
            ci_duration=(mean_dur, ci_lo, ci_hi),
            mean_throughput=mean_tp,
            p95_latency_ms=p95_lat,
            heartbeat_reliability=hb_reliability,
            rate_limit_effectiveness=rl_effectiveness,
        )

    # Compute overhead and dimension scores
    for (scenario, prototype), summary in summaries.items():
        if prototype == "baseline":
            continue

        baseline_key = (scenario, "baseline")
        if baseline_key in summaries:
            baseline_dur = summaries[baseline_key].mean_duration
            if baseline_dur > 0:
                summary.overhead_pct = (
                    (summary.mean_duration - baseline_dur) / baseline_dur
                ) * 100

        summary.dimension_scores = compute_dimension_scores(
            summary, summaries.get(baseline_key)
        )
        summary.weighted_total = sum(
            WEIGHTS[dim] * score
            for dim, score in summary.dimension_scores.items()
        )

    return summaries


def compute_dimension_scores(proto_summary, baseline_summary):
    """Compute 0-100 score for each dimension."""
    scores = {}

    # Correctness
    scored_runs = proto_summary.runs
    total_records = sum(r.data_points for r in scored_runs)
    total_corrupted = sum(r.corrupted_records for r in scored_runs)
    total_duplicates = sum(r.duplicate_records for r in scored_runs)
    total_expected = total_records + total_corrupted  # approximate
    cs = 100
    if total_expected > 0:
        cs -= 50 * (total_corrupted / total_expected)
        cs -= 20 * (total_duplicates / total_expected)
    scores["correctness"] = max(0, cs)

    # Latency
    if baseline_summary and baseline_summary.p95_latency_ms > 0:
        ratio = proto_summary.p95_latency_ms / baseline_summary.p95_latency_ms
        scores["latency"] = max(0, 100 - (ratio - 1) * 100)
    else:
        scores["latency"] = 80  # no baseline comparison, neutral score

    # Throughput
    if baseline_summary and baseline_summary.mean_throughput > 0:
        scores["throughput"] = min(100,
            (proto_summary.mean_throughput / baseline_summary.mean_throughput) * 100)
    else:
        scores["throughput"] = 80

    # Overhead
    o = proto_summary.overhead_pct
    if o <= 0:      scores["overhead"] = 100
    elif o <= 5:    scores["overhead"] = 90
    elif o <= 10:   scores["overhead"] = 75
    elif o <= 15:   scores["overhead"] = 60
    elif o <= 25:   scores["overhead"] = 40
    else:           scores["overhead"] = max(0, 25 - o)

    # Reliability
    hb = proto_summary.heartbeat_reliability * 100
    rl = proto_summary.rate_limit_effectiveness * 100
    scores["reliability"] = 0.6 * hb + 0.4 * rl

    # Resilience (placeholder; populated from S4 data if available)
    scores["resilience"] = 80  # default neutral

    return scores


def generate_scorecard(test_id, summaries, args):
    """Produce the final comparison JSON."""
    scorecard = {
        "test_id": test_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "scenarios": args.scenarios,
            "prototypes": args.prototypes,
            "runs_per_scenario": args.runs,
            "warmup_runs": args.warmup,
            "query": args.query,
        },
        "scenario_results": {},
        "dimension_scores": {},
        "statistical_tests": [],
        "verdict": {},
    }

    # Populate scenario results
    for (scenario, prototype), summary in summaries.items():
        key = f"{scenario}_{prototype}"
        scorecard["scenario_results"][key] = {
            "mean_duration_s": summary.mean_duration,
            "ci_95_duration": list(summary.ci_duration),
            "mean_throughput_pts_per_min_per_agent": round(summary.mean_throughput, 2),
            "p95_latency_ms": round(summary.p95_latency_ms, 2),
            "heartbeat_reliability": round(summary.heartbeat_reliability, 4),
            "rate_limit_effectiveness": round(summary.rate_limit_effectiveness, 4),
            "overhead_pct": round(summary.overhead_pct, 2),
        }

    # Populate dimension scores (aggregate across scenarios)
    for prototype in args.prototypes:
        if prototype == "baseline":
            continue
        proto_summaries = [s for (sc, p), s in summaries.items() if p == prototype]
        if not proto_summaries:
            continue

        agg_scores = {}
        for dim in WEIGHTS:
            dim_vals = [s.dimension_scores.get(dim, 0) for s in proto_summaries
                       if s.dimension_scores]
            agg_scores[dim] = round(sum(dim_vals) / len(dim_vals), 1) if dim_vals else 0

        weighted = sum(WEIGHTS[d] * agg_scores[d] for d in WEIGHTS)
        scorecard["dimension_scores"][prototype] = {
            "per_dimension": agg_scores,
            "weighted_total": round(weighted, 1),
        }

    # Statistical tests (A vs B, A vs Baseline, B vs Baseline)
    for scenario in args.scenarios:
        for proto_x, proto_y in [("A", "baseline"), ("B", "baseline"), ("A", "B")]:
            key_x = (scenario, proto_x)
            key_y = (scenario, proto_y)
            if key_x in summaries and key_y in summaries:
                durs_x = [r.duration_s for r in summaries[key_x].runs]
                durs_y = [r.duration_s for r in summaries[key_y].runs]
                t_stat, p_val, d = welch_t_test(durs_x, durs_y)
                scorecard["statistical_tests"].append({
                    "scenario": scenario,
                    "comparison": f"{proto_x} vs {proto_y}",
                    "metric": "duration_s",
                    "t_statistic": t_stat,
                    "p_value": p_val,
                    "cohens_d": d,
                    "significant": p_val < 0.0167,  # Bonferroni-corrected
                })

    # Verdict
    totals = {p: scorecard["dimension_scores"].get(p, {}).get("weighted_total", 0)
              for p in ["A", "B"]}
    winner = max(totals, key=totals.get) if totals else "undetermined"
    margin = abs(totals.get("A", 0) - totals.get("B", 0))

    if margin < 3:
        # Tiebreaker: prefer higher correctness
        a_corr = scorecard["dimension_scores"].get("A", {}).get("per_dimension", {}).get("correctness", 0)
        b_corr = scorecard["dimension_scores"].get("B", {}).get("per_dimension", {}).get("correctness", 0)
        if a_corr >= b_corr:
            winner = "A"
            reason = "Tie broken by correctness score (or default preference for simpler architecture)"
        else:
            winner = "B"
            reason = "Tie broken by higher correctness score"
    else:
        reason = f"Winner by {margin:.1f} weighted points"

    scorecard["verdict"] = {
        "winner": f"Prototype {winner}",
        "weighted_total_A": totals.get("A", 0),
        "weighted_total_B": totals.get("B", 0),
        "margin": round(margin, 1),
        "rationale": reason,
    }

    return scorecard


def print_scorecard(scorecard):
    """Print human-readable scorecard to stderr."""
    print("\n" + "=" * 72, file=sys.stderr)
    print(f"PROTOTYPE BENCHMARK SCORECARD — {scorecard['test_id']}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)

    # Scenario results table
    print(f"\n{'Scenario/Proto':<20} {'Duration':>10} {'Throughput':>12} {'Overhead':>10}",
          file=sys.stderr)
    print("-" * 55, file=sys.stderr)
    for key, data in sorted(scorecard["scenario_results"].items()):
        ci = data["ci_95_duration"]
        print(f"{key:<20} {ci[0]:>8.1f}s  {data['mean_throughput_pts_per_min_per_agent']:>10.1f}  {data['overhead_pct']:>8.1f}%",
              file=sys.stderr)

    # Dimension scores
    print(f"\n{'Dimension':<15} {'Weight':>6} {'Proto A':>10} {'Proto B':>10}", file=sys.stderr)
    print("-" * 45, file=sys.stderr)
    for dim in WEIGHTS:
        wt = f"{WEIGHTS[dim]*100:.0f}%"
        a_score = scorecard["dimension_scores"].get("A", {}).get("per_dimension", {}).get(dim, "—")
        b_score = scorecard["dimension_scores"].get("B", {}).get("per_dimension", {}).get(dim, "—")
        print(f"{dim:<15} {wt:>6} {str(a_score):>10} {str(b_score):>10}", file=sys.stderr)

    total_a = scorecard["dimension_scores"].get("A", {}).get("weighted_total", "—")
    total_b = scorecard["dimension_scores"].get("B", {}).get("weighted_total", "—")
    print("-" * 45, file=sys.stderr)
    print(f"{'WEIGHTED TOTAL':<15} {'100%':>6} {str(total_a):>10} {str(total_b):>10}", file=sys.stderr)

    # Verdict
    v = scorecard["verdict"]
    print(f"\nVERDICT: {v['winner']} — {v['rationale']}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)


def generate_test_id():
    return f"LIVE-TEST-{datetime.now().strftime('%Y%m%d')}-001"


def parse_args():
    parser = argparse.ArgumentParser(description="Prototype benchmark runner")
    parser.add_argument("--scenario", nargs="+", dest="scenarios",
                        default=["S1", "S2", "S3", "S4"],
                        choices=["S1", "S2", "S3", "S4"])
    parser.add_argument("--prototypes", nargs="+", default=["A", "B", "baseline"])
    parser.add_argument("--runs", type=int, default=5,
                        help="Number of scored runs per scenario (default: 5)")
    parser.add_argument("--warmup", type=int, default=1,
                        help="Number of warmup runs to discard (default: 1)")
    parser.add_argument("--output-dir", default="storage/scripts/data-gathering/benchmarks/")
    parser.add_argument("--test-id", default=None)
    parser.add_argument("--query", default="artificial intelligence trading strategies")
    return parser.parse_args()


def wait_for_completion(agent_procs, timeout_s, start_time):
    """Wait until all agents finish or timeout expires."""
    deadline = start_time + timeout_s
    while time.monotonic() < deadline:
        if all(p.poll() is not None for p in agent_procs.values()):
            break
        time.sleep(1)
    # Kill any remaining agents
    for name, proc in agent_procs.items():
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def cleanup_agents(agent_procs):
    """Ensure all agent processes are terminated."""
    for proc in agent_procs.values():
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def cleanup_coordination_dir(coord_dir):
    """Remove temporary coordination directory."""
    import shutil
    shutil.rmtree(coord_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
```

---

## Appendix A: Metric Collection File Formats

Each agent writes these files to `{coord_dir}/metrics/{agent_name}/`:

### `latencies.jsonl`
```json
{"ts": 1710000000.123, "msg_id": "uuid", "send_ts": 1710000000.100, "recv_ts": 1710000000.123, "latency_ms": 23.0, "channel": "global"}
```

### `api_calls.jsonl`
```json
{"ts": 1710000000.500, "api": "openalex", "allowed": true, "status_code": 200, "duration_ms": 450}
```

### `results.json`
```json
{"agent": "data-a-sub-1", "data_points": 15, "errors": 0, "corrupted": 0, "duplicates": 0}
```

### `heartbeat_log.jsonl`
```json
{"ts": 1710000030.0, "agent": "data-a-sub-1", "type": "sent", "progress_pct": 45}
```

---

## Appendix B: Environment Checklist Before Running

- [ ] All 113 prototype tests pass: `python -m pytest prototype/tests/ -v`
- [ ] Python 3.10+
- [ ] Disk space > 100 MB free
- [ ] No stale coordination directories in `/tmp/proto-bench-*`
- [ ] API connectivity verified: OpenAlex, HN, arXiv reachable
- [ ] Existing `benchmark_runner.py` passes a test run
- [ ] Test ID does not collide with existing benchmarks in `storage/scripts/data-gathering/benchmarks/`

---

## Appendix C: Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| API rate limits during benchmark | High | Skewed throughput numbers | Use consistent queries; retry with backoff; note 429s in results |
| Filesystem cache invalidation between runs | Medium | Noisy warmup data | Discard first run; use `time.monotonic()` not wall clock |
| Proto B 32-agent limit exceeded | Low (max 10 agents in S3) | Test failure | Verify agent count < 32 in scenario config |
| SQLite WAL checkpoint during measurement | Medium | Latency spike | Set `PRAGMA wal_autocheckpoint=0` during benchmark; checkpoint after |
| Named pipe blocking in Proto B | Medium | Agent deadlock | Set O_NONBLOCK on all FIFOs; timeout at 5s |
| Network instability during multi-API scenario | Medium | Incomplete data | Record actual API errors; compare data completeness across protos |
