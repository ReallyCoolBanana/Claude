#!/usr/bin/env python3
"""Stress tests for the Team Help Protocol.

Pushes help_protocol.py to its limits with high concurrency, volume,
race conditions, error handling, resource exhaustion, and zombie detection.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

# Add parent directory to path so we can import help_protocol
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from help_protocol import HelpProtocol

# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class StressTestBase(unittest.TestCase):
    """Base class: creates temp DB and bus directory, tracks HelpProtocol instances."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="help_stress_")
        self.db_path = os.path.join(self._tmpdir, "state.db")
        self.bus_dir = os.path.join(self._tmpdir, "bus")
        os.makedirs(self.bus_dir, exist_ok=True)
        self._instances = []

    def tearDown(self):
        for hp in self._instances:
            try:
                hp.close()
            except Exception:
                pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_hp(self, team: str = "team-a", agent: str = "agent-1") -> HelpProtocol:
        hp = HelpProtocol(self.db_path, self.bus_dir, team, agent)
        self._instances.append(hp)
        return hp

    def _raw_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn


# ===================================================================
# 1. HIGH CONCURRENCY TESTS
# ===================================================================

class TestHighConcurrencyOffers(StressTestBase):
    """20+ teams simultaneously offering help on the same request."""

    def test_20_teams_offer_same_request(self):
        # Setup: one team creates a help request
        hp_a = self._make_hp("requester", "agent-req")
        wid = hp_a.add_work_item("Contested task", description="Many will try")
        rid = hp_a.request_help(wid, "Who will claim this?")

        num_teams = 25
        results = [None] * num_teams
        errors = [None] * num_teams
        barrier = threading.Barrier(num_teams, timeout=15)

        def offer(idx):
            try:
                hp = self._make_hp(f"offer-team-{idx}", f"agent-{idx}")
                barrier.wait()
                results[idx] = hp.offer_help(rid)
            except Exception as e:
                errors[idx] = e

        threads = [threading.Thread(target=offer, args=(i,)) for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # Verify no unexpected errors
        real_errors = [e for e in errors if e is not None]
        self.assertEqual(len(real_errors), 0, f"Unexpected errors: {real_errors}")

        winners = sum(1 for r in results if r is True)
        losers = sum(1 for r in results if r is False)
        self.assertEqual(winners, 1, f"Expected exactly 1 winner, got {winners}")
        self.assertEqual(losers, num_teams - 1)


class TestHighConcurrencyStatusUpdates(StressTestBase):
    """50+ concurrent status updates from different teams."""

    def test_50_concurrent_status_updates(self):
        num_teams = 50
        errors = []
        barrier = threading.Barrier(num_teams, timeout=15)

        def update(idx):
            try:
                hp = self._make_hp(f"status-team-{idx}", f"agent-{idx}")
                barrier.wait()
                hp.update_status("working", float(idx * 2), f"Task {idx}")
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=update, args=(i,)) for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Verify all 50 teams have status entries
        conn = self._raw_conn()
        count = conn.execute("SELECT COUNT(*) as c FROM team_status").fetchone()["c"]
        conn.close()
        self.assertEqual(count, num_teams)


class TestHighConcurrencyAutoAssign(StressTestBase):
    """10+ concurrent auto_assign_idle_teams() calls."""

    def test_10_concurrent_auto_assign(self):
        # Setup: 5 help requests, 5 idle teams
        requesters = []
        for i in range(5):
            hp = self._make_hp(f"req-team-{i}", f"req-agent-{i}")
            hp.update_status("needs_help", 30.0, f"Need help {i}")
            wid = hp.add_work_item(f"Task {i}", required_caps=["coding"])
            hp.request_help(wid, f"Help me with task {i}")
            requesters.append(hp)

        for i in range(5):
            hp = self._make_hp(f"idle-team-{i}", f"idle-agent-{i}")
            hp.register_capabilities(["coding"])
            hp.update_status("idle", 100.0, "Free")

        num_assigners = 12
        all_assignments = [None] * num_assigners
        errors = []
        barrier = threading.Barrier(num_assigners, timeout=15)

        def assign(idx):
            try:
                hp = self._make_hp(f"assigner-{idx}", f"assign-agent-{idx}")
                barrier.wait()
                all_assignments[idx] = hp.auto_assign_idle_teams()
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=assign, args=(i,)) for i in range(num_assigners)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Total assignments across all callers should be <= 5
        total = sum(len(a) for a in all_assignments if a is not None)
        self.assertLessEqual(total, 5, f"Over-assigned: {total} assignments for 5 requests")

        # Each request should be assigned at most once
        assigned_requests = []
        for assignments in all_assignments:
            if assignments:
                assigned_requests.extend(a["request_id"] for a in assignments)
        self.assertEqual(len(assigned_requests), len(set(assigned_requests)),
                         "Same request assigned multiple times")


