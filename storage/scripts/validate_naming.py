#!/usr/bin/env python3
"""Validate file naming conventions across the repository.

Checks all files against the naming rules defined in the repository's
naming conventions (SOP-029). Reports violations with file path,
expected pattern, actual name, and severity.

Usage:
    python validate_naming.py [--fix] [--json] [REPO_ROOT]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# Directories to skip entirely
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".claude", ".venv", "venv"}

# Patterns by resource type
RULES = [
    {
        "name": "kb_entry",
        "description": "Knowledge base entries must match KB-NNNN.md",
        "dir_pattern": "knowledge-base/entries",
        "regex": r"^KB-\d{4}\.md$",
        "expected": "KB-NNNN.md",
        "extensions": {".md"},
        "severity": "error",
        "exclude": {".gitkeep"},
    },
    {
        "name": "team_session_log",
        "description": "Team session logs must match TEAM-NNNN.md",
        "dir_pattern": "teams/sessions",
        "regex": r"^TEAM-\d{4}\.md$",
        "expected": "TEAM-NNNN.md",
        "extensions": {".md"},
        "severity": "error",
        "exclude": {".gitkeep"},
    },
    {
        "name": "sop_file",
        "description": "SOPs must match sop_NNN_*.json or SOP-NNNN.json",
        "dir_pattern": "storage/coordination/sops",
        "regex": r"^(sop_\d{3}_[a-z0-9_]+\.json|SOP-\d{4}(_v\d+)?\.json)$",
        "expected": "sop_NNN_descriptor.json or SOP-NNNN.json",
        "extensions": {".json"},
        "severity": "error",
        "exclude": {"index.json"},
    },
    {
        "name": "data_source",
        "description": "Sources must match SRC-NNNN.json or SRC-NNNN-*.json",
        "dir_pattern": "storage/sources",
        "regex": r"^SRC-\d{4}(-[a-z0-9-]+)?\.json$",
        "expected": "SRC-NNNN.json or SRC-NNNN-descriptor.json",
        "extensions": {".json"},
        "severity": "error",
        "exclude": {"index.json", "TEMPLATE.json"},
    },
    {
        "name": "script_py",
        "description": "Python scripts must be snake_case",
        "dir_pattern": "storage/scripts",
        "regex": r"^[a-z][a-z0-9_]*\.py$",
        "expected": "snake_case.py",
        "extensions": {".py"},
        "severity": "error",
        "exclude": set(),
        "recursive": True,
    },
    {
        "name": "coordination_module",
        "description": "Coordination modules must be snake_case.py",
        "dir_pattern": "storage/coordination",
        "regex": r"^[a-z][a-z0-9_]*\.py$",
        "expected": "snake_case.py",
        "extensions": {".py"},
        "severity": "warning",
        "exclude": {"__init__.py"},
        "recursive": False,
    },
    {
        "name": "coordination_test",
        "description": "Coordination tests must match test_*.py",
        "dir_pattern": "storage/coordination/tests",
        "regex": r"^test_[a-z0-9_]+\.py$",
        "expected": "test_*.py",
        "extensions": {".py"},
        "severity": "warning",
        "exclude": {"__init__.py"},
    },
    {
        "name": "coordination_stress_test",
        "description": "Stress tests must match test_stress_*.py",
        "dir_pattern": "storage/coordination/stress-tests",
        "regex": r"^test_stress_[a-z0-9_]+\.py$",
        "expected": "test_stress_*.py",
        "extensions": {".py"},
        "severity": "warning",
        "exclude": {"__init__.py"},
    },
    {
        "name": "template_file",
        "description": "Templates must be TEMPLATE.md or TEMPLATE.json",
        "global": True,
        "regex": r"^TEMPLATE\.(md|json)$",
        "match_pattern": r"(?i)template",
        "expected": "TEMPLATE.md or TEMPLATE.json",
        "severity": "warning",
    },
]


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


def should_skip_dir(dirname):
    """Check if a directory should be skipped."""
    return dirname in SKIP_DIRS


def get_relative_path(filepath, repo_root):
    """Get path relative to repo root."""
    try:
        return str(Path(filepath).relative_to(repo_root))
    except ValueError:
        return str(filepath)


def suggest_fix(filename, rule_name):
    """Suggest a fixed filename based on the rule."""
    if rule_name == "script_py" and filename.endswith(".py"):
        # Convert kebab-case to snake_case
        fixed = filename.replace("-", "_")
        if re.match(r"^[a-z][a-z0-9_]*\.py$", fixed):
            return fixed
    elif rule_name == "template_file":
        ext = Path(filename).suffix
        return f"TEMPLATE{ext}"
    elif rule_name == "sop_file":
        # Try to extract number from old-style names
        match = re.match(r"^sop_(\d{3})_(.+)\.json$", filename)
        if match:
            num = int(match.group(1))
            return f"SOP-{num:04d}.json"
    return None


def check_markdown_naming(filename, rel_dir, repo_root):
    """Check markdown file naming: kebab-case or UPPER_CASE."""
    if filename in {"README.md", "CLAUDE.md", "REFERENCE.md", "DIRECTIONS.md"}:
        return None  # known good
    if re.match(r"^TEMPLATE\.(md|json)$", filename):
        return None
    if re.match(r"^[A-Z][A-Z_]*\.md$", filename):
        return None  # UPPER_CASE is fine
    if re.match(r"^[a-z][a-z0-9-]*\.md$", filename):
        return None  # kebab-case is fine
    if re.match(r"^TEAM-\d{4}.*\.md$", filename):
        return None  # TEAM prefixed files
    if re.match(r"^KB-\d{4}\.md$", filename):
        return None
    if re.match(r"^PICK-\d{4}\.md$", filename):
        return None
    if re.match(r"^PROPOSAL-\d+\.md$", filename):
        return None

    # Check if it's snake_case (violation for .md)
    if "_" in filename and not filename.startswith("TEAM_LOG"):
        suggested = filename.replace("_", "-")
        return {
            "file_path": os.path.join(rel_dir, filename),
            "expected_pattern": "kebab-case.md or UPPER_CASE.md",
            "actual_name": filename,
            "severity": "warning",
            "rule": "markdown_doc",
            "suggested_fix": suggested,
        }
    return None


def validate_directory_rule(rule, repo_root):
    """Validate files in a specific directory against a rule."""
    violations = []
    files_checked = 0

    target_dir = repo_root / rule["dir_pattern"]
    if not target_dir.exists():
        return violations, files_checked

    recursive = rule.get("recursive", False)

    if recursive:
        walker = os.walk(target_dir)
    else:
        # Only check files directly in the target directory
        walker = [(str(target_dir), [], os.listdir(target_dir))]

    for dirpath, dirnames, filenames in walker:
        # Skip excluded directories
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]

        for filename in filenames:
            if filename in rule.get("exclude", set()):
                continue

            ext = Path(filename).suffix
            if "extensions" in rule and ext not in rule["extensions"]:
                continue

            files_checked += 1
            pattern = re.compile(rule["regex"])

            if not pattern.match(filename):
                rel_path = get_relative_path(
                    os.path.join(dirpath, filename), repo_root
                )
                fix = suggest_fix(filename, rule["name"])
                violation = {
                    "file_path": rel_path,
                    "expected_pattern": rule["expected"],
                    "actual_name": filename,
                    "severity": rule["severity"],
                    "rule": rule["name"],
                }
                if fix:
                    violation["suggested_fix"] = fix
                violations.append(violation)

    return violations, files_checked


def validate_global_rules(repo_root):
    """Check global naming rules (markdown docs, templates, etc.)."""
    violations = []
    files_checked = 0

    # Directories with specific rules handled elsewhere
    specific_dirs = {
        "knowledge-base/entries",
        "teams/sessions",
        "storage/coordination/sops",
        "storage/sources",
    }

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]

        rel_dir = get_relative_path(dirpath, repo_root)

        # Skip output directories (team outputs use snake_case.json, which is fine)
        if "/output" in rel_dir or rel_dir.endswith("output"):
            continue

        # Skip directories handled by specific rules
        skip = False
        for sd in specific_dirs:
            if rel_dir == sd or rel_dir.startswith(sd + "/"):
                skip = True
                break
        if skip:
            continue

        for filename in filenames:
            if filename.startswith(".") and filename not in {".gitkeep", ".gitignore"}:
                continue

            ext = Path(filename).suffix

            # Check markdown files for kebab-case or UPPER_CASE
            if ext == ".md":
                files_checked += 1
                v = check_markdown_naming(filename, rel_dir, repo_root)
                if v:
                    violations.append(v)

            # Check for TEMPLATE naming
            if "template" in filename.lower() and filename not in {
                "TEMPLATE.md",
                "TEMPLATE.json",
            }:
                if re.search(r"template", filename, re.IGNORECASE):
                    files_checked += 1
                    fix = suggest_fix(filename, "template_file")
                    violations.append(
                        {
                            "file_path": os.path.join(rel_dir, filename),
                            "expected_pattern": "TEMPLATE.md or TEMPLATE.json",
                            "actual_name": filename,
                            "severity": "warning",
                            "rule": "template_file",
                            "suggested_fix": fix,
                        }
                    )

    return violations, files_checked


def run_validation(repo_root):
    """Run all validation rules and return results."""
    all_violations = []
    total_checked = 0

    # Check directory-specific rules
    for rule in RULES:
        if rule.get("global"):
            continue
        violations, checked = validate_directory_rule(rule, repo_root)
        all_violations.extend(violations)
        total_checked += checked

    # Check global rules
    violations, checked = validate_global_rules(repo_root)
    all_violations.extend(violations)
    total_checked += checked

    return all_violations, total_checked


def format_text_output(violations, total_checked, show_fix=False):
    """Format violations as human-readable text."""
    lines = []
    errors = [v for v in violations if v["severity"] == "error"]
    warnings = [v for v in violations if v["severity"] == "warning"]

    if errors:
        lines.append("ERRORS:")
        lines.append("-" * 60)
        for v in sorted(errors, key=lambda x: x["file_path"]):
            lines.append(f"  {v['file_path']}")
            lines.append(f"    Rule: {v['rule']}")
            lines.append(f"    Expected: {v['expected_pattern']}")
            lines.append(f"    Actual: {v['actual_name']}")
            if show_fix and v.get("suggested_fix"):
                parent = str(Path(v["file_path"]).parent)
                old_path = v["file_path"]
                new_path = os.path.join(parent, v["suggested_fix"])
                lines.append(f"    Fix: mv '{old_path}' '{new_path}'")
            lines.append("")

    if warnings:
        lines.append("WARNINGS:")
        lines.append("-" * 60)
        for v in sorted(warnings, key=lambda x: x["file_path"]):
            lines.append(f"  {v['file_path']}")
            lines.append(f"    Rule: {v['rule']}")
            lines.append(f"    Expected: {v['expected_pattern']}")
            lines.append(f"    Actual: {v['actual_name']}")
            if show_fix and v.get("suggested_fix"):
                parent = str(Path(v["file_path"]).parent)
                old_path = v["file_path"]
                new_path = os.path.join(parent, v["suggested_fix"])
                lines.append(f"    Fix: mv '{old_path}' '{new_path}'")
            lines.append("")

    lines.append("=" * 60)
    lines.append("SUMMARY")
    lines.append(f"  Files checked: {total_checked}")
    lines.append(f"  Errors: {len(errors)}")
    lines.append(f"  Warnings: {len(warnings)}")

    if not errors and not warnings:
        lines.append("  All files pass naming conventions.")

    return "\n".join(lines)


def format_json_output(violations, total_checked):
    """Format violations as JSON."""
    errors = [v for v in violations if v["severity"] == "error"]
    warnings = [v for v in violations if v["severity"] == "warning"]
    return json.dumps(
        {
            "files_checked": total_checked,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "violations": violations,
        },
        indent=2,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Validate file naming conventions across the repository.",
        epilog="Checks files against naming rules from SOP-029. "
        "Exit code 0 if no errors, 1 if errors found.",
    )
    parser.add_argument(
        "repo_root",
        nargs="?",
        default=None,
        help="Path to the repository root (default: auto-detect from cwd)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Print suggested mv commands for violations (does not execute)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results in JSON format",
    )

    args = parser.parse_args()

    repo_root = find_repo_root(args.repo_root)

    if not (repo_root / "CLAUDE.md").exists():
        print(f"Warning: CLAUDE.md not found at {repo_root}", file=sys.stderr)

    violations, total_checked = run_validation(repo_root)

    if args.json_output:
        print(format_json_output(violations, total_checked))
    else:
        print(format_text_output(violations, total_checked, show_fix=args.fix))

    errors = [v for v in violations if v["severity"] == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
