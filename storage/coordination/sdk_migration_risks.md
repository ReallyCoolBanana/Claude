# SDK Migration Risk Analysis: CLI to Agent SDK (Option C)

**Date:** 2026-03-11
**Analyst:** Think Tank 2 -- Architecture Risk Analysis
**Scope:** Migration of `/home/user/Claude/storage/coordination/` from CLI-based multi-agent coordination to Agent SDK launcher

---

## Executive Summary

The existing coordination system is built around a CLI-launched, file-based architecture: SQLite WAL databases for shared state, JSONL files for the message bus, and `subprocess`/signal-based process lifecycle. Moving to an Agent SDK launcher introduces risks across five categories: process lifecycle, SQLite concurrency, JSONL bus integrity, component compatibility, and single-point-of-failure. Three risks are blockers; the rest are addressable incrementally.

---

## 1. Compatibility Risks

### 1.1 Process Lifecycle Model Mismatch (BLOCKER)

**Current model:** `multi_team_runner.py` (lines 388-509) manages agent lifecycle through:
- Registering agents in SQLite with PID tracking (`_register_agent`, line 270-285)
- Heartbeat monitoring via `_heartbeat_loop` (line 510-523)
- Dead-agent detection via PID-based heartbeat (`_get_dead_agents`, line 300-309)
- Signal-based shutdown: SIGINT/SIGTERM handled by `_signal_handler` (line 639-645)
- `_request_shutdown` sends `os.kill(pid, signal.SIGTERM)` (line 686-700)

**Risk:** SDK-managed agents are not OS processes the launcher controls via PID/signals. The SDK spawns agents as API-driven conversations, not subprocesses. This breaks:
- `agents.pid` column tracking (line 99-100 of multi_team_runner.py)
- `os.kill(pid, signal.SIGTERM)` shutdown path (line 694)
- Heartbeat model that assumes agents are local processes writing to the same filesystem

**Severity:** High -- fundamental architectural assumption mismatch.

**Mitigation:**
- Abstract the agent handle: replace PID-based tracking with an SDK agent handle/ID. The `agents` table should store an `sdk_handle` column alongside (or replacing) `pid`.
- Replace `os.kill` with SDK's cancellation API.
- Heartbeat can remain: SDK agents can still write heartbeats to SQLite if they share the working directory. Alternatively, the launcher can poll SDK agent status and write heartbeats on their behalf.

### 1.2 SQLite WAL Concurrent Access Under SDK (MODERATE)

**Current model:** Every module opens its own WAL-mode connection:
- `coordinator_hub.py` `_open_db` (line 109-127): `busy_timeout=30000`, WAL mode
- `direct_channels.py` `__init__` (line 240-245): same pattern
- `work_stealing.py` `_open_db` (line 128-137): same pattern
- `help_protocol.py` `__init__` (line 148-161): same pattern
- `multi_team_runner.py` `_get_connection` (line 215-237): shared connection with `_shared_conn_lock`

All modules duplicate the retry decorator (`_retry_on_busy`) with identical `_MAX_RETRIES=5` and `_RETRY_BACKOFF=0.1`.

**Risk:** With SDK-managed agents, the concurrency profile changes:
- CLI model: N agents = N processes, each with 1-2 SQLite connections. Writes are paced by human-like agent thinking time.
- SDK model: Agents may execute faster and in tighter loops, increasing write contention. If the SDK launcher itself also holds connections (for monitoring), that adds another writer.
- WAL mode handles concurrent readers well but writers still serialize. With many fast writers, the 5-retry/exponential-backoff pattern (max wait ~1.6s) may not be sufficient.

**Severity:** Moderate -- existing retry logic is sound but may need tuning.

**Mitigation:**
- Increase `_MAX_RETRIES` from 5 to 8-10 for SDK mode, or switch to a configurable backoff.
- Consider a connection pool in the launcher rather than per-component connections.
- Monitor `SQLITE_BUSY` rates in production and adjust `busy_timeout` accordingly.
- Long-term: evaluate whether a single writer process (the launcher) should proxy all writes.

### 1.3 JSONL Bus File Locking with Many Concurrent Writers (MODERATE)

