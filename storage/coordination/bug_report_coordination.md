# Bug Report: Coordination System

**Date**: 2026-03-11
**Reviewer**: Think Tank 1 - Bug Hunter
**Scope**: All 7 core coordination files

---

## coordinator_hub.py

### BUG-HUB-001: `send_instruction` race condition with `verify_exists`
- **Lines**: 573-589
- **Severity**: Medium
- **Description**: The `verify_exists` check and the subsequent INSERT are not wrapped in a single transaction (`BEGIN IMMEDIATE`). Between the SELECT check and the INSERT, the agent could be removed by another process, leading to an instruction sent to a nonexistent agent. Additionally, unlike `broadcast_instruction` (which uses `BEGIN IMMEDIATE`), `send_instruction` relies on autocommit, which provides weaker isolation.
- **Suggested fix**: Wrap the entire `verify_exists` check + INSERT in a `BEGIN IMMEDIATE` transaction, consistent with `broadcast_instruction`.

### BUG-HUB-002: `_register` calls `_bus_notify` outside the lock, but decorator retry re-enters the lock
- **Lines**: 213-243
- **Severity**: Low
- **Description**: The `_retry_on_busy` decorator wraps `_register`, which acquires `self._lock` internally. If `_register` fails with SQLITE_BUSY inside the lock region, the retry decorator will call `_register` again, which will attempt to re-acquire `self._lock`. Since `threading.Lock` is not reentrant, this will deadlock on the same thread. This applies to ALL methods decorated with `_retry_on_busy` that acquire `self._lock` internally.
- **Note**: This actually works because the `_retry_on_busy` decorator catches the exception AFTER the `with self._lock:` block has exited (the exception propagates out of the `with` block, releasing the lock). However, if `self._conn.commit()` raises SQLITE_BUSY (which is unlikely with WAL + busy_timeout but possible), the lock will already be released since the exception exits the `with` block. So this is safe but fragile -- if someone restructured the code to hold the lock longer, it would deadlock.
- **Suggested fix**: Document this design constraint. Consider using `threading.RLock` for safety margin.

### BUG-HUB-003: `update_status` uses INSERT OR REPLACE pattern that resets `started_at`
- **Lines**: 281-303
- **Severity**: Low
- **Description**: The `update_status` method uses INSERT ... ON CONFLICT ... DO UPDATE and sets `started_at = excluded.started_at` (which is `now`). This means every status update could overwrite the original `started_at` time if the INSERT path fires. While the ON CONFLICT path updates `last_updated` correctly, the INSERT path always sets `started_at = now`, which would be wrong if the agent was already registered. The `_register` method handles this correctly by using `started_at = excluded.started_at` in the upsert, but the risk exists if `update_status` fires before `_register` has run for some reason.
- **Suggested fix**: In the ON CONFLICT clause, do NOT update `started_at`: remove `started_at` from the conflict update, or use `started_at = agent_status.started_at` to preserve the original.

### BUG-HUB-004: `check_instructions` manual transaction inside autocommit connection
- **Lines**: 381-407
- **Severity**: Medium
- **Description**: `check_instructions` issues `BEGIN IMMEDIATE` on a connection that may be in autocommit mode. If any prior operation left an uncommitted transaction (e.g., due to a bug or partial failure), this `BEGIN IMMEDIATE` will fail with "cannot start a transaction within a transaction." The error handling does `ROLLBACK` in the except block, which is correct, but the connection state could be inconsistent if this happens.
- **Suggested fix**: Consider using `conn.in_transaction` check before issuing `BEGIN IMMEDIATE`, or use the connection as a context manager.

---

## help_protocol.py

### BUG-HELP-001: `fulfill_help` not wrapped in a transaction
- **Lines**: 541-581
- **Severity**: High
- **Description**: `fulfill_help` performs a SELECT, then two UPDATEs (one on `help_requests`, one on `work_items`), but does not use `BEGIN IMMEDIATE`. If the process crashes between the two UPDATEs, the help request could be marked 'fulfilled' but the work item left un-completed, or vice versa. This breaks data consistency.
- **Suggested fix**: Wrap the entire SELECT + UPDATE + UPDATE sequence in a `BEGIN IMMEDIATE` ... `COMMIT` transaction with proper rollback on failure.

