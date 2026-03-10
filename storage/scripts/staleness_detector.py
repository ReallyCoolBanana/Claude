#!/usr/bin/env python3
"""
staleness_detector.py — Detects stale, outdated, or inconsistent data across the repository.

Checks:
1. KB entries with status='experimental' never promoted
2. Entries with known bugs never fixed
3. Iteration 1 vs 2 data gathering regressions
4. Unaddressed handoff recommendations
5. index.json entries referencing non-existent files
6. Frontmatter vs index.json mismatches
7. Overall health report with staleness scores

Pure stdlib — no external dependencies.

Usage:
    python3 staleness_detector.py
    python3 staleness_detector.py --verbose
    python3 staleness_detector.py --json
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_ENTRIES_DIR = os.path.join(BASE_DIR, "knowledge-base", "entries")
KB_INDEX = os.path.join(BASE_DIR, "knowledge-base", "index.json")
TEAM_SESSIONS_DIR = os.path.join(BASE_DIR, "teams", "sessions")
TEAM_INDEX = os.path.join(BASE_DIR, "teams", "sessions", "index.json")
SCRIPTS_DIR = os.path.join(BASE_DIR, "storage", "scripts")
SCRIPTS_INDEX = os.path.join(SCRIPTS_DIR, "index.json")
API_TOOLS_DIR = os.path.join(BASE_DIR, "storage", "api-tools")
API_TOOLS_INDEX = os.path.join(API_TOOLS_DIR, "index.json")
SOURCES_DIR = os.path.join(BASE_DIR, "storage", "sources")
SOURCES_INDEX = os.path.join(SOURCES_DIR, "index.json")
BENCHMARKS_DIR = os.path.join(SCRIPTS_DIR, "data-gathering", "benchmarks")


# ---------------------------------------------------------------------------
# YAML frontmatter parser
# ---------------------------------------------------------------------------
def parse_frontmatter(text):
    """Parse YAML frontmatter. Returns (metadata_dict, body_text)."""
    metadata = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            yaml_block = parts[1].strip()
            body = parts[2].strip()
            for line in yaml_block.split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        items = [x.strip().strip('"').strip("'")
                                 for x in val[1:-1].split(",")]
                        metadata[key] = [x for x in items if x]
                    elif val.startswith('"') and val.endswith('"'):
                        metadata[key] = val[1:-1]
                    else:
                        metadata[key] = val
    return metadata, body


def read_json(filepath):
    """Safely read a JSON file."""
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError):
        return None


def read_file(filepath):
    """Safely read a text file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except (IOError, OSError):
        return None


