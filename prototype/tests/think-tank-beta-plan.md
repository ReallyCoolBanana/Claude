# Think Tank Beta: Coordination Strategy for Live Data Gathering Tests

> **Team:** Think Tank Beta (Lead + 2 subagents)
> **Date:** 2026-03-11
> **Inputs:** KB-0018, KB-0019, Proto A/B coordinator.py, Proto A state.py, SOPs-prototype-testing.md
> **Prototype:** A (JSONL + SQLite WAL) -- production candidate per KB-0018 recommendation

---

## 1. Coordinator Role Design

### 1.1 Responsibilities During Data Gathering

The coordinator is the single authority for phase management, work distribution, and result aggregation. It does NOT gather data itself. Its duties:

| Duty | Mechanism | Frequency |
|------|-----------|-----------|
| Phase management | `advance_phase()` + SQLite `phase_signals` | On gate conditions met |
| Work distribution | SQLite `work_assignments` table + bus directives | At phase 1, on rebalance |
| Heartbeat monitoring | `get_dead_agents()` on SQLite `agents` table | Every 30s (heartbeat loop) |
| Rate limit enforcement | SQLite `rate_limit_config` + `reserve_api_call()` | Passive (agents query) |
| Blocker escalation | Poll `topic-blockers` channel | Every heartbeat cycle |
| Result aggregation | Read `topic-results` channel + validate | At phase 3-4 |
| Benchmark recording | Write metrics JSON to disk | At phase 4 completion |

### 1.2 Coordinator State Machine

```
                          all agents          all sources         validation
                          registered          report done         passes
    +-------+  --------> +---------+ ------> +----------+ -----> +----------+
    | INIT  |            | PHASE_1 |         | PHASE_2  |        | PHASE_3  |
    | setup |            | assign  |         | gather   |        | validate |
    +-------+            +---------+         +----------+        +----------+
        |                    |                    |                    |
        |                    |                    |                    | dedup +
        |                    |                    |                    | quality ok
        |                    |                    |                    v
        |                    |                    |               +----------+
        |                    |                    |               | PHASE_4  |
        |                    |                    |               | aggregate|
        |                    |                    |               +----------+
        |                    |                    |                    |
        |                    v                    v                    v
        |               +---------+          +---------+         +---------+
        +-------------->| ABORT   |<---------|  ABORT  |<--------|  DONE   |
          fatal error   +---------+ timeout  +---------+         +---------+
                             or agent death
                             exceeds threshold

    Transitions:
    ============
    INIT     --[all agents registered & state.db ready]--> PHASE_1
    PHASE_1  --[all work_assignments ACK'd on bus]-------> PHASE_2
    PHASE_2  --[all sources report status=done|failed]---> PHASE_3
    PHASE_3  --[validation complete, dedup done]---------> PHASE_4
    PHASE_4  --[benchmark JSON written]------------------>  DONE
    ANY      --[fatal error | >50% agents dead]----------> ABORT
```

### 1.3 Detailed State Transitions

