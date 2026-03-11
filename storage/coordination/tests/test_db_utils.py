"""Tests for db_utils.py -- shared SQLite database utilities.

These tests validate the interface of db_utils.py, the shared database
utility module extracted from duplicated patterns across coordinator_hub.py,
help_protocol.py, direct_channels.py, work_stealing.py, bus_cli.py, and
multi_team_runner.py.

Team 5 (efficiency/refactoring) owns db_utils.py.  These tests were written
by Assist Team C based on the efficiency reports and the consolidation plan
(S-02: "Unify SQLite schema and db_open").

db_utils.py public interface:
    - get_connection(db_path, *, busy_timeout_ms=5000, row_factory=Row) -> Connection
    - ensure_schema(conn, schema_sql: str) -> None
    - atomic_update(conn, select_sql, update_sql, params=(), *,
                    select_params=None, update_params=None) -> Row|None
    - safe_close(conn) -> None
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    from storage.coordination.db_utils import (
        get_connection,
        ensure_schema,
        atomic_update,
        safe_close,
        DEFAULT_BUSY_TIMEOUT_MS,
    )
    _DB_UTILS_AVAILABLE = True
except ImportError:
    _DB_UTILS_AVAILABLE = False


@unittest.skipUnless(_DB_UTILS_AVAILABLE, "db_utils.py not yet created by Team 5")
class TestGetConnection(unittest.TestCase):
    """Tests for get_connection() -- SQLite connection factory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dbutils_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_sqlite_connection(self):
        """get_connection should return a sqlite3.Connection object."""
        conn = get_connection(self.db_path)
        try:
            self.assertIsInstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    def test_wal_mode(self):
        """Connection should use WAL journal mode for concurrency."""
        conn = get_connection(self.db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
        finally:
            conn.close()

    def test_busy_timeout_default(self):
        """Default busy_timeout should be 5000ms (matching bus_core convention)."""
        conn = get_connection(self.db_path)
        try:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(timeout, DEFAULT_BUSY_TIMEOUT_MS)
        finally:
            conn.close()

    def test_busy_timeout_custom(self):
        """Custom busy_timeout should be applied."""
        conn = get_connection(self.db_path, busy_timeout_ms=5000)
        try:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(timeout, 5000)
        finally:
            conn.close()

    def test_row_factory_is_row(self):
        """Connection should have row_factory set to sqlite3.Row."""
        conn = get_connection(self.db_path)
        try:
            self.assertEqual(conn.row_factory, sqlite3.Row)
        finally:
            conn.close()

    def test_creates_parent_directories(self):
        """get_connection should create missing parent directories."""
        deep_path = os.path.join(self.tmpdir, "deep", "nested", "dir", "db.sqlite")
        conn = get_connection(deep_path)
        try:
            self.assertTrue(os.path.isfile(deep_path))
        finally:
            conn.close()

    def test_check_same_thread_false(self):
        """Connection should be usable from multiple threads."""
        conn = get_connection(self.db_path)
        try:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.commit()

            results = []

            def worker():
                try:
                    conn.execute("INSERT INTO test (id) VALUES (1)")
                    conn.commit()
                    results.append("ok")
                except Exception as e:
                    results.append(f"error: {e}")

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=5)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0], "ok")
        finally:
            conn.close()


