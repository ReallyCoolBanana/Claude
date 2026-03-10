#!/usr/bin/env python3
"""
DG-0004: Hybrid Multi-Source Aggregation (v2)
==============================================
Combines all available methods (web search, Wikipedia, arXiv, OpenAlex,
Wikidata) into a single pipeline with parallel execution.

Iteration 2 improvements:
- Added OpenAlex and Wikidata as additional sources
- Parallel execution using concurrent.futures.ThreadPoolExecutor
- Cross-source corroboration scoring (findings confirmed by multiple sources score higher)
- Source credibility tiers (academic > structured_knowledge > encyclopedia > web)
- TF-IDF deduplication via dedup_utils

Usage:
    python3 method_hybrid.py "your search query"

Returns JSON to stdout with standardized format.
"""

import importlib.util
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse


METHOD_ID = "DG-0004"
METHOD_NAME = "Hybrid Multi-Source Aggregation v2"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Source credibility tiers: higher = more credible
CREDIBILITY_TIERS = {
    "arxiv": {"tier": "academic", "weight": 1.5},
    "openalex": {"tier": "academic", "weight": 1.5},
    "wikipedia": {"tier": "encyclopedia", "weight": 1.2},
    "wikidata": {"tier": "structured_knowledge", "weight": 1.1},
    "web_search": {"tier": "web", "weight": 1.0},
}


def _load_method(filename):
    """Dynamically load a sibling method module."""
    path = os.path.join(SCRIPT_DIR, filename)
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_dedup():
    """Load deduplication utilities."""
    try:
        mod = _load_method("dedup_utils.py")
        return mod
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shared stop words — imported from dedup_utils if available, else fallback
# ---------------------------------------------------------------------------
def _get_stop_words():
    """Get stop words set, preferring dedup_utils.STOP_WORDS."""
    dedup = _load_dedup()
    if dedup and hasattr(dedup, "STOP_WORDS"):
        return dedup.STOP_WORDS
    # Fallback inline set
    return {
        "this", "that", "with", "from", "have", "been", "were", "also",
        "which", "their", "about", "would", "there", "these", "other",
        "more", "some", "than", "into", "only", "over", "such", "after",
        "most", "when", "what", "they", "each", "does", "will", "many",
    }

STOP_WORDS = _get_stop_words()


def normalize_url(url):
    """Normalize a URL for comparison.

    Strips scheme, 'www.' prefix, trailing slashes, query params, and fragments.
    """
    url = url.strip()
    if not url:
        return ""

    # Parse the URL properly to strip query and fragment
    try:
        parsed = urlparse(url)
        # Reconstruct without query and fragment
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        clean = url

    clean = clean.strip().rstrip("/")

    # Strip scheme and www prefix — check 'https://www.' BEFORE 'https://'
    for prefix in ("https://www.", "http://www.", "https://", "http://"):
        if clean.lower().startswith(prefix):
            clean = clean[len(prefix):]
            break

    return clean.lower().rstrip("/")


def extract_keywords(text, min_len=4):
    """Extract significant words from text for cross-referencing."""
    words = re.findall(r'\b[a-zA-Z]{%d,}\b' % min_len, text.lower())
    return set(w for w in words if w not in STOP_WORDS)


def _run_method(method_name, mod, query):
    """Run a method and return (method_name, result_data) tuple."""
    try:
        data = mod.run(query)
        return (method_name, data)
    except Exception as e:
        return (method_name, {
            "results": [],
            "metadata": {"duration_seconds": 0, "data_points": 0, "error": str(e)}
        })


