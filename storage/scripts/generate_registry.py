#!/usr/bin/env python3
"""
generate_registry.py — Solution B: Distributed .pointer.json registry generator.

Walks known directories to find .pointer.json files, validates entries against
source files, and generates a REGISTRY.json at repo root.

Usage:
    python generate_registry.py                    # Generate REGISTRY.json
    python generate_registry.py --validate         # Read-only validation
    python generate_registry.py --allocate-id KB   # Return next ID for prefix
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Repo root: two levels up from storage/scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Known directories that may contain .pointer.json files
POINTER_DIRS = [
    "knowledge-base",
    "teams",
    "storage/scripts",
    "storage/api-tools",
    "storage/sources",
    "storage/coordination",
    "market-research",
    "market-research/picks",
    "market-research/methods",
]


def find_pointer_files() -> list[Path]:
    """Find all .pointer.json files in known directories."""
    found: list[Path] = []
    for rel_dir in POINTER_DIRS:
        candidate = REPO_ROOT / rel_dir / ".pointer.json"
        if candidate.exists():
            found.append(candidate)
    # Also do a shallow scan for any we missed
    for item in REPO_ROOT.rglob(".pointer.json"):
        if item not in found:
            found.append(item)
    found.sort(key=lambda p: str(p))
    return found


def load_pointer(path: Path) -> dict | None:
    """Load and validate a .pointer.json file."""
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("schema") != "pointer/1.0":
            print(f"WARN: {path} has unknown schema: {data.get('schema')}", file=sys.stderr)
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: Failed to load {path}: {e}", file=sys.stderr)
        return None


def validate_entry_paths(pointer_path: Path, entries: list[dict]) -> list[str]:
    """Check that each entry's path points to an existing file."""
    errors: list[str] = []
    for entry in entries:
        entry_path = REPO_ROOT / entry.get("path", "")
        if not entry_path.exists():
            errors.append(f"  {entry['id']}: file not found: {entry.get('path')}")
    return errors


def find_orphan_sources(pointer_path: Path, entries: list[dict]) -> list[str]:
    """Find source files in the pointer's directory that aren't in any entry."""
    warnings: list[str] = []
    pointer_dir = pointer_path.parent
    indexed_paths = {REPO_ROOT / e["path"] for e in entries if "path" in e}

    # Determine which source file patterns to check based on prefix
    prefix = ""
    for entry in entries:
        if "id" in entry:
            match = re.match(r"^([A-Z]+)-", entry["id"])
            if match:
                prefix = match.group(1)
                break

    # Map prefixes to file patterns
    patterns: dict[str, str] = {
        "KB": "*.md",
        "SRC": "*.json",
        "TEAM": "*.md",
    }

    pattern = patterns.get(prefix, "*")
    source_dir = pointer_dir
    # KB entries are in entries/ subdir
    if prefix == "KB" and (pointer_dir / "entries").is_dir():
        source_dir = pointer_dir / "entries"

    for source_file in sorted(source_dir.glob(pattern)):
        if source_file.name.startswith(".") or source_file.name in ("index.json", "TEMPLATE.md", "TEMPLATE.json", "README.md"):
            continue
        if source_file not in indexed_paths:
            rel = source_file.relative_to(REPO_ROOT)
            warnings.append(f"  orphan file not in pointer: {rel}")

    return warnings


def build_registry(pointer_files: list[Path]) -> dict:
    """Build the REGISTRY.json data structure from all pointer files."""
    all_entries: list[dict] = []
    pointer_info: list[dict] = []
    id_to_pointer: dict[str, str] = {}
    global_tags: dict[str, list[str]] = {}

    for pf in pointer_files:
        data = load_pointer(pf)
        if data is None:
            continue

        rel_path = str(pf.relative_to(REPO_ROOT))
        entries = data.get("entries", [])

        pointer_info.append({
            "path": rel_path,
            "prefix": data.get("prefix", ""),
            "entry_count": len(entries),
            "next_id": data.get("next_id", 0),
        })

        for entry in entries:
            entry_id = entry.get("id", "")
            id_to_pointer[entry_id] = rel_path
            all_entries.append(entry)

            for tag in entry.get("tags", []):
                if tag not in global_tags:
                    global_tags[tag] = []
                if entry_id not in global_tags[tag]:
                    global_tags[tag].append(entry_id)

    # Sort everything for deterministic output
    for tag in global_tags:
        global_tags[tag].sort()

    registry = {
        "schema": "registry/1.0",
        "generated": _now_iso(),
        "total_entries": len(all_entries),
        "pointer_files": sorted(pointer_info, key=lambda p: p["path"]),
        "global_tag_index": dict(sorted(global_tags.items())),
        "id_to_pointer_file": dict(sorted(id_to_pointer.items())),
    }
    return registry