### BUG-HELP-002: `request_help` does not verify work item belongs to the requesting team
- **Lines**: 398-445
- **Severity**: Medium
- **Description**: `request_help` checks that the work item exists (`WHERE id = ?`) but does not verify that it belongs to `self.team`. Any team can create a help request for any other team's work item. This could lead to confusion or security issues in a multi-tenant scenario.
- **Suggested fix**: Add `AND team = ?` to the SELECT query on line 411 with `(work_item_id, self.team)`.

### BUG-HELP-003: `request_help` not wrapped in an explicit transaction
- **Lines**: 407-433
- **Severity**: Medium
- **Description**: `request_help` performs a SELECT on `work_items`, then an UPDATE on `work_items`, then an INSERT into `help_requests`, all without `BEGIN IMMEDIATE`. If another process concurrently modifies the work item between the SELECT and the UPDATE, the state could become inconsistent. Also, if the INSERT succeeds but the commit is delayed, another process could see a partially-applied state.
- **Suggested fix**: Wrap in `BEGIN IMMEDIATE` ... `COMMIT` with rollback.

### BUG-HELP-004: `auto_assign_idle_teams` stale data from non-atomic reads
- **Lines**: 734-898
- **Severity**: Medium
- **Description**: `auto_assign_idle_teams` calls `get_open_help_requests()` and `get_idle_teams()` as separate transactions. Between these calls, the world can change. While the individual assignments use `BEGIN IMMEDIATE`, the overall logic could assign a team that is no longer idle (its status changed after `get_idle_teams()` returned). The atomic claim inside the loop only checks `help_requests.status`, not `team_status.status`.
- **Suggested fix**: Inside the `BEGIN IMMEDIATE` block (line 808), add a check that the helper team's status is still idle/complete before assigning.

### BUG-HELP-005: `register_capabilities` not atomic
- **Lines**: 224-245
- **Severity**: Low
- **Description**: Multiple INSERT statements are executed inside a single lock acquisition but without `BEGIN IMMEDIATE`. If the process crashes partway through the loop, some capabilities will be registered and others won't. This is a minor consistency issue.
- **Suggested fix**: Use `BEGIN IMMEDIATE` before the loop and `COMMIT` after.

### BUG-HELP-006: `complete_work_item` allows completing items not owned by current team if assigned to it
- **Lines**: 373-392
- **Severity**: Low
- **Description**: `complete_work_item` checks `WHERE id = ? AND team = ?` which uses the creating team, not the assigned team. If a helper team completes a work item assigned to them by `auto_assign_idle_teams`, this check will fail because `team` is the original owner, not the helper. The helper would get a ValueError.
- **Suggested fix**: Also accept completion when `assigned_to` matches the calling agent/team.

---

## direct_channels.py

### BUG-DC-001: `read_direct` calls `_bus_read` inside `self._lock`, blocking writers
- **Lines**: 453-460
- **Severity**: Medium
- **Description**: `read_direct` acquires `self._lock` and then calls `_bus_read`, which does file I/O (opening and reading a JSONL file). This holds the lock for the entire duration of the file read, blocking all other operations on this `DirectChannels` instance (including SQLite writes, presence updates, etc.). For large bus files, this could cause significant contention.
- **Suggested fix**: Read the offset under the lock, release the lock, perform the file I/O, then re-acquire the lock to update the offset (with a CAS check to handle races).

### BUG-DC-002: `__del__` relies on garbage collection for cleanup
- **Lines**: 260-265
- **Severity**: Low
- **Description**: The `__del__` method calls `self.close()`, but `__del__` is not guaranteed to be called (e.g., in reference cycles, or during interpreter shutdown). If `close()` is never called, the SQLite connection leaks. The class does implement `__enter__`/`__exit__` but only via the `close()` method -- there's no context manager protocol defined (missing `__enter__` and `__exit__`).
- **Suggested fix**: Add `__enter__` and `__exit__` methods for proper context manager support, matching the pattern in `coordinator_hub.py`.

