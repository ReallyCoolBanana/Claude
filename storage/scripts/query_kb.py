#!/usr/bin/env python3
"""Token-efficient query tool for the knowledge database.

Connects to storage/data/knowledge.db and supports compact queries
designed to minimize token usage in AI agent contexts.

Subcommands:
    summary [type]          Level 0 summary (entry counts, last updated)
    list <type>             Compact listing with optional field selection
    search <query>          FTS5 full-text search with BM25 ranking
    read <id>               Read a specific entry
    tags                    Tag cloud / frequency
    related <id>            Show entries connected via builds_on

Usage:
    python query_kb.py summary
    python query_kb.py summary KB
    python query_kb.py list KB --fields id,title,date
    python query_kb.py search "database optimization" --type KB --limit 5
    python query_kb.py read KB-0001 --fields title,summary,tags
    python query_kb.py tags --type KB
    python query_kb.py related KB-0031
    python query_kb.py search "coordination" --json --max-tokens 200
"""

import argparse
import csv
import io
import json
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "storage" / "data" / "knowledge.db"

VALID_TYPES = ['KB', 'SOP', 'SRC', 'SCR', 'TEAM', 'TOOL']

# Canonical field names for entries table
ENTRY_FIELDS = [
    'id', 'numeric_id', 'resource_type', 'title', 'summary', 'category',
    'status', 'confidence', 'file_path', 'content_hash', 'created_date',
    'updated_date', 'team', 'char_count', 'token_estimate', 'version',
    'description', 'extra_json',
]

# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def get_db():
    """Connect to the knowledge database."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}", file=sys.stderr)
        print("Run import_to_db.py first to create it.", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL;")
    db.execute("PRAGMA foreign_keys = ON;")
    return db


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def estimate_tokens(text):
    """Estimate tokens as chars / 4."""
    return len(text) // 4


def truncate_output(text, max_tokens):
    """Truncate output to fit within max_tokens budget."""
    if max_tokens <= 0:
        return text
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 20] + "\n... [truncated]"


def format_table(rows, columns):
    """Format rows as a compact aligned table."""
    if not rows:
        return "(no results)\n"

    # Compute column widths
    widths = {col: len(col) for col in columns}
    str_rows = []
    for row in rows:
        str_row = {}
        for col in columns:
            val = row[col] if col in row.keys() else ''
            if val is None:
                val = ''
            val = str(val)
            # Compact long values
            if len(val) > 60:
                val = val[:57] + '...'
            str_row[col] = val
            widths[col] = max(widths[col], len(val))
        str_rows.append(str_row)

    lines = []
    # Header
    header = '  '.join(col.ljust(widths[col]) for col in columns)
    lines.append(header)
    lines.append('  '.join('-' * widths[col] for col in columns))
    # Data
    for sr in str_rows:
        line = '  '.join(sr[col].ljust(widths[col]) for col in columns)
        lines.append(line)

    return '\n'.join(lines) + '\n'


def format_csv_output(rows, columns, delimiter=','):
    """Format rows as CSV/TSV."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[col] if col in row.keys() else '' for col in columns])
    return buf.getvalue()


def format_json_output(rows, columns=None):
    """Format rows as JSON."""
    result = []
    for row in rows:
        if columns:
            d = {col: row[col] if col in row.keys() else None for col in columns}
        else:
            d = dict(row)
        result.append(d)
    return json.dumps(result, indent=2) + '\n'


