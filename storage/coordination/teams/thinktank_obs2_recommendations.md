# THINK-TANK-OBS-2: Programming Team Workflow & Collaboration Recommendations

**Observer**: THINK-TANK-OBS-2
**Date**: 2026-03-11
**Scope**: Programming teams (PROG-TEAM-1, PROG-TEAM-2, PROG-TEAM-3) and their collaboration with data gathering teams

---

## Executive Summary

After analyzing the coordination infrastructure (work_stealing.py, help_protocol.py, direct_channels.py), the multi-team session configuration, and all 40 existing scripts plus 1 API tool, I have identified structural gaps in how programming teams receive, prioritize, and deliver tool requests from data gatherers. The existing primitives are powerful but lack a purpose-built workflow layer connecting tool consumers (data gatherers) to tool producers (programming teams).

Key findings:
- The WorkStealing queue, HelpProtocol, and DirectChannels provide solid building blocks, but no standardized "tool request" flow exists on top of them.
- 13 data-collection scripts already exist (SCR-0006 through SCR-0028), but there is no feedback loop from gatherers back to programmers about what is missing or broken.
- Programming teams have predefined assignments (PROG-1 supports DATA-GATHER-DOCS-1/2, PROG-2 supports DATA-GATHER-ALGO/PRACTICES), but no dynamic rebalancing when one team's backlog overflows.

---

## 1. Programming Team Workflow

### 1.1 Intake Phase

Programming teams should begin each session by checking three sources for pending work, in this order:

1. **Work Queue (WorkStealing)** -- Check `get_stealable_work()` for items tagged with `tool-request` in the title prefix. These are formally submitted requests.
2. **Help Requests (HelpProtocol)** -- Call `get_open_help_requests()` filtered by `required_capabilities` containing `"coding"` or `"api-dev"`. These are urgent, in-flight needs.
3. **Direct Channel Messages** -- Read from assigned gatherer channels (e.g., `dg-docs-1-to-prog` for PROG-TEAM-1). These are informal requests and bug reports.

### 1.2 Triage and Sprint Planning

Each programming team should maintain a local scratchpad key `sprint-backlog` (namespace: their team ID) containing a JSON array of prioritized work items. The triage process:

```
1. Pull all new items from the three intake sources above
2. Assign each item a priority score (see Section 5 - Priority Matrix)
3. Merge into sprint-backlog, re-sorting by priority
4. Estimate effort for top 5 items (store as estimated_minutes)
5. Commit sprint plan to scratchpad with 4-hour TTL
6. Broadcast sprint plan on team's delivery channel
```

### 1.3 Development Cycle

For each tool or API being built:

```
Phase 1 - SPEC (10% of effort)
  - Read the tool request description
  - Write a 1-paragraph spec to the scratchpad under key "spec-{TOOL_ID}"
  - Send spec to requesting team's direct channel for confirmation
  - Wait max 5 minutes for feedback before proceeding

Phase 2 - BUILD (60% of effort)
  - Implement the tool following existing patterns:
    - Data collection scripts go in storage/scripts/data-gathering/methods/
    - API tools go in storage/api-tools/
    - Utility scripts go in storage/scripts/
  - Use request_cache.py (SCR-0022) for any HTTP-based tools
  - Use ddg_utils.py (SCR-0021) as fallback for any web search

Phase 3 - TEST (20% of effort)
  - Run tool with at least 2 different queries
  - Verify output format matches what gatherer teams expect
  - Check error handling (network failure, empty results, malformed data)

Phase 4 - DELIVER (10% of effort)
  - Register in appropriate index.json
  - Broadcast completion on delivery channel and global bus
  - Write scratchpad entry with usage example (namespace: "global", key: "tool-usage-{TOOL_ID}")
  - Complete the work item via complete_work() or fulfill_help()
```

### 1.4 Ongoing Maintenance

Programming teams should reserve 20% of capacity for:
- Bug fixes on delivered tools (reported via help requests)
- Performance improvements flagged by think tank
- Dependency updates (e.g., API endpoint changes)

---

## 2. Live Collaboration Protocol

