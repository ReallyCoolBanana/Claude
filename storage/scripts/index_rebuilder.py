#!/usr/bin/env python3
"""
index_rebuilder.py — Index Rebuilder (SCR-0013)

Auto-generates index.json files by scanning directories.
Rebuilds knowledge-base/index.json and teams/sessions/index.json
from actual file contents.

Usage:
    python3 index_rebuilder.py [--dry-run] [--fix]

Options:
    --dry-run   Show what would change without writing (default behavior)
    --fix       Write the updated index files

Output: Diff between current and rebuilt index to stdout.
"""

import json
import os
import re
import sys
from pathlib import Path


def find_repo_root():
    """Find the repository root by looking for CLAUDE.md."""
    d = Path(__file__).resolve().parent
    for _ in range(10):
        if (d / "CLAUDE.md").exists():
            return d
        d = d.parent
    return Path(__file__).resolve().parent.parent.parent


def parse_yaml_frontmatter(filepath):
    """Parse YAML frontmatter from a markdown file.

    Returns dict of metadata. Simple parser for stdlib-only operation.
    """
    metadata = {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return metadata

    if not content.startswith("---"):
        return metadata

    parts = content.split("---", 2)
    if len(parts) < 3:
        return metadata

    yaml_text = parts[1].strip()
    if not yaml_text:
        return metadata

    for line in yaml_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        colon_idx = line.find(":")
        if colon_idx == -1:
            continue

        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if inner:
                metadata[key] = [item.strip().strip('"').strip("'") for item in inner.split(",")]
            else:
                metadata[key] = []
        elif (value.startswith('"') and value.endswith('"')) or \
             (value.startswith("'") and value.endswith("'")):
            metadata[key] = value[1:-1]
        elif value.lower() in ("null", "~", ""):
            metadata[key] = None
        else:
            metadata[key] = value

    return metadata


def extract_title_from_md(filepath):
    """Extract the first H1 heading from a markdown file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return os.path.basename(filepath)

    # Skip frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2]

    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()

    return os.path.basename(filepath)


def rebuild_kb_index(entries_dir):
    """Rebuild knowledge-base/index.json from entry files."""
    entries = []
    categories = {}
    tag_index = {}

    if not entries_dir.is_dir():
        return {"version": "1.0", "last_updated": "", "entry_count": 0,
                "entries": [], "categories": {}, "tag_index": {}}

    md_files = sorted(entries_dir.glob("*.md"))

    for mdfile in md_files:
        meta = parse_yaml_frontmatter(str(mdfile))
        if not meta.get("id"):
            continue

        title = extract_title_from_md(str(mdfile))

        entry = {
            "id": meta["id"],
            "title": title,
            "date": meta.get("date", ""),
            "team": meta.get("team", ""),
            "category": meta.get("category", ""),
            "tags": meta.get("tags", []),
            "status": meta.get("status", ""),
            "confidence": meta.get("confidence", ""),
        }

        # Include builds_on if present and non-empty
        builds_on = meta.get("builds_on", [])
        if builds_on and builds_on != []:
            entry["builds_on"] = builds_on

        entries.append(entry)

        # Populate categories
        cat = meta.get("category", "")
        if cat:
            categories.setdefault(cat, [])
            if meta["id"] not in categories[cat]:
                categories[cat].append(meta["id"])

        # Populate tag_index
        for tag in meta.get("tags", []):
            if isinstance(tag, str):
                tag_index.setdefault(tag, [])
                if meta["id"] not in tag_index[tag]:
                    tag_index[tag].append(meta["id"])

    # Ensure all known categories exist even if empty
    known_categories = ["methodology", "tool-usage", "debugging", "optimization", "integration", "market-research"]
    for cat in known_categories:
        categories.setdefault(cat, [])

    from datetime import date
    today = date.today().isoformat()

    index = {
        "version": "1.0",
        "last_updated": today,
        "entry_count": len(entries),
        "entries": entries,
        "categories": categories,
        "tag_index": tag_index
    }

    return index


def rebuild_teams_index(sessions_dir):
    """Rebuild teams/sessions/index.json from session files."""
    sessions = []

    if not sessions_dir.is_dir():
        return None

    md_files = sorted(sessions_dir.glob("*.md"))
    # Exclude templates
    md_files = [f for f in md_files if "TEMPLATE" not in f.name.upper()]

    for mdfile in md_files:
        meta = parse_yaml_frontmatter(str(mdfile))
        if not meta.get("team_id"):
            continue

        title = extract_title_from_md(str(mdfile))
        objective = meta.get("objective", "")
        if isinstance(objective, str):
            objective = objective.strip('"').strip("'")

        session = {
            "team_id": meta["team_id"],
            "filename": mdfile.name,
            "date": meta.get("date", ""),
            "objective": objective,
            "builds_on_knowledge": meta.get("builds_on_knowledge", []),
        }

        members = meta.get("members", [])
        if members:
            session["members"] = members

        sessions.append(session)

    from datetime import date
    today = date.today().isoformat()

    index = {
        "version": "1.0",
        "last_updated": today,
        "session_count": len(sessions),
        "sessions": sessions
    }

    return index


def json_diff(old_data, new_data, path=""):
    """Compute a human-readable diff between two JSON structures."""
    diffs = []

    if type(old_data) != type(new_data):
        diffs.append(f"  {path}: type changed from {type(old_data).__name__} to {type(new_data).__name__}")
        return diffs

    if isinstance(old_data, dict):
        all_keys = set(list(old_data.keys()) + list(new_data.keys()))
        for key in sorted(all_keys):
            sub_path = f"{path}.{key}" if path else key
            if key not in old_data:
                diffs.append(f"  + {sub_path}: {json.dumps(new_data[key], default=str)[:120]}")
            elif key not in new_data:
                diffs.append(f"  - {sub_path}: {json.dumps(old_data[key], default=str)[:120]}")
            else:
                diffs.extend(json_diff(old_data[key], new_data[key], sub_path))
    elif isinstance(old_data, list):
        if old_data != new_data:
            # For short lists, show full diff
            if len(old_data) < 20 and len(new_data) < 20:
                old_set = set(json.dumps(i, sort_keys=True, default=str) for i in old_data)
                new_set = set(json.dumps(i, sort_keys=True, default=str) for i in new_data)
                for item in new_set - old_set:
                    diffs.append(f"  + {path}[]: {item[:120]}")
                for item in old_set - new_set:
                    diffs.append(f"  - {path}[]: {item[:120]}")
            else:
                diffs.append(f"  ~ {path}: list changed ({len(old_data)} -> {len(new_data)} items)")
    else:
        if old_data != new_data:
            diffs.append(f"  ~ {path}: {old_data!r} -> {new_data!r}")

    return diffs


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild index.json files from directory contents")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Show changes without writing (default)")
    parser.add_argument("--fix", action="store_true",
                        help="Write updated index files")
    args = parser.parse_args()

    if args.fix:
        args.dry_run = False

    root = find_repo_root()
    results = {"indexes_checked": 0, "indexes_updated": 0, "diffs": {}}

    # --- Rebuild knowledge-base/index.json ---
    kb_entries_dir = root / "knowledge-base" / "entries"
    kb_index_path = root / "knowledge-base" / "index.json"

    new_kb_index = rebuild_kb_index(kb_entries_dir)
    results["indexes_checked"] += 1

    current_kb_index = {}
    if kb_index_path.exists():
        try:
            with open(kb_index_path, "r", encoding="utf-8") as f:
                current_kb_index = json.load(f)
        except (json.JSONDecodeError, IOError):
            current_kb_index = {}

    kb_diffs = json_diff(current_kb_index, new_kb_index)

    if kb_diffs:
        results["diffs"]["knowledge-base/index.json"] = kb_diffs
        results["indexes_updated"] += 1
        print("=" * 60)
        print("DIFF: knowledge-base/index.json")
        print("=" * 60)
        for d in kb_diffs:
            print(d)
        print()

        if not args.dry_run:
            with open(kb_index_path, "w", encoding="utf-8") as f:
                json.dump(new_kb_index, f, indent=2)
                f.write("\n")
            print(f"  WRITTEN: {kb_index_path}")
    else:
        print("knowledge-base/index.json: NO CHANGES")

    # --- Rebuild teams/sessions/index.json ---
    sessions_dir = root / "teams" / "sessions"
    sessions_index_path = sessions_dir / "index.json"

    new_sessions_index = rebuild_teams_index(sessions_dir)
    results["indexes_checked"] += 1

    if new_sessions_index is not None:
        current_sessions_index = {}
        if sessions_index_path.exists():
            try:
                with open(sessions_index_path, "r", encoding="utf-8") as f:
                    current_sessions_index = json.load(f)
            except (json.JSONDecodeError, IOError):
                current_sessions_index = {}

        sessions_diffs = json_diff(current_sessions_index, new_sessions_index)

        if sessions_diffs:
            results["diffs"]["teams/sessions/index.json"] = sessions_diffs
            results["indexes_updated"] += 1
            print("=" * 60)
            print("DIFF: teams/sessions/index.json")
            print("=" * 60)
            for d in sessions_diffs:
                print(d)
            print()

            if not args.dry_run:
                with open(sessions_index_path, "w", encoding="utf-8") as f:
                    json.dump(new_sessions_index, f, indent=2)
                    f.write("\n")
                print(f"  WRITTEN: {sessions_index_path}")
        else:
            print("teams/sessions/index.json: NO CHANGES")

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Indexes checked:  {results['indexes_checked']}")
    print(f"  Indexes with diffs: {results['indexes_updated']}")
    print(f"  Mode: {'DRY RUN (use --fix to write)' if args.dry_run else 'FIX (files written)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