```
State: INIT
  Entry actions:
    - Create coordination directory (bus/, state.db)
    - Configure rate limits in rate_limit_config table
    - Register self in agents table
    - Start heartbeat loop thread
  Exit condition:
    - All expected agents have rows in `agents` table
      with status='alive' AND last_heartbeat within 60s
  Timeout: 120s -> ABORT

State: PHASE_1 (Initialize & Assign)
  Entry actions:
    - Insert rows into work_assignments table
    - Broadcast phase-signal {to: "phase_1"} on global channel
    - Send per-agent directives on team-{N} channels
  Exit condition:
    - All agents have published ACK messages on global channel
      matching their assignment IDs
  Timeout: 60s -> ABORT (agents not responding)

State: PHASE_2 (Active Data Gathering)
  Entry actions:
    - Broadcast phase-signal {to: "phase_2"} on global channel
    - Start monitoring: heartbeats, blockers, rate limits
  Ongoing:
    - Every 30s: check_agents() for dead agents
    - Every heartbeat cycle: poll topic-blockers
    - On agent death: run failure recovery protocol (section 4)
    - On rate limit exhaustion: broadcast throttle directive
  Exit condition:
    - All work_assignments have status IN ('done', 'failed')
    - OR phase timeout reached (configurable, default 5min)
  Timeout: configurable per scenario -> transition to PHASE_3
    with partial results

State: PHASE_3 (Quality Validation & Dedup)
  Entry actions:
    - Broadcast phase-signal {to: "phase_3"}
    - Collect all result messages from topic-results channel
    - Run deduplication (see section 3 below)
    - Validate JSON structure, timestamps, source attribution
  Exit condition:
    - Validation report generated
    - Dedup complete (duplicates removed or flagged)
  Timeout: 120s -> proceed to PHASE_4 with warnings

State: PHASE_4 (Result Aggregation & Benchmark)
  Entry actions:
    - Broadcast phase-signal {to: "phase_4"}
    - Merge all validated results into single output
    - Calculate benchmark metrics (see SOPs section 6.1)
    - Write benchmark JSON to disk
    - Broadcast "operation-complete" on global channel
  Exit condition:
    - Benchmark JSON written
    - All agents receive shutdown directive
  Next state: DONE

State: DONE
  Entry actions:
    - Broadcast shutdown directive on global channel
    - Wait up to 30s for agent deregistration
    - Archive bus files if configured
    - Close state.db
    - Log final summary

State: ABORT
  Entry actions:
    - Broadcast abort + reason on global channel
    - Log all available diagnostics (agent table, last messages)
    - Write partial benchmark with error flags
    - Shut down all resources
```

---

## 2. Phase Gating Strategy

### 2.1 Gate Conditions Table

| Transition | Gate Condition | Verification Method | Timeout | On Timeout |
|-----------|---------------|-------------------|---------|-----------|
| INIT -> P1 | All N agents alive | `SELECT COUNT(*) FROM agents WHERE status='alive' AND last_heartbeat > ?` >= N | 120s | ABORT |
| P1 -> P2 | All assignments ACK'd | Bus ACK messages match all assignment IDs | 60s | ABORT |
| P2 -> P3 | All sources done/failed | `SELECT COUNT(*) FROM work_assignments WHERE status IN ('done','failed')` = total | Scenario-specific | Proceed with partial |
| P3 -> P4 | Validation pass | Validation report has error_count = 0 OR errors are non-fatal | 120s | Proceed with warnings |
| P4 -> DONE | Benchmark written | File exists and is valid JSON | 30s | ABORT |

### 2.2 Gate Check Implementation

The coordinator checks gate conditions in its heartbeat loop. This avoids dedicated polling threads:

```python
def heartbeat_loop(self):
    """Extended heartbeat loop with gate checking."""
    last_cleanup = time.time()
    while not self._stop_event.is_set():
        # Standard heartbeat
        self._state.heartbeat(self._agent_id)
        self.check_agents()

        # Phase-specific gate check
        if self._current_phase == "init":
            if self._check_all_agents_registered():
                self.advance_phase("phase_1")
        elif self._current_phase == "phase_1":
            if self._check_all_assignments_acked():
                self.advance_phase("phase_2")
        elif self._current_phase == "phase_2":
            if self._check_all_sources_complete():
                self.advance_phase("phase_3")
        # phase_3 and phase_4 are driven by synchronous
        # processing, not polling

        # Timeout enforcement
        self._check_phase_timeout()

        # Periodic cleanup
        if time.time() - last_cleanup >= self.cleanup_interval:
            self.run_cleanup()
            last_cleanup = time.time()

        self._stop_event.wait(self.heartbeat_interval)
```

### 2.3 Phase Timeout Enforcement

Each phase has a configurable timeout. The coordinator records phase start time in SQLite via `signal_phase()` and checks elapsed time each heartbeat cycle:

```python
PHASE_TIMEOUTS = {
    "init":    120,   # 2 min - agents should register fast
    "phase_1":  60,   # 1 min - assignment ACKs
    "phase_2": 300,   # 5 min - default data gathering (scenario-specific)
    "phase_3": 120,   # 2 min - validation
    "phase_4":  30,   # 30s   - benchmark write
}

def _check_phase_timeout(self):
    phase = self._current_phase
    if phase in ("done", "abort"):
        return
    elapsed = time.time() - self._phase_start_times[phase]
    timeout = self._phase_timeouts.get(phase, 300)
    if elapsed > timeout:
        if phase == "phase_2":
            # Partial results are acceptable
            log.warning("Phase 2 timeout (%.0fs) - proceeding with partial results", elapsed)
            self.advance_phase("phase_3")
        elif phase in ("phase_3", "phase_4"):
            log.warning("Phase %s timeout - proceeding with warnings", phase)
            self.advance_phase(self._next_phase(phase))
        else:
            log.error("Phase %s timeout after %.0fs - aborting", phase, elapsed)
            self._abort(f"Timeout in {phase} after {elapsed:.0f}s")
```

