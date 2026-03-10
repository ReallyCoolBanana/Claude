#!/usr/bin/env python3
"""
DG-0005: Hacker News Search
=============================
Uses the Algolia-powered Hacker News search API to find stories matching a
query. Also fetches the current top stories from the Firebase HN API.

Usage:
    python3 method_hackernews.py "your search query"

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


METHOD_ID = "DG-0005"
METHOD_NAME = "Hacker News Search"
TIMEOUT = 10
ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
FIREBASE_BASE = "https://hacker-news.firebaseio.com/v0"


# ---------------------------------------------------------------------------
# HTTP helper
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


# ---------------------------------------------------------------------------
# Algolia HN search
# ---------------------------------------------------------------------------

def search_stories(query, hits_per_page=20):
    """Search Hacker News stories via the Algolia API."""
    encoded = urllib.parse.quote(query)
    url = (
        f"{ALGOLIA_BASE}/search"
        f"?query={encoded}"
        f"&tags=story"
        f"&hitsPerPage={hits_per_page}"
    )
    data = _api_get(url)
    if "error" in data:
        return [{"error": data["error"], "source": "algolia_hn", "query": query}]

    results = []
    for hit in data.get("hits", []):
        results.append({
            "title": hit.get("title", ""),
            "url": hit.get("url", ""),
            "author": hit.get("author", ""),
            "points": hit.get("points", 0),
            "num_comments": hit.get("num_comments", 0),
            "created_at": hit.get("created_at", ""),
            "objectID": hit.get("objectID", ""),
            "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
        })

    return results


# ---------------------------------------------------------------------------
# Firebase HN top stories
# ---------------------------------------------------------------------------

def fetch_top_stories(limit=10):
    """Fetch current top stories from the Firebase HN API."""
    url = f"{FIREBASE_BASE}/topstories.json"
    data = _api_get(url)
    if isinstance(data, dict) and "error" in data:
        return [{"error": data["error"], "source": "firebase_hn"}]
    if not isinstance(data, list):
        return [{"error": "Unexpected response format", "source": "firebase_hn"}]

    story_ids = data[:limit]
    stories = []
    for sid in story_ids:
        item_url = f"{FIREBASE_BASE}/item/{sid}.json"
        item = _api_get(item_url)
        if isinstance(item, dict) and "error" not in item:
            stories.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "author": item.get("by", ""),
                "points": item.get("score", 0),
                "num_comments": item.get("descendants", 0),
                "created_at": item.get("time", ""),
                "objectID": str(item.get("id", "")),
                "hn_url": f"https://news.ycombinator.com/item?id={item.get('id', '')}",
            })

    return stories


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the Hacker News search method."""
    start = time.time()

    # Step 1: Search via Algolia
    search_results = search_stories(query, hits_per_page=20)

    # Check if we got real results or only errors
    search_errors = [r for r in search_results if "error" in r]
    valid_search = [r for r in search_results if "error" not in r]

    # Step 2: Fetch top stories
    top_stories = fetch_top_stories(limit=10)
    top_errors = [r for r in top_stories if "error" in r]
    valid_top = [r for r in top_stories if "error" not in r]

    all_errors = search_errors + top_errors

    duration = round(time.time() - start, 2)

    unique_authors = set()
    total_points = 0
    for r in valid_search + valid_top:
        author = r.get("author", "")
        if author:
            unique_authors.add(author)
        total_points += r.get("points", 0)

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": valid_search,
        "top_stories": valid_top,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(valid_search),
            "top_stories_count": len(valid_top),
            "data_points": len(valid_search) + len(valid_top),
            "unique_authors": len(unique_authors),
            "total_points": total_points,
            "errors": all_errors if all_errors else None,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
