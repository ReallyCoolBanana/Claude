# Think Tank Alpha: Communication Architecture Plan

> **Team:** Think Tank Alpha (Lead + 2 subagents)
> **Date:** 2026-03-11
> **Input:** KB-0018, KB-0019, Proto A/B bus implementations, SOPs-prototype-testing.md
> **Deliverable:** Communication plan for live data gathering prototype tests

---

## 1. Bus Channel Design

### 1.1 Channel Inventory

| Channel Name | Purpose | Message Types | Writers | Readers | Est. Volume |
|---|---|---|---|---|---|
| `global` | System-wide announcements, phase signals, coordinator commands | `phase-signal`, `info`, `heartbeat` | Coordinator, all agents | All agents | ~5 msg/min |
| `team-{id}` (e.g., `team-data-a`) | Coordinator directives to a specific team; team-internal coordination | `request`, `response`, `info` | Coordinator (directives), team lead (intra-team) | Team members only | ~10 msg/min per team |
| `topic-data-requests` | Work assignment distribution from coordinator/leads to workers | `request`, `response` | Coordinator, team leads | Workers | ~20 msg/min across all teams |
| `topic-data-results` | Gathered data results flowing back to coordinator | `response`, `info` | Workers | Coordinator, team leads | ~20 msg/min (mirrors request volume) |
| `topic-rate-limits` | Rate limit alerts and backoff coordination | `info` (rate-limit-alert subtype) | Any agent hitting a limit | Coordinator, all agents making API calls | ~2-5 msg/min (bursty) |
| `topic-quality` | Data quality validation results | `info` (quality-check subtype) | Quality checker agent or lead | Coordinator, team leads | ~5 msg/min |
| `topic-blockers` | Blocker escalation (per KB-0019 section 3.2) | `blocker` | Any blocked agent | Coordinator | ~0-2 msg/min (event-driven) |
| `heartbeat` | Dedicated heartbeat channel to avoid polluting `global` | `heartbeat` | All agents | Coordinator | ~20 msg/min (10 agents x 2/min) |

### 1.2 Channel Naming Convention

```
global                    # Exactly one, always exists
heartbeat                 # Dedicated heartbeat traffic
team-{team-id}            # One per team: team-data-a, team-data-b, team-data-c
topic-{topic}             # Thematic: topic-data-requests, topic-data-results, etc.
```

### 1.3 Volume Estimates by Test Scenario

| Scenario | Agents | Duration | Est. Total Messages | Bus File Size |
|---|---|---|---|---|
| S1: Single-source | 3 | 2 min | ~120 | ~24 KB |
| S2: Multi-source parallel | 9 | 3 min | ~500 | ~100 KB |
| S3: Full coordinator loop | 10 | 5 min | ~900 | ~180 KB |
| S4: Failure recovery | 7 | 3 min | ~350 | ~70 KB |

All well within the 50 MB ceiling identified in KB-0019 section 7.3.

---

## 2. Message Schemas

All messages use the existing `Message` dataclass envelope (shared by both prototypes). The `body` field carries the domain-specific payload. Below are the JSON schemas for the `body` field of each data gathering message type.

### 2.1 `data-request` — Query Assignment

Sent by coordinator or team lead to assign a data gathering task to a worker.

```json
{
  "msg_type_hint": "data-request",
  "body": {
    "request_id": "dreq-0001",
    "source": "openalex",
    "query": "machine learning transformer architecture",
    "params": {
      "max_results": 50,
      "date_range": ["2025-01-01", "2026-03-11"],
      "fields": ["title", "abstract", "doi", "cited_by_count"]
    },
    "priority": "normal",
    "deadline_ts": 1741700000.0,
    "assigned_to": "data-a-sub-1"
  }
}
```

**Field reference:**

| Field | Type | Required | Description |
|---|---|---|---|
| `request_id` | string | yes | Unique request identifier (for correlation) |
| `source` | string | yes | API source: `openalex`, `hackernews`, `wikipedia`, `ddg` |
| `query` | string | yes | Search query or topic |
| `params` | object | no | Source-specific parameters |
| `priority` | string | no | `high`, `normal`, `low` (default: `normal`) |
| `deadline_ts` | float | no | Unix timestamp deadline for this request |
| `assigned_to` | string | no | Target agent_id (if directed assignment) |

