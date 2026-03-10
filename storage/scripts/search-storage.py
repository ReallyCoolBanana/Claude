#!/usr/bin/env python3
"""
search-storage.py (SCR-0023)

Phase 3: Unified Search and Discovery Tool
Searches across all three storage subsystems simultaneously.
Supports keyword, category, team, date range, and data type filtering.

Usage:
    python search-storage.py --keyword "stock"
    python search-storage.py --team TEAM-0002
    python search-storage.py --category data-collection
    python search-storage.py --after 2026-03-01
    python search-storage.py --subsystem scripts --keyword "fetch"
    python search-storage.py --data-type financial
    python search-storage.py --keyword "price" --subsystem tools --team TEAM-0004

Added by: TEAM-0004 (Team Lead 4)
"""

import json
import os
import argparse
import re
from datetime import datetime

STORAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_INDEX = os.path.join(STORAGE_DIR, "scripts", "index.json")
TOOLS_INDEX = os.path.join(STORAGE_DIR, "api-tools", "index.json")
SOURCES_INDEX = os.path.join(STORAGE_DIR, "sources", "index.json")
CROSS_REF_PATH = os.path.join(STORAGE_DIR, "cross-references.json")


def load_all():
    """Load all indexes."""
    with open(SCRIPTS_INDEX) as f:
        scripts = json.load(f)
    with open(TOOLS_INDEX) as f:
        tools = json.load(f)
    with open(SOURCES_INDEX) as f:
        sources = json.load(f)

    cross_ref = None
    if os.path.exists(CROSS_REF_PATH):
        with open(CROSS_REF_PATH) as f:
            cross_ref = json.load(f)

    return scripts, tools, sources, cross_ref


def normalize_entry(entry, subsystem):
    """Normalize entries from different subsystems to a common format."""
    if subsystem == "scripts":
        return {
            "id": entry["id"],
            "name": entry["name"],
            "subsystem": "scripts",
            "category": entry.get("category", ""),
            "description": entry.get("description", ""),
            "team": entry.get("added_by_team", ""),
            "date": entry.get("added", ""),
            "data_types": [],
            "extra": f"lang={entry.get('language', '')}",
            "_searchable": f"{entry['id']} {entry['name']} {entry.get('description', '')} {entry.get('language', '')} {entry.get('category', '')} {entry.get('added_by_team', '')}".lower()
        }
    elif subsystem == "tools":
        return {
            "id": entry["id"],
            "name": entry["name"],
            "subsystem": "tools",
            "category": entry.get("category", ""),
            "description": entry.get("description", ""),
            "team": entry.get("added_by_team", ""),
            "date": entry.get("added", ""),
            "data_types": [],
            "extra": f"uses={entry.get('usage_count', 0)}",
            "_searchable": f"{entry['id']} {entry['name']} {entry.get('description', '')} {entry.get('category', '')} {entry.get('endpoint', '')} {entry.get('added_by_team', '')}".lower()
        }
    elif subsystem == "sources":
        return {
            "id": entry["id"],
            "name": entry["name"],
            "subsystem": "sources",
            "category": entry.get("access_type", ""),
            "description": entry.get("description", ""),
            "team": entry.get("added_by_team", ""),
            "date": entry.get("last_verified", entry.get("added", "")),
            "data_types": entry.get("data_types", []),
            "extra": f"access={entry.get('access_type', '')}",
            "_searchable": f"{entry['id']} {entry['name']} {entry.get('description', '')} {' '.join(entry.get('data_types', []))} {entry.get('access_type', '')} {entry.get('added_by_team', '')}".lower()
        }


def score_relevance(entry, keyword):
    """Score relevance of an entry to a keyword search."""
    if not keyword:
        return 1.0

    kw = keyword.lower()
    score = 0.0

    # Exact match in name (highest weight)
    if kw in entry["name"].lower():
        score += 10.0

    # Exact match in ID
    if kw in entry["id"].lower():
        score += 8.0

    # Match in description
    if kw in entry["description"].lower():
        score += 5.0

    # Partial word match in searchable text
    if kw in entry["_searchable"]:
        score += 2.0

    # Word-level matching for multi-word keywords
    kw_parts = kw.split()
    if len(kw_parts) > 1:
        matches = sum(1 for part in kw_parts if part in entry["_searchable"])
        score += matches * 3.0

    return score


def search(scripts, tools, sources, cross_ref,
           keyword=None, category=None, team=None,
           after=None, before=None, data_type=None,
           subsystem=None):
    """Search across all subsystems with filters."""

    all_entries = []

    if subsystem is None or subsystem == "scripts":
        for s in scripts.get("scripts", []):
            all_entries.append(normalize_entry(s, "scripts"))

    if subsystem is None or subsystem == "tools":
        for t in tools.get("tools", []):
            all_entries.append(normalize_entry(t, "tools"))

    if subsystem is None or subsystem == "sources":
        for s in sources.get("sources", []):
            all_entries.append(normalize_entry(s, "sources"))

    # Apply filters
    results = []
    for entry in all_entries:
        # Category filter
        if category and category.lower() != entry["category"].lower():
            continue

        # Team filter
        if team and team.upper() != entry["team"].upper():
            continue

        # Date range filters
        if after and entry["date"] and entry["date"] < after:
            continue
        if before and entry["date"] and entry["date"] > before:
            continue

        # Data type filter (sources only, but don't exclude non-sources)
        if data_type:
            if entry["subsystem"] == "sources":
                if data_type.lower() not in [dt.lower() for dt in entry["data_types"]]:
                    continue
            else:
                # For non-source entries, check if data_type keyword appears in description
                if data_type.lower() not in entry["_searchable"]:
                    continue

        # Keyword filter + scoring
        if keyword:
            score = score_relevance(entry, keyword)
            if score <= 0:
                continue
            entry["_score"] = score
        else:
            entry["_score"] = 1.0

        results.append(entry)

    # Sort by relevance score (descending), then by name
    results.sort(key=lambda x: (-x["_score"], x["name"]))

    return results


