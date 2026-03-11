# Think Tank 3 (TT-3) Findings: Productivity Trajectory Across 27 Teams

**Analyst:** TT-3 (Productivity & Maturity Analysis)
**Date:** 2026-03-11
**Focus:** Teams 0023-0027, with full historical context from Teams 0001-0022
**Verdict:** YES - Productivity has improved. Trajectory: Accelerating (with caveats).

---

## 1. Code Output Per Agent Over Time

### Raw Lines-Per-Agent Calculations

| Team | Agents | Code Output | Lines/Agent | Type of Work |
|------|--------|-------------|-------------|--------------|
| T01 | ~16 | Validation scripts + findings | ~30 (tooling) | Stress testing, validation |
| T05 | 1 | 2 methods + benchmark runner | ~400 | Pure coding |
| T09 | 8 | Go tools + Python tools + fixes | ~250 | Mixed build + verify |
| T14 | 9 | 6 API methods | ~150 | Templated coding |
| T22 | 18 | 1,997 lines (4 modules) | **111** | Production infra |
| T23 | 20 | 1,964 lines (3 modules) + 80 tests | **98** | Design + implementation |
| T24 | 8 | 2,847 test lines + 10 SOPs | **356** | Testing + documentation |
| T25 | 15 | 612 lines + 45 tests + 3 SOPs + 6 bug fixes | **44** (code) / **~80** (all artifacts) | Bug fixes + new module + SOPs |
| T26 | 1 | 12 registrations + 6 KB fixes | N/A (maintenance) | Data quality |
| T27 | 5 | Full audit + KB-0026 | **0 code** / high analytical value | Audit |

### Interpretation

Raw lines-per-agent is a misleading metric in isolation. Key observations:

1. **T24 had the highest lines/agent (356)** because stress tests are code-dense by nature (setup, assertions, teardown). This doesn't mean T24 was "most productive" -- it means testing generates more lines per insight than design work.

2. **T23 appears efficient at 98 lines/agent** but this understates its output. The 20 agents included 8 think-tank agents who produced ~86KB of design documents that directly shaped the 1,964 lines of code. The real metric is: 3 production modules with 80 passing tests from a single session.

3. **T25's low lines/agent (44-80)** masks its highest-impact work: fixing 6 bugs (including 2 critical race conditions), building coordinator_hub.py, and producing 3 SOPs that codified anti-sequential execution. Bug fixes have outsized value relative to line count.

4. **T27 produced zero new code** but delivered the most comprehensive assessment in repo history -- in 90 seconds. This is a pure efficiency win from mature coordination tooling.

### The Real Trend

Measuring "value per agent" rather than "lines per agent":

- **Early teams (T01-T09):** High line counts but frequent rework. T01 left 16 orphan files. T09's coordinator had read-only access to prevent conflicts (a workaround for missing coordination).
- **Mid teams (T14-T22):** Higher ambition, but T22 exposed the idle-team problem -- 5 teams sat waiting while Beta-1 processed. Lines/agent was decent (~111) but utilization was poor.
- **Late teams (T23-T27):** Lower lines/agent but dramatically higher impact-per-agent. Bug detection (T25), audit completion in 90s (T27), and structured knowledge transfer all represent compounding returns.

---

## 2. Bug Detection Rate

### T25: 14 Bugs Found Across 15 Agents

- 3 critical (bus crashes callers, TOCTOU race, double-assignment)
- 5 high severity (no validation, no auth, no closed-state tracking, self-help, zombie items)
- 5 medium (falsy evaluation, missing validation, no ownership checks)
- 1 low (missing functools.wraps)

**Detection rate: 0.93 bugs found per agent.**

### Comparison to Earlier Teams

| Team | Bug/Issue Finding | Agents | Rate |
|------|-------------------|--------|------|
| T01 | 16 orphan files, schema drift | 16 | ~1.0 issues/agent |
| T09 | Race condition in rate limiter, staleness issues | 8 | ~0.5 issues/agent |
| T22 | Benchmark config bug (critical discovery via cross-team analysis) | 18 | ~0.06 bugs/agent |
| T24 | 31 failure modes identified | 8 | 3.9 failure modes/agent |
| T25 | 14 bugs found, 6 fixed | 15 | 0.93 bugs/agent |

### Analysis

T24-T25 represent a qualitative leap in bug detection. T24 found 31 failure modes because it was *designed* to find them (stress testing mission). T25 then validated and fixed the highest-priority issues. The key insight: **structured bug hunting (think-tank-bugs teams) finds more bugs than incidental discovery.** T22 found 1 critical bug through cross-team serendipity; T25 found 14 through deliberate hunting.

The coordination system enables this: dedicated bug-hunting teams can run in parallel with fix-implementing teams, with the think tanks feeding findings directly to coders.

---

## 3. SOP Production and Knowledge Transfer

### SOP Timeline

