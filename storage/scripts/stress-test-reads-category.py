#!/usr/bin/env python3
"""
Sub-Agent Alpha: Retrieval by Category/Type Benchmarks
Tests lookup performance by category, language, auth_type, and access_type.
"""

import json
import os
import time
import random
import statistics

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


def benchmark_category_lookup_linear(data_list, key, categories, n=ITERATIONS):
    """Linear scan: filter list by category field."""
    times = []
    results_count = []
    for _ in range(n):
        cat = random.choice(categories)
        start = time.perf_counter_ns()
        results = [item for item in data_list if item.get(key) == cat]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        results_count.append(len(results))
    return times, results_count


def benchmark_category_lookup_indexed(index_dict, data_list, id_field, categories, n=ITERATIONS):
    """Use pre-built category index for lookup."""
    # Build a quick id->item map for resolving
    id_map = {item["id"]: item for item in data_list}
    times = []
    results_count = []
    for _ in range(n):
        cat = random.choice(categories)
        start = time.perf_counter_ns()
        ids = index_dict.get(cat, [])
        results = [id_map[id_] for id_ in ids if id_ in id_map]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        results_count.append(len(results))
    return times, results_count


def benchmark_nonexistent_category(data_list, key, n=100):
    """What happens when searching for a category that doesn't exist?"""
    fake_categories = ["nonexistent", "fake-category", "does-not-exist", "zzz-missing", "null-cat"]
    times = []
    for _ in range(n):
        cat = random.choice(fake_categories)
        start = time.perf_counter_ns()
        results = [item for item in data_list if item.get(key) == cat]
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        assert len(results) == 0
    return times


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
    }