**Bus envelope:** Use `type: "request"`, publish to `topic-data-requests` (broadcast) or `team-{id}` (directed).

### 2.2 `data-result` — Gathered Data

Sent by a worker back to the coordinator/lead after completing a data request.

```json
{
  "msg_type_hint": "data-result",
  "body": {
    "request_id": "dreq-0001",
    "source": "openalex",
    "status": "complete",
    "data_points": 47,
    "artifact_path": "/tmp/claude-proto-test-a/results/dreq-0001.json",
    "summary": "47 papers found for query, 12 with >100 citations",
    "duration_ms": 3200,
    "api_calls_made": 3,
    "errors": []
  }
}
```

**Field reference:**

| Field | Type | Required | Description |
|---|---|---|---|
| `request_id` | string | yes | Correlates to original `data-request` |
| `source` | string | yes | API source used |
| `status` | string | yes | `complete`, `partial`, `failed` |
| `data_points` | int | yes | Number of data points gathered |
| `artifact_path` | string | yes | Path to full result file (keeps bus messages <4KB) |
| `summary` | string | no | Human-readable summary |
| `duration_ms` | int | no | Time spent on this request |
| `api_calls_made` | int | no | Number of API calls consumed |
| `errors` | array | no | List of error strings encountered |

**Bus envelope:** Use `type: "response"`, `in_reply_to: "<original message id>"`, publish to `topic-data-results`.

### 2.3 `rate-limit-alert` — API Rate Limit Hit

Sent immediately when an agent encounters or approaches a rate limit.

```json
{
  "msg_type_hint": "rate-limit-alert",
  "body": {
    "api": "openalex",
    "limit_type": "requests_per_minute",
    "limit_value": 100,
    "current_usage": 98,
    "retry_after_sec": 12,
    "severity": "warning",
    "agent_id": "data-a-sub-1",
    "recommendation": "backoff"
  }
}
```

**Field reference:**

| Field | Type | Required | Description |
|---|---|---|---|
| `api` | string | yes | Which API is rate-limited |
| `limit_type` | string | yes | `requests_per_minute`, `requests_per_second`, `tokens_per_minute` |
| `limit_value` | int | yes | The rate limit threshold |
| `current_usage` | int | yes | Current usage count in the window |
| `retry_after_sec` | float | no | Seconds until limit resets |
| `severity` | string | yes | `warning` (approaching), `critical` (hit), `resolved` (cleared) |
| `agent_id` | string | yes | Agent that hit the limit |
| `recommendation` | string | no | `backoff`, `pause`, `switch-source` |

**Bus envelope:** Use `type: "info"`, publish to **both** `topic-rate-limits` and `global`. TTL should be set to `60` (rate limit alerts expire quickly).

### 2.4 `progress-update` — Worker Progress

Periodic progress report from workers. Sent on the heartbeat channel alongside the heartbeat itself.

```json
{
  "msg_type_hint": "progress-update",
  "body": {
    "request_id": "dreq-0001",
    "progress_pct": 65,
    "data_points_so_far": 31,
    "current_action": "Fetching page 3 of OpenAlex results",
    "estimated_completion_sec": 8,
    "api_calls_remaining": 1
  }
}
```

**Field reference:**

| Field | Type | Required | Description |
|---|---|---|---|
| `request_id` | string | yes | Which request this progress is for |
| `progress_pct` | int | yes | 0-100 completion percentage |
| `data_points_so_far` | int | no | Running count of data points gathered |
| `current_action` | string | no | Human-readable current activity |
| `estimated_completion_sec` | float | no | Estimated seconds to finish |
| `api_calls_remaining` | int | no | Estimated remaining API calls |

**Bus envelope:** Use `type: "heartbeat"`, publish to `heartbeat` channel. This is combined with the standard heartbeat so a single message serves dual purpose (liveness + progress).

### 2.5 `quality-check` — Data Quality Validation

Sent after a data-result is validated (by the lead or a dedicated quality agent).

