#!/usr/bin/env python3
"""
Sub-Agent Beta: Retrieval by ID Benchmarks
Tests linear scan vs hash index lookup, worst-case scenarios, non-existent IDs.
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


def linear_scan_by_id(data_list, target_id):
    for item in data_list:
        if item["id"] == target_id:
            return item
    return None


def benchmark_linear_scan(data_list, all_ids, n=ITERATIONS):
    times = []
    for _ in range(n):
        target = random.choice(all_ids)
        start = time.perf_counter_ns()
        result = linear_scan_by_id(data_list, target)
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        assert result is not None
    return times


def benchmark_hash_index_build(data_list):
    start = time.perf_counter_ns()
    index = {item["id"]: item for item in data_list}
    elapsed = time.perf_counter_ns() - start
    return index, elapsed


def benchmark_hash_lookup(index, all_ids, n=ITERATIONS):
    times = []
    for _ in range(n):
        target = random.choice(all_ids)
        start = time.perf_counter_ns()
        result = index.get(target)
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        assert result is not None
    return times


def benchmark_worst_case_linear(data_list):
    """Lookup the last entry in the list (worst case for linear scan)."""
    last_id = data_list[-1]["id"]
    times = []
    for _ in range(ITERATIONS):
        start = time.perf_counter_ns()
        result = linear_scan_by_id(data_list, last_id)
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
    return times


def benchmark_nonexistent_id_linear(data_list, n=ITERATIONS):
    """Lookup IDs that don't exist - always scans entire list."""
    fake_ids = [f"nonexistent-{i:05d}" for i in range(100)]
    times = []
    for _ in range(n):
        target = random.choice(fake_ids)
        start = time.perf_counter_ns()
        result = linear_scan_by_id(data_list, target)
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        assert result is None
    return times


def benchmark_nonexistent_id_hash(index, n=ITERATIONS):
    fake_ids = [f"nonexistent-{i:05d}" for i in range(100)]
    times = []
    for _ in range(n):
        target = random.choice(fake_ids)
        start = time.perf_counter_ns()
        result = index.get(target)
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
        assert result is None
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
        "p99_us": round(sorted(ns_list)[int(len(ns_list) * 0.99)] / 1000, 2) if len(ns_list) >= 100 else None,
    }


def run_subsystem_benchmarks(name, data_list):
    all_ids = [item["id"] for item in data_list]
    entry_count = len(data_list)

    print(f"\n{'='*60}")
    print(f"  {name} ({entry_count} entries)")
    print(f"{'='*60}")

    results = {}

    # Linear scan - random lookups
    t_linear = benchmark_linear_scan(data_list, all_ids)
    results["linear_random"] = format_ns(t_linear)
    print(f"\n  Random ID Lookup (Linear Scan):")
    print(f"    avg={results['linear_random']['avg_us']}us  median={results['linear_random']['median_us']}us  p99={results['linear_random']['p99_us']}us")

    # Hash index build
    hash_idx, build_time = benchmark_hash_index_build(data_list)
    results["hash_build_ns"] = build_time
    results["hash_build_us"] = round(build_time / 1000, 2)
    print(f"\n  Hash Index Build Time: {results['hash_build_us']}us")

    # Hash lookup - random
    t_hash = benchmark_hash_lookup(hash_idx, all_ids)
    results["hash_random"] = format_ns(t_hash)
    print(f"\n  Random ID Lookup (Hash Index):")
    print(f"    avg={results['hash_random']['avg_us']}us  median={results['hash_random']['median_us']}us  p99={results['hash_random']['p99_us']}us")

    speedup = results['linear_random']['avg_us'] / max(results['hash_random']['avg_us'], 0.001)
    results["speedup"] = round(speedup, 1)
    print(f"\n  Speedup (hash vs linear): {speedup:.1f}x")

    # Break-even analysis
    if results['hash_random']['avg_us'] > 0:
        avg_savings_per_lookup_ns = results['linear_random']['avg_ns'] - results['hash_random']['avg_ns']
        if avg_savings_per_lookup_ns > 0:
            breakeven = build_time / avg_savings_per_lookup_ns
            results["breakeven_lookups"] = round(breakeven, 1)
            print(f"  Break-even: hash index pays off after {breakeven:.0f} lookups")

    # Worst case: last entry
    t_worst = benchmark_worst_case_linear(data_list)
    results["linear_worst_case"] = format_ns(t_worst)
    print(f"\n  Worst Case (Last Entry, Linear):")
    print(f"    avg={results['linear_worst_case']['avg_us']}us  (scans all {entry_count} entries)")

    # Non-existent IDs
    t_miss_linear = benchmark_nonexistent_id_linear(data_list)
    t_miss_hash = benchmark_nonexistent_id_hash(hash_idx)
    results["miss_linear"] = format_ns(t_miss_linear)
    results["miss_hash"] = format_ns(t_miss_hash)
    print(f"\n  Non-Existent ID Lookup:")
    print(f"    Linear: avg={results['miss_linear']['avg_us']}us  (full scan every time)")
    print(f"    Hash:   avg={results['miss_hash']['avg_us']}us  (O(1) miss)")
    miss_speedup = results['miss_linear']['avg_us'] / max(results['miss_hash']['avg_us'], 0.001)
    results["miss_speedup"] = round(miss_speedup, 1)
    print(f"    Miss speedup: {miss_speedup:.1f}x")

    return results


def main():
    random.seed(42)
    scripts, tools, sources = load_indexes()

    print("=" * 60)
    print("SUB-AGENT BETA: ID Retrieval Benchmarks")
    print(f"Iterations per test: {ITERATIONS}")
    print("=" * 60)

    all_results = {}
    all_results["scripts"] = run_subsystem_benchmarks("Scripts", scripts["scripts"])
    all_results["tools"] = run_subsystem_benchmarks("API Tools", tools["tools"])
    all_results["sources"] = run_subsystem_benchmarks("Sources", sources["sources"])

    # Summary
    print(f"\n{'='*60}")
    print("  SUMMARY: Linear Scan vs Hash Index")
    print(f"{'='*60}")
    print(f"{'Subsystem':<15} {'Linear avg':<15} {'Hash avg':<15} {'Speedup':<10} {'Break-even':<12}")
    print("-" * 67)
    for name, r in all_results.items():
        be = r.get('breakeven_lookups', 'N/A')
        be_str = f"{be}" if isinstance(be, (int, float)) else be
        print(f"{name:<15} {r['linear_random']['avg_us']:>8}us    {r['hash_random']['avg_us']:>8}us    {r['speedup']:>6}x    {be_str:>8}")

    output_path = os.path.join(STORAGE_ROOT, "scripts", "stress-test-reads-id-results.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nRaw results saved to {output_path}")


if __name__ == "__main__":
    main()
