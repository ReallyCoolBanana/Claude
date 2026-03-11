# Prototype B Bug Report

**Author:** Subagent D, TEAM-0019
**Date:** 2026-03-11
**Test file:** `prototype/tests/test_proto_b.py`
**Results:** 55 passed, 7 failed out of 62 tests

---

## Summary

Prototype B has **7 confirmed bugs**, including 3 that completely prevent the Coordinator and Worker from starting. The core low-level components (CommDir, Message, PipeBusWriter/Reader, SharedStateMap) work correctly in isolation, but the coordinator layer (coordinator.py) has multiple API mismatches that make the system non-functional as an integrated whole.

Prototype A passed all 28 tests. Prototype B fails on integration-level bugs that Prototype A does not have.

---

## Bug List

### PB-001: Coordinator calls `self._state.register()` — method does not exist

- **Severity:** CRITICAL (blocks all coordinator functionality)
- **Test:** `TestCoordinatorAPIBugs::test_coordinator_register_method`
- **Location:** `coordinator.py` line 172
- **Description:** `Coordinator.start()` calls `self._state.register(...)` but `SharedStateMap` only has `register_agent()`. The coordinator cannot start at all.
- **Root cause:** API name mismatch between coordinator.py and state.py. Likely a rename of `register()` to `register_agent()` in state.py that was not propagated to coordinator.py.
- **Suggested fix:** Change line 172 from `self._state.register(...)` to `self._state.register_agent(...)`.

### PB-002: Worker calls `self._state.register()` — method does not exist

- **Severity:** CRITICAL (blocks all worker functionality)
- **Test:** `TestWorkerAPIBugs::test_worker_register_method`
- **Location:** `coordinator.py` line 572
- **Description:** `Worker.start()` calls `self._state.register(...)` but `SharedStateMap` only has `register_agent()`. No worker can start.
- **Root cause:** Same as PB-001. The method was renamed in state.py but the callers in coordinator.py were not updated.
- **Suggested fix:** Change line 572 from `self._state.register(...)` to `self._state.register_agent(...)`.

### PB-003: Coordinator.check_agents() treats AgentSlotView as dict

- **Severity:** HIGH (dead agent detection crashes)
- **Test:** `TestCoordinatorAPIBugs::test_coordinator_check_agents_return_type`
- **Location:** `coordinator.py` lines 291-294
- **Description:** `check_agents()` does `agent["agent_id"]` and `agent["last_heartbeat"]` but `SharedStateMap.get_dead_agents()` returns a list of `AgentSlotView` dataclass instances, not dicts. This raises `TypeError: 'AgentSlotView' object is not subscriptable`.
- **Root cause:** coordinator.py was written expecting dict returns (perhaps an earlier version of state.py returned dicts), but state.py returns frozen dataclass instances.
- **Suggested fix:** Change `agent["agent_id"]` to `agent.agent_id` and `agent["last_heartbeat"]` to `agent.last_heartbeat` on lines 293-294.

### PB-004: SharedStateMap missing `get_heartbeat()` method — CoordinatorWatchdog broken