### Step-by-Step Process for Data Gatherers Requesting Tools in Real-Time

**For the requesting data gatherer team:**

```
STEP 1: Check if a tool already exists
  - Search storage/scripts/index.json categories "data-collection"
  - Search storage/api-tools/index.json
  - Use kb_search.py (SCR-0018) with relevant query terms

STEP 2: Submit a formal tool request
  - Use WorkStealing.enqueue_work() with:
    - title: "TOOL-REQ: {short description}"
    - description: JSON string containing the Tool Request Format (Section 3)
    - priority: 1 (critical), 3 (high), 5 (normal), 7 (low)

STEP 3: Notify the assigned programming team
  - Send a direct channel message via DirectChannels.send_direct()
    to the assigned prog team with msg_type "request"
    and body: {"event": "tool-request", "work_id": <id from step 2>}

STEP 4: Monitor progress
  - Poll the Scratchpad for key "spec-{WORK_ID}" (namespace: prog team)
    to see the spec when available
  - Respond to spec confirmation requests on the direct channel

STEP 5: Receive delivery
  - Watch the global bus for "work-completed" events matching your work_id
  - Read the tool usage example from scratchpad key "tool-usage-{TOOL_ID}"
  - Begin using the tool immediately
```

**For the programming team receiving the request:**

```
STEP 1: Acknowledge receipt
  - Send direct channel message back with msg_type "response"
    and body: {"event": "tool-request-ack", "work_id": <id>, "eta_minutes": <estimate>}

STEP 2: Claim the work item
  - Call WorkStealing.steal_work() or explicitly claim via SQL
  - Update HelpProtocol status to "working"

STEP 3: Spec confirmation loop
  - Write spec to scratchpad
  - Send spec notification to requesting team
  - If no response in 5 minutes, proceed with best understanding

STEP 4: Build and deliver
  - Follow Development Cycle (Section 1.3)

STEP 5: Handoff
  - Complete the work item
  - Send direct channel message with delivery details
  - Remain available for 15 minutes for follow-up questions
```

### Escalation Path

If no programming team responds within 10 minutes:
1. Data gatherer sets team status to "needs_help" via HelpProtocol.update_status()
2. HelpProtocol.auto_assign_idle_teams() will attempt to assign any available team
3. If still unresolved after 20 minutes, broadcast on global channel requesting PROG-TEAM-3 (infrastructure team) to intervene

---

## 3. Tool Request Format

All tool requests should use this standardized JSON format in the `description` field of WorkStealing.enqueue_work():

```json
{
  "format_version": "1.0",
  "request_type": "new_tool | enhancement | bug_fix | api_wrapper",
  "requesting_team": "DATA-GATHER-DOCS-1",
  "requesting_agent": "agent-id-here",

  "tool_spec": {
    "name": "Short tool name (lowercase-hyphenated)",
    "purpose": "One sentence: what does this tool do?",
    "data_source": "URL or API name (e.g., 'PubMed API', 'StackOverflow')",
    "input_format": {
      "query": "string - search query",
      "max_results": "int - max results to return (default 20)",
      "filters": "object - optional filters (date range, category, etc.)"
    },
    "expected_output_format": {
      "results": [
        {
          "title": "string",
          "url": "string",
          "snippet": "string (first 500 chars)",
          "source": "string (domain or API name)",
          "date": "ISO-8601 or null",
          "metadata": "object - source-specific fields"
        }
      ],
      "query": "string - the original query",
      "source": "string - method identifier",
      "timestamp": "ISO-8601"
    },
    "error_handling": "What should happen on failure? (fallback, retry, empty result)",
    "rate_limits": "Known rate limits for the target API",
    "auth_required": "none | api-key | oauth",
    "existing_tools_to_reference": ["SCR-XXXX IDs of similar tools to use as patterns"]
  },

  "urgency": "blocking | high | normal | low",
  "blocking_reason": "If urgency=blocking, explain what is stalled",
  "context": "Why is this tool needed? What data gap does it fill?"
}
```

### Minimal Request (for quick informal requests)

For small requests sent via direct channel (not formal work queue), use:

