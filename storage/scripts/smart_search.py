#!/usr/bin/env python3
"""smart_search.py - Token-budget-aware search across all resource types.

Searches KB entries, SOPs, sources, team logs, and scripts with
configurable token budgets. Uses FTS5 if available, falls back to grep.

Usage:
    python smart_search.py "database" --types kb,sop --max-results 5
    python smart_search.py "stress test" --max-tokens 500 --format json
    python smart_search.py "cross-reference" --types kb,team,script
    python smart_search.py "api" --types src --max-results 3 --format csv
"""

import argparse
import json
import os
import re
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Resource type configs for search
SEARCH_DIRS = {
    "kb": {
        "dir": "knowledge-base/entries",
        "ext": ".md",
        "id_pattern": re.compile(r"^(KB-\d{4})\.md$"),
        "label": "knowledge_base",
    },
    "team": {
        "dir": "teams/sessions",
        "ext": ".md",
        "id_pattern": re.compile(r"^(TEAM-\d{4})\.md$"),
        "label": "team_log",
    },
    "sop": {
        "dir": "storage/coordination/sops",
        "ext": ".json",
        "id_pattern": re.compile(r"^(?:sop_\d{3}_.*|SOP-\d{4})\.json$"),
        "label": "sop",
    },
    "src": {
        "dir": "storage/sources",
        "ext": ".json",
        "id_pattern": re.compile(r"^(SRC-\d{4}).*\.json$"),
        "label": "data_source",
    },
    "script": {
        "dir": "storage/scripts",
        "ext": ".py",
        "id_pattern": re.compile(r"^(.+)\.py$"),
        "label": "script",
    },
}

DB_PATH = os.path.join(REPO_ROOT, "storage/data/knowledge.db")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def extract_title_md(content: str) -> str:
    """Extract title from Markdown (first # heading or frontmatter)."""
    # Check frontmatter for title-like field
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            front = content[4:end]
            for line in front.split("\n"):
                if line.strip().startswith("id:"):
                    pass  # skip id
                if line.strip().startswith("objective:"):
                    return line.split(":", 1)[1].strip().strip('"')

    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1) if match else "(no title)"


def extract_title_json(content: str) -> str:
    """Extract title from JSON."""
    try:
        data = json.loads(content)
        return data.get("title", data.get("name", data.get("id", "(no title)")))
    except (json.JSONDecodeError, AttributeError):
        return "(no title)"


def extract_id_from_filename(filename: str, resource_type: str) -> str:
    """Extract the ID from a filename."""
    config = SEARCH_DIRS[resource_type]
    match = config["id_pattern"].match(filename)
    if match and match.groups():
        return match.group(1)
    # For SOPs with old naming
    sop_match = re.match(r"sop_(\d{3})_.*\.json$", filename)
    if sop_match:
        return f"SOP-{int(sop_match.group(1)):04d}"
    return filename


def get_snippet(content: str, query: str, context_chars: int = 50) -> str:
    """Get a snippet around the first match of query in content."""
    query_lower = query.lower()
    content_lower = content.lower()

    # Try each word of the query
    words = query_lower.split()
    pos = -1
    for word in words:
        pos = content_lower.find(word)
        if pos != -1:
            break

    if pos == -1:
        # Return first 100 chars as fallback
        return content[:100].replace("\n", " ").strip()

    start = max(0, pos - context_chars)
    end = min(len(content), pos + len(words[0] if words else query) + context_chars)
    snippet = content[start:end].replace("\n", " ").strip()

    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet


def score_match(content: str, query: str) -> float:
    """Simple relevance scoring: count query term occurrences, weighted by location."""
    content_lower = content.lower()
    query_lower = query.lower()
    words = query_lower.split()

    score = 0.0
    # Exact phrase match gets highest score
    if query_lower in content_lower:
        score += 10.0

    for word in words:
        if len(word) < 2:
            continue
        # Count occurrences
        count = content_lower.count(word)
        if count > 0:
            score += min(count, 5)  # Cap at 5 per word

            # Bonus for match in title area (first 200 chars)
            if word in content_lower[:200]:
                score += 3.0

            # Bonus for match in frontmatter/metadata
            if word in content_lower[:500]:
                score += 1.0

    return score


def search_fts5(query: str, types: list, max_results: int) -> list:
    """Search using FTS5 database if available."""
    if not os.path.isfile(DB_PATH):
        return None  # Signal to fall back

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Check if FTS table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
        )
        if not cursor.fetchone():
            conn.close()
            return None

        type_labels = [SEARCH_DIRS[t]["label"] for t in types if t in SEARCH_DIRS]
        placeholders = ",".join("?" for _ in type_labels)

        sql = f"""
            SELECT id, title, resource_type, snippet(knowledge_fts, 3, '', '', '...', 20),
                   rank
            FROM knowledge_fts
            WHERE knowledge_fts MATCH ?
            AND resource_type IN ({placeholders})
            ORDER BY rank
            LIMIT ?
        """
        params = [query] + type_labels + [max_results]
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "title": row[1],
                "resource_type": row[2],
                "snippet": row[3],
                "relevance_score": round(-row[4], 2) if row[4] else 0,
            })
        return results

    except (sqlite3.Error, Exception):
        return None


