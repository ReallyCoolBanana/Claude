#!/usr/bin/env python3
"""Unified search across all repository domains.

Searches knowledge-base, scripts, sources, api-tools, and team sessions
from a single unified_index.json file.

Usage:
    python3 unified_search.py "SQLite coordination"
    python3 unified_search.py --tag coordination --domain scripts
    python3 unified_search.py --related KB-0015
    python3 unified_search.py --list-tags
    python3 unified_search.py --list-domains
"""

import argparse
import json
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INDEX_PATH = os.path.join(REPO_ROOT, "storage", "unified_index.json")

# Domain short aliases
DOMAIN_ALIASES = {
    "kb": "knowledge-base",
    "knowledge-base": "knowledge-base",
    "scripts": "scripts",
    "scr": "scripts",
    "sources": "sources",
    "src": "sources",
    "api-tools": "api-tools",
    "tools": "api-tools",
    "sessions": "sessions",
    "teams": "sessions",
}


def load_index():
    """Load the unified index, exit on failure."""
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Unified index not found at {INDEX_PATH}", file=sys.stderr)
        print("Run rebuild_unified_index.py first.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {INDEX_PATH}: {e}", file=sys.stderr)
        sys.exit(1)


def resolve_domains(domain_str):
    """Parse comma-separated domain string into canonical domain names."""
    if not domain_str:
        return None
    domains = set()
    for part in domain_str.split(","):
        part = part.strip().lower()
        if part in DOMAIN_ALIASES:
            domains.add(DOMAIN_ALIASES[part])
        else:
            print(f"WARNING: Unknown domain '{part}'. Valid: {', '.join(sorted(set(DOMAIN_ALIASES.values())))}", file=sys.stderr)
    return domains if domains else None


def keyword_match(query, entry):
    """Score an entry against a keyword query. Returns score >= 0."""
    if not query:
        return 1  # no query means match everything

    query_lower = query.lower()
    keywords = query_lower.split()

    title = entry.get("title", "").lower()
    description = entry.get("description", "").lower()
    tags = [t.lower() for t in entry.get("tags", [])]
    entry_id = entry.get("id", "").lower()

    score = 0
    matched_all = True

    for kw in keywords:
        kw_matched = False
        # Exact ID match is highest priority
        if kw == entry_id:
            score += 10
            kw_matched = True
        # Tag exact match
        if kw in tags:
            score += 5
            kw_matched = True
        # Title contains keyword
        if kw in title:
            score += 3
            kw_matched = True
        # Description contains keyword
        if kw in description:
            score += 1
            kw_matched = True

        if not kw_matched:
            matched_all = False

    return score if matched_all else 0


def filter_by_tags(entries, required_tags):
    """Keep only entries that have ALL specified tags."""
    if not required_tags:
        return entries
    required_lower = {t.lower() for t in required_tags}
    result = []
    for e in entries:
        entry_tags = {t.lower() for t in e.get("tags", [])}
        if required_lower.issubset(entry_tags):
            result.append(e)
    return result


def filter_by_domain(entries, domains):
    """Keep only entries in specified domains."""
    if not domains:
        return entries
    return [e for e in entries if e["domain"] in domains]


def filter_by_category(entries, category):
    """Keep only entries matching the category."""
    if not category:
        return entries
    cat_lower = category.lower()
    return [e for e in entries if e.get("category", "").lower() == cat_lower]


def filter_by_date(entries, after=None, before=None):
    """Filter entries by date range."""
    if not after and not before:
        return entries

    result = []
    for e in entries:
        date_str = e.get("date", "")
        if not date_str:
            continue
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if after:
            after_date = datetime.strptime(after, "%Y-%m-%d").date()
            if entry_date < after_date:
                continue
        if before:
            before_date = datetime.strptime(before, "%Y-%m-%d").date()
            if entry_date > before_date:
                continue
        result.append(e)
    return result


def find_related(entries, target_id, tag_index):
    """Find entries sharing tags with the target entry."""
    # Find the target entry
    target = None
    for e in entries:
        if e["id"] == target_id:
            target = e
            break

    if not target:
        print(f"ERROR: Entry '{target_id}' not found.", file=sys.stderr)
        sys.exit(1)

    target_tags = {t.lower() for t in target.get("tags", [])}
    if not target_tags:
        return []

    # Collect related IDs via tag index, count shared tags
    related_scores = {}
    for tag in target_tags:
        tag_lower = tag.lower()
        for entry_id in tag_index.get(tag_lower, []):
            if entry_id == target_id:
                continue
            related_scores[entry_id] = related_scores.get(entry_id, 0) + 1

    # Build results sorted by shared tag count
    results = []
    id_to_entry = {e["id"]: e for e in entries}
    for entry_id, shared_count in sorted(related_scores.items(), key=lambda x: -x[1]):
        if entry_id in id_to_entry:
            entry = dict(id_to_entry[entry_id])
            entry["_shared_tags"] = shared_count
            results.append(entry)

    return results