class TestHighConcurrencyCapabilityRegistration(StressTestBase):
    """Many teams registering capabilities simultaneously."""

    def test_30_teams_register_concurrently(self):
        num_teams = 30
        errors = []
        barrier = threading.Barrier(num_teams, timeout=30)

        # Pre-create all instances to avoid schema-init locking during the race
        instances = []
        for i in range(num_teams):
            instances.append(self._make_hp(f"cap-team-{i}", f"cap-agent-{i}"))

        def register(idx):
            try:
                barrier.wait()
                caps = [f"skill-{idx}-{j}" for j in range(5)]
                instances[idx].register_capabilities(caps)
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=register, args=(i,)) for i in range(num_teams)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        conn = self._raw_conn()
        count = conn.execute("SELECT COUNT(*) as c FROM team_capabilities").fetchone()["c"]
        conn.close()
        self.assertEqual(count, num_teams * 5)


# ===================================================================
# 2. VOLUME TESTS
# ===================================================================

class TestVolumeWorkItems(StressTestBase):
    """Create 1000+ work items rapidly."""

    def test_create_1000_work_items(self):
        hp = self._make_hp("volume-team", "volume-agent")
        ids = []
        for i in range(1000):
            wid = hp.add_work_item(
                f"Work item {i}",
                description=f"Description for item {i}",
                priority=["low", "medium", "high", "critical"][i % 4],
                est_minutes=float(i % 60),
            )
            ids.append(wid)

        self.assertEqual(len(ids), 1000)
        self.assertEqual(len(set(ids)), 1000, "Duplicate work item IDs")

        conn = self._raw_conn()
        count = conn.execute(
            "SELECT COUNT(*) as c FROM work_items WHERE team = 'volume-team'"
        ).fetchone()["c"]
        conn.close()
        self.assertEqual(count, 1000)


class TestVolumeHelpRequests(StressTestBase):
    """Create 100+ help requests."""

    def test_create_100_help_requests(self):
        hp = self._make_hp("help-volume", "agent-hv")
        request_ids = []
        for i in range(100):
            wid = hp.add_work_item(f"Help task {i}")
            rid = hp.request_help(wid, f"Need help with task {i}")
            request_ids.append(rid)

        self.assertEqual(len(request_ids), 100)
        self.assertEqual(len(set(request_ids)), 100)

        open_reqs = hp.get_open_help_requests()
        self.assertEqual(len(open_reqs), 100)


class TestVolumeTeamRegistration(StressTestBase):
    """Register 50+ teams with various capabilities."""

    def test_register_50_teams(self):
        all_caps = ["coding", "testing", "research", "design", "ml",
                     "devops", "security", "docs", "review", "debug"]
        for i in range(50):
            hp = self._make_hp(f"bulk-team-{i}", f"bulk-agent-{i}")
            team_caps = [all_caps[j % len(all_caps)] for j in range(i, i + 3)]
            hp.register_capabilities(team_caps)
            hp.update_status("idle", 100.0, "Standing by")

        conn = self._raw_conn()
        team_count = conn.execute("SELECT COUNT(*) as c FROM team_status").fetchone()["c"]
        cap_count = conn.execute(
            "SELECT COUNT(DISTINCT team) as c FROM team_capabilities"
        ).fetchone()["c"]
        conn.close()

        self.assertEqual(team_count, 50)
        self.assertEqual(cap_count, 50)