### BUG-DC-003: `join_channel` and `leave_channel` not atomic (TOCTOU)
- **Lines**: 340-385
- **Severity**: Medium
- **Description**: `join_channel` does a SELECT to get participants, modifies the list in Python, then does an UPDATE. Between the SELECT and UPDATE, another process could modify the participant list, and the UPDATE would overwrite their changes. Similarly for `leave_channel`. Neither uses `BEGIN IMMEDIATE`.
- **Suggested fix**: Use `BEGIN IMMEDIATE` to ensure the SELECT + UPDATE is atomic, or use a JSON SQL function if available.

### BUG-DC-004: `_save_read_offset` race with `_persisted_offsets` check
- **Lines**: 682-702
- **Severity**: Low
- **Description**: `_save_read_offset` checks `self._persisted_offsets` outside the lock (line 690), then acquires the lock to write. Between the check and the write, another thread could update `_persisted_offsets`. This is unlikely in practice (typically one reader per team) but is a design smell.
- **Suggested fix**: Move the `_persisted_offsets` check inside the lock.

### BUG-DC-005: Missing `__enter__`/`__exit__` context manager methods
- **Lines**: N/A (missing)
- **Severity**: Low
- **Description**: Unlike `AgentReporter`, `CoordinatorDashboard`, and `HelpProtocol`, `DirectChannels` does not implement `__enter__`/`__exit__`. This means it cannot be used with the `with` statement for guaranteed cleanup.
- **Suggested fix**: Add `__enter__` returning `self` and `__exit__` calling `self.close()`.

---

## work_stealing.py

### BUG-WS-001: `complete_work` check-then-act not atomic
- **Lines**: 294-318
- **Severity**: High
- **Description**: `complete_work` does a SELECT to verify authorization, then an UPDATE to mark completion, but these are not wrapped in a transaction. Between the SELECT and UPDATE, another process could steal or modify the work item. The authorization check could pass on stale data, or the UPDATE could overwrite a concurrent status change.
- **Suggested fix**: Wrap in `BEGIN IMMEDIATE` ... `COMMIT`, or add a WHERE clause to the UPDATE that re-checks the authorization condition (optimistic locking).

### BUG-WS-002: `fail_work` has no authorization check
- **Lines**: 325-334
- **Severity**: Medium
- **Description**: `fail_work` allows any team to mark any work item as failed without checking if the caller is the owner or claimer. In contrast, `complete_work` verifies authorization. A misbehaving or confused agent could fail work items belonging to other teams.
- **Suggested fix**: Add the same authorization check as `complete_work`: verify `claimed_by` or `owner_team` matches `self.team`.

### BUG-WS-003: `get_queue_depth` and `get_stealable_work` missing `_check_closed` call
- **Lines**: 336-361
- **Severity**: Low
- **Description**: These methods don't call `self._check_closed()` before operating, unlike other methods. If `close()` was called, these would operate on a closed connection, which could raise an unexpected `sqlite3.ProgrammingError` instead of the more informative `RuntimeError`.
- **Suggested fix**: Add `self._check_closed()` calls at the start of both methods. Actually, looking more carefully, none of the WorkStealing methods call `_check_closed()` -- but the class does have a `_closed` attribute. The field exists but the check method doesn't exist on this class.

### BUG-WS-004: `PipelineManager` missing `_check_closed` calls everywhere
- **Lines**: 369-716
- **Severity**: Low
- **Description**: `PipelineManager` has `self._closed = False` in `__init__` and sets it in `close()`, but never checks it. All methods will operate on a closed connection without warning.
- **Suggested fix**: Add a `_check_closed` method and call it in every public method.