def format_table(entries, show_score=False, show_shared=False):
    """Format entries as a human-readable table."""
    if not entries:
        print("No results found.")
        return

    # Column widths
    id_w = max(len(e["id"]) for e in entries)
    id_w = max(id_w, 4)
    domain_w = max(len(e["domain"]) for e in entries)
    domain_w = max(domain_w, 6)
    date_w = 10

    # Title gets remaining space, capped at 60
    title_w = 55

    # Header
    header_parts = [
        "ID".ljust(id_w),
        "Domain".ljust(domain_w),
        "Date".ljust(date_w),
        "Title".ljust(title_w),
    ]
    if show_score:
        header_parts.append("Score")
    if show_shared:
        header_parts.append("Shared")

    header = "  ".join(header_parts)
    print(header)
    print("-" * len(header))

    for e in entries:
        title = e.get("title", "")
        if len(title) > title_w:
            title = title[:title_w - 3] + "..."

        row_parts = [
            e["id"].ljust(id_w),
            e["domain"].ljust(domain_w),
            e.get("date", "").ljust(date_w) if e.get("date") else " " * date_w,
            title.ljust(title_w),
        ]
        if show_score:
            row_parts.append(str(e.get("_score", 0)))
        if show_shared:
            row_parts.append(str(e.get("_shared_tags", 0)))

        print("  ".join(row_parts))

    print(f"\n{len(entries)} result(s)")


def format_json(entries):
    """Output entries as JSON."""
    # Strip internal scoring keys
    clean = []
    for e in entries:
        c = {k: v for k, v in e.items() if not k.startswith("_")}
        clean.append(c)
    print(json.dumps(clean, indent=2, ensure_ascii=False))


def list_tags(tag_index):
    """Display all tags with counts, sorted by count descending."""
    if not tag_index:
        print("No tags found.")
        return

    sorted_tags = sorted(tag_index.items(), key=lambda x: (-len(x[1]), x[0]))

    tag_w = max(len(t) for t, _ in sorted_tags)
    tag_w = max(tag_w, 3)

    print(f"{'Tag'.ljust(tag_w)}  Count  IDs")
    print("-" * (tag_w + 40))

    for tag, ids in sorted_tags:
        ids_preview = ", ".join(ids[:5])
        if len(ids) > 5:
            ids_preview += f" (+{len(ids) - 5} more)"
        print(f"{tag.ljust(tag_w)}  {str(len(ids)).ljust(5)}  {ids_preview}")

    print(f"\n{len(sorted_tags)} unique tag(s)")


def list_domains(domain_counts):
    """Display domain counts."""
    if not domain_counts:
        print("No domains found.")
        return

    print(f"{'Domain'.ljust(15)}  Count")
    print("-" * 25)

    total = 0
    for domain, count in sorted(domain_counts.items()):
        print(f"{domain.ljust(15)}  {count}")
        total += count

    print("-" * 25)
    print(f"{'Total'.ljust(15)}  {total}")


def build_parser():
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Unified search across all repository domains.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s "SQLite coordination"
  %(prog)s --tag coordination --tag sqlite
  %(prog)s --tag coordination --domain scripts
  %(prog)s --related KB-0015
  %(prog)s --list-tags
  %(prog)s --list-domains
  %(prog)s --category optimization --after 2026-03-11
  %(prog)s --domain kb --json
""",
    )

    parser.add_argument(
        "query", nargs="?", default=None,
        help="Full-text search keywords (searches titles and descriptions)",
    )
    parser.add_argument(
        "--tag", action="append", dest="tags", metavar="TAG",
        help="Filter by tag (repeatable; entries must have ALL specified tags)",
    )
    parser.add_argument(
        "--domain", type=str, default=None, metavar="DOMAINS",
        help="Restrict to domains (comma-separated: kb,scripts,sources,tools,sessions)",
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Filter by category",
    )
    parser.add_argument(
        "--after", type=str, default=None, metavar="YYYY-MM-DD",
        help="Show entries on or after this date",
    )
    parser.add_argument(
        "--before", type=str, default=None, metavar="YYYY-MM-DD",
        help="Show entries on or before this date",
    )
    parser.add_argument(
        "--related", type=str, default=None, metavar="ID",
        help="Find entries sharing tags with the given entry ID",
    )
    parser.add_argument(
        "--list-tags", action="store_true",
        help="List all tags with entry counts",
    )
    parser.add_argument(
        "--list-domains", action="store_true",
        help="List domain entry counts",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON instead of table",
    )
    parser.add_argument(
        "--limit", type=int, default=50, metavar="N",
        help="Maximum number of results (default: 50)",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    index = load_index()
    entries = index.get("entries", [])
    tag_index = index.get("tag_index", {})
    domain_counts = index.get("domain_counts", {})

    # Special list modes
    if args.list_tags:
        if args.json:
            counts = {t: len(ids) for t, ids in tag_index.items()}
            print(json.dumps(counts, indent=2))
        else:
            list_tags(tag_index)
        return

    if args.list_domains:
        if args.json:
            print(json.dumps(domain_counts, indent=2))
        else:
            list_domains(domain_counts)
        return

    # Related mode
    if args.related:
        results = find_related(entries, args.related, tag_index)
        # Apply additional filters
        domains = resolve_domains(args.domain)
        results = filter_by_domain(results, domains)
        results = filter_by_tags(results, args.tags)
        results = filter_by_category(results, args.category)
        results = filter_by_date(results, args.after, args.before)
        results = results[:args.limit]

        if args.json:
            format_json(results)
        else:
            print(f"Entries related to {args.related}:\n")
            format_table(results, show_shared=True)
        return

    # Search / filter mode
    domains = resolve_domains(args.domain)
    results = filter_by_domain(entries, domains)
    results = filter_by_tags(results, args.tags)
    results = filter_by_category(results, args.category)
    results = filter_by_date(results, args.after, args.before)

    # Apply keyword search if query provided
    if args.query:
        scored = []
        for e in results:
            score = keyword_match(args.query, e)
            if score > 0:
                entry = dict(e)
                entry["_score"] = score
                scored.append(entry)
        scored.sort(key=lambda x: -x["_score"])
        results = scored

    results = results[:args.limit]

    if args.json:
        format_json(results)
    else:
        format_table(results, show_score=bool(args.query))


if __name__ == "__main__":
    main()