---

## 3. Rate Limit Sharing via SQLite

### 3.1 Existing Schema (from state.py)

Proto A already has the required tables. Here is the existing schema with annotations:

```sql
-- Configuration: set once by coordinator at INIT
CREATE TABLE IF NOT EXISTS rate_limit_config (
    api_endpoint TEXT PRIMARY KEY,     -- e.g. "openalex", "hackernews", "duckduckgo"
    max_calls    INTEGER NOT NULL,     -- max calls across ALL agents in window
    window_seconds INTEGER NOT NULL    -- sliding window size
);

-- Usage log: one row per API call, across all agents
CREATE TABLE IF NOT EXISTS rate_limits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    api_endpoint TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    called_at    REAL NOT NULL         -- time.time() epoch
);
```

### 3.2 Rate Limit Configuration for Test Data Sources

The coordinator sets these at INIT based on the data sources in SOPs section 5.2:

```python
# Coordinator INIT phase: configure collective rate limits
RATE_LIMITS = {
    "openalex":    {"max_calls": 10, "window_seconds": 1},   # polite pool: 10/s
    "hackernews":  {"max_calls": 30, "window_seconds": 60},  # generous but shared
    "wikipedia":   {"max_calls": 50, "window_seconds": 60},  # generous
    "duckduckgo":  {"max_calls": 1,  "window_seconds": 2},   # 0.5 req/s = 1 per 2s
}

for endpoint, cfg in RATE_LIMITS.items():
    state.configure_rate_limit(endpoint, cfg["max_calls"], cfg["window_seconds"])
```

### 3.3 Reservation Protocol (Already Implemented)

The `reserve_api_call()` method in `state.py` uses `BEGIN IMMEDIATE` to prevent TOCTOU races. Here is how agents use it:

```
Agent wants to call OpenAlex API:
  1. agent calls state.reserve_api_call("openalex", agent_id)
  2. Inside SQLite transaction (BEGIN IMMEDIATE):
     a. Read rate_limit_config for "openalex" -> max=10, window=1s
     b. COUNT(*) FROM rate_limits WHERE endpoint="openalex" AND called_at > (now - 1s)
     c. If count >= 10: ROLLBACK, return False (agent must wait)
     d. If count < 10:  INSERT into rate_limits, COMMIT, return True
  3. Agent proceeds with API call only if True
  4. On False: agent sleeps 200ms and retries (up to 5 times)
```

### 3.4 Agent-Side Rate Limit Wrapper

```python
def rate_limited_fetch(state, agent_id, api_name, fetch_fn, *args, **kwargs):
    """Wrapper that respects collective rate limits before calling fetch_fn."""
    MAX_WAIT_ATTEMPTS = 15   # 15 * 200ms = 3s max wait
    for attempt in range(MAX_WAIT_ATTEMPTS):
        if state.reserve_api_call(api_name, agent_id):
            return fetch_fn(*args, **kwargs)
        time.sleep(0.2)
    # Rate limit exhausted after max wait
    raise RateLimitExhaustedError(
        f"Could not reserve {api_name} slot after {MAX_WAIT_ATTEMPTS} attempts"
    )
```

### 3.5 Rate Limit Monitoring by Coordinator

The coordinator can query current utilization for diagnostics and throttling decisions:

```sql
-- Current utilization for each API (run by coordinator)
SELECT
    rlc.api_endpoint,
    rlc.max_calls,
    rlc.window_seconds,
    COUNT(rl.id) AS current_usage,
    ROUND(COUNT(rl.id) * 100.0 / rlc.max_calls, 1) AS utilization_pct
FROM rate_limit_config rlc
LEFT JOIN rate_limits rl
    ON rl.api_endpoint = rlc.api_endpoint
    AND rl.called_at > (strftime('%s','now') - rlc.window_seconds)
GROUP BY rlc.api_endpoint;
```

