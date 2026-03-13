#!/usr/bin/env python3
"""
normalize_dates.py — Migrates all legacy date field variants to standard "date" field.

Handles 5 legacy variants: created, created_date, modified, generated, timestamp,
plus added, last_updated, run_date.

Targets:
  - JSON files: rewrites with "date" field, removes legacy field
  - Markdown YAML frontmatter: updates frontmatter block
  - Index files: updates entries within arrays

Modes:
  --dry-run     Show changes without writing (default)
  --apply       Write changes to disk
  --report      JSON report of all fields found

Usage:
    python normalize_dates.py --dry-run           # preview all changes
    python normalize_dates.py --apply             # apply changes
    python normalize_dates.py --report --json     # JSON report
    python normalize_dates.py --path storage/     # target specific directory

ID: SCR-NORMALIZE-DATES
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Legacy date fields to normalize (in priority order for value selection)
LEGACY_DATE_FIELDS = [
    "created_date",  # most specific
    "created",
    "modified",
    "generated",
    "timestamp",
    "added",
    "last_updated",
    "run_date",
]

# Fields to preserve as secondary (not normalize away)
PRESERVE_FIELDS = {"last_verified"}  # SRC entries keep this

# ISO 8601 patterns
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?$")


def normalize_date_value(value) -> str | None:
    """Convert a date value to ISO 8601 string."""
    if isinstance(value, str):
        # Already ISO 8601
        if ISO_DATE_RE.match(value):
            # Strip timezone suffix for consistency, keep date or datetime
            if "T" in value:
                # Remove timezone: Z, +HH:MM, -HH:MM (only after time portion)
                clean = value.rstrip("Z")
                # Remove offset like +05:00 or -04:00 at end
                offset_match = re.search(r"[+-]\d{2}:\d{2}$", clean)
                if offset_match:
                    clean = clean[:offset_match.start()]
                return clean
            return value
        # Try common formats
        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                     "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y"]:
            try:
                dt = datetime.strptime(value, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        # If it looks like "auto" or non-date, skip
        if value.lower() in ("auto", "none", ""):
            return None
    elif isinstance(value, (int, float)):
        # Unix timestamp
        try:
            dt = datetime.fromtimestamp(value)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, OSError):
            return None
    return None


def find_legacy_fields(data: dict) -> list[tuple[str, any]]:
    """Find legacy date fields in a dict."""
    found = []
    for field in LEGACY_DATE_FIELDS:
        if field in data:
            found.append((field, data[field]))
    return found


def normalize_dict(data: dict, changes_log: list, filepath: str, entry_id: str = "") -> bool:
    """Normalize date fields in a dict. Returns True if changed."""
    changed = False
    legacy = find_legacy_fields(data)

    if not legacy:
        return False

    # If "date" already exists, just remove legacy fields
    if "date" in data:
        for field, value in legacy:
            if field not in PRESERVE_FIELDS:
                changes_log.append({
                    "file": filepath,
                    "entry_id": entry_id,
                    "action": "remove_legacy",
                    "field": field,
                    "value": str(value),
                    "reason": "'date' already present",
                })
                del data[field]
                changed = True
        return changed

    # No "date" field — pick best legacy value
    best_value = None
    best_field = None
    for field, value in legacy:
        normalized = normalize_date_value(value)
        if normalized and (best_value is None or normalized > best_value):
            best_value = normalized
            best_field = field

    if best_value:
        data["date"] = best_value
        changes_log.append({
            "file": filepath,
            "entry_id": entry_id,
            "action": "add_date",
            "field": "date",
            "value": best_value,
            "source_field": best_field,
        })
        changed = True

        # Remove all legacy fields
        for field, value in legacy:
            if field not in PRESERVE_FIELDS:
                changes_log.append({
                    "file": filepath,
                    "entry_id": entry_id,
                    "action": "remove_legacy",
                    "field": field,
                    "value": str(value),
                })
                del data[field]

    return changed


def process_json_file(filepath: Path, changes_log: list, apply: bool) -> bool:
    """Process a single JSON file."""
    try:
        with open(filepath) as f:
            data = json.load(f)
    except (json.JSONDecodeError, Exception):
        return False

    if not isinstance(data, dict):
        return False

    changed = False
    fp_str = str(filepath)

    # Normalize top-level
    changed |= normalize_dict(data, changes_log, fp_str, data.get("id", ""))

    # Normalize entries in arrays (index.json pattern)
    for key in ("scripts", "tools", "files", "entries", "sources", "sops"):
        if key in data and isinstance(data[key], list):
            for entry in data[key]:
                if isinstance(entry, dict):
                    changed |= normalize_dict(
                        entry, changes_log, fp_str,
                        entry.get("id", "")
                    )

    if changed and apply:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    return changed


def process_markdown_file(filepath: Path, changes_log: list, apply: bool) -> bool:
    """Process a Markdown file with YAML frontmatter."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return False

    if not text.startswith("---"):
        return False

    end = text.find("---", 3)
    if end == -1:
        return False

    frontmatter_text = text[3:end]
    body = text[end:]  # includes the closing ---

    try:
        import yaml
        meta = yaml.safe_load(frontmatter_text)
    except Exception:
        return False

    if not isinstance(meta, dict):
        return False

    fp_str = str(filepath)
    changes_before = len(changes_log)
    changed = normalize_dict(meta, changes_log, fp_str, meta.get("id", ""))

    if changed and apply:
        try:
            import yaml
            new_frontmatter = yaml.dump(meta, default_flow_style=False,
                                         allow_unicode=True, sort_keys=False)
            new_text = "---\n" + new_frontmatter + body
            filepath.write_text(new_text, encoding="utf-8")
        except Exception as e:
            changes_log.append({
                "file": fp_str,
                "action": "error",
                "message": f"Failed to write: {e}",
            })
            return False

    return changed