```json
{
  "request_type": "quick",
  "name": "tool-name",
  "purpose": "One sentence",
  "data_source": "URL or API",
  "urgency": "normal"
}
```

---

## 4. Delivery Pipeline

### 4.1 Pre-Delivery Checklist

Before marking a tool as complete, the programming team must verify:

| Check | Description | Pass Criteria |
|-------|-------------|---------------|
| Runs without error | Execute with a test query | Exit code 0, valid JSON output |
| Output format match | Compare output structure to spec | All required fields present |
| Error handling | Test with empty query, invalid input | Graceful failure, no crash |
| Cache integration | Uses request_cache.py if HTTP-based | Cache hit returns same result |
| Rate limiting | Respects target API limits | Includes sleep/backoff logic |
| Index registration | Added to appropriate index.json | Entry present with correct metadata |
| Documentation | Usage example in scratchpad | Example query and expected output |

### 4.2 Delivery Notification Flow

```
Programming Team                    Bus                    Data Gatherer
      |                              |                          |
      |-- complete_work(id, result) ->|                          |
      |                              |-- "work-completed" ------>|
      |                              |                          |
      |-- scratchpad.write(          |                          |
      |     "tool-usage-TOOL_ID",    |                          |
      |     usage_example,           |                          |
      |     namespace="global")      |                          |
      |                              |                          |
      |-- send_direct(               |                          |
      |     gatherer_team,           |                          |
      |     "response",              |                          |
      |     {"event":"tool-delivered",|                          |
      |      "tool_id": "SCR-XXXX",  |                          |
      |      "index_path": "...",    |                          |
      |      "usage_key": "..."})  ->|------------------------->|
      |                              |                          |
      |                              |<-- "tool-feedback" ------|
      |<-----------------------------|                          |
```

### 4.3 Post-Delivery Feedback

Data gatherers should send feedback within 30 minutes of delivery via direct channel:

```json
{
  "event": "tool-feedback",
  "tool_id": "SCR-XXXX",
  "status": "works | partial | broken",
  "issues": ["list of specific issues if any"],
  "quality_score": 1-5,
  "additional_needs": "any follow-up requests"
}
```

If `status` is "broken", automatically escalate by creating a new work item with priority 1 (critical) referencing the original tool request.

### 4.4 Tool Versioning

When updating an existing tool:
- Increment the version in the script header comment
- Update the index.json entry's description if functionality changed
- Write a changelog entry to scratchpad key `changelog-{TOOL_ID}` (namespace: "global")
- Notify all teams that previously used the tool via broadcast channel

---

## 5. Priority Matrix

### 5.1 Scoring System

Each tool request receives a composite priority score (lower = higher priority):

| Factor | Weight | Scoring |
|--------|--------|---------|
| **Urgency** | 40% | blocking=1, high=3, normal=5, low=8 |
| **Team Impact** | 25% | How many gatherer teams need this? (1 team=5, 2 teams=3, 3+=1) |
| **Data Gap** | 20% | Does this fill a coverage gap in sources? (yes=2, nice-to-have=5, redundant=8) |
| **Effort** | 15% | Estimated build time (<30min=2, 30-60min=4, 1-2hr=6, 2hr+=8) |

**Composite Score** = (urgency * 0.4) + (team_impact * 0.25) + (data_gap * 0.2) + (effort * 0.15)

### 5.2 Priority Tiers

| Tier | Score Range | Action | SLA |
|------|-------------|--------|-----|
| P0 - Critical | 1.0 - 2.0 | Drop everything, build immediately | 30 minutes |
| P1 - High | 2.1 - 3.5 | Next item in sprint | 2 hours |
| P2 - Normal | 3.6 - 5.5 | Queue for current session | 4 hours |
| P3 - Low | 5.6 - 8.0 | Backlog for next session | Best effort |

### 5.3 Automatic Priority Escalation

- Any P2 item not started within 2 hours escalates to P1
- Any P1 item not started within 1 hour escalates to P0
- P0 items not claimed within 15 minutes trigger PROG-TEAM-3 (infra) auto-assignment via `WorkStealing.reclaim_abandoned_work(timeout_seconds=900)`

