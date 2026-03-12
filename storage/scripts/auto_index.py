#!/usr/bin/env python3
"""Regenerate all index.json files from source file metadata.

Scans indexed directories, reads metadata from source files (YAML
frontmatter for .md, JSON fields for .json), and generates index.json
files using the unified schema from SOP-030.

Usage:
    python auto_index.py [--dry-run] [--diff] [REPO_ROOT]
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path


def find_repo_root(start_path=None):
    """Find the repository root by looking for CLAUDE.md."""
    if start_path:
        return Path(start_path).resolve()
    path = Path.cwd()
    while path != path.parent:
        if (path / "CLAUDE.md").exists():
            return path
        path = path.parent
    return Path.cwd()


def parse_yaml_frontmatter(filepath):
    """Parse YAML frontmatter from a Markdown file.

    Uses a simple parser to avoid external dependencies.
    Returns a dict of frontmatter fields.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, UnicodeDecodeError):
        return {}

    if not content.startswith("---"):
        return {}

    # Find the closing ---
    end = content.find("\n---", 3)
    if end == -1:
        return {}

    frontmatter_text = content[3:end].strip()
    result = {}

    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Handle key: value pairs
        match = re.match(r"^([a-z_]+)\s*:\s*(.*)$", line, re.IGNORECASE)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()

            # Handle inline arrays: [item1, item2]
            if value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                result[key] = [
                    i.strip().strip("\"'") for i in items if i.strip()
                ]
            # Handle quoted strings
            elif value.startswith('"') and value.endswith('"'):
                result[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                result[key] = value[1:-1]
            # Handle empty values
            elif value == "" or value == "~" or value == "null":
                result[key] = None
            else:
                result[key] = value

    return result


def read_json_file(filepath):
    """Read and parse a JSON file. Returns dict or empty dict on error."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def generate_kb_index(repo_root):
    """Generate index for knowledge-base/entries/."""
    entries_dir = repo_root / "knowledge-base" / "entries"
    if not entries_dir.exists():
        return None, []

    entries = []
    warnings = []

    for filename in sorted(os.listdir(entries_dir)):
        if not re.match(r"^KB-\d{4}\.md$", filename):
            continue

        filepath = entries_dir / filename
        fm = parse_yaml_frontmatter(filepath)

        entry_id = fm.get("id", filename.replace(".md", ""))
        entry = {
            "id": entry_id,
            "title": fm.get("title", ""),
            "file": f"entries/{filename}",
            "date": fm.get("date", ""),
            "team": fm.get("team", ""),
        }

        # Optional fields
        if fm.get("category"):
            entry["category"] = fm["category"]
        if fm.get("tags"):
            entry["tags"] = fm["tags"] if isinstance(fm["tags"], list) else [fm["tags"]]
        if fm.get("status"):
            entry["status"] = fm["status"]
        if fm.get("confidence"):
            entry["confidence"] = fm["confidence"]
        if fm.get("builds_on"):
            bo = fm["builds_on"]
            entry["builds_on"] = bo if isinstance(bo, list) else [bo]

        # Validate required fields
        for field in ["title", "date", "team"]:
            if not entry.get(field):
                warnings.append(
                    f"  {filename}: missing required field '{field}'"
                )

        entries.append(entry)

    index = {
        "version": "2.0",
        "last_updated": str(date.today()),
        "count": len(entries),
        "entries": entries,
    }

    return index, warnings


def generate_sessions_index(repo_root):
    """Generate index for teams/sessions/."""
    sessions_dir = repo_root / "teams" / "sessions"
    if not sessions_dir.exists():
        return None, []

    entries = []
    warnings = []

    for filename in sorted(os.listdir(sessions_dir)):
        if not re.match(r"^TEAM-\d{4}\.md$", filename):
            continue

        filepath = sessions_dir / filename
        fm = parse_yaml_frontmatter(filepath)

        entry_id = fm.get("team_id", fm.get("id", filename.replace(".md", "")))
        entry = {
            "id": entry_id,
            "title": fm.get("objective", fm.get("title", "")),
            "file": filename,
            "date": fm.get("date", ""),
            "team": entry_id,
        }

        if fm.get("tags"):
            entry["tags"] = fm["tags"] if isinstance(fm["tags"], list) else [fm["tags"]]
        if fm.get("status"):
            entry["status"] = fm["status"]
        if fm.get("builds_on_knowledge"):
            bo = fm["builds_on_knowledge"]
            entry["builds_on"] = bo if isinstance(bo, list) else [bo]

        for field in ["title", "date"]:
            if not entry.get(field):
                warnings.append(
                    f"  {filename}: missing required field '{field}'"
                )

        entries.append(entry)

    index = {
        "version": "2.0",
        "last_updated": str(date.today()),
        "count": len(entries),
        "entries": entries,
    }

    return index, warnings


def generate_sops_index(repo_root):
    """Generate index for storage/coordination/sops/."""
    sops_dir = repo_root / "storage" / "coordination" / "sops"
    if not sops_dir.exists():
        return None, []

    entries = []
    warnings = []

    for filename in sorted(os.listdir(sops_dir)):
        if filename == "index.json":
            continue
        if not filename.endswith(".json"):
            continue

        filepath = sops_dir / filename
        data = read_json_file(filepath)

        entry_id = data.get("id", "")
        if not entry_id:
            # Try to extract from filename
            match = re.match(r"sop_(\d{3})_", filename)
            if match:
                entry_id = f"SOP-{int(match.group(1)):03d}"
            else:
                match2 = re.match(r"SOP-(\d{4})", filename)
                if match2:
                    entry_id = f"SOP-{match2.group(1)}"

        entry = {
            "id": entry_id,
            "title": data.get("title", data.get("purpose", "")),
            "file": filename,
            "date": data.get("created", data.get("date", "")),
            "team": data.get("team", ""),
        }

        if data.get("tags"):
            entry["tags"] = data["tags"]
        if data.get("status"):
            entry["status"] = data["status"]
        if data.get("version"):
            entry["version_number"] = data["version"]
        if data.get("supersedes"):
            entry["supersedes"] = data["supersedes"]

        if not entry_id:
            warnings.append(f"  {filename}: could not determine SOP ID")

        entries.append(entry)

    index = {
        "version": "2.0",
        "last_updated": str(date.today()),
        "count": len(entries),
        "entries": entries,
    }

    return index, warnings


def generate_sources_index(repo_root):
    """Generate index for storage/sources/."""
    sources_dir = repo_root / "storage" / "sources"
    if not sources_dir.exists():
        return None, []

    entries = []
    warnings = []

    for filename in sorted(os.listdir(sources_dir)):
        if filename in {"index.json", "TEMPLATE.json"}:
            continue
        if not filename.endswith(".json"):
            continue
        if not re.match(r"^SRC-\d{4}", filename):
            continue

        filepath = sources_dir / filename
        data = read_json_file(filepath)

        entry_id = data.get("id", "")
        if not entry_id:
            match = re.match(r"(SRC-\d{4})", filename)
            if match:
                entry_id = match.group(1)

        entry = {
            "id": entry_id,
            "title": data.get("name", data.get("title", "")),
            "file": filename,
            "date": data.get("last_verified", data.get("date", "")),
            "team": data.get("added_by_team", data.get("team", "")),
        }

        # Type-specific optional fields
        if data.get("url"):
            entry["url"] = data["url"]
        if data.get("access_type"):
            entry["access_type"] = data["access_type"]
        if data.get("data_types"):
            entry["data_types"] = data["data_types"]
        if data.get("tags"):
            entry["tags"] = data["tags"]
        if data.get("status"):
            entry["status"] = data["status"]

        entries.append(entry)

    index = {
        "version": "2.0",
        "last_updated": str(date.today()),
        "count": len(entries),
        "entries": entries,
    }

    return index, warnings


def generate_scripts_index(repo_root):
    """Generate index for storage/scripts/."""
    scripts_dir = repo_root / "storage" / "scripts"
    if not scripts_dir.exists():
        return None, []

    # Read existing index to preserve metadata we can't extract from filenames
    existing_index = read_json_file(scripts_dir / "index.json")
    existing_entries = {}
    # Handle both old and new array key names
    old_entries = existing_index.get("entries",
                     existing_index.get("scripts", []))
    for e in old_entries:
        eid = e.get("id", "")
        if eid:
            existing_entries[eid] = e
        fname = e.get("filename", e.get("file", e.get("name", "")))
        if fname:
            existing_entries[fname] = e

    entries = []
    warnings = []

    for filename in sorted(os.listdir(scripts_dir)):
        if filename in {"index.json"}:
            continue
        if not filename.endswith((".py", ".js", ".sh", ".go")):
            continue

        # Try to find existing entry for this file
        existing = existing_entries.get(filename, {})

        entry_id = existing.get("id", "")
        entry = {
            "id": entry_id,
            "title": existing.get("name", existing.get("title", filename.replace(".py", "").replace("_", " ").title())),
            "file": filename,
            "date": existing.get("added", existing.get("date", "")),
            "team": existing.get("added_by_team", existing.get("team", "")),
        }

        if existing.get("language"):
            entry["language"] = existing["language"]
        elif filename.endswith(".py"):
            entry["language"] = "python"
        elif filename.endswith(".js"):
            entry["language"] = "javascript"
        elif filename.endswith(".go"):
            entry["language"] = "go"
        elif filename.endswith(".sh"):
            entry["language"] = "shell"

        if existing.get("category"):
            entry["category"] = existing["category"]
        if existing.get("tags"):
            entry["tags"] = existing["tags"]
        if existing.get("dependencies"):
            entry["dependencies"] = existing["dependencies"]
        if existing.get("description"):
            entry["description"] = existing["description"]

        entries.append(entry)

    index = {
        "version": "2.0",
        "last_updated": str(date.today()),
        "count": len(entries),
        "entries": entries,
    }

    return index, warnings


def generate_api_tools_index(repo_root):
    """Generate index for storage/api-tools/."""
    tools_dir = repo_root / "storage" / "api-tools"
    if not tools_dir.exists():
        return None, []

    existing_index = read_json_file(tools_dir / "index.json")
    old_entries = existing_index.get("entries",
                     existing_index.get("tools", []))

    entries = []
    warnings = []

    for e in old_entries:
        entry = {
            "id": e.get("id", ""),
            "title": e.get("name", e.get("title", "")),
            "file": e.get("path", e.get("file", e.get("name", ""))),
            "date": e.get("date_added", e.get("date", e.get("added", ""))),
            "team": e.get("team", ""),
        }

        if e.get("category"):
            entry["category"] = e["category"]
        if e.get("tags"):
            entry["tags"] = e["tags"]
        if e.get("auth_type"):
            entry["auth_type"] = e["auth_type"]
        if e.get("modules"):
            entry["modules"] = e["modules"]
        if e.get("entry_point"):
            entry["entry_point"] = e["entry_point"]

        entries.append(entry)

    index = {
        "version": "2.0",
        "last_updated": str(date.today()),
        "count": len(entries),
        "entries": entries,
    }

    return index, warnings


# Registry of all index generators
INDEX_GENERATORS = [
    {
        "name": "knowledge-base",
        "index_path": "knowledge-base/index.json",
        "generator": generate_kb_index,
    },
    {
        "name": "teams/sessions",
        "index_path": "teams/sessions/index.json",
        "generator": generate_sessions_index,
    },
    {
        "name": "sops",
        "index_path": "storage/coordination/sops/index.json",
        "generator": generate_sops_index,
    },
    {
        "name": "sources",
        "index_path": "storage/sources/index.json",
        "generator": generate_sources_index,
    },
    {
        "name": "scripts",
        "index_path": "storage/scripts/index.json",
        "generator": generate_scripts_index,
    },
    {
        "name": "api-tools",
        "index_path": "storage/api-tools/index.json",
        "generator": generate_api_tools_index,
    },
]


def compute_diff(old_content, new_content, path):
    """Compute a simple diff between old and new JSON content."""
    lines = []
    old_str = json.dumps(old_content, indent=2).splitlines() if old_content else []
    new_str = json.dumps(new_content, indent=2).splitlines()

    lines.append(f"--- {path} (current)")
    lines.append(f"+++ {path} (regenerated)")

    # Simple comparison: show key structural differences
    if old_content:
        old_keys = set(old_content.keys()) if isinstance(old_content, dict) else set()
        new_keys = set(new_content.keys()) if isinstance(new_content, dict) else set()

        removed_keys = old_keys - new_keys
        added_keys = new_keys - old_keys

        if removed_keys:
            lines.append(f"  Removed top-level keys: {', '.join(sorted(removed_keys))}")
        if added_keys:
            lines.append(f"  Added top-level keys: {', '.join(sorted(added_keys))}")

        # Compare entry counts
        old_count = len(old_content.get("entries",
                        old_content.get("scripts",
                        old_content.get("tools",
                        old_content.get("sources",
                        old_content.get("sops",
                        old_content.get("sessions",
                        old_content.get("methods",
                        old_content.get("picks", [])))))))))
        new_count = len(new_content.get("entries", []))
        if old_count != new_count:
            lines.append(f"  Entry count: {old_count} -> {new_count}")
        else:
            lines.append(f"  Entry count: {new_count} (unchanged)")

        # Show array key rename
        for old_key in ["scripts", "tools", "sources", "sops", "sessions", "methods", "picks"]:
            if old_key in old_keys and "entries" in new_keys:
                lines.append(f"  Array key renamed: '{old_key}' -> 'entries'")
                break
    else:
        lines.append("  (new file)")
        new_count = len(new_content.get("entries", []))
        lines.append(f"  Entry count: {new_count}")

    return "\n".join(lines)


def run_auto_index(repo_root, dry_run=False, show_diff=False):
    """Run all index generators and write results."""
    results = []
    total_entries = 0
    total_warnings = []

    for gen_info in INDEX_GENERATORS:
        index_path = repo_root / gen_info["index_path"]
        name = gen_info["name"]

        new_index, warnings = gen_info["generator"](repo_root)
        if new_index is None:
            results.append({"name": name, "status": "skipped", "reason": "directory not found"})
            continue

        total_entries += new_index["count"]
        total_warnings.extend(warnings)

        # Read existing index for comparison
        old_index = read_json_file(index_path) if index_path.exists() else None

        if show_diff:
            diff = compute_diff(old_index, new_index, gen_info["index_path"])
            print(f"\n{diff}")

        if dry_run:
            results.append({
                "name": name,
                "status": "dry-run",
                "entries": new_index["count"],
                "path": gen_info["index_path"],
            })
        else:
            # Write the new index
            index_path.parent.mkdir(parents=True, exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(new_index, f, indent=2, ensure_ascii=False)
                f.write("\n")
            results.append({
                "name": name,
                "status": "written",
                "entries": new_index["count"],
                "path": gen_info["index_path"],
            })

        if warnings:
            print(f"\nWarnings for {name}:")
            for w in warnings:
                print(w)

    return results, total_entries, total_warnings


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate all index.json files from source file metadata.",
        epilog="Scans indexed directories, reads metadata from source files, "
        "and generates index.json files using the unified SOP-030 schema.",
    )
    parser.add_argument(
        "repo_root",
        nargs="?",
        default=None,
        help="Path to the repository root (default: auto-detect from cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing any files",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show before/after comparison for each index",
    )

    args = parser.parse_args()

    repo_root = find_repo_root(args.repo_root)

    if not (repo_root / "CLAUDE.md").exists():
        print(f"Warning: CLAUDE.md not found at {repo_root}", file=sys.stderr)

    mode = "DRY RUN" if args.dry_run else "REGENERATE"
    print(f"Auto-index ({mode}) - repo: {repo_root}")
    print("=" * 60)

    results, total_entries, warnings = run_auto_index(
        repo_root, dry_run=args.dry_run, show_diff=args.diff
    )

    print("\n" + "=" * 60)
    print("SUMMARY")
    regen_count = sum(1 for r in results if r["status"] in ("written", "dry-run"))
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
    print(f"  Indexes {'to regenerate' if args.dry_run else 'regenerated'}: {regen_count}")
    if skipped_count:
        print(f"  Indexes skipped: {skipped_count}")
    print(f"  Total entries: {total_entries}")
    if warnings:
        print(f"  Warnings: {len(warnings)}")

    for r in results:
        status = r["status"]
        name = r["name"]
        if status == "skipped":
            print(f"    [{status}] {name}: {r.get('reason', '')}")
        else:
            print(f"    [{status}] {name}: {r.get('entries', 0)} entries -> {r.get('path', '')}")


if __name__ == "__main__":
    main()
