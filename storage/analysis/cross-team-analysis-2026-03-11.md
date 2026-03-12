# Cross-Team Analysis Report
**Date:** 2026-03-11
**Scope:** Teams 1-28, SOPs 011-021, KB entries 001-029
**Analysis ID:** ANALYSIS-001

## Executive Summary

Across 28 team sessions deploying 130+ agent-instances, the system evolved from ad-hoc coordination to a full coordination stack with 13 SOPs. Productivity improved in capability but plateaued in raw output. The primary risk is inward-facing gravity — teams increasingly build coordination infrastructure rather than doing domain work.

---

## 1. Team Performance Metrics

### Phase 1: Foundation (T01-T08) — Ad-hoc Coordination
| Team | Agents | Objective | Artifacts/Agent | Key Outcome |
|------|--------|-----------|-----------------|-------------|
| T01 | 16 | Storage stress test | 0.69 | Validated storage system, found scaling limits |
| T02 | 7 | Sonnet Research API | 1.0 | Built multi-agent research system |
| T03 | 27 | Competitive stock research | 0.44 | 5-team competitive analysis, 89% efficiency loss |
| T04 | 17 | Data gathering improvement loop | 0.59 | 3-team continuous improvement |
| T05 | 1 | Solo iteration 2 | 4.0 | **Highest per-agent efficiency** |
| T06 | 3 | Archive tools | 1.0 | 3 tools built |
| T07 | 8 | Config testing + archive | 0.75 | Team sizing research |
| T08 | 9 | Data storage research | 0.67 | AI-optimized storage patterns |

**Phase 1 Finding:** Solo agents (T05: 4.0) and small teams (T06: 1.0) vastly outperform large teams (T03: 0.44) in per-agent efficiency. Coordination overhead is 15-30% of agent-hours.

### Phase 2: Coordinator-Mediated (T09-T14)
| Team | Agents | Objective | Key Outcome |
|------|--------|-----------|-------------|
| T09 | 20 | Full coordinated ops | 0.45 artifacts/agent, Go tools + archive |
| T10 | 1 | Developer APIs | API catalog (solo) |
| T11 | 1 | Government APIs | API catalog (solo) |
| T12 | 1 | AI/ML APIs | API catalog (solo) |
| T13 | 1 | Government APIs v2 | Duplicate of T11 objective |
| T14 | 9 | API tool building | Data gathering tools built |

**Phase 2 Finding:** Coordinator becomes bottleneck. Solo research agents (T10-T13) efficiently produce focused catalogs. T13 duplicated T11's objective — coordination gap.

### Phase 3: Bus Communication (T15-T22)
| Team | Agents | Objective | Key Outcome |
|------|--------|-----------|-------------|
| T15-T17 | 1 each | Communication design | JSONL bus + SQLite WAL specs |
| T18 | 7 | Prototype build | Dual prototypes, test-fix cycles |
| T19 | 1 | Architecture analysis | Integration spec |
| T20 | 14 | Live prototype testing | Benchmark contradiction discovered |
| T21 | 4 | Proto A efficiency | Production decision for Proto A |
| T22 | 18 | Full production deployment | 6-team coordination, cross-team bug found |

**Phase 3 Finding:** Communication infrastructure enables cross-team discovery (T22 found bug via inter-team friction). Proto A benchmark was misconfigured (79% below baseline in synthetic, 102.5% in live). Proto B archived.

### Phase 4: Full Coordination Stack (T23-T28)
| Team | Agents | Objective | Key Outcome |
|------|--------|-----------|-------------|
| T23 | 21 | Dynamic coordination features | Help protocol, work stealing, direct channels |
| T24 | 8 | Stress testing + SOPs | 356 lines/agent (stress tests), SOPs created |
| T25 | 15 | Bug fixes + hub | 14 bugs found (0.93/agent), coordinator hub |
| T26 | 1 | Data quality fixes | Index registration cleanup |
| T27 | 5 | Full repo audit | **90 seconds, zero idle time** |
| T28 | 7 | 7-team think tank analysis | 3 TT + 3 RT + 1 bug monitor |

**Phase 4 Finding:** System now supports dedicated bug-hunting teams parallel with fix teams. T27 is proof-of-concept for efficient small teams. But T22-T28 all focused inward on coordination infrastructure.

---

## 2. Efficiency Analysis

### Optimal Team Sizes (Evidence-Based)
| Task Type | Optimal Size | Evidence |
|-----------|-------------|----------|
| Independent research | 1 agent | T05 (4.0 artifacts/agent) |
| Tool building | 3 agents | T06 (1.0 artifacts/agent) |
| Integrated builds | 7 agents | T07/T02 data |
| Research operations | 9 agents (3 leads × 2 subs) | T07 config testing |
| Full operations | 8-10 agents max | T24 stress testing |
| Large operations (20+) | Avoid unless unique capabilities needed | T03/T09 (89% efficiency loss) |

### Coordination Overhead by Team Size
- 1-3 agents: ~5% overhead
- 4-9 agents: 15-20% overhead
- 10-20 agents: 25-30% overhead
- 20+ agents: 30%+ overhead, diminishing returns

### Unique Multi-Team Capabilities (Worth the Overhead)
1. **Competitive verification** — independent quality scoring
2. **Cross-team bug detection** — bugs found via inter-team friction
3. **Parallel multi-language development** — Python/Go simultaneously
4. **Configuration meta-experimentation** — 4 configs simultaneously
5. **Continuous improvement loops** — feedback between teams

---