When utilization exceeds 80% on any API, the coordinator broadcasts a throttle advisory:

```python
def check_rate_utilization(self):
    """Broadcast throttle warnings when rate limits are near capacity."""
    for endpoint, usage_pct in self._get_utilization().items():
        if usage_pct > 80:
            self.broadcast("rate-warning", {
                "api": endpoint,
                "utilization_pct": usage_pct,
                "action": "reduce_request_rate"
            })
```

### 3.6 Cleanup

The existing `cleanup_expired()` in state.py deletes rate_limits rows older than 1 hour. This is sufficient -- the sliding window queries only look at the last N seconds, so old rows are harmless to correctness but waste space if not cleaned.

---

## 4. Failure Handling

### 4.1 Failure Detection, Diagnosis, Recovery, Report Protocol

```
+-------------------+     +-------------------+     +-------------------+
|    1. DETECT      |     |    2. DIAGNOSE    |     |    3. RECOVER     |
|                   |---->|                   |---->|                   |
| Heartbeat miss    |     | Check last msg    |     | Reassign work     |
| >120s stale       |     | Check PID alive   |     | Update assignments|
| get_dead_agents() |     | Check last phase  |     | Notify new agent  |
+-------------------+     +-------------------+     +-------------------+
                                                           |
                                                           v
                                                    +-------------------+
                                                    |    4. REPORT      |
                                                    |                   |
                                                    | Log to bus        |
                                                    | Update state.db   |
                                                    | Record in metrics |
                                                    +-------------------+
```

### 4.2 Step 1: Detect (Heartbeat Miss)

The coordinator already calls `check_agents()` every heartbeat cycle (30s). Detection uses the `agents` table:

```python
def check_agents(self) -> list[dict]:
    dead = self._state.get_dead_agents(timeout=self.dead_agent_timeout)  # 120s
    for agent in dead:
        self._handle_dead_agent(agent)
    return dead
```

**Detection criteria:** `last_heartbeat < (now - 120s)` AND `status = 'alive'`.

This means an agent can be dead for up to 150s before detection in the worst case (120s timeout + 30s polling interval). For data gathering tests (5 min scenarios), this is acceptable. For latency-critical operations, reduce `dead_agent_timeout` to 60s and `heartbeat_interval` to 15s.

### 4.3 Step 2: Diagnose (Check Last Message)

Before recovery, determine whether the agent is truly dead or just slow:

```python
def _diagnose_agent(self, agent: dict) -> str:
    """Returns one of: 'dead', 'stuck', 'slow', 'unknown'."""
    agent_id = agent["agent_id"]
    pid = agent.get("pid")

    # Check 1: Is the OS process still running?
    if pid:
        try:
            os.kill(pid, 0)  # signal 0 = existence check, no actual signal
            process_alive = True
        except OSError:
            process_alive = False
    else:
        process_alive = False

    if not process_alive:
        return "dead"  # Process is gone

    # Check 2: What was the agent's last bus message?
    last_msg = self._get_last_message_from(agent_id)
    if last_msg is None:
        return "unknown"

    msg_age = time.time() - last_msg["ts"]
    if msg_age > 300:
        return "stuck"  # Process alive but silent for 5+ minutes
    elif msg_age > 120:
        return "slow"   # Process alive, messages are old but recent-ish
    else:
        return "slow"   # Heartbeat missed but messages are flowing
```

### 4.4 Step 3: Recover (Reassign Work)

Recovery depends on diagnosis:

| Diagnosis | Recovery Action |
|-----------|----------------|
| `dead` | Mark agent status='dead' in DB. Reassign all its incomplete work_assignments to another alive agent. |
| `stuck` | Send "ping" directive on agent's team channel. Wait 30s. If no response, treat as `dead`. |
| `slow` | Send "status-request" directive. Wait 60s. If heartbeat resumes, take no action. |
| `unknown` | Treat as `dead` after 60s grace period. |

