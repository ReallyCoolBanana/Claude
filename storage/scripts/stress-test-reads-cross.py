#!/usr/bin/env python3
"""
Sub-Agent Gamma: Cross-Subsystem Query Benchmarks
Tests compound filters, cross-reference queries, and multi-field lookups.
"""

import json
import os
import time
import random
import statistics
from datetime import datetime

STORAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITERATIONS = 1000


def load_indexes():
    with open(os.path.join(STORAGE_ROOT, "scripts", "index.json")) as f:
        scripts = json.load(f)
    with open(os.path.join(STORAGE_ROOT, "api-tools", "index.json")) as f:
        tools = json.load(f)
    with open(os.path.join(STORAGE_ROOT, "sources", "index.json")) as f:
        sources = json.load(f)
    return scripts, tools, sources


def format_ns(ns_list):
    avg = statistics.mean(ns_list)
    med = statistics.median(ns_list)
    mn = min(ns_list)
    mx = max(ns_list)
    std = statistics.stdev(ns_list) if len(ns_list) > 1 else 0
    return {
        "avg_ns": round(avg),
        "avg_us": round(avg / 1000, 2),
        "median_us": round(med / 1000, 2),
        "min_us": round(mn / 1000, 2),
        "max_us": round(mx / 1000, 2),
        "stddev_us": round(std / 1000, 2),
        "p99_us": round(sorted(ns_list)[int(len(ns_list) * 0.99)] / 1000, 2) if len(ns_list) >= 100 else None,
    }


def benchmark_scripts_using_source(scripts_list, sources_list, n=ITERATIONS):
    """Find all scripts that reference a particular source (via used_by_scripts cross-ref)."""
    times = []
    for _ in range(n):
        # Pick a random source and find scripts that use it
        source = random.choice(sources_list)
        source_id = source["id"]
        start = time.perf_counter_ns()
        # Check if source has used_by_scripts field
        used_by = source.get("used_by_scripts", [])
        # Now resolve script IDs to actual scripts (cross-lookup)
        matching_scripts = [s for s in scripts_list if s["id"] in used_by]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
    return times


def benchmark_scripts_using_source_indexed(scripts_list, sources_list, n=ITERATIONS):
    """Same query but with a pre-built script ID index."""
    script_index = {s["id"]: s for s in scripts_list}
    times = []
    for _ in range(n):
        source = random.choice(sources_list)
        used_by = source.get("used_by_scripts", [])
        start = time.perf_counter_ns()
        matching_scripts = [script_index[sid] for sid in used_by if sid in script_index]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
    return times


def benchmark_tools_by_category_and_team(tools_list, n=ITERATIONS):
    """Find all tools in a category added by a specific team."""
    categories = list(set(t["category"] for t in tools_list))
    teams = list(set(t["created_by"] for t in tools_list))
    times = []
    result_counts = []
    for _ in range(n):
        cat = random.choice(categories)
        team = random.choice(teams)
        start = time.perf_counter_ns()
        results = [t for t in tools_list if t["category"] == cat and t["created_by"] == team]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        result_counts.append(len(results))
    return times, result_counts


def benchmark_compound_filter_scripts(scripts_list, n=ITERATIONS):
    """Compound filter: category + language + date range."""
    categories = list(set(s["category"] for s in scripts_list))
    languages = list(set(s["language"] for s in scripts_list))
    date_ranges = [
        ("2024-01-01", "2024-06-30"),
        ("2024-07-01", "2024-12-31"),
        ("2025-01-01", "2025-06-30"),
        ("2025-07-01", "2025-12-31"),
        ("2026-01-01", "2026-03-10"),
    ]
    times = []
    result_counts = []
    for _ in range(n):
        cat = random.choice(categories)
        lang = random.choice(languages)
        date_start, date_end = random.choice(date_ranges)
        start = time.perf_counter_ns()
        results = [
            s for s in scripts_list
            if s["category"] == cat
            and s["language"] == lang
            and date_start <= s["created"] <= date_end
        ]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        result_counts.append(len(results))
    return times, result_counts


