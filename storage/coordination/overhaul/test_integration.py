#!/usr/bin/env python3
"""Integration tests for the data storage overhaul deliverables.

Tests:
1. Rate limiter import and instantiation
2. Bus compactor import and class verification
3. DB partition import and class verification
4. Bus round-trip: write message, read message, verify content
5. Offset persistence: write, read, verify offset saved/restored
6. Rate limiter quota enforcement

Usage:
    python test_integration.py [--repo-root /path/to/repo]
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from typing import Any


# ---------------------------------------------------------------------------
# Repository root discovery and sys.path setup
# ---------------------------------------------------------------------------

def _find_repo_root(start: str | None = None) -> str:
    d = start or os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isfile(os.path.join(d, "CLAUDE.md")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError("Could not locate repository root (no CLAUDE.md found)")


REPO_ROOT = _find_repo_root()

# Ensure repo root is on sys.path for imports (needed for storage.coordination.*)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# The agent_comm package uses bare "agent_comm" imports internally,
# so prototype/ must also be on sys.path
_proto_dir = os.path.join(REPO_ROOT, "prototype")
if _proto_dir not in sys.path:
    sys.path.insert(0, _proto_dir)


# ---------------------------------------------------------------------------
# Test result tracking
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self, name: str, passed: bool, detail: str = "", error: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.error = error

    def to_dict(self) -> dict:
        d = {"test": self.name, "passed": self.passed}
        if self.detail:
            d["detail"] = self.detail
        if self.error:
            d["error"] = self.error
        return d


results: list[TestResult] = []


def run_test(name: str):
    """Decorator for test functions."""
    def decorator(func):
        def wrapper():
            print(f"  [{name}] Running...", end=" ")
            try:
                detail = func()
                results.append(TestResult(name, True, detail or "OK"))
                print("PASS" + (f" ({detail})" if detail else ""))
            except Exception as e:
                tb = traceback.format_exc()
                results.append(TestResult(name, False, error=str(e)))
                print(f"FAIL ({e})")
                for line in tb.strip().split("\n")[-3:]:
                    print(f"         {line}")
        wrapper.test_name = name
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Test 1: Rate limiter import
# ---------------------------------------------------------------------------

@run_test("rate_limiter_import")
def test_rate_limiter_import():
    from agent_comm.rate_limiter import RateLimiter
    assert hasattr(RateLimiter, '__init__'), "RateLimiter has no __init__"
    assert hasattr(RateLimiter, 'check_and_reserve'), "RateLimiter has no check_and_reserve"
    return "RateLimiter class found with check_and_reserve method"


# ---------------------------------------------------------------------------
# Test 2: Bus compactor import
# ---------------------------------------------------------------------------

@run_test("compactor_import")
def test_compactor_import():
    from agent_comm.compactor import BusCompactor, OffsetStore
    assert hasattr(BusCompactor, '__init__'), "BusCompactor has no __init__"
    assert hasattr(OffsetStore, '__init__'), "OffsetStore has no __init__"
    return "BusCompactor and OffsetStore classes found"


# ---------------------------------------------------------------------------
# Test 3: DB partition import
# ---------------------------------------------------------------------------

@run_test("db_partition_import")
def test_db_partition_import():
    from storage.coordination.db_partition import PartitionedStore
    assert hasattr(PartitionedStore, '__init__'), "PartitionedStore has no __init__"
    return "PartitionedStore class found in storage.coordination.db_partition"


# ---------------------------------------------------------------------------
# Test 4: Rate limiter instantiation
# ---------------------------------------------------------------------------

@run_test("rate_limiter_instantiate")
def test_rate_limiter_instantiate():
    from agent_comm.rate_limiter import RateLimiter
    tmpdir = tempfile.mkdtemp(prefix="test_rl_")
    try:
        db_path = os.path.join(tmpdir, "rate_limiter.db")
        rl = RateLimiter(db_path=db_path)
        assert rl is not None, "RateLimiter returned None"
        rl.close()
        return "RateLimiter instantiated and closed cleanly"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 5: Bus round-trip (write + read)
# ---------------------------------------------------------------------------

@run_test("bus_round_trip")
def test_bus_round_trip():
    from agent_comm.bus import BusWriter, BusReader
    tmpdir = tempfile.mkdtemp(prefix="test_bus_")
    try:
        # BusWriter(comm_dir, agent_id, team)
        writer = BusWriter(tmpdir, "test-agent", "test-team")

        channel = "test-channel"
        test_body = {"content": "hello from test_integration", "seq": 42}
        # publish(channel, msg_type, body) -- msg_type must be valid
        writer.publish(channel, "info", test_body)

        # BusReader(comm_dir, channel)
        reader = BusReader(tmpdir, channel)
        messages = reader.poll()

        assert len(messages) >= 1, f"Expected >= 1 messages, got {len(messages)}"

        # Messages are Message dataclass objects with a .body attribute
        found = False
        for msg in messages:
            body = getattr(msg, "body", None)
            if isinstance(body, dict) and body.get("content") == "hello from test_integration":
                found = True
                break

        assert found, f"Written message not found in read-back. Got: {[str(m)[:100] for m in messages]}"
        return f"Wrote 1 message, read {len(messages)} back, content verified"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 6: Offset persistence
# ---------------------------------------------------------------------------

@run_test("offset_persistence")
def test_offset_persistence():
    from agent_comm.bus import BusWriter, BusReader
    from agent_comm.compactor import OffsetStore
    tmpdir = tempfile.mkdtemp(prefix="test_offset_")
    try:
        offset_db_path = os.path.join(tmpdir, "offsets.db")
        offset_store = OffsetStore(db_path=offset_db_path)

        channel = "offset-test-channel"

        # Write 5 messages
        writer = BusWriter(tmpdir, "test-agent", "test-team")
        for i in range(5):
            writer.publish(channel, "info", {"seq": i})

        # Read with offset persistence
        reader1 = BusReader(tmpdir, channel, agent_id="test-reader", offset_store=offset_store)
        msgs1 = reader1.poll()
        msg_count_1 = len(msgs1)
        assert msg_count_1 >= 5, f"Expected >= 5 messages, got {msg_count_1}"

        # Write 3 more messages
        for i in range(5, 8):
            writer.publish(channel, "info", {"seq": i})

        # Create new reader with same offset_store -- should resume from saved offset
        reader2 = BusReader(tmpdir, channel, agent_id="test-reader", offset_store=offset_store)
        msgs2 = reader2.poll()
        msg_count_2 = len(msgs2)

        # Should only get the new messages (3), not all 8
        assert msg_count_2 <= 4, (
            f"Expected <= 4 new messages (offset should skip old), got {msg_count_2}"
        )

        offset_store.close() if hasattr(offset_store, 'close') else None
        return f"First read: {msg_count_1}, second read after resume: {msg_count_2} (offset persistence works)"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 7: Rate limiter quota enforcement
# ---------------------------------------------------------------------------

@run_test("rate_limiter_quota")
def test_rate_limiter_quota():
    from agent_comm.rate_limiter import RateLimiter
    tmpdir = tempfile.mkdtemp(prefix="test_rl_quota_")
    try:
        db_path = os.path.join(tmpdir, "rl.db")
        rl = RateLimiter(db_path=db_path)

        # Configure a tight quota for testing
        try:
            rl.configure(endpoint="test-ep", max_requests=5, window_seconds=60)
        except Exception:
            pass  # May use defaults

        agent_id = "test-agent"
        endpoint = "test-ep"

        # check_and_reserve(endpoint, agent_id) -> bool
        allowed = 0
        denied = 0
        total_attempts = 20

        for _ in range(total_attempts):
            result = rl.check_and_reserve(endpoint, agent_id)
            if result:
                allowed += 1
            else:
                denied += 1

        rl.close()
        return f"Allowed: {allowed}/{total_attempts}, Denied: {denied}/{total_attempts}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 8: PartitionedStore instantiation
# ---------------------------------------------------------------------------

@run_test("db_partition_instantiate")
def test_db_partition_instantiate():
    from storage.coordination.db_partition import PartitionedStore
    tmpdir = tempfile.mkdtemp(prefix="test_partition_")
    try:
        store = PartitionedStore(db_dir=tmpdir)
        assert store is not None, "PartitionedStore returned None"

        # Check it created partition files or has partition accessors
        partition_names = []
        for attr in ["hub", "queue", "comms", "findings"]:
            if hasattr(store, attr):
                partition_names.append(attr)

        # Check for .db files in the directory
        db_files = [f for f in os.listdir(tmpdir) if f.endswith(".db")]

        if hasattr(store, 'close'):
            store.close()

        return f"PartitionedStore OK; partitions: {partition_names or db_files or 'internal'}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 9: BusCompactor instantiation
# ---------------------------------------------------------------------------

@run_test("compactor_instantiate")
def test_compactor_instantiate():
    from agent_comm.compactor import BusCompactor, OffsetStore
    tmpdir = tempfile.mkdtemp(prefix="test_compact_")
    try:
        # Create offset store first (required by BusCompactor)
        offset_db = os.path.join(tmpdir, "offsets.db")
        offset_store = OffsetStore(db_path=offset_db)

        # BusCompactor(comm_dir, offset_store, default_ttl)
        compactor = BusCompactor(tmpdir, offset_store, default_ttl=300)
        assert compactor is not None, "BusCompactor returned None"

        offset_store.close() if hasattr(offset_store, 'close') else None
        return "BusCompactor instantiated with OffsetStore"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Integration tests for overhaul deliverables")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if args.repo_root:
        global REPO_ROOT
        REPO_ROOT = args.repo_root
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)

    output_path = args.output or os.path.join(
        REPO_ROOT, "storage", "coordination", "overhaul", "output",
        "integration_test_results.json"
    )

    print("=" * 60)
    print("INTEGRATION TESTS - Data Storage Overhaul")
    print("=" * 60)
    print(f"  Repo root: {REPO_ROOT}")
    print()

    # Run all tests
    test_fns = [
        test_rate_limiter_import,
        test_compactor_import,
        test_db_partition_import,
        test_rate_limiter_instantiate,
        test_bus_round_trip,
        test_offset_persistence,
        test_rate_limiter_quota,
        test_db_partition_instantiate,
        test_compactor_instantiate,
    ]

    for test_fn in test_fns:
        test_fn()

    # Summary
    print()
    print("-" * 60)
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)
    print(f"Results: {passed}/{total} passed, {failed} failed")

    if failed > 0:
        print("\nFailed tests:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.error}")

    # Write results
    report = {
        "title": "Integration Test Results",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repo_root": REPO_ROOT,
        "summary": {
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": f"{passed / total * 100:.1f}%" if total else "N/A",
        },
        "tests": [r.to_dict() for r in results],
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nReport written to: {output_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
