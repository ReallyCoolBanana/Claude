#!/usr/bin/env python3
"""Algorithm Catalog Builder

A tool that maintains a structured catalog of algorithms with search,
add, update, and export capabilities. Designed for use by DATA-GATHER-ALGO
teams to collect and organize algorithmic knowledge.

Usage:
    # Add an algorithm
    python algo_catalog.py add --name "Binary Search" --category "searching" \
        --time-complexity "O(log n)" --space-complexity "O(1)" \
        --description "Efficiently finds target in sorted array" \
        --references "CLRS Ch.2" --code 'def binary_search(arr, t): ...'

    # Search by category
    python algo_catalog.py search --category "sorting"

    # Search by complexity class
    python algo_catalog.py search --complexity "O(n log n)"

    # Search by keyword
    python algo_catalog.py search --keyword "graph traversal"

    # List all categories
    python algo_catalog.py categories

    # Export catalog to markdown
    python algo_catalog.py export --format markdown --output catalog.md

    # Export catalog to JSON
    python algo_catalog.py export --format json --output catalog.json

    # Import algorithms from a JSON file
    python algo_catalog.py import --file algorithms.json

    # Show catalog statistics
    python algo_catalog.py stats

    # Remove an algorithm by ID
    python algo_catalog.py remove --id "algo-0001"
"""

import argparse
import json
import os
import re
import sys
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional


DEFAULT_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "algo_catalog.json")

COMPLEXITY_CLASSES = [
    "O(1)", "O(log n)", "O(sqrt(n))", "O(n)", "O(n log n)",
    "O(n^2)", "O(n^3)", "O(2^n)", "O(n!)", "O(n^k)"
]

STANDARD_CATEGORIES = [
    "sorting", "searching", "graph", "dynamic-programming",
    "greedy", "divide-and-conquer", "string", "tree",
    "hashing", "math", "geometry", "backtracking",
    "bit-manipulation", "network-flow", "linear-algebra",
    "optimization", "randomized", "approximation",
    "data-structure", "concurrency", "machine-learning",
    "cryptography", "compression", "numerical"
]


def _generate_id(name: str) -> str:
    """Generate a deterministic ID from algorithm name."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    short_hash = hashlib.md5(name.encode()).hexdigest()[:6]
    return f"algo-{slug}-{short_hash}"


def load_catalog(catalog_path: str) -> Dict[str, Any]:
    """Load the algorithm catalog from disk."""
    if os.path.exists(catalog_path):
        with open(catalog_path, 'r') as f:
            return json.load(f)
    return {
        "version": "1.0",
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "algorithms": [],
        "category_index": {},
        "complexity_index": {}
    }


def save_catalog(catalog: Dict[str, Any], catalog_path: str) -> None:
    """Save the algorithm catalog to disk."""
    catalog["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    _rebuild_indexes(catalog)
    with open(catalog_path, 'w') as f:
        json.dump(catalog, f, indent=2)


def _rebuild_indexes(catalog: Dict[str, Any]) -> None:
    """Rebuild category and complexity indexes."""
    cat_index: Dict[str, List[str]] = {}
    comp_index: Dict[str, List[str]] = {}
    for algo in catalog["algorithms"]:
        aid = algo["id"]
        cat = algo.get("category", "uncategorized")
        cat_index.setdefault(cat, [])
        if aid not in cat_index[cat]:
            cat_index[cat].append(aid)
        tc = algo.get("time_complexity", "unknown")
        comp_index.setdefault(tc, [])
        if aid not in comp_index[tc]:
            comp_index[tc].append(aid)
    catalog["category_index"] = cat_index
    catalog["complexity_index"] = comp_index


def add_algorithm(catalog: Dict[str, Any], name: str, category: str,
                  time_complexity: str = "unknown",
                  space_complexity: str = "unknown",
                  description: str = "",
                  references: Optional[List[str]] = None,
                  code_snippet: str = "",
                  tags: Optional[List[str]] = None,
                  related: Optional[List[str]] = None) -> Dict[str, Any]:
    """Add a new algorithm to the catalog. Returns the new entry."""
    algo_id = _generate_id(name)

    # Check for duplicates
    for existing in catalog["algorithms"]:
        if existing["id"] == algo_id:
            raise ValueError(f"Algorithm '{name}' already exists with ID {algo_id}")

    entry = {
        "id": algo_id,
        "name": name,
        "category": category.lower(),
        "time_complexity": time_complexity,
        "space_complexity": space_complexity,
        "description": description,
        "references": references or [],
        "code_snippet": code_snippet,
        "tags": tags or [],
        "related_algorithms": related or [],
        "added": datetime.now().strftime("%Y-%m-%d"),
        "last_modified": datetime.now().strftime("%Y-%m-%d")
    }

    catalog["algorithms"].append(entry)
    return entry


def remove_algorithm(catalog: Dict[str, Any], algo_id: str) -> bool:
    """Remove an algorithm by ID. Returns True if found and removed."""
    initial_len = len(catalog["algorithms"])
    catalog["algorithms"] = [a for a in catalog["algorithms"] if a["id"] != algo_id]
    return len(catalog["algorithms"]) < initial_len


def search_catalog(catalog: Dict[str, Any],
                   category: Optional[str] = None,
                   complexity: Optional[str] = None,
                   keyword: Optional[str] = None,
                   tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Search the catalog with multiple filter criteria."""
    results = catalog["algorithms"]

    if category:
        category_lower = category.lower()
        results = [a for a in results if a.get("category", "").lower() == category_lower]

    if complexity:
        results = [a for a in results if a.get("time_complexity", "") == complexity]

    if keyword:
        keyword_lower = keyword.lower()
        results = [a for a in results if (
            keyword_lower in a.get("name", "").lower() or
            keyword_lower in a.get("description", "").lower() or
            keyword_lower in a.get("code_snippet", "").lower() or
            any(keyword_lower in ref.lower() for ref in a.get("references", [])) or
            any(keyword_lower in tag.lower() for tag in a.get("tags", []))
        )]

    if tags:
        tags_lower = {t.lower() for t in tags}
        results = [a for a in results if
                   tags_lower.intersection({t.lower() for t in a.get("tags", [])})]

    return results


