"""Stress tests for think_tank module.

Pushes deduplication, concurrent submissions, report generation, priority
edge cases, empty findings, roadmap generation, and file I/O to their limits.
"""

import functools
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from think_tank import (
    _deduplicate,
    _fingerprint,
    _generate_roadmap,
    _group_findings,
    _normalise_finding,
    _priority_key,
    write_report,
)


# ---------------------------------------------------------------------------
# Retry decorator for SQLite resilience in test helpers
# ---------------------------------------------------------------------------

_MAX_TEST_RETRIES = 5
_TEST_RETRY_BACKOFF = 0.05


def _retry_on_busy(func):
    """Retry a test helper on sqlite3.OperationalError (SQLITE_BUSY)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        delay = _TEST_RETRY_BACKOFF
        last_err = None
        for attempt in range(_MAX_TEST_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    last_err = e
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(title, category="testing", priority="medium",
                  team="team-a", agent_id="agent-1", content="",
                  ts=None):
    """Create a normalised finding dict for testing."""
    raw = {
        "title": title,
        "category": category,
        "priority": priority,
        "team": team,
        "agent_id": agent_id,
        "content": content,
        "ts": ts or time.time(),
    }
    return _normalise_finding(raw, "test")


def _setup_sqlite_findings(db_path, findings):
    """Create a team_findings table and insert findings into it."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT,
            agent_id TEXT,
            category TEXT,
            title TEXT,
            content TEXT,
            priority TEXT DEFAULT 'medium',
            ts REAL
        )
    """)
    for f in findings:
        conn.execute(
            """INSERT INTO team_findings (team, agent_id, category, title, content, priority, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (f.get("team", "unknown"), f.get("agent_id", "unknown"),
             f.get("category", "uncategorised"), f.get("title", "Untitled"),
             f.get("content", ""), f.get("priority", "medium"),
             f.get("ts", time.time())),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class _StressBase(unittest.TestCase):
    """Common setup: temp directory for isolated test state."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tt_stress_")
        self.db_path = os.path.join(self.tmpdir, "db", "state.db")
        self.output_dir = os.path.join(self.tmpdir, "output")
        os.makedirs(os.path.join(self.tmpdir, "db"), exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===================================================================
# 1. Large Volume Deduplication
# ===================================================================


class TestLargeVolumeDeduplication(_StressBase):
    """100+ findings with ~30% duplicates."""

    def test_130_findings_with_30pct_duplicates(self):
        """Generate 130 findings where 30% share titles with earlier ones."""
        findings = []
        # 100 unique findings
        for i in range(100):
            findings.append(_make_finding(
                title=f"Finding {i}",
                category=f"cat-{i % 10}",
                priority=["critical", "high", "medium", "low"][i % 4],
                team=f"team-{i % 5}",
            ))
        # 30 duplicates (same title+category as first 30)
        for i in range(30):
            findings.append(_make_finding(
                title=f"Finding {i}",
                category=f"cat-{i % 10}",
                priority="high",  # Higher priority than some originals
                team=f"team-dup-{i % 3}",
                ts=time.time() + 100,  # More recent
            ))

        unique = _deduplicate(findings)
        self.assertEqual(len(unique), 100)

        # Verify deduplication kept higher-priority or more-recent versions
        by_title = {f["title"]: f for f in unique}
        for i in range(30):
            f = by_title[f"Finding {i}"]
            # Original priorities: critical(0), high(1), medium(2), low(3)
            orig_priority = ["critical", "high", "medium", "low"][i % 4]
            if _priority_key("high") < _priority_key(orig_priority):
                # Duplicate was higher priority, should have won
                self.assertEqual(f["priority"], "high")
                self.assertIn("also_from", f)

    def test_all_duplicates(self):
        """All 100 findings are duplicates of the same title+category."""
        findings = []
        for i in range(100):
            findings.append(_make_finding(
                title="Same Finding",
                category="same-cat",
                priority=["critical", "high", "medium", "low"][i % 4],
                team=f"team-{i}",
                ts=float(i),
            ))

        unique = _deduplicate(findings)
        self.assertEqual(len(unique), 1)

        # The winner should be the critical-priority one
        self.assertEqual(unique[0]["priority"], "critical")

    def test_no_duplicates(self):
        """200 unique findings -- none removed."""
        findings = [
            _make_finding(title=f"Unique-{i}", category=f"cat-{i}")
            for i in range(200)
        ]
        unique = _deduplicate(findings)
        self.assertEqual(len(unique), 200)


# ===================================================================
# 2. Concurrent Findings Submission
# ===================================================================


class TestConcurrentFindingsSubmission(_StressBase):
    """Multiple teams writing findings to SQLite concurrently."""

    def test_10_teams_submit_findings_concurrently(self):
        """10 threads each insert 20 findings into the same SQLite DB."""
        num_teams = 10
        findings_per_team = 20
        barrier = threading.Barrier(num_teams, timeout=30)
        errors = []

        @_retry_on_busy
        def _insert_finding(conn, f):
            conn.execute(
                """INSERT INTO team_findings
                   (team, agent_id, category, title, content, priority, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (f["team"], f["agent_id"], f["category"],
                 f["title"], f["content"], f["priority"], f["ts"]),
            )
            conn.commit()

        def submit_findings(idx):
            try:
                conn = sqlite3.connect(
                    self.db_path, timeout=30, check_same_thread=False,
                )
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")
                barrier.wait()
                for j in range(findings_per_team):
                    f = _make_finding(
                        title=f"Team-{idx}-Finding-{j}",
                        category=f"cat-{j % 5}",
                        team=f"team-{idx}",
                        agent_id=f"agent-{idx}",
                    )
                    _insert_finding(conn, f)
                conn.close()
            except Exception as e:
                errors.append((idx, e))

        # Create the table first
        _setup_sqlite_findings(self.db_path, [])

        threads = [threading.Thread(target=submit_findings, args=(i,))
                   for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [], f"Submission errors: {errors}")

        # Verify all findings were inserted
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM team_findings").fetchone()[0]
        conn.close()
        self.assertEqual(count, num_teams * findings_per_team)


# ===================================================================
# 3. Report Generation with Very Large Finding Sets
# ===================================================================


class TestLargeReportGeneration(_StressBase):
    """Report generation with hundreds of findings."""

    def test_report_with_500_findings(self):
        """Generate a report from 500 findings across 10 categories."""
        findings = []
        for i in range(500):
            findings.append(_make_finding(
                title=f"Large-Finding-{i}",
                category=f"category-{i % 10}",
                priority=["critical", "high", "medium", "low"][i % 4],
                team=f"team-{i % 8}",
                content=f"Content for finding {i}. " * 20,  # Substantial content
            ))

        unique = _deduplicate(findings)
        self.assertEqual(len(unique), 500)

        grouped = _group_findings(unique)
        self.assertEqual(len(grouped), 10)

        # Each category should have 50 findings
        for cat, items in grouped.items():
            self.assertEqual(len(items), 50)
            # Verify sorted by priority then timestamp
            for j in range(len(items) - 1):
                pk_a = _priority_key(items[j]["priority"])
                pk_b = _priority_key(items[j + 1]["priority"])
                self.assertLessEqual(pk_a, pk_b)

        roadmap = _generate_roadmap(grouped)
        self.assertGreater(len(roadmap), 0)

        # Roadmap should be globally sorted by priority
        for j in range(len(roadmap) - 1):
            self.assertLessEqual(
                _priority_key(roadmap[j]["priority"]),
                _priority_key(roadmap[j + 1]["priority"]),
            )

    def test_report_write_large_json(self):
        """Write a large report (500 findings) to disk and verify it's valid JSON."""
        findings = [
            _make_finding(
                title=f"Write-Test-{i}",
                category=f"cat-{i % 5}",
                content="x" * 500,
            )
            for i in range(500)
        ]
        grouped = _group_findings(_deduplicate(findings))
        roadmap = _generate_roadmap(grouped)

        report = {
            "generated_at": time.time(),
            "total_findings": len(findings),
            "categories": {cat: {"findings": items} for cat, items in grouped.items()},
            "roadmap": roadmap,
            "summary": "Test report with 500 findings.",
        }

        output_path = os.path.join(self.output_dir, "large-report.json")
        result_path = write_report(report, output_path)
        self.assertEqual(result_path, output_path)
        self.assertTrue(os.path.exists(output_path))

        # Verify it's valid JSON and round-trips correctly
        with open(output_path, "r") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["total_findings"], 500)
        self.assertEqual(len(loaded["categories"]), 5)