def format_table(results, show_score=False):
    """Format results as a clean table."""
    if not results:
        print("\n  No results found.\n")
        return

    # Column widths
    id_w = max(len(r["id"]) for r in results)
    name_w = min(max(len(r["name"]) for r in results), 30)
    sub_w = 8
    cat_w = min(max(len(r["category"]) for r in results), 18)
    team_w = 10
    date_w = 10
    desc_w = 45

    header = f"  {'ID':<{id_w}}  {'Name':<{name_w}}  {'Type':<{sub_w}}  {'Category':<{cat_w}}  {'Team':<{team_w}}  {'Date':<{date_w}}"
    if show_score:
        header += "  Score"
    print()
    print(header)
    print("  " + "-" * (len(header) - 2))

    for r in results:
        name = r["name"][:name_w]
        cat = r["category"][:cat_w]
        line = f"  {r['id']:<{id_w}}  {name:<{name_w}}  {r['subsystem']:<{sub_w}}  {cat:<{cat_w}}  {r['team']:<{team_w}}  {r['date']:<{date_w}}"
        if show_score:
            line += f"  {r['_score']:.1f}"
        print(line)

    print(f"\n  Total results: {len(results)}")

    # Show cross-references if available
    return results


def format_with_crossrefs(results, cross_ref):
    """Show cross-references for top results."""
    if not cross_ref or not results:
        return

    entries = cross_ref.get("entries", {})
    top = results[:5]
    has_refs = False

    for r in top:
        if r["id"] in entries:
            ref = entries[r["id"]]
            refs_parts = []
            for key in ["uses_sources", "uses_tools", "used_by_scripts", "related_sources",
                         "consumed_by_scripts", "wrapped_by_tools"]:
                if key in ref and ref[key]:
                    refs_parts.append(f"{key}: {', '.join(ref[key])}")
            if refs_parts:
                if not has_refs:
                    print(f"\n  Cross-References (top results):")
                    has_refs = True
                print(f"    {r['id']}: {'; '.join(refs_parts)}")

    if not has_refs:
        print(f"\n  No cross-references found for top results.")


def run_demo_queries(scripts, tools, sources, cross_ref):
    """Run demo queries to showcase the search tool."""
    demos = [
        {"label": "Search all: keyword 'stock'",
         "kwargs": {"keyword": "stock"}},
        {"label": "Scripts only: keyword 'fetch'",
         "kwargs": {"keyword": "fetch", "subsystem": "scripts"}},
        {"label": "All entries by TEAM-0002",
         "kwargs": {"team": "TEAM-0002"}},
        {"label": "Sources with data type 'financial'",
         "kwargs": {"data_type": "financial"}},
        {"label": "Entries added after 2026-03-01",
         "kwargs": {"after": "2026-03-01"}},
        {"label": "Tools in 'communication' category",
         "kwargs": {"category": "communication", "subsystem": "tools"}},
        {"label": "Keyword 'price' across all subsystems",
         "kwargs": {"keyword": "price"}},
    ]

    for demo in demos:
        print(f"\n{'=' * 70}")
        print(f"  QUERY: {demo['label']}")
        print(f"  Filters: {demo['kwargs']}")
        print(f"{'=' * 70}")

        results = search(scripts, tools, sources, cross_ref, **demo["kwargs"])
        format_table(results, show_score=bool(demo["kwargs"].get("keyword")))
        format_with_crossrefs(results, cross_ref)


def main():
    parser = argparse.ArgumentParser(description="Unified Storage Search Tool")
    parser.add_argument("--keyword", "-k", help="Search keyword")
    parser.add_argument("--category", "-c", help="Filter by category")
    parser.add_argument("--team", "-t", help="Filter by team (e.g. TEAM-0002)")
    parser.add_argument("--after", help="Only entries after this date (YYYY-MM-DD)")
    parser.add_argument("--before", help="Only entries before this date (YYYY-MM-DD)")
    parser.add_argument("--data-type", "-d", help="Filter by data type (financial, news, etc.)")
    parser.add_argument("--subsystem", "-s", choices=["scripts", "tools", "sources"],
                        help="Limit search to a specific subsystem")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo queries to showcase the search tool")

    args = parser.parse_args()

    print("=" * 70)
    print("PHASE 3: Unified Storage Search Tool")
    print("Team Lead 4 — Sub-Agent Gamma")
    print("=" * 70)

    scripts, tools, sources, cross_ref = load_all()

    if args.demo:
        run_demo_queries(scripts, tools, sources, cross_ref)
    else:
        has_filter = any([args.keyword, args.category, args.team, args.after,
                          args.before, args.data_type, args.subsystem])
        if not has_filter:
            print("\nNo filters specified. Running demo queries...\n")
            run_demo_queries(scripts, tools, sources, cross_ref)
        else:
            results = search(scripts, tools, sources, cross_ref,
                            keyword=args.keyword, category=args.category,
                            team=args.team, after=args.after, before=args.before,
                            data_type=args.data_type, subsystem=args.subsystem)
            format_table(results, show_score=bool(args.keyword))
            format_with_crossrefs(results, cross_ref)

    print(f"\nSearch complete.\n")


if __name__ == "__main__":
    main()
