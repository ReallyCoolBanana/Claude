# SOPs & Test Coverage Analysis

**Team:** Research Team 3C
**Date:** 2026-03-11
**Purpose:** Map all SOPs, identify SDK-launcher-relevant procedures, assess test coverage and gaps.

---

## SOP Index

### Tool SOPs (tool_sops.json)

| ID | Title | Summary |
|---|---|---|
| SOP-001 | Help Protocol | Manages team help requests, idle detection, and automatic work assignment via HelpProtocol class. |
| SOP-002 | Direct Channels | Named communication channels (direct/topic/broadcast), presence tracking, progress broadcasting with bottleneck detection. |
| SOP-003 | Work Stealing | Shared work queue with atomic steal via BEGIN IMMEDIATE; prevents double-steal for parallel task distribution. |
| SOP-004 | Pipeline Management | Multi-stage pipeline DAGs with dependency tracking; stages auto-trigger when upstream completes (PipelineManager class). |
| SOP-005 | Scratchpad | Namespaced key-value store with TTL expiration for sharing intermediate results between agents/teams. |
| SOP-006 | Bus CLI | Command-line interface for JSONL bus read/write and shared state management; primary shell-based agent interface. |
| SOP-007 | Multi-Team Runner | Launches N teams x M agents; handles registration, heartbeat monitoring, phase management, and clean shutdown. |
| SOP-008 | Think Tank Convergence | Collects findings from all teams, deduplicates by SHA-256(category+title), generates prioritized improvement roadmap. |
| SOP-009 | Dashboard | Text-based real-time monitoring of agent status, bus activity, phase signals, and findings summary. |
| SOP-010 | New Team Onboarding | Step-by-step for new AI teams: read prior knowledge, init coordination, register, do work, create handoff artifacts. |

### Standalone SOPs

| ID | Title | Summary |
|---|---|---|
| SOP-011 | Central Commander Launch Protocol | Ensures coordinator maximizes parallelism; all teams launch simultaneously unless hard data dependency exists. |
| SOP-012 | Real-Time Monitoring and Intervention | Structured status-file-based monitoring via JSONL; replaces ad-hoc filesystem polling for agent health/progress. |
| SOP-013 | Agent Prompt Engineering and Context Distribution | Standardized 6-part prompt templates, context rules, agent type templates, and output coordination. |
| SOP-014 | Simultaneous Multi-Project Management | Hub-and-spoke management of 2-5 simultaneous projects with isolated team groups, output paths, and progress tracking. |
| SOP-015 | Infrastructure Audit and Mass Indexing | Procedure for repo audits: file indexing, tool inventory, bug testing, documentation generation. |
| SOP-016 | Cross-Team Data Flow and Report Consolidation | Standard report formats with required metadata blocks; consolidation pipelines and deduplication. |
| SOP-017 | Data Gathering Operations | Method selection (hybrid, openalex), rate limiting, caching, error handling, and result grading for data collection. |
| SOP-018 | Market Research Operations | Stock analysis method selection, pick logging, performance tracking via market-research/ directory. |
| SOP-019 | Environment Setup for Fresh Deployment | Python installation, dependency management, Go binary compilation, directory initialization, verification. |
| SOP-020 | Systematic Bug Fix Workflow | Bug report triage, reproduction, root cause analysis, targeted fix, test verification, documentation. |
| SOP-021 | Failure Recovery Runbook | Recovery procedures for agent crashes, SQLite lock contention, bus file corruption, coordinator failures. |

### Support Documents

| File | Summary |
|---|---|
| efficiency_guide.json | Efficiency patterns (EFF-001 through EFF-005+): idle reassignment, work decomposition, pipeline chaining, etc. |
| future_team_data_strategy.json | Data storage strategy and knowledge transfer protocol: what/where/how to store and hand off between sessions. |

---

## Key SOPs Affecting SDK Launcher Design

The following SOPs are **directly relevant** to building an SDK-based launcher:

1. **SOP-007 (Multi-Team Runner)** -- CRITICAL. This IS the current launcher. Defines team config format, agent registration, heartbeat monitoring, health checks, phase management, and shutdown. The SDK launcher must replicate or wrap this functionality.

2. **SOP-011 (Central Commander Launch)** -- CRITICAL. Mandates simultaneous parallel launch of all teams. The SDK launcher must NOT use sequential wave patterns. Burden of proof is on sequencing.

3. **SOP-010 (New Team Onboarding)** -- HIGH. Defines the agent startup sequence: read CLAUDE.md, read session logs, check KB, init coordination, register agent, set presence. The SDK launcher must inject this sequence into agent prompts or initialization.

4. **SOP-012 (Monitoring and Intervention)** -- HIGH. Defines the JSONL status file schema that agents must write to. The SDK launcher needs to implement or integrate with this monitoring layer.

5. **SOP-013 (Prompt Engineering)** -- HIGH. Defines the 6-part canonical prompt template. The SDK launcher must generate agent prompts following this structure.

6. **SOP-019 (Environment Setup)** -- MEDIUM. The SDK launcher should verify environment prerequisites (Python 3, pytest, required dirs) before launching agents.

7. **SOP-021 (Failure Recovery)** -- MEDIUM. The SDK launcher should implement crash detection (stale heartbeats), partial output preservation, and agent relaunch with context.

8. **SOP-014 (Multi-Project Management)** -- MEDIUM. If the SDK launcher supports multiple projects, it needs project isolation per this SOP.

---

## Test Coverage Analysis

### Modules with Unit Tests

