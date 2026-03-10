#!/usr/bin/env python3
"""
DG-0006: Guardian News Search
===============================
Uses The Guardian's Content API to search for news articles matching a query.
Extracts headlines, standfirsts, body text (truncated), section names, and
publication dates. Falls back to DuckDuckGo site-scoped search if the
Guardian API is unavailable.

Usage:
    python3 method_guardian.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

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


METHOD_ID = "DG-0006"
METHOD_NAME = "Guardian News Search"
TIMEOUT = 10
API_BASE = "https://content.guardianapis.com/search"
API_KEY = "test"  # The Guardian's public test API key


# ---------------------------------------------------------------------------
# Guardian API methods
# ---------------------------------------------------------------------------

def _api_get(url):
    """Make an HTTP GET request and return parsed JSON."""
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


def search_articles(query, page_size=20):
    """Search the Guardian Content API for articles matching query."""
    encoded = urllib.parse.quote(query)
    url = (
        f"{API_BASE}"
        f"?q={encoded}"
        f"&show-fields=bodyText,headline,standfirst"
        f"&page-size={page_size}"
        f"&api-key={API_KEY}"
    )
    data = _api_get(url)
    if "error" in data:
        return [{"error": data["error"], "source": "guardian", "query": query}]

    response = data.get("response", {})
    if response.get("status") != "ok":
        return [{"error": f"API status: {response.get('status', 'unknown')}", "source": "guardian", "query": query}]

    results = []
    for item in response.get("results", []):
        fields = item.get("fields", {})

        # Truncate body text to 500 characters
        body_text = fields.get("bodyText", "") or ""
        if len(body_text) > 500:
            body_text = body_text[:500] + "..."

        results.append({
            "webTitle": item.get("webTitle", ""),
            "webUrl": item.get("webUrl", ""),
            "sectionName": item.get("sectionName", ""),
            "webPublicationDate": item.get("webPublicationDate", ""),
            "headline": fields.get("headline", ""),
            "standfirst": fields.get("standfirst", ""),
            "bodyText": body_text,
            "type": item.get("type", ""),
            "pillarName": item.get("pillarName", ""),
        })

    return results


# ---------------------------------------------------------------------------
# Fallback: DuckDuckGo site-scoped Guardian search
# ---------------------------------------------------------------------------

def fallback_guardian_search(query):
    """Use DuckDuckGo to find Guardian articles when the API is blocked."""
    articles = []
    seen_urls = set()

    search_queries = [
        f"site:theguardian.com {query}",
        f"site:theguardian.com {query} news",
    ]
    for sq in search_queries:
        hits = _ddg_search(sq)
        for h in hits:
            url = h.get("url", "")
            if not url or url in seen_urls:
                continue
            if "theguardian.com" not in url:
                continue
            seen_urls.add(url)

            title = h.get("title", "")
            # Clean up title — DDG often appends " | The Guardian"
            title = re.sub(r'\s*[|]\s*The Guardian.*$', '', title).strip()

            articles.append({
                "webTitle": title,
                "webUrl": url,
                "sectionName": "",
                "webPublicationDate": "",
                "headline": title,
                "standfirst": h.get("snippet", ""),
                "bodyText": "",
                "type": "",
                "pillarName": "",
                "source": "duckduckgo_fallback",
            })

    return articles


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the Guardian news search method."""
    start = time.time()
    used_fallback = False

    articles = search_articles(query, page_size=20)

    # Check if we got real results or only errors
    errors = [a for a in articles if "error" in a]
    valid_articles = [a for a in articles if "error" not in a]

    if not valid_articles and errors:
        # API failed — use fallback
        used_fallback = True
        valid_articles = fallback_guardian_search(query)
        errors = []

    duration = round(time.time() - start, 2)

    sections = set()
    for a in valid_articles:
        section = a.get("sectionName", "")
        if section:
            sections.add(section)

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": valid_articles,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(valid_articles),
            "data_points": sum(1 for a in valid_articles if a.get("bodyText")),
            "sections": list(sections),
            "errors": errors if errors else None,
            "used_fallback": used_fallback,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
