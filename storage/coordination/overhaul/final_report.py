#!/usr/bin/env python3
"""Final quality gate validator for the data storage overhaul.

Scans ALL index.json files and validates:
- Every file reference resolves to an actual file on disk
- YAML frontmatter in .md files has required fields (id, title, date, type, status)
- JSON data files have required fields
- No duplicate IDs across subsystems
- Date field consistency (canonical 'date', no legacy names)
- Market-research picks have identity fields
- Produces structured JSON report with per-subsystem pass/fail counts

Usage:
    python final_report.py [--repo-root /path/to/repo]
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
# YAML frontmatter parser (no external deps)
# ---------------------------------------------------------------------------

def parse_yaml_frontmatter(filepath: str) -> dict[str, Any] | None:
    """Parse YAML frontmatter from a markdown file. Returns None if no frontmatter."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, IOError):
        return None

    if not content.startswith("---"):
        return None

    end = content.find("\n---", 3)
    if end == -1:
        return None

    frontmatter_text = content[3:end].strip()
    result = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Handle key: value
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', line)
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            # Handle list notation [a, b, c]
            if value.startswith("[") and value.endswith("]"):
                items = [x.strip().strip("'\"") for x in value[1:-1].split(",")]
                result[key] = [x for x in items if x]
            elif value.lower() in ("true", "false"):
                result[key] = value.lower() == "true"
            elif value.startswith('"') and value.endswith('"'):
                result[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                result[key] = value[1:-1]
            else:
                result[key] = value
    return result


# ---------------------------------------------------------------------------
# Check result accumulator
# ---------------------------------------------------------------------------

class CheckResult:
    def __init__(self, subsystem: str, check_name: str, passed: bool,
                 file_path: str = "", detail: str = ""):
        self.subsystem = subsystem
        self.check_name = check_name
        self.passed = passed
        self.file_path = file_path
        self.detail = detail

    def to_dict(self) -> dict:
        d = {
            "subsystem": self.subsystem,
            "check": self.check_name,
            "passed": self.passed,
        }
        if self.file_path:
            d["file"] = self.file_path
        if self.detail:
            d["detail"] = self.detail
        return d


class ReportCollector:
    def __init__(self):
        self.results: list[CheckResult] = []

    def add(self, subsystem: str, check_name: str, passed: bool,
            file_path: str = "", detail: str = ""):
        self.results.append(CheckResult(subsystem, check_name, passed, file_path, detail))

    def summary(self) -> dict:
        subsystems: dict[str, dict] = {}
        for r in self.results:
            if r.subsystem not in subsystems:
                subsystems[r.subsystem] = {"passed": 0, "failed": 0, "failures": []}
            if r.passed:
                subsystems[r.subsystem]["passed"] += 1
            else:
                subsystems[r.subsystem]["failed"] += 1
                subsystems[r.subsystem]["failures"].append(r.to_dict())

        total_pass = sum(s["passed"] for s in subsystems.values())
        total_fail = sum(s["failed"] for s in subsystems.values())
        total = total_pass + total_fail
        pct = (total_pass / total * 100) if total > 0 else 0.0

        # Per-subsystem summary
        per_sub = {}
        for name, data in subsystems.items():
            sub_total = data["passed"] + data["failed"]
            per_sub[name] = {
                "passed": data["passed"],
                "failed": data["failed"],
                "total": sub_total,
                "pass_rate": f"{data['passed'] / sub_total * 100:.1f}%" if sub_total else "N/A",
                "status": "PASS" if data["failed"] == 0 else "FAIL",
                "failures": data["failures"],
            }

        return {
            "overall": {
                "passed": total_pass,
                "failed": total_fail,
                "total": total,
                "pass_rate": f"{pct:.1f}%",
                "status": "PASS" if total_fail == 0 else "FAIL",
            },
            "subsystems": per_sub,
        }


# ---------------------------------------------------------------------------
# Validation: Index file references
# ---------------------------------------------------------------------------

LEGACY_DATE_FIELDS = [
    "last_updated", "created", "timestamp", "generated",
    "created_date", "added", "run_date", "date_added",
]

INDEX_CONFIGS = {
    "knowledge-base": {
        "index_path": "knowledge-base/index.json",
        "entries_key": "entries",
        "file_field": "file",
        "base_dir": "knowledge-base",
        "required_fields": ["id", "title", "file", "date", "type", "status"],
        "id_prefix": "KB-",
    },
    "scripts": {
        "index_path": "storage/scripts/index.json",
        "entries_key": "entries",
        "file_field": "file",
        "base_dir": "storage/scripts",
        "required_fields": ["id", "title", "file", "date", "status"],
        "id_prefix": "SCR-",
    },
    "sources": {
        "index_path": "storage/sources/index.json",
        "entries_key": "entries",
        "file_field": "file",
        "base_dir": "storage/sources",
        "required_fields": ["id", "title", "file", "date", "status"],
        "id_prefix": "SRC-",
    },
    "api-tools": {
        "index_path": "storage/api-tools/index.json",
        "entries_key": "tools",
        "file_field": "path",
        "base_dir": "storage/api-tools",
        "required_fields": ["id", "date"],
        "id_prefix": "TOOL-",
    },
    "sops": {
        "index_path": "storage/coordination/sops/index.json",
        "entries_key": "entries",
        "file_field": "file",
        "base_dir": "storage/coordination/sops",
        "required_fields": ["id", "title", "file", "date", "status"],
        "id_prefix": "SOP-",
    },
    "data": {
        "index_path": "storage/data/index.json",
        "entries_key": "entries",
        "file_field": "file",
        "base_dir": "storage/data",
        "required_fields": ["id", "title", "file", "date", "status"],
        "id_prefix": "DATA-",
    },
    "teams": {
        "index_path": "teams/sessions/index.json",
        "entries_key": "entries",
        "file_field": "file",
        "base_dir": "teams/sessions",
        "required_fields": ["id", "title", "file", "date", "status"],
        "id_prefix": "TEAM-",
    },
    "market-research-picks": {
        "index_path": "market-research/picks/index.json",
        "entries_key": "picks",
        "file_field": "file",
        "base_dir": "market-research/picks",
        "required_fields": ["id", "title", "file", "date", "type", "status"],
        "id_prefix": "PICK-",
    },
}


def validate_index_references(repo_root: str, collector: ReportCollector):
    """Validate that every file reference in every index.json resolves."""
    all_ids: dict[str, list[str]] = {}  # id -> [subsystem, ...]

    for subsystem, cfg in INDEX_CONFIGS.items():
        index_path = os.path.join(repo_root, cfg["index_path"])
        if not os.path.isfile(index_path):
            collector.add(subsystem, "index_exists", False,
                          cfg["index_path"], "Index file not found")
            continue

        collector.add(subsystem, "index_exists", True, cfg["index_path"])

        try:
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            collector.add(subsystem, "index_parseable", False,
                          cfg["index_path"], f"JSON parse error: {e}")
            continue

        collector.add(subsystem, "index_parseable", True, cfg["index_path"])

        entries = data.get(cfg["entries_key"], [])
        if not entries:
            collector.add(subsystem, "entries_exist", False,
                          cfg["index_path"], f"No entries found under key '{cfg['entries_key']}'")
            continue

        collector.add(subsystem, "entries_exist", True,
                      cfg["index_path"], f"{len(entries)} entries found")

        base_dir = os.path.join(repo_root, cfg["base_dir"])

        for entry in entries:
            entry_id = entry.get("id", "UNKNOWN")

            # Track for duplicate detection
            if entry_id not in all_ids:
                all_ids[entry_id] = []
            all_ids[entry_id].append(subsystem)

            # Check required fields
            for field in cfg["required_fields"]:
                has_field = field in entry and entry[field] not in (None, "", [])
                collector.add(subsystem, f"entry_has_{field}",
                              has_field, f"{cfg['index_path']}#{entry_id}",
                              "" if has_field else f"Missing required field '{field}'")

            # Check file reference resolves
            file_ref = entry.get(cfg["file_field"], "")
            if file_ref:
                # Try multiple resolution paths
                candidates = [
                    os.path.join(base_dir, file_ref),
                    os.path.join(repo_root, file_ref),
                    os.path.join(repo_root, cfg["base_dir"], file_ref),
                ]
                found = any(os.path.exists(c) for c in candidates)
                collector.add(subsystem, "file_ref_resolves",
                              found, file_ref,
                              "" if found else f"File not found: {file_ref} (tried {cfg['base_dir']}/)")

            # Check for legacy date fields
            for legacy_field in LEGACY_DATE_FIELDS:
                if legacy_field in entry:
                    collector.add(subsystem, "no_legacy_date_field", False,
                                  f"{cfg['index_path']}#{entry_id}",
                                  f"Legacy date field '{legacy_field}' present (should be 'date')")

    # Duplicate ID detection
    for entry_id, subsystems in all_ids.items():
        if len(subsystems) > 1:
            collector.add("cross-subsystem", "no_duplicate_ids", False,
                          entry_id, f"Duplicate ID found in: {', '.join(subsystems)}")
        else:
            collector.add("cross-subsystem", "no_duplicate_ids", True, entry_id)


# ---------------------------------------------------------------------------
# Validation: YAML frontmatter in .md KB entries
# ---------------------------------------------------------------------------

KB_REQUIRED_FRONTMATTER = ["id", "type", "date", "status"]


def validate_kb_frontmatter(repo_root: str, collector: ReportCollector):
    """Validate YAML frontmatter in all KB-*.md files."""
    entries_dir = os.path.join(repo_root, "knowledge-base", "entries")
    if not os.path.isdir(entries_dir):
        collector.add("kb-frontmatter", "entries_dir_exists", False,
                      entries_dir, "KB entries directory not found")
        return

    collector.add("kb-frontmatter", "entries_dir_exists", True, entries_dir)

    md_files = sorted([f for f in os.listdir(entries_dir)
                       if f.startswith("KB-") and f.endswith(".md")])

    for md_file in md_files:
        filepath = os.path.join(entries_dir, md_file)
        fm = parse_yaml_frontmatter(filepath)

        if fm is None:
            collector.add("kb-frontmatter", "has_frontmatter", False,
                          md_file, "No YAML frontmatter found")
            continue

        collector.add("kb-frontmatter", "has_frontmatter", True, md_file)

        for field in KB_REQUIRED_FRONTMATTER:
            has_field = field in fm and fm[field] not in (None, "", [])
            collector.add("kb-frontmatter", f"fm_has_{field}",
                          has_field, md_file,
                          "" if has_field else f"Frontmatter missing '{field}'")

        # Check for legacy date fields
        for legacy_field in LEGACY_DATE_FIELDS:
            if legacy_field in fm:
                collector.add("kb-frontmatter", "no_legacy_date_field", False,
                              md_file,
                              f"Legacy date field '{legacy_field}' in frontmatter")

        # Validate date format (YYYY-MM-DD)
        date_val = fm.get("date", "")
        if date_val:
            date_ok = bool(re.match(r'^\d{4}-\d{2}-\d{2}$', str(date_val)))
            collector.add("kb-frontmatter", "date_format_ok",
                          date_ok, md_file,
                          "" if date_ok else f"Date '{date_val}' not in YYYY-MM-DD format")


# ---------------------------------------------------------------------------
# Validation: Market research picks identity
# ---------------------------------------------------------------------------

PICK_REQUIRED_FIELDS = ["id", "title", "ticker", "date", "type", "status", "file"]


def validate_picks(repo_root: str, collector: ReportCollector):
    """Validate market-research picks have required identity fields."""
    picks_index = os.path.join(repo_root, "market-research", "picks", "index.json")
    if not os.path.isfile(picks_index):
        collector.add("picks", "index_exists", False, picks_index, "Picks index not found")
        return

    try:
        with open(picks_index, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        collector.add("picks", "index_parseable", False, picks_index, str(e))
        return

    picks = data.get("picks", [])
    for pick in picks:
        pick_id = pick.get("id", "UNKNOWN")
        for field in PICK_REQUIRED_FIELDS:
            has_field = field in pick and pick[field] not in (None, "", [])
            collector.add("picks", f"pick_has_{field}",
                          has_field, f"picks/index.json#{pick_id}",
                          "" if has_field else f"Pick missing '{field}'")

        # Validate file reference
        file_ref = pick.get("file", "")
        if file_ref:
            picks_dir = os.path.join(repo_root, "market-research", "picks")
            found = os.path.isfile(os.path.join(picks_dir, file_ref))
            collector.add("picks", "file_ref_resolves",
                          found, file_ref,
                          "" if found else f"Pick file not found: {file_ref}")


# ---------------------------------------------------------------------------
# Validation: JSON data files structure
# ---------------------------------------------------------------------------

def validate_json_data_files(repo_root: str, collector: ReportCollector):
    """Validate JSON data files in storage/data/ have required fields."""
    data_dir = os.path.join(repo_root, "storage", "data")
    if not os.path.isdir(data_dir):
        collector.add("json-data", "data_dir_exists", False, data_dir)
        return

    json_files = [f for f in os.listdir(data_dir)
                  if f.endswith(".json") and f != "index.json"
                  and not f.startswith("INDEX_") and not f.startswith("SUMMARY")]

    for jf in sorted(json_files):
        filepath = os.path.join(data_dir, jf)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            collector.add("json-data", "file_parseable", False, jf, str(e))
            continue

        collector.add("json-data", "file_parseable", True, jf)

        # Check for legacy date fields in top-level
        if isinstance(data, dict):
            for legacy_field in LEGACY_DATE_FIELDS:
                if legacy_field in data:
                    collector.add("json-data", "no_legacy_date_field", False,
                                  jf, f"Legacy date field '{legacy_field}'")


# ---------------------------------------------------------------------------
# Validation: Source JSON files have required fields
# ---------------------------------------------------------------------------

def validate_source_files(repo_root: str, collector: ReportCollector):
    """Validate individual source JSON files have required fields."""
    sources_dir = os.path.join(repo_root, "storage", "sources")
    if not os.path.isdir(sources_dir):
        return

    json_files = [f for f in os.listdir(sources_dir)
                  if f.startswith("SRC-") and f.endswith(".json")]

    required = ["id", "name", "url", "date"]

    for jf in sorted(json_files):
        filepath = os.path.join(sources_dir, jf)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            collector.add("source-files", "file_parseable", False, jf, str(e))
            continue

        for field in required:
            has_field = field in data and data[field] not in (None, "", [])
            collector.add("source-files", f"has_{field}",
                          has_field, jf,
                          "" if has_field else f"Missing '{field}'")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Final quality gate validator")
    parser.add_argument("--repo-root", default=None, help="Repository root path")
    parser.add_argument("--output", default=None, help="Output JSON report path")
    args = parser.parse_args()

    repo_root = args.repo_root or _find_repo_root()
    output_path = args.output or os.path.join(
        repo_root, "storage", "coordination", "overhaul", "output",
        "final_quality_report.json"
    )

    print(f"[final_report] Repository root: {repo_root}")
    print(f"[final_report] Output: {output_path}")
    print()

    collector = ReportCollector()

    # Run all validations
    print("[1/5] Validating index file references...")
    validate_index_references(repo_root, collector)

    print("[2/5] Validating KB YAML frontmatter...")
    validate_kb_frontmatter(repo_root, collector)

    print("[3/5] Validating market-research picks...")
    validate_picks(repo_root, collector)

    print("[4/5] Validating JSON data files...")
    validate_json_data_files(repo_root, collector)

    print("[5/5] Validating source files...")
    validate_source_files(repo_root, collector)

    # Generate report
    summary = collector.summary()
    report = {
        "title": "Final Quality Gate Report",
        "date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "repo_root": repo_root,
        "summary": summary["overall"],
        "subsystems": summary["subsystems"],
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print summary
    print()
    print("=" * 60)
    print("FINAL QUALITY GATE REPORT")
    print("=" * 60)
    overall = summary["overall"]
    print(f"  Overall: {overall['passed']}/{overall['total']} checks passed ({overall['pass_rate']})")
    print(f"  Status:  {overall['status']}")
    print()

    for name, sub in sorted(summary["subsystems"].items()):
        status_mark = "PASS" if sub["status"] == "PASS" else "FAIL"
        print(f"  [{status_mark}] {name}: {sub['passed']}/{sub['total']} ({sub['pass_rate']})")
        if sub["failures"]:
            # Show up to 5 failures per subsystem
            for fail in sub["failures"][:5]:
                detail = fail.get("detail", "")
                file_info = fail.get("file", "")
                print(f"         - {file_info}: {detail}")
            if len(sub["failures"]) > 5:
                print(f"         ... and {len(sub['failures']) - 5} more failures")
    print()
    print(f"Report written to: {output_path}")

    return 0 if overall["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
