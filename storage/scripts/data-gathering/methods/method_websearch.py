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
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser


METHOD_ID = "DG-0001"
METHOD_NAME = "Multi-query Web Search"
TIMEOUT = 10


class DuckDuckGoParser(HTMLParser):
    """Parse DuckDuckGo HTML search results page."""

    def __init__(self):
        super().__init__()
        self.results = []
        self._current = {}
        self._in_result_title = False
        self._in_snippet = False
        self._capture_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        # Result title link
        if tag == "a" and "result__a" in cls:
            self._in_result_title = True
            self._capture_text = ""
            href = attrs_dict.get("href", "")
            # DuckDuckGo wraps URLs; extract actual URL
            if "uddg=" in href:
                match = re.search(r'uddg=([^&]+)', href)
                if match:
                    href = urllib.parse.unquote(match.group(1))
            self._current["url"] = href
        # Snippet
        if tag == "a" and "result__snippet" in cls:
            self._in_snippet = True
            self._capture_text = ""

    def handle_endtag(self, tag):
        if tag == "a" and self._in_result_title:
            self._in_result_title = False
            self._current["title"] = self._capture_text.strip()
        if tag == "a" and self._in_snippet:
            self._in_snippet = False
            self._current["snippet"] = self._capture_text.strip()
            # End of one result block
            if self._current.get("url") and self._current.get("title"):
                self.results.append(dict(self._current))
            self._current = {}

    def handle_data(self, data):
        if self._in_result_title or self._in_snippet:
            self._capture_text += data


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


def search_duckduckgo(query):
    """Search DuckDuckGo HTML and return parsed results."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = DuckDuckGoParser()
        parser.feed(html)
        return parser.results
    except Exception as e:
        return [{"error": str(e), "query": query}]


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