def _normalize_result(result, source_type):
    """Normalize a result from any source into a common format."""
    entry = {
        "title": "",
        "url": "",
        "snippet": "",
        "source_type": source_type,
        "credibility_tier": CREDIBILITY_TIERS.get(source_type, {}).get("tier", "web"),
        "credibility_weight": CREDIBILITY_TIERS.get(source_type, {}).get("weight", 1.0),
        "corroboration_score": 1,
        "corroborating_sources": [source_type],
    }

    if source_type == "web_search":
        entry["title"] = result.get("title", "")
        entry["url"] = result.get("url", "")
        entry["snippet"] = result.get("snippet", "")

    elif source_type == "wikipedia":
        entry["title"] = result.get("title", "")
        entry["url"] = result.get("url", "")
        entry["snippet"] = (result.get("extract", "") or "")[:300]
        entry["categories"] = result.get("categories", [])

    elif source_type == "arxiv":
        entry["title"] = result.get("title", "")
        entry["url"] = result.get("arxiv_url", "")
        entry["snippet"] = (result.get("abstract", "") or "")[:300]
        entry["authors"] = result.get("authors", [])
        entry["published"] = result.get("published", "")
        entry["categories"] = result.get("categories", [])

    elif source_type == "openalex":
        entry["title"] = result.get("title", "")
        entry["url"] = result.get("url", "") or result.get("doi", "")
        entry["snippet"] = (result.get("abstract", "") or "")[:300]
        entry["authors"] = result.get("authors", [])
        entry["cited_by_count"] = result.get("cited_by_count", 0)
        entry["publication_year"] = result.get("publication_year")
        entry["journal"] = result.get("journal", "")

    elif source_type == "wikidata":
        entry["title"] = result.get("title", "") or result.get("label", "")
        entry["url"] = result.get("url", "") or result.get("entity_uri", "")
        entry["snippet"] = result.get("content", "") or result.get("description", "") or ""
        entry["aliases"] = result.get("metadata", {}).get("aliases", []) if isinstance(result.get("metadata"), dict) else []

    return entry


def cross_reference_results(all_source_results):
    """
    Cross-reference findings across all sources and score corroboration.

    all_source_results: dict mapping source_type -> list of normalized entries
    """
    unified = []

    # Flatten all entries first
    for source_type, entries in all_source_results.items():
        unified.extend(entries)

    if not unified:
        return unified

    # Precompute keywords for ALL results once (instead of per-pair)
    keywords_cache = []
    for entry in unified:
        kw = extract_keywords(entry["title"] + " " + entry["snippet"])
        keywords_cache.append(kw)

    # Cross-reference using keyword overlap
    for i, entry in enumerate(unified):
        keywords_i = keywords_cache[i]
        if not keywords_i:
            continue

        for j, other in enumerate(unified):
            if i == j:
                continue
            if other["source_type"] == entry["source_type"]:
                continue
            # Skip if already counted this source type
            other_key = other["source_type"] + "_related"
            if other_key in entry["corroborating_sources"]:
                continue

            keywords_j = keywords_cache[j]
            overlap = keywords_i & keywords_j
            # Require >=4 keyword overlap (was 3) to reduce false corroboration
            if len(overlap) >= 4:
                entry["corroboration_score"] += 0.5
                entry["corroborating_sources"].append(other_key)

        # URL-based corroboration
        entry_url = normalize_url(entry.get("url", ""))
        if entry_url:
            for j, other in enumerate(unified):
                if i == j or other["source_type"] == entry["source_type"]:
                    continue
                other_url = normalize_url(other.get("url", ""))
                if entry_url == other_url:
                    src = other["source_type"]
                    if src not in entry["corroborating_sources"]:
                        entry["corroboration_score"] += 1
                        entry["corroborating_sources"].append(src)

    # Apply credibility weighting and quality penalties/bonuses
    for entry in unified:
        weight = entry.get("credibility_weight", 1.0)

        # Penalize results that are just URL+title with no real content
        snippet = (entry.get("snippet") or "").strip()
        if len(snippet) < 20:
            weight *= 0.6  # 40% penalty for content-less results

        # Bonus for results corroborated by 2+ distinct sources
        distinct_sources = set()
        for src in entry.get("corroborating_sources", []):
            # Normalize "foo_related" -> "foo"
            base = src.replace("_related", "")
            distinct_sources.add(base)
        if len(distinct_sources) >= 3:
            # Corroborated by 2+ other sources (original + 2)
            weight *= 1.2

        entry["weighted_score"] = round(entry["corroboration_score"] * weight, 2)

    # Sort by weighted score descending, then corroboration score
    unified.sort(key=lambda x: (x.get("weighted_score", 0), x["corroboration_score"]), reverse=True)
    return unified


