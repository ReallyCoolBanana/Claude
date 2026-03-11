#!/usr/bin/env python3
"""Think-tank convergence script for Proto A coordination.

Reads all team findings from the SQLite ``team_findings`` table and from
team output JSON files on disk, cross-references and deduplicates them,
groups by category and priority, and generates a consolidated improvement
roadmap.  The final report is written to
``storage/coordination/teams/think-tank-report.json``.

Usage:
    python think_tank.py                     # Generate the report
    python think_tank.py --output PATH       # Custom output path
    python think_tank.py --summary           # Print a summary to stdout
    python think_tank.py --category build    # Filter by category

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from collections import defaultdict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_DIR = os.path.join(_SCRIPT_DIR, "db")
_DB_PATH = os.path.join(_DB_DIR, "state.db")
_TEAMS_DIR = os.path.join(_SCRIPT_DIR, "teams")
_DEFAULT_OUTPUT = os.path.join(_TEAMS_DIR, "think-tank-report.json")

# Also scan the repository-level teams directory for output files
_REPO_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
_REPO_TEAMS_DIR = os.path.join(_REPO_ROOT, "teams")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("think_tank")

# ---------------------------------------------------------------------------
# Priority ordering for sorting
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _priority_key(priority: str) -> int:
    """Return a numeric sort key for a priority string."""
    return _PRIORITY_ORDER.get(priority.lower(), 99)


# ---------------------------------------------------------------------------
# Finding normalisation
# ---------------------------------------------------------------------------

def _fingerprint(finding: dict) -> str:
    """Generate a stable fingerprint for deduplication.

    Uses the title (lowered, stripped) plus category to produce a short
    SHA-256 hex digest.  Two findings with the same title and category
    are considered duplicates even if the content differs slightly.
    """
    key = (
        finding.get("category", "").strip().lower()
        + ":"
        + finding.get("title", "").strip().lower()
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _normalise_finding(raw: dict, source: str) -> dict:
    """Normalise a raw finding dict into a canonical form."""
    return {
        "team": raw.get("team", "unknown"),
        "agent_id": raw.get("agent_id", "unknown"),
        "category": raw.get("category", "uncategorised").strip().lower(),
        "title": raw.get("title", "Untitled").strip(),
        "content": raw.get("content", ""),
        "priority": raw.get("priority", "medium").strip().lower(),
        "ts": raw.get("ts", 0.0),
        "source": source,
    }


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _collect_from_sqlite() -> list[dict]:
    """Read all findings from the SQLite team_findings table.

    Uses WAL journal mode and busy_timeout for efficient concurrent access.
    Handles missing DB files and missing tables gracefully.
    """
    if not os.path.exists(_DB_PATH):
        log.warning("Database not found at %s — skipping SQLite source", _DB_PATH)
        return []

    findings = []
    conn = None
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            "SELECT * FROM team_findings ORDER BY ts"
        ).fetchall()
        for row in rows:
            findings.append(_normalise_finding(dict(row), "sqlite"))
        log.info("Collected %d findings from SQLite", len(findings))
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "no such table" in msg:
            log.warning("team_findings table does not exist yet: %s", e)
        elif "locked" in msg or "busy" in msg:
            log.warning("Database busy/locked, could not read findings: %s", e)
        else:
            log.warning("Failed to read team_findings table: %s", e)
    except sqlite3.DatabaseError as e:
        log.error("Database file corrupt or unreadable at %s: %s", _DB_PATH, e)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return findings


def _collect_from_files() -> list[dict]:
    """Scan team output directories for JSON finding files.

    Looks in two locations:
    - storage/coordination/teams/*/output/*.json
    - teams/*/output/*.json (repo-level)
    """
    findings = []
    patterns = [
        os.path.join(_TEAMS_DIR, "*", "output", "*.json"),
        os.path.join(_REPO_TEAMS_DIR, "*", "output", "*.json"),
    ]
    seen_paths: set[str] = set()

    for pattern in patterns:
        for filepath in sorted(glob.glob(pattern)):
            real = os.path.realpath(filepath)
            if real in seen_paths:
                continue
            seen_paths.add(real)

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Skipping %s: %s", filepath, e)
                continue

            # The file can contain a single finding dict or a list of them
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Require at least a title or content field
                if "title" not in item and "content" not in item:
                    continue
                findings.append(
                    _normalise_finding(item, f"file:{filepath}")
                )

    log.info("Collected %d findings from output files", len(findings))
    return findings


# ---------------------------------------------------------------------------
# Deduplication and grouping
# ---------------------------------------------------------------------------

def _deduplicate(findings: list[dict]) -> list[dict]:
    """Remove duplicate findings based on fingerprint.

    When duplicates are found, the one with higher priority wins.
    If priorities match, the most recent one (by timestamp) wins.
    """
    by_fp: dict[str, dict] = {}
    dupe_count = 0

    for f in findings:
        fp = _fingerprint(f)
        if fp in by_fp:
            existing = by_fp[fp]
            # Keep the higher-priority or more-recent finding
            if (_priority_key(f["priority"]) < _priority_key(existing["priority"])
                    or (f["priority"] == existing["priority"]
                        and f["ts"] > existing["ts"])):
                # Merge: note both sources
                sources = existing.get("also_from", [])
                sources.append(existing["source"])
                f["also_from"] = sources
                by_fp[fp] = f
            else:
                sources = existing.get("also_from", [])
                sources.append(f["source"])
                existing["also_from"] = sources
            dupe_count += 1
        else:
            by_fp[fp] = f

    log.info("Deduplicated %d findings (%d duplicates removed)",
             len(by_fp), dupe_count)
    return list(by_fp.values())


def _group_findings(findings: list[dict]) -> dict[str, list[dict]]:
    """Group findings by category, sorted by priority within each group."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        groups[f["category"]].append(f)

    # Sort each group by priority (critical first), then by timestamp
    for cat in groups:
        groups[cat].sort(key=lambda x: (_priority_key(x["priority"]), -x["ts"]))

    return dict(groups)


# ---------------------------------------------------------------------------
# Roadmap generation
# ---------------------------------------------------------------------------

def _generate_roadmap(grouped: dict[str, list[dict]]) -> list[dict]:
    """Generate a prioritised improvement roadmap from grouped findings.

    The roadmap is a flat list of items sorted by priority, each
    referencing the findings that support it.
    """
    roadmap = []

    for category, findings in grouped.items():
        # Group by priority within each category
        by_priority: dict[str, list[dict]] = defaultdict(list)
        for f in findings:
            by_priority[f["priority"]].append(f)

        for priority in sorted(by_priority.keys(), key=_priority_key):
            items = by_priority[priority]
            roadmap.append({
                "category": category,
                "priority": priority,
                "item_count": len(items),
                "titles": [f["title"] for f in items],
                "teams_involved": list({f["team"] for f in items}),
                "action": _suggest_action(category, priority, items),
            })

    # Sort roadmap globally by priority
    roadmap.sort(key=lambda x: _priority_key(x["priority"]))
    return roadmap


def _suggest_action(category: str, priority: str, items: list[dict]) -> str:
    """Generate a suggested action based on category and priority."""
    n = len(items)
    if priority in ("critical", "high"):
        return (f"Address {n} {priority}-priority {category} "
                f"item(s) immediately in the next sprint")
    elif priority == "medium":
        return (f"Schedule {n} {category} improvement(s) for upcoming work")
    else:
        return (f"Track {n} low-priority {category} item(s) for future review")


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    category_filter: str | None = None,
) -> dict:
    """Collect, deduplicate, group, and generate the full report."""
    # Collect from all sources
    all_findings = []
    all_findings.extend(_collect_from_sqlite())
    all_findings.extend(_collect_from_files())

    if not all_findings:
        log.warning("No findings collected from any source")
        return {
            "generated_at": time.time(),
            "total_findings": 0,
            "categories": {},
            "roadmap": [],
            "summary": "No findings available.",
        }

    # Deduplicate
    unique = _deduplicate(all_findings)

    # Filter by category if requested
    if category_filter:
        cat = category_filter.strip().lower()
        unique = [f for f in unique if f["category"] == cat]
        log.info("Filtered to %d findings in category '%s'", len(unique), cat)

    # Group
    grouped = _group_findings(unique)

    # Generate roadmap
    roadmap = _generate_roadmap(grouped)

    # Build category summaries
    categories = {}
    for cat, items in grouped.items():
        priorities = defaultdict(int)
        for item in items:
            priorities[item["priority"]] += 1
        categories[cat] = {
            "total": len(items),
            "by_priority": dict(priorities),
            "teams": list({item["team"] for item in items}),
            "findings": items,
        }

    # Summary statistics
    total = len(unique)
    critical = sum(1 for f in unique if f["priority"] == "critical")
    high = sum(1 for f in unique if f["priority"] == "high")

    summary_parts = [f"{total} unique findings across {len(grouped)} categories"]
    if critical:
        summary_parts.append(f"{critical} critical")
    if high:
        summary_parts.append(f"{high} high-priority")
    summary = ". ".join(summary_parts) + "."

    report = {
        "generated_at": time.time(),
        "total_findings": total,
        "total_categories": len(grouped),
        "priority_breakdown": {
            "critical": critical,
            "high": high,
            "medium": sum(1 for f in unique if f["priority"] == "medium"),
            "low": sum(1 for f in unique if f["priority"] == "low"),
        },
        "categories": categories,
        "roadmap": roadmap,
        "summary": summary,
    }

    return report


def write_report(report: dict, output_path: str) -> str:
    """Write the report to a JSON file.  Returns the path written."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("Report written to %s", output_path)
    return output_path


def print_summary(report: dict) -> None:
    """Print a human-readable summary to stdout."""
    print("=" * 60)
    print("  THINK TANK CONVERGENCE REPORT")
    print("=" * 60)
    print()
    print(f"  Summary: {report['summary']}")
    print()

    breakdown = report.get("priority_breakdown", {})
    print("  Priority Breakdown:")
    for p in ("critical", "high", "medium", "low"):
        count = breakdown.get(p, 0)
        if count:
            marker = " (!)" if p in ("critical", "high") else ""
            print(f"    {p:>10}: {count}{marker}")
    print()

    categories = report.get("categories", {})
    if categories:
        print("  Categories:")
        for cat, info in sorted(categories.items()):
            teams = ", ".join(info.get("teams", []))
            print(f"    [{cat}] {info['total']} findings from: {teams}")
        print()

    roadmap = report.get("roadmap", [])
    if roadmap:
        print("  Improvement Roadmap:")
        for i, item in enumerate(roadmap, 1):
            print(f"    {i}. [{item['priority'].upper()}] {item['category']}: "
                  f"{item['action']}")
            for title in item.get("titles", [])[:3]:
                print(f"       - {title}")
            if len(item.get("titles", [])) > 3:
                extra = len(item["titles"]) - 3
                print(f"       ... and {extra} more")
        print()

    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Think-tank convergence: consolidate team findings",
    )
    parser.add_argument(
        "--output", type=str, default=_DEFAULT_OUTPUT,
        help=f"Output path for the report (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Print a human-readable summary to stdout",
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Filter findings by category",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Generate report without writing to disk",
    )

    args = parser.parse_args()

    report = generate_report(category_filter=args.category)

    if not args.no_write:
        path = write_report(report, args.output)
        print(f"Report written to {path}")

    if args.summary or args.no_write:
        print_summary(report)

    # Return exit code based on critical findings
    critical = report.get("priority_breakdown", {}).get("critical", 0)
    if critical > 0:
        log.warning("%d critical findings require immediate attention", critical)
        sys.exit(1)


if __name__ == "__main__":
    main()
