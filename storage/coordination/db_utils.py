"""Common database utilities for the Proto A coordination layer.

Provides reusable helpers for SQLite connection management, idempotent
schema creation, and atomic read-modify-write transactions.  These
utilities complement ``bus_core.init_db`` with higher-level patterns
observed across the coordination codebase.

All functions use WAL mode and appropriate busy timeouts by default.
Only uses the Python standard library.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default configuration matching bus_core conventions
DEFAULT_BUSY_TIMEOUT_MS: int = 5000


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def get_connection(
    db_path: str,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    row_factory: Any = sqlite3.Row,
) -> sqlite3.Connection:
    """Return a configured SQLite connection with WAL mode and busy timeout.

    This is a convenience wrapper that ensures consistent connection setup
    across the coordination layer.  It creates parent directories as needed,
    enables WAL journal mode for concurrent access, and sets both the
    Python-level connect timeout and SQLite's internal busy handler.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database file.
    busy_timeout_ms:
        SQLite busy timeout in milliseconds.  Controls how long SQLite
        waits internally before returning SQLITE_BUSY.
    row_factory:
        Row factory for the connection.  Defaults to ``sqlite3.Row``
        for dict-like column access.

    Returns
    -------
    sqlite3.Connection
        A fully configured connection ready for use.

    Notes
    -----
    The caller is responsible for closing the returned connection.
    ``check_same_thread=False`` is set so the connection can be shared
    across threads, but callers must provide their own locking (e.g.
    ``threading.Lock``) to serialize access.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        timeout=busy_timeout_ms / 1000,
        check_same_thread=False,
    )
    conn.row_factory = row_factory
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Idempotently apply a SQL schema to the given connection.

    The *schema_sql* string should use ``CREATE TABLE IF NOT EXISTS``,
    ``CREATE INDEX IF NOT EXISTS``, etc., so that calling this function
    multiple times is safe and produces no errors.

    The schema is applied via ``executescript`` (which implicitly commits
    any pending transaction first), followed by an explicit ``commit``.

    Parameters
    ----------
    conn:
        An open SQLite connection.
    schema_sql:
        SQL DDL statements to execute.  Should be idempotent (use
        ``IF NOT EXISTS`` clauses).

    Raises
    ------
    sqlite3.OperationalError
        If the schema SQL is malformed or the database is locked beyond
        the configured busy_timeout.

    Examples
    --------
    >>> conn = get_connection("/tmp/test.db")
    >>> ensure_schema(conn, '''
    ...     CREATE TABLE IF NOT EXISTS items (
    ...         id INTEGER PRIMARY KEY,
    ...         name TEXT NOT NULL
    ...     );
    ... ''')
    """
    conn.executescript(schema_sql)
    conn.commit()
    logger.debug("Schema applied successfully")


# ---------------------------------------------------------------------------
# Atomic read-modify-write
# ---------------------------------------------------------------------------

def atomic_update(
    conn: sqlite3.Connection,
    select_sql: str,
    update_sql: str,
    params: tuple | dict = (),
    *,
    select_params: tuple | dict | None = None,
    update_params: tuple | dict | None = None,
) -> Optional[sqlite3.Row]:
    """Perform an atomic read-modify-write using BEGIN IMMEDIATE.

    This wraps a common pattern in the coordination layer: read a row
    (SELECT), then conditionally update it (UPDATE), all within a single
    IMMEDIATE transaction to prevent races with concurrent writers.

    The transaction is committed on success and rolled back on any error.

    Parameters
    ----------
    conn:
        An open SQLite connection.  Must NOT already be inside a
        transaction (autocommit or committed state).
    select_sql:
        A SELECT statement to read the current state.  Should return
        at most one row (only the first row is used).
    update_sql:
        An UPDATE/INSERT statement to apply the modification.
    params:
        Shared parameter tuple/dict used for both the SELECT and UPDATE
        if *select_params* and *update_params* are not provided.
    select_params:
        Parameters specifically for the SELECT statement.  Overrides
        *params* for the SELECT.
    update_params:
        Parameters specifically for the UPDATE statement.  Overrides
        *params* for the UPDATE.

    Returns
    -------
    sqlite3.Row or None
        The row returned by the SELECT, or None if no row was found.
        The UPDATE is still executed even if no row is found (the caller
        can check the return value to decide if the update was meaningful).

    Raises
    ------
    sqlite3.OperationalError
        If BEGIN IMMEDIATE fails (e.g. SQLITE_BUSY after timeout) or
        if the SQL statements are malformed.

    Examples
    --------
    >>> row = atomic_update(
    ...     conn,
    ...     "SELECT status FROM tasks WHERE id = ?",
    ...     "UPDATE tasks SET status = 'done' WHERE id = ?",
    ...     params=(42,),
    ... )
    """
    sel_p = select_params if select_params is not None else params
    upd_p = update_params if update_params is not None else params

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(select_sql, sel_p).fetchone()
        conn.execute(update_sql, upd_p)
        conn.execute("COMMIT")
        return row
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


# ---------------------------------------------------------------------------
# Safe close helper
# ---------------------------------------------------------------------------

def safe_close(conn: Optional[sqlite3.Connection]) -> None:
    """Close a SQLite connection, ignoring errors if already closed.

    A convenience helper to avoid try/except boilerplate when cleaning up
    connections that may already be closed or None.

    Parameters
    ----------
    conn:
        The connection to close, or None (in which case this is a no-op).
    """
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