**Current model:** Every component writes to JSONL bus files using atomic `os.open` + `os.write` + `os.close`:
- `coordinator_hub.py` `_bus_notify` (line 135-167)
- `direct_channels.py` `_bus_publish` (line 120-154)
- `work_stealing.py` `_bus_notify` (line 144-176)
- `help_protocol.py` `_publish_bus` (line 186-218)
- `multi_team_runner.py` `_bus_write` (line 175-200)

All use the same pattern: `O_WRONLY | O_APPEND | O_CREAT`, write a single JSON line, close.

**Risk:** On Linux, `O_APPEND` writes are atomic for writes under `PIPE_BUF` (4096 bytes). The `MAX_MESSAGE_BYTES = 4096` limit in `direct_channels.py` (line 35) and the 4096-byte check in `multi_team_runner.py` (line 192) ensure atomicity. However:
- If SDK agents are co-located and write faster, partial-write corruption risk increases on heavily loaded NFS or non-local filesystems.
- The `_bus_read` function in `direct_channels.py` (line 157-209) handles partial lines correctly (lines 185-186), which is good.
- No file-level locking is used -- only POSIX append semantics.

**Severity:** Low-to-moderate. The existing safeguards are well-designed for local filesystems.

**Mitigation:**
- Verify the deployment filesystem is local (not NFS/CIFS) where `O_APPEND` atomicity holds.
- For higher throughput: consider consolidating bus writes through the launcher process.
- Monitor for partial-line occurrences in bus files.

### 1.4 Multiple Database Files Create Schema Coordination Risk (LOW)

**Current model:** Different components use different databases or may share the same one:
- `coordinator_hub.py`: user-specified `db_path` with its own schema (agent_status, coordinator_instructions)
- `direct_channels.py`: user-specified `db_path` with its own schema (channels, presence, progress, read_offsets)
- `work_stealing.py`: user-specified `db_path` with its own schema (work_queue, pipelines, pipeline_stages, scratchpad)
- `help_protocol.py`: user-specified `db_path` with its own schema (work_items, help_requests, team_capabilities, team_status)
- `multi_team_runner.py`: hardcoded `_DB_PATH` with its own schema (agents, rate_limits, messages, phase_signals, team_findings, think_tank, runner_state)

**Risk:** The SDK launcher needs to know which databases to initialize and where. If a single `db_path` is used for all components, schema collisions are possible (e.g., both `work_stealing.py` and `help_protocol.py` have `work_items`-like tables with different schemas). If separate databases are used, the launcher must coordinate multiple DB lifecycle.

**Severity:** Low -- current code uses `CREATE TABLE IF NOT EXISTS` everywhere, so tables don't collide if they have different names. But the operational complexity of managing multiple DB paths in the SDK config is real.

**Mitigation:**
- Standardize on a single database file with all schemas merged, using table name prefixes if needed.
- Have the launcher initialize all schemas once at startup.

---

## 2. Architecture Risks

### 2.1 Single Point of Failure in the Launcher (BLOCKER)

**Current model:** `MultiTeamRunner` (multi_team_runner.py line 388) is already a single coordinator, but it only does monitoring/heartbeat -- agents run independently as separate CLI processes. If the runner dies, agents continue working.

**Risk:** In the SDK model, the launcher is the parent process that spawns and manages all agent conversations. If it crashes:
- All agent SDK sessions may be orphaned or terminated.
- No heartbeat monitoring occurs.
- No new agents can be spawned.
- In-flight work is lost with no recovery mechanism.

The current `_signal_handler` (line 639-645) calls `runner.stop()` then `sys.exit(0)`, which is a clean shutdown. But an unclean crash (OOM, segfault) leaves no recovery path.

**Severity:** High -- the launcher becomes the single point of failure for the entire coordination system.

**Mitigation:**
- **Watchdog process:** Run a separate watchdog that monitors the launcher PID and restarts it on crash. The `runner_state` table (line 163-167) already stores `status` and `pid`, which a watchdog can use.
- **State recovery:** On launcher restart, read all `agent_status` records and SDK session state to reconnect to surviving agents.
- **Graceful degradation:** Ensure agents can continue limited operation (finishing current task) even if the launcher is temporarily down, by designing the SDK integration to not require constant launcher heartbeats.
- **Checkpoint system:** Periodically persist launcher state (which agents are active, current phase, pending instructions) to SQLite so recovery is possible.