# ===================================================================
# 4. Priority Distribution Edge Cases
# ===================================================================


class TestPriorityEdgeCases(_StressBase):
    """Edge cases: all same priority, all critical, all low."""

    def test_all_same_priority_medium(self):
        """100 findings all with medium priority."""
        findings = [
            _make_finding(title=f"Med-{i}", priority="medium", category="testing")
            for i in range(100)
        ]
        grouped = _group_findings(findings)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(len(grouped["testing"]), 100)

        roadmap = _generate_roadmap(grouped)
        self.assertEqual(len(roadmap), 1)
        self.assertEqual(roadmap[0]["priority"], "medium")
        self.assertEqual(roadmap[0]["item_count"], 100)
        self.assertIn("Schedule", roadmap[0]["action"])

    def test_all_critical(self):
        """50 findings all critical priority."""
        findings = [
            _make_finding(
                title=f"Crit-{i}", priority="critical",
                category=f"cat-{i % 3}",
            )
            for i in range(50)
        ]
        grouped = _group_findings(findings)
        roadmap = _generate_roadmap(grouped)

        # All roadmap items should be critical
        for item in roadmap:
            self.assertEqual(item["priority"], "critical")
            self.assertIn("immediately", item["action"])

    def test_all_low(self):
        """50 findings all low priority."""
        findings = [
            _make_finding(
                title=f"Low-{i}", priority="low",
                category=f"cat-{i % 4}",
            )
            for i in range(50)
        ]
        grouped = _group_findings(findings)
        roadmap = _generate_roadmap(grouped)

        for item in roadmap:
            self.assertEqual(item["priority"], "low")
            self.assertIn("Track", item["action"])

    def test_mixed_priorities_in_single_category(self):
        """One category with all four priority levels."""
        findings = []
        for p in ["critical", "high", "medium", "low"]:
            for i in range(10):
                findings.append(_make_finding(
                    title=f"{p}-{i}", priority=p, category="mixed",
                ))

        grouped = _group_findings(findings)
        self.assertEqual(len(grouped["mixed"]), 40)

        # Verify sorting: critical first, then high, medium, low
        items = grouped["mixed"]
        priorities_seen = [item["priority"] for item in items]
        expected_order = (["critical"] * 10 + ["high"] * 10
                          + ["medium"] * 10 + ["low"] * 10)
        self.assertEqual(priorities_seen, expected_order)

        roadmap = _generate_roadmap(grouped)
        self.assertEqual(len(roadmap), 4)  # One entry per priority level