def main():
    random.seed(42)
    scripts, tools, sources = load_indexes()

    print("=" * 70)
    print("SUB-AGENT ALPHA: Category/Type Retrieval Benchmarks")
    print(f"Iterations per test: {ITERATIONS}")
    print("=" * 70)

    results = {}

    # --- Scripts by category ---
    script_cats = list(set(s["category"] for s in scripts["scripts"]))
    t_linear, cnt = benchmark_category_lookup_linear(scripts["scripts"], "category", script_cats)
    t_indexed, _ = benchmark_category_lookup_indexed(scripts.get("categories", {}), scripts["scripts"], "id", script_cats)
    results["scripts_by_category_linear"] = format_ns(t_linear)
    results["scripts_by_category_indexed"] = format_ns(t_indexed)
    print(f"\n--- Scripts by Category ({len(script_cats)} categories, {len(scripts['scripts'])} entries) ---")
    print(f"  Linear scan:  avg={results['scripts_by_category_linear']['avg_us']}us  median={results['scripts_by_category_linear']['median_us']}us")
    print(f"  Indexed:      avg={results['scripts_by_category_indexed']['avg_us']}us  median={results['scripts_by_category_indexed']['median_us']}us")
    speedup = results['scripts_by_category_linear']['avg_us'] / max(results['scripts_by_category_indexed']['avg_us'], 0.01)
    print(f"  Speedup:      {speedup:.1f}x")

    # --- Scripts by language ---
    script_langs = list(set(s["language"] for s in scripts["scripts"]))
    t_linear, _ = benchmark_category_lookup_linear(scripts["scripts"], "language", script_langs)
    t_indexed, _ = benchmark_category_lookup_indexed(scripts.get("language_index", {}), scripts["scripts"], "id", script_langs)
    results["scripts_by_language_linear"] = format_ns(t_linear)
    results["scripts_by_language_indexed"] = format_ns(t_indexed)
    print(f"\n--- Scripts by Language ({len(script_langs)} languages) ---")
    print(f"  Linear scan:  avg={results['scripts_by_language_linear']['avg_us']}us  median={results['scripts_by_language_linear']['median_us']}us")
    print(f"  Indexed:      avg={results['scripts_by_language_indexed']['avg_us']}us  median={results['scripts_by_language_indexed']['median_us']}us")
    speedup = results['scripts_by_language_linear']['avg_us'] / max(results['scripts_by_language_indexed']['avg_us'], 0.01)
    print(f"  Speedup:      {speedup:.1f}x")

    # --- API Tools by category ---
    tool_cats = list(set(t["category"] for t in tools["tools"]))
    t_linear, _ = benchmark_category_lookup_linear(tools["tools"], "category", tool_cats)
    t_indexed, _ = benchmark_category_lookup_indexed(tools.get("categories", {}), tools["tools"], "id", tool_cats)
    results["tools_by_category_linear"] = format_ns(t_linear)
    results["tools_by_category_indexed"] = format_ns(t_indexed)
    print(f"\n--- API Tools by Category ({len(tool_cats)} categories, {len(tools['tools'])} entries) ---")
    print(f"  Linear scan:  avg={results['tools_by_category_linear']['avg_us']}us  median={results['tools_by_category_linear']['median_us']}us")
    print(f"  Indexed:      avg={results['tools_by_category_indexed']['avg_us']}us  median={results['tools_by_category_indexed']['median_us']}us")
    speedup = results['tools_by_category_linear']['avg_us'] / max(results['tools_by_category_indexed']['avg_us'], 0.01)
    print(f"  Speedup:      {speedup:.1f}x")

    # --- API Tools by auth_type ---
    tool_auths = list(set(t["auth_type"] for t in tools["tools"]))
    t_linear, _ = benchmark_category_lookup_linear(tools["tools"], "auth_type", tool_auths)
    t_indexed, _ = benchmark_category_lookup_indexed(tools.get("auth_types", {}), tools["tools"], "id", tool_auths)
    results["tools_by_auth_type_linear"] = format_ns(t_linear)
    results["tools_by_auth_type_indexed"] = format_ns(t_indexed)
    print(f"\n--- API Tools by Auth Type ({len(tool_auths)} auth types) ---")
    print(f"  Linear scan:  avg={results['tools_by_auth_type_linear']['avg_us']}us  median={results['tools_by_auth_type_linear']['median_us']}us")
    print(f"  Indexed:      avg={results['tools_by_auth_type_indexed']['avg_us']}us  median={results['tools_by_auth_type_indexed']['median_us']}us")
    speedup = results['tools_by_auth_type_linear']['avg_us'] / max(results['tools_by_auth_type_indexed']['avg_us'], 0.01)
    print(f"  Speedup:      {speedup:.1f}x")

    # --- Sources by data_type ---
    src_types = list(set(s["data_type"] for s in sources["sources"]))
    t_linear, _ = benchmark_category_lookup_linear(sources["sources"], "data_type", src_types)
    t_indexed, _ = benchmark_category_lookup_indexed(sources.get("by_data_type", {}), sources["sources"], "id", src_types)
    results["sources_by_data_type_linear"] = format_ns(t_linear)
    results["sources_by_data_type_indexed"] = format_ns(t_indexed)
    print(f"\n--- Sources by Data Type ({len(src_types)} data types, {len(sources['sources'])} entries) ---")
    print(f"  Linear scan:  avg={results['sources_by_data_type_linear']['avg_us']}us  median={results['sources_by_data_type_linear']['median_us']}us")
    print(f"  Indexed:      avg={results['sources_by_data_type_indexed']['avg_us']}us  median={results['sources_by_data_type_indexed']['median_us']}us")
    speedup = results['sources_by_data_type_linear']['avg_us'] / max(results['sources_by_data_type_indexed']['avg_us'], 0.01)
    print(f"  Speedup:      {speedup:.1f}x")

    # --- Sources by access_type ---
    src_access = list(set(s["access_type"] for s in sources["sources"]))
    t_linear, _ = benchmark_category_lookup_linear(sources["sources"], "access_type", src_access)
    t_indexed, _ = benchmark_category_lookup_indexed(sources.get("by_access_type", {}), sources["sources"], "id", src_access)
    results["sources_by_access_type_linear"] = format_ns(t_linear)
    results["sources_by_access_type_indexed"] = format_ns(t_indexed)
    print(f"\n--- Sources by Access Type ({len(src_access)} access types) ---")
    print(f"  Linear scan:  avg={results['sources_by_access_type_linear']['avg_us']}us  median={results['sources_by_access_type_linear']['median_us']}us")
    print(f"  Indexed:      avg={results['sources_by_access_type_indexed']['avg_us']}us  median={results['sources_by_access_type_indexed']['median_us']}us")
    speedup = results['sources_by_access_type_linear']['avg_us'] / max(results['sources_by_access_type_indexed']['avg_us'], 0.01)
    print(f"  Speedup:      {speedup:.1f}x")

    # --- Non-existent category tests ---
    print(f"\n--- Non-Existent Category Lookup (100 iterations) ---")
    t_scripts = benchmark_nonexistent_category(scripts["scripts"], "category")
    t_tools = benchmark_nonexistent_category(tools["tools"], "category")
    t_sources = benchmark_nonexistent_category(sources["sources"], "data_type")
    results["nonexistent_scripts"] = format_ns(t_scripts)
    results["nonexistent_tools"] = format_ns(t_tools)
    results["nonexistent_sources"] = format_ns(t_sources)
    print(f"  Scripts (50 entries):  avg={results['nonexistent_scripts']['avg_us']}us  (still scans all entries)")
    print(f"  Tools (30 entries):    avg={results['nonexistent_tools']['avg_us']}us  (still scans all entries)")
    print(f"  Sources (40 entries):  avg={results['nonexistent_sources']['avg_us']}us  (still scans all entries)")
    print(f"  NOTE: Non-existent lookups always incur FULL linear scan cost.")

    # Save raw results
    output_path = os.path.join(STORAGE_ROOT, "scripts", "stress-test-reads-category-results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nRaw results saved to {output_path}")


if __name__ == "__main__":
    main()