```python
def _recover_dead_agent(self, agent_id: str):
    """Reassign work from a dead agent to surviving agents."""
    # 1. Mark dead
    with self._state._lock:
        self._state._conn.execute(
            "UPDATE agents SET status = 'dead' WHERE agent_id = ?",
            (agent_id,)
        )
        self._state._conn.commit()

    # 2. Find incomplete assignments
    incomplete = self._get_incomplete_assignments(agent_id)
    if not incomplete:
        return  # Nothing to reassign

    # 3. Find alive agents with lowest current load
    alive = self._get_alive_workers()
    if not alive:
        self._abort("No alive workers remaining for reassignment")
        return

    # 4. Distribute incomplete work using load balancer (section 5)
    for assignment in incomplete:
        target = self._select_least_loaded_agent(alive, assignment)
        self._reassign_work(assignment, agent_id, target["agent_id"])

    # 5. Notify affected agents via bus
    for target_id in set(a["reassigned_to"] for a in incomplete):
        self.broadcast("directive", {
            "event": "work-reassigned",
            "from_agent": agent_id,
            "assignments": [a for a in incomplete if a["reassigned_to"] == target_id],
        }, channel=f"team-{target_id}")
```

### 4.5 Step 4: Report (Log to Bus)

Every failure event is reported in three places:

1. **Bus (global channel):** For live visibility by all agents.
2. **SQLite (phase_signals table):** For structured post-mortem queries.
3. **Benchmark metrics:** Increments `agent_deaths`, `reassignments`, `recovery_time_ms`.

```python
def _report_agent_failure(self, agent_id, diagnosis, recovery_action, recovery_time_ms):
    # 1. Bus broadcast
    self.broadcast("agent-failure", {
        "agent_id": agent_id,
        "diagnosis": diagnosis,
        "recovery_action": recovery_action,
        "recovery_time_ms": recovery_time_ms,
        "timestamp": time.time(),
    })

    # 2. SQLite phase signal
    self._state.signal_phase(
        self._current_phase, "agent-failure", self._agent_id,
        data=json.dumps({
            "failed_agent": agent_id,
            "diagnosis": diagnosis,
            "recovery": recovery_action,
        })
    )

    # 3. Metrics (in-memory, written at PHASE_4)
    self._metrics["agent_deaths"] += 1
    self._metrics["reassignments"] += len(recovery_action.get("reassigned", []))
    self._metrics["recovery_events"].append({
        "agent": agent_id,
        "diagnosis": diagnosis,
        "recovery_ms": recovery_time_ms,
    })
```

### 4.6 Failure Thresholds

| Condition | Action |
|-----------|--------|
| 1 agent dead | Reassign its work, continue operation |
| 2 agents dead (out of N<=5) | Reassign, broadcast warning, extend phase timeout by 50% |
| >50% agents dead | ABORT the operation |
| Coordinator dead | Workers detect via CoordinatorWatchdog, save work, exit cleanly |

---

## 5. Work Distribution Algorithm

### 5.1 Work Assignment Table Schema

Add to the existing SQLite schema (extends state.py):

```sql
CREATE TABLE IF NOT EXISTS work_assignments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id   TEXT UNIQUE NOT NULL,       -- e.g. "WA-001"
    agent_id        TEXT NOT NULL,              -- assigned agent
    api_endpoint    TEXT NOT NULL,              -- "openalex", "hackernews", etc.
    query           TEXT NOT NULL,              -- the search query or URL
    priority        INTEGER DEFAULT 5,          -- 1=highest, 10=lowest
    status          TEXT DEFAULT 'pending',     -- pending/ack/running/done/failed
    assigned_at     REAL NOT NULL,
    started_at      REAL,
    completed_at    REAL,
    result_count    INTEGER DEFAULT 0,          -- data points gathered
    error           TEXT,                       -- error message if failed
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

CREATE INDEX IF NOT EXISTS idx_wa_agent ON work_assignments(agent_id);
CREATE INDEX IF NOT EXISTS idx_wa_status ON work_assignments(status);
```

### 5.2 Source Weight Model

Different APIs have different response times and rate limits. The work distributor accounts for this with a weight model:

```
Source Weights (estimated cost per query):
==========================================
API             Avg Response   Rate Limit       Weight   Explanation
                Time (ms)      (shared/window)
-----------     -----------    ---------------  ------   -----------
openalex        300            10/1s            1.0      Fast, generous
hackernews      200            30/60s           1.5      Fast, moderate limit
wikipedia       250            50/60s           0.8      Fast, very generous
duckduckgo      800            1/2s             4.0      Slow, strict limit
```