def search_grep(query: str, types: list, max_results: int, max_tokens: int) -> list:
    """Grep-based search across all indexed directories."""
    results = []
    tokens_used = 0

    for rtype in types:
        if rtype not in SEARCH_DIRS:
            continue

        config = SEARCH_DIRS[rtype]
        abs_dir = os.path.join(REPO_ROOT, config["dir"])

        if not os.path.isdir(abs_dir):
            continue

        for fname in sorted(os.listdir(abs_dir)):
            if not fname.endswith(config["ext"]):
                continue
            # Skip index.json, README, templates
            if fname in ("index.json", "README.md", "TEMPLATE.md", "TEMPLATE.json"):
                continue

            filepath = os.path.join(abs_dir, fname)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except (OSError, IOError):
                continue

            score = score_match(content, query)
            if score <= 0:
                continue

            # Extract metadata
            entry_id = extract_id_from_filename(fname, rtype)
            if config["ext"] == ".md":
                title = extract_title_md(content)
            else:
                title = extract_title_json(content)

            snippet = get_snippet(content, query)

            result = {
                "id": entry_id,
                "title": title,
                "resource_type": config["label"],
                "relevance_score": round(score, 1),
                "snippet": snippet,
            }

            result_tokens = estimate_tokens(json.dumps(result))
            if max_tokens > 0 and tokens_used + result_tokens > max_tokens:
                break

            results.append(result)
            tokens_used += result_tokens

            if len(results) >= max_results:
                break

        if len(results) >= max_results:
            break
        if max_tokens > 0 and tokens_used >= max_tokens:
            break

    # Sort by relevance score descending
    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results[:max_results]


def estimate_full_read_tokens(results: list, types: list) -> int:
    """Estimate how many tokens a naive full-read approach would cost."""
    # Average file sizes by type (from baseline data)
    avg_tokens = {
        "kb": 4000,
        "team": 900,
        "sop": 5000,
        "src": 300,
        "script": 500,
    }
    # Reading full index + each matched file
    total = 0
    seen_types = set()
    for r in results:
        for t in types:
            if SEARCH_DIRS.get(t, {}).get("label") == r["resource_type"]:
                total += avg_tokens.get(t, 1000)
                seen_types.add(t)
                break

    # Add index read cost per type searched
    index_costs = {"kb": 6093, "team": 3530, "sop": 1802, "src": 6936, "script": 6684}
    for t in types:
        total += index_costs.get(t, 2000)

    return total


def format_results(results: list, fmt: str, tokens_used: int,
                   tokens_saved: int, full_read_tokens: int) -> str:
    """Format search results."""
    if fmt == "json":
        output = {
            "results": results,
            "stats": {
                "results_found": len(results),
                "tokens_used": tokens_used,
                "full_read_tokens": full_read_tokens,
                "tokens_saved": tokens_saved,
                "savings_pct": round(100 * tokens_saved / max(1, full_read_tokens), 1),
            },
        }
        return json.dumps(output, indent=2)

    elif fmt == "csv":
        lines = ["id,title,resource_type,relevance_score,snippet"]
        for r in results:
            snippet = r["snippet"].replace('"', '""')
            lines.append(f'{r["id"]},"{r["title"]}",{r["resource_type"]},{r["relevance_score"]},"{snippet}"')
        return "\n".join(lines)

    else:  # text
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. [{r['id']}] {r['title']}")
            lines.append(f"   Type: {r['resource_type']} | Score: {r['relevance_score']}")
            lines.append(f"   {r['snippet']}")
            lines.append("")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Token-budget-aware search across all resource types. "
        "Uses FTS5 if available, falls back to grep-based search.",
        epilog="Examples:\n"
        '  smart_search.py "database" --types kb,sop\n'
        '  smart_search.py "stress test" --max-tokens 500 --format json\n'
        '  smart_search.py "api" --types src --max-results 3',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", help="Search query string")
    parser.add_argument("--types", "-t",
                        default="kb,sop,src,team,script",
                        help="Comma-separated resource types to search (default: all)")
    parser.add_argument("--max-results", "-n", type=int, default=10,
                        help="Maximum number of results (default: 10)")
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="Token budget for results (0 = unlimited)")
    parser.add_argument("--format", "-f", choices=["json", "csv", "text"],
                        default="text", help="Output format (default: text)")

    args = parser.parse_args()

    types = [t.strip() for t in args.types.split(",")]
    invalid_types = [t for t in types if t not in SEARCH_DIRS]
    if invalid_types:
        print(f"Warning: Unknown types ignored: {', '.join(invalid_types)}", file=sys.stderr)
        types = [t for t in types if t in SEARCH_DIRS]

    if not types:
        print("Error: No valid resource types specified.", file=sys.stderr)
        sys.exit(1)

    # Try FTS5 first
    results = search_fts5(args.query, types, args.max_results)

    if results is None:
        # Fall back to grep-based search
        results = search_grep(args.query, types, args.max_results, args.max_tokens)

    # Calculate token stats
    output_text = format_results(results, args.format, 0, 0, 0)
    tokens_used = estimate_tokens(output_text)
    full_read_tokens = estimate_full_read_tokens(results, types)
    tokens_saved = max(0, full_read_tokens - tokens_used)

    # Re-format with actual stats
    output_text = format_results(results, args.format, tokens_used, tokens_saved, full_read_tokens)
    tokens_used = estimate_tokens(output_text)

    print(output_text)
    print(f"\n--- Search stats: {len(results)} results, {tokens_used} tokens used, "
          f"{tokens_saved} tokens saved vs full-read ({full_read_tokens} tokens) ---",
          file=sys.stderr)


if __name__ == "__main__":
    main()
