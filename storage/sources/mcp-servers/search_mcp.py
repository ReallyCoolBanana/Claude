#!/usr/bin/env python3
"""Search the MCP server catalog by keyword, category, or capability.

Usage:
    python3 search_mcp.py "database"
    python3 search_mcp.py --category dev-tool
    python3 search_mcp.py --official
    python3 search_mcp.py --list-categories
"""

import argparse
import json
import os
import sys

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")


def load_catalog():
    with open(CATALOG_PATH, "r") as f:
        return json.load(f)


def search(query=None, category=None, official_only=False):
    """Search the MCP server catalog."""
    catalog = load_catalog()
    servers = catalog["servers"]

    results = []
    for s in servers:
        if official_only and not s.get("official", False):
            continue
        if category and s.get("category") != category:
            continue
        if query:
            q = query.lower()
            text = f"{s['name']} {s['description']} {s.get('category', '')}".lower()
            if q not in text:
                continue
        results.append(s)

    return results


def main():
    parser = argparse.ArgumentParser(description="Search MCP server catalog")
    parser.add_argument("query", nargs="?", help="Search keyword")
    parser.add_argument("--category", help="Filter by category")
    parser.add_argument("--official", action="store_true", help="Official servers only")
    parser.add_argument("--list-categories", action="store_true", help="List categories")
    parser.add_argument("--stats", action="store_true", help="Show catalog statistics")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    catalog = load_catalog()

    if args.list_categories:
        for cat, servers in sorted(catalog["categories"].items()):
            print(f"  {cat:20s}  ({len(servers)} servers)")
        return

    if args.stats:
        stats = catalog["stats"]
        print(f"Total servers:     {stats['total_servers']}")
        print(f"Official:          {stats['official_count']}")
        print(f"Community:         {stats['community_count']}")
        print(f"Categories:        {stats['category_count']}")
        print(f"Registries:        {len(catalog['registries'])}")
        return

    results = search(
        query=args.query,
        category=args.category,
        official_only=args.official,
    )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No matching MCP servers found.")
            return
        print(f"Found {len(results)} MCP server(s):\n")
        for s in results:
            badge = "[official]" if s.get("official") else "[community]"
            print(f"  {s['name']:24s} {badge:12s}  {s['description']}")
            print(f"  {'':24s} category: {s['category']}")
            print(f"  {'':24s} install:  {s.get('install', 'N/A')}")
            print()


if __name__ == "__main__":
    main()
