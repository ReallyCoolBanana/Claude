#!/usr/bin/env python3
"""Algorithm Catalog Builder

A tool that maintains a structured catalog of algorithms with support for
search by category, complexity class, or keyword. Designed for use by
DATA-GATHER-ALGO teams to organize algorithmic knowledge.

Usage:
    # Add an algorithm
    python algo_catalog.py add --name "Binary Search" \
        --category "searching" \
        --time-complexity "O(log n)" \
        --space-complexity "O(1)" \
        --description "Efficient search on sorted arrays" \
        --references '["CLRS Ch.2", "https://en.wikipedia.org/wiki/Binary_search"]' \
        --code 'def binary_search(arr, target):\n    lo, hi = 0, len(arr)-1\n    while lo <= hi:\n        mid = (lo+hi)//2\n        if arr[mid] == target: return mid\n        elif arr[mid] < target: lo = mid+1\n        else: hi = mid-1\n    return -1'

    # Search by category
    python algo_catalog.py search --category "sorting"

    # Search by complexity class
    python algo_catalog.py search --complexity "O(n log n)"

    # Search by keyword
    python algo_catalog.py search --keyword "graph traversal"

    # List all categories
    python algo_catalog.py categories

    # Export catalog as markdown
    python algo_catalog.py export --format markdown

    # Export catalog as JSON
    python algo_catalog.py export --format json

    # Import algorithms from a JSON file
    python algo_catalog.py import --file algorithms.json

    # Show catalog stats
    python algo_catalog.py stats
"""

import argparse
import json
import os
import re
import sys
import hashlib
from datetime import datetime
from typing import Any

DEFAULT_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "algorithm_catalog.json")


def _load_catalog(path: str) -> dict:
    """Load the catalog from disk, or return empty structure."""
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {
        "version": "1.0",
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "algorithms": [],
        "category_index": {},
        "complexity_index": {},
    }


