# Coordination System Architecture Map

**Generated:** 2026-03-11 | **Team:** Research Team 3

---

## Module Overview

| Module | Description |
|--------|-------------|
| `bus_core.py` | Shared primitives: retry decorator, SQLite WAL init, JSONL bus read/write, channel sanitizer |
| `bus_cli.py` | CLI tool for agents to read/write JSONL bus channels and issue help protocol commands |
| `coordinator_hub.py` | Hub-and-spoke agent monitoring via SQLite; agents report status, coordinator mediates |
| `help_protocol.py` | Team-level help requests, idle detection, capability matching, automatic work assignment |
| `direct_channels.py` | Named peer-to-peer channels (direct/topic/team/broadcast), presence tracking, progress broadcasting |
| `work_stealing.py` | Shared work queues, multi-stage pipelines with dependency tracking, key-value scratchpad |
| `multi_team_runner.py` | Launches N teams x M agents, heartbeat monitoring, clean shutdown, operational logging |
| `dashboard.py` | Text-based real-time dashboard: agent status, bus activity, findings summary |
| `think_tank.py` | Cross-team findings convergence: deduplication, categorization, consolidated roadmap output |
| `sdk_config.py` | YAML/JSON config loading, team/role definitions, preset configs (dev, research, review, production) |
| `db_utils.py` | Higher-level SQLite helpers: connection management, idempotent schema creation, atomic updates |

---

## Key Classes and Roles

### Core Infrastructure
- **`bus_core.retry_on_busy()`** - Canonical decorator for SQLITE_BUSY resilience (exponential backoff, 5 retries)
- **`bus_core.init_db()`** - Standard WAL-mode connection setup (busy_timeout=5000ms default)
- **`bus_core.bus_write()`** - POSIX-atomic JSONL append via `os.write()` (max 4096 bytes/msg)
- **`bus_core.bus_read()`** - Binary-mode bus reader with byte offset tracking
- **`db_utils.get_connection()`** - Convenience WAL connection wrapper
- **`db_utils.ensure_schema()`** - Idempotent schema creation
- **`db_utils.atomic_update()`** - Read-modify-write transaction helper

### Agent Monitoring (coordinator_hub.py)
- **`AgentReporter`** (line 175) - Each agent writes its own status row to `agent_status` table
- **`CoordinatorDashboard`** (line 429) - Coordinator reads all agent statuses; sends instructions via `coordinator_instructions` table
- Agents CANNOT see each other; coordinator mediates all cross-agent communication

### Help Protocol (help_protocol.py)
- **`HelpProtocol`** (line 127) - Manages work items, help requests/offers, idle detection, team capabilities
- Solves the idle-team problem: detects finished teams and auto-assigns them to busy teams

### Direct Channels (direct_channels.py)
- **`DirectChannels`** (line 217) - Named channels with SQLite metadata + JSONL bus delivery
- Tracks presence (available/busy/helping/away) and progress with bottleneck detection
- Uses `read_offsets` table for per-team, per-channel cursor tracking

### Work Stealing (work_stealing.py)
- **`WorkStealing`** (line 184) - Shared queue; idle teams atomically claim unclaimed items
- **`PipelineManager`** (line 369) - Multi-stage pipelines; stages auto-trigger when upstream completes
- **`Scratchpad`** (line 724) - Namespaced key-value store with TTL for sharing intermediate data

### Launcher and Config
- **`MultiTeamRunner`** (line 388 in multi_team_runner.py) - Orchestrator: init dirs, register agents, heartbeat loop, signal handling
- **`LaunchConfig`** / **`TeamDefinition`** (sdk_config.py) - Dataclass-based config with presets: `standard_dev_team()`, `research_team()`, `review_team()`, `full_production_team()`
- **`AgentRole`** enum: leader, worker, researcher, reviewer, think_tank

### Analysis
- **`think_tank.py`** - Reads `team_findings` from SQLite + JSON files on disk, deduplicates by fingerprint, outputs `teams/think-tank-report.json`

---

## Data Flow

### SQLite Tables (all in `db/state.db`, WAL mode)

```
coordinator_hub.py writes:
  agent_status        - per-agent status (id, team, role, status, progress, blockers, findings)
  coordinator_instructions - coordinator -> agent directives (pending/read lifecycle)

help_protocol.py writes:
  work_items           - trackable work units (pending/in_progress/complete)
  help_requests        - open/accepted/resolved help flow
  team_capabilities    - capability registry for matching
  team_status          - per-team progress tracking

direct_channels.py writes:
  channels             - named channel registry (direct/team/topic/broadcast)
  presence             - per-team availability status
  progress             - per-team phase/progress with bottleneck field
  read_offsets         - per-team per-channel byte offset cursor

work_stealing.py writes:
  work_queue           - shared steal-able queue (queued/claimed/completed)
  pipelines            - pipeline definitions (name, stages JSON)
  pipeline_stages      - per-stage state with dependency tracking (waiting/running/completed)
  scratchpad           - namespaced KV with TTL expiration
```

