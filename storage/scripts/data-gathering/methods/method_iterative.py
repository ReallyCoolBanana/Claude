#!/usr/bin/env python3
"""
DG-0005: Iterative Deepening Search
====================================
Starts with a broad search, analyzes results, then generates focused
follow-up queries. Does 3 rounds of progressively deeper searching.
Each round uses insights from the previous round to refine queries.

Usage:
    python3 method_iterative.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser


METHOD_ID = "DG-0005"
METHOD_NAME = "Iterative Deepening Search"
TIMEOUT = 10
MAX_ROUNDS = 3


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
        if tag == "a" and "result__a" in cls:
            self._in_result_title = True
            self._capture_text = ""
            href = attrs_dict.get("href", "")
            if "uddg=" in href:
                match = re.search(r'uddg=([^&]+)', href)
                if match:
                    href = urllib.parse.unquote(match.group(1))
            self._current["url"] = href
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
            if self._current.get("url") and self._current.get("title"):
                self.results.append(dict(self._current))
            self._current = {}

    def handle_data(self, data):
        if self._in_result_title or self._in_snippet:
            self._capture_text += data


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
        return [{"error": str(e)}]


def extract_key_terms(results, min_len=4):
    """Extract frequently occurring significant terms from results."""
    stop_words = {
        "this", "that", "with", "from", "have", "been", "were", "also",
        "which", "their", "about", "would", "there", "these", "other",
        "more", "some", "than", "into", "only", "over", "such", "after",
        "most", "when", "what", "they", "each", "does", "will", "many",
        "your", "just", "like", "very", "here", "then", "them", "made",
        "used", "using", "based", "make", "find", "best", "know", "need",
        "could", "should", "first", "between", "being", "through", "where",
        "before", "those", "same", "back", "well", "even", "because",
    }
    word_counter = Counter()
    for r in results:
        if "error" in r:
            continue
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        words = re.findall(r'\b[a-zA-Z]{%d,}\b' % min_len, text.lower())
        word_counter.update(w for w in words if w not in stop_words)
    return word_counter


def generate_deeper_queries(original_query, results, round_num):
    """Generate focused follow-up queries based on analysis of previous results."""
    key_terms = extract_key_terms(results)
    # Get the top terms not already in the query
    query_words = set(original_query.lower().split())
    novel_terms = [
        term for term, count in key_terms.most_common(20)
        if term not in query_words and count >= 2
    ]

    queries = []
    if round_num == 1:
        # Round 1 -> 2: Focus on top emergent themes
        if len(novel_terms) >= 2:
            queries.append(f"{original_query} {novel_terms[0]} {novel_terms[1]}")
        if len(novel_terms) >= 4:
            queries.append(f"{novel_terms[2]} {novel_terms[3]} {original_query}")
        # Add a "how" or "techniques" query
        queries.append(f"{original_query} techniques applications")
    elif round_num == 2:
        # Round 2 -> 3: Drill into specifics
        if len(novel_terms) >= 1:
            queries.append(f"{original_query} {novel_terms[0]} detailed analysis")
        if len(novel_terms) >= 3:
            queries.append(f"{novel_terms[0]} {novel_terms[1]} {novel_terms[2]} research")
        queries.append(f"{original_query} recent advances challenges")

    return queries[:3]  # Max 3 queries per round


def run(query):
    """Execute the iterative deepening search method."""
    start = time.time()
    all_results = []
    seen_urls = set()
    rounds_data = []

    current_queries = [query]
    all_accumulated_results = []

    for round_num in range(MAX_ROUNDS):
        round_start = time.time()
        round_results = []

        for q in current_queries:
            results = search_duckduckgo(q)
            for r in results:
                if "error" in r:
                    continue
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    r["depth_level"] = round_num + 1
                    r["search_query"] = q
                    round_results.append(r)
                    all_results.append(r)

        all_accumulated_results.extend(round_results)

        round_duration = round(time.time() - round_start, 2)
        rounds_data.append({
            "round": round_num + 1,
            "queries_used": current_queries,
            "new_results_found": len(round_results),
            "duration_seconds": round_duration,
        })

        # Generate queries for next round based on accumulated results
        if round_num < MAX_ROUNDS - 1:
            current_queries = generate_deeper_queries(
                query, all_accumulated_results, round_num + 1
            )
            if not current_queries:
                break  # No more queries to generate

    # Build synthesis: top terms across all rounds
    all_terms = extract_key_terms(all_results)
    top_themes = [{"term": t, "frequency": c} for t, c in all_terms.most_common(15)]

    duration = round(time.time() - start, 2)
    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": all_results,
        "synthesis": {
            "top_themes": top_themes,
            "total_rounds": len(rounds_data),
            "rounds": rounds_data,
        },
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(seen_urls),
            "data_points": len(all_results),
            "depth_distribution": {
                f"level_{i+1}": sum(1 for r in all_results if r.get("depth_level") == i + 1)
                for i in range(MAX_ROUNDS)
            },
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
