#!/usr/bin/env python3
"""
Semantic Indexer - Indexes KB entries into LanceDB for semantic search.

Usage:
    python semantic_indexer.py              # Full reindex of all KB entries
    python semantic_indexer.py --search "query text"  # Search the index
    python semantic_indexer.py --info        # Show index stats

Requirements:
    pip install lancedb sentence-transformers

Creates a LanceDB database at storage/data/lance_db/ with:
- Vector embeddings via sentence-transformers (all-MiniLM-L6-v2)
- Full-text search index for hybrid search
- Metadata fields for filtering (category, tags, date, etc.)
"""

import glob
import os
import sys
import yaml
import argparse
from pathlib import Path

import lancedb
from lancedb.pydantic import LanceModel, Vector
from lancedb.embeddings import get_registry

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # /home/user/Claude
KB_ENTRIES_DIR = REPO_ROOT / "knowledge-base" / "entries"
LANCE_DB_PATH = REPO_ROOT / "storage" / "data" / "lance_db"
TABLE_NAME = "documents"
MODEL_NAME = "all-MiniLM-L6-v2"

# ---------------------------------------------------------------------------
# Embedding model & schema
# ---------------------------------------------------------------------------

model = get_registry().get("sentence-transformers").create(
    name=MODEL_NAME, device="cpu"
)


class Document(LanceModel):
    """Schema for indexed documents with auto-generated embeddings."""
    text: str = model.SourceField()
    vector: Vector(model.ndims()) = model.VectorField()
    entry_id: str
    title: str
    source_path: str
    source_type: str
    category: str = ""
    tags: str = ""
    date: str = ""


# ---------------------------------------------------------------------------
# YAML frontmatter parser
# ---------------------------------------------------------------------------