| Module | Test File | What's Tested |
|---|---|---|
| `coordinator_hub.py` | `test_coordinator_hub.py` | Agent registration, status updates, multi-agent threading, instruction delivery, status transitions, error reporting, blocked agent detection, summary stats, closed-state behavior, input validation. |
| `help_protocol.py` | `test_help_protocol.py` | Team registration, capability matching, help request/offer/accept flow, idle detection, auto-assignment, concurrent help offers (only one wins). |
| `direct_channels.py` | `test_direct_channels.py` | Channel creation/lifecycle (direct, topic, join, leave, archive), direct messaging, presence tracking, progress broadcasting, bottleneck detection, multiple concurrent channels. |
| `work_stealing.py` | `test_work_stealing.py` | Enqueue/steal work items, concurrent steal attempts, pipeline creation/stage progression, pipeline auto-trigger, scratchpad CRUD, scratchpad TTL expiration. |
| `bus_core.py` | `test_bus_core.py` | sanitize_channel, bus_write, bus_read, retry_on_busy decorator, VALID_MSG_TYPES, init_db, is_busy_or_locked, MAX_MESSAGE_BYTES. |
| `db_utils.py` | `test_db_utils.py` | get_connection, ensure_schema, atomic_update, safe_close. |

### Specialized Test Files

| Test File | What's Tested |
|---|---|
| `test_operation_health_coord.py` | Bug-fix verification for specific bug IDs: channel sanitization (BUG-CORE-006), retry_on_busy functools.wraps (BUG-CORE-001), get_presence return type (BUG-CORE-021), dashboard connection management (NEW-COORD-008), pipeline stage atomicity (NEW-COORD-015/016), progress validation (BUG-CORE-003), error reporting resets progress (BUG-CORE-004), started_at preservation (BUG-CORE-002), help_protocol close idempotency (BUG-CORE-008), work_item existence check (BUG-CORE-009), fulfill_help authorization (BUG-CORE-010), priority validation (BUG-CORE-024), mark_work_available rowcount (BUG-CORE-019), complete_work_item team filter (BUG-CORE-020). |
| `test_unknown_bugs_x.py` | Previously unreported bugs in coordinator_hub, help_protocol, direct_channels, and work_stealing. Tests pass while demonstrating buggy behavior. |

### Stress Test Coverage

| Test File | Scenarios |
|---|---|
| `test_stress_direct_channels.py` | High concurrency channels, large message volumes, boundary conditions, race conditions, bottleneck detection under load. |
| `test_stress_help_protocol.py` | High concurrency help requests, volume stress, race conditions on help offers, error handling, resource exhaustion, zombie detection. |
| `test_stress_work_stealing.py` | WorkStealing/PipelineManager/Scratchpad under high concurrency, large data volumes, adversarial timing. Uses threading.Barrier for synchronized starts and 10-second timeouts. |

---

## Test Gaps

### Modules with NO tests

| Module | Risk | Notes |
|---|---|---|
| **`multi_team_runner.py`** | **CRITICAL** | The multi-team runner (SOP-007) has zero test coverage. This is the primary orchestration engine -- the component the SDK launcher wraps. No tests for team config parsing, agent registration lifecycle, heartbeat monitoring, dead agent detection, phase management, or clean shutdown. |
| **`think_tank.py`** | **HIGH** | The findings convergence system (SOP-008) has no tests. No coverage for findings collection, deduplication (SHA-256 fingerprinting), category grouping, roadmap generation, or file-based findings ingestion. |
| **`dashboard.py`** | **MEDIUM** | The real-time monitoring dashboard (SOP-009) has no dedicated tests. Only indirect coverage via test_operation_health_coord.py for connection management (NEW-COORD-008). |
| **`bus_cli.py`** | **MEDIUM** | The CLI wrapper has no integration tests. All subcommands (init, register, write, read, status, help-request, etc.) are untested end-to-end. |
| **`sdk_launcher.py`** | **CRITICAL** | The SDK launcher itself has no tests. |
| **`sdk_config.py`** | **HIGH** | The SDK configuration module has no tests. |

### Features/scenarios lacking test coverage

1. **End-to-end multi-team lifecycle** -- No integration test that launches multiple teams, runs them through phases, and verifies clean shutdown.
2. **Cross-module integration** -- No tests verify that help_protocol + direct_channels + work_stealing work together in a realistic scenario.
3. **Failure recovery** -- No tests for SOP-021 scenarios: agent crash recovery, SQLite lock contention recovery, bus file corruption handling.
4. **Phase signal propagation** -- No tests verify that phase signals reach all registered agents.
5. **Agent relaunch after crash** -- No tests for detecting a dead agent and relaunching it with preserved context.
6. **Bus message TTL expiration** -- Limited coverage; no tests verify that expired messages are correctly filtered during reads across modules.
7. **Pipeline DAG validation** -- No tests for detecting circular dependencies in pipeline stage definitions.

---

## Summary for SDK Launcher Design

The SDK launcher must:

1. **Follow SOP-011**: Launch all teams in parallel by default
2. **Implement SOP-007**: Team config, registration, heartbeat, phase management, shutdown
3. **Generate prompts per SOP-013**: 6-part canonical template
4. **Integrate SOP-012 monitoring**: JSONL status file with defined schema
5. **Handle SOP-021 failure recovery**: Crash detection, partial output preservation, relaunch
6. **Verify SOP-019 prerequisites**: Environment checks before launch

The **biggest risk** is that `multi_team_runner.py` (the component the SDK launcher wraps) has zero test coverage, and the SDK launcher + config modules themselves also lack tests. Establishing test coverage for these modules should be the highest priority.