| Session | SOPs Produced | Scope |
|---------|---------------|-------|
| T24 | SOP-001 through SOP-010 | Tool usage, onboarding, data preservation |
| T25 | SOP-011, SOP-012, SOP-013 | Central commander launch, monitoring, prompt engineering |

### Did SOPs Reduce Repeated Mistakes?

**Evidence for YES:**

1. **T24's research finding:** Structured handoff checklists improve AI team knowledge transfer by 40-60%. T25 immediately applied this -- its session log is the most structured in the repo, with explicit bug IDs, fix patterns, and remaining work.

2. **T27 achieved 98% KB template compliance** across 25 entries. This is a direct result of SOP-010 (onboarding) and SOP-013 (prompt engineering) providing explicit templates and checklists.

3. **Anti-sequential correction:** T25 notes that the user "corrected the coordinator's initial sequential plan." The fact that SOP-011 was then created with 10 explicit Anti-Sequential Rules (ASR-001 through ASR-010) is a direct encoding of a learned lesson. Future teams now have a decision tree: "Does Task B require a specific computed artifact that only Task A can produce?" If no, run parallel.

4. **T27 completed a full-repo audit in 90 seconds** using 4 parallel teams with zero cross-dependencies. This execution pattern directly follows SOP-011's task decomposition rules.

**Evidence for CAUTION:**

1. SOPs are only 2 teams old. We have T25, T26, T27 as post-SOP teams. T26 was a single agent (no coordination needed). T27 used the patterns. Sample size is small.

2. The "40-60% improvement" claim from T24's research is based on external literature, not measured within this repo. We have no controlled experiment.

3. SOPs can calcify. 13 SOPs plus 10 anti-sequential rules is already a substantial process overhead. If teams spend more time reading SOPs than doing work, the investment turns negative.

### Verdict on SOPs

**Net positive, with diminishing returns ahead.** The first 13 SOPs addressed real failure modes (idle teams, sequential execution, missing validation). But the repository is approaching SOP saturation. Future SOPs should be created only for genuinely novel failure modes, not as a reflex.

---

## 4. Anti-Sequential Execution (SOP-011)

### Before SOP-011

- **T09 (coordinator era):** Used ad-hoc parallel coordination. Coordinator had read-only access. Teams ran in parallel but with manual routing.
- **T22 (Proto A first deployment):** 6 teams launched simultaneously, but the idle-team problem emerged. When Beta-1 was slow, 5 teams waited. No mechanism to reassign or redistribute.
- **T23:** 7 teams simultaneous, but standby team sat idle (the impetus for designing the help protocol).

### After SOP-011

- **T25:** User had to correct the coordinator's initial sequential plan. Three batches instead of one, but within each batch, all teams ran simultaneously. Completed teams were reassigned.
- **T27:** 4 teams, all parallel, no batches, completed in 90 seconds. Clean anti-sequential execution.

### Measurable Impact

The progression from T22 to T27 shows:
- T22: 18 agents, unknown duration, idle-team problem
- T23: 20 agents, help protocol designed to solve idle problem
- T25: 15 agents, explicit anti-sequential enforcement, reassignment of completed teams
- T27: 5 agents, 90 seconds, zero idle time

**T27 is the proof point.** A 5-agent team completing a full repository audit in 90 seconds with zero idle time represents near-optimal utilization. The anti-sequential rules worked here because:
1. Tasks had no cross-dependencies (each team audited a different subsystem)
2. SOP-011's "Hard Dependency Test" was trivially satisfied
3. The team was small enough that coordination overhead was minimal

### Caveat

Anti-sequential execution is easier with smaller teams (5 agents) than larger ones (20 agents). T23 and T25 still had coordination overhead from think-tank-then-code pipelines. SOP-011 correctly identifies when parallel execution is safe, but the practical throughput gain depends on task decomposability.

---

## 5. The Maturity Curve

### Phase 1: Ad Hoc (Teams 1-8)
- **Coordination:** None to minimal. Teams used git worktrees for isolation. No inter-agent communication.
- **Knowledge transfer:** KB entries created but no structured handoff process.
- **Team sizes:** Variable (1-16 agents). Single-agent teams (T05, T06) were common.
- **Characteristic:** Agents worked independently. Duplication was common (T10-T13 were near-identical research missions that could have been one team).
- **Productivity:** Moderate. Good individual output but significant waste from isolation.

### Phase 2: Coordinator Era (Teams 9-14)
- **Coordination:** Dedicated coordinator agent. Read-only access to team outputs. Manual routing of findings.
- **Knowledge transfer:** Coordinator produced cross-team synergy reports. KB entries became more structured.
- **Team sizes:** 8-9 agents with coordinator. Two-phase operations (research then build).
- **Characteristic:** The coordinator bottleneck. One agent trying to route information between 8+ teams. Better than nothing but not scalable.
- **Productivity:** Higher per-team but the coordinator was a single point of failure.