```json
{
  "msg_type_hint": "quality-check",
  "body": {
    "request_id": "dreq-0001",
    "artifact_path": "/tmp/claude-proto-test-a/results/dreq-0001.json",
    "verdict": "pass",
    "checks": {
      "json_valid": true,
      "no_duplicates": true,
      "schema_conformant": true,
      "source_attribution": true,
      "timestamp_range": true,
      "min_data_points": true
    },
    "issues": [],
    "data_points_validated": 47,
    "data_points_rejected": 0
  }
}
```

**Field reference:**

| Field | Type | Required | Description |
|---|---|---|---|
| `request_id` | string | yes | Which data-result was checked |
| `artifact_path` | string | yes | Path to the data file that was checked |
| `verdict` | string | yes | `pass`, `warn`, `fail` |
| `checks` | object | yes | Individual check results (all boolean) |
| `issues` | array | no | List of issue description strings |
| `data_points_validated` | int | no | Count of valid data points |
| `data_points_rejected` | int | no | Count of rejected data points |

**Bus envelope:** Use `type: "info"`, publish to `topic-quality`. Use `in_reply_to` referencing the `data-result` message id.

---

## 3. Heartbeat Configuration

### 3.1 Recommended Interval: 15 seconds

| Parameter | Value | Rationale |
|---|---|---|
| **Heartbeat interval** | 15 seconds | API calls take 1-5 sec each. A 15s interval gives 3-15 API calls between heartbeats, providing useful progress granularity without excessive bus traffic. |
| **Stale threshold** | 45 seconds (3 missed) | Conservative: declares agent dead after 3 missed heartbeats. Avoids false positives from a single slow API call (max 5s) or transient I/O stall. |
| **Critical threshold** | 90 seconds (6 missed) | Coordinator triggers recovery action (reassign work, alert). |
| **TTL on heartbeat messages** | 30 seconds | Heartbeats older than 2 intervals are irrelevant. Short TTL keeps BusReader filtering fast. |

### 3.2 Rationale for 15s Over Alternatives

- **30s (KB-0019 suggestion):** Too coarse for data gathering. If an agent dies mid-API-call, detection takes 90s (3 x 30s). During a 2-minute test (S1), that is 75% of the test window wasted.
- **5s:** Too chatty. With 10 agents, that is 120 heartbeats/min = 2/sec. Adds measurable I/O overhead, especially for Proto A (JSONL appends). FIFO-based Proto B handles this better but we want consistent config.
- **15s:** Sweet spot. 10 agents produce 40 heartbeats/min (~0.67/sec). Detection time is 45s worst case. For a 5-minute test (S3), that gives 6+ detection windows.

### 3.3 Heartbeat Message Format

Heartbeat messages carry progress data (section 2.4) to avoid sending separate progress messages:

```json
{
  "type": "heartbeat",
  "channel": "heartbeat",
  "body": {
    "status": "active",
    "current_task": "dreq-0001",
    "progress_pct": 65,
    "data_points_so_far": 31,
    "api_calls_made": 7,
    "uptime_sec": 120
  }
}
```

---

## 4. Channel Routing Matrix

### 4.1 Write Permissions

| Agent Role | `global` | `heartbeat` | `team-{own}` | `team-{other}` | `topic-data-requests` | `topic-data-results` | `topic-rate-limits` | `topic-quality` | `topic-blockers` |
|---|---|---|---|---|---|---|---|---|---|
| **Coordinator** | WRITE | READ | WRITE (directives) | WRITE (directives) | WRITE | READ | READ | READ | READ |
| **Team Lead** | read | WRITE | WRITE | -- | WRITE (sub-tasks) | READ | WRITE | WRITE | WRITE |
| **Worker** | read | WRITE | read | -- | read | WRITE | WRITE | -- | WRITE |

Legend: **WRITE** = publishes to this channel. **READ** = subscribes to this channel. **read** (lowercase) = reads but does not write. **--** = no access.

### 4.2 Read Subscriptions

| Agent Role | Channels Subscribed |
|---|---|
| **Coordinator** | `global`, `heartbeat`, `topic-data-results`, `topic-rate-limits`, `topic-quality`, `topic-blockers` |
| **Team Lead** | `global`, `heartbeat` (own team filter), `team-{own}`, `topic-data-requests` (for assigned work), `topic-data-results` (own team filter), `topic-rate-limits` |
| **Worker** | `global`, `team-{own}`, `topic-data-requests` (for assigned work), `topic-rate-limits` |

