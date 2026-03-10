#!/usr/bin/env python3
"""
DG-0009: GitHub Repository Search
===================================
Uses the GitHub API to search for repositories and code matching a query.
Extracts repo metadata including stars, language, topics, and license info.

Usage:
    python3 method_github.py "your search query"

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


METHOD_ID = "DG-0009"
METHOD_NAME = "GitHub Repository Search"
TIMEOUT = 10
API_BASE = "https://api.github.com"

HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "DataGatheringBot/1.0 (research tool)",
}


# ---------------------------------------------------------------------------
# GitHub API methods
# ---------------------------------------------------------------------------

def github_api(url):
    """Make a GitHub API call and return JSON response.

    Handles 403 rate-limit responses gracefully.
    """
    try:
        if _cached_request is not None:
            raw = _cached_request(url, headers=HEADERS, timeout=TIMEOUT)
            return json.loads(raw.decode("utf-8"))
        else:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {"error": f"Rate limited (HTTP 403). GitHub limits unauthenticated requests to 10 search requests/minute.",
                    "rate_limited": True}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"URL error: {e.reason}"}
    except OSError as e:
        return {"error": f"Network error: {e}"}


def search_repositories(query, per_page=20):
    """Search GitHub for repositories matching query, sorted by stars."""
    encoded = urllib.parse.quote(query)
    url = f"{API_BASE}/search/repositories?q={encoded}&sort=stars&per_page={per_page}"
    data = github_api(url)
    if "error" in data:
        return [data]
    return parse_repositories(data)


def parse_repositories(data):
    """Parse GitHub repository search response into structured results."""
    repos = []
    if not isinstance(data, dict):
        return [{"error": "Unexpected response format"}]

    for item in data.get("items", []):
        repo = {}
        repo["full_name"] = item.get("full_name", "")
        repo["description"] = item.get("description", "") or ""
        repo["html_url"] = item.get("html_url", "")
        repo["stargazers_count"] = item.get("stargazers_count", 0)
        repo["language"] = item.get("language", "") or ""
        repo["topics"] = item.get("topics", []) or []
        repo["updated_at"] = item.get("updated_at", "")
        repo["forks_count"] = item.get("forks_count", 0)
        repo["open_issues_count"] = item.get("open_issues_count", 0)

        # License
        license_info = item.get("license")
        repo["license"] = license_info.get("name", "") if isinstance(license_info, dict) else ""

        repos.append(repo)

    return repos


def search_code(query, per_page=10):
    """Search GitHub for code matching query."""
    encoded = urllib.parse.quote(query)
    url = f"{API_BASE}/search/code?q={encoded}&per_page={per_page}"
    data = github_api(url)
    if "error" in data:
        return [data]
    return parse_code_results(data)


def parse_code_results(data):
    """Parse GitHub code search response into structured results."""
    results = []
    if not isinstance(data, dict):
        return [{"error": "Unexpected response format"}]

    for item in data.get("items", []):
        result = {}
        result["name"] = item.get("name", "")
        result["path"] = item.get("path", "")
        result["html_url"] = item.get("html_url", "")

        repo = item.get("repository", {})
        result["repository"] = repo.get("full_name", "")
        result["repository_url"] = repo.get("html_url", "")
        result["repository_description"] = repo.get("description", "") or ""

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the GitHub repository search method."""
    start = time.time()

    # Search repositories
    repos = search_repositories(query, per_page=20)
    repo_errors = [r for r in repos if "error" in r]
    valid_repos = [r for r in repos if "error" not in r]

    # Search code (separate request)
    code_results = search_code(query, per_page=10)
    code_errors = [c for c in code_results if "error" in c]
    valid_code = [c for c in code_results if "error" not in c]

    all_errors = repo_errors + code_errors

    duration = round(time.time() - start, 2)

    # Collect unique languages and topics
    unique_languages = set()
    unique_topics = set()
    for r in valid_repos:
        lang = r.get("language", "")
        if lang:
            unique_languages.add(lang)
        for t in r.get("topics", []):
            unique_topics.add(t)

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": valid_repos,
        "code_results": valid_code,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(valid_repos),
            "data_points": len(valid_repos) + len(valid_code),
            "repositories_found": len(valid_repos),
            "code_matches_found": len(valid_code),
            "unique_languages": list(unique_languages),
            "unique_topics": list(unique_topics),
            "errors": all_errors if all_errors else None,
            "rate_limited": any(e.get("rate_limited") for e in all_errors),
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
