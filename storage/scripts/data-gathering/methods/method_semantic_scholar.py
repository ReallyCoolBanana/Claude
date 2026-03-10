#!/usr/bin/env python3
"""
DG-0007: Semantic Scholar Paper Search
=======================================
Uses the Semantic Scholar API to search for academic papers, extracting titles,
authors, abstracts, citation counts, TLDRs, and external IDs.

Usage:
    python3 method_semantic_scholar.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import sys
import time
import urllib.error
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


METHOD_ID = "DG-0007"
METHOD_NAME = "Semantic Scholar Paper Search"
TIMEOUT = 10
API_BASE = "https://api.semanticscholar.org/graph/v1"

SEARCH_FIELDS = (
    "title,authors,abstract,year,citationCount,url,"
    "tldr,externalIds,fieldsOfStudy,openAccessPdf"
)


# ---------------------------------------------------------------------------
# Semantic Scholar API methods
# ---------------------------------------------------------------------------

def search_papers(query, limit=20):
    """Search Semantic Scholar for papers matching query."""
    encoded = urllib.parse.quote(query)
    url = (
        f"{API_BASE}/paper/search"
        f"?query={encoded}&limit={limit}&fields={SEARCH_FIELDS}"
    )
    headers = {"User-Agent": "DataGatheringBot/1.0 (research tool)"}
    try:
        if _cached_request is not None:
            raw = _cached_request(url, headers=headers, timeout=TIMEOUT)
            data = json.loads(raw.decode("utf-8"))
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        return parse_papers(data)
    except urllib.error.HTTPError as e:
        return [{"error": f"HTTP {e.code}: {e.reason}", "source": "semantic_scholar", "query": query}]
    except urllib.error.URLError as e:
        return [{"error": f"URL error: {e.reason}", "source": "semantic_scholar", "query": query}]
    except OSError as e:
        return [{"error": f"Network error: {e}", "source": "semantic_scholar", "query": query}]


def parse_papers(data):
    """Parse Semantic Scholar API response into structured results."""
    papers = []
    if not isinstance(data, dict):
        return [{"error": "Unexpected response format"}]

    for item in data.get("data", []):
        paper = {}
        paper["paperId"] = item.get("paperId", "")
        paper["title"] = item.get("title", "")
        paper["abstract"] = item.get("abstract", "") or ""
        paper["year"] = item.get("year")
        paper["citationCount"] = item.get("citationCount", 0)
        paper["url"] = item.get("url", "")

        # Authors: extract list of name strings
        authors_raw = item.get("authors", []) or []
        paper["authors"] = [a.get("name", "") for a in authors_raw if a.get("name")]

        # TLDR
        tldr = item.get("tldr")
        paper["tldr"] = tldr.get("text", "") if isinstance(tldr, dict) else ""

        # Fields of study
        paper["fieldsOfStudy"] = item.get("fieldsOfStudy", []) or []

        # External IDs (DOI, ArXiv)
        ext_ids = item.get("externalIds", {}) or {}
        paper["doi"] = ext_ids.get("DOI", "")
        paper["arxivId"] = ext_ids.get("ArXiv", "")

        # Open access PDF
        oa_pdf = item.get("openAccessPdf")
        paper["openAccessPdfUrl"] = oa_pdf.get("url", "") if isinstance(oa_pdf, dict) else ""

        papers.append(paper)

    return papers


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the Semantic Scholar paper search method."""
    start = time.time()

    # Respect 1 request/second rate limit
    time.sleep(1)

    papers = search_papers(query, limit=20)

    # Separate errors from valid results
    errors = [p for p in papers if "error" in p]
    valid_papers = [p for p in papers if "error" not in p]

    duration = round(time.time() - start, 2)

    unique_authors = set()
    unique_fields = set()
    for p in valid_papers:
        unique_authors.update(p.get("authors", []))
        for f in p.get("fieldsOfStudy", []):
            unique_fields.add(f)

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": valid_papers,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(valid_papers),
            "data_points": len(valid_papers),
            "unique_authors": len(unique_authors),
            "unique_fields": list(unique_fields),
            "errors": errors if errors else None,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
