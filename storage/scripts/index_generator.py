#!/usr/bin/env python3
"""
Universal Index Generator for the Claude Agent Repository.

Scans all 6 subsystem directories for actual files on disk,
generates/regenerates index.json, SUMMARY.json, and INDEX_COMPACT.json
for each subsystem. Detects orphaned files and dangling references.

Follows SOP-030 (Universal Index Schema) and SOP-036 (Three-tier indexing).
Idempotent: running twice produces identical output.

Usage:
    python3 index_generator.py                  # Full regeneration + validation report
    python3 index_generator.py --dry-run        # Report only, no writes
    python3 index_generator.py --subsystem KB   # Only regenerate KB indexes
    python3 index_generator.py --validate-only  # Only validate, no regeneration
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # /home/user/Claude

SUBSYSTEMS = {
    "KB": {
        "dir": REPO_ROOT / "knowledge-base" / "entries",
        "index_dir": REPO_ROOT / "knowledge-base",
        "pattern": r"^KB-\d{4}\.md$",
        "id_prefix": "KB",
        "id_digits": 4,
    },
    "SCR": {
        "dir": REPO_ROOT / "storage" / "scripts",
        "index_dir": REPO_ROOT / "storage" / "scripts",
        "pattern": r".*\.(py|go)$",
        "id_prefix": "SCR",
        "id_digits": 4,
        "recursive": True,
        "exclude_dirs": {"__pycache__", ".git", "fast_search", "fast_validate", "fast_cache", "data-gathering"},
        "include_subdirs": {
            "fast_search": True,
            "fast_validate": True,
            "fast_cache": True,
            "data-gathering": True,
        },
    },
    "SOP": {
        "dir": REPO_ROOT / "storage" / "coordination" / "sops",
        "index_dir": REPO_ROOT / "storage" / "coordination" / "sops",
        "pattern": r"^sop_\d{3}_.*\.json$",
        "id_prefix": "SOP",
        "id_digits": 3,
    },
    "SRC": {
        "dir": REPO_ROOT / "storage" / "sources",
        "index_dir": REPO_ROOT / "storage" / "sources",
        "pattern": r"^SRC-\d{4}-.*\.json$",
        "id_prefix": "SRC",
        "id_digits": 4,
    },
    "DATA": {
        "dir": REPO_ROOT / "storage" / "data",
        "index_dir": REPO_ROOT / "storage" / "data",
        "pattern": r"^(?!index\.json$|INDEX_COMPACT\.json$|SUMMARY\.json$).*\.(json|db|csv|sqlite)$",
        "id_prefix": "DATA",
        "id_digits": 4,
    },
    "TEAM": {
        "dir": REPO_ROOT / "teams" / "sessions",
        "index_dir": REPO_ROOT / "teams" / "sessions",
        "pattern": r"^TEAM-\d{4}\.md$",
        "id_prefix": "TEAM",
        "id_digits": 4,
    },
}

TODAY = date.today().isoformat()

# Meta-files to skip when scanning
META_FILES = {"index.json", "INDEX_COMPACT.json", "SUMMARY.json", "TEMPLATE.json",
              "TEMPLATE.md", "README.md", "__init__.py"}

# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def scan_disk_files(subsystem: str) -> list[str]:
    """Return list of relative file paths for a subsystem directory."""
    cfg = SUBSYSTEMS[subsystem]
    base_dir = cfg["dir"]
    pattern = re.compile(cfg["pattern"])

    if not base_dir.exists():
        return []

    files = []

    if subsystem == "SCR":
        # Special handling for scripts: walk the full scripts tree
        scripts_dir = cfg["dir"]
        for root, dirs, filenames in os.walk(scripts_dir):
            # Skip meta directories
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", "benchmarks"}]
            for fname in sorted(filenames):
                if fname in META_FILES:
                    continue
                if not (fname.endswith(".py") or fname.endswith(".go")):
                    continue
                rel = os.path.relpath(os.path.join(root, fname), scripts_dir)
                files.append(rel)
    else:
        for entry in sorted(base_dir.iterdir()):
            if entry.is_file() and entry.name not in META_FILES:
                if pattern.match(entry.name):
                    files.append(entry.name)

    return files


def extract_id_from_filename(filename: str, subsystem: str) -> str | None:
    """Extract the ID (e.g., KB-0001, SRC-0012) from a filename."""
    cfg = SUBSYSTEMS[subsystem]
    prefix = cfg["id_prefix"]
    digits = cfg["id_digits"]

    if subsystem == "SRC":
        m = re.match(r"(SRC-\d{4})", filename)
        return m.group(1) if m else None
    elif subsystem == "KB":
        m = re.match(r"(KB-\d{4})", filename)
        return m.group(1) if m else None
    elif subsystem == "TEAM":
        m = re.match(r"(TEAM-\d{4})", filename)
        return m.group(1) if m else None
    elif subsystem == "SOP":
        m = re.match(r"sop_(\d{3})", filename)
        return f"SOP-{m.group(1)}" if m else None
    elif subsystem == "DATA":
        # DATA files don't have ID-prefixed names; match from existing index
        return None
    elif subsystem == "SCR":
        # Scripts don't have ID-prefixed names
        return None
    return None


# ---------------------------------------------------------------------------
# Entry parsing per subsystem
# ---------------------------------------------------------------------------

def parse_yaml_frontmatter(filepath: Path) -> dict:
    """Parse YAML frontmatter from a markdown file."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}

    if not content.startswith("---"):
        return {}

    end = content.find("---", 3)
    if end == -1:
        return {}

    fm_text = content[3:end].strip()
    result = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # Handle arrays
            if val.startswith("["):
                try:
                    result[key] = json.loads(val.replace("'", '"'))
                except json.JSONDecodeError:
                    # Parse comma-separated bracketed list
                    inner = val.strip("[]")
                    result[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
            elif val.startswith('"') or val.startswith("'"):
                result[key] = val.strip("'\"")
            else:
                result[key] = val
    return result


def build_kb_entry(filename: str) -> dict | None:
    """Build a KB index entry from the file on disk."""
    filepath = SUBSYSTEMS["KB"]["dir"] / filename
    entry_id = extract_id_from_filename(filename, "KB")
    if not entry_id:
        return None

    fm = parse_yaml_frontmatter(filepath)
    title = fm.get("title", entry_id)
    date_val = fm.get("date", TODAY)
    team = fm.get("team", "unknown")
    category = fm.get("category", "uncategorized")
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    status = fm.get("status", "validated")
    confidence = fm.get("confidence", "medium")
    builds_on = fm.get("builds_on", [])
    if isinstance(builds_on, str):
        builds_on = [b.strip() for b in builds_on.split(",")]

    return {
        "id": entry_id,
        "title": title[:80],
        "file": filename,
        "date": date_val,
        "team": team,
        "tags": tags[:5] if isinstance(tags, list) else [],
        "status": status,
        "category": category,
        "confidence": confidence,
        "builds_on": builds_on if isinstance(builds_on, list) else [],
    }


def build_src_entry(filename: str) -> dict | None:
    """Build a SRC index entry from the JSON file on disk."""
    filepath = SUBSYSTEMS["SRC"]["dir"] / filename
    entry_id = extract_id_from_filename(filename, "SRC")
    if not entry_id:
        return None

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    # Source files have varying schemas; extract common fields
    name = data.get("name", data.get("title", entry_id))
    url = data.get("url", data.get("base_url", ""))
    data_types = data.get("data_types", [])
    access_type = data.get("access_type", data.get("auth", "unknown"))
    tags = data.get("tags", [])
    if not tags and data_types:
        tags = data_types[:5]

    return {
        "id": entry_id,
        "title": str(name)[:80],
        "file": filename,
        "date": data.get("date", data.get("date_added", TODAY)),
        "team": data.get("team", data.get("populated_by", "unknown")),
        "tags": tags[:5] if isinstance(tags, list) else [],
        "status": data.get("status", "active"),
        "url": url,
        "data_types": data_types,
        "access_type": access_type,
    }


def build_sop_entry(filename: str) -> dict | None:
    """Build a SOP index entry from the JSON file on disk."""
    filepath = SUBSYSTEMS["SOP"]["dir"] / filename
    entry_id = extract_id_from_filename(filename, "SOP")
    if not entry_id:
        return None

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    # Handle v2 variants: if filename contains _v2, append -v2 to ID
    raw_id = data.get("id", entry_id)
    if "_v2" in filename:
        raw_id = raw_id + "-v2" if not raw_id.endswith("-v2") else raw_id

    return {
        "id": raw_id,
        "title": str(data.get("title", entry_id))[:80],
        "file": filename,
        "date": data.get("created", data.get("created_date", TODAY)),
        "team": data.get("team", "unknown"),
        "tags": data.get("tags", [])[:5],
        "status": data.get("status", "active"),
        "version": data.get("version", "1.0"),
        "builds_on": data.get("builds_on", []),
    }


def build_data_entries(disk_files: list[str]) -> list[dict]:
    """Build DATA entries, auto-assigning IDs to files that lack them."""
    existing = load_existing_data_index()

    # Map existing IDs
    id_map = {}  # filename -> id
    used_ids = set()
    for fname, entry in existing.items():
        eid = entry.get("id", "")
        if eid:
            id_map[fname] = eid
            used_ids.add(eid)

    max_id = 0
    for eid in used_ids:
        m = re.match(r"DATA-(\d+)", eid)
        if m:
            max_id = max(max_id, int(m.group(1)))

    entries = []
    for filename in sorted(disk_files):
        e = existing.get(filename, {})

        if filename in id_map:
            entry_id = id_map[filename]
        else:
            max_id += 1
            entry_id = f"DATA-{max_id:04d}"

        title = e.get("description", e.get("title", filename))
        entries.append({
            "id": entry_id,
            "title": str(title)[:80],
            "file": filename,
            "date": e.get("date", e.get("date_created", TODAY)),
            "team": e.get("team", "unknown"),
            "tags": e.get("tags", [])[:5],
            "status": e.get("status", "active"),
            "category": e.get("category", "data"),
            "format": e.get("format", filename.rsplit(".", 1)[-1] if "." in filename else "unknown"),
        })

    entries.sort(key=lambda x: x["id"])
    return entries


def build_team_entry(filename: str) -> dict | None:
    """Build a TEAM index entry from the session log on disk."""
    filepath = SUBSYSTEMS["TEAM"]["dir"] / filename
    entry_id = extract_id_from_filename(filename, "TEAM")
    if not entry_id:
        return None

    fm = parse_yaml_frontmatter(filepath)
    title = fm.get("title", f"Session Log {entry_id}")
    date_val = fm.get("date", TODAY)
    team = fm.get("team", entry_id)

    return {
        "id": entry_id,
        "title": str(title)[:80],
        "file": filename,
        "date": date_val,
        "team": team,
        "tags": [],
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# Script index: merge disk scan with existing metadata
# ---------------------------------------------------------------------------

def load_existing_script_index() -> dict[str, dict]:
    """Load existing script index entries keyed by filename."""
    index_path = SUBSYSTEMS["SCR"]["index_dir"] / "index.json"
    if not index_path.exists():
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    result = {}
    entries = data.get("scripts", data.get("entries", []))
    for e in entries:
        fname = e.get("filename", e.get("file", ""))
        if fname:
            result[fname] = e
    return result


def load_existing_data_index() -> dict[str, dict]:
    """Load existing data index entries keyed by filename."""
    index_path = SUBSYSTEMS["DATA"]["index_dir"] / "index.json"
    if not index_path.exists():
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    result = {}
    entries = data.get("files", data.get("entries", []))
    for e in entries:
        fname = e.get("filename", e.get("file", ""))
        if fname:
            result[fname] = e
    return result


def infer_script_category(filepath: str) -> str:
    """Infer category from script file path."""
    if "data-gathering" in filepath:
        return "data-collection"
    if "fast_" in filepath:
        return "utility"
    if "test" in filepath.lower() or "stress" in filepath.lower():
        return "testing"
    if "bench" in filepath.lower():
        return "analysis"
    return "utility"


def infer_script_language(filepath: str) -> str:
    """Infer language from file extension."""
    if filepath.endswith(".go"):
        return "go"
    return "python"


def build_script_entries(disk_files: list[str]) -> list[dict]:
    """Build script entries, merging disk scan with existing metadata."""
    existing = load_existing_script_index()

    # Build a mapping from existing entries to preserve IDs
    id_map = {}  # filename -> id
    used_ids = set()
    for fname, entry in existing.items():
        eid = entry.get("id", "")
        if eid:
            id_map[fname] = eid
            used_ids.add(eid)

    # Find max existing ID
    max_id = 0
    for eid in used_ids:
        m = re.match(r"SCR-(\d+)", eid)
        if m:
            max_id = max(max_id, int(m.group(1)))

    entries = []
    for filepath in sorted(disk_files):
        # Skip index_generator itself
        if os.path.basename(filepath) == "index_generator.py":
            continue

        # Try to find existing metadata
        existing_entry = existing.get(filepath, {})

        # Assign ID
        if filepath in id_map:
            entry_id = id_map[filepath]
        else:
            max_id += 1
            entry_id = f"SCR-{max_id:04d}"
            id_map[filepath] = entry_id

        name = existing_entry.get("name", os.path.splitext(os.path.basename(filepath))[0])
        desc = existing_entry.get("description", f"Script: {filepath}")
        category = existing_entry.get("category", infer_script_category(filepath))
        language = existing_entry.get("language", infer_script_language(filepath))
        deps = existing_entry.get("dependencies", [])
        date_val = existing_entry.get("added", existing_entry.get("date", TODAY))
        team = existing_entry.get("added_by_team", existing_entry.get("team", "unknown"))

        entries.append({
            "id": entry_id,
            "title": str(name)[:80],
            "file": filepath,
            "date": date_val,
            "team": team,
            "tags": [],
            "status": "active",
            "category": category,
            "language": language,
            "description": desc,
            "dependencies": deps,
        })

    # Sort by ID
    entries.sort(key=lambda e: e["id"])
    return entries


# ---------------------------------------------------------------------------
# Three-tier index generation
# ---------------------------------------------------------------------------

def generate_index(entries: list[dict]) -> dict:
    """Generate a full index.json following SOP-030 schema."""
    return {
        "version": "1.0",
        "last_updated": TODAY,
        "count": len(entries),
        "entries": entries,
    }


def generate_compact(entries: list[dict]) -> dict:
    """Generate INDEX_COMPACT.json (tier 2). Max ~50 tokens per entry."""
    compact_entries = []
    for e in entries:
        compact = {
            "id": e["id"],
            "title": e["title"],
            "date": e["date"],
            "status": e.get("status", "active"),
        }
        if "category" in e:
            compact["category"] = e["category"]
        compact_entries.append(compact)

    return {
        "version": "1.0",
        "last_updated": TODAY,
        "count": len(compact_entries),
        "entries": compact_entries,
    }


def generate_summary(all_subsystem_counts: dict[str, int]) -> dict:
    """Generate SUMMARY.json (tier 1). Must be < 500 bytes."""
    return {
        "v": "1.0",
        "date": TODAY,
        "counts": all_subsystem_counts,
        "total": sum(all_subsystem_counts.values()),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_subsystem(subsystem: str, disk_files: list[str], entries: list[dict]) -> dict:
    """Validate a subsystem and return issues."""
    issues = {
        "orphaned": [],   # on disk, not indexed
        "dangling": [],   # indexed, not on disk
        "duplicates": [], # duplicate IDs
    }

    entry_files = {e["file"] for e in entries}
    entry_ids = [e["id"] for e in entries]

    # Check for orphans (disk files not in entries)
    for f in disk_files:
        if f not in entry_files:
            # Skip the generator itself
            if os.path.basename(f) != "index_generator.py":
                issues["orphaned"].append(f)

    # Check for dangling (entry files not on disk)
    disk_set = set(disk_files)
    for e in entries:
        if e["file"] not in disk_set:
            issues["dangling"].append({"id": e["id"], "file": e["file"]})

    # Check for duplicate IDs
    seen_ids = set()
    for eid in entry_ids:
        if eid in seen_ids:
            issues["duplicates"].append(eid)
        seen_ids.add(eid)

    return issues


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def process_subsystem(subsystem: str, dry_run: bool = False) -> dict:
    """Process one subsystem: scan, build entries, generate indexes, validate."""
    cfg = SUBSYSTEMS[subsystem]
    disk_files = scan_disk_files(subsystem)

    # Build entries
    if subsystem == "KB":
        entries = [e for f in disk_files if (e := build_kb_entry(f)) is not None]
    elif subsystem == "SRC":
        entries = [e for f in disk_files if (e := build_src_entry(f)) is not None]
    elif subsystem == "SOP":
        entries = [e for f in disk_files if (e := build_sop_entry(f)) is not None]
    elif subsystem == "SCR":
        entries = build_script_entries(disk_files)
    elif subsystem == "DATA":
        entries = build_data_entries(disk_files)
    elif subsystem == "TEAM":
        entries = [e for f in disk_files if (e := build_team_entry(f)) is not None]
    else:
        entries = []

    # Sort entries by ID
    entries.sort(key=lambda e: e.get("id", ""))

    # Generate three-tier indexes
    index_data = generate_index(entries)
    compact_data = generate_compact(entries)

    # Validate
    issues = validate_subsystem(subsystem, disk_files, entries)

    # Write files
    index_dir = cfg["index_dir"]
    if not dry_run:
        index_path = index_dir / "index.json"
        compact_path = index_dir / "INDEX_COMPACT.json"

        index_path.write_text(json.dumps(index_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        compact_path.write_text(json.dumps(compact_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "subsystem": subsystem,
        "disk_files": len(disk_files),
        "indexed_entries": len(entries),
        "issues": issues,
        "index_written": not dry_run,
    }


def main():
    parser = argparse.ArgumentParser(description="Universal Index Generator")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    parser.add_argument("--subsystem", type=str, help="Only process this subsystem (KB, SCR, SOP, SRC, DATA, TEAM)")
    parser.add_argument("--validate-only", action="store_true", help="Only validate, no regeneration")
    args = parser.parse_args()

    subsystems_to_process = [args.subsystem] if args.subsystem else list(SUBSYSTEMS.keys())
    dry_run = args.dry_run or args.validate_only

    results = {}
    counts = {}

    print("=" * 60)
    print("UNIVERSAL INDEX GENERATOR")
    print(f"Date: {TODAY}")
    print(f"Mode: {'DRY RUN' if dry_run else 'WRITE'}")
    print("=" * 60)

    for sub in subsystems_to_process:
        if sub not in SUBSYSTEMS:
            print(f"[ERROR] Unknown subsystem: {sub}")
            continue

        print(f"\n--- Processing {sub} ---")
        result = process_subsystem(sub, dry_run=dry_run)
        results[sub] = result
        counts[sub] = result["indexed_entries"]

        print(f"  Files on disk: {result['disk_files']}")
        print(f"  Indexed entries: {result['indexed_entries']}")

        issues = result["issues"]
        if issues["orphaned"]:
            print(f"  [WARN] Orphaned files ({len(issues['orphaned'])}):")
            for f in issues["orphaned"]:
                print(f"    - {f}")
        if issues["dangling"]:
            print(f"  [WARN] Dangling refs ({len(issues['dangling'])}):")
            for d in issues["dangling"]:
                print(f"    - {d['id']}: {d['file']}")
        if issues["duplicates"]:
            print(f"  [WARN] Duplicate IDs ({len(issues['duplicates'])}):")
            for d in issues["duplicates"]:
                print(f"    - {d}")
        if not issues["orphaned"] and not issues["dangling"] and not issues["duplicates"]:
            print("  [OK] No issues found")

    # Generate global SUMMARY.json
    if not dry_run:
        summary = generate_summary(counts)
        summary_locations = [
            REPO_ROOT / "storage" / "data" / "SUMMARY.json",
        ]
        for loc in summary_locations:
            loc.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\n[OK] SUMMARY.json written ({json.dumps(summary)})")

    # Print final report
    print("\n" + "=" * 60)
    print("VALIDATION REPORT")
    print("=" * 60)

    total_orphaned = sum(len(r["issues"]["orphaned"]) for r in results.values())
    total_dangling = sum(len(r["issues"]["dangling"]) for r in results.values())
    total_duplicates = sum(len(r["issues"]["duplicates"]) for r in results.values())
    total_entries = sum(r["indexed_entries"] for r in results.values())

    print(f"Total entries indexed: {total_entries}")
    print(f"Total orphaned files: {total_orphaned}")
    print(f"Total dangling refs: {total_dangling}")
    print(f"Total duplicate IDs: {total_duplicates}")

    if total_orphaned + total_dangling + total_duplicates == 0:
        print("\n[PASS] All indexes are consistent with files on disk.")
    else:
        print(f"\n[FAIL] {total_orphaned + total_dangling + total_duplicates} issues found.")

    # Return results for programmatic use
    return {
        "date": TODAY,
        "mode": "dry_run" if dry_run else "write",
        "results": results,
        "summary": {
            "total_entries": total_entries,
            "total_orphaned": total_orphaned,
            "total_dangling": total_dangling,
            "total_duplicates": total_duplicates,
            "subsystem_counts": counts,
        }
    }


if __name__ == "__main__":
    report = main()
    # Also write report to stdout as JSON for piping
    if "--json" in sys.argv:
        # Convert Path objects etc for JSON serialization
        print(json.dumps(report, indent=2, default=str))