## 3. Bug Detection Maturity

| Phase | Method | Rate | Example |
|-------|--------|------|---------|
| Early (T01-T09) | Incidental | ~1 issue/agent | Storage test findings |
| Mid (T14-T22) | Ad hoc | 0.06 bugs/agent | T22 cross-team discovery |
| Late (T24-T25) | Systematic think-tank | 0.93 bugs/agent | T25 found 14 bugs |
| Latest (T28) | Dedicated bug monitor | targeted | Bug monitor report: 95 issues |

### Critical Bug Categories
- **High Severity (2):** fulfill_help not transactional, complete_work TOCTOU
- **Medium Severity (15):** Operational impact across 7 modules
- **Low Severity (15):** Design improvements
- **Schema Violations (30):** Missing required Proto A fields
- **Type Violations (58):** Invalid message types
- **Expired Messages (53):** No cleanup process

---

## 4. SOP Effectiveness Analysis

### Current SOPs (13 total, SOP-011 through SOP-021)
| SOP | Title | Effectiveness | Issues |
|-----|-------|--------------|--------|
| 011 | Central Commander Launch | High | Core procedure, well-tested |
| 012 | Monitoring & Intervention | Medium | Reactive only, no proactive alerts |
| 013 | Prompt Engineering | High | Good patterns, anti-sequential rules |
| 014 | Simultaneous Project Mgmt | Medium | Overlaps with 011 |
| 015 | Infrastructure Audit | High | T27 validated this |
| 016 | Cross-Team Data Flow | Medium | Missing handoff protocol |
| 017 | Data Gathering Operations | High | Core data workflow |
| 018 | Market Research Operations | Low | **Never executed** — zero picks logged |
| 019 | Environment Setup | Medium | Basic setup only |
| 020 | Bug Fix Workflow | High | T25 proved effective |
| 021 | Failure Recovery Runbook | Medium | Never stress-tested in production |

### SOP Gaps Identified
1. **No team sizing SOP** — teams formed ad-hoc despite clear evidence on optimal sizes
2. **No handoff protocol** — gatherer-to-sorter handoff undefined
3. **No quality gate SOP** — data quality not validated between stages
4. **No SOP retirement process** — saturation risk with 13+ SOPs
5. **No domain work SOP** — all SOPs focus on coordination, none on actual output
6. **No anti-recursion SOP** — nothing prevents teams from building more coordination tools

---

## 5. Knowledge Transfer Analysis

### KB Health
- **29 entries** (KB-0001 through KB-0029)
- **1 gap entry** (KB-0011 — reconstructed placeholder)
- **98% template compliance** (T27 audit)
- **4 missing team session logs** (T15, T16, T17 only have KB entries, no session logs)

### Knowledge Chain
```
KB-0001 (Storage) → KB-0006 (Archive Tools) → KB-0009 (Multi-Team Ops)
                  → KB-0002 (Sonnet API) → KB-0003 (Stock Research)
                                         → KB-0004 (Data Gathering) → KB-0005 (Iteration 2)
                                                                    → KB-0014 (API Tools)
KB-0015/16/17 (Communication Design) → KB-0018 (Prototype) → KB-0020 (Live Test) → KB-0021 (Production Decision)
                                                             → KB-0019 (Architecture)
KB-0022 (Production Deployment) → KB-0023 (Dynamic Coordination) → KB-0024 (Stress Test/SOPs) → KB-0025 (Bug Fixes)
                                                                                               → KB-0026 (Audit)
                                                                                               → KB-0027 (SDK Launcher)
                                                                                               → KB-0028 (Deep Bugs)
```

### Knowledge Categories Distribution
- Integration: 10 entries (34%)
- Methodology: 10 entries (34%)
- Optimization: 2 entries (7%)
- Debugging: 2 entries (7%)
- Market Research: 1 entry (3%) — **severely underrepresented**
- Tool Usage: 1 entry (3%)
- Testing: 1 entry (3%)

---

## 6. Critical Risk: Inward-Facing Gravity

### Evidence
- Teams 22-28 (7 consecutive teams) all focused on coordination infrastructure
- Zero market research picks logged despite having SOP-018
- Zero new data gathering operations since T14
- Knowledge base is 34% integration, 34% methodology — infrastructure-heavy
- The system is building coordination tools to coordinate the building of more coordination tools

### Recommendation
**Immediately shift focus to domain work.** The coordination stack is mature enough. Future teams should:
1. Use existing tools, not build new ones
2. Execute SOP-018 (market research) for real
3. Produce domain artifacts (picks, analyses, reports)
4. Only modify coordination infrastructure for blocking bugs

---

## 7. Infrastructure Health Summary

### Working Well
- SQLite WAL concurrency (0.3ms message I/O)
- Heartbeat failure detection (120s window)
- Bus communication (fire-and-forget, no cascading failures)
- Atomic work claiming (BEGIN IMMEDIATE transactions)
- Template compliance (98%)

### Needs Attention
- 95 bus issues (30 schema violations, 58 type violations)
- No expired message cleanup process
- Missing SQLite database files (SharedState not initialized)
- 2 high-severity code bugs (data corruption, authorization bypass risks)
- Benchmark rate limiter misconfiguration

### SDK Migration Readiness
- 2 blocker risks (process lifecycle mismatch, launcher SPOF)
- 4 moderate risks (SQLite contention, JSONL atomicity, error propagation, dashboard accuracy)
- 6 low risks (documentation/monitoring items)
- Phased migration plan exists but unexecuted