### 5.4 Cross-Team Load Balancing

When a programming team's queue depth exceeds 5 items:
1. The overloaded team broadcasts a "capacity-exceeded" event on the global bus
2. Other programming teams with queue depth < 3 should call `steal_work()` to take items
3. PROG-TEAM-3 acts as overflow handler when both PROG-TEAM-1 and PROG-TEAM-2 are saturated

Current team assignments (from multi_team_session.json):
- **PROG-TEAM-1** -> DATA-GATHER-DOCS-1, DATA-GATHER-DOCS-2
- **PROG-TEAM-2** -> DATA-GATHER-ALGO, DATA-GATHER-PRACTICES
- **PROG-TEAM-3** -> Think tank infrastructure requests + overflow

---

## 6. Identified Gaps and Recommended New Tools

Based on the existing 13 data-collection scripts (SCR-0006 through SCR-0028), the following sources are NOT yet covered and should be prioritized:

| Missing Source | Priority | Assigned To | Rationale |
|---------------|----------|-------------|-----------|
| StackOverflow API | P1 | PROG-TEAM-2 | Best practices team needs Q&A data |
| PubMed / bioRxiv | P2 | PROG-TEAM-1 | Documentation team needs scientific literature beyond arXiv |
| Reddit API | P2 | PROG-TEAM-2 | Community discussions on algorithms and practices |
| PyPI / npm package metadata | P2 | PROG-TEAM-1 | Framework documentation needs package ecosystem data |
| CrossRef / DOI resolution | P3 | PROG-TEAM-1 | Complement existing academic tools with citation resolution |

### Recommended Infrastructure Improvements (for PROG-TEAM-3):

1. **Unified Data Gatherer CLI** -- A single entry point that wraps all 13+ data-collection methods with consistent argument parsing and output format. This would eliminate the need for gatherers to know which specific script to call.

2. **Tool Health Dashboard** -- Extend the existing dashboard (SCR-0032) with a "tools" tab showing: last successful run, average latency, failure rate per data-collection script.

3. **Auto-Retry Wrapper** -- A decorator/wrapper that adds automatic retry with exponential backoff to any data-collection method, since several (arXiv, Semantic Scholar, GitHub) have rate limits that cause intermittent failures.

---

## 7. Coordination Infrastructure Usage Recommendations

### Use the Right Primitive for the Right Purpose

| Scenario | Use This | Not This |
|----------|----------|----------|
| Formal tool request | WorkStealing.enqueue_work() | Direct channel message |
| Quick bug report | DirectChannels.send_direct() | Work queue |
| "I'm stuck, need coding help" | HelpProtocol.request_help() | Global broadcast |
| "Tool X is ready" | WorkStealing.complete_work() + bus notify | Scratchpad only |
| Sharing intermediate results | Scratchpad.write() (namespace="global") | Direct messages |
| Checking who is available | DirectChannels.get_available_teams() | Polling all channels |

### Scratchpad Namespaces Convention

| Namespace | Purpose | TTL |
|-----------|---------|-----|
| `global` | Cross-team shared data (tool specs, usage examples) | 4 hours |
| `{team-id}` | Team-internal sprint plans, WIP | 2 hours |
| `tool-specs` | Active tool request specifications | 1 hour |
| `tool-feedback` | Post-delivery feedback aggregation | 4 hours |

---

## 8. Metrics to Track

The following metrics should be tracked by the dashboard or a periodic monitoring script:

1. **Request-to-Delivery Time** -- Mean time from tool request to delivery (target: <2 hours for P1)
2. **First-Response Time** -- Mean time from request to acknowledgment (target: <10 minutes)
3. **Tool Reuse Rate** -- How many teams use each delivered tool (target: >1.5 teams per tool)
4. **Queue Depth per Team** -- Real-time backlog size (alert threshold: >5)
5. **Feedback Score** -- Average quality_score from post-delivery feedback (target: >3.5/5)
6. **Escalation Rate** -- Percentage of requests that auto-escalate priority (target: <20%)

---

*End of THINK-TANK-OBS-2 Recommendations*
*Next review scheduled: After first programming sprint cycle completes*