def parse_kb_entry(filepath: str) -> dict | None:
    """Parse a KB Markdown file into frontmatter dict + body text."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Split on YAML frontmatter delimiters
    if not content.startswith("---"):
        print(f"  [WARN] No frontmatter in {filepath}, skipping.")
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        print(f"  [WARN] Malformed frontmatter in {filepath}, skipping.")
        return None

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        print(f"  [WARN] YAML parse error in {filepath}: {e}")
        return None

    body = parts[2].strip()

    # Extract title from first heading or frontmatter
    title = ""
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break

    if not title:
        title = frontmatter.get("title", frontmatter.get("id", "Untitled"))

    # Build tags as comma-separated string
    tags_raw = frontmatter.get("tags", [])
    if isinstance(tags_raw, list):
        tags_str = ", ".join(str(t) for t in tags_raw)
    else:
        tags_str = str(tags_raw)

    # builds_on for reference
    builds_on = frontmatter.get("builds_on", [])
    if isinstance(builds_on, list):
        builds_on_str = ", ".join(str(b) for b in builds_on)
    else:
        builds_on_str = str(builds_on)

    # Text to embed: title + full body
    text = f"{title}\n\n{body}"

    # Relative path from repo root
    rel_path = os.path.relpath(filepath, REPO_ROOT)

    return {
        "text": text,
        "entry_id": str(frontmatter.get("id", "")),
        "title": title,
        "source_path": rel_path,
        "source_type": "kb",
        "category": str(frontmatter.get("category", "")),
        "tags": tags_str,
        "date": str(frontmatter.get("date", "")),
    }


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def collect_kb_entries() -> list[dict]:
    """Scan KB entries directory and parse all entries."""
    pattern = str(KB_ENTRIES_DIR / "KB-*.md")
    files = sorted(glob.glob(pattern))
    print(f"Found {len(files)} KB entry files.")

    entries = []
    for filepath in files:
        entry = parse_kb_entry(filepath)
        if entry:
            entries.append(entry)
        else:
            print(f"  [SKIP] {filepath}")

    print(f"Parsed {len(entries)} entries successfully.")
    return entries


def build_index(entries: list[dict]) -> None:
    """Create or overwrite the LanceDB index with the given entries."""
    # Ensure the database directory exists
    LANCE_DB_PATH.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to LanceDB at {LANCE_DB_PATH}")
    db = lancedb.connect(str(LANCE_DB_PATH))

    # Create table with schema (overwrite if exists)
    print(f"Creating table '{TABLE_NAME}' with {len(entries)} entries...")
    print(f"  Embedding model: {MODEL_NAME} ({model.ndims()} dimensions)")
    print("  Generating embeddings (this may take a moment on first run)...")

    table = db.create_table(TABLE_NAME, schema=Document, mode="overwrite")
    table.add(entries)

    print(f"  Added {len(entries)} entries to the table.")

    # Create FTS index for hybrid search
    print("Creating full-text search index...")
    try:
        table.create_fts_index("text", replace=True)
        print("  FTS index created successfully.")
    except Exception as e:
        print(f"  [WARN] FTS index creation failed: {e}")
        print("  Vector search will still work; FTS/hybrid search unavailable.")

    return table


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_index(query: str, limit: int = 10, query_type: str = "hybrid"):
    """Search the index and print results."""
    db = lancedb.connect(str(LANCE_DB_PATH))
    table = db.open_table(TABLE_NAME)

    print(f"\nSearching for: '{query}' (type={query_type}, limit={limit})")
    print("-" * 60)

    try:
        results = (
            table.search(query, query_type=query_type)
            .limit(limit)
            .to_list()
        )
    except Exception:
        # Fall back to vector search if hybrid fails
        print("  [INFO] Falling back to vector search.")
        results = (
            table.search(query)
            .limit(limit)
            .to_list()
        )

    for i, row in enumerate(results, 1):
        score = row.get("_distance", row.get("_score", "N/A"))
        print(f"\n{i}. [{row['entry_id']}] {row['title']}")
        print(f"   Category: {row['category']} | Date: {row['date']}")
        print(f"   Tags: {row['tags']}")
        print(f"   Path: {row['source_path']}")
        print(f"   Score: {score}")
        # Show first 200 chars of text
        snippet = row["text"][:200].replace("\n", " ")
        print(f"   Snippet: {snippet}...")

    print(f"\n{len(results)} results returned.")
    return results


def show_info():
    """Show index statistics."""
    db = lancedb.connect(str(LANCE_DB_PATH))
    try:
        table = db.open_table(TABLE_NAME)
    except Exception:
        print("No index found. Run without arguments to build the index first.")
        return

    count = table.count_rows()
    print(f"LanceDB Index Info")
    print(f"  Database path: {LANCE_DB_PATH}")
    print(f"  Table: {TABLE_NAME}")
    print(f"  Total entries: {count}")
    print(f"  Embedding model: {MODEL_NAME} ({model.ndims()} dims)")

    # Show a sample
    sample = table.search("").limit(3).to_list()
    print(f"\n  Sample entries:")
    for row in sample:
        print(f"    - [{row['entry_id']}] {row['title']} ({row['category']})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Semantic Indexer for KB entries")
    parser.add_argument("--search", type=str, help="Search query")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--query-type", type=str, default="hybrid",
                        choices=["hybrid", "vector", "fts"],
                        help="Search type (default: hybrid)")
    parser.add_argument("--info", action="store_true", help="Show index stats")
    args = parser.parse_args()

    if args.info:
        show_info()
    elif args.search:
        search_index(args.search, limit=args.limit, query_type=args.query_type)
    else:
        # Full reindex
        print("=" * 60)
        print("Semantic Indexer - Full Reindex")
        print("=" * 60)
        entries = collect_kb_entries()
        if not entries:
            print("No entries found. Nothing to index.")
            sys.exit(1)
        build_index(entries)
        print("\nDone! Index is ready for search.")
        print(f"  Use: python {__file__} --search 'your query'")


if __name__ == "__main__":
    main()