### BUG-WS-005: `Scratchpad` missing `_check_closed` calls
- **Lines**: 724-833
- **Severity**: Low
- **Description**: Same issue as BUG-WS-004. `Scratchpad` has `_closed` but never checks it.
- **Suggested fix**: Add `_check_closed` calls.

### BUG-WS-006: `complete_stage` does not verify current stage status
- **Lines**: 557-574
- **Severity**: Medium
- **Description**: `complete_stage` blindly marks a stage as completed without verifying it's currently `in_progress`. A stage that is `waiting`, `ready`, or already `failed` could be marked completed, breaking pipeline invariants. Contrast with `start_stage` which properly checks for `ready` status.
- **Suggested fix**: Add a status check: only allow completing a stage that is `in_progress`.

### BUG-WS-007: `get_ready_stages` returns stages with status="waiting" that were just updated
- **Lines**: 596-632
- **Severity**: Low
- **Description**: `get_ready_stages` updates waiting stages to 'ready' in the database but returns the `dict(stage)` where `stage["status"]` is still "waiting" (the dict was built before the UPDATE). Callers would see incorrect status in the returned data.
- **Suggested fix**: Update the dict's status to "ready" after the UPDATE, e.g., `r["status"] = "ready"` for updated items.

### BUG-WS-008: No `__enter__`/`__exit__` on `WorkStealing`, `PipelineManager`, `Scratchpad`
- **Lines**: N/A (missing)
- **Severity**: Low
- **Description**: None of the three classes in this file implement context manager protocol. This is inconsistent with `coordinator_hub.py` and `help_protocol.py` which do support `with` statements.
- **Suggested fix**: Add `__enter__`/`__exit__` to all three classes.

---

## multi_team_runner.py

### BUG-MTR-001: `_init_db` schema does not run under the shared connection lock properly
- **Lines**: 261-267
- **Severity**: Medium
- **Description**: `_init_db` calls `_get_connection()` which acquires `_shared_conn_lock` to create the connection, then releases it. Then `_init_db` acquires `_shared_conn_lock` again to run `executescript`. Between these two lock acquisitions, another thread could use the connection, potentially interfering with the schema creation.
- **Suggested fix**: Perform schema initialization inside `_get_connection()` itself (after creating the connection, before returning), so it happens atomically under the lock. Or ensure `_init_db` is always called before any other thread starts.

### BUG-MTR-002: `_bus_write` channel sanitization is weaker than other files
- **Lines**: 193
- **Severity**: Medium (Security)
- **Description**: `_bus_write` sanitizes the channel name with `channel.replace("/", "_").replace("..", "_")`, while all other files use `re.sub(r'[^a-zA-Z0-9_-]', '_', channel)`. The `multi_team_runner.py` version allows special characters like spaces, semicolons, colons, etc. in filenames, which could cause issues on certain filesystems or enable path injection on systems where these characters are special.
- **Suggested fix**: Use the same `re.sub(r'[^a-zA-Z0-9_-]', '_', channel)` pattern as the other files.

### BUG-MTR-003: `_signal_handler` calls `sys.exit(0)` which may not clean up properly
- **Lines**: 639-645
- **Severity**: Medium
- **Description**: `_signal_handler` calls `runner.stop()` then `sys.exit(0)`. However, if the signal fires while the main thread is in `runner._stop_event.wait(1.0)` (line 780), the KeyboardInterrupt path will also try to call `runner.stop()`. This means `stop()` could be called twice concurrently (from the signal handler thread and the main thread). `stop()` sets `self._stop_event`, joins threads, writes to bus and DB -- none of which are guarded against double invocation.
- **Suggested fix**: Add a guard in `stop()` (e.g., `if self._phase == "shutdown": return`) to prevent double execution. Also, `_signal_handler` should not call `sys.exit()` -- it should just set the stop event and let the main loop exit naturally.

### BUG-MTR-004: `_get_runner_state` silently swallows `OperationalError`
- **Lines**: 325-335
- **Severity**: Low
- **Description**: `_get_runner_state` catches `sqlite3.OperationalError` and returns `None`, making it impossible to distinguish between "key not found" and "database error." If the table doesn't exist or the DB is corrupt, this silently returns None.
- **Suggested fix**: Only catch the specific "no such table" case, or log the error.

