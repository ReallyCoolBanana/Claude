# Deep Stress Test Report -- TEAM-0019 Subagent E

**Date:** 2026-03-11
**Target:** Prototype A (`agent_comm/` -- JSONL + SQLite WAL)
**Test file:** `prototype/tests/test_stress_deep.py`
**Runner:** `python -m pytest prototype/tests/test_stress_deep.py -v --tb=short`

## Results Summary

| # | Test Category | Tests | Result |
|---|---|---|---|
| 1 | Rapid coordinator restart | 3 | ALL PASS |
| 2 | Message flood (5000 msgs / 5 writers) | 2 | ALL PASS |
| 3 | Interleaved reader/writer | 2 | ALL PASS |
| 4 | Rate limit under extreme contention (20 threads / 10 slots) | 2 | ALL PASS |
| 5 | Heartbeat accuracy under load | 1 | ALL PASS |
| 6 | Large message edge case (4095/4096/4097 bytes) | 4 | ALL PASS |
| 7 | Connection churn (100+ rapid open/close cycles) | 3 | ALL PASS |
| 8 | Cleanup under active writes | 2 | ALL PASS |
| 9 | Multi-channel stress (20 ch, 10 writers, 10 readers, 3s) | 1 | ALL PASS |
| 10 | Phase transition during active work | 3 | ALL PASS |

**Total: 23 tests, 23 passed, 0 failed** (14.20s runtime)

## New Bugs Found

**None.** All 10 stress scenarios passed without finding new bugs.

## Analysis

Prototype A held up well under all adversarial conditions tested:

1. **Coordinator restart** -- State survives across rapid start/stop cycles including 5 consecutive restarts. The CR-3 stateless restart fix is solid.

2. **Message flood** -- 5000 concurrent messages from 5 writers yielded zero lost or corrupted messages. POSIX atomic append on JSONL files works correctly under contention.

3. **Interleaved reader/writer** -- Readers never observed partial or corrupt JSON, even with 3 concurrent readers and 1 writer. The incomplete-line detection in `BusReader.poll()` is robust.

4. **Rate limiting** -- `BEGIN IMMEDIATE` transaction isolation (RC-4 fix) correctly serializes 20 threads competing for 10 slots. Exactly 10 succeed, 10 fail, no over-admission or under-admission.

5. **Heartbeat under load** -- Agent heartbeats remained within the dead-agent timeout even with 5 I/O-heavy threads generating continuous SQLite writes. The `busy_timeout` + retry-on-busy decorator keeps heartbeats flowing.

6. **Boundary messages** -- 4095 and 4096 byte messages are accepted and readable; 4097 bytes is correctly rejected. The POSIX atomic write boundary at 4096 bytes is properly enforced.

7. **Connection churn** -- 100 sequential and 200 concurrent (10x20) open/close cycles produced no WAL/SHM corruption. `PRAGMA integrity_check` passes after all churn.

8. **Cleanup during writes** -- `cleanup_expired()` running concurrently with writers caused no errors. Active (non-expired) messages were never incorrectly deleted.

9. **Multi-channel stress** -- 20 channels, 10 writers, and 10 readers running for 3 seconds produced zero corrupt messages across all channels.

10. **Phase transitions** -- Rapid phase advances while 5 workers are actively writing and heartbeating caused no crashes. All phase signals were recorded correctly.

## Risk Areas (not bugs, but worth monitoring)

- **SQLite busy retries**: Under extreme contention (test 4, 8), the retry-on-busy decorator with 5 retries and exponential backoff was sufficient. However, with significantly more agents (50+), the 5-retry limit could become a bottleneck. Consider making `_MAX_RETRIES` configurable.

- **BusReader offset tracking**: Each `BusReader` instance independently tracks its byte offset. If a reader is recreated (e.g., after agent restart), it starts from offset 0 and re-reads the entire file. For long-running channels with large files, this could cause latency spikes. The `EpochRotator` mitigates this, but only at hourly granularity.

- **SharedState single connection**: `SharedState` uses a single `sqlite3.Connection` protected by a `threading.Lock`. This serializes all operations within a process. Under very high thread counts, this could become a throughput bottleneck.