### 4.3 Message Flow Diagram

```
                          +-----------+
                          |Coordinator|
                          +-----+-----+
                                |
                   WRITE: global, team-{id}, topic-data-requests
                   READ:  heartbeat, topic-data-results, topic-rate-limits,
                          topic-quality, topic-blockers
                                |
           +--------------------+--------------------+
           |                    |                    |
     +-----+-----+       +-----+-----+       +-----+-----+
     | Team A Lead|       | Team B Lead|       | Team C Lead|
     +-----+-----+       +-----+-----+       +-----+-----+
           |                    |                    |
     +-----+-----+       +-----+-----+       +-----+-----+
     |  W1  | W2  |       |  W1  | W2  |       |  W1  | W2  |
     +------+-----+       +------+-----+       +------+-----+

Workers WRITE: topic-data-results, topic-rate-limits, heartbeat
Workers READ:  global, team-{own}, topic-data-requests
```

### 4.4 Enforcement Note

Channel write restrictions are enforced by **convention**, not code. Both Proto A and Proto B allow any agent to write to any channel file. The coordinator should log and flag messages from unexpected sources on restricted channels (e.g., a worker writing to `team-{other}`).

---

## 5. Proto A vs Proto B Communication Differences

### 5.1 Architectural Impact on Channel Design

| Dimension | Proto A (JSONL) | Proto B (FIFO) |
|---|---|---|
| **Channel creation** | Create `.jsonl` file (or it auto-creates on first write via `O_CREAT`) | Must call `os.mkfifo()` before use; reader must `open()` the FIFO before writer publishes |
| **Reader setup order** | No ordering constraint. Readers can start before or after writers. BusReader seeks to offset 0 or end-of-file. | **Reader MUST open the FIFO before writers send.** Otherwise messages go to spillover file. Start all `PipeBusReader.open()` calls before any worker begins publishing. |
| **Multi-reader support** | Native. Multiple BusReaders open the same `.jsonl` file independently, each tracking their own offset. | **Not native.** FIFO data is consumed by the first reader. For multi-reader channels (`global`, `topic-rate-limits`), use one of: (a) a fan-out agent that reads the FIFO and writes to per-reader spillover files, or (b) duplicate the channel as separate FIFOs per reader. |
| **Message persistence** | Messages persist in the JSONL file until cleanup. Late-joining readers can read history. | Messages are consumed on read and gone. Spillover file provides partial persistence but is drained and deleted by the first reader. |
| **Backpressure** | None. Writers always succeed (append to file). Readers may fall behind. | Natural. If FIFO buffer is full (64KB on Linux), writer gets `EAGAIN` and spills. This signals a slow reader. |
| **Channel file growth** | Linear growth. Monitor file size; rotate if >10MB (per KB-0019). | No growth for FIFO. Spillover files grow if reader is absent. |

### 5.2 Channel Adaptations for Proto B

Because Proto B FIFOs are single-consumer, the following adjustments are needed:

**Problem:** Channels like `global` and `topic-rate-limits` have multiple readers (coordinator + all team leads + workers).

**Solution: Fan-out pattern for shared channels**

```
Proto A:                          Proto B:

global.jsonl  <-- all read        global.fifo  <-- coordinator reads
                                  global-lead-a.spill  \
                                  global-lead-b.spill   } coordinator fans out
                                  global-lead-c.spill  /
                                  global-worker-a1.spill  ...etc
```

For Proto B testing, implement a lightweight **fan-out relay** in the coordinator:

```python
# Proto B fan-out relay (runs in coordinator)
class FanOutRelay:
    def __init__(self, source_channel: str, target_agents: list[str]):
        self.reader = PipeBusReader(pipes_dir, source_channel)
        self.reader.open()
        self.writers = {
            agent: PipeBusWriter(pipes_dir, "relay", "coordinator")
            for agent in target_agents
        }

    def relay(self):
        messages = self.reader.poll()
        for msg in messages:
            for agent_id, writer in self.writers.items():
                writer.publish(
                    f"{msg.channel}-{agent_id}",
                    msg.type, msg.body, msg.ttl
                )
```

