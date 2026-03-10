#!/usr/bin/env python3
"""
DG-0008: Wikidata Knowledge Graph Search
==========================================
Uses the Wikidata API to search for entities, then fetches detailed
properties including labels, descriptions, claims, and sitelinks.

Usage:
    python3 method_wikidata.py "your search query"

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


METHOD_ID = "DG-0008"
METHOD_NAME = "Wikidata Knowledge Graph Search"
TIMEOUT = 10
API_BASE = "https://www.wikidata.org/w/api.php"

HEADERS = {
    "User-Agent": "DataGatheringBot/1.0 (research tool)",
}

# Well-known property IDs for extraction
P_INSTANCE_OF = "P31"
P_SUBCLASS_OF = "P279"


# ---------------------------------------------------------------------------
# Wikidata API methods
# ---------------------------------------------------------------------------

def wikidata_api(params):
    """Make a Wikidata API call and return JSON response."""
    params["format"] = "json"
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    try:
        if _cached_request is not None:
            raw = _cached_request(url, headers=HEADERS, timeout=TIMEOUT)
            return json.loads(raw.decode("utf-8"))
        else:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def search_entities(query, limit=10):
    """Search Wikidata for entities matching query."""
    data = wikidata_api({
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "limit": str(limit),
    })
    if "error" in data:
        return []
    return data.get("search", [])


def get_entity_details(entity_id):
    """Fetch detailed info for a Wikidata entity."""
    data = wikidata_api({
        "action": "wbgetentities",
        "ids": entity_id,
        "props": "labels|descriptions|claims|sitelinks",
    })
    if "error" in data:
        return {"id": entity_id, "error": data["error"]}

    entities = data.get("entities", {})
    entity = entities.get(entity_id)
    if not entity:
        return {"id": entity_id, "error": "Entity not found"}

    # Label (English)
    labels = entity.get("labels", {})
    label = labels.get("en", {}).get("value", "")

    # Description (English)
    descriptions = entity.get("descriptions", {})
    description = descriptions.get("en", {}).get("value", "")

    # Aliases (English)
    aliases_raw = entity.get("aliases", {}).get("en", [])
    aliases = [a.get("value", "") for a in aliases_raw]

    # Claims: extract instance_of and subclass_of
    claims = entity.get("claims", {})
    instance_of = _extract_claim_labels(claims, P_INSTANCE_OF)
    subclass_of = _extract_claim_labels(claims, P_SUBCLASS_OF)

    # Wikipedia sitelink
    sitelinks = entity.get("sitelinks", {})
    enwiki = sitelinks.get("enwiki", {})
    wikipedia_title = enwiki.get("title", "")
    wikipedia_url = ""
    if wikipedia_title:
        wikipedia_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(wikipedia_title.replace(' ', '_'))}"

    return {
        "id": entity_id,
        "label": label,
        "description": description,
        "aliases": aliases,
        "instance_of": instance_of,
        "subclass_of": subclass_of,
        "wikipedia_title": wikipedia_title,
        "wikipedia_url": wikipedia_url,
        "wikidata_url": f"https://www.wikidata.org/wiki/{entity_id}",
    }


def _extract_claim_labels(claims, property_id):
    """Extract human-readable values from a claim property.

    Returns Q-IDs and labels where available.
    """
    results = []
    claim_list = claims.get(property_id, [])
    for claim in claim_list:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", {})
        if isinstance(value, dict) and "id" in value:
            results.append(value["id"])
    return results


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the Wikidata knowledge graph search method."""
    start = time.time()
    results = []
    errors = []

    # Step 1: Search for matching entities
    search_hits = search_entities(query, limit=10)

    if not search_hits:
        errors.append({"error": "No entities found", "query": query})

    # Step 2: Fetch details for each entity
    for hit in search_hits:
        entity_id = hit.get("id", "")
        if not entity_id:
            continue
        details = get_entity_details(entity_id)
        if "error" in details:
            errors.append(details)
            continue
        # Include the search-level description as a fallback
        if not details.get("description") and hit.get("description"):
            details["description"] = hit["description"]
        results.append(details)

    duration = round(time.time() - start, 2)

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": results,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(results),
            "data_points": len(results),
            "entities_searched": len(search_hits),
            "errors": errors if errors else None,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