# ===================================================================
# 5. Empty Findings Handling
# ===================================================================


class TestEmptyFindings(_StressBase):
    """Deduplication, grouping, and roadmap with no findings."""

    def test_empty_deduplication(self):
        unique = _deduplicate([])
        self.assertEqual(unique, [])

    def test_empty_grouping(self):
        grouped = _group_findings([])
        self.assertEqual(grouped, {})

    def test_empty_roadmap(self):
        roadmap = _generate_roadmap({})
        self.assertEqual(roadmap, [])

    def test_findings_with_missing_fields(self):
        """Findings with minimal or missing fields should normalise gracefully."""
        raw_minimal = {"title": "Just a title"}
        normalised = _normalise_finding(raw_minimal, "test")
        self.assertEqual(normalised["title"], "Just a title")
        self.assertEqual(normalised["category"], "uncategorised")
        self.assertEqual(normalised["priority"], "medium")
        self.assertEqual(normalised["team"], "unknown")

        raw_empty = {}
        normalised_empty = _normalise_finding(raw_empty, "test")
        self.assertEqual(normalised_empty["title"], "Untitled")


# ===================================================================
# 6. Roadmap Generation with Complex Category Mixing
# ===================================================================


class TestComplexRoadmap(_StressBase):
    """Roadmap with many categories and overlapping teams."""

    def test_20_categories_4_priorities_8_teams(self):
        """Generate a roadmap spanning 20 categories, all priorities, 8 teams."""
        findings = []
        for cat_idx in range(20):
            for pri_idx, priority in enumerate(["critical", "high", "medium", "low"]):
                for team_idx in range(8):
                    findings.append(_make_finding(
                        title=f"C{cat_idx}-P{pri_idx}-T{team_idx}",
                        category=f"category-{cat_idx}",
                        priority=priority,
                        team=f"team-{team_idx}",
                    ))

        self.assertEqual(len(findings), 20 * 4 * 8)  # 640

        unique = _deduplicate(findings)
        self.assertEqual(len(unique), 640)  # All unique

        grouped = _group_findings(unique)
        self.assertEqual(len(grouped), 20)

        roadmap = _generate_roadmap(grouped)
        # 20 categories x 4 priorities = 80 roadmap entries
        self.assertEqual(len(roadmap), 80)

        # Verify global priority ordering
        for j in range(len(roadmap) - 1):
            self.assertLessEqual(
                _priority_key(roadmap[j]["priority"]),
                _priority_key(roadmap[j + 1]["priority"]),
            )

        # Each entry should list 8 teams
        for item in roadmap:
            self.assertEqual(item["item_count"], 8)
            self.assertEqual(len(item["teams_involved"]), 8)

    def test_roadmap_action_text_correctness(self):
        """Verify action text matches priority level."""
        findings = [
            _make_finding(title="Crit-1", priority="critical", category="deploy"),
            _make_finding(title="High-1", priority="high", category="build"),
            _make_finding(title="Med-1", priority="medium", category="docs"),
            _make_finding(title="Low-1", priority="low", category="style"),
        ]
        grouped = _group_findings(findings)
        roadmap = _generate_roadmap(grouped)

        action_map = {item["priority"]: item["action"] for item in roadmap}
        self.assertIn("immediately", action_map["critical"])
        self.assertIn("immediately", action_map["high"])
        self.assertIn("Schedule", action_map["medium"])
        self.assertIn("Track", action_map["low"])


