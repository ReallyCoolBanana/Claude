#!/usr/bin/env python3
"""
DG-0006: OpenAlex Academic Search
==================================
Uses the OpenAlex API to search for academic works matching a query.
Extracts titles, authors, publication year, abstract, citation count,
DOI, and open access URL. Results sorted by citation count (most cited first).

OpenAlex covers 250M+ works and is completely free with no API key required.

Usage:
    python3 method_openalex.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import sys
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Cache integration (graceful fallback if unavailable)
# ---------------------------------------------------------------------------
USE_CACHE = True

_cached_request = None
try:
    if USE_CACHE:
        from request_cache import cached_request as _cached_request
except ImportError:
    pass


METHOD_ID = "DG-0006"
METHOD_NAME = "OpenAlex Academic Search"
TIMEOUT = 15
PER_PAGE = 15
API_BASE = "https://api.openalex.org/works"


def search_openalex(query, per_page=PER_PAGE):
    """Search OpenAlex API for academic works matching the query."""
    params = {
        "search": query,
        "per_page": str(per_page),
        "sort": "cited_by_count:desc",
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    headers = {
        "User-Agent": "DataGatheringBot/1.0 (research tool; mailto:research@example.com)",
        "Accept": "application/json",
    }
    try:
        if _cached_request is not None:
            raw = _cached_request(url, headers=headers, timeout=TIMEOUT)
            data = json.loads(raw.decode("utf-8"))
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        return parse_openalex_response(data)
    except Exception as e:
        return [{"error": str(e)}]


def parse_openalex_response(data):
    """Parse OpenAlex JSON response into structured results."""
    works = []
    for item in data.get("results", []):
        work = {}

        # Title
        work["title"] = item.get("title", "") or ""

        # Authors
        authors = []
        for authorship in item.get("authorships", []):
            author_info = authorship.get("author", {})
            name = author_info.get("display_name", "")
            if name:
                authors.append(name)
        work["authors"] = authors

        # Publication year
        work["publication_year"] = item.get("publication_year")

        # Abstract (OpenAlex provides inverted index; reconstruct it)
        abstract_index = item.get("abstract_inverted_index")
        if abstract_index:
            work["abstract"] = _reconstruct_abstract(abstract_index)
        else:
            work["abstract"] = ""

        # Citation count
        work["cited_by_count"] = item.get("cited_by_count", 0)

        # DOI
        doi = item.get("doi", "") or ""
        work["doi"] = doi

        # OpenAlex ID
        work["openalex_id"] = item.get("id", "")

        # Open access URL
        oa = item.get("open_access", {})
        work["open_access_url"] = oa.get("oa_url", "") or ""
        work["is_open_access"] = oa.get("is_oa", False)

        # Source / journal
        primary_location = item.get("primary_location", {}) or {}
        source = primary_location.get("source", {}) or {}
        work["journal"] = source.get("display_name", "") or ""

        # Type
        work["type"] = item.get("type", "")

        # Concepts / topics
        concepts = []
        for concept in item.get("concepts", [])[:5]:
            concepts.append({
                "name": concept.get("display_name", ""),
                "score": concept.get("score", 0),
            })
        work["concepts"] = concepts

        # URL for the work
        work["url"] = doi if doi else work["openalex_id"]

        works.append(work)

    return works


def _reconstruct_abstract(inverted_index):
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    # Build position -> word mapping
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    # Sort by position and join
    word_positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in word_positions)


def run(query):
    """Execute the OpenAlex academic search method."""
    start = time.time()

    works = search_openalex(query, per_page=PER_PAGE)

    # Filter out error entries
    errors = [w for w in works if "error" in w]
    valid_works = [w for w in works if "error" not in w]

    duration = round(time.time() - start, 2)

    # Compute stats
    unique_authors = set()
    total_citations = 0
    journals = set()
    for w in valid_works:
        unique_authors.update(w.get("authors", []))
        total_citations += w.get("cited_by_count", 0)
        journal = w.get("journal", "")
        if journal:
            journals.add(journal)

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": valid_works,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(valid_works),
            "data_points": len(valid_works),
            "unique_authors": len(unique_authors),
            "total_citations": total_citations,
            "unique_journals": len(journals),
            "errors": errors if errors else None,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
