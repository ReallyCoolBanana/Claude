#!/usr/bin/env python3
"""
DG-0004: OpenAlex Academic Search
==================================
Uses the OpenAlex API to search for academic works matching a query, extracts
titles, authors, publication years, citation counts, DOIs, open access status,
abstracts, and host venues.

Usage:
    python3 method_openalex.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

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


METHOD_ID = "DG-0004"
METHOD_NAME = "OpenAlex Academic Search"
TIMEOUT = 10
API_BASE = "https://api.openalex.org"
MAILTO = "research@example.com"


# ---------------------------------------------------------------------------
# OpenAlex API methods
# ---------------------------------------------------------------------------

def _api_get(url):
    """Make an OpenAlex API call and return parsed JSON."""
    headers = {"User-Agent": "DataGatheringBot/1.0 (research tool)"}
    try:
        if _cached_request is not None:
            raw = _cached_request(url, headers=headers, timeout=TIMEOUT)
            return json.loads(raw.decode("utf-8"))
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"URL error: {e.reason}"}
    except OSError as e:
        return {"error": f"Network error: {e}"}


def reconstruct_abstract(inverted_index):
    """Convert an abstract_inverted_index to plain text.

    The OpenAlex inverted index maps each word to a list of positions.
    We rebuild the abstract by placing each word at its position(s).
    """
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in word_positions)


def search_works(query, per_page=20):
    """Search OpenAlex for works matching query."""
    encoded = urllib.parse.quote(query)
    url = (
        f"{API_BASE}/works?search={encoded}"
        f"&per_page={per_page}"
        f"&mailto={MAILTO}"
    )
    data = _api_get(url)
    if "error" in data:
        return [{"error": data["error"], "source": "openalex", "query": query}]

    results = []
    for work in data.get("results", []):
        # Extract authors
        authors = []
        for authorship in work.get("authorships", []):
            author = authorship.get("author", {})
            name = author.get("display_name", "")
            if name:
                authors.append(name)

        # Extract host venue
        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}
        host_venue = {
            "name": source.get("display_name", ""),
            "type": source.get("type", ""),
            "issn_l": source.get("issn_l", ""),
            "is_oa": primary_location.get("is_oa", False),
        }

        # Extract open access info
        oa = work.get("open_access", {})
        open_access = {
            "is_oa": oa.get("is_oa", False),
            "oa_status": oa.get("oa_status", ""),
            "oa_url": oa.get("oa_url", ""),
        }

        # Reconstruct abstract
        abstract = reconstruct_abstract(
            work.get("abstract_inverted_index")
        )

        results.append({
            "title": work.get("title", ""),
            "authors": authors,
            "publication_year": work.get("publication_year"),
            "cited_by_count": work.get("cited_by_count", 0),
            "doi": work.get("doi", ""),
            "open_access": open_access,
            "abstract": abstract,
            "host_venue": host_venue,
            "openalex_id": work.get("id", ""),
            "type": work.get("type", ""),
        })

    return results


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the OpenAlex academic search method."""
    start = time.time()

    works = search_works(query, per_page=20)

    # Check if we got real results or only errors
    errors = [w for w in works if "error" in w]
    valid_works = [w for w in works if "error" not in w]

    duration = round(time.time() - start, 2)

    unique_authors = set()
    total_citations = 0
    for w in valid_works:
        unique_authors.update(w.get("authors", []))
        total_citations += w.get("cited_by_count", 0)

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
            "errors": errors if errors else None,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