### BUG-MTR-005: `_check_agent_health` does not mark dead agents' status
- **Lines**: 525-540
- **Severity**: Medium
- **Description**: `_check_agent_health` detects dead agents and logs warnings to the bus, but never updates the agent's `status` column in the database from 'alive' to 'dead'. This means `_get_dead_agents` will keep returning the same dead agents on every check cycle, generating repeated bus warnings without ever resolving the situation.
- **Suggested fix**: Update the agent's status to 'dead' in the database when detected, so subsequent checks don't re-report them.

### BUG-MTR-006: Module-level `_logging_configured` flag is not thread-safe
- **Lines**: 67-83
- **Severity**: Low
- **Description**: `_setup_logging` uses a plain bool `_logging_configured` without a lock. If two threads call `_setup_logging` simultaneously, both could see `False` and configure logging twice, potentially duplicating handlers.
- **Suggested fix**: Use a `threading.Lock` to guard the flag, or use `logging.basicConfig`'s built-in idempotency (it only configures the root logger if it has no handlers).

### BUG-MTR-007: Global `_shared_conn` is module-level state that persists across tests
- **Lines**: 211-212
- **Severity**: Low
- **Description**: The global `_shared_conn` and `_shared_conn_lock` are module-level. If this module is imported in tests, the connection persists across test cases, potentially causing test pollution. There's no reset mechanism other than calling `_close_connection()`.
- **Suggested fix**: Provide a `_reset_for_testing()` function, or make the connection instance-level on `MultiTeamRunner`.

---

## bus_cli.py

### BUG-CLI-001: `read_msgs` reads entire remaining file into memory
- **Lines**: 60-81
- **Severity**: Medium
- **Description**: `read_msgs` uses `f.read()` after seeking to the offset, which reads the entire remaining file into memory. For a long-running system with large bus files, this could consume significant memory. Additionally, it reads in text mode (`"r"`) and calculates the new offset using `len(data.encode("utf-8"))`, which means the offset tracking is correct byte-wise, but the seek on line 67 uses a text-mode file position. If there are multi-byte UTF-8 characters, `f.seek(since_offset)` using a byte offset on a text-mode file will be incorrect.
- **Suggested fix**: Open in binary mode (`"rb"`) like `direct_channels.py`'s `_bus_read` does, and use line-by-line reading to avoid memory issues.

### BUG-CLI-002: `read_msgs` can return partial lines as valid messages
- **Lines**: 60-81
- **Severity**: Medium
- **Description**: Unlike `direct_channels.py`'s `_bus_read` which carefully handles partial (incomplete) lines at EOF, `bus_cli.py`'s `read_msgs` does `data.strip().split("\n")` which will happily attempt to parse a partial last line. If a concurrent writer is in the middle of appending a message, `read_msgs` could try to parse a truncated JSON line, fail with `json.JSONDecodeError`, skip it, and advance the offset past it. The next read would miss this message entirely.
- **Suggested fix**: Use the same partial-line detection as `direct_channels.py`: open in binary mode, check if the last line ends with `\n`, and don't advance offset past incomplete lines.

### BUG-CLI-003: `init_db` and `register_agent` open new connections without closing on error
- **Lines**: 84-175
- **Severity**: Low
- **Description**: `init_db` and `register_agent` create SQLite connections but don't use try/finally or context managers for cleanup. If an exception occurs after `sqlite3.connect` but before `conn.close()`, the connection leaks.
- **Suggested fix**: Use `with sqlite3.connect(...) as conn:` or try/finally.

### BUG-CLI-004: `post_finding` does not enable WAL mode
- **Lines**: 178-189
- **Severity**: Low
- **Description**: `post_finding` sets `busy_timeout` but does not set `journal_mode=WAL`, unlike all other database functions. This means it could conflict with other connections that expect WAL mode. While WAL mode is a database-level setting that persists, if this is the first connection to a fresh DB, it would use the default journal mode.
- **Suggested fix**: Add `conn.execute("PRAGMA journal_mode=WAL")`.