def scan_directory(path: Path, changes_log: list, apply: bool) -> dict:
    """Scan a directory tree for files with legacy date fields."""
    stats = {"files_scanned": 0, "files_changed": 0, "json_files": 0, "md_files": 0}

    for root, dirs, files in os.walk(path):
        # Skip hidden dirs, node_modules, .git
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]

        for fname in sorted(files):
            fpath = Path(root) / fname

            if fname.endswith(".json"):
                stats["files_scanned"] += 1
                stats["json_files"] += 1
                if process_json_file(fpath, changes_log, apply):
                    stats["files_changed"] += 1

            elif fname.endswith(".md"):
                stats["files_scanned"] += 1
                stats["md_files"] += 1
                if process_markdown_file(fpath, changes_log, apply):
                    stats["files_changed"] += 1

    return stats


def run_normalization(path: str | None = None, apply: bool = False) -> dict:
    """Run date normalization across the repository."""
    target = Path(path).resolve() if path else REPO_ROOT
    if not target.exists():
        target = REPO_ROOT / path if path else REPO_ROOT

    changes_log = []

    if target.is_file():
        stats = {"files_scanned": 1, "files_changed": 0}
        if target.suffix == ".json":
            if process_json_file(target, changes_log, apply):
                stats["files_changed"] = 1
        elif target.suffix == ".md":
            if process_markdown_file(target, changes_log, apply):
                stats["files_changed"] = 1
    else:
        stats = scan_directory(target, changes_log, apply)

    # Summary of legacy fields found
    field_counts = {}
    for c in changes_log:
        if c.get("action") in ("remove_legacy", "add_date"):
            field = c.get("source_field") or c.get("field", "")
            if field and field != "date":
                field_counts[field] = field_counts.get(field, 0) + 1

    return {
        "normalized_at": datetime.now().isoformat(),
        "mode": "apply" if apply else "dry-run",
        "target_path": str(target),
        "stats": stats,
        "legacy_field_counts": field_counts,
        "total_changes": len(changes_log),
        "changes": changes_log,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Normalize legacy date fields to 'date' (ISO 8601)"
    )
    parser.add_argument("--path", default=None,
                        help="Target file or directory (default: repo root)")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to disk (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview changes without writing (default)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON report")
    parser.add_argument("--report", action="store_true",
                        help="Show detailed change report")
    args = parser.parse_args()

    if args.apply:
        args.dry_run = False

    report = run_normalization(args.path, args.apply)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        s = report["stats"]
        mode = report["mode"]
        print(f"Date Normalization ({mode.upper()})")
        print(f"  Target: {report['target_path']}")
        print(f"  Files scanned: {s['files_scanned']}")
        print(f"  Files with changes: {s['files_changed']}")
        print(f"  Total field changes: {report['total_changes']}")
        print()

        if report["legacy_field_counts"]:
            print("  Legacy fields found:")
            for field, count in sorted(report["legacy_field_counts"].items(),
                                        key=lambda x: -x[1]):
                print(f"    {field}: {count} occurrences")
            print()

        if args.report:
            for c in report["changes"]:
                action = c.get("action", "?")
                filepath = c.get("file", "?")
                field = c.get("field", "")
                value = c.get("value", "")
                entry_id = c.get("entry_id", "")
                prefix = f"[{entry_id}] " if entry_id else ""
                if action == "add_date":
                    src = c.get("source_field", "")
                    print(f"  + {prefix}{filepath}: date={value} (from {src})")
                elif action == "remove_legacy":
                    print(f"  - {prefix}{filepath}: remove {field}={value}")
                elif action == "error":
                    print(f"  ! {filepath}: {c.get('message', '')}")

        if mode == "dry-run" and report["total_changes"] > 0:
            print(f"\n  Run with --apply to write {report['total_changes']} changes to disk.")

    sys.exit(0)


if __name__ == "__main__":
    main()
