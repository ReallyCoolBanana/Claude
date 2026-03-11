#!/usr/bin/env python3
"""
kb_validator.py — Knowledge Base Validator (SCR-0012)

Validates knowledge base entries and index.json consistency.
Checks YAML frontmatter, required fields, cross-references,
index completeness, tag consistency, and team ID format.

Usage:
    python3 kb_validator.py [--kb-dir PATH]

Output: Structured JSON validation report to stdout.
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

    Returns (metadata_dict, errors_list). Uses a simple parser
    since we cannot import external YAML libraries.
    """
    errors = []
    metadata = {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return metadata, [f"Cannot read file: {e}"]

    # Check for frontmatter delimiters.
    # Use a more robust approach: find the first line that is exactly '---',
    # then find the next '---' line.  This avoids false splits on '---'
    # that appear inside YAML values.
    if not content.startswith("---"):
        return metadata, ["No YAML frontmatter found (missing opening ---)"]

    lines_all = content.split("\n")
    # First line is '---'; find the closing '---'.
    closing_idx = None
    for idx, ln in enumerate(lines_all[1:], start=1):
        if ln.strip() == "---":
            closing_idx = idx
            break

    if closing_idx is None:
        return metadata, ["Malformed YAML frontmatter (missing closing ---)"]

    yaml_text = "\n".join(lines_all[1:closing_idx]).strip()
    if not yaml_text:
        return metadata, ["Empty YAML frontmatter"]

    # Parse YAML key: value pairs.
    # Limitation: multi-line values (block scalars with | or >) are NOT
    # supported by this simple parser.  Such values will be parsed only
    # up to the first line.  Use a proper YAML library for full support.
    for line in yaml_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        colon_idx = line.find(":")
        if colon_idx == -1:
            errors.append(f"Invalid YAML line (no colon): {line!r}")
            continue

        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()

        # Parse list values: [item1, item2]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if inner:
                items = [item.strip().strip('"').strip("'") for item in inner.split(",")]
                metadata[key] = items
            else:
                metadata[key] = []
        # Parse quoted strings
        elif (value.startswith('"') and value.endswith('"')) or \
             (value.startswith("'") and value.endswith("'")):
            metadata[key] = value[1:-1]
        # Parse null
        elif value.lower() in ("null", "~", ""):
            metadata[key] = None
        else:
            metadata[key] = value

    return metadata, errors


def validate_entry(filepath, all_kb_ids):
    """Validate a single KB entry file. Returns list of issues."""
    issues = []
    filename = os.path.basename(filepath)

    metadata, parse_errors = parse_yaml_frontmatter(filepath)
    for e in parse_errors:
        issues.append({"level": "error", "file": filename, "message": e})

    if not metadata:
        return metadata, issues

    # Check required fields
    required_fields = ["id", "date", "team", "category", "tags", "status", "confidence"]
    for field in required_fields:
        if field not in metadata:
            issues.append({
                "level": "error",
                "file": filename,
                "message": f"Missing required field: {field}"
            })

    # Validate ID format: KB-XXXX
    kb_id = metadata.get("id", "")
    if kb_id and not re.match(r"^KB-\d{4}$", kb_id):
        issues.append({
            "level": "warning",
            "file": filename,
            "message": f"Non-standard KB ID format: {kb_id!r} (expected KB-XXXX)"
        })

    # Check filename matches ID
    expected_filename = f"{kb_id}.md" if kb_id else None
    if expected_filename and filename != expected_filename:
        issues.append({
            "level": "warning",
            "file": filename,
            "message": f"Filename {filename!r} doesn't match ID {kb_id!r} (expected {expected_filename})"
        })

    # Validate date format: YYYY-MM-DD
    date_val = metadata.get("date", "")
    if date_val and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_val)):
        issues.append({
            "level": "error",
            "file": filename,
            "message": f"Invalid date format: {date_val!r} (expected YYYY-MM-DD)"
        })

    # Validate team ID format: TEAM-XXXX or TEAM-X (some legacy)
    team_val = metadata.get("team", "")
    if team_val and not re.match(r"^TEAM-\d{1,4}$", str(team_val)):
        issues.append({
            "level": "warning",
            "file": filename,
            "message": f"Non-standard team ID format: {team_val!r} (expected TEAM-XXXX)"
        })

    # Validate category
    valid_categories = ["methodology", "tool-usage", "debugging", "optimization", "integration", "market-research"]
    category = metadata.get("category", "")
    if category and category not in valid_categories:
        issues.append({
            "level": "warning",
            "file": filename,
            "message": f"Unknown category: {category!r} (valid: {valid_categories})"
        })

    # Validate status
    valid_statuses = ["validated", "experimental", "deprecated"]
    status = metadata.get("status", "")
    if status and status not in valid_statuses:
        issues.append({
            "level": "error",
            "file": filename,
            "message": f"Invalid status: {status!r} (valid: {valid_statuses})"
        })

    # Validate confidence
    valid_confidences = ["high", "medium", "low"]
    confidence = metadata.get("confidence", "")
    if confidence and confidence not in valid_confidences:
        issues.append({
            "level": "error",
            "file": filename,
            "message": f"Invalid confidence: {confidence!r} (valid: {valid_confidences})"
        })

    # Validate tags is a list
    tags = metadata.get("tags", [])
    if not isinstance(tags, list):
        issues.append({
            "level": "error",
            "file": filename,
            "message": f"Tags should be a list, got: {type(tags).__name__}"
        })

    # Validate builds_on references
    builds_on = metadata.get("builds_on", [])
    if builds_on is None:
        builds_on = []
    if isinstance(builds_on, str):
        builds_on = [builds_on]
    if isinstance(builds_on, list):
        for ref in builds_on:
            if ref and ref not in all_kb_ids:
                issues.append({
                    "level": "error",
                    "file": filename,
                    "message": f"builds_on reference {ref!r} does not exist"
                })

    return metadata, issues


