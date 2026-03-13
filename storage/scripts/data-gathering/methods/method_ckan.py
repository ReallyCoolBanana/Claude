#!/usr/bin/env python3
"""
DG-0010: CKAN/DKAN Open Data Search
====================================
Searches multiple CKAN-powered government open data portals for datasets
matching a query. Supports data.gov (US), data.gov.uk (UK), European Data
Portal, Canada, Australia, and custom CKAN instances.

Uses the CKAN Action API v3 (package_search endpoint). DKAN portals expose
the same API, so both are supported transparently.

No API key required for public read-only endpoints. No external dependencies.

Usage:
    python3 method_ckan.py "climate change emissions"
    python3 method_ckan.py "transportation safety" --portals us,uk,eu

Returns JSON to stdout with standardized format.
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

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

METHOD_ID = "DG-0010"
METHOD_NAME = "CKAN/DKAN Open Data Search"
TIMEOUT = 15
USER_AGENT = "DataGatheringBot/1.0 (open-data-research)"

# ---------------------------------------------------------------------------
# Known CKAN portals
# ---------------------------------------------------------------------------

PORTALS = {
    "us": {
        "name": "US Data.gov",
        "base_url": "https://catalog.data.gov",
        "country": "US",
        "type": "ckan",
    },
    "uk": {
        "name": "UK data.gov.uk",
        "base_url": "https://ckan.publishing.service.gov.uk",
        "country": "UK",
        "type": "ckan",
    },
    "eu": {
        "name": "European Data Portal",
        "base_url": "https://data.europa.eu/api/hub/search",
        "country": "EU",
        "type": "ckan-compatible",
    },
    "canada": {
        "name": "Canada Open Data",
        "base_url": "https://open.canada.ca/data",
        "country": "CA",
        "type": "ckan",
    },
    "australia": {
        "name": "Australia data.gov.au",
        "base_url": "https://data.gov.au",
        "country": "AU",
        "type": "ckan",
    },
    "brazil": {
        "name": "Brazil dados.gov.br",
        "base_url": "https://dados.gov.br",
        "country": "BR",
        "type": "ckan",
    },
    "africa": {
        "name": "openAFRICA",
        "base_url": "https://open.africa",
        "country": "Africa",
        "type": "ckan",
    },
}

# Default portals to search (the most reliable ones).
DEFAULT_PORTALS = ["us", "uk", "canada", "australia"]


# ---------------------------------------------------------------------------
# CKAN API methods
# ---------------------------------------------------------------------------

def _api_get(url):
    """Make an HTTP GET request and return parsed JSON."""
    headers = {"User-Agent": USER_AGENT}
    try:
        if _cached_request is not None:
            raw = _cached_request(url, headers=headers, timeout=TIMEOUT)
            return json.loads(raw.decode("utf-8"))
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "url": url}
    except urllib.error.URLError as e:
        return {"error": f"URL error: {e.reason}", "url": url}
    except OSError as e:
        return {"error": f"Network error: {e}", "url": url}
    except json.JSONDecodeError as e:
        return {"error": f"JSON decode error: {e}", "url": url}


def search_portal(portal_key, query, rows=20):
    """Search a single CKAN portal for datasets matching the query.

    Returns a list of dataset dicts in standardized format.
    """
    if portal_key not in PORTALS:
        return [{"error": f"Unknown portal: {portal_key}"}]

    portal = PORTALS[portal_key]
    base = portal["base_url"].rstrip("/")
    encoded_q = urllib.parse.quote(query)

    url = f"{base}/api/3/action/package_search?q={encoded_q}&rows={rows}"

    data = _api_get(url)

    if "error" in data:
        return [{
            "error": data["error"],
            "portal": portal["name"],
            "portal_key": portal_key,
        }]

    # CKAN wraps results in {"success": true, "result": {...}}
    if not data.get("success", False):
        error_msg = data.get("error", {})
        return [{
            "error": f"CKAN API error: {error_msg}",
            "portal": portal["name"],
        }]

    result = data.get("result", {})
    datasets = result.get("results", [])
    total_count = result.get("count", 0)

    parsed = []
    for ds in datasets:
        # Extract resources (files/APIs available for download).
        resources = []
        for res in ds.get("resources", []):
            resources.append({
                "name": res.get("name", ""),
                "format": res.get("format", "").upper(),
                "url": res.get("url", ""),
                "size": res.get("size"),
                "description": (res.get("description") or "")[:200],
            })

        # Extract tags.
        tags = [t.get("display_name", t.get("name", ""))
                for t in ds.get("tags", [])]

        # Extract organization.
        org = ds.get("organization") or {}
        org_name = org.get("title", org.get("name", ""))

        parsed.append({
            "title": ds.get("title", ""),
            "name": ds.get("name", ""),
            "description": (ds.get("notes") or "")[:500],
            "organization": org_name,
            "tags": tags,
            "formats": list({r["format"] for r in resources if r["format"]}),
            "resources_count": len(resources),
            "resources": resources[:5],  # Limit to top 5 resources.
            "metadata_created": ds.get("metadata_created", ""),
            "metadata_modified": ds.get("metadata_modified", ""),
            "license_title": ds.get("license_title", ""),
            "portal": portal["name"],
            "portal_key": portal_key,
            "portal_url": f"{base}/dataset/{ds.get('name', '')}",
        })

    return parsed, total_count


def search_all_portals(query, portal_keys=None, rows_per_portal=10):
    """Search multiple CKAN portals in parallel.

    Returns aggregated results from all portals.
    """
    portal_keys = portal_keys or DEFAULT_PORTALS
    all_results = []
    errors = []
    portal_stats = {}

    with ThreadPoolExecutor(max_workers=len(portal_keys)) as pool:
        futures = {
            pool.submit(search_portal, pk, query, rows_per_portal): pk
            for pk in portal_keys
        }

        for future in as_completed(futures, timeout=TIMEOUT + 5):
            pk = futures[future]
            try:
                result = future.result()
                if isinstance(result, tuple):
                    datasets, total = result
                    # Filter out error entries.
                    valid = [d for d in datasets if "error" not in d]
                    errs = [d for d in datasets if "error" in d]
                    all_results.extend(valid)
                    errors.extend(errs)
                    portal_stats[pk] = {
                        "name": PORTALS[pk]["name"],
                        "results_returned": len(valid),
                        "total_available": total,
                    }
                elif isinstance(result, list):
                    # All errors.
                    errors.extend(result)
                    portal_stats[pk] = {
                        "name": PORTALS[pk]["name"],
                        "results_returned": 0,
                        "error": result[0].get("error", "Unknown"),
                    }
            except Exception as e:
                errors.append({"error": str(e), "portal_key": pk})
                portal_stats[pk] = {
                    "name": PORTALS.get(pk, {}).get("name", pk),
                    "results_returned": 0,
                    "error": str(e),
                }

    return all_results, errors, portal_stats


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query, portal_keys=None, rows_per_portal=10):
    """Execute the CKAN/DKAN open data search method."""
    start = time.time()

    results, errors, portal_stats = search_all_portals(
        query, portal_keys=portal_keys, rows_per_portal=rows_per_portal,
    )

    duration = round(time.time() - start, 2)

    # Collect unique formats and tags across all results.
    all_formats = set()
    all_tags = set()
    for r in results:
        all_formats.update(r.get("formats", []))
        all_tags.update(r.get("tags", []))

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": results,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(results),
            "data_points": len(results),
            "portals_searched": len(portal_stats),
            "portal_stats": portal_stats,
            "unique_formats": sorted(all_formats),
            "unique_tags_sample": sorted(list(all_tags))[:20],
            "errors": errors if errors else None,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Search CKAN/DKAN open data portals for datasets",
    )
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--portals", type=str, default=None,
        help="Comma-separated portal keys (us,uk,canada,australia,eu,brazil,africa)",
    )
    parser.add_argument(
        "--rows", type=int, default=10,
        help="Max results per portal (default: 10)",
    )
    parser.add_argument(
        "--list-portals", action="store_true",
        help="List available portals and exit",
    )

    args = parser.parse_args()

    if args.list_portals:
        for key, info in sorted(PORTALS.items()):
            print(f"  {key:12s}  {info['name']:30s}  {info['base_url']}")
        sys.exit(0)

    portal_keys = args.portals.split(",") if args.portals else None
    result = run(args.query, portal_keys=portal_keys, rows_per_portal=args.rows)
    print(json.dumps(result, indent=2, ensure_ascii=False))
