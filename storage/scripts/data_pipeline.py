#!/usr/bin/env python3
"""Data pipeline automation tool for the research-to-storage workflow.

Automates KB ingestion, validation, search, and health checks.

Usage:
    python3 data_pipeline.py ingest research.json --tags t1,t2 --category optimization
    python3 data_pipeline.py validate
    python3 data_pipeline.py search "query" --tag coordination
    python3 data_pipeline.py health
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
KB_DIR = REPO_ROOT / "knowledge-base"
KB_ENTRIES_DIR = KB_DIR / "entries"
KB_INDEX = KB_DIR / "index.json"
TAXONOMY_PATH = REPO_ROOT / "storage" / "taxonomy.json"
UNIFIED_INDEX = REPO_ROOT / "storage" / "unified_index.json"
REBUILD_SCRIPT = SCRIPT_DIR / "rebuild_unified_index.py"
KB_VALIDATOR = SCRIPT_DIR / "kb_validator.py"
UNIFIED_SEARCH = SCRIPT_DIR / "unified_search.py"

TODAY = datetime.now().strftime("%Y-%m-%d")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_json(path):
    """Load a JSON file, returning None on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: Cannot load {path}: {e}", file=sys.stderr)
        return None


def save_json(path, data):
    """Write data as pretty-printed JSON."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_taxonomy():
    """Load taxonomy.json and build canonical tag set + alias map."""
    tax = load_json(TAXONOMY_PATH)
    if not tax:
        return set(), {}
    canonical = {t["name"] for t in tax.get("tags", [])}
    aliases = dict(tax.get("consolidations", {}))
    return canonical, aliases


def next_kb_id(index_data):
    """Determine the next KB ID by scanning existing entries."""
    max_num = 0
    for entry in index_data.get("entries", []):
        m = re.match(r"KB-(\d+)", entry.get("id", ""))
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"KB-{max_num + 1:04d}"


def resolve_tags(raw_tags, aliases):
    """Resolve alias tags to canonical form."""
    resolved = []
    for tag in raw_tags:
        tag = tag.strip()
        resolved.append(aliases.get(tag, tag))
    return resolved


# ---------------------------------------------------------------------------
# INGEST command
# ---------------------------------------------------------------------------


def cmd_ingest(args):
    """Ingest a research JSON file into the knowledge base."""
    # Load research input
    research = load_json(args.input_file)
    if not research:
        sys.exit(1)

    # Load existing index
    index_data = load_json(KB_INDEX)
    if not index_data:
        sys.exit(1)

    canonical_tags, alias_map = load_taxonomy()

    # Determine new ID
    new_id = next_kb_id(index_data)

    # Resolve tags
    raw_tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    tags = resolve_tags(raw_tags, alias_map)

    # Warn on non-canonical tags
    for tag in tags:
        if tag not in canonical_tags:
            print(f"WARNING: Tag '{tag}' is not in taxonomy.json", file=sys.stderr)

    # Parse builds_on
    builds_on = []
    if args.builds_on:
        builds_on = [b.strip() for b in args.builds_on.split(",")]

    # Extract title and content from research JSON
    title = research.get("title", "Untitled Research")
    context = research.get("context", research.get("summary", "Auto-ingested research."))
    method = research.get("method", "See source research file.")
    result = research.get("result", research.get("findings", "See source research file."))
    lessons = research.get("lessons", research.get("lessons_learned", ""))
    recommendations = research.get("recommendations", "")
    team = research.get("team", "auto-ingest")
    category = args.category or research.get("category", "methodology")
    confidence = research.get("confidence", "medium")
    status = research.get("status", "validated")

    # Build KB markdown entry
    frontmatter_lines = [
        "---",
        f"id: {new_id}",
        f"date: {TODAY}",
        f"team: {team}",
        f"category: {category}",
        f"tags: [{', '.join(tags)}]",
        f"status: {status}",
        f"confidence: {confidence}",
    ]
    if builds_on:
        frontmatter_lines.append(f"builds_on: [{', '.join(builds_on)}]")
    frontmatter_lines.append("---")

    body_sections = [
        f"# {title}",
        "",
        "## Context",
        context,
        "",
        "## Method",
        method,
        "",
        "## Result",
        result,
    ]
    if lessons:
        body_sections += ["", "## Lessons Learned", lessons]
    if recommendations:
        body_sections += ["", "## Recommendations", recommendations]

    md_content = "\n".join(frontmatter_lines) + "\n" + "\n".join(body_sections) + "\n"

    # Write the entry file
    entry_path = KB_ENTRIES_DIR / f"{new_id}.md"
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    with open(entry_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Created entry: {entry_path}")

    # Update index.json
    new_entry = {
        "id": new_id,
        "title": title,
        "date": TODAY,
        "team": team,
        "category": category,
        "tags": tags,
        "status": status,
        "confidence": confidence,
    }
    if builds_on:
        new_entry["builds_on"] = builds_on

    index_data["entries"].append(new_entry)
    index_data["entry_count"] = len(index_data["entries"])
    index_data["last_updated"] = TODAY

    # Update categories index
    cats = index_data.setdefault("categories", {})
    cat_list = cats.setdefault(category, [])
    if new_id not in cat_list:
        cat_list.append(new_id)

    # Update tag_index
    tag_idx = index_data.setdefault("tag_index", {})
    for tag in tags:
        tag_list = tag_idx.setdefault(tag, [])
        if new_id not in tag_list:
            tag_list.append(new_id)

    save_json(KB_INDEX, index_data)
    print(f"Updated index: {KB_INDEX} (now {index_data['entry_count']} entries)")

    # Run rebuild_unified_index.py if it exists
    if REBUILD_SCRIPT.exists():
        print("Rebuilding unified index...")
        result = subprocess.run(
            [sys.executable, str(REBUILD_SCRIPT)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print("Unified index rebuilt successfully.")
        else:
            print(f"WARNING: rebuild_unified_index.py failed:\n{result.stderr}",
                  file=sys.stderr)
    else:
        print(f"WARNING: {REBUILD_SCRIPT} not found, skipping unified index rebuild.",
              file=sys.stderr)

    print(f"\nIngested as {new_id}: {title}")


# ---------------------------------------------------------------------------
# VALIDATE command
# ---------------------------------------------------------------------------


def cmd_validate(args):
    """Run all validators and report on repository health."""
    canonical_tags, alias_map = load_taxonomy()
    index_data = load_json(KB_INDEX)
    if not index_data:
        print("FAIL: Cannot load knowledge-base/index.json", file=sys.stderr)
        sys.exit(1)

    issues = []
    warnings = []

    # 1. Run kb_validator.py if it exists
    if KB_VALIDATOR.exists():
        print("Running kb_validator.py ...")
        result = subprocess.run(
            [sys.executable, str(KB_VALIDATOR)],
            capture_output=True, text=True
        )
        # Try to parse JSON output regardless of exit code
        # (kb_validator exits non-zero when it finds issues)
        try:
            report = json.loads(result.stdout)
            summary = report.get("summary", {})
            err_count = summary.get("errors", 0)
            warn_count = summary.get("warnings", 0)
            if err_count:
                issues.append(f"kb_validator found {err_count} error(s)")
                for item in report.get("issues", []):
                    if item.get("level") == "error":
                        issues.append(
                            f"  - {item.get('file', '?')}: {item.get('message', '?')}"
                        )
            for item in report.get("issues", []):
                if item.get("level") == "warning":
                    warnings.append(
                        f"kb_validator: {item.get('file', '?')}: "
                        f"{item.get('message', '?')}"
                    )
            print(f"  kb_validator: {err_count} errors, {warn_count} warnings")
        except (json.JSONDecodeError, KeyError):
            if result.returncode != 0:
                issues.append(f"kb_validator.py exited with code {result.returncode}")
                if result.stderr:
                    for line in result.stderr.strip().split("\n")[:10]:
                        issues.append(f"  {line}")
            else:
                print(f"  kb_validator ran (non-JSON output)")
    else:
        warnings.append("kb_validator.py not found")

    # 2. Check index JSON validity (already loaded above)
    entries = index_data.get("entries", [])
    indexed_ids = {e["id"] for e in entries}
    print(f"Index entries: {len(entries)}")

    # 3. Check for orphan files (on disk but not in index)
    entry_files = set()
    if KB_ENTRIES_DIR.exists():
        for f in KB_ENTRIES_DIR.iterdir():
            if f.suffix == ".md":
                entry_files.add(f.stem)

    orphans = entry_files - indexed_ids
    phantoms = indexed_ids - entry_files  # in index but no file

    if orphans:
        issues.append(f"Orphan files (on disk, not in index): {sorted(orphans)}")
    if phantoms:
        issues.append(f"Phantom entries (in index, no file): {sorted(phantoms)}")

    # 4. Check tags against taxonomy
    non_canonical = set()
    all_tags_used = set()
    for entry in entries:
        for tag in entry.get("tags", []):
            all_tags_used.add(tag)
            if tag not in canonical_tags and tag not in alias_map:
                non_canonical.add(tag)

    if non_canonical:
        warnings.append(f"Non-canonical tags in use: {sorted(non_canonical)}")

    # 5. Check tag_index consistency
    tag_idx = index_data.get("tag_index", {})
    for tag, ids in tag_idx.items():
        for kb_id in ids:
            if kb_id not in indexed_ids:
                issues.append(f"tag_index[{tag}] references missing entry {kb_id}")

    # Print report
    print("\n--- Validation Report ---")
    print(f"Entries count:       {len(entries)}")
    print(f"Tags in use:         {len(all_tags_used)}")
    print(f"Canonical tags:      {len(canonical_tags)}")
    print(f"Orphan files:        {len(orphans)}")
    print(f"Phantom entries:     {len(phantoms)}")
    print(f"Non-canonical tags:  {len(non_canonical)}")
    print(f"Issues:              {len(issues)}")
    print(f"Warnings:            {len(warnings)}")

    if issues:
        print("\nISSUES:")
        for issue in issues:
            print(f"  [!] {issue}")

    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print(f"  [~] {w}")

    if not issues and not warnings:
        print("\nAll checks passed.")

    return len(issues)


# ---------------------------------------------------------------------------
# SEARCH command
# ---------------------------------------------------------------------------


def cmd_search(args):
    """Delegate search to unified_search.py."""
    if not UNIFIED_SEARCH.exists():
        print(f"ERROR: {UNIFIED_SEARCH} not found", file=sys.stderr)
        sys.exit(1)

    cmd = [sys.executable, str(UNIFIED_SEARCH)]

    if args.query:
        cmd.append(args.query)
    if args.tag:
        cmd.extend(["--tag", args.tag])
    if args.domain:
        cmd.extend(["--domain", args.domain])
    if args.related:
        cmd.extend(["--related", args.related])

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# HEALTH command
# ---------------------------------------------------------------------------


def cmd_health(args):
    """Quick repository health check across all domains."""
    canonical_tags, alias_map = load_taxonomy()

    # Count entries across domains
    domain_counts = {}
    total_entries = 0

    # Knowledge base
    kb_data = load_json(KB_INDEX)
    if kb_data:
        count = len(kb_data.get("entries", []))
        domain_counts["knowledge-base"] = count
        total_entries += count

    # Scripts index
    scripts_idx = load_json(REPO_ROOT / "storage" / "scripts" / "index.json")
    if scripts_idx:
        count = len(scripts_idx.get("scripts", scripts_idx.get("entries", [])))
        domain_counts["scripts"] = count
        total_entries += count

    # Sources index
    sources_idx = load_json(REPO_ROOT / "storage" / "sources" / "index.json")
    if sources_idx:
        count = len(sources_idx.get("sources", sources_idx.get("entries", [])))
        domain_counts["sources"] = count
        total_entries += count

    # API tools index
    api_idx = load_json(REPO_ROOT / "storage" / "api-tools" / "index.json")
    if api_idx:
        count = len(api_idx.get("tools", api_idx.get("entries", [])))
        domain_counts["api-tools"] = count
        total_entries += count

    # Sessions index
    sessions_idx = load_json(REPO_ROOT / "teams" / "sessions" / "index.json")
    if sessions_idx:
        count = len(sessions_idx.get("sessions", sessions_idx.get("entries", [])))
        domain_counts["sessions"] = count
        total_entries += count

    # Tags analysis
    all_tags_used = set()
    if kb_data:
        for entry in kb_data.get("entries", []):
            all_tags_used.update(entry.get("tags", []))

    # Orphan detection (KB entries)
    orphan_count = 0
    if kb_data and KB_ENTRIES_DIR.exists():
        indexed_ids = {e["id"] for e in kb_data.get("entries", [])}
        on_disk = {f.stem for f in KB_ENTRIES_DIR.iterdir() if f.suffix == ".md"}
        orphan_count = len(on_disk - indexed_ids)

    # Staleness: entries older than 90 days
    stale_entries = []
    cutoff = datetime.now() - timedelta(days=90)
    if kb_data:
        for entry in kb_data.get("entries", []):
            try:
                entry_date = datetime.strptime(entry.get("date", ""), "%Y-%m-%d")
                if entry_date < cutoff:
                    stale_entries.append(entry["id"])
            except ValueError:
                pass

    # Index sync: does unified_index.json exist and is it recent?
    unified_status = "missing"
    if UNIFIED_INDEX.exists():
        uni_data = load_json(UNIFIED_INDEX)
        if uni_data:
            uni_updated = uni_data.get("last_updated", "unknown")
            unified_status = f"present (updated: {uni_updated})"
        else:
            unified_status = "present (parse error)"

    # Print health report
    print("=" * 55)
    print("  Repository Health Check")
    print("=" * 55)
    print()

    print("Domain Counts:")
    for domain, count in sorted(domain_counts.items()):
        print(f"  {domain:20s}  {count:4d} entries")
    print(f"  {'TOTAL':20s}  {total_entries:4d} entries")
    print()

    print("Tags:")
    print(f"  Tags in use:       {len(all_tags_used)}")
    print(f"  Canonical tags:    {len(canonical_tags)}")
    non_canonical = all_tags_used - canonical_tags
    if non_canonical:
        print(f"  Non-canonical:     {len(non_canonical)}  {sorted(non_canonical)}")
    else:
        print(f"  Non-canonical:     0")
    print()

    print("Quality:")
    print(f"  Orphan files:      {orphan_count}")
    if stale_entries:
        print(f"  Stale (>90 days):  {len(stale_entries)}  {stale_entries[:5]}")
    else:
        print(f"  Stale (>90 days):  0")
    print()

    print("Index Sync:")
    print(f"  Unified index:     {unified_status}")
    kb_updated = kb_data.get("last_updated", "unknown") if kb_data else "N/A"
    print(f"  KB index updated:  {kb_updated}")
    print()

    if orphan_count == 0 and not stale_entries and not non_canonical:
        print("Status: HEALTHY")
    elif orphan_count > 0 or non_canonical:
        print("Status: NEEDS ATTENTION")
    else:
        print("Status: OK (minor staleness)")

    print("=" * 55)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="data_pipeline",
        description="Data pipeline automation for the research-to-storage workflow.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- ingest --
    p_ingest = subparsers.add_parser("ingest", help="Ingest a research JSON into KB")
    p_ingest.add_argument("input_file", help="Path to research JSON file")
    p_ingest.add_argument("--tags", default="", help="Comma-separated tags")
    p_ingest.add_argument("--category", default=None,
                          help="KB category (e.g. optimization, methodology)")
    p_ingest.add_argument("--builds-on", default=None, dest="builds_on",
                          help="Comma-separated KB IDs this builds on")
    p_ingest.set_defaults(func=cmd_ingest)

    # -- validate --
    p_validate = subparsers.add_parser("validate", help="Run all validators")
    p_validate.set_defaults(func=cmd_validate)

    # -- search --
    p_search = subparsers.add_parser("search", help="Unified search wrapper")
    p_search.add_argument("query", nargs="?", default=None, help="Search query")
    p_search.add_argument("--tag", default=None, help="Filter by tag")
    p_search.add_argument("--domain", default=None, help="Filter by domain")
    p_search.add_argument("--related", default=None, help="Find related entries by ID")
    p_search.set_defaults(func=cmd_search)

    # -- health --
    p_health = subparsers.add_parser("health", help="Quick repository health check")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