# ===================================================================
# 7. File I/O Stress (Concurrent Report Writes)
# ===================================================================


class TestConcurrentReportWrites(_StressBase):
    """Multiple threads writing reports to different files simultaneously."""

    def test_10_concurrent_report_writes(self):
        """10 threads write separate reports concurrently."""
        num_writers = 10
        barrier = threading.Barrier(num_writers, timeout=30)
        errors = []

        def write_one(idx):
            try:
                findings = [
                    _make_finding(
                        title=f"Writer-{idx}-Finding-{j}",
                        category=f"cat-{j % 3}",
                        team=f"team-{idx}",
                    )
                    for j in range(50)
                ]
                grouped = _group_findings(_deduplicate(findings))
                roadmap = _generate_roadmap(grouped)
                report = {
                    "generated_at": time.time(),
                    "writer": idx,
                    "total_findings": len(findings),
                    "categories": grouped,
                    "roadmap": roadmap,
                }
                path = os.path.join(self.output_dir, f"report-{idx}.json")
                barrier.wait()
                write_report(report, path)
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=write_one, args=(i,))
                   for i in range(num_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Write errors: {errors}")

        # Verify all files exist and are valid JSON
        for i in range(num_writers):
            path = os.path.join(self.output_dir, f"report-{i}.json")
            self.assertTrue(os.path.exists(path), f"Missing report-{i}.json")
            with open(path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["writer"], i)
            self.assertEqual(data["total_findings"], 50)

    def test_overwrite_same_file_10_times_concurrently(self):
        """10 threads all write to the SAME file -- last write wins, no corruption."""
        num_writers = 10
        barrier = threading.Barrier(num_writers, timeout=30)
        errors = []
        target_path = os.path.join(self.output_dir, "shared-report.json")

        def overwrite(idx):
            try:
                report = {
                    "generated_at": time.time(),
                    "writer": idx,
                    "total_findings": idx * 10,
                    "categories": {},
                    "roadmap": [],
                    "summary": f"Report from writer {idx}",
                }
                barrier.wait()
                write_report(report, target_path)
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=overwrite, args=(i,))
                   for i in range(num_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"Overwrite errors: {errors}")

        # File should exist and be valid JSON (one of the writers' data)
        self.assertTrue(os.path.exists(target_path))
        with open(target_path, "r") as f:
            data = json.load(f)
        self.assertIn("writer", data)
        self.assertIn(data["writer"], list(range(num_writers)))


# ===================================================================
# 8. Fingerprint Stability and Collision Resistance
# ===================================================================


class TestFingerprintStress(_StressBase):
    """Fingerprint correctness under volume."""

    def test_1000_unique_fingerprints(self):
        """1000 distinct title+category pairs produce 1000 unique fingerprints."""
        fps = set()
        for i in range(1000):
            f = _make_finding(title=f"Title-{i}", category=f"Cat-{i % 50}")
            fp = _fingerprint(f)
            fps.add(fp)
        self.assertEqual(len(fps), 1000)

    def test_fingerprint_case_insensitivity(self):
        """Fingerprints are case-insensitive for title and category."""
        f1 = _make_finding(title="Hello World", category="Testing")
        f2 = _make_finding(title="hello world", category="testing")
        f3 = _make_finding(title="HELLO WORLD", category="TESTING")

        fp1 = _fingerprint(f1)
        fp2 = _fingerprint(f2)
        fp3 = _fingerprint(f3)

        self.assertEqual(fp1, fp2)
        self.assertEqual(fp2, fp3)

    def test_fingerprint_whitespace_stripping(self):
        """Leading/trailing whitespace in title/category is stripped."""
        f1 = _make_finding(title="  Padded Title  ", category="  padded cat  ")
        f2 = _make_finding(title="Padded Title", category="padded cat")

        self.assertEqual(_fingerprint(f1), _fingerprint(f2))


if __name__ == "__main__":
    unittest.main()
