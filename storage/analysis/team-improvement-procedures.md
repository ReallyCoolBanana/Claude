# Team Improvement Procedures
**Date:** 2026-03-11
**Based on:** ANALYSIS-001 (Cross-Team Analysis of T01-T28)
**Applies to:** All future team sessions (T29+)

---

## Procedure 1: Pre-Launch Planning Protocol

### Purpose
Prevent the recurring issues of oversized teams, duplicate work, and inward-facing gravity before agents are launched.

### Steps

1. **Define domain deliverables first**
   - Before thinking about team size or composition, write down exactly what domain artifacts this session should produce.
   - Examples: "3 market research picks", "data analysis report on sector X", "2 new API tools for external data"
   - If you cannot name a domain deliverable, reconsider whether the session is necessary.

2. **Apply SOP-022 Team Sizing**
   - Use the decision flowchart in SOP-022 Section 3
   - Default to 5 agents unless evidence supports more
   - Never exceed 12 agents without written justification

3. **Apply SOP-023 Anti-Recursion Check**
   - Count planned agents by category: domain vs infrastructure vs overhead
   - Verify: domain ≥ 60%, infrastructure ≤ 30% (max 3 agents), overhead ≤ 10%
   - If launching infrastructure work, document which domain task it unblocks

4. **Pre-load the work queue**
   - Before launching, add 3-5 fallback tasks to the WorkStealing queue
   - These should be domain tasks any agent can pick up when their primary work completes
   - T27's zero idle time came from having work ready to steal

5. **Run SOP-013 QA Checklist**
   - Every agent prompt passes QA-01 through QA-12
   - Output path allocation table created and verified for no conflicts

---

## Procedure 2: Session Execution Monitoring

### Purpose
Catch issues within 2 minutes instead of discovering them at session end.

### Steps

1. **Use team-size-aware polling from SOP-012 v2**
   - Small teams (1-3): check at 15s, 2min, 40% elapsed
   - Medium teams (4-9): check at 30s, 2min, 5min, 40% elapsed
   - Large teams (10+): check at 30s, 2min, 5min, 8min, 30% elapsed

2. **Track velocity, not just progress**
   - A team at 30% after 5 minutes is healthy
   - A team at 30% after 25 minutes is stalled
   - Threshold: < 1% per minute for 5+ minutes = immediate intervention

3. **Use automatic escalation timers**
   - 3 minutes after blocker reported: auto-trigger HelpProtocol
   - 1 minute after completion: auto-reassign via WorkStealing
   - At 50% session time: mandatory progress review

4. **Watch for recursion drift**
   - If any agent's current_task mentions coordination/protocol/SOP/infrastructure and they weren't assigned infra work → redirect immediately

---

## Procedure 3: Handoff and Quality Gate Enforcement

### Purpose
Ensure data quality doesn't degrade as it flows through the pipeline.

### Steps

1. **Gatherer self-assessment before handoff**
   - Apply G1 quality gate from SOP-016 v2
   - Minimum score: 0.6 (60%)
   - All manifest fields populated
   - No empty content items

2. **Sorter validation on receipt**
   - Apply G2 quality gate
   - Minimum score: 0.7 (70%)
   - Deduplicate against existing archive using content fingerprinting
   - Reject and return non-conforming batches

3. **Think tank spot-check**
   - Apply G3 periodic validation
   - Every 30 minutes or 50 items, whichever first
   - Random sample of 5 items for accuracy
   - Flag systematic quality issues

---

## Procedure 4: Post-Session Review and Knowledge Capture

### Purpose
Extract maximum learning from each session and enforce the knowledge transfer prime directive.

### Steps

1. **Immediate post-session (within 5 minutes of completion)**
   - Count domain artifacts produced
   - Count infrastructure changes made
   - Record actual domain/infra/overhead split
   - Flag if domain < 60% → next session must be domain-focused

