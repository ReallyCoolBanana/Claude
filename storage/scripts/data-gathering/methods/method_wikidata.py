#!/usr/bin/env python3
"""DG-0007: Wikidata Knowledge Extraction.

Queries Wikidata's search API to find structured knowledge
about a given topic. Extracts entity labels, descriptions, aliases,
and related properties.

Usage:
    python3 method_wikidata.py "query string"
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

METHOD_ID = "DG-0007"
METHOD_NAME = "Wikidata SPARQL"
ENDPOINT = "https://www.wikidata.org/w/api.php"
TIMEOUT = 15


def search_wikidata(query, limit=15):
    params = urllib.parse.urlencode({
        "action": "wbsearchentities", "search": query,
        "language": "en", "limit": str(limit), "format": "json",
    })
    try:
        req = urllib.request.Request(
            f"{ENDPOINT}?{params}",
            headers={"User-Agent": "DataGatheringBot/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode()).get("search", [])
    except urllib.error.HTTPError as e:
        return [{"error": f"HTTP {e.code}: {e.reason}", "source": "wikidata"}]
    except urllib.error.URLError as e:
        return [{"error": f"URL error: {e.reason}", "source": "wikidata"}]
    except json.JSONDecodeError as e:
        return [{"error": f"JSON decode error: {e}", "source": "wikidata"}]


def get_entity_details(entity_id):
    params = urllib.parse.urlencode({
        "action": "wbgetentities", "ids": entity_id,
        "languages": "en", "props": "labels|descriptions|aliases|sitelinks",
        "format": "json",
    })
    try:
        req = urllib.request.Request(
            f"{ENDPOINT}?{params}",
            headers={"User-Agent": "DataGatheringBot/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return data.get("entities", {}).get(entity_id, {})
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "source": "wikidata", "entity_id": entity_id}
    except urllib.error.URLError as e:
        return {"error": f"URL error: {e.reason}", "source": "wikidata", "entity_id": entity_id}
    except json.JSONDecodeError as e:
        return {"error": f"JSON decode error: {e}", "source": "wikidata", "entity_id": entity_id}


def extract_info(entity):
    labels = entity.get("labels", {})
    descriptions = entity.get("descriptions", {})
    aliases = entity.get("aliases", {})
    sitelinks = entity.get("sitelinks", {})
    label = labels.get("en", {}).get("value", "Unknown")
    description = descriptions.get("en", {}).get("value", "")
    alias_list = [a["value"] for a in aliases.get("en", [])]
    wp_url = ""
    if "enwiki" in sitelinks:
        title = sitelinks["enwiki"].get("title", "")
        wp_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title)}"
    return {
        "label": label, "description": description, "aliases": alias_list,
        "wikipedia_url": wp_url, "entity_id": entity.get("id", ""),
        "wikidata_url": f"https://www.wikidata.org/wiki/{entity.get('id', '')}",
    }


def gather(query):
    start = time.time()
    results, sources = [], set()
    search_results = search_wikidata(query)
    words = query.split()
    if len(words) > 2:
        for i in range(0, len(words) - 1, 2):
            search_results.extend(search_wikidata(" ".join(words[i:i+3]), 5))
    seen = set()
    unique = []
    for sr in search_results:
        eid = sr.get("id", "")
        if eid not in seen:
            seen.add(eid)
            unique.append(sr)
    for sr in unique[:15]:
        eid = sr.get("id", "")
        if not eid:
            continue
        details = get_entity_details(eid)
        info = extract_info(details) if details else {
            "label": sr.get("label", ""), "description": sr.get("description", ""),
            "aliases": [], "wikipedia_url": "", "entity_id": eid,
            "wikidata_url": f"https://www.wikidata.org/wiki/{eid}",
        }
        results.append({
            "title": info["label"], "content": info["description"],
            "url": info["wikidata_url"], "source": "wikidata",
            "metadata": {"entity_id": info["entity_id"],
                         "aliases": info["aliases"],
                         "wikipedia_url": info["wikipedia_url"]},
        })
        sources.add(info["wikidata_url"])
        if info["wikipedia_url"]:
            sources.add(info["wikipedia_url"])
    return {
        "method_id": METHOD_ID, "method_name": METHOD_NAME, "query": query,
        "results": results,
        "metadata": {"duration_seconds": round(time.time() - start, 2),
                     "sources_count": len(sources), "data_points": len(results)},
    }


run = gather


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 method_wikidata.py \"query\"", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(gather(sys.argv[1]), indent=2))
