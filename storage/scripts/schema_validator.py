#!/usr/bin/env python3
"""
schema_validator.py — Validates entries against the unified schema (schema.json).

Reports missing required fields, wrong field names, type mismatches.
Can validate entire directories recursively. Outputs JSON report.

Usage:
    python schema_validator.py <path> [--type KB|SCR|SOP|DATA|SRC|TEAM] [--json]
    python schema_validator.py knowledge-base/entries/          # validate all KB entries
    python schema_validator.py storage/sources/SRC-0001.json    # validate single file
    python schema_validator.py . --recursive                    # validate everything

ID: SCR-SCHEMA-VALIDATOR
"""

import argparse
import json
import os
import re
import sys
import yaml
from pathlib import Path
from datetime import datetime

# Paths relative to repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "storage" / "data" / "schema.json"

# --- Schema definitions (loaded from schema.json or hardcoded fallback) ---

REQUIRED_FIELDS = ["id", "title", "date", "type", "status"]
OPTIONAL_UNIVERSAL = ["team", "tags", "category", "confidence", "builds_on",
                       "description", "cross_references", "version"]

STATUS_VALUES = ["active", "draft", "validated", "superseded", "deprecated"]
CONFIDENCE_VALUES = ["low", "medium", "high", "very-high"]
RESOURCE_TYPES = ["KB", "SCR", "SOP", "DATA", "SRC", "TOOL", "TEAM"]

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")
ID_RE = re.compile(r"^(KB|SCR|SOP|DATA|SRC|TOOL|TEAM)-\d{3,4}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Legacy date fields that should be migrated to "date"
LEGACY_DATE_FIELDS = {
    "created", "created_date", "modified", "generated",
    "timestamp", "added", "last_updated", "run_date"
}

# Per-subsystem known fields (not errors, just extensions)
SUBSYSTEM_FIELDS = {
    "KB": {"role", "content"},
    "SCR": {"language", "filename", "dependencies", "added_by_team", "name"},
    "SOP": {"purpose", "scope", "supersedes"},
    "DATA": {"format", "size_estimate", "data_points", "sources", "filename"},
    "SRC": {"url", "base_url", "api_docs_url", "data_types", "access_type",
            "auth_method", "rate_limits", "response_format", "reliability",
            "last_verified", "added_by_team", "name", "notes"},
    "TEAM": {"team_id", "members", "objective", "parent_team",
             "builds_on_knowledge"},
}


def load_schema():
    """Load unified schema if available."""
    if SCHEMA_PATH.exists():
        with open(SCHEMA_PATH) as f:
            return json.load(f)
    return None


def detect_resource_type(filepath: Path) -> str | None:
    """Infer resource type from file path or content."""
    fp = str(filepath)
    if "/knowledge-base/entries/" in fp or fp.startswith("KB-"):
        return "KB"
    if "/storage/scripts/" in fp:
        return "SCR"
    if "/sops/" in fp and fp.endswith(".json"):
        return "SOP"
    if "/storage/data/" in fp:
        return "DATA"
    if "/storage/sources/" in fp:
        return "SRC"
    if "/teams/" in fp:
        return "TEAM"
    return None


def parse_yaml_frontmatter(filepath: Path) -> dict | None:
    """Extract YAML frontmatter from a Markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    try:
        return yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None


def parse_json_file(filepath: Path) -> dict | None:
    """Load a JSON file."""
    try:
        with open(filepath) as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return None


def extract_metadata(filepath: Path, resource_type: str | None = None) -> tuple[dict | None, str | None]:
    """Extract metadata dict from file. Returns (metadata, detected_type)."""
    if resource_type is None:
        resource_type = detect_resource_type(filepath)

    if filepath.suffix == ".md":
        meta = parse_yaml_frontmatter(filepath)
    elif filepath.suffix == ".json":
        meta = parse_json_file(filepath)
    else:
        return None, resource_type

    return meta, resource_type


def validate_entry(meta: dict, resource_type: str | None, filepath: Path) -> dict:
    """Validate a single entry's metadata. Returns validation report."""
    report = {
        "file": str(filepath),
        "resource_type": resource_type,
        "pass": True,
        "errors": [],
        "warnings": [],
    }

    if meta is None:
        report["pass"] = False
        report["errors"].append("Could not parse metadata from file")
        return report

    # --- Required fields ---
    # For existing entries, we check if they HAVE the field or a legacy equivalent
    for field in REQUIRED_FIELDS:
        if field not in meta:
            # Check legacy equivalents for "date"
            if field == "date":
                has_legacy = any(lf in meta for lf in LEGACY_DATE_FIELDS)
                if has_legacy:
                    legacy_found = [lf for lf in LEGACY_DATE_FIELDS if lf in meta]
                    report["warnings"].append(
                        f"Missing 'date' field but has legacy: {legacy_found}. "
                        f"Run normalize_dates.py to migrate."
                    )
                else:
                    report["pass"] = False
                    report["errors"].append(f"Missing required field: {field}")
            elif field == "type":
                # Many entries use implicit type from directory
                if resource_type:
                    report["warnings"].append(
                        f"Missing 'type' field (inferred as '{resource_type}' from path)"
                    )
                else:
                    report["pass"] = False
                    report["errors"].append(f"Missing required field: {field}")
            elif field == "title":
                # Some entries use "name" instead
                if "name" in meta:
                    report["warnings"].append(
                        "Missing 'title' field but has 'name'. Consider aliasing."
                    )
                else:
                    report["pass"] = False
                    report["errors"].append(f"Missing required field: {field}")
            elif field == "status":
                report["warnings"].append(
                    "Missing 'status' field (defaults to 'active')"
                )
            else:
                report["pass"] = False
                report["errors"].append(f"Missing required field: {field}")

    # --- Field type checks ---
    if "id" in meta:
        if not isinstance(meta["id"], str):
            report["pass"] = False
            report["errors"].append(f"'id' must be string, got {type(meta['id']).__name__}")
        elif not ID_RE.match(meta["id"]):
            report["warnings"].append(
                f"'id' format '{meta['id']}' doesn't match PREFIX-NNNN pattern"
            )

    if "date" in meta:
        if isinstance(meta["date"], str) and not ISO_DATE_RE.match(meta["date"]):
            report["errors"].append(
                f"'date' value '{meta['date']}' is not ISO 8601 (YYYY-MM-DD)"
            )
            report["pass"] = False

    if "status" in meta:
        if meta["status"] not in STATUS_VALUES:
            report["warnings"].append(
                f"'status' value '{meta['status']}' not in {STATUS_VALUES}"
            )

    if "confidence" in meta:
        if meta["confidence"] not in CONFIDENCE_VALUES:
            report["warnings"].append(
                f"'confidence' value '{meta['confidence']}' not in {CONFIDENCE_VALUES}"
            )

    if "tags" in meta:
        if not isinstance(meta["tags"], list):
            report["errors"].append("'tags' must be an array")
            report["pass"] = False
        else:
            for tag in meta["tags"]:
                if isinstance(tag, str) and not TAG_RE.match(tag):
                    report["warnings"].append(f"Tag '{tag}' not lowercase-kebab-case")

    if "builds_on" in meta:
        if not isinstance(meta["builds_on"], list):
            report["errors"].append("'builds_on' must be an array")
            report["pass"] = False

    # --- Legacy date field warnings ---
    for lf in LEGACY_DATE_FIELDS:
        if lf in meta and "date" in meta:
            report["warnings"].append(
                f"Legacy date field '{lf}' present alongside 'date'. "
                f"Remove after migration."
            )

    # --- Title length ---
    title = meta.get("title") or meta.get("name", "")
    if isinstance(title, str) and len(title) > 200:
        report["warnings"].append(f"Title exceeds 200 chars ({len(title)})")

    return report


def find_files(path: Path, resource_type: str | None = None, recursive: bool = True) -> list[Path]:
    """Find validatable files in path."""
    if path.is_file():
        return [path]

    files = []
    patterns = []

    if resource_type == "KB" or resource_type is None:
        patterns.append(("knowledge-base/entries", "KB-*.md"))
    if resource_type == "SRC" or resource_type is None:
        patterns.append(("storage/sources", "SRC-*.json"))
    if resource_type == "SOP" or resource_type is None:
        patterns.append(("storage/coordination/sops", "sop_*.json"))
    if resource_type == "SCR" or resource_type is None:
        # Scripts are in index.json, not individual files
        pass
    if resource_type == "TEAM" or resource_type is None:
        patterns.append(("teams/sessions", "TEAM-*.md"))
        patterns.append(("teams", "TEAM-*.md"))

    # If path is a specific directory, search it directly
    if path.is_dir():
        for f in sorted(path.rglob("*") if recursive else path.glob("*")):
            if f.is_file() and f.suffix in (".md", ".json"):
                # Skip index/summary/compact files
                if f.name in ("index.json", "SUMMARY.json", "INDEX_COMPACT.json"):
                    continue
                files.append(f)
        return files

    # Search from repo root using patterns
    for subdir, pattern in patterns:
        search_dir = REPO_ROOT / subdir
        if search_dir.exists():
            for f in sorted(search_dir.glob(pattern)):
                files.append(f)

    return files


def validate_index_entries(index_path: Path, resource_type: str) -> list[dict]:
    """Validate entries inside an index.json file (for SCR, DATA)."""
    reports = []
    data = parse_json_file(index_path)
    if not data:
        return [{"file": str(index_path), "pass": False,
                 "errors": ["Could not parse index.json"], "warnings": [],
                 "resource_type": resource_type}]

    # Find the entries array
    entries = []
    for key in ("scripts", "tools", "files", "entries", "sources", "sops"):
        if key in data and isinstance(data[key], list):
            entries = data[key]
            break

    for entry in entries:
        report = validate_entry(entry, resource_type, index_path)
        report["entry_id"] = entry.get("id", "unknown")
        report["file"] = f"{index_path}#{entry.get('id', '?')}"
        reports.append(report)

    return reports


def run_validation(path: str, resource_type: str | None = None,
                   recursive: bool = True) -> dict:
    """Run validation and return full report."""
    target = Path(path).resolve()
    if not target.exists():
        # Try relative to repo root
        target = REPO_ROOT / path
    if not target.exists():
        return {"error": f"Path not found: {path}", "files": [], "summary": {}}

    all_reports = []

    # Validate individual files
    files = find_files(target, resource_type, recursive)
    for f in files:
        meta, rtype = extract_metadata(f, resource_type)
        report = validate_entry(meta, rtype, f)
        all_reports.append(report)

    # Also validate index.json entries if we're scanning directories
    if target.is_dir() or str(target) == str(REPO_ROOT):
        index_paths = [
            (REPO_ROOT / "storage/scripts/index.json", "SCR"),
            (REPO_ROOT / "storage/data/index.json", "DATA"),
            (REPO_ROOT / "storage/sources/index.json", "SRC"),
        ]
        for idx_path, rtype in index_paths:
            if idx_path.exists() and (resource_type is None or resource_type == rtype):
                all_reports.extend(validate_index_entries(idx_path, rtype))

    # Summary
    total = len(all_reports)
    passed = sum(1 for r in all_reports if r["pass"])
    failed = total - passed
    warning_count = sum(len(r.get("warnings", [])) for r in all_reports)
    error_count = sum(len(r.get("errors", [])) for r in all_reports)

    return {
        "validated_at": datetime.now().isoformat(),
        "path": str(target),
        "resource_type_filter": resource_type,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "warnings": warning_count,
            "errors": error_count,
        },
        "files": all_reports,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate entries against unified schema"
    )
    parser.add_argument("path", help="File or directory to validate")
    parser.add_argument("--type", choices=RESOURCE_TYPES,
                        help="Filter by resource type")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON (default: human-readable)")
    parser.add_argument("--recursive", action="store_true", default=True,
                        help="Recurse into subdirectories")
    parser.add_argument("--errors-only", action="store_true",
                        help="Only show entries with errors")
    args = parser.parse_args()

    # Try to import yaml, fall back gracefully
    try:
        import yaml as _
    except ImportError:
        print("WARNING: PyYAML not installed. Markdown frontmatter parsing disabled.",
              file=sys.stderr)

    report = run_validation(args.path, args.type, args.recursive)

    if args.errors_only:
        report["files"] = [f for f in report["files"] if not f["pass"]]

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        s = report["summary"]
        print(f"Schema Validation: {args.path}")
        print(f"  Total: {s['total']} | Passed: {s['passed']} | "
              f"Failed: {s['failed']} | Warnings: {s['warnings']}")
        print()
        for f in report["files"]:
            status = "PASS" if f["pass"] else "FAIL"
            print(f"  [{status}] {f['file']}")
            for e in f.get("errors", []):
                print(f"         ERROR: {e}")
            for w in f.get("warnings", []):
                print(f"         WARN:  {w}")

    sys.exit(0 if report["summary"]["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
