#!/usr/bin/env python3
"""MCP server exposing knowledge base search tools via FastMCP."""

import json
import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "storage" / "data" / "knowledge.db"

mcp = FastMCP("kb-search")


def _db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = ON")
    return db


@mcp.tool()
def kb_search(query: str, resource_type: str = None, limit: int = 10) -> list[dict]:
    """FTS5 full-text search with BM25 ranking across all knowledge base entries."""
    db = _db()
    sql = """SELECT e.id, e.title, e.resource_type, e.summary,
                    bm25(entries_fts, 10.0, 5.0, 1.0) AS score
             FROM entries_fts f JOIN entries e ON f.rowid = e.rowid
             WHERE entries_fts MATCH ?"""
    params: list = [query]
    if resource_type:
        sql += " AND e.resource_type = ?"
        params.append(resource_type.upper())
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        params[0] = '"' + query.replace('"', '""') + '"'
        rows = db.execute(sql, params).fetchall()
    result = [dict(r) for r in rows]
    db.close()
    return result


@mcp.tool()
def kb_get(entry_id: str) -> dict:
    """Get a specific knowledge base entry by ID, including tags and relationships."""
    db = _db()
    row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id.upper(),)).fetchone()
    if not row:
        db.close()
        return {"error": f"Entry '{entry_id}' not found"}
    result = dict(row)
    tags = db.execute("SELECT tag FROM tags WHERE entry_id = ?", (entry_id.upper(),)).fetchall()
    result["tags"] = [r["tag"] for r in tags]
    rels = db.execute("SELECT target_id, rel_type FROM relationships WHERE source_id = ?",
                      (entry_id.upper(),)).fetchall()
    result["builds_on"] = [r["target_id"] for r in rels if r["rel_type"] == "builds_on"]
    db.close()
    return result


@mcp.tool()
def kb_list(resource_type: str = "KB") -> list[dict]:
    """List all entries of a given resource type (KB, SOP, SRC, SCR, TEAM, TOOL)."""
    db = _db()
    rows = db.execute(
        "SELECT id, title, category, status, created_date FROM entries WHERE resource_type = ? ORDER BY numeric_id",
        (resource_type.upper(),)).fetchall()
    result = [dict(r) for r in rows]
    db.close()
    return result


@mcp.tool()
def kb_tags(resource_type: str = None) -> list[dict]:
    """Tag frequency cloud, optionally filtered by resource type."""
    db = _db()
    if resource_type:
        rows = db.execute(
            "SELECT t.tag, COUNT(*) AS usage_count FROM tags t JOIN entries e ON t.entry_id = e.id "
            "WHERE e.resource_type = ? GROUP BY t.tag ORDER BY usage_count DESC",
            (resource_type.upper(),)).fetchall()
    else:
        rows = db.execute("SELECT * FROM v_tag_cloud").fetchall()
    result = [dict(r) for r in rows]
    db.close()
    return result


@mcp.tool()
def kb_related(entry_id: str) -> dict:
    """Find entries related via builds_on chains and shared tags."""
    db, eid = _db(), entry_id.upper()
    row = db.execute("SELECT id, title FROM entries WHERE id = ?", (eid,)).fetchone()
    if not row:
        db.close()
        return {"error": f"Entry '{entry_id}' not found"}
    chain = db.execute("""
        WITH RECURSIVE chain AS (
            SELECT target_id, 1 AS depth, 'parent' AS direction FROM relationships
            WHERE source_id = ? AND rel_type = 'builds_on'
            UNION ALL
            SELECT r.target_id, c.depth + 1, 'ancestor' FROM relationships r
            JOIN chain c ON r.source_id = c.target_id
            WHERE r.rel_type = 'builds_on' AND c.depth < 10
        ), children AS (
            SELECT source_id AS related_id, 1 AS depth, 'child' AS direction FROM relationships
            WHERE target_id = ? AND rel_type = 'builds_on'
        )
        SELECT e.id, e.title, e.resource_type, c.depth, c.direction FROM chain c
        JOIN entries e ON c.target_id = e.id
        UNION ALL
        SELECT e.id, e.title, e.resource_type, ch.depth, ch.direction FROM children ch
        JOIN entries e ON ch.related_id = e.id ORDER BY direction, depth
    """, (eid, eid)).fetchall()
    shared = db.execute("""
        SELECT e2.id, e2.title, e2.resource_type, COUNT(*) AS shared_tags
        FROM tags t1 JOIN tags t2 ON t1.tag = t2.tag AND t1.entry_id != t2.entry_id
        JOIN entries e2 ON t2.entry_id = e2.id WHERE t1.entry_id = ?
        GROUP BY e2.id ORDER BY shared_tags DESC LIMIT 5
    """, (eid,)).fetchall()
    db.close()
    return {"entry": dict(row), "builds_on_chain": [dict(r) for r in chain],
            "shared_tags": [dict(r) for r in shared]}


if __name__ == "__main__":
    mcp.run()
