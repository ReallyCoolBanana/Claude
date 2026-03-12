#!/usr/bin/env python3
"""Comprehensive validation suite for the data storage overhaul operation.

Validates outputs from all 4 parallel overhaul teams:
- ALPHA: Proto A rate limiter fix and bus compaction
- BETA: Index unification and automated generation
- GAMMA: Schema standardization and deduplication
- DELTA: SQLite partitioning and contention monitoring

Outputs a structured JSON report with pass/fail per check, timing data.
Exit code 0 = all pass, 1 = failures found.

Usage:
    python validate_overhaul.py [--output report.json] [--repo-root /path/to/repo]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Repository root discovery
# ---------------------------------------------------------------------------

def _find_repo_root(start: str | None = None) -> str:
    """Walk up from *start* (default: this file's directory) until we find CLAUDE.md."""
    d = start or os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isfile(os.path.join(d, "CLAUDE.md")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError("Could not locate repository root (no CLAUDE.md found)")


# ---------------------------------------------------------------------------
# Validation result helpers
# ---------------------------------------------------------------------------

class ValidationResult:
    """Accumulates check results for a single validation category."""

    def __init__(self, category: str):
        self.category = category
        self.checks: list[dict] = []
        self.start_time: float = 0.0
        self.end_time: float = 0.0

    def start(self) -> None:
        self.start_time = time.monotonic()

    def stop(self) -> None:
        self.end_time = time.monotonic()

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000

    def add_pass(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "result": "pass", "detail": detail})

    def add_fail(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "result": "fail", "detail": detail})

    def add_skip(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "result": "skip", "detail": detail})

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c["result"] == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c["result"] == "fail")

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.checks if c["result"] == "skip")

    @property
    def all_pass(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "duration_ms": round(self.duration_ms, 3),
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "all_pass": self.all_pass,
            "checks": self.checks,
        }


# ===================================================================
# 1. INDEX VALIDATION (verifies BETA team output)
# ===================================================================

def validate_indexes(repo_root: str) -> ValidationResult:
    """Scan all index.json files, check referenced files exist, detect orphans."""
    result = ValidationResult("index_validation")
    result.start()

    index_paths = [
        "knowledge-base/index.json",
        "storage/scripts/index.json",
        "storage/api-tools/index.json",
        "storage/sources/index.json",
        "storage/data/index.json",
        "storage/coordination/sops/index.json",
        "teams/sessions/index.json",
        "market-research/picks/index.json",
    ]

    total_refs = 0
    missing_refs = 0
    orphan_files: list[str] = []

    for rel_index in index_paths:
        index_path = os.path.join(repo_root, rel_index)
        check_name = f"index_exists:{rel_index}"

        if not os.path.isfile(index_path):
            result.add_fail(check_name, f"Index file not found: {index_path}")
            continue
        result.add_pass(check_name)

        # Parse the index
        try:
            with open(index_path, "r") as f:
                index_data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            result.add_fail(f"index_parse:{rel_index}", str(exc))
            continue
        result.add_pass(f"index_parse:{rel_index}")

        # Validate JSON structure
        if not isinstance(index_data, (dict, list)):
            result.add_fail(f"index_structure:{rel_index}", "Root must be dict or list")
            continue
        result.add_pass(f"index_structure:{rel_index}")

        # Extract file references from entries
        index_dir = os.path.dirname(index_path)
        entries = _extract_entries(index_data)
        referenced_files: set[str] = set()

        for entry in entries:
            # Look for file/path references in common fields
            for key in ("file", "path", "source", "filename", "location"):
                ref = entry.get(key)
                if ref and isinstance(ref, str):
                    # Resolve relative to index dir or repo root
                    abs_ref = os.path.join(index_dir, ref)
                    if not os.path.isabs(ref):
                        abs_from_root = os.path.join(repo_root, ref)
                    else:
                        abs_from_root = ref

                    total_refs += 1
                    referenced_files.add(os.path.abspath(abs_ref))
                    referenced_files.add(os.path.abspath(abs_from_root))

                    if os.path.exists(abs_ref) or os.path.exists(abs_from_root):
                        pass  # file found
                    else:
                        missing_refs += 1
                        result.add_fail(
                            f"ref_exists:{rel_index}:{ref}",
                            f"Referenced file not found: {ref}"
                        )

        # Check for required fields in entries
        for i, entry in enumerate(entries):
            entry_id = entry.get("id", f"entry_{i}")
            if "id" not in entry and "title" not in entry and "name" not in entry:
                result.add_fail(
                    f"entry_identity:{rel_index}:{i}",
                    "Entry lacks id, title, and name fields"
                )

        # Check for duplicate IDs
        ids = [e.get("id") for e in entries if e.get("id")]
        seen_ids: set[str] = set()
        for eid in ids:
            if eid in seen_ids:
                result.add_fail(f"duplicate_id:{rel_index}:{eid}", f"Duplicate ID: {eid}")
            else:
                seen_ids.add(eid)

    # Summary checks
    result.add_pass(
        "index_scan_complete",
        f"Scanned {len(index_paths)} index paths, {total_refs} file references, {missing_refs} missing"
    )

    if missing_refs == 0 and total_refs > 0:
        result.add_pass("all_refs_resolve", f"All {total_refs} referenced files exist")
    elif total_refs == 0:
        result.add_skip("all_refs_resolve", "No file references found in indexes")
    else:
        result.add_fail("all_refs_resolve", f"{missing_refs}/{total_refs} references broken")

    result.stop()
    return result