### BUG-CLI-005: `get_all_findings` does not set `busy_timeout`
- **Lines**: 192-203
- **Severity**: Low
- **Description**: `get_all_findings` connects without setting `busy_timeout`. Under contention, it would use SQLite's default timeout of 5 seconds, while other functions use 30 seconds. This inconsistency could cause spurious "database is locked" errors.
- **Suggested fix**: Add `conn.execute("PRAGMA busy_timeout=30000")`.

### BUG-CLI-006: `_get_help_protocol` modifies `sys.path` permanently
- **Lines**: 242-251
- **Severity**: Low
- **Description**: `_get_help_protocol` inserts the script directory into `sys.path[0]`. This modification persists for the lifetime of the process and could affect other imports. In a long-running process or test suite, this path manipulation could cause unexpected module resolution.
- **Suggested fix**: Use a relative import or `importlib` instead.

---

## think_tank.py

### BUG-TT-001: Module-level `logging.basicConfig` fires on import
- **Lines**: 50-53
- **Severity**: Medium
- **Description**: `logging.basicConfig()` is called at module level (line 50-53), meaning it runs whenever this module is imported. This will hijack the root logger configuration for any application that imports `think_tank`. This is particularly problematic because `multi_team_runner.py` carefully avoids this pattern (see its `_setup_logging()` function). If both modules are imported, whichever is imported first wins the logging config.
- **Suggested fix**: Move the `logging.basicConfig` call inside `main()`, matching the pattern in `multi_team_runner.py`.

### BUG-TT-002: `_collect_from_sqlite` connection not using `with` or proper error handling
- **Lines**: 110-149
- **Severity**: Low
- **Description**: While this function does have a try/finally to close the connection, it opens the connection outside the try block (line 123). If `sqlite3.connect()` raises an exception other than `OperationalError` (e.g., `PermissionError` from the OS), the `finally` block would run with `conn = None` (set on line 121), which is handled, but the error would propagate as an unexpected exception rather than being caught.
- **Suggested fix**: Minor -- the code is actually OK because `conn` is initialized to `None` before the try block. No change needed.

### BUG-TT-003: `_deduplicate` mutates input finding dicts
- **Lines**: 200-232
- **Severity**: Medium
- **Description**: `_deduplicate` adds an `also_from` key directly to the input dicts (lines 219, 225). Since Python dicts are mutable and passed by reference, this modifies the original finding dicts that were collected by `_collect_from_sqlite` and `_collect_from_files`. If a caller re-uses the `all_findings` list after calling `_deduplicate`, the dicts will have unexpected `also_from` keys. This also means running `_deduplicate` twice on the same data would produce different results (the `also_from` lists would accumulate).
- **Suggested fix**: Deep-copy findings before mutating, or create new dicts instead of modifying existing ones.

### BUG-TT-004: `_fingerprint` collision risk with short hash
- **Lines**: 77-89
- **Severity**: Low
- **Description**: The fingerprint uses only 16 hex characters (64 bits) of SHA-256. For a deduplication use case, this is probably fine (birthday paradox collision at ~2^32 items), but the truncation should be documented as an intentional trade-off.
- **Suggested fix**: Document the collision risk. For extra safety, could use 32 hex chars (128 bits).

### BUG-TT-005: `_collect_from_files` path traversal via glob patterns
- **Lines**: 152-193
- **Severity**: Low (Security)
- **Description**: `_collect_from_files` uses hardcoded glob patterns that are relative to known directories, so the glob itself is safe. However, symlinks within the scanned directories could point outside the expected tree. The `os.path.realpath` check on line 168 handles deduplication of symlink targets but doesn't prevent reading files outside the expected directory tree.
- **Suggested fix**: Consider adding a check that `os.path.realpath(filepath)` starts with an expected prefix.

---

## Cross-Cutting Issues