### 2.2 Error Propagation from Agents to Launcher (MODERATE)

**Current model:** Errors are reported through:
- `AgentReporter.report_error` (coordinator_hub.py line 312-337): writes to SQLite
- Bus notifications: fire-and-forget JSONL writes
- `CoordinatorDashboard.get_failed_agents` (line 720-736): polls SQLite

**Risk:** SDK agents report errors through the SDK's callback/event system, not through SQLite writes. The launcher needs to:
1. Catch SDK-level errors (API failures, token limits, tool execution errors)
2. Translate them into the existing `agent_status.error_message` format
3. Decide whether to retry, reassign, or escalate

The current system has no retry/reassignment logic -- it just logs errors. The SDK launcher needs a richer error taxonomy.

**Severity:** Moderate -- the gap is in the translation layer, not the core architecture.

**Mitigation:**
- Build an error translation layer in the launcher that maps SDK error types to coordination system statuses.
- Add retry logic for transient SDK errors (rate limits, network timeouts).
- Use `CoordinatorDashboard.send_instruction` (line 548-599) to redirect work away from failed agents.

### 2.3 Memory/Resource Scaling (MODERATE)

**Current model:** Each agent is a separate process with its own memory space. The runner only holds config and thread handles (line 399-409).

**Risk:** In the SDK model, the launcher process holds:
- SDK client objects for each agent (API connections, conversation state)
- Monitoring thread state
- SQLite connections for coordination
- In-memory caches for bus messages

With 10+ agents, this could consume significant memory in a single process.

**Severity:** Moderate -- depends on SDK client memory footprint.

**Mitigation:**
- Profile SDK client memory usage with target agent counts.
- Use lazy initialization: only create SDK clients when agents are needed.
- Set per-agent memory budgets and implement back-pressure if limits are hit.
- Consider a pool-of-launchers architecture for very large deployments.

### 2.4 Network Partition Handling (LOW)

**Current model:** Everything is local -- SQLite and JSONL files on the same filesystem. No network involved.

**Risk:** If the SDK communicates with a remote API, network partitions between the launcher and the API will stall agents. The coordination system has no concept of "agent stalled due to API unavailability."

**Severity:** Low -- this is an inherent SDK concern, not a coordination system design flaw.

**Mitigation:**
- Add a new agent status: `"stalled"` or `"api_unavailable"` to the `VALID_AGENT_STATUSES` set in `coordinator_hub.py` (line 32-34).
- Implement circuit-breaker logic in the launcher for API calls.
- `get_stale_agents` (coordinator_hub.py line 601-628) already detects agents that stop updating; leverage this for API-stalled agents.

---

## 3. Integration Risks

### 3.1 Dashboard Requires Updates (MODERATE)

**Current model:** `dashboard.py` reads directly from SQLite and JSONL files:
- `_get_agents` (line 164-182): reads `agents` table (which has `pid`, `last_heartbeat`)
- `_status_indicator` (line 104-115): classifies by heartbeat age
- `_get_bus_activity` (line 264-303): reads JSONL files from `_BUS_DIR`

**Risk:** If agents are SDK-managed:
- The `pid` column will not be meaningful (SDK agents are API conversations, not processes).
- Heartbeat frequency may differ (SDK agents update when they complete tool calls, not on a fixed interval).
- The dashboard's dead/stale thresholds (`_DEAD_THRESHOLD = 120.0`, `_STALE_THRESHOLD = 60.0` at lines 50-51) may need adjustment since SDK agent response times are different from CLI agent heartbeat intervals.

**Severity:** Moderate -- the dashboard is a monitoring tool, not a critical path, but incorrect status display undermines operator trust.

**Mitigation:**
- Add an `agent_type` column to the `agents` table: `"cli"` vs `"sdk"`.
- Adjust thresholds dynamically based on agent type.
- Add SDK-specific status fields (token usage, conversation turn count) to the dashboard.

### 3.2 Help Protocol Compatibility (LOW)