Weight represents relative cost: a DuckDuckGo query "costs" 4x an OpenAlex query in terms of time an agent spends waiting.

### 5.3 Distribution Algorithm: Weighted Round-Robin with Load Awareness

```
Algorithm: WEIGHTED_LOAD_BALANCED_ASSIGN
========================================

Input:
  - sources[]: list of (api_endpoint, query, weight) tuples
  - agents[]:  list of alive worker agent_ids
  - rate_limits{}: api -> (max_calls, window_seconds)

Output:
  - assignments[]: list of (agent_id, api_endpoint, query)

Procedure:
  1. Sort sources by weight DESCENDING (assign heaviest first)
  2. Initialize agent_load = {agent_id: 0.0 for each agent}
  3. For each source in sorted sources:
     a. Find candidate agents: those not already at max concurrent
        assignments for this API endpoint
     b. Among candidates, select agent with LOWEST agent_load[agent_id]
     c. Assign source to selected agent
     d. Update: agent_load[agent_id] += source.weight
  4. Return assignments

Properties:
  - O(S * A) where S=sources, A=agents
  - Guarantees no agent gets >2x the load of any other (by weight)
  - Respects per-API concurrency: if API allows 10 req/s across
    3 agents, each agent gets ~3 concurrent slots
```

### 5.4 Implementation

```python
def distribute_work(self, sources, agents):
    """Assign data sources to agents using weighted load balancing.

    Args:
        sources: list of dicts with keys: api_endpoint, query, weight
        agents: list of alive agent_id strings

    Returns:
        list of work assignment dicts
    """
    # Sort heaviest first for better balance
    sorted_sources = sorted(sources, key=lambda s: s["weight"], reverse=True)

    agent_load = {aid: 0.0 for aid in agents}
    assignments = []

    for source in sorted_sources:
        # Select agent with lowest current load
        target = min(agent_load, key=agent_load.get)
        assignment_id = f"WA-{len(assignments)+1:03d}"

        assignments.append({
            "assignment_id": assignment_id,
            "agent_id": target,
            "api_endpoint": source["api_endpoint"],
            "query": source["query"],
            "priority": source.get("priority", 5),
            "weight": source["weight"],
        })

        agent_load[target] += source["weight"]

    return assignments
```

### 5.5 Example Distribution

Given 12 queries across 3 agents:

```
Queries:
  4x OpenAlex  (weight 1.0 each) = total 4.0
  3x HackerNews (weight 1.5 each) = total 4.5
  3x Wikipedia  (weight 0.8 each) = total 2.4
  2x DuckDuckGo (weight 4.0 each) = total 8.0

Total weight: 18.9
Target per agent: 18.9 / 3 = 6.3

Assignment result:
  Agent data-a-sub-1:  1x DDG(4.0) + 1x HN(1.5) + 1x Wiki(0.8)  = 6.3
  Agent data-a-sub-2:  1x DDG(4.0) + 1x HN(1.5) + 1x Wiki(0.8)  = 6.3
  Agent data-a-lead:   1x HN(1.5) + 4x OA(4.0) + 1x Wiki(0.8)   = 6.3
```

### 5.6 Rebalancing on Agent Death

When an agent dies and its work is reassigned (section 4.4), the same weighted algorithm runs on just the incomplete assignments:

```python
def rebalance_after_death(self, dead_agent_id):
    """Redistribute dead agent's incomplete work."""
    incomplete = self._get_incomplete_assignments(dead_agent_id)
    alive_agents = [a["agent_id"] for a in self._get_alive_workers()]

    if not alive_agents:
        return self._abort("No workers alive for rebalance")

    # Treat incomplete assignments as new sources
    sources = [{
        "api_endpoint": a["api_endpoint"],
        "query": a["query"],
        "weight": SOURCE_WEIGHTS.get(a["api_endpoint"], 1.0),
        "priority": 1,  # Bumped priority for reassigned work
    } for a in incomplete]

    # Factor in existing load
    new_assignments = self.distribute_work(sources, alive_agents)
    for assignment in new_assignments:
        self._insert_assignment(assignment)
```

---

## 6. Integration with Existing Prototype Code

