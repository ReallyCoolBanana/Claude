#!/usr/bin/env python3
"""Cross-Team Search - Search across all team findings files.

Searches across team session logs, findings files, and knowledge base entries
with support for keyword, regex, and category-based search. Returns ranked
results with team attribution.

Usage:
    python cross_team_search.py QUERY [OPTIONS]
    python cross_team_search.py "machine learning" --type keyword
    python cross_team_search.py "def\\s+\\w+search" --type regex
    python cross_team_search.py --category methodology --team TEAM-0005

Options:
    QUERY                   Search query (keyword or regex pattern)
    -t, --type TYPE         Search type: keyword|regex|category (default: keyword)
    --team TEAM             Filter by team name
    --category CAT          Filter by category
    --path DIR              Additional search path
    -n, --limit N           Max results to return (default: 20)
    --context N             Lines of context around matches (default: 2)
    -o, --output FILE       Output JSON to file
    --json                  Output as JSON (default: human-readable)
    -h, --help              Show this help
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Directories to search
SEARCH_DIRS = [
    os.path.join(REPO_ROOT, 'teams'),
    os.path.join(REPO_ROOT, 'knowledge-base', 'entries'),
    os.path.join(REPO_ROOT, 'storage', 'scripts'),
    os.path.join(REPO_ROOT, 'market-research'),
]


def find_searchable_files(extra_paths: list[str] | None = None) -> list[str]:
    """Find all searchable files in the repository."""
    extensions = {'.md', '.json', '.jsonl', '.py', '.txt'}
    files = []

    dirs = list(SEARCH_DIRS)
    if extra_paths:
        dirs.extend(extra_paths)

    for dirpath in dirs:
        if not os.path.isdir(dirpath):
            continue
        for root, _, filenames in os.walk(dirpath):
            for name in filenames:
                if any(name.endswith(ext) for ext in extensions):
                    files.append(os.path.join(root, name))

    return sorted(files)


def extract_team_from_file(filepath: str, content: str) -> str:
    """Try to determine which team produced this file."""
    # Check filename
    match = re.search(r'(TEAM-\d+|PROG-TEAM-\d+|RESEARCH-\d+)', filepath, re.I)
    if match:
        return match.group(1).upper()

    # Check YAML frontmatter
    match = re.search(r'^team:\s*(.+)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip()

    # Check JSON team field
    match = re.search(r'"team"\s*:\s*"([^"]+)"', content)
    if match:
        return match.group(1)

    # Check added_by_team in index
    match = re.search(r'"added_by_team"\s*:\s*"([^"]+)"', content)
    if match:
        return match.group(1)

    # Infer from path
    parts = filepath.split(os.sep)
    for part in parts:
        if re.match(r'(?i)(team|session|prog)', part):
            return part

    return "unknown"


def extract_category_from_file(filepath: str, content: str) -> str:
    """Extract category from file content."""
    match = re.search(r'^category:\s*(.+)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip().strip('[]')

    match = re.search(r'"category"\s*:\s*"([^"]+)"', content)
    if match:
        return match.group(1)

    return "unknown"


def search_keyword(content: str, query: str, context_lines: int = 2) -> list[dict]:
    """Search for keywords in content (case-insensitive)."""
    matches = []
    lines = content.split('\n')
    query_lower = query.lower()
    query_words = query_lower.split()

    for i, line in enumerate(lines):
        line_lower = line.lower()
        # All query words must be present in the line
        if all(w in line_lower for w in query_words):
            # Get context
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            context = '\n'.join(lines[start:end])

            matches.append({
                "line_number": i + 1,
                "line": line.strip(),
                "context": context.strip(),
                "score": _keyword_score(line_lower, query_words),
            })

    return matches


def _keyword_score(line: str, query_words: list[str]) -> float:
    """Score a keyword match - higher is better."""
    score = 0.0
    for word in query_words:
        count = line.count(word)
        score += count * (1.0 / max(1, len(line.split())))
    # Bonus for exact phrase
    if ' '.join(query_words) in line:
        score += 0.5
    return round(score, 4)


def search_regex(content: str, pattern: str, context_lines: int = 2) -> list[dict]:
    """Search using regex pattern."""
    matches = []
    lines = content.split('\n')

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        print(f"Invalid regex pattern: {e}", file=sys.stderr)
        return []

    for i, line in enumerate(lines):
        m = compiled.search(line)
        if m:
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            context = '\n'.join(lines[start:end])

            matches.append({
                "line_number": i + 1,
                "line": line.strip(),
                "match": m.group(0),
                "context": context.strip(),
                "score": 1.0,
            })

    return matches


def search_category(content: str, filepath: str, category: str) -> list[dict]:
    """Search for entries matching a specific category."""
    file_cat = extract_category_from_file(filepath, content)
    if category.lower() in file_cat.lower():
        # Return the whole file as a match
        preview = content[:500].strip()
        return [{
            "line_number": 1,
            "line": preview.split('\n')[0],
            "context": preview,
            "score": 1.0,
            "category": file_cat,
        }]
    return []


def search_files(query: str, search_type: str = "keyword",
                 team_filter: str | None = None,
                 category_filter: str | None = None,
                 extra_paths: list[str] | None = None,
                 context_lines: int = 2,
                 limit: int = 20) -> list[dict]:
    """Search across all team files and return ranked results."""
    files = find_searchable_files(extra_paths)
    all_results = []

    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except (IOError, OSError):
            continue

        team = extract_team_from_file(filepath, content)

        # Apply team filter
        if team_filter and team_filter.upper() not in team.upper():
            continue

        # Apply category filter (in addition to query)
        if category_filter:
            file_cat = extract_category_from_file(filepath, content)
            if category_filter.lower() not in file_cat.lower():
                continue

        # Search based on type
        if search_type == "keyword" and query:
            matches = search_keyword(content, query, context_lines)
        elif search_type == "regex" and query:
            matches = search_regex(content, query, context_lines)
        elif search_type == "category":
            cat = query or category_filter or ""
            matches = search_category(content, filepath, cat)
        else:
            continue

        # Add file metadata to each match
        rel_path = os.path.relpath(filepath, REPO_ROOT)
        for match in matches:
            match['file'] = rel_path
            match['team'] = team
            match['abs_path'] = filepath
            all_results.append(match)

    # Sort by score descending
    all_results.sort(key=lambda x: x.get('score', 0), reverse=True)

    return all_results[:limit]


def format_human_readable(results: list[dict]) -> str:
    """Format results for human reading."""
    if not results:
        return "No results found."

    lines = [f"Found {len(results)} result(s):\n"]

    for i, r in enumerate(results, 1):
        lines.append(f"--- Result {i} (score: {r.get('score', 0):.3f}) ---")
        lines.append(f"  File: {r['file']}")
        lines.append(f"  Team: {r['team']}")
        lines.append(f"  Line: {r.get('line_number', '?')}")
        if r.get('match'):
            lines.append(f"  Match: {r['match']}")
        lines.append(f"  Context:")
        for ctx_line in r.get('context', '').split('\n'):
            lines.append(f"    {ctx_line}")
        lines.append("")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Cross-Team Search - Search across all team findings",
    )
    parser.add_argument('query', nargs='?', default='',
                        help='Search query')
    parser.add_argument('-t', '--type', dest='search_type',
                        choices=['keyword', 'regex', 'category'],
                        default='keyword', help='Search type')
    parser.add_argument('--team', help='Filter by team name')
    parser.add_argument('--category', help='Filter by category')
    parser.add_argument('--path', action='append', default=[],
                        help='Additional search paths')
    parser.add_argument('-n', '--limit', type=int, default=20,
                        help='Max results (default: 20)')
    parser.add_argument('--context', type=int, default=2,
                        help='Context lines around matches')
    parser.add_argument('-o', '--output', help='Output JSON to file')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')

    args = parser.parse_args()

    if not args.query and not args.category:
        parser.error("Must specify a query or --category")

    results = search_files(
        query=args.query,
        search_type=args.search_type,
        team_filter=args.team,
        category_filter=args.category,
        extra_paths=args.path if args.path else None,
        context_lines=args.context,
        limit=args.limit,
    )

    if args.json or args.output:
        # Clean up abs_path for JSON output
        for r in results:
            r.pop('abs_path', None)
        output = {
            "metadata": {
                "tool": "cross_team_search.py",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": args.query,
                "search_type": args.search_type,
                "team_filter": args.team,
                "category_filter": args.category,
                "total_results": len(results),
            },
            "results": results,
        }
        output_json = json.dumps(output, indent=2, default=str)

        if args.output:
            with open(args.output, 'w') as f:
                f.write(output_json)
            print(f"Wrote {len(results)} results to {args.output}",
                  file=sys.stderr)
        else:
            print(output_json)
    else:
        print(format_human_readable(results))


if __name__ == '__main__':
    main()