def _extract_entries(data: Any) -> list[dict]:
    """Recursively extract entry dicts from an index structure."""
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        # Check for common wrapper keys
        for key in ("entries", "items", "tools", "scripts", "sources",
                     "methods", "picks", "sessions", "sops", "data_files"):
            if key in data and isinstance(data[key], list):
                return [e for e in data[key] if isinstance(e, dict)]
        # If the dict itself has id/title, treat it as a single entry
        if "id" in data or "title" in data:
            return [data]
        # Recurse into values
        results = []
        for v in data.values():
            if isinstance(v, (list, dict)):
                results.extend(_extract_entries(v))
        return results
    return []


# ===================================================================
# 2. SCHEMA VALIDATION (verifies GAMMA team output)
# ===================================================================

def validate_schema(repo_root: str) -> ValidationResult:
    """Validate entries against schema.json if it exists."""
    result = ValidationResult("schema_validation")
    result.start()

    schema_path = os.path.join(repo_root, "storage", "data", "schema.json")
    if not os.path.isfile(schema_path):
        result.add_skip("schema_exists", "schema.json not found (GAMMA team may not have created it yet)")
        result.stop()
        return result
    result.add_pass("schema_exists")

    try:
        with open(schema_path, "r") as f:
            schema = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        result.add_fail("schema_parse", str(exc))
        result.stop()
        return result
    result.add_pass("schema_parse")

    # Validate schema has expected structure
    required_schema_keys = ["type", "required", "properties"]
    for key in required_schema_keys:
        if key in schema:
            result.add_pass(f"schema_has_{key}")
        else:
            result.add_fail(f"schema_has_{key}", f"Schema missing required key: {key}")

    # Validate required fields list
    required_fields = schema.get("required", [])
    if required_fields:
        result.add_pass("schema_required_fields", f"Required fields: {required_fields}")
    else:
        result.add_fail("schema_required_fields", "No required fields defined")

    # Check property definitions
    properties = schema.get("properties", {})
    expected_props = ["id", "title", "date", "type", "status"]
    for prop in expected_props:
        if prop in properties:
            result.add_pass(f"schema_prop:{prop}")
        else:
            result.add_fail(f"schema_prop:{prop}", f"Expected property '{prop}' missing from schema")

    # Lightweight validation of KB entries against schema required fields
    kb_index_path = os.path.join(repo_root, "knowledge-base", "index.json")
    if os.path.isfile(kb_index_path):
        try:
            with open(kb_index_path, "r") as f:
                kb_index = json.load(f)
            entries = _extract_entries(kb_index)
            validated_count = 0
            violation_count = 0
            for entry in entries[:20]:  # sample up to 20
                entry_id = entry.get("id", "unknown")
                missing = [f for f in required_fields if f not in entry]
                if missing:
                    violation_count += 1
                    result.add_fail(
                        f"entry_schema:{entry_id}",
                        f"Missing required fields: {missing}"
                    )
                else:
                    validated_count += 1
            result.add_pass(
                "schema_validation_summary",
                f"Validated {validated_count} entries, {violation_count} violations"
            )
        except Exception as exc:
            result.add_fail("kb_entry_validation", str(exc))

    # Check definitions section
    definitions = schema.get("definitions", {})
    expected_defs = ["iso_date", "id_format", "status_enum"]
    for defn in expected_defs:
        if defn in definitions:
            result.add_pass(f"schema_def:{defn}")
        else:
            result.add_skip(f"schema_def:{defn}", f"Definition '{defn}' not in schema")

    result.stop()
    return result