# ===================================================================
# 3. RACE CONDITION TESTS
# ===================================================================

class TestRaceRequestAndOffer(StressTestBase):
    """Request help while simultaneously offering help."""

    def test_request_and_offer_race(self):
        # Pre-create work items and requests
        hp_a = self._make_hp("race-req", "agent-req")
        work_ids = []
        request_ids = []
        for i in range(5):
            wid = hp_a.add_work_item(f"Race task {i}")
            rid = hp_a.request_help(wid, f"Race help {i}")
            work_ids.append(wid)
            request_ids.append(rid)

        errors = []
        offer_results = [None] * 5

        barrier = threading.Barrier(10, timeout=15)

        def create_more_requests(start_idx):
            """Create additional help requests concurrently."""
            try:
                hp = self._make_hp("race-req-extra", "agent-extra")
                barrier.wait()
                for i in range(5):
                    wid = hp.add_work_item(f"Extra race task {start_idx + i}")
                    hp.request_help(wid, f"Extra help {start_idx + i}")
            except Exception as e:
                errors.append(("creator", e))

        def offer_on_existing(idx):
            """Offer help on existing requests concurrently."""
            try:
                hp = self._make_hp(f"race-offer-{idx}", f"offer-agent-{idx}")
                barrier.wait()
                offer_results[idx] = hp.offer_help(request_ids[idx])
            except Exception as e:
                errors.append(("offerer", e))

        threads = []
        # 5 threads offering help
        for i in range(5):
            threads.append(threading.Thread(target=offer_on_existing, args=(i,)))
        # 5 threads creating new requests
        for i in range(5):
            threads.append(threading.Thread(target=create_more_requests, args=(i * 5,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        # Each of the 5 distinct requests should have exactly one winner
        for i, result in enumerate(offer_results):
            self.assertTrue(result, f"Offer {i} on distinct request should succeed")


class TestRaceStatusAndAutoAssign(StressTestBase):
    """Update status while auto-assign is running."""

    def test_status_update_during_auto_assign(self):
        # Setup
        hp_req = self._make_hp("race-need", "agent-need")
        hp_req.update_status("needs_help", 30.0, "Stuck")
        wid = hp_req.add_work_item("Race auto task")
        hp_req.request_help(wid, "Help during auto-assign race")

        for i in range(3):
            hp = self._make_hp(f"race-idle-{i}", f"idle-agent-{i}")
            hp.register_capabilities(["coding"])
            hp.update_status("idle", 100.0, "Free")

        errors = []
        barrier = threading.Barrier(6, timeout=15)

        def do_auto_assign(idx):
            try:
                hp = self._make_hp(f"assigner-race-{idx}", f"assign-race-{idx}")
                barrier.wait()
                hp.auto_assign_idle_teams()
            except Exception as e:
                errors.append(("assigner", e))

        def do_status_update(idx):
            try:
                hp = self._make_hp(f"race-idle-{idx}", f"idle-agent-{idx}")
                barrier.wait()
                # Flip from idle to working while auto-assign might be running
                hp.update_status("working", 50.0, "Now busy")
            except Exception as e:
                errors.append(("updater", e))

        threads = []
        for i in range(3):
            threads.append(threading.Thread(target=do_auto_assign, args=(i,)))
        for i in range(3):
            threads.append(threading.Thread(target=do_status_update, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")


class TestRaceCompleteWhileOffering(StressTestBase):
    """Complete work item while another team is offering help on it."""

    def test_complete_during_offer(self):
        hp_a = self._make_hp("complete-race-a", "agent-a")
        wid = hp_a.add_work_item("Complete race task")
        rid = hp_a.request_help(wid, "Will be completed during offer")

        errors = []
        offer_result = [None]
        barrier = threading.Barrier(2, timeout=15)

        def complete_it():
            try:
                barrier.wait()
                hp_a.complete_work_item(wid)
            except Exception as e:
                errors.append(("completer", e))

        def offer_it():
            try:
                hp_b = self._make_hp("complete-race-b", "agent-b")
                barrier.wait()
                offer_result[0] = hp_b.offer_help(rid)
            except Exception as e:
                errors.append(("offerer", e))

        t1 = threading.Thread(target=complete_it)
        t2 = threading.Thread(target=offer_it)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        # offer_result can be True or False; neither should crash


# ===================================================================
# 4. ERROR HANDLING TESTS
# ===================================================================

class TestOperationsAfterClose(StressTestBase):
    """Operations after close() called on connection."""

    def test_add_work_item_after_close(self):
        hp = self._make_hp("closed-team", "closed-agent")
        hp.close()
        with self.assertRaises(Exception):
            hp.add_work_item("Should fail")

    def test_update_status_after_close(self):
        hp = self._make_hp("closed-team2", "closed-agent2")
        hp.close()
        with self.assertRaises(Exception):
            hp.update_status("idle", 0.0, "nope")

    def test_register_capabilities_after_close(self):
        hp = self._make_hp("closed-team3", "closed-agent3")
        hp.close()
        with self.assertRaises(Exception):
            hp.register_capabilities(["coding"])

    def test_offer_help_after_close(self):
        hp = self._make_hp("closed-team4", "closed-agent4")
        hp.close()
        with self.assertRaises(Exception):
            hp.offer_help(1)


class TestInvalidStatusValues(StressTestBase):
    """Invalid status values -- the module doesn't validate, so they store."""

    def test_nonsense_status_stored(self):
        hp = self._make_hp("bad-status-team", "agent-bs")
        # Module doesn't validate status; verify it doesn't crash
        hp.update_status("TOTALLY_INVALID_STATUS", 50.0, "whatever")
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT status FROM team_status WHERE team = 'bad-status-team'"
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "TOTALLY_INVALID_STATUS")


class TestVeryLongStrings(StressTestBase):
    """Very long strings (10KB+ descriptions)."""

    def test_10kb_description(self):
        hp = self._make_hp("long-str-team", "agent-ls")
        long_desc = "A" * 10240  # 10 KB
        wid = hp.add_work_item("Long desc item", description=long_desc)
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT description FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()
        self.assertEqual(len(row["description"]), 10240)

    def test_100kb_description(self):
        hp = self._make_hp("very-long-team", "agent-vl")
        long_desc = "B" * 102400  # 100 KB
        wid = hp.add_work_item("Very long desc", description=long_desc)
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT description FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()
        self.assertEqual(len(row["description"]), 102400)

    def test_long_help_request_description(self):
        hp = self._make_hp("long-help-team", "agent-lh")
        wid = hp.add_work_item("Task for long help")
        long_desc = "C" * 10240
        rid = hp.request_help(wid, long_desc)
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT description FROM help_requests WHERE id = ?", (rid,)
        ).fetchone()
        conn.close()
        self.assertEqual(len(row["description"]), 10240)


class TestUnicodeAndSpecialChars(StressTestBase):
    """Unicode and special characters in team names."""

    def test_unicode_team_name(self):
        hp = self._make_hp("team-\u00e9\u00e0\u00fc\u00f1", "agent-unicode")
        hp.update_status("idle", 100.0, "Standing by")
        idle = hp.get_idle_teams()
        names = {t["team"] for t in idle}
        self.assertIn("team-\u00e9\u00e0\u00fc\u00f1", names)

    def test_emoji_team_name(self):
        hp = self._make_hp("team-\U0001f680\U0001f525", "agent-emoji")
        hp.register_capabilities(["launch"])
        hp.update_status("idle", 100.0, "Ready")
        idle = hp.get_idle_teams()
        names = {t["team"] for t in idle}
        self.assertIn("team-\U0001f680\U0001f525", names)

    def test_cjk_team_name(self):
        hp = self._make_hp("\u56e2\u961f-\u4e00", "\u4ee3\u7406-1")
        wid = hp.add_work_item("\u4efb\u52a1\u4e00", description="\u63cf\u8ff0")
        self.assertIsInstance(wid, int)

    def test_special_chars_in_description(self):
        hp = self._make_hp("special-team", "agent-sp")
        special = "Line1\nLine2\tTabbed\r\nCRLF\x00NULL'single\"double\\back"
        wid = hp.add_work_item("Special chars", description=special)
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT description FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()
        # SQLite strips null bytes, so just check it doesn't crash
        self.assertIsNotNone(row["description"])

    def test_sql_injection_attempt(self):
        hp = self._make_hp("'; DROP TABLE work_items; --", "agent-inject")
        wid = hp.add_work_item("Robert'); DROP TABLE work_items;--")
        # If we get here, parameterized queries protected us
        conn = self._raw_conn()
        count = conn.execute("SELECT COUNT(*) as c FROM work_items").fetchone()["c"]
        conn.close()
        self.assertGreaterEqual(count, 1)


class TestNullAndEmptyCapabilities(StressTestBase):
    """Null/empty capability lists."""

    def test_empty_capability_list(self):
        hp = self._make_hp("empty-cap-team", "agent-ec")
        hp.register_capabilities([])
        conn = self._raw_conn()
        count = conn.execute(
            "SELECT COUNT(*) as c FROM team_capabilities WHERE team = 'empty-cap-team'"
        ).fetchone()["c"]
        conn.close()
        self.assertEqual(count, 0)

    def test_none_required_caps_work_item(self):
        hp = self._make_hp("none-cap-team", "agent-nc")
        wid = hp.add_work_item("No caps", required_caps=None)
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT required_capabilities FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()
        self.assertIsNone(row["required_capabilities"])

    def test_empty_required_caps_work_item(self):
        hp = self._make_hp("empty-req-team", "agent-er")
        wid = hp.add_work_item("Empty caps", required_caps=[])
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT required_capabilities FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()
        # Empty list is falsy in Python, so add_work_item stores None
        self.assertIsNone(row["required_capabilities"])


class TestNegativeProgress(StressTestBase):
    """Negative progress percentages."""

    def test_negative_progress(self):
        hp = self._make_hp("neg-progress", "agent-np")
        # Module doesn't validate; check it stores
        hp.update_status("working", -50.0, "Going backwards")
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT progress_pct FROM team_status WHERE team = 'neg-progress'"
        ).fetchone()
        conn.close()
        self.assertAlmostEqual(row["progress_pct"], -50.0)

    def test_over_100_progress(self):
        hp = self._make_hp("over-progress", "agent-op")
        hp.update_status("working", 999.99, "Overachiever")
        conn = self._raw_conn()
        row = conn.execute(
            "SELECT progress_pct FROM team_status WHERE team = 'over-progress'"
        ).fetchone()
        conn.close()
        self.assertAlmostEqual(row["progress_pct"], 999.99)


# ===================================================================
# 5. RESOURCE EXHAUSTION TESTS
# ===================================================================

class TestRapidOpenClose(StressTestBase):
    """Rapid open/close of connections."""

    def test_100_open_close_cycles(self):
        for i in range(100):
            hp = HelpProtocol(self.db_path, self.bus_dir, f"cycle-{i}", "agent-cycle")
            hp.update_status("idle", 0.0, "ping")
            hp.close()

        # Verify DB is still functional
        hp = self._make_hp("post-cycle", "agent-pc")
        hp.update_status("idle", 100.0, "Still works")
        idle = hp.get_idle_teams()
        post_cycle = [t for t in idle if t["team"] == "post-cycle"]
        self.assertEqual(len(post_cycle), 1)


class TestManySimultaneousConnections(StressTestBase):
    """Many simultaneous database connections (20+)."""

    def test_25_simultaneous_connections(self):
        instances = []
        for i in range(25):
            hp = self._make_hp(f"sim-team-{i}", f"sim-agent-{i}")
            instances.append(hp)

        errors = []
        barrier = threading.Barrier(25, timeout=15)

        def do_work(idx, hp):
            try:
                barrier.wait()
                hp.register_capabilities([f"cap-{idx}"])
                hp.update_status("working", float(idx), f"Task {idx}")
                hp.add_work_item(f"Item {idx}")
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=do_work, args=(i, instances[i]))
                   for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        conn = self._raw_conn()
        work_count = conn.execute("SELECT COUNT(*) as c FROM work_items").fetchone()["c"]
        status_count = conn.execute("SELECT COUNT(*) as c FROM team_status").fetchone()["c"]
        conn.close()
        self.assertEqual(work_count, 25)
        self.assertEqual(status_count, 25)


class TestBusDirectoryWithManyFiles(StressTestBase):
    """Bus directory with many channel files."""

    def test_many_bus_channels(self):
        hp = self._make_hp("bus-team", "bus-agent")
        # Publish to many different channels via status updates with different teams
        for i in range(50):
            hp_i = self._make_hp(f"bus-team-{i}", f"bus-agent-{i}")
            hp_i.update_status("working", float(i), f"Channel test {i}")

        # Verify bus file was created and has entries
        bus_file = os.path.join(self.bus_dir, "global.jsonl")
        self.assertTrue(os.path.exists(bus_file))
        with open(bus_file) as f:
            lines = f.readlines()
        self.assertGreaterEqual(len(lines), 50)


# ===================================================================
# 6. ZOMBIE DETECTION TESTS
# ===================================================================

class TestZombieWorkItems(StressTestBase):
    """Team claims work but never completes it (stale in_progress items)."""

    def test_detect_stale_in_progress_work(self):
        hp_a = self._make_hp("zombie-req", "agent-zr")
        wid = hp_a.add_work_item("Zombie task")
        rid = hp_a.request_help(wid, "Someone take this")

        hp_b = self._make_hp("zombie-helper", "agent-zh")
        hp_b.offer_help(rid)
        # Now the work item is in_progress but zombie-helper never completes it

        conn = self._raw_conn()
        row = conn.execute(
            "SELECT status, assigned_to, updated_at FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        conn.close()

        self.assertEqual(row["status"], "in_progress")
        self.assertEqual(row["assigned_to"], "agent-zh")
        # In a real system, you'd check updated_at against a threshold
        self.assertIsNotNone(row["updated_at"])

        # Simulate zombie detection: find items in_progress older than threshold
        conn = self._raw_conn()
        threshold = time.time() + 1  # everything is "stale" from the future
        stale = conn.execute(
            "SELECT id, team, assigned_to FROM work_items WHERE status = 'in_progress' AND updated_at < ?",
            (threshold,),
        ).fetchall()
        conn.close()
        stale_ids = [r["id"] for r in stale]
        self.assertIn(wid, stale_ids)


class TestZombieHelpRequests(StressTestBase):
    """Help request accepted but never fulfilled."""

    def test_detect_accepted_unfulfilled(self):
        hp_a = self._make_hp("zombie-req2", "agent-zr2")
        wid = hp_a.add_work_item("Unfulfilled task")
        rid = hp_a.request_help(wid, "Will be accepted but not fulfilled")

        hp_b = self._make_hp("zombie-acceptor", "agent-za")
        hp_b.offer_help(rid)
        # Never calls fulfill_help()

        conn = self._raw_conn()
        row = conn.execute(
            "SELECT status, accepted_by_team, resolved_at FROM help_requests WHERE id = ?",
            (rid,),
        ).fetchone()
        conn.close()

        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["accepted_by_team"], "zombie-acceptor")
        self.assertIsNone(row["resolved_at"])

        # Detect zombie: accepted requests with no resolved_at
        conn = self._raw_conn()
        zombies = conn.execute(
            "SELECT id, requesting_team, accepted_by_team FROM help_requests "
            "WHERE status = 'accepted' AND resolved_at IS NULL"
        ).fetchall()
        conn.close()
        zombie_ids = [r["id"] for r in zombies]
        self.assertIn(rid, zombie_ids)


class TestStaleTeamStatus(StressTestBase):
    """Stale team_status entries."""

    def test_detect_stale_team_status(self):
        hp = self._make_hp("stale-team", "agent-st")
        hp.update_status("working", 50.0, "Was working")

        # Manually set last_updated to a very old time to simulate staleness
        conn = self._raw_conn()
        old_time = time.time() - 7200  # 2 hours ago
        conn.execute(
            "UPDATE team_status SET last_updated = ? WHERE team = 'stale-team'",
            (old_time,),
        )
        conn.commit()

        # Detect stale: teams with last_updated older than threshold
        threshold = time.time() - 3600  # 1 hour ago
        stale = conn.execute(
            "SELECT team, status, last_updated FROM team_status WHERE last_updated < ?",
            (threshold,),
        ).fetchall()
        conn.close()

        stale_teams = [r["team"] for r in stale]
        self.assertIn("stale-team", stale_teams)

    def test_multiple_stale_vs_fresh_teams(self):
        # Create 5 stale teams and 5 fresh teams
        for i in range(5):
            hp = self._make_hp(f"stale-{i}", f"agent-stale-{i}")
            hp.update_status("working", 50.0, f"Stale task {i}")

        conn = self._raw_conn()
        old_time = time.time() - 7200
        for i in range(5):
            conn.execute(
                "UPDATE team_status SET last_updated = ? WHERE team = ?",
                (old_time, f"stale-{i}"),
            )
        conn.commit()
        conn.close()

        for i in range(5):
            hp = self._make_hp(f"fresh-{i}", f"agent-fresh-{i}")
            hp.update_status("working", 50.0, f"Fresh task {i}")

        conn = self._raw_conn()
        threshold = time.time() - 3600
        stale = conn.execute(
            "SELECT team FROM team_status WHERE last_updated < ?",
            (threshold,),
        ).fetchall()
        fresh = conn.execute(
            "SELECT team FROM team_status WHERE last_updated >= ?",
            (threshold,),
        ).fetchall()
        conn.close()

        self.assertEqual(len(stale), 5)
        self.assertEqual(len(fresh), 5)


# ===================================================================
# ADDITIONAL EDGE CASES
# ===================================================================

class TestFulfillNonexistent(StressTestBase):
    """Fulfill a help request that doesn't exist."""

    def test_fulfill_nonexistent_request(self):
        hp = self._make_hp("fulfill-bad", "agent-fb")
        # Should not crash; just a no-op UPDATE
        hp.fulfill_help(99999)

    def test_complete_nonexistent_work_item(self):
        hp = self._make_hp("complete-bad", "agent-cb")
        # Should not crash
        hp.complete_work_item(99999)


class TestDoubleClose(StressTestBase):
    """Close a connection twice -- verify it does not crash the process."""

    def test_double_close_no_crash(self):
        hp = HelpProtocol(self.db_path, self.bus_dir, "double-close", "agent-dc")
        hp.close()
        # SQLite allows double-close without error; verify no crash
        try:
            hp.close()
        except Exception:
            pass  # Either outcome is acceptable
        # The process should still be alive and the DB usable
        hp2 = self._make_hp("post-double-close", "agent-pdc")
        hp2.update_status("idle", 100.0, "Still works after double close")
        idle = hp2.get_idle_teams()
        names = {t["team"] for t in idle}
        self.assertIn("post-double-close", names)


class TestConcurrentOffersMultipleRequests(StressTestBase):
    """Multiple requests with multiple teams offering concurrently."""

    def test_10_requests_20_offerers(self):
        # Create 10 help requests
        hp_requester = self._make_hp("multi-req", "agent-mr")
        request_ids = []
        for i in range(10):
            wid = hp_requester.add_work_item(f"Multi task {i}")
            rid = hp_requester.request_help(wid, f"Multi help {i}")
            request_ids.append(rid)

        num_offerers = 20
        results = {}
        errors = []
        lock = threading.Lock()
        barrier = threading.Barrier(num_offerers, timeout=15)

        def offer_all(idx):
            try:
                hp = self._make_hp(f"multi-offer-{idx}", f"multi-agent-{idx}")
                barrier.wait()
                for rid in request_ids:
                    won = hp.offer_help(rid)
                    if won:
                        with lock:
                            results.setdefault(rid, []).append(idx)
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=offer_all, args=(i,))
                   for i in range(num_offerers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(len(errors), 0, f"Errors: {errors}")

        # Each request should have at most 1 winner
        for rid, winners in results.items():
            self.assertEqual(len(winners), 1,
                             f"Request {rid} has {len(winners)} winners: {winners}")


if __name__ == "__main__":
    unittest.main()