# ---------------------------------------------------------------------------
# Check 1: KB entry status issues
# ---------------------------------------------------------------------------
def check_kb_status_issues(verbose=False):
    """Find KB entries with status=experimental never promoted, or known bugs."""
    issues = []
    if not os.path.isdir(KB_ENTRIES_DIR):
        return issues

    for fname in sorted(os.listdir(KB_ENTRIES_DIR)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(KB_ENTRIES_DIR, fname)
        text = read_file(fpath)
        if not text:
            continue
        meta, body = parse_frontmatter(text)
        kb_id = meta.get("id", fname.replace(".md", ""))
        status = meta.get("status", "")

        # Check for experimental status
        if status == "experimental":
            issues.append({
                "type": "experimental_not_promoted",
                "severity": "medium",
                "id": kb_id,
                "file": fpath,
                "detail": f"{kb_id} has status='experimental' — may need review for promotion",
            })

        # Check for draft status
        if status == "draft":
            issues.append({
                "type": "draft_not_finalized",
                "severity": "low",
                "id": kb_id,
                "file": fpath,
                "detail": f"{kb_id} has status='draft' — should be finalized or removed",
            })

        # Check for known bugs mentioned in body
        body_lower = body.lower()
        bug_indicators = ["broken", "bug", "broken", "status: broken", "status: degraded",
                          "does not work", "fails", "403", "500 error", "blocked"]
        found_bugs = [ind for ind in bug_indicators if ind in body_lower]
        if found_bugs:
            # Check if there's also a fix mentioned
            fix_indicators = ["fixed", "resolved", "repaired", "patched", "corrected"]
            has_fix = any(fix in body_lower for fix in fix_indicators)
            if not has_fix:
                issues.append({
                    "type": "known_bugs_unfixed",
                    "severity": "high",
                    "id": kb_id,
                    "file": fpath,
                    "detail": f"{kb_id} mentions issues ({', '.join(found_bugs[:3])}) "
                              f"with no fix noted",
                })

    return issues


# ---------------------------------------------------------------------------
# Check 2: Iteration 1 vs 2 regressions
# ---------------------------------------------------------------------------
def check_iteration_regressions(verbose=False):
    """Compare iteration 1 vs 2 benchmark data — flag methods that regressed."""
    issues = []

    # Parse benchmark data from KB entries
    iter1_data = {}
    iter2_data = {}

    # KB-0004 has iteration 1 benchmarks
    kb4_text = read_file(os.path.join(KB_ENTRIES_DIR, "KB-0004.md"))
    if kb4_text:
        _, body = parse_frontmatter(kb4_text)
        # Parse benchmark table
        iter1_data = _parse_benchmark_table(body, "iteration1")

    # KB-0005 has iteration 2 benchmarks
    kb5_text = read_file(os.path.join(KB_ENTRIES_DIR, "KB-0005.md"))
    if kb5_text:
        _, body = parse_frontmatter(kb5_text)
        iter2_data = _parse_benchmark_table(body, "iteration2")

    # Also check TEAM-0005 for iteration 2 data
    team5_text = read_file(os.path.join(TEAM_SESSIONS_DIR, "TEAM-0005.md"))
    if team5_text and not iter2_data:
        _, body = parse_frontmatter(team5_text)
        iter2_data = _parse_benchmark_table(body, "iteration2")

    # Compare methods present in both iterations
    if iter1_data and iter2_data:
        for method in iter1_data:
            if method in iter2_data:
                i1 = iter1_data[method]
                i2 = iter2_data[method]
                # Check quality regression
                if i1.get("quality", 0) > 0 and i2.get("quality", 0) < i1.get("quality", 0):
                    issues.append({
                        "type": "quality_regression",
                        "severity": "medium",
                        "method": method,
                        "detail": f"{method}: quality dropped from {i1['quality']} to "
                                  f"{i2['quality']} between iterations",
                        "iter1_quality": i1["quality"],
                        "iter2_quality": i2["quality"],
                    })
                # Check if method went from working to broken
                if i1.get("data_points", 0) > 0 and i2.get("data_points", 0) == 0:
                    issues.append({
                        "type": "method_broken",
                        "severity": "high",
                        "method": method,
                        "detail": f"{method}: was producing {i1['data_points']} results "
                                  f"in iteration 1, now produces 0",
                    })

    # Check for methods that are still broken across iterations
    if iter2_data:
        for method, data in iter2_data.items():
            if data.get("quality", 0) == 0 and data.get("data_points", 0) == 0:
                issues.append({
                    "type": "method_still_broken",
                    "severity": "medium",
                    "method": method,
                    "detail": f"{method}: quality=0, data_points=0 in latest iteration",
                })

    if not iter1_data and not iter2_data:
        issues.append({
            "type": "no_benchmark_data",
            "severity": "low",
            "detail": "Could not find benchmark data in KB-0004 or KB-0005",
        })

    return issues


def _parse_benchmark_table(body, label):
    """Parse a markdown benchmark table from body text."""
    data = {}
    # Find table rows with | delimiters
    lines = body.split("\n")
    in_table = False
    headers = []

    for line in lines:
        line = line.strip()
        if "|" in line and ("Method" in line or "method" in line.lower()):
            # Header row
            headers = [h.strip().lower() for h in line.split("|") if h.strip()]
            in_table = True
            continue
        if in_table and line.startswith("|") and "---" in line:
            continue  # separator row
        if in_table and "|" in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 2:
                method_name = cols[0].strip("* ")
                entry = {"method": method_name}
                for i, col in enumerate(cols[1:], 1):
                    if i < len(headers):
                        hdr = headers[i]
                        # Extract numeric values
                        nums = re.findall(r'[\d.]+', col)
                        if nums:
                            val = float(nums[0])
                            if "quality" in hdr or "score" in hdr:
                                entry["quality"] = val
                            elif "data" in hdr or "point" in hdr:
                                entry["data_points"] = int(val)
                            elif "time" in hdr or "speed" in hdr:
                                entry["time"] = val
                data[method_name.lower()] = entry
        elif in_table and not line:
            in_table = False

    return data


# ---------------------------------------------------------------------------
# Check 3: Unaddressed handoff recommendations
# ---------------------------------------------------------------------------
def check_handoff_recommendations(verbose=False):
    """Check if handoff recommendations from prior teams were addressed."""
    issues = []
    logs = []

    if not os.path.isdir(TEAM_SESSIONS_DIR):
        return issues

    for fname in sorted(os.listdir(TEAM_SESSIONS_DIR)):
        if not fname.endswith(".md") or not fname.startswith("TEAM-"):
            continue
        fpath = os.path.join(TEAM_SESSIONS_DIR, fname)
        text = read_file(fpath)
        if not text:
            continue
        meta, body = parse_frontmatter(text)
        team_id = meta.get("team_id", fname.replace(".md", ""))
        logs.append({"team_id": team_id, "body": body})

    # Check last team's handoff notes — these are by definition unaddressed
    if logs:
        last_log = logs[-1]
        handoff_match = re.search(
            r'##\s*Handoff\s+Notes.*?\n(.*?)(?=\n##\s|\Z)',
            last_log["body"], re.DOTALL | re.IGNORECASE
        )
        if handoff_match:
            section = handoff_match.group(1)
            bullets = re.findall(r'^\s*(?:\d+\.\s*|\-\s*|\*\s*)(.+)',
                                 section, re.MULTILINE)
            for bullet in bullets:
                clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', bullet.strip())
                issues.append({
                    "type": "latest_handoff_pending",
                    "severity": "info",
                    "source_team": last_log["team_id"],
                    "detail": f"Pending from {last_log['team_id']}: {clean[:120]}",
                })

    return issues


# ---------------------------------------------------------------------------
# Check 4: Broken file references in index.json
# ---------------------------------------------------------------------------
def check_broken_file_references(verbose=False):
    """Flag index.json entries that reference non-existent files."""
    issues = []

    # Check scripts index
    scripts_data = read_json(SCRIPTS_INDEX)
    if scripts_data:
        for script in scripts_data.get("scripts", []):
            filename = script.get("filename", "")
            if filename:
                fpath = os.path.join(SCRIPTS_DIR, filename)
                if not os.path.isfile(fpath):
                    issues.append({
                        "type": "missing_file",
                        "severity": "high",
                        "index": "storage/scripts/index.json",
                        "id": script.get("id", ""),
                        "referenced_file": filename,
                        "detail": f"{script.get('id', '')}: references "
                                  f"'{filename}' which does not exist",
                    })

    # Check KB index — entries should have matching .md files
    kb_data = read_json(KB_INDEX)
    if kb_data:
        for entry in kb_data.get("entries", []):
            kb_id = entry.get("id", "")
            fpath = os.path.join(KB_ENTRIES_DIR, f"{kb_id}.md")
            if not os.path.isfile(fpath):
                issues.append({
                    "type": "missing_file",
                    "severity": "high",
                    "index": "knowledge-base/index.json",
                    "id": kb_id,
                    "referenced_file": f"{kb_id}.md",
                    "detail": f"{kb_id}: index entry exists but {kb_id}.md not found",
                })

    # Check for orphan KB files not in index
    if kb_data and os.path.isdir(KB_ENTRIES_DIR):
        indexed_ids = {e.get("id") for e in kb_data.get("entries", [])}
        for fname in os.listdir(KB_ENTRIES_DIR):
            if fname.endswith(".md"):
                fid = fname.replace(".md", "")
                if fid not in indexed_ids:
                    issues.append({
                        "type": "orphan_file",
                        "severity": "low",
                        "index": "knowledge-base/index.json",
                        "file": fname,
                        "detail": f"{fname} exists but is not in index.json",
                    })

    # Check api-tools index
    api_data = read_json(API_TOOLS_INDEX)
    if api_data:
        for tool in api_data.get("tools", []):
            directory = tool.get("directory", "")
            if directory:
                dpath = os.path.join(API_TOOLS_DIR, directory)
                if not os.path.isdir(dpath):
                    issues.append({
                        "type": "missing_directory",
                        "severity": "high",
                        "index": "storage/api-tools/index.json",
                        "id": tool.get("id", ""),
                        "referenced_dir": directory,
                        "detail": f"{tool.get('id', '')}: references directory "
                                  f"'{directory}' which does not exist",
                    })

    # Check sources index
    sources_data = read_json(SOURCES_INDEX)
    if sources_data:
        for source in sources_data.get("sources", []):
            filename = source.get("filename", "")
            if filename:
                fpath = os.path.join(SOURCES_DIR, filename)
                if not os.path.isfile(fpath):
                    issues.append({
                        "type": "missing_file",
                        "severity": "medium",
                        "index": "storage/sources/index.json",
                        "id": source.get("id", ""),
                        "referenced_file": filename,
                        "detail": f"{source.get('id', '')}: references "
                                  f"'{filename}' which does not exist",
                    })

    return issues


# ---------------------------------------------------------------------------
# Check 5: Frontmatter vs index.json mismatches
# ---------------------------------------------------------------------------
def check_frontmatter_index_mismatches(verbose=False):
    """Flag entries where frontmatter doesn't match index.json."""
    issues = []

    kb_data = read_json(KB_INDEX)
    if not kb_data:
        return issues

    index_entries = {e["id"]: e for e in kb_data.get("entries", []) if "id" in e}

    for fname in sorted(os.listdir(KB_ENTRIES_DIR)) if os.path.isdir(KB_ENTRIES_DIR) else []:
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(KB_ENTRIES_DIR, fname)
        text = read_file(fpath)
        if not text:
            continue
        meta, _ = parse_frontmatter(text)
        kb_id = meta.get("id", fname.replace(".md", ""))

        if kb_id not in index_entries:
            continue

        idx_entry = index_entries[kb_id]

        # Compare fields
        field_checks = [
            ("category", meta.get("category", ""), idx_entry.get("category", "")),
            ("team", meta.get("team", ""), idx_entry.get("team", "")),
            ("status", meta.get("status", ""), idx_entry.get("status", "")),
            ("confidence", meta.get("confidence", ""), idx_entry.get("confidence", "")),
            ("date", meta.get("date", ""), idx_entry.get("date", "")),
        ]

        for field_name, file_val, index_val in field_checks:
            if file_val and index_val and str(file_val) != str(index_val):
                issues.append({
                    "type": "frontmatter_mismatch",
                    "severity": "medium",
                    "id": kb_id,
                    "field": field_name,
                    "file_value": str(file_val),
                    "index_value": str(index_val),
                    "detail": f"{kb_id}: {field_name} is '{file_val}' in file "
                              f"but '{index_val}' in index.json",
                })

        # Compare tags
        file_tags = set(meta.get("tags", []) if isinstance(meta.get("tags"), list) else [])
        idx_tags = set(idx_entry.get("tags", []) if isinstance(idx_entry.get("tags"), list) else [])
        if file_tags and idx_tags and file_tags != idx_tags:
            missing_in_index = file_tags - idx_tags
            extra_in_index = idx_tags - file_tags
            if missing_in_index:
                issues.append({
                    "type": "tag_mismatch",
                    "severity": "low",
                    "id": kb_id,
                    "detail": f"{kb_id}: tags in file but not index: "
                              f"{', '.join(sorted(missing_in_index))}",
                })
            if extra_in_index:
                issues.append({
                    "type": "tag_mismatch",
                    "severity": "low",
                    "id": kb_id,
                    "detail": f"{kb_id}: tags in index but not file: "
                              f"{', '.join(sorted(extra_in_index))}",
                })

        # Compare builds_on
        file_builds = set(meta.get("builds_on", [])
                          if isinstance(meta.get("builds_on"), list) else [])
        idx_builds = set(idx_entry.get("builds_on", [])
                         if isinstance(idx_entry.get("builds_on"), list) else [])
        if file_builds != idx_builds:
            if file_builds and not idx_builds:
                issues.append({
                    "type": "builds_on_mismatch",
                    "severity": "low",
                    "id": kb_id,
                    "detail": f"{kb_id}: has builds_on={sorted(file_builds)} in file "
                              f"but missing from index",
                })
            elif file_builds != idx_builds and idx_builds:
                issues.append({
                    "type": "builds_on_mismatch",
                    "severity": "low",
                    "id": kb_id,
                    "detail": f"{kb_id}: builds_on differs — file: "
                              f"{sorted(file_builds)}, index: {sorted(idx_builds)}",
                })

    return issues


# ---------------------------------------------------------------------------
# Check 6: Orphan scripts (files not in index)
# ---------------------------------------------------------------------------
def check_orphan_scripts(verbose=False):
    """Find .py files in storage/scripts/ not registered in index.json."""
    issues = []
    scripts_data = read_json(SCRIPTS_INDEX)
    if not scripts_data:
        return issues

    indexed_files = set()
    for script in scripts_data.get("scripts", []):
        fname = script.get("filename", "")
        if fname:
            indexed_files.add(fname)

    if os.path.isdir(SCRIPTS_DIR):
        for fname in os.listdir(SCRIPTS_DIR):
            if fname.endswith(".py") and fname != "__init__.py":
                if fname not in indexed_files:
                    issues.append({
                        "type": "orphan_script",
                        "severity": "low",
                        "file": fname,
                        "detail": f"{fname} exists in storage/scripts/ but is not "
                                  f"registered in index.json",
                    })

    return issues


# ---------------------------------------------------------------------------
# Health scoring
# ---------------------------------------------------------------------------
def compute_health_score(all_issues):
    """Compute an overall health score from 0-100."""
    if not all_issues:
        return 100

    severity_weights = {
        "high": 10,
        "medium": 5,
        "low": 2,
        "info": 0,
    }

    total_penalty = sum(
        severity_weights.get(issue.get("severity", "low"), 2)
        for issue in all_issues
    )

    # Score from 0-100, with diminishing penalty
    score = max(0, 100 - total_penalty)
    return score


# ---------------------------------------------------------------------------
# Main detection logic
# ---------------------------------------------------------------------------
def detect_staleness(verbose=False):
    """Run all staleness checks and build report."""
    report = {
        "checks": {},
        "issues": [],
        "summary": {},
    }

    checks = [
        ("kb_status", "KB entry status issues", check_kb_status_issues),
        ("iteration_regressions", "Iteration 1 vs 2 regressions", check_iteration_regressions),
        ("handoff_recommendations", "Unaddressed handoff recommendations", check_handoff_recommendations),
        ("broken_references", "Broken file references in indexes", check_broken_file_references),
        ("frontmatter_mismatches", "Frontmatter vs index.json mismatches", check_frontmatter_index_mismatches),
        ("orphan_scripts", "Orphan scripts not in index", check_orphan_scripts),
    ]

    all_issues = []
    for check_id, check_name, check_fn in checks:
        issues = check_fn(verbose=verbose)
        report["checks"][check_id] = {
            "name": check_name,
            "issue_count": len(issues),
            "issues": issues,
        }
        all_issues.extend(issues)

    report["issues"] = all_issues

    # Compute summary
    severity_counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
    for issue in all_issues:
        sev = issue.get("severity", "low")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    health_score = compute_health_score(all_issues)

    report["summary"] = {
        "total_issues": len(all_issues),
        "by_severity": severity_counts,
        "health_score": health_score,
        "health_grade": (
            "A" if health_score >= 90 else
            "B" if health_score >= 75 else
            "C" if health_score >= 60 else
            "D" if health_score >= 40 else "F"
        ),
        "checks_run": len(checks),
    }

    return report


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def format_text_report(report, verbose=False):
    """Format report for human-readable terminal output."""
    lines = []
    summary = report["summary"]

    lines.append("=" * 70)
    lines.append("STALENESS DETECTOR — REPOSITORY HEALTH REPORT")
    lines.append("=" * 70)
    lines.append(f"Health Score:  {summary['health_score']}/100 "
                 f"(Grade: {summary['health_grade']})")
    lines.append(f"Total Issues:  {summary['total_issues']}")
    lines.append(f"  High:   {summary['by_severity']['high']}")
    lines.append(f"  Medium: {summary['by_severity']['medium']}")
    lines.append(f"  Low:    {summary['by_severity']['low']}")
    lines.append(f"  Info:   {summary['by_severity']['info']}")
    lines.append(f"Checks Run:   {summary['checks_run']}")
    lines.append("")

    for check_id, check_data in report["checks"].items():
        count = check_data["issue_count"]
        status = "PASS" if count == 0 else f"{count} issue(s)"
        lines.append(f"  [{status:>14}] {check_data['name']}")

        if verbose and check_data["issues"]:
            for issue in check_data["issues"]:
                sev = issue.get("severity", "?").upper()
                detail = issue.get("detail", "")
                if len(detail) > 120:
                    detail = detail[:117] + "..."
                lines.append(f"                  [{sev:>6}] {detail}")
            lines.append("")

    if not verbose and summary["total_issues"] > 0:
        lines.append("")
        lines.append("Run with --verbose to see issue details.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Detect stale, outdated, or inconsistent data across the repository.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed issue descriptions")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of formatted text")
    args = parser.parse_args()

    report = detect_staleness(verbose=args.verbose)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_text_report(report, verbose=args.verbose))


if __name__ == "__main__":
    main()
