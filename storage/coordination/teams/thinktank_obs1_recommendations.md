# THINK-TANK-OBS-1: Process Improvement Recommendations

**Date:** 2026-03-11
**Observer:** THINK-TANK-OBS-1
**Scope:** Data gathering and sorting team workflows within the multi-agent coordination system

---

## 1. Current Process Assessment

### Strengths

1. **Robust concurrency primitives.** The coordination system uses SQLite WAL mode consistently across all modules (`coordinator_hub.py`, `direct_channels.py`, `help_protocol.py`, `work_stealing.py`). The `_retry_on_busy` decorator with exponential backoff is applied uniformly, giving strong resilience to lock contention under moderate load.

2. **Well-layered architecture.** The system separates concerns cleanly:
   - `coordinator_hub.py` handles agent status visibility (hub-and-spoke)
   - `direct_channels.py` handles peer-to-peer messaging and presence
   - `help_protocol.py` handles idle detection and work redistribution
   - `work_stealing.py` handles shared queues, pipelines, and scratchpad
   - `think_tank.py` handles cross-team findings convergence

3. **Fire-and-forget bus notifications.** Bus writes never block database operations. This is critical -- a bus I/O failure cannot cascade into a coordination failure.

4. **Comprehensive failure recovery.** SOP-021 covers agent crashes, SQLite lock contention, bus corruption, coordinator failure, and network issues with concrete commands and checklists.

5. **Atomic work claiming.** Both `HelpProtocol.offer_help()` and `WorkStealing.steal_work()` use `BEGIN IMMEDIATE` transactions to prevent double-assignment races. This is correct and battle-tested.

### Weaknesses

1. **No gatherer-to-sorter handoff protocol.** The session config (`multi_team_session.json`) defines 4 data gathering teams and 3 sorter/archiver teams with dedicated channels (e.g., `dg-docs-1-to-sorters`), but no code or SOP defines what a "handoff" message looks like, how sorters know a batch is ready, or how to handle partial deliveries. This is the single largest gap.

2. **No data quality gate between gather and sort.** SOP-017 defines result grading (`grade_research.py`), but there is no enforcement point. A gatherer can push ungraded or low-quality data to sorters without any check. Sorters have no mechanism to reject or request re-gathering.

3. **Sorter assignment is static.** The three sorter teams have fixed focus areas (documentation, algorithms, best practices). If DATA-GATHER-DOCS-1 and DATA-GATHER-DOCS-2 both flood SORT-ARCHIVE-1 with documentation data simultaneously, SA-1 becomes a bottleneck while SA-2 and SA-3 may be idle. There is no load-balancing across sorters.

4. **Think tank has no feedback loop.** THINK-TANK-OBS-1 and OBS-2 observe and broadcast recommendations, but there is no mechanism for teams to acknowledge, accept, or reject recommendations. Recommendations go into a JSONL file and may never be read.

5. **Multiple overlapping status systems.** Agent status is tracked in at least three places: the `agent_status` table in `coordinator_hub.py`, the `team_status` table in `help_protocol.py`, and the `progress` table in `direct_channels.py`. These are not synchronized. A team could appear "idle" in one system and "working" in another.

6. **No pipeline definition for the gather-sort-archive workflow.** `PipelineManager` in `work_stealing.py` supports multi-stage pipelines with dependency tracking, but no pipeline is defined for the core data flow. This is an unused capability that directly solves the handoff problem.

---

## 2. Data Flow Optimization

### Current State (Implicit)
```
DATA-GATHER-* --> (channel message) --> SORT-ARCHIVE-* --> (file write) --> archive
```
The handoff is ad-hoc. No schema, no acknowledgment, no quality gate.

### Recommended State (Explicit Pipeline)

```
DATA-GATHER-*                    SORT-ARCHIVE-*                 ARCHIVE
  |                                  |                            |
  +--> [1] Gather batch              |                            |
  +--> [2] Grade results             |                            |
  |    (grade_research.py)           |                            |
  +--> [3] Write batch manifest ---> |                            |
  |    to scratchpad                 |                            |
  |    (namespace="handoff")         |                            |
  |                                  +--> [4] Claim batch         |
  |                                  |    (work stealing)         |
  |                                  +--> [5] Validate + sort     |
  |                                  +--> [6] Write to archive ---+
  |                                  +--> [7] Ack completion      |
  +--> [8] Verify ack <------------ +                            |
```

### Implementation Steps

**Step A: Define a batch manifest schema.**
Every gathering team writes a manifest to the global scratchpad when a batch is ready:

```python
scratchpad.write(
    key=f"batch-{team}-{batch_num}",
    value={
        "team": "DATA-GATHER-DOCS-1",
        "batch_num": 3,
        "item_count": 47,
        "grade_distribution": {"A": 5, "B": 18, "C": 20, "D": 4},
        "quality_score": 0.72,  # weighted average
        "output_file": "storage/coordination/teams/dg-docs-1/output/batch_003.json",
        "categories": ["python-docs", "api-reference"],
        "ready_at": time.time()
    },
    namespace="handoff",
    ttl=7200
)
```

