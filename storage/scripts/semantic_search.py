#!/usr/bin/env python3
"""Semantic search over the Claude Agent knowledge base using LanceDB.

Usage:
    python semantic_search.py "agent communication protocols"
    python semantic_search.py --hybrid "rate limiter bugs"
    python semantic_search.py --type kb "MCP server"
    python semantic_search.py --top 5 --hybrid --type sop "coordination"
"""
import argparse, os, sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "lance_db")
TABLE_NAME = "documents"
MODEL_NAME = "all-MiniLM-L6-v2"
_model_cache = {}


def _get_model():
    if "m" not in _model_cache:
        from sentence_transformers import SentenceTransformer
        _model_cache["m"] = SentenceTransformer(MODEL_NAME)
    return _model_cache["m"]


def _open_table():
    try:
        import lancedb
    except ImportError:
        sys.exit("Error: lancedb not installed. Run: pip install lancedb sentence-transformers")
    if not os.path.isdir(DB_PATH):
        sys.exit(f"Error: Database not found at {DB_PATH}\nRun the indexer first to build the index.")
    db = lancedb.connect(DB_PATH)
    try:
        return db.open_table(TABLE_NAME)
    except Exception as e:
        sys.exit(f"Error opening table '{TABLE_NAME}': {e}\nThe index may not have been built yet.")


def semantic_search(query: str, top_k: int = 10, source_type: str = None) -> list[dict]:
    """Vector similarity search. Returns results sorted by distance (lower = better)."""
    table = _open_table()
    vec = _get_model().encode(query).tolist()
    q = table.search(vec).limit(top_k)
    if source_type:
        q = q.where(f"source_type = '{source_type}'")
    return q.to_list()


def hybrid_search(query: str, top_k: int = 10, source_type: str = None) -> list[dict]:
    """Combined vector + BM25 full-text search. Falls back to vector if no FTS index."""
    table = _open_table()
    try:
        vec = _get_model().encode(query).tolist()
        q = table.search(query, vector_column_name="vector", query_type="hybrid").limit(top_k)
        if source_type:
            q = q.where(f"source_type = '{source_type}'")
        return q.to_list()
    except Exception:
        return semantic_search(query, top_k, source_type)


def _format_results(results: list[dict], hybrid_mode: bool = False):
    if not results:
        print("No results found."); return
    score_key = "_relevance_score" if hybrid_mode else "_distance"
    for i, r in enumerate(results, 1):
        score = r.get(score_key, r.get("_distance", r.get("_relevance_score", "?")))
        title = r.get("title", "untitled")
        eid = r.get("entry_id", "")
        snippet = (r.get("text", "") or "")[:120].replace("\n", " ")
        src = r.get("source_type", "")
        label = "relevance" if hybrid_mode else "distance"
        fmt_score = f"{score:.4f}" if isinstance(score, float) else str(score)
        print(f"  {i}. [{src}] {eid:12s} {title}")
        print(f"     {label}: {fmt_score}")
        print(f"     {snippet}...")
        print()


def main():
    ap = argparse.ArgumentParser(description="Search the Claude Agent knowledge base")
    ap.add_argument("query", help="Search query text")
    ap.add_argument("--hybrid", action="store_true", help="Use hybrid vector+BM25 search")
    ap.add_argument("--type", dest="source_type", help="Filter: kb, team_log, script, sop")
    ap.add_argument("--top", type=int, default=10, help="Number of results (default: 10)")
    args = ap.parse_args()
    fn = hybrid_search if args.hybrid else semantic_search
    _format_results(fn(args.query, args.top, args.source_type), hybrid_mode=args.hybrid)


if __name__ == "__main__":
    main()