def run_with_results(query, precomputed_results):
    """Execute hybrid aggregation using pre-computed standalone method results.

    This avoids re-executing standalone methods when called from the benchmark
    runner, eliminating double-execution overhead.

    Parameters
    ----------
    query : str
        The search query.
    precomputed_results : dict
        Mapping of source_name -> raw result data from standalone methods.
        Keys should be: web_search, wikipedia, arxiv, openalex, wikidata.
    """
    start = time.time()

    sub_method_stats = {}
    raw_results = {}

    for name, data in precomputed_results.items():
        if data is not None:
            raw_results[name] = data
            sub_method_stats[name] = {
                "results": data.get("metadata", {}).get("data_points", 0),
                "duration": data.get("metadata", {}).get("duration_seconds", 0),
                "error": data.get("metadata", {}).get("error"),
                "source": "precomputed",
            }

    return _aggregate(query, raw_results, sub_method_stats, start)


def run(query):
    """Execute the hybrid multi-source aggregation method with parallel execution."""
    start = time.time()

    # Load all method modules
    methods = {}
    method_files = {
        "web_search": "method_websearch.py",
        "wikipedia": "method_wikipedia.py",
        "arxiv": "method_arxiv.py",
        "openalex": "method_openalex.py",
        "wikidata": "method_wikidata.py",
    }

    for name, filename in method_files.items():
        try:
            methods[name] = _load_method(filename)
        except Exception as e:
            methods[name] = None

    # Execute all methods in parallel
    sub_method_stats = {}
    raw_results = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for name, mod in methods.items():
            if mod is not None:
                future = executor.submit(_run_method, name, mod, query)
                futures[future] = name

        for future in as_completed(futures):
            name, data = future.result()
            raw_results[name] = data
            sub_method_stats[name] = {
                "results": data.get("metadata", {}).get("data_points", 0),
                "duration": data.get("metadata", {}).get("duration_seconds", 0),
                "error": data.get("metadata", {}).get("error"),
            }

    return _aggregate(query, raw_results, sub_method_stats, start)


def _aggregate(query, raw_results, sub_method_stats, start):
    """Shared aggregation logic for run() and run_with_results()."""
    # Normalize results from each source
    all_source_results = {}
    for source_type, data in raw_results.items():
        entries = []
        for r in data.get("results", []):
            if isinstance(r, dict) and "error" not in r:
                entries.append(_normalize_result(r, source_type))
        all_source_results[source_type] = entries

    # Cross-reference across sources
    unified = cross_reference_results(all_source_results)

    # Apply TF-IDF deduplication
    dedup_mod = _load_dedup()
    if dedup_mod and unified:
        unified = dedup_mod.deduplicate_results(unified, threshold=0.65)

    duration = round(time.time() - start, 2)

    # Collect stats
    all_urls = set()
    for r in unified:
        url = r.get("url", "")
        if url:
            all_urls.add(url)

    corroborated = sum(1 for r in unified if r["corroboration_score"] > 1)
    avg_score = round(
        sum(r["corroboration_score"] for r in unified) / max(len(unified), 1), 2
    )
    avg_weighted = round(
        sum(r.get("weighted_score", 0) for r in unified) / max(len(unified), 1), 2
    )

    # Count by credibility tier
    tier_counts = {}
    for r in unified:
        tier = r.get("credibility_tier", "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": unified,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(all_urls),
            "data_points": len(unified),
            "sub_method_stats": sub_method_stats,
            "corroborated_findings": corroborated,
            "avg_corroboration_score": avg_score,
            "avg_weighted_score": avg_weighted,
            "credibility_tier_distribution": tier_counts,
            "deduplication_applied": dedup_mod is not None,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
