#!/usr/bin/env python3
"""
kb_search.py — Full-text search across knowledge base entries, team logs, and script metadata.

Reuses TF-IDF functions from dedup_utils.py for relevance ranking.
Pure stdlib — no external dependencies.

Usage:
    python3 kb_search.py "query"
    python3 kb_search.py "data gathering" --tags benchmarks,iteration-2
    python3 kb_search.py "validation" --category utility --type script
    python3 kb_search.py "hybrid method" --type kb,team --top 5
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_ENTRIES_DIR = os.path.join(BASE_DIR, "knowledge-base", "entries")
TEAM_SESSIONS_DIR = os.path.join(BASE_DIR, "teams", "sessions")
SCRIPTS_INDEX = os.path.join(BASE_DIR, "storage", "scripts", "index.json")
KB_INDEX = os.path.join(BASE_DIR, "knowledge-base", "index.json")

# ---------------------------------------------------------------------------
# TF-IDF implementation (adapted from dedup_utils.py)
# ---------------------------------------------------------------------------
STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "this", "that",
    "these", "those", "it", "its", "not", "no", "nor", "so", "if", "than",
    "too", "very", "just", "about", "above", "after", "again", "all", "also",
    "am", "any", "as", "because", "before", "between", "both", "each",
    "even", "few", "get", "got", "he", "her", "here", "him", "his", "how",
    "into", "made", "make", "many", "me", "more", "most", "much", "my",
    "new", "now", "off", "old", "only", "other", "our", "out", "over",
    "own", "same", "she", "some", "such", "tell", "then", "them", "there",
    "they", "through", "under", "until", "upon", "use", "used", "using",
    "was", "we", "well", "what", "when", "where", "which", "while", "who",
    "whom", "why", "you", "your",
}


def tokenize(text):
    """Tokenize text into lowercase words, removing stop words."""
    if not text:
        return []
    words = re.findall(r'\b[a-zA-Z]{2,}\b', text.lower())
    return [w for w in words if w not in STOP_WORDS]


def compute_tf(tokens):
    if not tokens:
        return {}
    counter = Counter(tokens)
    total = len(tokens)
    return {word: count / total for word, count in counter.items()}


def compute_idf(documents):
    n_docs = len(documents)
    if n_docs == 0:
        return {}
    doc_freq = Counter()
    for tokens in documents:
        doc_freq.update(set(tokens))
    return {
        word: math.log((n_docs + 1) / (freq + 1)) + 1
        for word, freq in doc_freq.items()
    }


def compute_tfidf_vector(tokens, idf):
    tf = compute_tf(tokens)
    return {word: tf_val * idf.get(word, 1.0) for word, tf_val in tf.items()}


def cosine_similarity(vec_a, vec_b):
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a) & set(vec_b)
    dot = sum(vec_a[k] * vec_b[k] for k in common)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# YAML frontmatter parser (simple, handles this repo's format)
# ---------------------------------------------------------------------------
def parse_frontmatter(text):
    """Parse YAML frontmatter from markdown text. Returns (metadata_dict, body_text)."""
    metadata = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            yaml_block = parts[1].strip()
            body = parts[2].strip()
            for line in yaml_block.split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    # Handle bracket lists: [a, b, c]
                    if val.startswith("[") and val.endswith("]"):
                        items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",")]
                        metadata[key] = [x for x in items if x]
                    elif val.startswith('"') and val.endswith('"'):
                        metadata[key] = val[1:-1]
                    else:
                        metadata[key] = val
    return metadata, body


# ---------------------------------------------------------------------------
# Document indexing
# ---------------------------------------------------------------------------
class Document:
    """Represents an indexed document."""
    __slots__ = ("doc_type", "doc_id", "title", "body", "tags", "category",
                 "team", "filepath", "tokens")

    def __init__(self, doc_type, doc_id, title, body, tags=None, category="",
                 team="", filepath=""):
        self.doc_type = doc_type  # "kb", "team", "script"
        self.doc_id = doc_id
        self.title = title
        self.body = body
        self.tags = tags or []
        self.category = category
        self.team = team
        self.filepath = filepath
        self.tokens = tokenize(title + " " + body)


def index_kb_entries():
    """Index all .md files in knowledge-base/entries/."""
    docs = []
    if not os.path.isdir(KB_ENTRIES_DIR):
        return docs
    for fname in sorted(os.listdir(KB_ENTRIES_DIR)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(KB_ENTRIES_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read()
        except (IOError, OSError):
            continue
        meta, body = parse_frontmatter(text)
        doc_id = meta.get("id", fname.replace(".md", ""))
        title = ""
        # Extract first heading as title
        for line in body.split("\n"):
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                break
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        docs.append(Document(
            doc_type="kb",
            doc_id=doc_id,
            title=title,
            body=body,
            tags=tags,
            category=meta.get("category", ""),
            team=meta.get("team", ""),
            filepath=fpath,
        ))
    return docs


def index_team_sessions():
    """Index all TEAM-*.md files in teams/sessions/."""
    docs = []
    if not os.path.isdir(TEAM_SESSIONS_DIR):
        return docs
    for fname in sorted(os.listdir(TEAM_SESSIONS_DIR)):
        if not fname.endswith(".md") or not fname.startswith("TEAM-"):
            continue
        fpath = os.path.join(TEAM_SESSIONS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read()
        except (IOError, OSError):
            continue
        meta, body = parse_frontmatter(text)
        team_id = meta.get("team_id", fname.replace(".md", ""))
        title = meta.get("objective", "")
        if isinstance(title, str) and title.startswith('"') and title.endswith('"'):
            title = title[1:-1]
        docs.append(Document(
            doc_type="team",
            doc_id=team_id,
            title=title,
            body=body,
            tags=[],
            category="",
            team=team_id,
            filepath=fpath,
        ))
    return docs


def index_scripts():
    """Index all script descriptions from storage/scripts/index.json."""
    docs = []
    if not os.path.isfile(SCRIPTS_INDEX):
        return docs
    try:
        with open(SCRIPTS_INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, json.JSONDecodeError):
        return docs
    for script in data.get("scripts", []):
        sid = script.get("id", "")
        name = script.get("name", "")
        desc = script.get("description", "")
        cat = script.get("category", "")
        team = script.get("added_by_team", "")
        docs.append(Document(
            doc_type="script",
            doc_id=sid,
            title=name,
            body=desc,
            tags=[],
            category=cat,
            team=team,
            filepath=os.path.join(os.path.dirname(SCRIPTS_INDEX),
                                  script.get("filename", "")),
        ))
    return docs


# ---------------------------------------------------------------------------
# Search engine
# ---------------------------------------------------------------------------
def search(query, documents, top_n=10):
    """
    Rank documents by TF-IDF cosine similarity to query.
    Returns list of (score, document) tuples, descending by score.
    """
    if not documents:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    # Build IDF from all documents + query
    all_token_lists = [doc.tokens for doc in documents] + [query_tokens]
    idf = compute_idf(all_token_lists)

    query_vec = compute_tfidf_vector(query_tokens, idf)

    results = []
    for doc in documents:
        doc_vec = compute_tfidf_vector(doc.tokens, idf)
        score = cosine_similarity(query_vec, doc_vec)
        if score > 0.0:
            results.append((score, doc))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_n]


def get_snippet(body, query, max_len=200):
    """Extract the most relevant snippet from body text."""
    query_words = set(tokenize(query))
    if not query_words:
        return body[:max_len].replace("\n", " ").strip()

    # Find the paragraph/section with the most query word hits
    sections = re.split(r'\n\n+', body)
    best_section = ""
    best_hits = 0
    for section in sections:
        section_lower = section.lower()
        hits = sum(1 for w in query_words if w in section_lower)
        if hits > best_hits:
            best_hits = hits
            best_section = section

    if not best_section:
        best_section = body

    # Clean and truncate
    snippet = best_section.replace("\n", " ").strip()
    snippet = re.sub(r'\s+', ' ', snippet)
    if len(snippet) > max_len:
        snippet = snippet[:max_len].rsplit(" ", 1)[0] + "..."
    return snippet


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
def filter_documents(documents, tags=None, category=None, doc_types=None):
    """Filter documents by tags, category, and/or type."""
    filtered = documents

    if doc_types:
        type_set = set(doc_types)
        filtered = [d for d in filtered if d.doc_type in type_set]

    if category:
        cat_lower = category.lower()
        filtered = [d for d in filtered if d.category.lower() == cat_lower]

    if tags:
        tag_set = set(t.lower() for t in tags)
        filtered = [d for d in filtered
                    if any(t.lower() in tag_set for t in d.tags)]

    return filtered


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def format_results(results, query, output_json=False):
    """Format search results for display."""
    if output_json:
        items = []
        for score, doc in results:
            items.append({
                "score": round(score, 4),
                "type": doc.doc_type,
                "id": doc.doc_id,
                "title": doc.title,
                "category": doc.category,
                "team": doc.team,
                "tags": doc.tags,
                "snippet": get_snippet(doc.body, query),
                "filepath": doc.filepath,
            })
        return json.dumps(items, indent=2)

    lines = []
    lines.append(f"Search results for: \"{query}\"")
    lines.append(f"Found {len(results)} result(s)\n")

    for rank, (score, doc) in enumerate(results, 1):
        type_label = {"kb": "KB Entry", "team": "Team Log", "script": "Script"}
        snippet = get_snippet(doc.body, query)
        lines.append(f"  {rank}. [{type_label.get(doc.doc_type, doc.doc_type)}] "
                      f"{doc.doc_id} — {doc.title}")
        lines.append(f"     Score: {score:.4f} | Category: {doc.category or 'N/A'} | "
                      f"Team: {doc.team or 'N/A'}")
        if doc.tags:
            lines.append(f"     Tags: {', '.join(doc.tags)}")
        lines.append(f"     {snippet}")
        lines.append(f"     File: {doc.filepath}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Full-text search across knowledge base, team logs, and scripts.")
    parser.add_argument("query", help="Search query string")
    parser.add_argument("--tags", default=None,
                        help="Filter by tags (comma-separated)")
    parser.add_argument("--category", default=None,
                        help="Filter by category")
    parser.add_argument("--type", dest="doc_type", default=None,
                        help="Filter by type: kb, team, script (comma-separated)")
    parser.add_argument("--top", type=int, default=10,
                        help="Number of results to return (default: 10)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    # Build index
    all_docs = []
    all_docs.extend(index_kb_entries())
    all_docs.extend(index_team_sessions())
    all_docs.extend(index_scripts())

    if not all_docs:
        print("No documents found to index.", file=sys.stderr)
        sys.exit(1)

    # Apply filters
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    doc_types = [t.strip() for t in args.doc_type.split(",")] if args.doc_type else None

    filtered = filter_documents(all_docs, tags=tags, category=args.category,
                                doc_types=doc_types)

    if not filtered:
        print("No documents match the specified filters.", file=sys.stderr)
        sys.exit(1)

    # Search
    results = search(args.query, filtered, top_n=args.top)

    if not results:
        print(f"No results found for: \"{args.query}\"")
        sys.exit(0)

    print(format_results(results, args.query, output_json=args.json))


if __name__ == "__main__":
    main()
