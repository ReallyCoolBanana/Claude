#!/usr/bin/env python3
"""
DG-0001: Multi-query Web Search with Result Synthesis
=====================================================
Takes an input query, generates 3-5 variant search queries (rephrasings,
related terms), searches DuckDuckGo HTML for each variant, parses results,
deduplicates by URL, and returns structured results.

Usage:
    python3 method_websearch.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import sys
import time

from ddg_utils import search_duckduckgo


METHOD_ID = "DG-0001"
METHOD_NAME = "Multi-query Web Search"


def generate_query_variants(query):
    """Generate 3-5 variant search queries from the original."""
    words = query.split()
    variants = [query]

    # Variant 2: Add context word
    variants.append(f"{query} overview research")

    # Variant 3: Reorder / rephrase
    if len(words) >= 2:
        variants.append(f"{' '.join(words[1:])} {words[0]}")
    else:
        variants.append(f"what is {query}")

    # Variant 4: Add specificity
    variants.append(f"{query} techniques methods")

    # Variant 5: Broader context
    variants.append(f"{query} recent developments")

    return variants[:5]


def run(query):
    """Execute the multi-query web search method."""
    start = time.time()
    variants = generate_query_variants(query)
    all_results = []
    seen_urls = set()

    for variant in variants:
        results = search_duckduckgo(variant)
        for r in results:
            if "error" in r:
                continue
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                r["source_query"] = variant
                all_results.append(r)

    duration = round(time.time() - start, 2)
    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": all_results,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(seen_urls),
            "data_points": len(all_results),
            "query_variants_used": variants,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
