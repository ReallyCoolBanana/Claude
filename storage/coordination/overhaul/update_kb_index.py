#!/usr/bin/env python3
"""KB index regenerator.

Scans knowledge-base/entries/ for all KB-*.md files, parses YAML frontmatter,
and regenerates:
  - knowledge-base/index.json          (full index)
  - knowledge-base/INDEX_COMPACT.json  (compact: id, title, file, date, type)
  - knowledge-base/SUMMARY.json        (counts and metadata)

Ensures all entries have: id, title, file, date, type, status, team, category, tags

Usage:
    python update_kb_index.py [--repo-root /path/to/repo] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Repository root discovery
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


# ---------------------------------------------------------------------------
# YAML frontmatter parser
# ---------------------------------------------------------------------------

def parse_yaml_frontmatter(filepath: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown file. Returns empty dict if none."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, IOError):
        return {}

    if not content.startswith("---"):
        return {}

    end = content.find("\n---", 3)
    if end == -1:
        return {}

    frontmatter_text = content[3:end].strip()
    result = {}
    current_key = None
    list_mode = False

    for line in frontmatter_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Detect YAML list continuation (  - value)
        if list_mode and re.match(r'^-\s+', stripped):
            val = stripped[1:].strip().strip("'\"")
            if current_key and isinstance(result.get(current_key), list):
                result[current_key].append(val)
            continue
        else:
            list_mode = False

        # Handle key: value
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', stripped)
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            current_key = key

            # Handle inline list [a, b, c]
            if value.startswith("[") and value.endswith("]"):
                items = [x.strip().strip("'\"") for x in value[1:-1].split(",")]
                result[key] = [x for x in items if x]
            elif value == "" or value == "[]":
                result[key] = []
                list_mode = True
            elif value.lower() in ("true", "false"):
                result[key] = value.lower() == "true"
            elif value.startswith('"') and value.endswith('"'):
                result[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                result[key] = value[1:-1]
            else:
                result[key] = value

    return result


def extract_title_from_content(filepath: str) -> str:
    """Extract the first H1 heading from markdown content as fallback title."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, IOError):
        return ""

    # Skip frontmatter
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            content = content[end + 4:]

    # Find first H1
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# ") and not line.startswith("##"):
            return line[2:].strip()

    return ""


# ---------------------------------------------------------------------------
# Index generation
# ---------------------------------------------------------------------------

DEFAULT_ENTRY_FIELDS = {
    "type": "knowledge-base",
    "status": "validated",
    "team": "unknown",
    "category": "general",
    "tags": [],
    "confidence": "medium",
    "builds_on": [],
}


def build_entry(md_file: str, filepath: str) -> dict[str, Any]:
    """Build an index entry from a KB markdown file."""
    fm = parse_yaml_frontmatter(filepath)

    entry_id = fm.get("id", md_file.replace(".md", ""))

    # Title: from frontmatter, then from H1, then from ID
    title = fm.get("title", "")
    if not title or title == entry_id:
        title = extract_title_from_content(filepath)
    if not title:
        title = entry_id

    entry = {
        "id": entry_id,
        "title": title,
        "file": f"entries/{md_file}",
        "date": fm.get("date", ""),
    }

    # Fill in remaining fields with defaults
    for field, default in DEFAULT_ENTRY_FIELDS.items():
        val = fm.get(field, default)
        entry[field] = val

    # Ensure tags is a list
    if not isinstance(entry.get("tags"), list):
        entry["tags"] = []

    # Ensure builds_on is a list
    if not isinstance(entry.get("builds_on"), list):
        entry["builds_on"] = []

    return entry


