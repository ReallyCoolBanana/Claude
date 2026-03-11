# Standard Operating Procedures: Prototype Live Testing

> **Specialized SOP for live testing of multi-agent communication prototypes.**
> Supplements (does NOT replace) the main `SOPs.md`.
> Created: 2026-03-11. Based on KB-0018, KB-0019, and 113-test validation suite.

---

## Table of Contents

1. [Scope & Purpose](#1-scope--purpose)
2. [Prototype Overview](#2-prototype-overview)
3. [Pre-Test Checklist](#3-pre-test-checklist)
4. [Think Tank Phase SOP](#4-think-tank-phase-sop)
5. [Data Gathering Test SOP](#5-data-gathering-test-sop)
6. [Benchmarking SOP](#6-benchmarking-sop)
7. [Team Configurations for Prototype Testing](#7-team-configurations-for-prototype-testing)
8. [Metrics & Scoring](#8-metrics--scoring)
9. [Post-Test Archiving](#9-post-test-archiving)

---

## 1. Scope & Purpose

This SOP governs **live operational testing** of the two communication prototypes:
- **Prototype A** (JSONL + SQLite WAL): Production candidate
- **Prototype B** (Named pipes + mmap): Speed-optimized reference

Live testing means running actual multi-agent operations (research, data gathering, coordination) using the prototype communication layers, not just unit/integration tests.

**Goals:**
- Validate prototypes under real workloads (data gathering, research, coordination)
- Benchmark communication overhead vs. existing file-polling pattern
- Identify operational gaps not caught by synthetic tests
- Determine optimal team configurations for prototype-enhanced operations

---

## 2. Prototype Overview

| Aspect | Prototype A | Prototype B |
|--------|-------------|-------------|
| Message Bus | JSONL append-only, O_APPEND atomic | Named pipes (FIFOs) + spillover |
| Shared State | SQLite WAL, BEGIN IMMEDIATE | mmap fixed-size struct + fcntl.flock |
| Coordinator | Thread-per-concern, polling | select()-based event loop |
| Max Agents | Unlimited (SQLite rows) | 32 (fixed mmap slots) |
| Test Status | 51/51 PASSED | 62/62 PASSED (after fixes) |
| Recommended For | Production use | Performance comparison baseline |

### Key Modules

```
prototype/agent_comm/          # Prototype A
  core.py                      # CommDir, AgentIdentity, CommConfig
  bus.py                       # BusWriter, BusReader (JSONL)
  state.py                     # SharedState (SQLite WAL)
  coordinator.py               # Coordinator, Worker, EpochRotator, Watchdog

prototype/agent_comm_b/        # Prototype B
  core.py                      # CommDir (pipes/shm dirs)
  bus.py                       # PipeBusWriter, PipeBusReader (FIFOs)
  state.py                     # SharedStateMap (mmap)
  coordinator.py               # Coordinator, Worker, SpilloverCleaner, Watchdog
```

---

## 3. Pre-Test Checklist

### 3.1 Environment Verification

- [ ] Run existing test suite: `python -m pytest prototype/tests/ -v --tb=short`
- [ ] Confirm 113/113 tests pass before any live testing
- [ ] Verify temp directory is writable: `python3 -c "import tempfile; print(tempfile.mkdtemp())"`
- [ ] Check available disk space (need ~100MB for coordination files)
- [ ] Verify Python 3.10+ (for dataclass features used in prototypes)

### 3.2 Prototype Initialization

- [ ] Create coordination directory structure:
  ```bash
  mkdir -p /tmp/claude-proto-test-a/bus
  mkdir -p /tmp/claude-proto-test-b/{pipes,shm}
  ```
- [ ] Initialize Prototype A state:
  ```python
  from prototype.agent_comm.state import SharedState
  ss = SharedState("/tmp/claude-proto-test-a/state.db")
  ```
- [ ] Initialize Prototype B state:
  ```python
  from prototype.agent_comm_b.state import SharedStateMap
  sm = SharedStateMap("/tmp/claude-proto-test-b/shm")
  ```

### 3.3 ID Allocation

For live testing sessions, use these ID ranges:
- Teams: TEAM-0020+ (check `teams/sessions/index.json` for latest)
- KB entries: KB-0020+ (check `knowledge-base/index.json` for latest)
- Test run IDs: `LIVE-TEST-{YYYYMMDD}-{sequence}` (e.g., `LIVE-TEST-20260311-001`)

---

## 4. Think Tank Phase SOP

### 4.1 Purpose

Before running data gathering tests, deploy 3 Think Tank teams to plan how to efficiently use prototype features. Each team analyzes a different dimension.

### 4.2 Team Structure (3 Teams, 3-4 agents each)

**Think Tank Alpha: Communication Architecture**
- Lead + 2 subagents
- Focus: Which bus channels to create, message schemas for data gathering, heartbeat intervals
- Deliverable: Communication plan document

**Think Tank Beta: Coordination Strategy**
- Lead + 2 subagents
- Focus: Coordinator role design, phase gating for data gathering, rate limit sharing via SQLite
- Deliverable: Coordination strategy document

**Think Tank Gamma: Benchmark Design**
- Lead + 2 subagents
- Focus: What metrics to measure, how to compare proto A vs B vs baseline, statistical methodology
- Deliverable: Benchmark specification document

### 4.3 Think Tank Execution Rules

1. All 3 teams run in **parallel** (no dependencies between them)
2. Each team reads KB-0018 (prototype results) and KB-0019 (architecture analysis) before planning
3. Output format: structured JSON with `plan`, `rationale`, `risks`, `metrics` fields
4. Time limit: Each team has 5 minutes max
5. After all 3 complete, the coordinator merges plans into a unified test strategy

---

## 5. Data Gathering Test SOP

### 5.1 Test Scenarios

Run these scenarios using both prototypes and a baseline (no communication layer):

| Scenario | Teams | Agents | Duration | What It Tests |
|----------|-------|--------|----------|---------------|
| S1: Single-source fetch | 1 | 3 | 2 min | Basic coordination overhead |
| S2: Multi-source parallel | 3 | 9 | 3 min | Cross-team rate limit sharing |
| S3: Coordinator + workers | 1+3 | 10 | 5 min | Full coordinator loop with heartbeats |
| S4: Failure recovery | 1+2 | 7 | 3 min | Dead agent detection + recovery |

### 5.2 Data Sources for Testing

Use these free, rate-limit-friendly APIs for data gathering tests:

| Source | Script | Rate Limit | Best For |
|--------|--------|-----------|----------|
| OpenAlex | method_openalex.py | Polite pool | Academic paper metadata |
| Hacker News | method_hackernews.py | Generous | Tech news + comments |
| Wikipedia | method_wikipedia.py | Generous | Encyclopedia data |
| DuckDuckGo | ddg_utils.py | 0.5s/req | Web search |

### 5.3 Test Execution Protocol

1. **Baseline run** (no prototype): Run the data gathering scenario with standard file-polling coordination. Record: time, data points, errors.
2. **Prototype A run**: Same scenario with JSONL bus + SQLite state active. Record same metrics plus: messages sent, heartbeats, bus file size.
3. **Prototype B run**: Same scenario with FIFO bus + mmap state. Record same metrics plus: pipe throughput, mmap operations.
4. **Comparison**: Calculate overhead percentage = (proto_time - baseline_time) / baseline_time * 100

### 5.4 Data Quality Validation

After each test run, validate gathered data:
- [ ] All expected data points present (no silent drops)
- [ ] JSON output is valid and parseable
- [ ] No duplicate entries (run through dedup_utils.py)
- [ ] Source attribution is correct
- [ ] Timestamps are within expected range

---

## 6. Benchmarking SOP

### 6.1 Metrics to Capture

| Metric | Unit | How Measured |
|--------|------|-------------|
| End-to-end latency | ms | Time from coordinator command to agent acknowledgement |
| Message throughput | msg/sec | Messages processed per second on bus |
| Heartbeat accuracy | % | Heartbeats received vs expected |
| Dead agent detection time | sec | Time from agent death to coordinator detection |
| Rate limit accuracy | % | API calls within limit vs total attempted |
| Data gathering throughput | points/min | Data points collected per minute |
| Communication overhead | % | (proto_time - baseline_time) / baseline_time |
| Bus file growth | KB/min | JSONL file size growth rate |
| Memory usage | MB | Peak RSS during test |
| Error rate | % | Failed operations / total operations |

### 6.2 Benchmark Output Format

```json
{
  "test_id": "LIVE-TEST-20260311-001",
  "scenario": "S1",
  "prototype": "A",
  "timestamp": "2026-03-11T...",
  "metrics": {
    "duration_ms": 0,
    "messages_sent": 0,
    "messages_received": 0,
    "data_points_collected": 0,
    "heartbeats_sent": 0,
    "heartbeats_received": 0,
    "errors": 0,
    "bus_file_bytes": 0,
    "peak_memory_mb": 0
  },
  "comparison": {
    "baseline_duration_ms": 0,
    "overhead_pct": 0
  }
}
```

### 6.3 Statistical Requirements

- Run each scenario **3 times minimum** for each prototype
- Report: mean, median, min, max, stddev for all timing metrics
- Discard first run as warmup (filesystem cache effects)
- Use consistent test queries across all runs

---

## 7. Team Configurations for Prototype Testing

### 7.1 Recommended Team Setups

**Configuration 1: Minimal Test (7 agents)**
```
Coordinator (1)
├── Think Tank (3 agents, parallel)
│   ├── Alpha: Communication plan
│   ├── Beta: Coordination strategy
│   └── Gamma: Benchmark design
└── Test Runner (3 agents, sequential per scenario)
    ├── Baseline runner
    ├── Proto A runner
    └── Proto B runner
```

**Configuration 2: Full Test (13 agents)**
```
Coordinator (1)
├── Think Tank (3 × 3 = 9 agents, parallel triads)
│   ├── Alpha team (lead + 2 subs)
│   ├── Beta team (lead + 2 subs)
│   └── Gamma team (lead + 2 subs)
└── Test Execution (3 agents, parallel per prototype)
    ├── Proto A test team
    ├── Proto B test team
    └── Baseline test team
```

**Configuration 3: Data Gathering Benchmark (10 agents)**
```
Coordinator (1)
├── Data Team A (3 agents) — Proto A communication
│   ├── Lead: OpenAlex + Wikipedia
│   ├── Sub-1: Hacker News
│   └── Sub-2: DuckDuckGo search
├── Data Team B (3 agents) — Proto B communication
│   ├── Lead: OpenAlex + Wikipedia
│   ├── Sub-1: Hacker News
│   └── Sub-2: DuckDuckGo search
└── Data Team C (3 agents) — Baseline (no proto)
    ├── Lead: OpenAlex + Wikipedia
    ├── Sub-1: Hacker News
    └── Sub-2: DuckDuckGo search
```

### 7.2 Agent Naming Convention

For prototype test teams, use this naming:
- `proto-test-coord`: Test coordinator
- `think-{alpha|beta|gamma}-lead`: Think tank leads
- `think-{alpha|beta|gamma}-sub-{N}`: Think tank subagents
- `data-{a|b|c}-lead`: Data gathering team leads
- `data-{a|b|c}-sub-{N}`: Data gathering subagents
- `bench-runner-{a|b|baseline}`: Benchmark execution agents

---

## 8. Metrics & Scoring

### 8.1 Prototype Comparison Scorecard

| Dimension | Weight | Proto A Score | Proto B Score | Baseline |
|-----------|--------|--------------|--------------|----------|
| Correctness | 30% | ? | ? | ? |
| Latency | 20% | ? | ? | ? |
| Throughput | 15% | ? | ? | ? |
| Overhead | 15% | ? | ? | ? |
| Reliability | 10% | ? | ? | ? |
| Scalability | 10% | ? | ? | ? |

### 8.2 Scoring Criteria

- **Correctness**: All data gathered, no corruption, no duplicates
- **Latency**: Time to detect events (heartbeat miss, blocker, phase change)
- **Throughput**: Data points per minute, messages per second
- **Overhead**: Time added vs baseline (lower is better)
- **Reliability**: Error rate, recovery from failures
- **Scalability**: Performance degradation at higher agent counts

### 8.3 Pass/Fail Criteria

- **PASS**: Overhead < 10%, zero data corruption, heartbeat accuracy > 95%
- **CONDITIONAL PASS**: Overhead 10-25%, minor data issues, heartbeat accuracy > 85%
- **FAIL**: Overhead > 25%, data corruption, heartbeat accuracy < 85%

---

## 9. Post-Test Archiving

### 9.1 Required Artifacts

After each live test session, archive:

- [ ] Benchmark results JSON (to `storage/scripts/data-gathering/benchmarks/`)
- [ ] Team session log (to `teams/sessions/`)
- [ ] KB entry with findings (to `knowledge-base/entries/`)
- [ ] Raw bus files (compressed, to `prototype/tests/live-test-archives/`)
- [ ] Comparison scorecard (in KB entry)

### 9.2 Cleanup

- [ ] Delete temporary coordination directories
- [ ] Remove any leftover FIFO pipes
- [ ] Clear SQLite WAL/SHM files
- [ ] Update all index.json files

### 9.3 Handoff Notes Template for Live Tests

```markdown
## Live Test Handoff
- Test ID: LIVE-TEST-YYYYMMDD-NNN
- Scenarios run: [S1, S2, ...]
- Proto A result: PASS/CONDITIONAL/FAIL
- Proto B result: PASS/CONDITIONAL/FAIL
- Key finding: [one sentence]
- Blocker for next test: [if any]
- Recommended next scenario: [description]
```