**Current model:** `HelpProtocol` (help_protocol.py) is self-contained:
- Uses its own SQLite tables (work_items, help_requests, team_capabilities, team_status)
- Atomic operations via `BEGIN IMMEDIATE` (e.g., `offer_help` line 471-539)
- Bus publishing via `_publish_bus` (line 186-218)

**Risk:** The help protocol is decoupled from the process lifecycle model. It operates on team/agent IDs, not PIDs. The atomic `offer_help` pattern (BEGIN IMMEDIATE + check + claim + COMMIT) will work identically under SDK management because it depends on SQLite, not on process identity.

**One concern:** `auto_assign_idle_teams` (line 734-899) creates assignments within the current process's DB connection. If the launcher proxies these calls on behalf of agents, the `self.team` and `self.agent_id` must be set correctly for the helper team, not the launcher's identity.

**Severity:** Low -- the protocol is well-isolated.

**Mitigation:**
- Ensure the launcher creates per-team `HelpProtocol` instances (or passes correct team/agent IDs) when proxying operations.
- No structural changes needed.

### 3.3 Direct Channel Compatibility (LOW)

**Current model:** `DirectChannels` (direct_channels.py) uses:
- SQLite for channel registry, presence, progress (lines 39-75)
- JSONL bus files for actual message delivery (line 120-154)
- Byte-offset tracking for read positions (lines 69-74, 157-209)

**Risk:** The offset-tracking mechanism (`read_offsets` table and `_read_offsets` dict) is per-instance. If SDK agents are restarted (e.g., after a failure), they create new `DirectChannels` instances that load offsets from the DB (line 671-680). This works correctly because offsets are persisted.

**Severity:** Low -- the design is already resilient to agent restarts.

**Mitigation:**
- No changes needed. The `_load_read_offsets` / `_save_read_offset` pattern (lines 671-702) handles restart correctly.

### 3.4 Work Queue Atomicity (LOW)

**Current model:** `WorkStealing.steal_work` (work_stealing.py lines 240-292) uses `BEGIN IMMEDIATE` to atomically claim work. This is the correct pattern for preventing double-steal.

**Risk:** Under SDK management, multiple agents might call `steal_work` with higher concurrency. The `BEGIN IMMEDIATE` + retry pattern handles this, but the 5-retry limit might need increasing.

**Severity:** Low -- the atomic pattern is correct; only tuning may be needed.

**Mitigation:**
- Increase `_MAX_RETRIES` to 8 for work stealing under high concurrency.
- Monitor steal-failure rates.

---

## 4. Risk Summary Matrix

| # | Risk | Severity | Blocker? | Effort to Fix |
|---|------|----------|----------|---------------|
| 1.1 | Process lifecycle mismatch (PID/signal) | High | YES | Medium -- abstract agent handles |
| 1.2 | SQLite WAL contention under faster agents | Moderate | No | Low -- tune retries |
| 1.3 | JSONL bus write integrity | Low-Moderate | No | Low -- verify filesystem |
| 1.4 | Multiple DB schema coordination | Low | No | Low -- standardize paths |
| 2.1 | Single point of failure in launcher | High | YES | High -- watchdog + recovery |
| 2.2 | Error propagation gap | Moderate | No | Medium -- translation layer |
| 2.3 | Memory scaling | Moderate | No | Low -- profile and monitor |
| 2.4 | Network partition handling | Low | No | Low -- add status enum |
| 3.1 | Dashboard needs updates | Moderate | No | Low -- add agent_type column |
| 3.2 | Help protocol compatibility | Low | No | Minimal |
| 3.3 | Direct channel compatibility | Low | No | None |
| 3.4 | Work queue atomicity | Low | No | Minimal -- tune retries |

---

## 5. Recommendations: Phased Migration Plan

### Phase 1: Foundation (BLOCKER resolution)

**Goal:** Make the coordination system SDK-aware without breaking CLI compatibility.

1. **Abstract agent handles** (Risk 1.1):
   - Add `sdk_handle TEXT` and `agent_type TEXT DEFAULT 'cli'` columns to the `agents` table in `multi_team_runner.py` (line 98-106).
   - Modify `_register_agent` (line 270-285) to accept `agent_type` parameter.
   - Replace `os.kill` in `_request_shutdown` (line 694) with a dispatcher: `if agent_type == 'sdk': sdk.cancel(handle)` else `os.kill(pid, SIGTERM)`.