### JSONL Bus (files in `bus/`)

```
Write path:  bus_core.bus_write() / bus_cli.py write
             -> os.open() + os.write() (POSIX atomic for <4096 bytes)
             -> file: bus/<sanitized_channel>.jsonl

Read path:   bus_core.bus_read() / bus_cli.py read
             -> binary-mode read from byte offset
             -> returns (messages[], new_offset)

Message schema:
  { id, type, channel, team, agent_id, ts, ttl, body }

Valid types: info, blocker, phase-signal, heartbeat, request, response

Modules that write to bus:
  - coordinator_hub.py (_bus_notify)
  - direct_channels.py (_bus_publish)
  - work_stealing.py (_bus_notify)
  - bus_cli.py (write_msg)
  - multi_team_runner.py (_bus_write)
```

### Read Dependencies
- `dashboard.py` reads: `agent_status` (SQLite) + all `bus/*.jsonl` files
- `think_tank.py` reads: `team_findings` (SQLite) + `teams/*.json` output files
- `multi_team_runner.py` reads/writes: `agent_status`, runner state, bus
- `HelpProtocol` reads: `team_status`, `team_capabilities`, `help_requests` for idle matching

---

## Directory Layout (Runtime)

```
storage/coordination/
  bus/                  # JSONL message files (one per channel)
  db/
    state.db            # Single shared SQLite database (WAL mode)
  teams/                # Team output JSON files, think-tank-report.json
  runner.log            # Multi-team runner log file
```

---

## SOPs (storage/coordination/sops/)

| File | Title |
|------|-------|
| `sop_011_central_commander_launch.json` | Central Commander Launch Protocol - maximize parallelism, eliminate sequential-wave anti-pattern |
| `sop_012_monitoring_intervention.json` | Monitoring and Intervention |
| `sop_013_prompt_engineering.json` | Prompt Engineering |
| `sop_014_simultaneous_project_management.json` | Simultaneous Project Management |
| `sop_015_infrastructure_audit.json` | Infrastructure Audit |
| `sop_016_cross_team_data_flow.json` | Cross-Team Data Flow - report formats, consolidation pipelines, deduplication |
| `sop_017_data_gathering_operations.json` | Data Gathering Operations |
| `sop_018_market_research_operations.json` | Market Research Operations |
| `sop_019_environment_setup.json` | Environment Setup |
| `sop_020_bug_fix_workflow.json` | Bug Fix Workflow |
| `sop_021_failure_recovery_runbook.json` | Failure Recovery Runbook |
| `efficiency_guide.json` | Efficiency Guide |
| `future_team_data_strategy.json` | Future Team Data Strategy |
| `tool_sops.json` | Tool SOPs |

---

## Tests

**Unit tests** (`tests/`): test_bus_core, test_coordinator_hub, test_db_utils, test_direct_channels, test_help_protocol, test_operation_health_coord, test_unknown_bugs_x, test_work_stealing

**Stress tests** (`stress-tests/`): test_stress_direct_channels, test_stress_help_protocol, test_stress_work_stealing

---

## Integration Points for a New SDK Launcher

1. **Config entry point:** Use `sdk_config.load_config()` or presets (`standard_dev_team()`, etc.) to define team topology
2. **Runner integration:** `MultiTeamRunner` accepts config dict with teams/agents; call `_init_db()` then register agents
3. **Agent registration:** Each agent needs an `AgentReporter` instance (coordinator_hub.py) to report status
4. **Bus communication:** Use `bus_core.bus_write()` / `bus_core.bus_read()` for inter-agent messaging
5. **Help protocol:** Call `HelpProtocol.register_team()` with capabilities, then `update_team_status()` for idle detection
6. **Work distribution:** Use `WorkStealing.enqueue()` for shared queues or `PipelineManager.create_pipeline()` for staged workflows
7. **Scratchpad:** Use `Scratchpad.put()` / `Scratchpad.get()` for sharing intermediate results between agents
8. **Monitoring:** `dashboard.py --watch` for real-time view; `CoordinatorDashboard` for programmatic access
9. **Convergence:** Run `think_tank.py` post-operation to consolidate findings

**Key constraints:**
- Stdlib only (no external deps except optional PyYAML in sdk_config)
- All SQLite writes use `BEGIN IMMEDIATE` + retry-on-busy decorator
- Single shared `db/state.db` file; all modules coexist in same database
- Bus messages must be <4096 bytes for atomic POSIX writes
- Agent IDs follow convention: `{team_name}-lead`, `{team_name}-worker-{n}`