def _save_catalog(catalog: dict, path: str) -> None:
    """Save the catalog to disk."""
    catalog["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(catalog, f, indent=2)


def _generate_id(name: str) -> str:
    """Generate a deterministic short ID from the algorithm name."""
    h = hashlib.sha256(name.lower().strip().encode()).hexdigest()[:8]
    return f"ALGO-{h}"


def _rebuild_indexes(catalog: dict) -> None:
    """Rebuild category and complexity indexes from the algorithms list."""
    catalog["category_index"] = {}
    catalog["complexity_index"] = {}
    for algo in catalog["algorithms"]:
        aid = algo["id"]
        cat = algo.get("category", "uncategorized")
        catalog["category_index"].setdefault(cat, [])
        if aid not in catalog["category_index"][cat]:
            catalog["category_index"][cat].append(aid)

        for ctype in ("time_complexity", "space_complexity"):
            comp = algo.get(ctype, "")
            if comp:
                catalog["complexity_index"].setdefault(comp, [])
                if aid not in catalog["complexity_index"][comp]:
                    catalog["complexity_index"][comp].append(aid)


def _normalize_complexity(c: str) -> str:
    """Normalize a complexity string for comparison."""
    c = c.strip().upper().replace(" ", "")
    # Standardize O(...) notation
    c = re.sub(r"^O\(", "O(", c)
    return c


def add_algorithm(
    catalog: dict,
    name: str,
    category: str,
    time_complexity: str = "",
    space_complexity: str = "",
    description: str = "",
    references: list | None = None,
    code_snippet: str = "",
    tags: list | None = None,
) -> dict:
    """Add an algorithm to the catalog. Returns the new entry."""
    aid = _generate_id(name)

    # Check for duplicates
    for algo in catalog["algorithms"]:
        if algo["id"] == aid:
            print(f"Algorithm '{name}' already exists with ID {aid}. Updating.", file=sys.stderr)
            algo.update({
                "name": name,
                "category": category.lower().strip(),
                "time_complexity": time_complexity,
                "space_complexity": space_complexity,
                "description": description,
                "references": references or [],
                "code_snippet": code_snippet,
                "tags": tags or [],
                "updated": datetime.now().strftime("%Y-%m-%d"),
            })
            _rebuild_indexes(catalog)
            return algo

    entry = {
        "id": aid,
        "name": name,
        "category": category.lower().strip(),
        "time_complexity": time_complexity,
        "space_complexity": space_complexity,
        "description": description,
        "references": references or [],
        "code_snippet": code_snippet,
        "tags": tags or [],
        "added": datetime.now().strftime("%Y-%m-%d"),
    }
    catalog["algorithms"].append(entry)
    _rebuild_indexes(catalog)
    return entry


def search_catalog(
    catalog: dict,
    category: str | None = None,
    complexity: str | None = None,
    keyword: str | None = None,
    tags: list | None = None,
) -> list[dict]:
    """Search the catalog with multiple filters (AND logic)."""
    results = list(catalog["algorithms"])

    if category:
        cat = category.lower().strip()
        results = [a for a in results if a.get("category", "") == cat]

    if complexity:
        norm = _normalize_complexity(complexity)
        results = [
            a for a in results
            if _normalize_complexity(a.get("time_complexity", "")) == norm
            or _normalize_complexity(a.get("space_complexity", "")) == norm
        ]

    if keyword:
        kw = keyword.lower()
        def _matches(algo: dict) -> bool:
            searchable = " ".join([
                algo.get("name", ""),
                algo.get("description", ""),
                algo.get("category", ""),
                " ".join(algo.get("tags", [])),
                algo.get("code_snippet", ""),
            ]).lower()
            return kw in searchable
        results = [a for a in results if _matches(a)]

    if tags:
        tag_set = {t.lower() for t in tags}
        results = [
            a for a in results
            if tag_set.intersection({t.lower() for t in a.get("tags", [])})
        ]

    return results


def list_categories(catalog: dict) -> dict[str, int]:
    """Return categories with counts."""
    counts: dict[str, int] = {}
    for algo in catalog["algorithms"]:
        cat = algo.get("category", "uncategorized")
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def export_catalog(catalog: dict, fmt: str = "json") -> str:
    """Export the catalog in the specified format."""
    if fmt == "json":
        return json.dumps(catalog, indent=2)

    if fmt == "markdown":
        lines = ["# Algorithm Catalog", ""]
        lines.append(f"**Last updated:** {catalog.get('last_updated', 'N/A')}")
        lines.append(f"**Total algorithms:** {len(catalog['algorithms'])}")
        lines.append("")

        # Group by category
        by_cat: dict[str, list] = {}
        for algo in catalog["algorithms"]:
            cat = algo.get("category", "uncategorized")
            by_cat.setdefault(cat, []).append(algo)

        for cat in sorted(by_cat.keys()):
            lines.append(f"## {cat.title()}")
            lines.append("")
            for algo in sorted(by_cat[cat], key=lambda a: a["name"]):
                lines.append(f"### {algo['name']}")
                lines.append(f"**ID:** `{algo['id']}`")
                if algo.get("time_complexity"):
                    lines.append(f"**Time:** {algo['time_complexity']}")
                if algo.get("space_complexity"):
                    lines.append(f"**Space:** {algo['space_complexity']}")
                if algo.get("description"):
                    lines.append(f"\n{algo['description']}")
                if algo.get("tags"):
                    lines.append(f"\n**Tags:** {', '.join(algo['tags'])}")
                if algo.get("references"):
                    lines.append("\n**References:**")
                    for ref in algo["references"]:
                        lines.append(f"- {ref}")
                if algo.get("code_snippet"):
                    lines.append("\n```python")
                    lines.append(algo["code_snippet"])
                    lines.append("```")
                lines.append("")
        return "\n".join(lines)

    raise ValueError(f"Unknown format: {fmt}")


def import_algorithms(catalog: dict, data: list[dict]) -> int:
    """Import algorithms from a list of dicts. Returns count of imported."""
    count = 0
    for item in data:
        name = item.get("name")
        category = item.get("category", "uncategorized")
        if not name:
            continue
        add_algorithm(
            catalog,
            name=name,
            category=category,
            time_complexity=item.get("time_complexity", ""),
            space_complexity=item.get("space_complexity", ""),
            description=item.get("description", ""),
            references=item.get("references", []),
            code_snippet=item.get("code_snippet", ""),
            tags=item.get("tags", []),
        )
        count += 1
    return count


def get_stats(catalog: dict) -> dict:
    """Return catalog statistics."""
    cats = list_categories(catalog)
    complexities: dict[str, int] = {}
    for algo in catalog["algorithms"]:
        tc = algo.get("time_complexity", "")
        if tc:
            complexities[tc] = complexities.get(tc, 0) + 1
    return {
        "total_algorithms": len(catalog["algorithms"]),
        "categories": cats,
        "complexity_distribution": complexities,
        "last_updated": catalog.get("last_updated", "N/A"),
    }


def main():
    parser = argparse.ArgumentParser(description="Algorithm Catalog Builder")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG_PATH, help="Path to catalog JSON file")
    sub = parser.add_subparsers(dest="command")

    # add
    p_add = sub.add_parser("add", help="Add an algorithm")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--category", required=True)
    p_add.add_argument("--time-complexity", default="")
    p_add.add_argument("--space-complexity", default="")
    p_add.add_argument("--description", default="")
    p_add.add_argument("--references", default="[]", help="JSON array of references")
    p_add.add_argument("--code", default="", help="Code snippet")
    p_add.add_argument("--tags", default="[]", help="JSON array of tags")

    # search
    p_search = sub.add_parser("search", help="Search algorithms")
    p_search.add_argument("--category", default=None)
    p_search.add_argument("--complexity", default=None)
    p_search.add_argument("--keyword", default=None)
    p_search.add_argument("--tags", default=None, help="Comma-separated tags")

    # categories
    sub.add_parser("categories", help="List categories")

    # export
    p_export = sub.add_parser("export", help="Export catalog")
    p_export.add_argument("--format", choices=["json", "markdown"], default="json")

    # import
    p_import = sub.add_parser("import", help="Import algorithms from JSON file")
    p_import.add_argument("--file", required=True)

    # stats
    sub.add_parser("stats", help="Show catalog statistics")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    catalog = _load_catalog(args.catalog)

    if args.command == "add":
        refs = json.loads(args.references) if args.references else []
        tags = json.loads(args.tags) if args.tags else []
        entry = add_algorithm(
            catalog, args.name, args.category,
            time_complexity=args.time_complexity,
            space_complexity=args.space_complexity,
            description=args.description,
            references=refs,
            code_snippet=args.code,
            tags=tags,
        )
        _save_catalog(catalog, args.catalog)
        print(json.dumps(entry, indent=2))

    elif args.command == "search":
        tag_list = [t.strip() for t in args.tags.split(",")] if args.tags else None
        results = search_catalog(catalog, args.category, args.complexity, args.keyword, tag_list)
        print(json.dumps(results, indent=2))

    elif args.command == "categories":
        cats = list_categories(catalog)
        print(json.dumps(cats, indent=2))

    elif args.command == "export":
        print(export_catalog(catalog, args.format))

    elif args.command == "import":
        with open(args.file, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and "algorithms" in data:
            data = data["algorithms"]
        count = import_algorithms(catalog, data)
        _save_catalog(catalog, args.catalog)
        print(f"Imported {count} algorithms")

    elif args.command == "stats":
        print(json.dumps(get_stats(catalog), indent=2))


if __name__ == "__main__":
    main()