def generate_indexes(repo_root: str, dry_run: bool = False) -> dict:
    """Scan KB entries and regenerate all index files."""
    entries_dir = os.path.join(repo_root, "knowledge-base", "entries")
    kb_dir = os.path.join(repo_root, "knowledge-base")

    if not os.path.isdir(entries_dir):
        print(f"[ERROR] KB entries directory not found: {entries_dir}")
        return {"error": "entries directory not found"}

    # Scan for all KB-*.md files
    md_files = sorted([f for f in os.listdir(entries_dir)
                       if f.startswith("KB-") and f.endswith(".md")])

    print(f"[update_kb_index] Found {len(md_files)} KB entry files")

    entries = []
    issues = []

    for md_file in md_files:
        filepath = os.path.join(entries_dir, md_file)
        entry = build_entry(md_file, filepath)

        # Track issues
        if not entry.get("date"):
            issues.append(f"{md_file}: missing 'date' field")
        if entry.get("type") in (None, ""):
            issues.append(f"{md_file}: missing 'type' field")
            entry["type"] = "knowledge-base"
        if entry.get("title") == entry.get("id"):
            issues.append(f"{md_file}: title equals id (no real title)")

        entries.append(entry)

    # Build full index
    full_index = {
        "version": "1.0",
        "count": len(entries),
        "entries": entries,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }

    # Build compact index
    compact_entries = []
    for e in entries:
        compact_entries.append({
            "id": e["id"],
            "title": e["title"],
            "file": e["file"],
            "date": e["date"],
            "type": e["type"],
            "category": e.get("category", ""),
            "tags": e.get("tags", []),
        })

    compact_index = {
        "version": "1.0",
        "count": len(compact_entries),
        "entries": compact_entries,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }

    # Build summary
    categories = {}
    teams = {}
    for e in entries:
        cat = e.get("category", "uncategorized")
        categories[cat] = categories.get(cat, 0) + 1
        team = e.get("team", "unknown")
        teams[team] = teams.get(team, 0) + 1

    summary = {
        "version": "1.0",
        "total_entries": len(entries),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "categories": categories,
        "teams_count": len(teams),
        "id_range": {
            "first": entries[0]["id"] if entries else None,
            "last": entries[-1]["id"] if entries else None,
        },
        "issues_found": len(issues),
        "issues": issues[:20],  # Cap at 20 for readability
    }

    if dry_run:
        print("[DRY RUN] Would write:")
        print(f"  - knowledge-base/index.json ({len(entries)} entries)")
        print(f"  - knowledge-base/INDEX_COMPACT.json ({len(compact_entries)} entries)")
        print(f"  - knowledge-base/SUMMARY.json")
        if issues:
            print(f"\n  Issues found ({len(issues)}):")
            for issue in issues[:10]:
                print(f"    - {issue}")
    else:
        # Write full index
        index_path = os.path.join(kb_dir, "index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(full_index, f, indent=2, ensure_ascii=False)
        print(f"  Written: {index_path} ({len(entries)} entries)")

        # Write compact index
        compact_path = os.path.join(kb_dir, "INDEX_COMPACT.json")
        with open(compact_path, "w", encoding="utf-8") as f:
            json.dump(compact_index, f, indent=2, ensure_ascii=False)
        print(f"  Written: {compact_path} ({len(compact_entries)} entries)")

        # Write summary
        summary_path = os.path.join(kb_dir, "SUMMARY.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"  Written: {summary_path}")

    return {
        "entries_scanned": len(md_files),
        "entries_indexed": len(entries),
        "issues": issues,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="KB index regenerator")
    parser.add_argument("--repo-root", default=None, help="Repository root path")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files")
    args = parser.parse_args()

    repo_root = args.repo_root or _find_repo_root()
    print(f"[update_kb_index] Repository root: {repo_root}")
    print()

    result = generate_indexes(repo_root, dry_run=args.dry_run)

    print()
    if result.get("issues"):
        print(f"Issues found: {len(result['issues'])}")
        for issue in result["issues"][:10]:
            print(f"  - {issue}")
    else:
        print("No issues found.")

    print(f"\nTotal entries indexed: {result.get('entries_indexed', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
