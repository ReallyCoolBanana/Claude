# Data Flow Map - Proto A Coordination System

**Generated**: 2026-03-11
**Author**: Research Team 3B (Data Flow Mapper)

---

## 1. SQLite Tables and Schemas

The system uses **three separate SQLite databases**, all configured with `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout` for concurrent access.

### 1.1 Coordinator Hub DB (`storage/coordination/coordinator_hub.py`)

**Database path**: Passed as `db_path` parameter to `CoordinatorHub.__init__`.

| Table | Purpose |
|---|---|
| `agent_status` | Central registry of all agents, their status, and progress |
| `coordinator_instructions` | Command queue from coordinator to agents |

**agent_status schema:**
```sql
CREATE TABLE IF NOT EXISTS agent_status (
    agent_id TEXT PRIMARY KEY,
    team TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'initializing',
    progress_pct REAL DEFAULT 0,
    current_task TEXT,
    blockers TEXT,
    findings_count INTEGER DEFAULT 0,
    output_files TEXT,
    error_message TEXT,
    started_at REAL NOT NULL,
    last_updated REAL NOT NULL
);
```

**coordinator_instructions schema:**
```sql
CREATE TABLE IF NOT EXISTS coordinator_instructions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_agent TEXT NOT NULL,
    instruction_type TEXT NOT NULL,
    payload TEXT,
    status TEXT DEFAULT 'pending',
    created_at REAL NOT NULL,
    read_at REAL
);
```

**Indexes**: `idx_agent_status_team(team)`, `idx_agent_status_status(status)`, `idx_coordinator_instructions_target_status(target_agent, status)`.

**Write patterns**: `INSERT INTO agent_status` (register), `UPDATE agent_status` (heartbeat/status updates), `INSERT INTO coordinator_instructions` (send instruction).
**Read patterns**: `SELECT * FROM agent_status` (list all, filter by team/status), `SELECT * FROM coordinator_instructions WHERE target_agent=? AND status='pending'` (poll for instructions, then mark read via `BEGIN IMMEDIATE` transaction).

### 1.2 Direct Channels DB (`storage/coordination/direct_channels.py`)

**Database path**: Passed as `db_path` to `DirectChannels.__init__`.

| Table | Purpose |
|---|---|
| `channels` | Registry of named communication channels (direct + topic) |
| `presence` | Team availability/status tracking |
| `progress` | Per-team phase/progress tracking |
| `read_offsets` | Byte offsets into JSONL files per team per channel |

**channels schema:**
```sql
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT UNIQUE NOT NULL,
    channel_type TEXT NOT NULL,
    created_by TEXT NOT NULL,
    participants TEXT NOT NULL,   -- JSON array
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL,
    last_activity REAL
);
```

**presence schema:**
```sql
CREATE TABLE IF NOT EXISTS presence (
    team TEXT PRIMARY KEY,
    status TEXT DEFAULT 'available',    -- {available, busy, helping, away}
    current_channels TEXT,
    last_seen REAL NOT NULL
);
```

**progress schema:**
```sql
CREATE TABLE IF NOT EXISTS progress (
    team TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    progress_pct REAL DEFAULT 0,
    items_total INTEGER DEFAULT 0,
    items_done INTEGER DEFAULT 0,
    estimated_completion_ts REAL,
    current_bottleneck TEXT,
    last_updated REAL NOT NULL
);
```

**read_offsets schema:**
```sql
CREATE TABLE IF NOT EXISTS read_offsets (
    team TEXT NOT NULL,
    channel TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team, channel)
);
```

### 1.3 Help Protocol / State DB (`storage/coordination/db/state.db`)

**Database path**: `storage/coordination/db/state.db` (used by both `help_protocol.py` and `bus_cli.py`).

| Table | Purpose |
|---|---|
| `work_items` | Trackable work units with assignment |
| `help_requests` | Cross-team help request lifecycle |
| `team_capabilities` | What each team can do |
| `team_status` | Idle detection and progress tracking |

**work_items schema:**
```sql
CREATE TABLE IF NOT EXISTS work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    estimated_minutes REAL,
    required_capabilities TEXT,
    assigned_to TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
```

**help_requests schema:**
```sql
CREATE TABLE IF NOT EXISTS help_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requesting_team TEXT NOT NULL,
    requesting_agent TEXT NOT NULL,
    work_item_id INTEGER REFERENCES work_items(id),
    description TEXT NOT NULL,
    required_capabilities TEXT,
    status TEXT DEFAULT 'open',
    accepted_by_team TEXT,
    accepted_by_agent TEXT,
    created_at REAL NOT NULL,
    resolved_at REAL
);
```

**team_capabilities schema:**
```sql
CREATE TABLE IF NOT EXISTS team_capabilities (
    team TEXT NOT NULL,
    capability TEXT NOT NULL,
    proficiency TEXT DEFAULT 'standard',
    PRIMARY KEY (team, capability)
);
```