def output_result(rows, columns, fmt='table', max_tokens=0):
    """Format and output results with token estimate."""
    if fmt == 'json':
        text = format_json_output(rows, columns)
    elif fmt == 'csv':
        text = format_csv_output(rows, columns, delimiter=',')
    elif fmt == 'tsv':
        text = format_csv_output(rows, columns, delimiter='\t')
    else:
        text = format_table(rows, columns)

    if max_tokens > 0:
        text = truncate_output(text, max_tokens)

    print(text, end='')
    tokens = estimate_tokens(text)
    print(f"[~{tokens} tokens]", file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_summary(args):
    """Show Level 0 summary of the knowledge base."""
    db = get_db()

    if args.type:
        rtype = args.type.upper()
        if rtype not in VALID_TYPES:
            print(f"Error: Invalid type '{rtype}'. Valid: {', '.join(VALID_TYPES)}", file=sys.stderr)
            sys.exit(1)
        rows = db.execute(
            "SELECT * FROM v_summary WHERE resource_type = ?", (rtype,)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM v_summary").fetchall()

    columns = ['resource_type', 'entry_count', 'last_updated', 'categories']

    if args.json:
        text = format_json_output(rows, columns)
    else:
        text = format_table(rows, columns)

    if args.max_tokens > 0:
        text = truncate_output(text, args.max_tokens)

    print(text, end='')
    print(f"[~{estimate_tokens(text)} tokens]", file=sys.stderr)
    db.close()


def cmd_list(args):
    """List entries of a given type with optional field selection."""
    db = get_db()

    rtype = args.type.upper()
    if rtype not in VALID_TYPES:
        print(f"Error: Invalid type '{rtype}'. Valid: {', '.join(VALID_TYPES)}", file=sys.stderr)
        sys.exit(1)

    # Determine fields
    if args.fields:
        columns = [f.strip() for f in args.fields.split(',')]
        # Validate field names
        valid = set(ENTRY_FIELDS)
        for col in columns:
            if col not in valid:
                print(f"Warning: Unknown field '{col}', using anyway", file=sys.stderr)
    else:
        columns = ['id', 'title', 'category', 'created_date']

    select_clause = ', '.join(columns)
    rows = db.execute(
        f"SELECT {select_clause} FROM entries WHERE resource_type = ? ORDER BY numeric_id",
        (rtype,)
    ).fetchall()

    fmt = args.format or 'table'
    output_result(rows, columns, fmt, args.max_tokens)
    db.close()


def cmd_search(args):
    """Full-text search with BM25 ranking."""
    db = get_db()

    query = args.query
    limit = args.limit or 10

    # Build FTS5 match query
    type_filter = ""
    params = [query]
    if args.type:
        rtype = args.type.upper()
        if rtype not in VALID_TYPES:
            print(f"Error: Invalid type '{rtype}'. Valid: {', '.join(VALID_TYPES)}", file=sys.stderr)
            sys.exit(1)
        type_filter = "AND e.resource_type = ?"
        params.append(rtype)

    params.append(limit)

    sql = f"""
        SELECT e.id, e.title, e.resource_type, e.summary,
               bm25(entries_fts, 10.0, 5.0, 1.0) AS score
        FROM entries_fts f
        JOIN entries e ON f.rowid = e.rowid
        WHERE entries_fts MATCH ?
        {type_filter}
        ORDER BY score
        LIMIT ?
    """

    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # FTS5 match failures - try quoting as phrase
        if 'fts5' in str(exc).lower():
            escaped = '"' + query.replace('"', '""') + '"'
            params[0] = escaped
            rows = db.execute(sql, params).fetchall()
        else:
            raise

    columns = ['id', 'title', 'resource_type', 'summary', 'score']

    fmt = 'json' if args.json else (args.format or 'table')
    output_result(rows, columns, fmt, args.max_tokens)
    db.close()


def cmd_read(args):
    """Read a specific entry by ID."""
    db = get_db()

    entry_id = args.id.upper()

    row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        print(f"Error: Entry '{entry_id}' not found.", file=sys.stderr)
        sys.exit(1)

    # Determine fields
    if args.fields:
        columns = [f.strip() for f in args.fields.split(',')]
    else:
        columns = ['id', 'title', 'resource_type', 'summary', 'category', 'status',
                    'created_date', 'team', 'file_path', 'char_count', 'token_estimate']

    # Add tags and relationships if requested or default
    result = dict(row)

    # Fetch tags
    tags_rows = db.execute("SELECT tag FROM tags WHERE entry_id = ?", (entry_id,)).fetchall()
    result['tags'] = ', '.join(r['tag'] for r in tags_rows) if tags_rows else ''

    # Fetch relationships
    rels = db.execute(
        "SELECT target_id, rel_type FROM relationships WHERE source_id = ?",
        (entry_id,)
    ).fetchall()
    result['builds_on'] = ', '.join(r['target_id'] for r in rels if r['rel_type'] == 'builds_on')

    # Check if tags or builds_on requested
    if args.fields:
        if 'tags' in columns and 'tags' not in ENTRY_FIELDS:
            pass  # already added
        if 'builds_on' in columns and 'builds_on' not in ENTRY_FIELDS:
            pass  # already added
    else:
        columns.extend(['tags', 'builds_on'])

    # Build output
    if args.json:
        out = {col: result.get(col) for col in columns}
        text = json.dumps(out, indent=2) + '\n'
    else:
        lines = []
        for col in columns:
            val = result.get(col, '')
            if val is None:
                val = ''
            lines.append(f"{col}: {val}")
        text = '\n'.join(lines) + '\n'

    if args.max_tokens > 0:
        text = truncate_output(text, args.max_tokens)

    print(text, end='')
    print(f"[~{estimate_tokens(text)} tokens]", file=sys.stderr)
    db.close()


def cmd_tags(args):
    """Show tag cloud / frequency."""
    db = get_db()

    if args.type:
        rtype = args.type.upper()
        if rtype not in VALID_TYPES:
            print(f"Error: Invalid type '{rtype}'. Valid: {', '.join(VALID_TYPES)}", file=sys.stderr)
            sys.exit(1)
        rows = db.execute("""
            SELECT t.tag, COUNT(*) AS usage_count
            FROM tags t
            JOIN entries e ON t.entry_id = e.id
            WHERE e.resource_type = ?
            GROUP BY t.tag
            ORDER BY usage_count DESC
        """, (rtype,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM v_tag_cloud").fetchall()

    columns = ['tag', 'usage_count']

    fmt = 'json' if args.json else 'table'
    output_result(rows, columns, fmt, args.max_tokens)
    db.close()


def cmd_related(args):
    """Show entries connected via builds_on relationships."""
    db = get_db()

    entry_id = args.id.upper()

    # Check entry exists
    row = db.execute("SELECT id, title FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        print(f"Error: Entry '{entry_id}' not found.", file=sys.stderr)
        sys.exit(1)

    # Use recursive CTE for full ancestry chain
    rows = db.execute("""
        WITH RECURSIVE chain AS (
            SELECT target_id, 1 AS depth, 'parent' AS direction
            FROM relationships
            WHERE source_id = ? AND rel_type = 'builds_on'
            UNION ALL
            SELECT r.target_id, c.depth + 1, 'ancestor'
            FROM relationships r
            JOIN chain c ON r.source_id = c.target_id
            WHERE r.rel_type = 'builds_on' AND c.depth < 10
        ),
        children AS (
            SELECT source_id AS related_id, 1 AS depth, 'child' AS direction
            FROM relationships
            WHERE target_id = ? AND rel_type = 'builds_on'
        )
        SELECT e.id, e.title, e.resource_type, c.depth, c.direction
        FROM chain c
        JOIN entries e ON c.target_id = e.id
        UNION ALL
        SELECT e.id, e.title, e.resource_type, ch.depth, ch.direction
        FROM children ch
        JOIN entries e ON ch.related_id = e.id
        ORDER BY direction, depth
    """, (entry_id, entry_id)).fetchall()

    # Also find entries sharing tags
    shared_tag_rows = db.execute("""
        SELECT e2.id, e2.title, e2.resource_type, COUNT(*) AS shared_tags
        FROM tags t1
        JOIN tags t2 ON t1.tag = t2.tag AND t1.entry_id != t2.entry_id
        JOIN entries e2 ON t2.entry_id = e2.id
        WHERE t1.entry_id = ?
        GROUP BY e2.id
        ORDER BY shared_tags DESC
        LIMIT 5
    """, (entry_id,)).fetchall()

    # Print header
    print(f"Related entries for {entry_id} ({row['title']}):\n")

    if rows:
        print("--- Builds-on chain ---")
        columns = ['id', 'title', 'resource_type', 'depth', 'direction']
        text1 = format_table(rows, columns)
        print(text1)

    if shared_tag_rows:
        print("--- Shared tags ---")
        columns2 = ['id', 'title', 'resource_type', 'shared_tags']
        text2 = format_table(shared_tag_rows, columns2)
        print(text2)

    if not rows and not shared_tag_rows:
        print("(no related entries found)\n")

    db.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    # Parent parser with shared options so --json/--max-tokens work on every subcommand
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument('--json', action='store_true',
                        help='Output in JSON format')
    parent.add_argument('--max-tokens', type=int, default=0,
                        help='Maximum tokens for output (truncate to fit)')

    parser = argparse.ArgumentParser(
        description="Token-efficient query tool for the knowledge database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s summary                           # Level 0 overview
  %(prog)s summary KB                        # KB-only summary
  %(prog)s list KB --fields id,title,date    # Compact KB listing
  %(prog)s search "database" --type KB       # FTS5 search in KB entries
  %(prog)s read KB-0001                      # Read specific entry
  %(prog)s read KB-0001 --fields title,tags  # Read specific fields
  %(prog)s tags                              # Full tag cloud
  %(prog)s tags --type SRC                   # Tags for sources only
  %(prog)s related KB-0001                   # Relationship graph
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # summary
    p_summary = subparsers.add_parser('summary', parents=[parent],
                                       help='Level 0 summary (entry counts, last updated)')
    p_summary.add_argument('type', nargs='?', default=None,
                           help='Resource type to summarize (KB, SOP, SRC, SCR, TEAM, TOOL)')

    # list
    p_list = subparsers.add_parser('list', parents=[parent],
                                    help='Compact listing of entries by type')
    p_list.add_argument('type', help='Resource type (KB, SOP, SRC, SCR, TEAM, TOOL)')
    p_list.add_argument('--fields', type=str, default=None,
                        help='Comma-separated field names (default: id,title,category,created_date)')
    p_list.add_argument('--format', choices=['table', 'json', 'csv', 'tsv'], default=None,
                        help='Output format (default: table, or json if --json)')

    # search
    p_search = subparsers.add_parser('search', parents=[parent],
                                      help='FTS5 full-text search with BM25 ranking')
    p_search.add_argument('query', help='Search query')
    p_search.add_argument('--type', type=str, default=None,
                          help='Filter by resource type')
    p_search.add_argument('--limit', '-n', type=int, default=10,
                          help='Maximum results (default: 10)')
    p_search.add_argument('--format', choices=['table', 'json', 'csv'], default=None,
                          help='Output format')

    # read
    p_read = subparsers.add_parser('read', parents=[parent],
                                    help='Read a specific entry by ID')
    p_read.add_argument('id', help='Entry ID (e.g., KB-0001, SOP-020)')
    p_read.add_argument('--fields', type=str, default=None,
                        help='Comma-separated field names (e.g., title,summary,tags)')

    # tags
    p_tags = subparsers.add_parser('tags', parents=[parent],
                                    help='Show tag cloud / frequency')
    p_tags.add_argument('--type', type=str, default=None,
                        help='Filter by resource type')

    # related
    p_related = subparsers.add_parser('related', parents=[parent],
                                       help='Show related entries via builds_on')
    p_related.add_argument('id', help='Entry ID to find relationships for')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # If --json is set but format is not set on list/search, use json
    if hasattr(args, 'format') and args.format is None and args.json:
        args.format = 'json'

    commands = {
        'summary': cmd_summary,
        'list': cmd_list,
        'search': cmd_search,
        'read': cmd_read,
        'tags': cmd_tags,
        'related': cmd_related,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