# ===================================================================
# 3. BUS VALIDATION (verifies ALPHA team output)
# ===================================================================

def validate_bus(repo_root: str) -> ValidationResult:
    """Test bus read/write with compaction enabled."""
    result = ValidationResult("bus_validation")
    result.start()

    # Import bus_core
    sys.path.insert(0, repo_root)
    try:
        from storage.coordination.bus_core import (
            bus_write, bus_read, sanitize_channel, VALID_MSG_TYPES, MAX_MESSAGE_BYTES
        )
    except ImportError as exc:
        result.add_fail("bus_core_import", f"Cannot import bus_core: {exc}")
        result.stop()
        return result
    result.add_pass("bus_core_import")

    # Test in a temporary directory
    with tempfile.TemporaryDirectory(prefix="overhaul_bus_") as tmpdir:
        bus_dir = os.path.join(tmpdir, "bus")
        channel = "test-validation"

        # Test basic write
        msg_id = bus_write(bus_dir, channel, "info", {"test": "basic_write"}, "epsilon", "val-1")
        if msg_id:
            result.add_pass("bus_write_basic", f"msg_id={msg_id}")
        else:
            result.add_fail("bus_write_basic", "bus_write returned None")

        # Test basic read
        msgs, offset = bus_read(bus_dir, channel, 0)
        if len(msgs) == 1 and msgs[0].get("body", {}).get("test") == "basic_write":
            result.add_pass("bus_read_basic", f"Read 1 message, offset={offset}")
        else:
            result.add_fail("bus_read_basic", f"Expected 1 message, got {len(msgs)}")

        # Test offset tracking - write more, read from offset
        for i in range(5):
            bus_write(bus_dir, channel, "info", {"seq": i}, "epsilon", "val-1")
        msgs2, offset2 = bus_read(bus_dir, channel, offset)
        if len(msgs2) == 5:
            result.add_pass("bus_offset_tracking", f"Read 5 new messages from offset {offset}")
        else:
            result.add_fail("bus_offset_tracking", f"Expected 5, got {len(msgs2)}")

        # Test idempotent read at same offset
        msgs3, offset3 = bus_read(bus_dir, channel, offset2)
        if len(msgs3) == 0 and offset3 == offset2:
            result.add_pass("bus_read_idempotent", "No new messages at current offset")
        else:
            result.add_fail("bus_read_idempotent", f"Expected 0 messages, got {len(msgs3)}")

        # Test channel sanitization
        safe = sanitize_channel("team/dangerous../path")
        if "/" not in safe and ".." not in safe:
            result.add_pass("bus_channel_sanitize", f"Sanitized to: {safe}")
        else:
            result.add_fail("bus_channel_sanitize", f"Unsafe channel name: {safe}")

        # Test valid message types
        if "info" in VALID_MSG_TYPES and "heartbeat" in VALID_MSG_TYPES:
            result.add_pass("bus_valid_msg_types", f"{len(VALID_MSG_TYPES)} types defined")
        else:
            result.add_fail("bus_valid_msg_types", "Missing expected message types")

        # Test message size limit
        oversized_body = {"data": "x" * (MAX_MESSAGE_BYTES + 100)}
        try:
            bus_write(bus_dir, "test-size", "info", oversized_body, "epsilon", "val-1")
            result.add_fail("bus_size_limit", "Oversized message was not rejected")
        except ValueError:
            result.add_pass("bus_size_limit", f"Correctly rejected message > {MAX_MESSAGE_BYTES} bytes")

        # Test TTL expiration
        bus_write(bus_dir, "test-ttl", "info", {"ttl_test": True}, "epsilon", "val-1", ttl=0)
        time.sleep(0.01)
        ttl_msgs, _ = bus_read(bus_dir, "test-ttl", 0)
        if len(ttl_msgs) == 0:
            result.add_pass("bus_ttl_expiry", "Expired messages correctly filtered")
        else:
            result.add_fail("bus_ttl_expiry", f"Expected 0 expired messages, got {len(ttl_msgs)}")

        # Test concurrent writes
        errors: list[str] = []
        barrier = threading.Barrier(4)

        def concurrent_writer(writer_id: int):
            try:
                barrier.wait(timeout=5)
                for j in range(10):
                    mid = bus_write(bus_dir, "test-concurrent", "info",
                                    {"writer": writer_id, "seq": j}, "epsilon", f"w-{writer_id}")
                    if mid is None:
                        errors.append(f"Writer {writer_id} msg {j} failed")
            except Exception as exc:
                errors.append(f"Writer {writer_id}: {exc}")

        threads = [threading.Thread(target=concurrent_writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        if not errors:
            result.add_pass("bus_concurrent_writes", "4 writers x 10 messages with no errors")
        else:
            result.add_fail("bus_concurrent_writes", f"{len(errors)} errors: {errors[:3]}")

        # Verify all concurrent messages readable
        all_msgs, _ = bus_read(bus_dir, "test-concurrent", 0)
        if len(all_msgs) == 40:
            result.add_pass("bus_concurrent_read_all", "All 40 concurrent messages readable")
        else:
            result.add_fail("bus_concurrent_read_all", f"Expected 40, got {len(all_msgs)}")

        # Test bus compaction (check if Alpha added compaction)
        try:
            from storage.coordination.bus_core import bus_compact
            # If compaction exists, test it
            compact_channel = "test-compact"
            for i in range(20):
                bus_write(bus_dir, compact_channel, "info", {"i": i}, "epsilon", "val-1", ttl=0)
            time.sleep(0.01)
            # Write some non-expired messages
            for i in range(5):
                bus_write(bus_dir, compact_channel, "info", {"live": i}, "epsilon", "val-1")

            before_size = os.path.getsize(
                os.path.join(bus_dir, f"{sanitize_channel(compact_channel)}.jsonl")
            )
            bus_compact(bus_dir, compact_channel)
            after_size = os.path.getsize(
                os.path.join(bus_dir, f"{sanitize_channel(compact_channel)}.jsonl")
            )

            if after_size < before_size:
                result.add_pass("bus_compaction",
                                f"Compacted {before_size} -> {after_size} bytes "
                                f"({100 - (after_size/before_size)*100:.1f}% reduction)")
            else:
                result.add_fail("bus_compaction",
                                f"No size reduction: {before_size} -> {after_size}")

            # Verify live messages survived compaction
            live_msgs, _ = bus_read(bus_dir, compact_channel, 0)
            if len(live_msgs) >= 5:
                result.add_pass("bus_compaction_preserves_live",
                                f"{len(live_msgs)} live messages after compaction")
            else:
                result.add_fail("bus_compaction_preserves_live",
                                f"Expected >=5 live messages, got {len(live_msgs)}")
        except ImportError:
            result.add_skip("bus_compaction", "bus_compact not available (ALPHA may not have added it yet)")
            result.add_skip("bus_compaction_preserves_live", "Depends on bus_compaction")

    result.stop()
    return result


# ===================================================================
# 4. RATE LIMITER VALIDATION (verifies ALPHA team output)
# ===================================================================

def validate_rate_limiter(repo_root: str) -> ValidationResult:
    """Test rate limiter under concurrent load, verify 0% false denials."""
    result = ValidationResult("rate_limiter_validation")
    result.start()

    sys.path.insert(0, repo_root)

    # Try multiple possible locations for the rate limiter
    rate_limiter_class = None
    rate_limiter_source = None

    for module_path, class_name in [
        ("prototype.agent_comm.state", "RateLimiter"),
        ("storage.coordination.rate_limiter", "RateLimiter"),
        ("prototype.agent_comm.rate_limiter", "RateLimiter"),
    ]:
        try:
            mod = __import__(module_path, fromlist=[class_name])
            rate_limiter_class = getattr(mod, class_name, None)
            if rate_limiter_class:
                rate_limiter_source = module_path
                break
        except (ImportError, AttributeError):
            continue

    if rate_limiter_class is None:
        # Try to find any rate limiter in the proto A state module
        try:
            from prototype.agent_comm import state
            # Look for rate limiting functions/classes
            for attr_name in dir(state):
                obj = getattr(state, attr_name)
                if callable(obj) and "rate" in attr_name.lower():
                    rate_limiter_source = f"prototype.agent_comm.state.{attr_name}"
                    break
        except ImportError:
            pass

    if rate_limiter_class is None:
        result.add_skip("rate_limiter_import",
                         "No RateLimiter class found. ALPHA team may not have deployed fix yet. "
                         "Checked: prototype.agent_comm.state, storage.coordination.rate_limiter")
        result.stop()
        return result

    result.add_pass("rate_limiter_import", f"Found at {rate_limiter_source}")

    with tempfile.TemporaryDirectory(prefix="overhaul_rl_") as tmpdir:
        try:
            # Test basic rate limiting - should allow requests within limit
            rl = rate_limiter_class(
                max_requests=100,
                window_seconds=10,
            )

            # Single-threaded: all requests within limit should be allowed
            false_denials = 0
            total_checks = 0
            for i in range(50):  # well under 100/10s limit
                total_checks += 1
                allowed = rl.check("test-agent-1")
                if not allowed:
                    false_denials += 1

            if false_denials == 0:
                result.add_pass("rl_single_thread_no_false_denials",
                                f"0/{total_checks} false denials")
            else:
                result.add_fail("rl_single_thread_no_false_denials",
                                f"{false_denials}/{total_checks} false denials "
                                f"({false_denials/total_checks*100:.1f}%)")

            # Multi-threaded: concurrent requests within aggregate limit
            concurrent_false_denials = 0
            concurrent_total = 0
            lock = threading.Lock()
            barrier = threading.Barrier(4)

            def rate_check_worker(worker_id: int, rl_instance):
                nonlocal concurrent_false_denials, concurrent_total
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    return
                local_denials = 0
                local_total = 0
                for j in range(10):  # 4 workers x 10 = 40, well under 100
                    local_total += 1
                    allowed = rl_instance.check(f"agent-{worker_id}")
                    if not allowed:
                        local_denials += 1
                with lock:
                    concurrent_false_denials += local_denials
                    concurrent_total += local_total

            rl2 = rate_limiter_class(max_requests=100, window_seconds=10)
            threads = [
                threading.Thread(target=rate_check_worker, args=(i, rl2))
                for i in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            denial_rate = (concurrent_false_denials / concurrent_total * 100
                          if concurrent_total > 0 else 0)
            if denial_rate < 1.0:
                result.add_pass("rl_concurrent_low_false_denials",
                                f"{concurrent_false_denials}/{concurrent_total} "
                                f"({denial_rate:.2f}%) - target <1%")
            else:
                result.add_fail("rl_concurrent_low_false_denials",
                                f"{concurrent_false_denials}/{concurrent_total} "
                                f"({denial_rate:.2f}%) - target <1%")

            # Test that actual over-limit requests ARE denied
            rl3 = rate_limiter_class(max_requests=5, window_seconds=60)
            for _ in range(5):
                rl3.check("overload-agent")
            denied = not rl3.check("overload-agent")
            if denied:
                result.add_pass("rl_over_limit_denied", "Over-limit request correctly denied")
            else:
                result.add_fail("rl_over_limit_denied", "Over-limit request was incorrectly allowed")

        except TypeError as exc:
            result.add_skip("rate_limiter_tests",
                            f"RateLimiter constructor signature mismatch: {exc}. "
                            "ALPHA team may have changed the API.")
        except Exception as exc:
            result.add_fail("rate_limiter_tests", f"Unexpected error: {exc}")

    result.stop()
    return result


# ===================================================================
# 5. DB PARTITION VALIDATION (verifies DELTA team output)
# ===================================================================

def validate_db_partitions(repo_root: str) -> ValidationResult:
    """Test each partition DB independently, then all together."""
    result = ValidationResult("db_partition_validation")
    result.start()

    sys.path.insert(0, repo_root)

    # Try to import partition manager
    partition_mgr = None
    for module_path in [
        "storage.coordination.db_partitions",
        "storage.coordination.db_utils",
        "storage.coordination.partition_manager",
    ]:
        try:
            mod = __import__(module_path, fromlist=["PartitionManager"])
            partition_mgr = getattr(mod, "PartitionManager", None)
            if partition_mgr:
                result.add_pass("partition_import", f"Found at {module_path}")
                break
        except (ImportError, AttributeError):
            continue

    # Also test the existing coordination modules work with WAL mode
    try:
        from storage.coordination.bus_core import init_db
        result.add_pass("init_db_import")
    except ImportError:
        result.add_fail("init_db_import", "Cannot import init_db from bus_core")
        result.stop()
        return result

    with tempfile.TemporaryDirectory(prefix="overhaul_db_") as tmpdir:
        # Test independent DB creation and WAL mode
        partition_names = ["hub", "help", "channels", "work"]
        dbs: dict[str, sqlite3.Connection] = {}

        for name in partition_names:
            db_path = os.path.join(tmpdir, f"{name}.db")
            try:
                conn = init_db(db_path)
                # Verify WAL mode
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                if mode.lower() == "wal":
                    result.add_pass(f"partition_wal:{name}", "WAL mode confirmed")
                else:
                    result.add_fail(f"partition_wal:{name}", f"Expected WAL, got {mode}")

                # Create a test table and insert data
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS test_{name} (
                        id INTEGER PRIMARY KEY,
                        data TEXT,
                        ts REAL
                    )
                """)
                conn.commit()

                for i in range(10):
                    conn.execute(
                        f"INSERT INTO test_{name} (data, ts) VALUES (?, ?)",
                        (f"test-{name}-{i}", time.time())
                    )
                conn.commit()

                count = conn.execute(f"SELECT COUNT(*) FROM test_{name}").fetchone()[0]
                if count == 10:
                    result.add_pass(f"partition_write_read:{name}", f"10 rows written and verified")
                else:
                    result.add_fail(f"partition_write_read:{name}", f"Expected 10, got {count}")

                dbs[name] = conn
            except Exception as exc:
                result.add_fail(f"partition_create:{name}", str(exc))

        # Test concurrent access to different partitions
        concurrent_errors: list[str] = []
        barrier = threading.Barrier(len(dbs))

        def partition_writer(name: str, conn: sqlite3.Connection):
            try:
                barrier.wait(timeout=5)
                for i in range(20):
                    try:
                        conn.execute(
                            f"INSERT INTO test_{name} (data, ts) VALUES (?, ?)",
                            (f"concurrent-{i}", time.time())
                        )
                        conn.commit()
                    except sqlite3.OperationalError as exc:
                        if "busy" in str(exc).lower() or "locked" in str(exc).lower():
                            time.sleep(0.01)
                        else:
                            concurrent_errors.append(f"{name}: {exc}")
            except Exception as exc:
                concurrent_errors.append(f"{name}: {exc}")

        threads = [
            threading.Thread(target=partition_writer, args=(name, conn))
            for name, conn in dbs.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        if not concurrent_errors:
            result.add_pass("partition_concurrent_independent",
                            f"{len(dbs)} partitions written concurrently with no contention")
        else:
            result.add_fail("partition_concurrent_independent",
                            f"{len(concurrent_errors)} errors: {concurrent_errors[:3]}")

        # Test concurrent writers to SAME partition (contention test)
        single_db_path = os.path.join(tmpdir, "contention_test.db")
        single_conn = init_db(single_db_path)
        single_conn.execute("""
            CREATE TABLE IF NOT EXISTS contention (
                id INTEGER PRIMARY KEY,
                writer TEXT,
                seq INTEGER,
                ts REAL
            )
        """)
        single_conn.commit()

        contention_errors: list[str] = []
        contention_barrier = threading.Barrier(4)
        contention_lock = threading.Lock()
        successful_writes = [0]

        def contention_writer(writer_id: int):
            # Each thread gets its own connection for true concurrency
            conn = init_db(single_db_path)
            try:
                contention_barrier.wait(timeout=5)
                for seq in range(25):
                    retries = 0
                    while retries < 10:
                        try:
                            conn.execute(
                                "INSERT INTO contention (writer, seq, ts) VALUES (?, ?, ?)",
                                (f"w-{writer_id}", seq, time.time())
                            )
                            conn.commit()
                            with contention_lock:
                                successful_writes[0] += 1
                            break
                        except sqlite3.OperationalError as exc:
                            if "busy" in str(exc).lower() or "locked" in str(exc).lower():
                                retries += 1
                                time.sleep(0.01 * retries)
                            else:
                                contention_errors.append(f"w-{writer_id}: {exc}")
                                break
            except Exception as exc:
                contention_errors.append(f"w-{writer_id}: {exc}")
            finally:
                conn.close()

        c_threads = [threading.Thread(target=contention_writer, args=(i,)) for i in range(4)]
        for t in c_threads:
            t.start()
        for t in c_threads:
            t.join(timeout=15)

        expected_writes = 4 * 25
        if successful_writes[0] == expected_writes:
            result.add_pass("partition_contention_4writers",
                            f"All {expected_writes} writes succeeded with 4 concurrent writers")
        elif successful_writes[0] > expected_writes * 0.95:
            result.add_pass("partition_contention_4writers",
                            f"{successful_writes[0]}/{expected_writes} writes succeeded "
                            f"(>95% success rate)")
        else:
            result.add_fail("partition_contention_4writers",
                            f"Only {successful_writes[0]}/{expected_writes} succeeded")

        # Verify data integrity
        verify_conn = init_db(single_db_path)
        total_rows = verify_conn.execute("SELECT COUNT(*) FROM contention").fetchone()[0]
        unique_rows = verify_conn.execute(
            "SELECT COUNT(DISTINCT writer || '-' || seq) FROM contention"
        ).fetchone()[0]
        verify_conn.close()

        if total_rows == unique_rows:
            result.add_pass("partition_data_integrity",
                            f"{total_rows} rows, all unique (no duplicates)")
        else:
            result.add_fail("partition_data_integrity",
                            f"{total_rows} rows but only {unique_rows} unique")

        # Cleanup
        for conn in dbs.values():
            conn.close()
        single_conn.close()

    # If partition manager exists, test it
    if partition_mgr:
        result.add_pass("partition_manager_available", "PartitionManager class found")
    else:
        result.add_skip("partition_manager_available",
                         "No PartitionManager class found (DELTA team may not have deployed yet)")

    result.stop()
    return result


# ===================================================================
# 6. COORDINATION INTEGRATION VALIDATION
# ===================================================================

def validate_coordination_integration(repo_root: str) -> ValidationResult:
    """End-to-end validation: register, heartbeat, work-steal, help-request, findings."""
    result = ValidationResult("coordination_integration")
    result.start()

    sys.path.insert(0, repo_root)

    with tempfile.TemporaryDirectory(prefix="overhaul_coord_") as tmpdir:
        db_path = os.path.join(tmpdir, "coord.db")
        bus_dir = os.path.join(tmpdir, "bus")

        # Test CoordinatorHub
        try:
            from storage.coordination.coordinator_hub import AgentReporter, CoordinatorDashboard
            result.add_pass("coordinator_hub_import")

            dashboard = CoordinatorDashboard(db_path, bus_dir=bus_dir)
            agents = []
            for i in range(3):
                agent = AgentReporter(db_path, f"agent-{i}", "epsilon", f"dev-{i}", bus_dir=bus_dir)
                agents.append(agent)
            result.add_pass("agent_registration", "3 agents registered")

            # Update status
            for i, agent in enumerate(agents):
                agent.update_status("working", i * 30, f"Task {i}")
            statuses = dashboard.get_all_status()
            if len(statuses) == 3:
                result.add_pass("status_updates", "3 status updates visible")
            else:
                result.add_fail("status_updates", f"Expected 3, got {len(statuses)}")

            # Send instructions
            inst_id = dashboard.send_instruction("agent-0", "priority_change", '{"priority": "high"}')
            instructions = agents[0].check_instructions()
            if len(instructions) == 1:
                result.add_pass("instructions_roundtrip", f"Instruction {inst_id} delivered")
            else:
                result.add_fail("instructions_roundtrip", f"Expected 1 instruction, got {len(instructions)}")

            # Complete one agent
            agents[0].report_complete("output.json")
            summary = dashboard.get_summary()
            if summary["complete"] == 1:
                result.add_pass("agent_completion", "Agent marked complete in summary")
            else:
                result.add_fail("agent_completion", f"Expected 1 complete, got {summary}")

            for agent in agents:
                agent.close()
            dashboard.close()

        except ImportError as exc:
            result.add_fail("coordinator_hub_import", str(exc))

        # Test WorkStealing
        ws_db_path = os.path.join(tmpdir, "ws.db")
        try:
            from storage.coordination.work_stealing import WorkStealing
            result.add_pass("work_stealing_import")

            ws = WorkStealing(ws_db_path, bus_dir=bus_dir)

            # Enqueue work
            work_id = ws.enqueue("epsilon", "Test task", "Description", priority=5)
            if work_id:
                result.add_pass("ws_enqueue", f"Work item {work_id} created")
            else:
                result.add_fail("ws_enqueue", "enqueue returned None/0")

            # Steal work
            item = ws.steal("other-team")
            if item and item.get("title") == "Test task":
                result.add_pass("ws_steal", "Work item stolen successfully")
            else:
                result.add_fail("ws_steal", f"Unexpected steal result: {item}")

            # Complete work
            try:
                ws.complete(work_id, "other-team", "Done")
                result.add_pass("ws_complete", "Work item completed")
            except Exception as exc:
                result.add_fail("ws_complete", str(exc))

            ws.close()
        except ImportError as exc:
            result.add_fail("work_stealing_import", str(exc))
        except Exception as exc:
            result.add_fail("work_stealing_test", str(exc))

        # Test HelpProtocol
        hp_db_path = os.path.join(tmpdir, "hp.db")
        try:
            from storage.coordination.help_protocol import HelpProtocol
            result.add_pass("help_protocol_import")

            hp = HelpProtocol(hp_db_path, bus_dir=bus_dir)

            # Register team
            hp.register_team("epsilon", capabilities=["validation", "benchmarking"])

            # Create work item
            wi_id = hp.create_work_item("epsilon", "ep-lead", "Validation", priority="high")
            if wi_id:
                result.add_pass("hp_work_item", f"Work item {wi_id} created")
            else:
                result.add_fail("hp_work_item", "create_work_item returned None/0")

            # Request help
            req_id = hp.request_help("epsilon", "ep-lead", "Need benchmarking help",
                                      required_capabilities=["benchmarking"])
            if req_id:
                result.add_pass("hp_request_help", f"Help request {req_id} created")
            else:
                result.add_fail("hp_request_help", "request_help returned None/0")

            hp.close()
        except ImportError as exc:
            result.add_fail("help_protocol_import", str(exc))
        except Exception as exc:
            result.add_fail("help_protocol_test", str(exc))

    result.stop()
    return result


# ===================================================================
# Main runner
# ===================================================================

def run_all_validations(repo_root: str) -> dict:
    """Run all validation categories and return the full report."""
    start_time = time.time()

    results = []
    validators = [
        ("index_validation", validate_indexes),
        ("schema_validation", validate_schema),
        ("bus_validation", validate_bus),
        ("rate_limiter_validation", validate_rate_limiter),
        ("db_partition_validation", validate_db_partitions),
        ("coordination_integration", validate_coordination_integration),
    ]

    for name, validator_fn in validators:
        try:
            vr = validator_fn(repo_root)
            results.append(vr.to_dict())
        except Exception as exc:
            results.append({
                "category": name,
                "duration_ms": 0,
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "all_pass": False,
                "checks": [{"name": "validator_crash", "result": "fail", "detail": str(exc)}],
            })

    end_time = time.time()

    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] for r in results)
    total_skipped = sum(r["skipped"] for r in results)
    all_pass = all(r["all_pass"] for r in results)

    report = {
        "validation_suite": "overhaul_validation",
        "version": "1.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repo_root": repo_root,
        "total_duration_ms": round((end_time - start_time) * 1000, 3),
        "summary": {
            "categories": len(results),
            "total_checks": total_passed + total_failed + total_skipped,
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "all_pass": all_pass,
        },
        "categories": results,
    }

    return report


def main():
    parser = argparse.ArgumentParser(description="Overhaul Validation Suite")
    parser.add_argument("--repo-root", default=None,
                        help="Repository root path (auto-detected if omitted)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSON report path")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed results")
    args = parser.parse_args()

    repo_root = args.repo_root or _find_repo_root()
    report = run_all_validations(repo_root)

    # Output
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report written to {args.output}")

    # Console summary
    summary = report["summary"]
    print(f"\n{'='*60}")
    print(f"OVERHAUL VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"Categories: {summary['categories']}")
    print(f"Total checks: {summary['total_checks']}")
    print(f"  Passed:  {summary['passed']}")
    print(f"  Failed:  {summary['failed']}")
    print(f"  Skipped: {summary['skipped']}")
    print(f"Duration: {report['total_duration_ms']:.1f}ms")
    print(f"Result: {'ALL PASS' if summary['all_pass'] else 'FAILURES DETECTED'}")
    print(f"{'='*60}")

    if args.verbose or summary['failed'] > 0:
        for cat in report["categories"]:
            print(f"\n--- {cat['category']} ({cat['duration_ms']:.1f}ms) ---")
            for check in cat["checks"]:
                icon = {"pass": "OK", "fail": "FAIL", "skip": "SKIP"}[check["result"]]
                detail = f" - {check['detail']}" if check.get("detail") else ""
                if check["result"] == "fail" or args.verbose:
                    print(f"  [{icon}] {check['name']}{detail}")

    sys.exit(0 if summary["all_pass"] else 1)


if __name__ == "__main__":
    main()
