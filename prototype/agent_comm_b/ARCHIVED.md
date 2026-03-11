# Prototype B — ARCHIVED

**Status:** Archived (2026-03-11)
**Reason:** Proto A (JSONL + SQLite WAL) selected for production use.

## Summary

Proto B (Named Pipes + mmap) served as a speed-optimized reference implementation.
It is preserved here for record purposes and potential future reference.

### Key Stats
- 1,855 lines of code across bus.py, state.py, coordinator.py, core.py
- 62/62 tests passed (after 7 bug fixes)
- Benchmark overhead: 0.1-0.4% (slightly lower than Proto A)
- Weaker rate-limit enforcement than Proto A

### Why Proto A Was Chosen
1. Superior robustness (0 bugs in deep stress testing vs 7 for Proto B)
2. Active SQLite-based rate limiting prevents API abuse
3. JSONL provides durable message history (vs ephemeral FIFOs)
4. Simpler debugging — human-readable append-only log files

### Reference
- KB-0018: Implementation & test results
- KB-0020: Live benchmark comparison
- TEAM-0020: Final evaluation session