- **Severity:** CRITICAL (coordinator death detection non-functional)
- **Tests:** `TestCR3_CoordinatorDeathDetection::test_watchdog_get_heartbeat_method_exists`, `test_watchdog_is_coordinator_alive`
- **Location:** `coordinator.py` line 450, `state.py` (missing method)
- **Description:** `CoordinatorWatchdog.is_coordinator_alive()` calls `self._state.get_heartbeat(agent_id)` but `SharedStateMap` has no `get_heartbeat()` method. The method silently fails (caught by the broad `except Exception`) and always returns `False`, meaning the watchdog always thinks the coordinator is dead.
- **Root cause:** `state.py` was not written with a `get_heartbeat()` API. The watchdog was designed against a different interface (possibly matching Prototype A's SQLite API).
- **Suggested fix:** Add a `get_heartbeat()` method to `SharedStateMap`:
  ```python
  def get_heartbeat(self, agent_id: str) -> float | None:
      with self._flock():
          slot = self._find_slot_by_agent_id(agent_id)
          if slot is None:
              return None
          sv = self._read_slot(slot)
          return sv.last_heartbeat
  ```

### PB-005: `_init_header()` uses no-op lock (`self._lock()` instead of `self._flock()`)

- **Severity:** MEDIUM (potential mmap corruption during concurrent initialization)
- **Test:** `TestMmapIntegrity::test_init_header_lock_bug`
- **Location:** `state.py` line 165
- **Description:** `_init_header()` calls `self._lock()` which invokes the nested `_lock` class with `fd=None`. When `fd=None`, the `__enter__` and `__exit__` methods are no-ops — no actual file locking occurs. All other methods correctly use `self._flock()` which passes `self._fd`.
- **Root cause:** Name collision between the `_lock` nested class and what looks like an intended method call. `self._lock()` constructs a `_lock(fd=None)` instance rather than acquiring a lock on the backing file.
- **Suggested fix:** Change `self._lock()` to `self._flock()` on line 165.

### PB-006: CommDir `_check_canonical_match()` is tautological — never detects symlinks

- **Severity:** MEDIUM (SB-1 split-brain protection is ineffective)
- **Test:** `TestSB1_PathCanonicalization::test_canonical_mismatch_raises`
- **Location:** `core.py` lines 128-135
- **Description:** `__init__` sets `self._path = os.path.realpath(self._requested)`. Then `_check_canonical_match()` compares `os.path.realpath(self._requested) != self._path`, which is equivalent to `realpath(x) != realpath(x)` — always False. The check can never raise. Symlinked paths are silently accepted without warning.
- **Root cause:** The check re-canonicalizes `_requested` instead of comparing the raw requested path against the canonical path.
- **Suggested fix:** Change the comparison to `self._requested != self._path` so that symlinked inputs are actually detected and rejected.

### PB-007: `fcntl.flock()` is per-fd per-process — ineffective for multi-threaded access

- **Severity:** MEDIUM (rate limiter may over-grant under multi-threaded access)
- **Test:** `TestMmapConcurrentWrites::test_flock_ineffective_for_threads` (PASSED in this run but architecturally broken)
- **Test:** `TestRC4_RateLimitTOCTOU::test_concurrent_rate_limit_reservation` (PASSED in this run)
- **Location:** `state.py` lines 199-220
- **Description:** `fcntl.flock()` operates at the (fd, process) granularity. Multiple threads within the same process sharing the same file descriptor are NOT serialized by flock. In the current codebase, Coordinator and Worker use threads (heartbeat_loop, event_loop) that access the same `SharedStateMap` instance. Under contention, this can allow concurrent reads/writes to the mmap without serialization.
- **Root cause:** flock is designed for inter-process locking, not intra-process thread synchronization. A threading.Lock should be used in addition to (or instead of) flock for thread safety.
- **Note:** This test passed in the current run because Python's GIL provides some accidental serialization, and the thread barrier timing may not have triggered the race. The bug is architectural and will manifest under sustained load or with free-threaded Python (3.13+).
- **Suggested fix:** Add a `threading.Lock` alongside the flock. Acquire the threading lock first, then the flock, to provide both thread-safety and process-safety:
  ```python
  def __init__(self, shm_dir):
      ...
      self._thread_lock = threading.Lock()

  def _flock(self):
      # Return a combined lock context manager
      ...
  ```

---

## Comparison to Prototype A

| Aspect | Prototype A | Prototype B |
|--------|-------------|-------------|
| **Tests passed** | 28/28 (100%) | 55/62 (89%) |
| **Can coordinator start?** | Yes | **No** (PB-001) |
| **Can workers start?** | Yes | **No** (PB-002) |
| **Dead agent detection** | Works | **Crashes** (PB-003) |
| **Coordinator death detection** | Works | **Broken** (PB-004) |
| **Header initialization locking** | N/A (SQLite handles it) | **No-op lock** (PB-005) |
| **Symlink split-brain protection** | Works | **Tautological check** (PB-006) |
| **Thread-safe locking** | SQLite handles it | **flock is per-process only** (PB-007) |
| **Rate limit TOCTOU (RC-4)** | Fixed with BEGIN IMMEDIATE | Uses flock (architecturally weaker) |

### Assessment

Prototype B's **low-level components are solid** — the FIFO bus, mmap state map, and message serialization all work correctly in unit tests. The **integration layer (coordinator.py) is non-functional** due to API mismatches (PB-001 through PB-004) that appear to be incomplete refactoring: state.py was updated with new method names and return types, but coordinator.py was not updated to match.

Bugs PB-001 through PB-004 are trivial to fix (method name changes, attribute access instead of dict subscript, add one missing method). Bug PB-005 is a one-line fix. Bug PB-006 requires a logic change in the comparison. Bug PB-007 requires adding a threading.Lock for proper thread safety.

After fixing PB-001 through PB-006 (estimated effort: ~30 minutes), the prototype would be functional. PB-007 (thread safety) requires more design thought but is mitigated in practice by the GIL for CPython < 3.13.

**Recommendation:** Fix PB-001 through PB-006 immediately, then re-run the full test suite to verify. Address PB-007 as a follow-up before any concurrent stress testing.