### BUG-CROSS-001: Inconsistent `os.write` atomicity assumptions across all files
- **Lines**: Various (all JSONL bus writers)
- **Severity**: Medium
- **Description**: All bus writers assume `os.write(fd, raw)` is atomic for the entire message. POSIX guarantees atomicity only for `write()` calls up to `PIPE_BUF` (typically 4096 bytes on Linux). Most messages are well under this limit, but `direct_channels.py` allows messages up to `MAX_MESSAGE_BYTES = 4096` (the raw JSON + newline). If a message is exactly at the limit, the encoded output could exceed `PIPE_BUF` and the write could be non-atomic on some filesystems, leading to interleaved messages.
- **Suggested fix**: Either reduce `MAX_MESSAGE_BYTES` to account for the newline and encoding overhead, or use file locking (`fcntl.flock`) for writes exceeding `PIPE_BUF`.

### BUG-CROSS-002: No file locking on JSONL bus writes
- **Lines**: Various
- **Severity**: Medium
- **Description**: All bus writers use `O_APPEND` for atomic appends, which is generally safe on local filesystems. However, on NFS or other network filesystems, `O_APPEND` atomicity is not guaranteed. If the coordination system is deployed on a shared network filesystem, messages could be corrupted.
- **Suggested fix**: Document the local-filesystem requirement, or add `fcntl.flock` for portability.

### BUG-CROSS-003: No cleanup/rotation of JSONL bus files
- **Lines**: Various
- **Severity**: Medium
- **Description**: Bus files grow without bound. While messages have TTL, expired messages are only filtered on read, never actually deleted from the file. Over time, bus files will grow large, slowing down reads (especially `bus_cli.py`'s `read_msgs` which reads the entire file). Only `multi_team_runner.py`'s cleanup loop purges expired SQLite data, but never touches bus files.
- **Suggested fix**: Implement periodic bus file compaction (rewrite file with only non-expired messages) or rotation.

### BUG-CROSS-004: Multiple database files with overlapping schemas
- **Lines**: Various
- **Severity**: Low (Design)
- **Description**: `coordinator_hub.py` uses its own DB (via `_open_db`), `help_protocol.py` uses its own connection, `direct_channels.py` uses its own, `work_stealing.py` uses its own, and `multi_team_runner.py`/`bus_cli.py` use yet another. If different callers pass different `db_path` values, the system ends up with multiple SQLite databases with different subsets of the schema, making it impossible to do cross-table queries (e.g., joining `agent_status` with `team_status`).
- **Suggested fix**: Document the expected DB topology. Consider consolidating into a single shared database with all schemas, or at least document which components must share a DB path.

### BUG-CROSS-005: `_retry_on_busy` decorator has no jitter
- **Lines**: Various (all files)
- **Severity**: Low
- **Description**: The retry decorator uses deterministic exponential backoff (0.1, 0.2, 0.4, 0.8, 1.6 seconds). If multiple agents retry simultaneously after a collision, they will all retry at the exact same times, causing repeated collisions (thundering herd). Adding random jitter would reduce collision probability.
- **Suggested fix**: Add `random.uniform(0, delay)` jitter to the sleep duration.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 0     |
| High     | 2     |
| Medium   | 15    |
| Low      | 15    |
| **Total**| **32** |

### Top Priority Fixes (High):
1. **BUG-HELP-001**: `fulfill_help` missing transaction -- data corruption risk
2. **BUG-WS-001**: `complete_work` check-then-act not atomic -- authorization bypass risk

### Most Impactful Medium Fixes:
1. **BUG-CLI-001/002**: `read_msgs` text-mode offset bug and partial-line handling -- data loss risk
2. **BUG-MTR-003**: Double `stop()` invocation -- crash during shutdown
3. **BUG-MTR-005**: Dead agents never marked in DB -- infinite warning spam
4. **BUG-MTR-002**: Weak channel sanitization -- potential security issue
5. **BUG-CROSS-001/002**: Bus write atomicity assumptions -- corruption under edge cases