def benchmark_compound_with_prefilter(scripts_list, n=ITERATIONS):
    """Same compound filter but using pre-built indexes to narrow search first."""
    categories = list(set(s["category"] for s in scripts_list))
    languages = list(set(s["language"] for s in scripts_list))
    date_ranges = [
        ("2024-01-01", "2024-06-30"),
        ("2024-07-01", "2024-12-31"),
        ("2025-01-01", "2025-06-30"),
        ("2025-07-01", "2025-12-31"),
        ("2026-01-01", "2026-03-10"),
    ]

    # Pre-build indexes
    cat_index = {}
    for s in scripts_list:
        cat_index.setdefault(s["category"], []).append(s)
    lang_index = {}
    for s in scripts_list:
        lang_index.setdefault(s["language"], []).append(s)
    # Build intersection sets
    cat_lang_index = {}
    for s in scripts_list:
        key = (s["category"], s["language"])
        cat_lang_index.setdefault(key, []).append(s)

    times = []
    result_counts = []
    for _ in range(n):
        cat = random.choice(categories)
        lang = random.choice(languages)
        date_start, date_end = random.choice(date_ranges)
        start = time.perf_counter_ns()
        candidates = cat_lang_index.get((cat, lang), [])
        results = [s for s in candidates if date_start <= s["created"] <= date_end]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        result_counts.append(len(results))
    return times, result_counts


def benchmark_full_cross_join(scripts_list, tools_list, sources_list, n=500):
    """Find scripts + tools + sources all matching a given team (cross-subsystem)."""
    all_teams = list(set(
        [s["created_by"] for s in scripts_list] +
        [t["created_by"] for t in tools_list] +
        [s["created_by"] for s in sources_list]
    ))
    times = []
    for _ in range(n):
        team = random.choice(all_teams)
        start = time.perf_counter_ns()
        team_scripts = [s for s in scripts_list if s["created_by"] == team]
        team_tools = [t for t in tools_list if t["created_by"] == team]
        team_sources = [s for s in sources_list if s["created_by"] == team]
        combined = {
            "scripts": team_scripts,
            "tools": team_tools,
            "sources": team_sources,
            "total": len(team_scripts) + len(team_tools) + len(team_sources),
        }
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
    return times


def benchmark_tag_search(scripts_list, tools_list, sources_list, n=ITERATIONS):
    """Search for entries with a specific tag across all subsystems."""
    all_tags = set()
    for s in scripts_list:
        all_tags.update(s.get("tags", []))
    for t in tools_list:
        all_tags.update(t.get("tags", []))
    for s in sources_list:
        all_tags.update(s.get("tags", []))
    tag_list = list(all_tags)

    times = []
    for _ in range(n):
        tag = random.choice(tag_list)
        start = time.perf_counter_ns()
        matching = (
            [s for s in scripts_list if tag in s.get("tags", [])] +
            [t for t in tools_list if tag in t.get("tags", [])] +
            [s for s in sources_list if tag in s.get("tags", [])]
        )
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
    return times