@unittest.skipUnless(_DB_UTILS_AVAILABLE, "db_utils.py not yet created by Team 5")
class TestEnsureSchema(unittest.TestCase):
    """Tests for ensure_schema() -- idempotent schema application."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dbutils_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.conn = get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_tables(self):
        """ensure_schema should create tables from a CREATE TABLE IF NOT EXISTS statement."""
        schema = "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT);"
        ensure_schema(self.conn, schema)
        # Verify table exists by inserting
        self.conn.execute("INSERT INTO items (name) VALUES ('test')")
        self.conn.commit()
        row = self.conn.execute("SELECT name FROM items").fetchone()
        self.assertEqual(row["name"], "test")

    def test_idempotent(self):
        """Calling ensure_schema multiple times should not raise or duplicate tables."""
        schema = """
        CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS t2 (id INTEGER PRIMARY KEY);
        """
        ensure_schema(self.conn, schema)
        ensure_schema(self.conn, schema)  # second call should be harmless
        ensure_schema(self.conn, schema)  # third call too

        # Tables should still work
        self.conn.execute("INSERT INTO t1 (id) VALUES (1)")
        self.conn.execute("INSERT INTO t2 (id) VALUES (1)")
        self.conn.commit()

    def test_multiple_tables(self):
        """ensure_schema should handle multiple CREATE TABLE statements."""
        schema = """
        CREATE TABLE IF NOT EXISTS alpha (id INTEGER PRIMARY KEY, val TEXT);
        CREATE TABLE IF NOT EXISTS beta (id INTEGER PRIMARY KEY, num REAL);
        CREATE INDEX IF NOT EXISTS idx_beta_num ON beta(num);
        """
        ensure_schema(self.conn, schema)

        # Both tables should exist
        tables = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('alpha', 'beta')"
        ).fetchall()}
        self.assertEqual(tables, {"alpha", "beta"})

    def test_preserves_existing_data(self):
        """ensure_schema on an existing table should not drop or modify data."""
        schema = "CREATE TABLE IF NOT EXISTS data (id INTEGER PRIMARY KEY, value TEXT);"
        ensure_schema(self.conn, schema)
        self.conn.execute("INSERT INTO data (value) VALUES ('preserved')")
        self.conn.commit()

        # Re-apply schema
        ensure_schema(self.conn, schema)

        row = self.conn.execute("SELECT value FROM data").fetchone()
        self.assertEqual(row["value"], "preserved")


@unittest.skipUnless(_DB_UTILS_AVAILABLE, "db_utils.py not yet created by Team 5")
class TestAtomicUpdate(unittest.TestCase):
    """Tests for atomic_update() -- BEGIN IMMEDIATE-based atomic writes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dbutils_test_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.conn = get_connection(self.db_path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, val TEXT)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_insert(self):
        """atomic_update should execute a SELECT + INSERT within BEGIN IMMEDIATE."""
        # Use atomic_update's read-modify-write pattern:
        # SELECT first (returns None since key doesn't exist), then INSERT
        row = atomic_update(
            self.conn,
            "SELECT val FROM kv WHERE key = ?",
            "INSERT INTO kv (key, val) VALUES (?, ?)",
            select_params=("k1",),
            update_params=("k1", "v1"),
        )
        self.assertIsNone(row)  # no row existed before
        result = self.conn.execute("SELECT val FROM kv WHERE key = 'k1'").fetchone()
        self.assertEqual(result["val"], "v1")

    def test_basic_update(self):
        """atomic_update should execute a SELECT + UPDATE atomically."""
        self.conn.execute("INSERT INTO kv (key, val) VALUES ('k1', 'old')")
        self.conn.commit()
        row = atomic_update(
            self.conn,
            "SELECT val FROM kv WHERE key = ?",
            "UPDATE kv SET val = ? WHERE key = ?",
            select_params=("k1",),
            update_params=("new", "k1"),
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["val"], "old")  # returns the SELECT result (old value)
        result = self.conn.execute("SELECT val FROM kv WHERE key = 'k1'").fetchone()
        self.assertEqual(result["val"], "new")

    def test_returns_select_row(self):
        """atomic_update should return the row from the SELECT statement."""
        self.conn.execute("INSERT INTO kv (key, val) VALUES ('k1', 'hello')")
        self.conn.commit()
        row = atomic_update(
            self.conn,
            "SELECT val FROM kv WHERE key = ?",
            "UPDATE kv SET val = 'updated' WHERE key = ?",
            params=("k1",),
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["val"], "hello")

    def test_atomicity_on_error(self):
        """If the UPDATE fails, the transaction should be rolled back."""
        self.conn.execute("INSERT INTO kv (key, val) VALUES ('existing', 'safe')")
        self.conn.commit()

        # This should fail because the UPDATE tries to insert a duplicate key
        try:
            atomic_update(
                self.conn,
                "SELECT val FROM kv WHERE key = ?",
                "INSERT INTO kv (key, val) VALUES (?, ?)",
                select_params=("existing",),
                update_params=("existing", "conflict"),
            )
        except sqlite3.IntegrityError:
            pass  # expected

        # Original value should be preserved
        row = self.conn.execute("SELECT val FROM kv WHERE key = 'existing'").fetchone()
        self.assertEqual(row["val"], "safe")

    def test_concurrent_access_simulation(self):
        """Simulate two threads competing for the same row update.

        Both threads try to read-modify-write the same key using
        atomic_update's BEGIN IMMEDIATE to serialize access.
        """
        # Set initial value
        self.conn.execute("INSERT INTO kv (key, val) VALUES ('counter', '0')")
        self.conn.commit()

        errors = []
        results = []

        def increment(conn_path, thread_id):
            """Open own connection and atomically increment counter."""
            try:
                c = get_connection(conn_path)
                try:
                    for _ in range(5):
                        # Read current value, then update it
                        row = atomic_update(
                            c,
                            "SELECT val FROM kv WHERE key = 'counter'",
                            "UPDATE kv SET val = CAST(CAST(val AS INTEGER) + 1 AS TEXT) WHERE key = 'counter'",
                        )
                    results.append(thread_id)
                finally:
                    c.close()
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        t1 = threading.Thread(target=increment, args=(self.db_path, 1))
        t2 = threading.Thread(target=increment, args=(self.db_path, 2))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both threads should have completed without errors
        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 2)

        # Final counter value should be 10 (5 increments x 2 threads)
        # With BEGIN IMMEDIATE the updates are serialized
        final = self.conn.execute("SELECT val FROM kv WHERE key = 'counter'").fetchone()
        final_val = int(final["val"])
        self.assertEqual(final_val, 10,
                         "Counter should be 10 (5 increments x 2 threads with serialized access)")


if __name__ == "__main__":
    unittest.main()