**team_status schema:**
```sql
CREATE TABLE IF NOT EXISTS team_status (
    team TEXT PRIMARY KEY,
    status TEXT DEFAULT 'idle',
    progress_pct REAL DEFAULT 0,
    estimated_completion REAL,
    current_task TEXT,
    work_items_total INTEGER DEFAULT 0,
    work_items_done INTEGER DEFAULT 0,
    last_updated REAL NOT NULL
);
```

### 1.4 Work Stealing DB (`storage/coordination/work_stealing.py`)

**Database path**: Passed as `db_path` to `WorkStealing.__init__`.

| Table | Purpose |
|---|---|
| `work_queue` | Shared priority queue for cross-team task stealing |
| `pipelines` | Multi-stage pipeline definitions |
| `pipeline_stages` | Individual stages with dependency tracking |
| `scratchpad` | Key-value store with TTL for inter-team data sharing |

**work_queue schema:**
```sql
CREATE TABLE IF NOT EXISTS work_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_team TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'queued',     -- queued -> claimed -> completed|failed
    claimed_by TEXT,
    claimed_at REAL,
    completed_at REAL,
    result TEXT,                       -- JSON-encoded result or error
    created_at REAL NOT NULL
);
```

**pipelines schema:**
```sql
CREATE TABLE IF NOT EXISTS pipelines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    stages TEXT NOT NULL,              -- JSON definition
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL
);
```

**pipeline_stages schema:**
```sql
CREATE TABLE IF NOT EXISTS pipeline_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER REFERENCES pipelines(id),
    stage_name TEXT NOT NULL,
    assigned_team TEXT,
    depends_on TEXT,                   -- JSON list of dependency stage names
    status TEXT DEFAULT 'waiting',
    input_data TEXT,
    output_data TEXT,
    error_message TEXT,
    started_at REAL,
    completed_at REAL
);
```

**scratchpad schema:**
```sql
CREATE TABLE IF NOT EXISTS scratchpad (
    key TEXT NOT NULL,
    namespace TEXT NOT NULL,
    value TEXT NOT NULL,
    written_by TEXT NOT NULL,
    ttl_seconds INTEGER DEFAULT 3600,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (key, namespace)
);
```

**Indexes**: `idx_work_queue_status_priority(status, priority, created_at)`, `idx_pipeline_stages_pipeline_status(pipeline_id, status)`.

---

## 2. JSONL Bus

### Message Format

Every message is a single JSON line appended to a `.jsonl` file:

```json
{
  "id": "<uuid4>",
  "type": "<msg_type>",
  "channel": "<channel_name>",
  "team": "<team_name>",
  "agent_id": "<agent_id>",
  "ts": <unix_timestamp>,
  "ttl": 3600,
  "body": { ... }
}
```

**Valid message types** (`bus_cli.py`): `info`, `blocker`, `phase-signal`, `heartbeat`, `request`, `response`.

### Storage

- **Directory**: `storage/coordination/bus/`
- **File naming**: `<safe_channel>.jsonl` where channel name is sanitized via `re.sub(r'[^a-zA-Z0-9_-]', '_', channel)`
- **Common channels**: `global.jsonl` (system-wide announcements), `research3.jsonl` (team-specific), direct channels like `teamA__teamB.jsonl`

### Who Writes

- **bus_cli.py** `write_msg()`: CLI entry point for any agent to publish
- **direct_channels.py** `_bus_publish()`: Called internally when creating channels, joining channels, sending direct messages, updating presence, broadcasting findings
- **work_stealing.py**: Notifies on `global` channel for events: `work-enqueued`, `work-stolen`, `work-completed`, `work-failed`, `pipeline-created`, `pipeline-stage-started`, `pipeline-stage-completed`

### Who Reads

- **bus_cli.py** `read_msgs()`: CLI entry point, reads from offset, filters by TTL expiry
- **direct_channels.py** `_bus_read()`: Internal reader for polling channels; returns `(msgs, new_offset)` and persists offset to SQLite `read_offsets` table
- **think_tank.py**: Does NOT read the bus directly; reads from `team_findings` SQLite table and team output JSON files

### TTL Expiry

Messages have a `ttl` field (default 300s in `_bus_read`, 3600s in `bus_cli.py`). Expired messages (`ts + ttl < now`) are filtered out at read time but remain in the file.

---

## 3. File-Based Channels

### Path Patterns

- **Bus files**: `storage/coordination/bus/<channel>.jsonl`
- **Direct channel naming**: `_direct_channel_name(team_a, team_b)` sorts teams alphabetically and joins them, producing deterministic names like `teamA__teamB`
- **Topic channels**: Named arbitrarily, registered in the `channels` SQLite table with `channel_type='topic'`
- **Team output files**: `storage/coordination/teams/*/output/*.json` and `teams/*/output/*.json` (repo-level)

### Locking Strategy