def main():
    random.seed(42)
    scripts, tools, sources = load_indexes()
    scripts_list = scripts["scripts"]
    tools_list = tools["tools"]
    sources_list = sources["sources"]

    print("=" * 70)
    print("SUB-AGENT GAMMA: Cross-Subsystem Query Benchmarks")
    print(f"Iterations per test: {ITERATIONS}")
    print("=" * 70)

    results = {}

    # Test 1: Scripts using a particular source
    print("\n--- Test 1: Find scripts that use a particular source ---")
    t_linear = benchmark_scripts_using_source(scripts_list, sources_list)
    t_indexed = benchmark_scripts_using_source_indexed(scripts_list, sources_list)
    results["cross_ref_linear"] = format_ns(t_linear)
    results["cross_ref_indexed"] = format_ns(t_indexed)
    print(f"  Linear cross-ref:  avg={results['cross_ref_linear']['avg_us']}us")
    print(f"  Indexed cross-ref: avg={results['cross_ref_indexed']['avg_us']}us")
    speedup = results['cross_ref_linear']['avg_us'] / max(results['cross_ref_indexed']['avg_us'], 0.001)
    print(f"  Speedup: {speedup:.1f}x")

    # Test 2: Tools by category + team
    print("\n--- Test 2: Tools by category AND team (2-field filter) ---")
    t_compound2, counts2 = benchmark_tools_by_category_and_team(tools_list)
    results["tools_cat_team"] = format_ns(t_compound2)
    results["tools_cat_team_avg_results"] = round(statistics.mean(counts2), 1)
    print(f"  avg={results['tools_cat_team']['avg_us']}us  median={results['tools_cat_team']['median_us']}us")
    print(f"  Avg result set size: {results['tools_cat_team_avg_results']}")

    # Test 3: Compound filter (category + language + date range)
    print("\n--- Test 3: Scripts compound filter (category + language + date range) ---")
    t_compound3, counts3 = benchmark_compound_filter_scripts(scripts_list)
    t_prefilter, counts3p = benchmark_compound_with_prefilter(scripts_list)
    results["compound_3field_linear"] = format_ns(t_compound3)
    results["compound_3field_prefilter"] = format_ns(t_prefilter)
    results["compound_3field_avg_results"] = round(statistics.mean(counts3), 1)
    print(f"  Linear scan:    avg={results['compound_3field_linear']['avg_us']}us")
    print(f"  Pre-filtered:   avg={results['compound_3field_prefilter']['avg_us']}us")
    speedup3 = results['compound_3field_linear']['avg_us'] / max(results['compound_3field_prefilter']['avg_us'], 0.001)
    print(f"  Speedup: {speedup3:.1f}x")
    print(f"  Avg result set size: {results['compound_3field_avg_results']}")

    # Test 4: Full cross-subsystem team query
    print("\n--- Test 4: Cross-subsystem query (all entries by team) ---")
    t_cross = benchmark_full_cross_join(scripts_list, tools_list, sources_list)
    results["cross_subsystem_team"] = format_ns(t_cross)
    print(f"  avg={results['cross_subsystem_team']['avg_us']}us  (scans 120 total entries across 3 files)")

    # Test 5: Tag search across all subsystems
    print("\n--- Test 5: Tag search across all subsystems ---")
    t_tags = benchmark_tag_search(scripts_list, tools_list, sources_list)
    results["tag_search_all"] = format_ns(t_tags)
    print(f"  avg={results['tag_search_all']['avg_us']}us  (scans all 120 entries, checks tag arrays)")

    # Recommendations
    print(f"\n{'='*70}")
    print("RECOMMENDATIONS")
    print(f"{'='*70}")
    print("""
1. CROSS-REFERENCES: The used_by_scripts field on sources creates an O(n)
   lookup when resolving script IDs. Pre-building a hash index eliminates this.

2. COMPOUND FILTERS: With 50 entries, linear scan is fast enough (<30us).
   At scale (5000+), compound indexes (category+language) provide major gains.

3. CROSS-SUBSYSTEM QUERIES: Scanning 3 separate files is inherently 3x the
   cost of a single file. Consider a unified index or materialized views for
   common cross-system queries.

4. TAG SEARCH: Tag arrays require O(n*m) scanning (n entries, m tags per entry).
   An inverted tag index would reduce this to O(1) per tag lookup.

5. DATE RANGE QUERIES: String comparison works but sorted indexes would enable
   binary search for date ranges at scale.
""")

    output_path = os.path.join(STORAGE_ROOT, "scripts", "stress-test-reads-cross-results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Raw results saved to {output_path}")


if __name__ == "__main__":
    main()