def validate_index(index_path, entries_metadata):
    """Validate index.json against actual entry files."""
    issues = []

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)
    except FileNotFoundError:
        return [{"level": "error", "file": "index.json", "message": "index.json not found"}]
    except json.JSONDecodeError as e:
        return [{"level": "error", "file": "index.json", "message": f"Invalid JSON: {e}"}]

    index_entries = index_data.get("entries", [])
    index_ids = {e["id"] for e in index_entries if "id" in e}
    file_ids = {m.get("id") for m in entries_metadata.values() if m.get("id")}

    # Check entry_count
    declared_count = index_data.get("entry_count", 0)
    actual_count = len(index_entries)
    if declared_count != actual_count:
        issues.append({
            "level": "error",
            "file": "index.json",
            "message": f"entry_count is {declared_count} but {actual_count} entries found in array"
        })

    # Entries in index but not as files
    for idx_id in index_ids:
        if idx_id not in file_ids:
            issues.append({
                "level": "error",
                "file": "index.json",
                "message": f"Index references {idx_id} but no matching .md file found"
            })

    # Files that exist but aren't in index
    for fid in file_ids:
        if fid not in index_ids:
            issues.append({
                "level": "error",
                "file": "index.json",
                "message": f"File with ID {fid} exists but is not in index.json"
            })

    # Validate categories mapping
    categories = index_data.get("categories", {})
    for cat, cat_ids in categories.items():
        for cid in cat_ids:
            if cid not in index_ids:
                issues.append({
                    "level": "error",
                    "file": "index.json",
                    "message": f"Category {cat!r} references {cid} which is not in entries"
                })

    # Check all entries are in their correct category
    for entry in index_entries:
        eid = entry.get("id", "")
        ecat = entry.get("category", "")
        if ecat in categories:
            if eid not in categories[ecat]:
                issues.append({
                    "level": "warning",
                    "file": "index.json",
                    "message": f"Entry {eid} has category {ecat!r} but is not listed in categories[{ecat!r}]"
                })

    # Validate tag_index completeness
    tag_index = index_data.get("tag_index", {})

    # Build expected tag_index from entries
    expected_tags = {}
    for entry in index_entries:
        eid = entry.get("id", "")
        for tag in entry.get("tags", []):
            expected_tags.setdefault(tag, set()).add(eid)

    # Check tag_index has all expected tags
    for tag, expected_ids in expected_tags.items():
        if tag not in tag_index:
            issues.append({
                "level": "error",
                "file": "index.json",
                "message": f"Tag {tag!r} used by {expected_ids} but missing from tag_index"
            })
        else:
            actual_ids = set(tag_index[tag])
            missing = expected_ids - actual_ids
            if missing:
                issues.append({
                    "level": "error",
                    "file": "index.json",
                    "message": f"Tag {tag!r} missing entries: {missing}"
                })
            extra = actual_ids - expected_ids
            if extra:
                issues.append({
                    "level": "warning",
                    "file": "index.json",
                    "message": f"Tag {tag!r} has extra entries in tag_index: {extra}"
                })

    # Check for tags in tag_index not used by any entry
    for tag in tag_index:
        if tag not in expected_tags:
            issues.append({
                "level": "warning",
                "file": "index.json",
                "message": f"Tag {tag!r} in tag_index but not used by any entry"
            })

    # Cross-check index entry fields against file metadata
    for entry in index_entries:
        eid = entry.get("id", "")
        # Find matching file metadata
        for fpath, meta in entries_metadata.items():
            if meta.get("id") == eid:
                # Check category consistency
                if entry.get("category") != meta.get("category"):
                    issues.append({
                        "level": "warning",
                        "file": "index.json",
                        "message": f"{eid}: index category {entry.get('category')!r} != file category {meta.get('category')!r}"
                    })
                # Check team consistency
                if entry.get("team") != meta.get("team"):
                    issues.append({
                        "level": "warning",
                        "file": "index.json",
                        "message": f"{eid}: index team {entry.get('team')!r} != file team {meta.get('team')!r}"
                    })
                break

    return issues


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate KB entries and index.json")
    parser.add_argument("--kb-dir", default=None, help="Path to knowledge-base directory")
    args = parser.parse_args()

    root = find_repo_root()
    kb_dir = Path(args.kb_dir) if args.kb_dir else root / "knowledge-base"
    entries_dir = kb_dir / "entries"
    index_path = kb_dir / "index.json"

    all_issues = []
    entries_metadata = {}

    # Scan all .md files in entries/
    if entries_dir.is_dir():
        md_files = sorted(entries_dir.glob("*.md"))
    else:
        md_files = []
        all_issues.append({
            "level": "error",
            "file": "entries/",
            "message": f"Entries directory not found: {entries_dir}"
        })

    # First pass: collect all KB IDs
    all_kb_ids = set()
    for mdfile in md_files:
        meta, _ = parse_yaml_frontmatter(str(mdfile))
        if meta.get("id"):
            all_kb_ids.add(meta["id"])

    # Second pass: full validation
    for mdfile in md_files:
        meta, issues = validate_entry(str(mdfile), all_kb_ids)
        entries_metadata[str(mdfile)] = meta
        all_issues.extend(issues)

    # Validate index.json
    if index_path.exists():
        index_issues = validate_index(str(index_path), entries_metadata)
        all_issues.extend(index_issues)
    else:
        all_issues.append({
            "level": "error",
            "file": "index.json",
            "message": "index.json does not exist"
        })

    # Build summary
    errors = [i for i in all_issues if i["level"] == "error"]
    warnings = [i for i in all_issues if i["level"] == "warning"]

    report = {
        "validator": "kb_validator",
        "version": "1.0",
        "kb_directory": str(kb_dir),
        "files_scanned": len(md_files),
        "summary": {
            "total_issues": len(all_issues),
            "errors": len(errors),
            "warnings": len(warnings),
            "status": "PASS" if len(errors) == 0 else "FAIL"
        },
        "issues": all_issues
    }

    print(json.dumps(report, indent=2))
    return 0 if len(errors) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