**There is no file-level locking (no `fcntl`, `flock`, or `lockf`).** Instead, the system relies on:

1. **Atomic append via OS primitives**: `os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)` followed by `os.write(fd, raw)`. On Linux, `O_APPEND` guarantees atomic appends for writes under `PIPE_BUF` (4096 bytes). Each message is a single JSON line, typically well under this limit.

2. **Partial-line detection on read**: `_bus_read()` reads in binary mode (`"rb"`) line by line. If a line does not end with `\n`, it is treated as an incomplete write and the offset is NOT advanced past it. This handles the race where a reader catches a writer mid-line.

3. **Byte-offset tracking**: Each team tracks its read position per channel as a byte offset. Offsets are persisted in the `read_offsets` SQLite table and only written when they change (optimization INEFF-PERF-013).

---

## 4. Work Queues

### Storage Mechanism

Work queues are stored in SQLite via the `work_queue` table. Priority is an integer (lower = higher priority, default 5). Items flow through states: `queued` -> `claimed` -> `completed` | `failed`.

### Atomicity Approach

**`BEGIN IMMEDIATE` transactions** prevent double-steal races:

1. `steal_work()` acquires an immediate write lock via `BEGIN IMMEDIATE`
2. Selects the highest-priority unclaimed item (`status='queued'`, `ORDER BY priority ASC, created_at ASC`, `LIMIT 1`)
3. Atomically updates it to `status='claimed'` with `claimed_by` and `claimed_at`
4. Commits the transaction
5. On any failure: `ROLLBACK`

If `BEGIN IMMEDIATE` fails with `SQLITE_BUSY` (another writer holds the lock), the `_retry_on_busy` decorator retries up to a configured number of times with exponential backoff.

### Bus Notifications

Every queue state change publishes a notification to the `global` JSONL bus channel:
- `work-enqueued` (with `work_id` and `title`)
- `work-stolen` (with `work_id`, `claimed_by`)
- `work-completed` / `work-failed` (with `work_id`, `result`)

---

## 5. Think Tank Aggregation

The think tank (`think_tank.py`) is a **convergence script** that does NOT participate in real-time bus communication. It performs batch aggregation:

### Data Sources

1. **SQLite `team_findings` table** in `storage/coordination/db/state.db` - read via `SELECT * FROM team_findings ORDER BY ts`
2. **JSON output files** from two glob patterns:
   - `storage/coordination/teams/*/output/*.json`
   - `teams/*/output/*.json` (repo root level)

### Processing Pipeline

1. **Collect**: Gather findings from both SQLite and file sources
2. **Normalize**: Each finding gets `title`, `category`, `priority`, `team`, `ts`, `content`, `source` fields
3. **Deduplicate**: SHA-256 fingerprint of `(title_lower + category)` - higher priority or more recent entry wins ties
4. **Group**: By category, sorted by priority within each group
5. **Generate roadmap**: Prioritized improvement items referencing supporting findings

### Output

Written to `storage/coordination/teams/think-tank-report.json` (default) as a JSON report with:
- `total_findings`, `total_categories`, `priority_breakdown`
- `categories` dict with per-category stats and findings
- `roadmap` list of prioritized improvement items
- `summary` string

---

## 6. Data Flow Summary Diagram

```
                       +-----------------------+
                       |   JSONL Bus (files)   |
                       |  bus/<channel>.jsonl   |
                       +-----------+-----------+
                          ^    ^    |
          write (O_APPEND)|    |    | read (byte offset)
                          |    |    v
  +---------------+   +---+----+---+---+   +------------------+
  | bus_cli.py    |   | direct_channels |   | work_stealing.py |
  | (CLI writes)  |   | (pub/read)      |   | (notifications)  |
  +---------------+   +--------+--------+   +--------+---------+
                               |                      |
                    SQLite WAL |           SQLite WAL  |
                               v                      v
                       +-------+-------+    +---------+---------+
                       | channels DB   |    | work_queue DB     |
                       | - channels    |    | - work_queue      |
                       | - presence    |    | - pipelines       |
                       | - progress    |    | - pipeline_stages |
                       | - read_offsets|    | - scratchpad      |
                       +---------------+    +-------------------+

  +------------------+         +-------------------+
  | coordinator_hub  |         | help_protocol     |
  | (hub DB)         |         | (state.db)        |
  +--------+---------+         +---------+---------+
           |                             |
  SQLite WAL                    SQLite WAL
           v                             v
  +--------+---------+         +---------+---------+
  | - agent_status   |         | - work_items      |
  | - coordinator_   |         | - help_requests   |
  |   instructions   |         | - team_capabilities|
  +------------------+         | - team_status     |
                               +-------------------+

  +------------------+
  | think_tank.py    |  <-- Batch reader (no bus participation)
  | reads:           |      Sources: state.db/team_findings + teams/*/output/*.json
  | writes:          |      Output:  teams/think-tank-report.json
  +------------------+
```
