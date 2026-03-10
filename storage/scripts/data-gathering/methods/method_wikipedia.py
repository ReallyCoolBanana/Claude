#!/usr/bin/env python3
"""
DG-0002: Wikipedia API Deep Extraction
=======================================
Uses the Wikipedia API to search for relevant articles, extract summaries,
sections, references, and categories. Falls back to DuckDuckGo site-scoped
search if the Wikipedia API is unavailable.

Usage:
    python3 method_wikipedia.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request

from ddg_utils import search_duckduckgo as _ddg_search

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


METHOD_ID = "DG-0002"
METHOD_NAME = "Wikipedia API Deep Extraction"
TIMEOUT = 10
API_BASE = "https://en.wikipedia.org/w/api.php"


# ---------------------------------------------------------------------------
# Wikipedia API methods
# ---------------------------------------------------------------------------

def wiki_api(params):
    """Make a Wikipedia API call and return JSON response."""
    params["format"] = "json"
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": "DataGatheringBot/1.0 (research tool)"}
    try:
        if _cached_request is not None:
            raw = _cached_request(url, headers=headers, timeout=TIMEOUT)
            return json.loads(raw.decode("utf-8"))
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def search_articles(query, limit=5):
    """Search Wikipedia for articles matching query."""
    data = wiki_api({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "srprop": "snippet|titlesnippet|size|wordcount",
    })
    if "error" in data:
        return []
    return data.get("query", {}).get("search", [])


def get_article_details(title):
    """Get detailed info for a Wikipedia article."""
    data = wiki_api({
        "action": "query",
        "titles": title,
        "prop": "extracts|categories|links|extlinks",
        "exintro": "1",
        "explaintext": "1",
        "exsectionformat": "plain",
        "cllimit": "20",
        "pllimit": "20",
        "ellimit": "10",
    })
    if "error" in data:
        return {"title": title, "error": data["error"]}

    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return {"title": title, "error": "No page data"}

    page = list(pages.values())[0]
    if "missing" in page:
        return {"title": title, "error": "Page not found"}

    categories = [c.get("title", "").replace("Category:", "")
                  for c in page.get("categories", [])]
    internal_links = [l.get("title", "") for l in page.get("links", [])]
    external_refs = [e.get("*", "") for e in page.get("extlinks", [])]

    return {
        "title": page.get("title", title),
        "page_id": page.get("pageid"),
        "extract": page.get("extract", ""),
        "categories": categories,
        "internal_links": internal_links[:10],
        "external_references": external_refs,
        "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(page.get('title', title).replace(' ', '_'))}",
    }


def get_sections(title):
    """Get section headings for an article."""
    data = wiki_api({
        "action": "parse",
        "page": title,
        "prop": "sections",
    })
    if "error" in data:
        return []
    return [
        {"index": s.get("index"), "heading": s.get("line"), "level": s.get("level")}
        for s in data.get("parse", {}).get("sections", [])
    ]


def get_see_also(title):
    """Try to extract 'See also' links from article sections."""
    sections = get_sections(title)
    see_also_index = None
    for s in sections:
        if s.get("heading", "").lower() == "see also":
            see_also_index = s.get("index")
            break
    if not see_also_index:
        return []
    data = wiki_api({
        "action": "parse",
        "page": title,
        "prop": "links",
        "section": str(see_also_index),
    })
    if "error" in data:
        return []
    links = data.get("parse", {}).get("links", [])
    return [l.get("*", "") for l in links if l.get("exists") is not None or l.get("ns", -1) == 0]


# ---------------------------------------------------------------------------
# Fallback: DuckDuckGo site-scoped Wikipedia search
# ---------------------------------------------------------------------------

def _extract_wiki_title_from_url(url):
    """Extract article title from a Wikipedia URL."""
    match = re.search(r'en\.wikipedia\.org/wiki/(.+?)(?:\?|#|$)', url)
    if match:
        return urllib.parse.unquote(match.group(1)).replace("_", " ")
    return None


def fallback_wikipedia_search(query):
    """Use DuckDuckGo to find Wikipedia articles when the API is blocked."""
    results = []
    seen = set()

    # Search variants scoped to Wikipedia
    search_queries = [
        f"site:en.wikipedia.org {query}",
        f"site:en.wikipedia.org {query} overview",
    ]
    for sq in search_queries:
        hits = _ddg_search(sq)
        for h in hits:
            url = h.get("url", "")
            title = _extract_wiki_title_from_url(url)
            if not title or title in seen:
                # Also accept non-wiki URLs that DDG returns for the query
                if url and url not in seen and "wikipedia.org" in url:
                    seen.add(url)
                continue
            seen.add(title)
            results.append({
                "title": title,
                "url": url,
                "extract": h.get("snippet", ""),
                "categories": [],
                "internal_links": [],
                "external_references": [],
                "sections": [],
                "search_snippet": h.get("snippet", ""),
                "word_count": 0,
                "source": "duckduckgo_fallback",
            })

    # Also search without site: to find Wikipedia mentions in general results
    general_hits = _ddg_search(f"{query} wikipedia")
    for h in general_hits:
        url = h.get("url", "")
        if "wikipedia.org" not in url:
            continue
        title = _extract_wiki_title_from_url(url) or h.get("title", "")
        if title in seen:
            continue
        seen.add(title)
        results.append({
            "title": title,
            "url": url,
            "extract": h.get("snippet", ""),
            "categories": [],
            "internal_links": [],
            "external_references": [],
            "sections": [],
            "search_snippet": h.get("snippet", ""),
            "word_count": 0,
            "found_via": "general_search",
            "source": "duckduckgo_fallback",
        })

    return results


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the Wikipedia deep extraction method."""
    start = time.time()
    results = []
    seen_titles = set()
    used_fallback = False

    # Step 1: Try Wikipedia API
    search_hits = search_articles(query, limit=5)

    if search_hits:
        # API is working — use the full pipeline
        for hit in search_hits:
            title = hit.get("title", "")
            if title in seen_titles:
                continue
            seen_titles.add(title)
            details = get_article_details(title)
            details["search_snippet"] = hit.get("snippet", "")
            details["word_count"] = hit.get("wordcount", 0)
            details["sections"] = get_sections(title)
            results.append(details)

        # Follow "See also" links for breadth
        see_also_titles = []
        for r in results[:2]:
            sa = get_see_also(r.get("title", ""))
            see_also_titles.extend(sa)
        for title in see_also_titles[:5]:
            if title in seen_titles:
                continue
            seen_titles.add(title)
            details = get_article_details(title)
            details["found_via"] = "see_also"
            results.append(details)
    else:
        # API unavailable — fallback to DuckDuckGo site-scoped search
        used_fallback = True
        results = fallback_wikipedia_search(query)

    duration = round(time.time() - start, 2)
    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": results,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(results),
            "data_points": sum(1 for r in results if r.get("extract")),
            "articles_searched": len(search_hits) if not used_fallback else 0,
            "see_also_followed": len([r for r in results if r.get("found_via") == "see_also"]),
            "used_fallback": used_fallback,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