def get_stats(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Get catalog statistics."""
    algos = catalog["algorithms"]
    categories = {}
    complexities = {}
    for a in algos:
        cat = a.get("category", "uncategorized")
        categories[cat] = categories.get(cat, 0) + 1
        tc = a.get("time_complexity", "unknown")
        complexities[tc] = complexities.get(tc, 0) + 1

    return {
        "total_algorithms": len(algos),
        "categories": categories,
        "complexity_distribution": complexities,
        "with_code_snippets": sum(1 for a in algos if a.get("code_snippet")),
        "with_references": sum(1 for a in algos if a.get("references")),
        "last_updated": catalog.get("last_updated", "unknown")
    }


def export_markdown(catalog: Dict[str, Any]) -> str:
    """Export the catalog as structured Markdown."""
    lines = ["# Algorithm Catalog\n"]
    lines.append(f"Last updated: {catalog.get('last_updated', 'unknown')}\n")

    stats = get_stats(catalog)
    lines.append(f"Total algorithms: {stats['total_algorithms']}\n")

    # Group by category
    by_category: Dict[str, List[Dict]] = {}
    for algo in catalog["algorithms"]:
        cat = algo.get("category", "uncategorized")
        by_category.setdefault(cat, []).append(algo)

    for cat in sorted(by_category.keys()):
        lines.append(f"\n## {cat.replace('-', ' ').title()}\n")
        for algo in sorted(by_category[cat], key=lambda a: a["name"]):
            lines.append(f"### {algo['name']}\n")
            lines.append(f"- **ID**: `{algo['id']}`")
            lines.append(f"- **Time Complexity**: {algo['time_complexity']}")
            lines.append(f"- **Space Complexity**: {algo['space_complexity']}")
            if algo.get("description"):
                lines.append(f"- **Description**: {algo['description']}")
            if algo.get("tags"):
                lines.append(f"- **Tags**: {', '.join(algo['tags'])}")
            if algo.get("references"):
                lines.append(f"- **References**: {', '.join(algo['references'])}")
            if algo.get("related_algorithms"):
                lines.append(f"- **Related**: {', '.join(algo['related_algorithms'])}")
            if algo.get("code_snippet"):
                lines.append(f"\n```python\n{algo['code_snippet']}\n```\n")
            lines.append("")

    return "\n".join(lines)


def export_json(catalog: Dict[str, Any]) -> str:
    """Export the catalog as formatted JSON."""
    return json.dumps(catalog, indent=2)


def import_algorithms(catalog: Dict[str, Any], data: List[Dict[str, Any]]) -> int:
    """Import algorithms from a list of dicts. Returns count of imported."""
    count = 0
    for item in data:
        name = item.get("name")
        if not name:
            continue
        try:
            add_algorithm(
                catalog,
                name=name,
                category=item.get("category", "uncategorized"),
                time_complexity=item.get("time_complexity", "unknown"),
                space_complexity=item.get("space_complexity", "unknown"),
                description=item.get("description", ""),
                references=item.get("references", []),
                code_snippet=item.get("code_snippet", ""),
                tags=item.get("tags", []),
                related=item.get("related_algorithms", [])
            )
            count += 1
        except ValueError:
            pass  # skip duplicates
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Algorithm Catalog Builder - maintain a structured algorithm catalog",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", default=DEFAULT_CATALOG_PATH,
                        help="Path to catalog JSON file")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # add
    add_p = subparsers.add_parser("add", help="Add an algorithm to the catalog")
    add_p.add_argument("--name", required=True, help="Algorithm name")
    add_p.add_argument("--category", required=True, help="Category (e.g., sorting, graph)")
    add_p.add_argument("--time-complexity", default="unknown", help="Time complexity (e.g., O(n log n))")
    add_p.add_argument("--space-complexity", default="unknown", help="Space complexity")
    add_p.add_argument("--description", default="", help="Algorithm description")
    add_p.add_argument("--references", nargs="*", default=[], help="Reference sources")
    add_p.add_argument("--code", default="", help="Code snippet")
    add_p.add_argument("--tags", nargs="*", default=[], help="Tags")
    add_p.add_argument("--related", nargs="*", default=[], help="Related algorithm IDs")

    # search
    search_p = subparsers.add_parser("search", help="Search the catalog")
    search_p.add_argument("--category", help="Filter by category")
    search_p.add_argument("--complexity", help="Filter by time complexity")
    search_p.add_argument("--keyword", help="Search by keyword")
    search_p.add_argument("--tags", nargs="*", help="Filter by tags")
    search_p.add_argument("--json", action="store_true", help="Output as JSON")

    # categories
    subparsers.add_parser("categories", help="List all categories")

    # export
    export_p = subparsers.add_parser("export", help="Export the catalog")
    export_p.add_argument("--format", choices=["json", "markdown"], default="json",
                          help="Export format")
    export_p.add_argument("--output", help="Output file (stdout if omitted)")

    # import
    import_p = subparsers.add_parser("import", help="Import algorithms from JSON file")
    import_p.add_argument("--file", required=True, help="JSON file to import")

    # stats
    subparsers.add_parser("stats", help="Show catalog statistics")

    # remove
    remove_p = subparsers.add_parser("remove", help="Remove an algorithm by ID")
    remove_p.add_argument("--id", required=True, help="Algorithm ID to remove")

    args = parser.parse_args()
    catalog = load_catalog(args.catalog)

    if args.command == "add":
        try:
            entry = add_algorithm(
                catalog, name=args.name, category=args.category,
                time_complexity=args.time_complexity,
                space_complexity=args.space_complexity,
                description=args.description,
                references=args.references,
                code_snippet=args.code,
                tags=args.tags,
                related=args.related
            )
            save_catalog(catalog, args.catalog)
            print(json.dumps({"status": "added", "entry": entry}, indent=2))
        except ValueError as e:
            print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "search":
        results = search_catalog(catalog, category=args.category,
                                 complexity=args.complexity,
                                 keyword=args.keyword, tags=args.tags)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            if not results:
                print("No algorithms found matching criteria.")
            for algo in results:
                print(f"  [{algo['id']}] {algo['name']} ({algo['category']})")
                print(f"    Time: {algo['time_complexity']}  Space: {algo['space_complexity']}")
                if algo.get("description"):
                    print(f"    {algo['description'][:100]}")
                print()

    elif args.command == "categories":
        cats = catalog.get("category_index", {})
        if not cats:
            _rebuild_indexes(catalog)
            cats = catalog.get("category_index", {})
        print("Categories:")
        for cat, ids in sorted(cats.items()):
            print(f"  {cat}: {len(ids)} algorithms")
        print(f"\nStandard categories: {', '.join(STANDARD_CATEGORIES)}")

    elif args.command == "export":
        if args.format == "markdown":
            output = export_markdown(catalog)
        else:
            output = export_json(catalog)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            print(f"Exported to {args.output}")
        else:
            print(output)

    elif args.command == "import":
        with open(args.file, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict) and "algorithms" in data:
            data = data["algorithms"]
        count = import_algorithms(catalog, data)
        save_catalog(catalog, args.catalog)
        print(f"Imported {count} algorithms.")

    elif args.command == "stats":
        stats = get_stats(catalog)
        print(json.dumps(stats, indent=2))

    elif args.command == "remove":
        if remove_algorithm(catalog, args.id):
            save_catalog(catalog, args.catalog)
            print(f"Removed algorithm {args.id}")
        else:
            print(f"Algorithm {args.id} not found", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
