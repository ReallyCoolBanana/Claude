# Live Test Team Plan — TEAM-0020

> **Date:** 2026-03-11
> **Objective:** Run live data gathering tests using both prototypes, benchmark, and compare
> **Builds On:** KB-0018, KB-0019, Think Tank Alpha/Beta/Gamma plans
> **Test ID Series:** LIVE-TEST-20260311-*

---

## Team Structure

### Phase 1: Think Tank (3 teams, 9 simulated agents) — COMPLETED
- **Alpha** (Lead + 2 subs): Communication architecture → `think-tank-alpha-plan.md`
- **Beta** (Lead + 2 subs): Coordination strategy → `think-tank-beta-plan.md`
- **Gamma** (Lead + 2 subs): Benchmark design → `think-tank-gamma-plan.md`

### Phase 2: Data Gathering Teams (3 teams, 9 agents each configuration)

**Data Team A — Prototype A (JSONL + SQLite)**
```
data-a-coordinator (1)
├── data-a-lead (team lead, coordinates sub-tasks)
├── data-a-sub-1 (worker: synthetic-api-1, fast)
└── data-a-sub-2 (worker: synthetic-api-2, medium)
```
Communication: JSONL bus + SQLite shared state
Channels: global, team-data-a, heartbeat, topic-data-results, topic-rate-limits

**Data Team B — Prototype B (FIFO + mmap)**
```
data-b-coordinator (1)
├── data-b-lead (team lead, coordinates sub-tasks)
├── data-b-sub-1 (worker: synthetic-api-1, fast)
└── data-b-sub-2 (worker: synthetic-api-2, medium)
```
Communication: Named pipe bus + mmap shared state
Channels: global, team-data-b, heartbeat, topic-data-results, topic-rate-limits

**Data Team C — Baseline (no communication prototype)**
```
data-c-lead (team lead)
├── data-c-sub-1 (worker: synthetic-api-1, file-polling)
└── data-c-sub-2 (worker: synthetic-api-2, file-polling)
```
Communication: File-based polling (no bus, no shared state)

### Phase 3: Benchmark Analysis (1 agent)
- Collects results from all 3 data teams
- Runs `proto_benchmark_runner.py` for automated scoring
- Produces comparison scorecard per SOPs-prototype-testing.md section 8

---

## Test Scenarios to Run

| Scenario | Teams | Agents | Duration | What Tests |
|----------|-------|--------|----------|------------|
| S1 | 1 × 3 configs | 3 each | 15s | Basic coordination overhead |
| S2 | 3 × 3 configs | 9 each | 20s | Cross-team rate limit sharing |
| S3 | 4 × 3 configs | 10 each | 25s | Full coordinator with heartbeats |
| S4 | 3 × 3 configs | 7 each | 20s | Failure injection + recovery |

**Total runs:** 4 scenarios × 3 prototypes × 3 repetitions = 36 runs

---

## Key Metrics (from Think Tank Gamma)

1. **Communication latency**: Message send → receive (ms)
2. **Coordination overhead**: Proto time vs baseline (%)
3. **Data throughput**: Data points / minute / agent
4. **Heartbeat reliability**: Received / expected ratio
5. **Rate limit effectiveness**: Over-limit calls / total
6. **Memory footprint**: RSS delta (KB)
7. **Bus file growth**: Bytes / minute

---

## Pass/Fail Criteria

| Grade | Overhead | Errors | Heartbeat Accuracy |
|-------|----------|--------|--------------------|
| PASS | < 10% | 0 | > 95% |
| CONDITIONAL | 10-25% | Minor | > 85% |
| FAIL | > 25% | Data loss | < 85% |

---

## Artifacts

- `proto_benchmark_runner.py` — Automated benchmark harness
- `benchmark_results.json` — Raw results from all 36 runs
- `think-tank-alpha-plan.md` — Communication architecture
- `think-tank-beta-plan.md` — Coordination strategy
- `think-tank-gamma-plan.md` — Benchmark methodology
- `live-test-team-plan.md` — This document