**Step B: Use PipelineManager for the workflow.**
Create a pipeline per gathering batch:

```python
pipeline_id = pm.create_pipeline(f"gather-sort-{team}-batch-{n}", [
    {"name": "gather", "team": gathering_team},
    {"name": "grade", "team": gathering_team, "depends_on": ["gather"]},
    {"name": "sort", "depends_on": ["grade"]},  # no team assigned -- claimed by sorter
    {"name": "archive", "depends_on": ["sort"]},
])
```

This leverages the existing `trigger_downstream()` method to automatically notify sorters when grading completes.

**Step C: Use WorkStealing for sorter load balancing.**
Instead of statically assigning sorters to categories, sorters steal batch work items from a shared queue:

```python
# Gatherer enqueues after grading:
ws.enqueue_work(
    title=f"Sort batch {batch_num} from {team}",
    description=json.dumps(manifest),
    priority=quality_priority  # higher quality = higher priority
)

# Any idle sorter steals:
work = ws.steal_work()
if work:
    manifest = json.loads(work["description"])
    # sort and archive...
    ws.complete_work(work["id"], {"archived_path": "...", "items_archived": 47})
```

This naturally load-balances: if SA-1 is busy, SA-2 or SA-3 picks up the next batch.

---

## 3. Bottleneck Prevention

### Bottleneck 1: Sorter Overload
**Risk:** All gatherers complete simultaneously, flooding the sort queue.
**Prevention:**
- Use the `priority` field in `WorkStealing.enqueue_work()` to stagger processing. Higher-quality batches (more A/B grades) get lower priority numbers (processed first).
- Gatherers should produce smaller, more frequent batches (10-20 items) rather than one large dump. This creates a steady stream that sorters can process incrementally.
- Monitor with `ws.get_queue_depth()`. If depth exceeds 3x the number of sorter teams, trigger `help_protocol.auto_assign_idle_teams()` to reassign idle gatherers as temporary sorters.

### Bottleneck 2: Single Gatherer Falling Behind
**Risk:** One DATA-GATHER team is slower (harder topic, API rate limits), blocking the overall pipeline.
**Prevention:**
- Each gatherer should call `direct_channels.update_progress()` after every batch. The coordinator can call `get_slowest_team()` to identify laggards early.
- If a gatherer falls below 50% of the average progress, auto-split its remaining work into the shared queue so other gatherers can help.
- SOP-017's partial result saving (every 10 queries) already supports this -- partial results can be handed off.