### 6.1 What Already Exists and Can Be Used Directly

| Component | File | Status |
|-----------|------|--------|
| `SharedState` with rate limiting | `agent_comm/state.py` | Ready. `reserve_api_call()` and `configure_rate_limit()` are implemented with BEGIN IMMEDIATE. |
| `Coordinator.advance_phase()` | `agent_comm/coordinator.py` | Ready. Writes to `phase_signals` table and broadcasts on bus. |
| `Coordinator.check_agents()` | `agent_comm/coordinator.py` | Ready. Detects dead agents and broadcasts warnings. |
| `CoordinatorWatchdog` | `agent_comm/coordinator.py` | Ready. Workers detect coordinator death via heartbeat monitoring. |
| `Worker.report_blocker()` | `agent_comm/coordinator.py` | Ready. Posts to `topic-blockers` channel. |
| `EpochRotator` | `agent_comm/coordinator.py` | Ready. Handles JSONL file rotation and cleanup. |

### 6.2 What Needs to Be Added

| Component | Where | Effort |
|-----------|-------|--------|
| `work_assignments` table | `state.py` schema | Add CREATE TABLE + CRUD methods |
| `distribute_work()` | New method on `Coordinator` | ~50 lines |
| `_diagnose_agent()` | New method on `Coordinator` | ~30 lines |
| `_recover_dead_agent()` | New method on `Coordinator` | ~40 lines |
| Gate-checking logic in heartbeat loop | Extend `heartbeat_loop()` | ~40 lines |
| Phase timeout tracking | New dict + check method on `Coordinator` | ~20 lines |
| `rate_limited_fetch()` wrapper | New utility function | ~15 lines |
| `topic-results` bus channel | Convention only (agents publish results there) | 0 lines |

### 6.3 Bus Channel Plan for Data Gathering

| Channel | Writers | Readers | Purpose |
|---------|---------|---------|---------|
| `global` | Coordinator + all agents | All | Phase signals, broadcasts, announcements |
| `topic-blockers` | Agents | Coordinator | Blocker escalation |
| `topic-results` | Agents | Coordinator | Data gathering results (or file paths to results) |
| `team-{agent_id}` | Coordinator | Specific agent | Directives: assignments, reassignments, shutdown |

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| SQLite contention under 10 agents | Low | Medium | `busy_timeout=30000ms` + `_retry_on_busy` decorator already handle this (tested in KB-0018 stress tests) |
| Rate limit window drift across agents | Low | Low | All agents read the same SQLite clock via `time.time()`. Drift is sub-millisecond on a single host. |
| Phase timeout too short for slow APIs | Medium | High | Make timeouts configurable per scenario. Default to generous values. Coordinator logs warnings before timeouts. |
| Agent death during SQLite write | Low | Low | SQLite WAL auto-recovery handles this. `BEGIN IMMEDIATE` + journal ensures atomic writes. |
| Bus file grows large during long tests | Low | Low | EpochRotator + `cleanup_old_epochs()` runs every 600s. Max ~50MB per operation (KB-0019 estimate). |
| Work rebalancing causes duplicate fetches | Medium | Medium | Each assignment has a unique `assignment_id`. Agents check status before starting. PHASE_3 dedup catches any slips. |

---

## 8. Summary of Recommendations

1. **Use Prototype A** (JSONL + SQLite WAL) for all live data gathering tests. It has better correctness guarantees and all required rate-limiting infrastructure already implemented.

2. **Add the `work_assignments` table** to `state.py` to formalize work distribution and enable reassignment on agent death.

3. **Extend `Coordinator.heartbeat_loop()`** with phase gate checks rather than adding new polling threads. Keep the architecture single-loop.

4. **Configure rate limits at INIT**, not at runtime. The coordinator is the sole authority for rate limit configuration. Agents only call `reserve_api_call()`.

5. **Tolerate partial results.** PHASE_2 timeout should transition to PHASE_3 with whatever data was gathered, not ABORT. Data gathering is inherently best-effort.

6. **Prioritize reassigned work.** When an agent dies, its incomplete assignments get `priority=1` so surviving agents process them before their own remaining low-priority work.

7. **Run PHASE_3 validation synchronously** in the coordinator (not distributed). The coordinator has the full result set and can deduplicate globally without coordination overhead.