**Alternative (simpler, recommended for initial tests):** Restrict shared channels to coordinator-only reading. The coordinator relays relevant messages to teams via their `team-{id}` FIFO. This avoids the fan-out complexity entirely.

### 5.3 Initialization Sequence Differences

**Proto A — Order does not matter:**

```
1. Create coordination directory
2. Initialize SQLite state
3. Start coordinator
4. Start agents (any order)
   -- Agents auto-create .jsonl files on first write
   -- BusReaders start from offset 0
```

**Proto B — Order is critical:**

```
1. Create coordination directory + pipes/ subdirectory
2. Create all FIFOs: os.mkfifo() for every planned channel
3. Initialize mmap shared state
4. Start coordinator (opens FIFOs for reading on shared channels)
5. WAIT until coordinator has all FIFOs open
6. Start agents (they can now write to FIFOs without spillover)
   -- Late-started agents read spillover files first
```

### 5.4 Proto A vs Proto B Channel Count Recommendation

| Scenario | Proto A Channels | Proto B Channels | Notes |
|---|---|---|---|
| S1 (3 agents) | 5 channels | 5 + 3 fan-out = 8 | Fan-out for `global` to 3 agents |
| S2 (9 agents) | 8 channels | 8 + 9 fan-out = 17 | Fan-out for `global` + `topic-rate-limits` |
| S3 (10 agents) | 9 channels | 9 + 10 fan-out = 19 | Or use coordinator-relay simplification |
| S4 (7 agents) | 7 channels | 7 + 7 fan-out = 14 | |

**Recommendation for Proto B tests:** Use the coordinator-relay simplification (no fan-out) for S1 and S4. Use full fan-out only for S2 and S3 where cross-team communication is the primary thing being tested.

### 5.5 Configuration Summary Table

| Config Parameter | Proto A Value | Proto B Value |
|---|---|---|
| Bus directory | `{coord_dir}/bus/` | `{coord_dir}/pipes/` |
| Channel file extension | `.jsonl` | `.fifo` (+ `.spill` for fallback) |
| Writer class | `BusWriter` | `PipeBusWriter` |
| Reader class | `BusReader` | `PipeBusReader` (+ `.open()` call) |
| Multi-reader strategy | Native (independent offsets) | Fan-out relay or coordinator-relay |
| Heartbeat interval | 15 seconds | 15 seconds (same) |
| Stale threshold | 45 seconds | 45 seconds (same) |
| Max message size | 4096 bytes | 4096 bytes (PIPE_BUF) |
| Message TTL (heartbeat) | 30 seconds | 30 seconds |
| Message TTL (data messages) | 300 seconds | 300 seconds |
| Init order dependency | None | Reader before writer |
| Cleanup | Delete `.jsonl` files | Delete `.fifo` + `.spill` files |

---

## 6. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Proto B fan-out adds latency | Medium | Low | Use coordinator-relay simplification; measure overhead separately |
| Heartbeat floods on `heartbeat` channel at scale | Low | Medium | Dedicated channel isolates heartbeat I/O from data messages; 15s interval keeps volume manageable |
| Message exceeds 4096 byte PIPE_BUF limit | Low | High (corruption) | All data payloads use `artifact_path` pattern: write large data to file, reference path in message body |
| Stale spillover files in Proto B | Medium | Low | Coordinator runs `SpilloverCleaner` (already in Proto B); add spillover age check to post-test cleanup |
| Channel name collision | Very Low | Medium | Strict naming convention; sanitization in both bus implementations already replaces `/` and `..` |

---

## 7. Implementation Checklist

- [ ] Create channel JSONL/FIFO files for all channels listed in section 1.1
- [ ] Register message body schemas as validation functions (or JSON Schema files)
- [ ] Configure heartbeat interval to 15s in coordinator config
- [ ] Set stale threshold to 45s, critical threshold to 90s
- [ ] For Proto B: implement fan-out relay or coordinator-relay pattern for shared channels
- [ ] For Proto B: enforce reader-before-writer initialization order
- [ ] Add `msg_type_hint` field to all `body` payloads for filtering (since the envelope `type` field uses the limited `VALID_MSG_TYPES` set)
- [ ] Test channel routing matrix with 3-agent smoke test before full scenario runs
- [ ] Verify all messages stay under 4096 bytes with representative payloads
