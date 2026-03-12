#!/usr/bin/env python3
"""
repo_mcp_server.py — MCP server for the Claude Agent Repository.

Reads .pointer.json files and REGISTRY.json to serve repository knowledge
via MCP tools, resources, and prompts over stdio transport.

Usage:
    python server.py

Requires: mcp>=1.0.0, pyyaml>=6.0
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

# Repo root: three levels up from storage/mcp-server/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

_pointer_cache: dict[str, dict] = {}
_registry_cache: dict | None = None
_entries_by_id: dict[str, dict] = {}
_all_entries: list[dict] = []


def _load_pointer_files() -> list[dict]:
    """Load all .pointer.json files from the repo."""
    pointer_files: list[dict] = []
    for pf in sorted(REPO_ROOT.rglob(".pointer.json")):
        try:
            with open(pf) as f:
                data = json.load(f)
            rel = str(pf.relative_to(REPO_ROOT))
            _pointer_cache[rel] = data
            pointer_files.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARN: Failed to load {pf}: {e}", file=sys.stderr)
    return pointer_files


def _load_registry() -> dict | None:
    """Load REGISTRY.json if it exists."""
    global _registry_cache
    reg_path = REPO_ROOT / "REGISTRY.json"
    if reg_path.exists():
        try:
            with open(reg_path) as f:
                _registry_cache = json.load(f)
            return _registry_cache
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _build_index():
    """Build in-memory index from pointer files (or fall back to source parsing)."""
    global _all_entries, _entries_by_id

    pointers = _load_pointer_files()
    _load_registry()

    if pointers:
        for pdata in pointers:
            for entry in pdata.get("entries", []):
                _all_entries.append(entry)
                _entries_by_id[entry["id"]] = entry
    else:
        # Fallback: parse KB entries directly
        _fallback_parse_kb()

    _all_entries.sort(key=lambda e: e.get("id", ""))


def _fallback_parse_kb():
    """Parse knowledge-base entries directly if no pointer files exist."""
    try:
        import yaml
    except ImportError:
        print("WARN: pyyaml not installed, cannot parse KB entries", file=sys.stderr)
        return

    kb_dir = REPO_ROOT / "knowledge-base" / "entries"
    if not kb_dir.is_dir():
        return

    for md_file in sorted(kb_dir.glob("KB-*.md")):
        try:
            content = md_file.read_text()
            m = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
            if not m:
                continue
            fm = yaml.safe_load(m.group(1))
            body = m.group(2).strip()
            title = fm.get("title", "")
            if not title:
                title_match = re.match(r"#\s+(.*)", body)
                title = title_match.group(1) if title_match else md_file.name

            entry = {
                "id": fm["id"],
                "title": title,
                "path": str(md_file.relative_to(REPO_ROOT)),
                "date": str(fm.get("date", "")),
                "team": fm.get("team", "unknown"),
                "category": fm.get("category", ""),
                "tags": fm.get("tags", []),
                "status": fm.get("status", "unknown"),
                "builds_on": fm.get("builds_on", []) or [],
            }
            _all_entries.append(entry)
            _entries_by_id[entry["id"]] = entry
        except Exception as e:
            print(f"WARN: Failed to parse {md_file}: {e}", file=sys.stderr)


def _read_file_content(rel_path: str) -> str | None:
    """Read file content from a relative path."""
    full = REPO_ROOT / rel_path
    if full.exists():
        return full.read_text()
    return None


# ---------------------------------------------------------------------------
# Conventions data
# ---------------------------------------------------------------------------

CONVENTIONS = {
    "kb_entry": {
        "description": "Knowledge Base entry format",
        "template_path": "knowledge-base/TEMPLATE.md",
        "naming": "KB-XXXX.md (zero-padded 4 digits)",
        "location": "knowledge-base/entries/",
        "frontmatter_fields": ["id", "date", "team", "role", "category", "tags", "status", "confidence", "builds_on"],
        "categories": ["methodology", "tool-usage", "debugging", "optimization", "integration", "market-research", "testing"],
    },
    "team_log": {
        "description": "Team session log format",
        "template_path": "teams/TEAM_LOG_TEMPLATE.md",
        "naming": "TEAM-XXXX.md (zero-padded 4 digits)",
        "location": "teams/",
    },
    "source": {
        "description": "Data source definition",
        "template_path": "storage/sources/TEMPLATE.json",
        "naming": "SRC-XXXX-name.json",
        "location": "storage/sources/",
    },
    "script": {
        "description": "Reusable script",
        "location": "storage/scripts/",
        "index": "storage/scripts/index.json",
    },
}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def run_server():
    """Start the MCP server with stdio transport."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "ERROR: The 'mcp' package is not installed.\n"
            "Install it with: pip install 'mcp>=1.0.0' 'pyyaml>=6.0'\n"
            "Or:              pip install -r storage/mcp-server/requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build index before starting server
    _build_index()

    mcp = FastMCP(
        "Claude Agent Repository",
        version="0.1.0",
    )

    # --- Tools ---

    @mcp.tool()
    def search(
        query: str,
        type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> str:
        """Search repository entries by text query, type prefix, or tags.

        Args:
            query: Text to search in titles and IDs (case-insensitive)
            type: Filter by ID prefix (e.g. "KB", "SRC", "TEAM")
            tags: Filter entries that have ALL of these tags
            limit: Max results to return (default 10)
        """
        results: list[dict] = []
        query_lower = query.lower()

        for entry in _all_entries:
            # Type filter
            if type and not entry.get("id", "").startswith(type):
                continue

            # Tag filter (AND logic)
            if tags:
                entry_tags = set(entry.get("tags", []))
                if not all(t in entry_tags for t in tags):
                    continue

            # Text match on title, id, category, tags
            searchable = " ".join([
                entry.get("id", ""),
                entry.get("title", ""),
                entry.get("category", ""),
                " ".join(entry.get("tags", [])),
            ]).lower()

            if query_lower and query_lower not in searchable:
                continue

            results.append(entry)
            if len(results) >= limit:
                break

        if not results:
            return json.dumps({"matches": 0, "results": []}, indent=2)

        return json.dumps({"matches": len(results), "results": results}, indent=2)

    @mcp.tool()
    def get(id: str) -> str:
        """Get a specific entry by ID (e.g. KB-0001). Returns metadata and file content.

        Args:
            id: The entry ID (e.g. "KB-0001")
        """
        entry = _entries_by_id.get(id)
        if not entry:
            return json.dumps({"error": f"Entry '{id}' not found"})

        result = dict(entry)
        # Include file content if path exists
        path = entry.get("path", "")
        if path:
            content = _read_file_content(path)
            if content is not None:
                result["content"] = content

        return json.dumps(result, indent=2)

    @mcp.tool()
    def status() -> str:
        """Get repository status: entry counts, pointer files, last update."""
        pointer_count = len(_pointer_cache)
        entry_count = len(_all_entries)

        # Count by prefix
        by_prefix: dict[str, int] = {}
        for entry in _all_entries:
            match = re.match(r"^([A-Z]+)-", entry.get("id", ""))
            if match:
                p = match.group(1)
                by_prefix[p] = by_prefix.get(p, 0) + 1

        # Count by category
        by_category: dict[str, int] = {}
        for entry in _all_entries:
            cat = entry.get("category", "uncategorized")
            by_category[cat] = by_category.get(cat, 0) + 1

        # Registry info
        reg_info = None
        if _registry_cache:
            reg_info = {
                "generated": _registry_cache.get("generated"),
                "total_entries": _registry_cache.get("total_entries"),
                "unique_tags": len(_registry_cache.get("global_tag_index", {})),
            }

        return json.dumps({
            "pointer_files": pointer_count,
            "total_entries": entry_count,
            "entries_by_prefix": dict(sorted(by_prefix.items())),
            "entries_by_category": dict(sorted(by_category.items())),
            "registry": reg_info,
            "repo_root": str(REPO_ROOT),
        }, indent=2)

    @mcp.tool()
    def conventions(type: str) -> str:
        """Get repository conventions for a specific type of content.

        Args:
            type: Content type — one of: kb_entry, team_log, source, script
        """
        conv = CONVENTIONS.get(type)
        if not conv:
            return json.dumps({
                "error": f"Unknown type '{type}'",
                "available_types": list(CONVENTIONS.keys()),
            })

        result = dict(conv)
        # Include template content if available
        template_path = conv.get("template_path")
        if template_path:
            content = _read_file_content(template_path)
            if content:
                result["template_content"] = content

        return json.dumps(result, indent=2)

    # --- Resources ---

    @mcp.resource("repo://kb/{id}")
    def kb_resource(id: str) -> str:
        """Read a Knowledge Base entry by ID."""
        entry = _entries_by_id.get(id)
        if not entry:
            return f"Entry '{id}' not found"

        path = entry.get("path", "")
        if path:
            content = _read_file_content(path)
            if content:
                return content

        return json.dumps(entry, indent=2)

    # --- Prompts ---

    @mcp.prompt()
    def new_team_onboarding() -> str:
        """Onboarding prompt for a new team joining the repository."""
        recent = _all_entries[-5:] if len(_all_entries) >= 5 else _all_entries
        recent_summary = "\n".join(
            f"- {e['id']}: {e.get('title', 'untitled')} [{e.get('date', '?')}]"
            for e in reversed(recent)
        )

        return f"""You are a new team starting work in the Claude Agent Repository.

## Prime Directive
Every AI team builds on the knowledge of the last team. Read prior work before starting.

## Repository Quick Status
- Total indexed entries: {len(_all_entries)}
- Pointer files loaded: {len(_pointer_cache)}

## Most Recent Entries
{recent_summary}

## Your Workflow
1. Read the latest team session logs in teams/
2. Check knowledge-base/index.json for relevant prior knowledge
3. Do your assigned work
4. Create KB entries for anything you learned (use knowledge-base/TEMPLATE.md)
5. Write a team session log (use teams/TEAM_LOG_TEMPLATE.md)
6. Update relevant index.json files

## Key Directories
- knowledge-base/entries/ — Shared knowledge (KB-XXXX.md)
- teams/ — Team session logs
- storage/scripts/ — Reusable scripts
- storage/coordination/ — Multi-agent coordination system
- storage/sources/ — Curated data source definitions

## Conventions
Use the `conventions` tool to get templates and naming rules for any content type.
Use the `search` tool to find relevant prior work before starting.
"""

    # Run with stdio transport
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