### Bottleneck 3: SQLite Write Contention at Scale
**Risk:** With 4 gatherers + 3 sorters + 3 think tanks + 3 programming teams (13 teams, ~30 agents) all writing to the same SQLite database, WAL contention becomes real.
**Prevention:**
- Use separate databases for separate concerns. The coordination hub, help protocol, work stealing, and direct channels each open their own database. Ensure they use different files (currently each module's `_open_db` creates its own, but if callers pass the same `db_path`, they contend).
- Recommended layout:
  - `db/coordinator.db` -- agent status, coordinator instructions
  - `db/channels.db` -- channels, presence, progress, read offsets
  - `db/help.db` -- work items, help requests, team status, capabilities
  - `db/work.db` -- work queue, pipelines, pipeline stages, scratchpad
- Add jitter to writes as SOP-021 recommends: `time.sleep(random.uniform(0, 0.3))` before non-urgent status updates.

### Bottleneck 4: Bus File Growth
**Risk:** High-volume channels (especially `global`) grow unbounded during long sessions.
**Prevention:**
- Implement bus file rotation. When a `.jsonl` file exceeds 1MB, rename it to `{channel}_{timestamp}.jsonl.archived` and create a fresh file.
- The `_bus_read` function already uses byte offsets, so rotation requires updating the read offset tracking in `direct_channels.py` to handle file replacement.
- Alternatively, have the coordinator periodically call `scratchpad.cleanup_expired()` and truncate bus files older than TTL.

---

## 4. Quality Assurance

### QA Gate 1: Pre-Handoff Grading (Gatherer Responsibility)
Every batch MUST be graded before handoff. Enforce this by requiring the batch manifest to include a `quality_score` field. Sorters should reject manifests without it.

**Minimum quality threshold:** 60% of items graded B or above. If a batch fails this threshold:
1. The gatherer re-runs with refined search terms.
2. If re-run still fails, the batch is marked as "supplementary" (cannot be used for primary analysis).

### QA Gate 2: Schema Validation (Sorter Responsibility)
Sorters validate each item against the findings taxonomy defined in SOP-016 section 4:
- Required fields: category (from the taxonomy), title, content, priority (from severity levels)
- Items missing required fields are logged and returned to the gatherer queue.

### QA Gate 3: Deduplication (Sorter Responsibility)
Use `think_tank.py`'s `_fingerprint()` function for deduplication. Before archiving, check if the fingerprint already exists:

```python
fp = _fingerprint(finding)
existing = scratchpad.read(f"archived-{fp}", namespace="dedup")
if existing:
    # Duplicate. Merge if higher priority, skip otherwise.
    pass
else:
    scratchpad.write(f"archived-{fp}", finding, namespace="dedup", ttl=86400)
    # Proceed to archive
```

### QA Gate 4: Cross-Validation (Think Tank Responsibility)
Think tanks should run `think_tank.py` periodically (not just at the end) to detect:
- Contradictory findings from different gatherers
- Category imbalance (too much in one category, gaps in others)
- Priority inflation (everything marked "critical")

Run with `python think_tank.py --summary --no-write` every 15 minutes during active gathering.

---

## 5. Scaling Recommendations

### Adding More Gathering Teams
**Current:** 4 gatherers (12 agents), 3 sorters (6 agents), 3 think tanks, 3 programming teams.

**To add a 5th gatherer:**
1. Add the team definition to `multi_team_session.json` with unique channels.
2. No changes needed to sorters -- the work-stealing queue absorbs the additional output.
3. Monitor `ws.get_queue_depth()` -- if consistently > 5, add a 4th sorter.

**Scaling rule of thumb:** 1 sorter per 2 gatherers. Sorters are faster than gatherers because sorting is cheaper than external API calls.

### Adding More Sorter Teams
1. Sorters are stateless consumers of the work-stealing queue. Adding a sorter requires only:
   - A new team entry in `multi_team_session.json`
   - Launching the agent with access to the shared work-stealing database
2. No code changes needed. The `steal_work()` atomic semantics handle any number of consumers.

### Scaling to 50+ Agents
Beyond SOP-014's "large" tier (16-30 agents), additional measures are needed:

1. **Introduce team leads.** Each team of 3 agents designates one as lead. The lead:
   - Aggregates sub-agent status into a single team-level report
   - Handles intra-team work distribution
   - Is the only agent that writes to the coordination database

   This reduces database writers from 50 to ~15 (one per team).

2. **Shard the work-stealing queue by category.** Instead of one `work_queue` table, create per-category queues:
   - `work_queue_docs`
   - `work_queue_algo`
   - `work_queue_practices`

   Sorters subscribe to relevant categories. This reduces contention on the `BEGIN IMMEDIATE` lock in `steal_work()`.

3. **Use the bus more, SQLite less.** For read-heavy operations (progress monitoring, presence tracking), shift to bus-based polling. Write progress updates to bus channels and let consumers read from JSONL files. Reserve SQLite for transactional operations (work claiming, help request acceptance).

4. **Implement backpressure.** If the sort queue exceeds a threshold, gatherers should slow down. This prevents memory/disk exhaustion:

```python
depth = ws.get_queue_depth()
if depth > MAX_QUEUE_DEPTH:
    dc.update_progress(phase="backpressure", progress_pct=current_pct,
                       items_total=total, items_done=done,
                       bottleneck="sort-queue-full")
    time.sleep(depth * 0.5)  # linear backoff
```

---

## 6. Immediate Action Items

These can be implemented by programming teams without architectural changes:

| Priority | Action | Owner | Effort |
|----------|--------|-------|--------|
| HIGH | Define batch manifest schema and write to scratchpad on gather completion | PROG-TEAM-1 | 2 hours |
| HIGH | Sorters use `steal_work()` instead of static channel assignment | PROG-TEAM-2 | 2 hours |
| HIGH | Add quality_score to batch manifests; sorters reject batches below threshold | DATA-GATHER-* | 1 hour |
| MEDIUM | Create a pipeline definition for gather-grade-sort-archive using PipelineManager | PROG-TEAM-3 | 3 hours |
| MEDIUM | Split coordination into 4 separate SQLite databases | PROG-TEAM-3 | 2 hours |
| MEDIUM | Add periodic think_tank.py runs during gathering (not just post-mortem) | THINK-TANK-OBS-1 | 30 min |
| LOW | Implement bus file rotation at 1MB threshold | PROG-TEAM-3 | 2 hours |
| LOW | Add backpressure mechanism to gatherers based on queue depth | PROG-TEAM-1 | 1 hour |

---

## 7. Status System Consolidation Recommendation

The three overlapping status systems (`agent_status` in coordinator_hub, `team_status` in help_protocol, `progress` in direct_channels) should be unified. Recommended approach:

- **Keep `coordinator_hub.agent_status`** as the single source of truth for individual agent state.
- **Keep `help_protocol.team_status`** for team-level aggregated state (it already counts work items).
- **Deprecate `direct_channels.progress`** -- its fields (phase, progress_pct, items_total, items_done, bottleneck) should be added as columns to `help_protocol.team_status`.
- Have `direct_channels.update_progress()` write to `team_status` instead of its own `progress` table.

This eliminates one table and one potential source of stale/contradictory data.

---

*End of THINK-TANK-OBS-1 analysis. Broadcast message sent to tt-obs-1-broadcast channel.*