2. **Build launcher watchdog** (Risk 2.1):
   - Create `sdk_launcher_watchdog.py` that monitors the launcher's PID via `runner_state` table.
   - On launcher crash: read persisted state, restart launcher, reconnect to surviving SDK sessions.
   - Write launcher PID and state checkpoints every heartbeat interval.

3. **Single schema initialization**:
   - Create a `schema.py` that merges all schemas and initializes them in one call.
   - The launcher calls this once at startup.

### Phase 2: Integration Layer

**Goal:** Build the SDK-to-coordination translation layer.

4. **Error translation** (Risk 2.2):
   - Map SDK error types to `VALID_AGENT_STATUSES`.
   - Add retry logic for transient errors.
   - Use `send_instruction` to reassign work from failed SDK agents.

5. **Dashboard updates** (Risk 3.1):
   - Add `agent_type` awareness to `_status_indicator` in `dashboard.py`.
   - Adjust thresholds for SDK agents.
   - Add SDK-specific metrics (token usage, turn count).

6. **Heartbeat proxy** (Risk 1.1):
   - The launcher writes heartbeats on behalf of SDK agents based on SDK event callbacks.
   - This avoids requiring SDK agents to directly access the SQLite database.

### Phase 3: Optimization

**Goal:** Tune for SDK concurrency patterns.

7. **SQLite retry tuning** (Risks 1.2, 3.4):
   - Make `_MAX_RETRIES` and `_RETRY_BACKOFF` configurable via environment variables.
   - Add metrics collection for SQLITE_BUSY events.

8. **Bus write consolidation** (Risk 1.3):
   - Optionally route bus writes through the launcher for higher throughput.
   - Keep direct writes as fallback for CLI agents.

9. **Memory profiling** (Risk 2.3):
   - Profile launcher memory with target agent counts (5, 10, 20).
   - Implement back-pressure if memory exceeds budget.

---

## 6. Test Coverage Assessment

Existing tests that must pass throughout migration:

| Test File | Component | SDK Risk |
|-----------|-----------|----------|
| `tests/test_coordinator_hub.py` | AgentReporter, CoordinatorDashboard | Medium -- PID changes |
| `tests/test_direct_channels.py` | DirectChannels | Low |
| `tests/test_help_protocol.py` | HelpProtocol | Low |
| `tests/test_work_stealing.py` | WorkStealing, PipelineManager, Scratchpad | Low |
| `tests/test_bus_core.py` | Bus read/write | Low |
| `tests/test_db_utils.py` | DB utilities | Low |
| `stress-tests/test_stress_direct_channels.py` | Concurrent channel ops | Medium -- concurrency profile changes |
| `stress-tests/test_stress_help_protocol.py` | Concurrent help flow | Medium |
| `stress-tests/test_stress_work_stealing.py` | Concurrent steal ops | Medium |

**New tests needed:**
- SDK agent lifecycle (spawn, monitor, cancel, restart)
- Launcher crash recovery
- Mixed CLI + SDK agent operation
- SDK error translation
- Launcher memory under load

---

## 7. Key Architectural Strengths to Preserve

The existing system has several well-engineered patterns that should be preserved:

1. **Fire-and-forget bus writes**: All bus notification functions catch and log errors without propagating them (e.g., `coordinator_hub.py` line 163). This prevents bus failures from corrupting DB state.

2. **Atomic steal/claim patterns**: `BEGIN IMMEDIATE` + check + update + `COMMIT` with proper rollback handling (e.g., `work_stealing.py` lines 250-284, `help_protocol.py` lines 481-539).

3. **Thread-safe connection management**: Every component uses `threading.Lock` around DB access. The SDK launcher must maintain this discipline.

4. **Partial-line resilience in bus reads**: `_bus_read` in `direct_channels.py` (lines 157-209) correctly handles incomplete writes. This is critical under higher SDK concurrency.

5. **Idempotent registration**: All `INSERT ... ON CONFLICT DO UPDATE` patterns allow safe re-registration after restarts.