### Phase 3: Proto A Communication (Teams 18-22)
- **Coordination:** JSONL bus + SQLite shared state. Agents could publish/subscribe to channels.
- **Knowledge transfer:** Bus messages, shared state tables, output file conventions.
- **Team sizes:** 4-18 agents. First deployment of 18 agents (T22).
- **Characteristic:** First real inter-agent communication. T22 made the critical cross-team discovery (benchmark config bug) that would have been impossible without shared state. But also exposed the idle-team problem.
- **Productivity:** Step change improvement. T22 produced 1,997 lines of production code plus 157KB of structured analysis.

### Phase 4: Full Coordination Stack (Teams 23-27)
- **Coordination:** Help protocol + direct channels + work stealing + coordinator hub + SOPs + anti-sequential rules.
- **Knowledge transfer:** 13 SOPs, structured handoff checklists, KB template compliance at 98%.
- **Team sizes:** 1-20 agents. Wide variance reflecting task-appropriate sizing.
- **Characteristic:** Rich tooling but increasing process overhead. The coordination system itself became a major work product (5,267 lines of tests alone).
- **Productivity:** High-impact work with lower raw output. The system is now self-aware (T27's audit).

### Trajectory Assessment

```
Phase 1 (T1-T8):   ████░░░░░░  Ad hoc, moderate output, high waste
Phase 2 (T9-T14):  ██████░░░░  Coordinator-mediated, less waste, bottleneck
Phase 3 (T18-T22): ████████░░  Bus communication, cross-team discovery, idle problem
Phase 4 (T23-T27): █████████░  Full stack, high impact, process overhead emerging
```

**The trajectory is accelerating in capability but showing early signs of plateauing in raw output.**

The system can now do things that were impossible in Phase 1 (cross-team bug hunting, 90-second audits, structured knowledge transfer). But it's also spending more cycles on self-improvement (building coordination tools, writing SOPs, auditing itself) and fewer on external-facing work (no new data-gathering tools since T14, no market research picks logged despite having the system).

---

## 6. Overall Verdict

### Has productivity improved over 27 teams?

**YES.** The evidence is clear:

1. **Qualitative capability leap:** T27's 90-second full-repo audit with 5 agents is impossible without the coordination stack. T22's cross-team benchmark discovery is impossible without Proto A.

2. **Bug detection matured:** From incidental discovery (T01, T09) to systematic hunting (T24-T25). 14 bugs found and triaged in a single session.

3. **Knowledge compounds:** 98% template compliance, 25 KB entries, 13 SOPs. Each team starts with more context than the last.

4. **Team sizing improved:** From arbitrary (T03: 25+ agents for research) to deliberate (T27: 5 agents for audit). SOP-011 provides decision trees for team composition.

### Is the trend sustainable?

**MIXED.** Three risks:

1. **Inward-facing gravity:** Teams 22-27 spent most of their effort improving the coordination system itself. This is valuable infrastructure investment, but the repository risks becoming a coordination system that coordinates the building of more coordination systems. T27's audit found zero market research picks logged -- the actual use case for all this infrastructure remains untested.

2. **Process overhead:** 13 SOPs + 10 ASR rules + 5-checkpoint monitoring + 12-item QA checklists. A 5-agent team can absorb this. A 20-agent team may spend more time on process compliance than productive work. The coordination system needs to be invisible, not a burden.

3. **Diminishing returns on self-improvement:** The gap between T22 (basic bus) and T23 (help protocol + work stealing) was a major capability jump. The gap between T25 (full stack) and T27 (audit) was primarily about using existing tools effectively. The easy wins are captured. Future gains require harder problems (actual multi-process coordination, real distributed workloads, external API integration under load).

### Recommended Path Forward

1. **Declare the coordination stack "done enough"** and shift focus to using it for external-facing work (market research, data analysis, API tool development).
2. **Cap team size at 8-10 agents** for most operations. T24 (8 agents, 356 lines/agent) and T27 (5 agents, 90s completion) show that smaller, focused teams outperform large ones.
3. **Run a market research operation** using the full coordination stack. This would be the first real test of whether the infrastructure translates to domain output.
4. **Freeze SOP creation** unless a genuinely novel failure mode is encountered. 13 SOPs is sufficient for current operations.
5. **Measure utilization, not output.** The key metric going forward is: what percentage of agent-seconds are spent on productive work vs. coordination overhead vs. idle time?

---

## 7. Key Statistics Summary

| Metric | Value |
|--------|-------|
| Total teams analyzed | 27 |
| Total agents deployed (historical) | 130+ |
| Coordination code lines | ~2,576 (modules) + 5,267 (tests) |
| SOPs produced | 13 |
| Bugs found (T24-T25) | 14 + 31 failure modes |
| KB entries | 25+ |
| Fastest operation | T27: 90 seconds, 5 agents, full audit |
| Largest operation | T22: 18 agents, 6 teams, 1,997 lines + 157KB output |
| Lines/agent range | 44-356 (varies dramatically by task type) |
| Recommended team size | 5-10 agents |
| Maturity trajectory | Accelerating capability, plateauing raw output |
| Sustainability | Sustainable IF focus shifts from self-improvement to domain work |