def _now_iso() -> str:
    """Current UTC time as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_generate() -> int:
    """Generate REGISTRY.json at repo root."""
    pointer_files = find_pointer_files()
    if not pointer_files:
        print("No .pointer.json files found.", file=sys.stderr)
        return 1

    registry = build_registry(pointer_files)
    out_path = REPO_ROOT / "REGISTRY.json"
    with open(out_path, "w") as f:
        json.dump(registry, f, indent=2, sort_keys=False)
        f.write("\n")

    print(f"Generated {out_path}")
    print(f"  pointer files: {len(registry['pointer_files'])}")
    print(f"  total entries: {registry['total_entries']}")
    print(f"  unique tags:   {len(registry['global_tag_index'])}")
    return 0


def cmd_validate() -> int:
    """Read-only validation of pointer files against source files."""
    pointer_files = find_pointer_files()
    if not pointer_files:
        print("No .pointer.json files found.", file=sys.stderr)
        return 1

    total_errors = 0
    total_warnings = 0

    for pf in pointer_files:
        data = load_pointer(pf)
        if data is None:
            total_errors += 1
            continue

        rel = pf.relative_to(REPO_ROOT)
        entries = data.get("entries", [])
        print(f"\n{rel} ({len(entries)} entries, prefix={data.get('prefix', '?')})")

        # Check entry paths exist
        path_errors = validate_entry_paths(pf, entries)
        for err in path_errors:
            print(err, file=sys.stderr)
        total_errors += len(path_errors)

        # Check for orphan source files
        orphans = find_orphan_sources(pf, entries)
        for warn in orphans:
            print(warn, file=sys.stderr)
        total_warnings += len(orphans)

        # Check for duplicate IDs
        ids = [e.get("id") for e in entries]
        dupes = [eid for eid in ids if ids.count(eid) > 1]
        if dupes:
            print(f"  ERROR: duplicate IDs: {set(dupes)}", file=sys.stderr)
            total_errors += len(set(dupes))

        # Check next_id consistency
        max_num = 0
        prefix = data.get("prefix", "")
        for entry in entries:
            match = re.match(rf"^{re.escape(prefix)}-(\d+)$", entry.get("id", ""))
            if match:
                max_num = max(max_num, int(match.group(1)))
        expected_next = max_num + 1
        actual_next = data.get("next_id", 0)
        if actual_next < expected_next:
            print(f"  ERROR: next_id={actual_next} but max existing is {prefix}-{max_num:04d} (should be >= {expected_next})", file=sys.stderr)
            total_errors += 1

    print(f"\nValidation complete: {total_errors} errors, {total_warnings} warnings")
    return 1 if total_errors > 0 else 0


def cmd_allocate_id(prefix: str) -> int:
    """Find the pointer file for the given prefix and return the next ID."""
    pointer_files = find_pointer_files()
    for pf in pointer_files:
        data = load_pointer(pf)
        if data and data.get("prefix") == prefix:
            next_num = data.get("next_id", 1)
            # Determine zero-padding width from existing entries
            width = 4  # default
            for entry in data.get("entries", []):
                match = re.match(rf"^{re.escape(prefix)}-(\d+)$", entry.get("id", ""))
                if match:
                    width = len(match.group(1))
                    break
            next_id = f"{prefix}-{next_num:0{width}d}"
            print(next_id)
            return 0

    print(f"No pointer file found with prefix '{prefix}'", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate REGISTRY.json from .pointer.json files"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Read-only validation (no files written)"
    )
    parser.add_argument(
        "--allocate-id", metavar="PREFIX",
        help="Return the next available ID for a given prefix"
    )
    args = parser.parse_args()

    # Change to repo root for consistent relative paths
    os.chdir(REPO_ROOT)

    if args.allocate_id:
        return cmd_allocate_id(args.allocate_id)
    elif args.validate:
        return cmd_validate()
    else:
        return cmd_generate()


if __name__ == "__main__":
    sys.exit(main())