2. **Session log creation**
   - Use teams/TEAM_LOG_TEMPLATE.md
   - Include: objectives, outcomes, metrics, problems, solutions, lessons
   - Must include agent count, team size chosen, and whether SOP-022 sizing was followed

3. **Knowledge base entry creation**
   - Create KB entry for any finding that future teams need
   - Set builds_on to reference prior entries being extended
   - Update knowledge-base/index.json

4. **SOP effectiveness feedback**
   - Note which SOPs were used and whether they helped
   - Note any failures that no SOP covers → candidate for SOP-024 review
   - Do NOT create a new SOP unless it passes SOP-024 CREATE-01 through CREATE-04

5. **Metrics for trend tracking**
   - Add to storage/analysis/team-metrics-archive.json:
     - Team ID, date, agent count, domain/infra/overhead split
     - Artifacts produced, bugs found/fixed, idle time percentage
     - SOPs referenced, violations observed

---

## Procedure 5: Continuous Improvement Cycle

### Purpose
Structured improvement cadence that prevents both stagnation and over-optimization.

### Cadence
- **Every session:** Post-session review (Procedure 4)
- **Every 5 sessions:** SOP review per SOP-024 Section 2
- **Every 10 sessions:** Full cross-team analysis (re-run ANALYSIS-001 methodology)
- **On demand:** When a novel failure mode is encountered twice

### Improvement Categories (Prioritized)

1. **Domain output improvement** (highest priority)
   - Are we producing domain artifacts? Are they high quality?
   - Is the market research pipeline being executed?
   - Are data gathering tools being used?

2. **Efficiency improvement** (medium priority)
   - Is idle time decreasing?
   - Are team sizes appropriate for tasks?
   - Is coordination overhead within expected ranges?

3. **Quality improvement** (medium priority)
   - Are quality gates catching issues?
   - Is KB template compliance maintained at 98%+?
   - Are bugs being found proactively?

4. **Infrastructure improvement** (lowest priority — only when blocking domain work)
   - Only fix blocking bugs
   - Only add features that domain teams requested
   - Apply SOP-023 FREEZE rules

---

## Procedure 6: New Team Onboarding

### Purpose
Ensure new teams start productive immediately by leveraging all accumulated knowledge.

### Steps (Required for every new session)

1. **Read the latest 3 team session logs**
   - `teams/sessions/TEAM-0027.md`, `TEAM-0028.md`, and the most recent
   - This fulfills the Prime Directive: "Every AI team builds on the knowledge of the last team"

2. **Read the cross-team analysis**
   - `storage/analysis/cross-team-analysis-2026-03-11.md`
   - Understand optimal team sizes, known risks, and infrastructure health

3. **Check the improvement procedures (this document)**
   - Understand the pre-launch, execution, and post-session protocols

4. **Review relevant SOPs for the planned work**
   - Domain work: SOP-017 (data gathering), SOP-018 (market research)
   - Team operations: SOP-011 (launch), SOP-022 (sizing), SOP-023 (anti-recursion)
   - Monitoring: SOP-012 v2 (monitoring), SOP-013 (prompts)

5. **Start work within 5 minutes of session start**
   - Context reading should take 3-5 minutes, not 15-20
   - Use the pre-loaded work queue to start producing immediately

---

## Quick Reference Card

```
PRE-LAUNCH:
  □ Domain deliverables defined
  □ Team size per SOP-022 (default: 5)
  □ Anti-recursion check per SOP-023 (domain ≥ 60%)
  □ Work queue pre-loaded with fallback tasks
  □ Prompts pass SOP-013 QA checklist

EXECUTION:
  □ Polling per SOP-012 v2 (team-size-aware)
  □ Velocity tracking (< 1%/min = intervene)
  □ Auto-escalation timers set
  □ Recursion drift monitoring

POST-SESSION:
  □ Domain artifacts counted
  □ Actual domain/infra/overhead split recorded
  □ Session log created
  □ KB entries created
  □ Metrics added to archive
```
