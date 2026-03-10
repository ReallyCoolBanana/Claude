#!/usr/bin/env python3
"""
Benchmark Runner for Data Gathering Methods
=============================================
Runs all data gathering methods against a test query and collects
performance metrics. Saves results to the benchmarks directory.

Usage:
    python3 benchmark_runner.py "test query" [output_file]

If output_file is not specified, results are printed to stdout.
"""

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARKS_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "benchmarks")


# All methods to benchmark
METHODS = [
    {"id": "DG-0001", "name": "WebSearch", "file": "method_websearch.py"},
    {"id": "DG-0002", "name": "Wikipedia", "file": "method_wikipedia.py"},
    {"id": "DG-0003", "name": "arXiv", "file": "method_arxiv.py"},
    {"id": "DG-0004", "name": "Hybrid v2", "file": "method_hybrid.py"},
    {"id": "DG-0005", "name": "Iterative", "file": "method_iterative.py"},
    {"id": "DG-0006", "name": "OpenAlex", "file": "method_openalex.py"},
    {"id": "DG-0007", "name": "Wikidata", "file": "method_wikidata.py"},
]


def _load_method(filename):
    """Dynamically load a method module."""
    path = os.path.join(SCRIPT_DIR, filename)
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compute_quality_score(result):
    """
    Compute a quality score (0-100) based on multiple factors:
    - Number of data points (up to 30 points)
    - Number of unique sources (up to 20 points)
    - Presence of abstracts/snippets (up to 20 points)
    - Citation/corroboration info (up to 15 points)
    - Source diversity (up to 15 points)
    """
    score = 0
    results_list = result.get("results", [])
    metadata = result.get("metadata", {})

    # Data points: up to 30 points (1 per data point, max 30)
    data_points = metadata.get("data_points", len(results_list))
    score += min(data_points, 30)

    # Unique sources: up to 20 points (2 per source, max 20)
    sources_count = metadata.get("sources_count", 0)
    score += min(sources_count * 2, 20)

    # Content richness: up to 20 points
    rich_count = 0
    for r in results_list:
        text = r.get("snippet", "") or r.get("abstract", "") or r.get("extract", "") or r.get("content", "")
        if len(str(text)) > 50:
            rich_count += 1
    score += min(rich_count * 2, 20)

    # Citation/corroboration: up to 15 points
    corroborated = metadata.get("corroborated_findings", 0)
    total_citations = metadata.get("total_citations", 0)
    score += min(corroborated * 3, 10)
    if total_citations > 0:
        score += min(5, total_citations // 100)

    # Source diversity: up to 15 points
    sub_stats = metadata.get("sub_method_stats", {})
    if sub_stats:
        active_sources = sum(1 for s in sub_stats.values()
                            if isinstance(s, dict) and s.get("results", 0) > 0)
        score += min(active_sources * 3, 15)
    else:
        # Single method gets base points for having results
        if data_points > 0:
            score += 5

    return min(score, 100)


def run_benchmark(query, methods_to_run=None):
    """Run benchmarks for specified methods (or all if None)."""
    if methods_to_run is None:
        methods_to_run = METHODS

    results = []
    timestamp = datetime.now(timezone.utc).isoformat()

    for method_info in methods_to_run:
        method_id = method_info["id"]
        method_name = method_info["name"]
        filename = method_info["file"]

        print(f"  Running {method_name} ({method_id})...", file=sys.stderr, flush=True)

        try:
            mod = _load_method(filename)
            start = time.time()
            result = mod.run(query)
            duration = round(time.time() - start, 2)

            metadata = result.get("metadata", {})
            data_points = metadata.get("data_points", len(result.get("results", [])))
            sources_count = metadata.get("sources_count", 0)
            quality = compute_quality_score(result)

            benchmark_entry = {
                "method_id": method_id,
                "method_name": method_name,
                "iteration": 2,
                "timestamp": timestamp,
                "metrics": {
                    "speed_seconds": duration,
                    "data_points_collected": data_points,
                    "source_quality_score": quality,
                    "unique_sources": sources_count,
                },
                "test_query": query,
                "raw_results_summary": f"{data_points} results in {duration}s from {sources_count} sources",
                "notes": "",
            }

            # Add method-specific notes
            if "corroborated_findings" in metadata:
                benchmark_entry["notes"] += f"Corroborated: {metadata['corroborated_findings']}. "
            if "avg_corroboration_score" in metadata:
                benchmark_entry["notes"] += f"Avg corroboration: {metadata['avg_corroboration_score']}. "
            if metadata.get("deduplication_applied"):
                benchmark_entry["notes"] += "Deduplication applied. "
            if "total_citations" in metadata:
                benchmark_entry["notes"] += f"Total citations: {metadata['total_citations']}. "
            if metadata.get("error"):
                benchmark_entry["notes"] += f"Error: {metadata['error']}. "

            results.append(benchmark_entry)
            print(f"    Done: {data_points} pts, {duration}s, quality={quality}",
                  file=sys.stderr, flush=True)

        except Exception as e:
            results.append({
                "method_id": method_id,
                "method_name": method_name,
                "iteration": 2,
                "timestamp": timestamp,
                "metrics": {
                    "speed_seconds": 0,
                    "data_points_collected": 0,
                    "source_quality_score": 0,
                    "unique_sources": 0,
                },
                "test_query": query,
                "raw_results_summary": f"FAILED: {e}",
                "notes": f"Exception: {e}",
            })
            print(f"    FAILED: {e}", file=sys.stderr, flush=True)

    return {
        "version": "2.0",
        "iteration": 2,
        "timestamp": timestamp,
        "test_query": query,
        "benchmarks": results,
        "summary": _compute_summary(results),
    }


def _compute_summary(results):
    """Compute summary statistics across all benchmarks."""
    if not results:
        return {}

    speeds = [r["metrics"]["speed_seconds"] for r in results if r["metrics"]["speed_seconds"] > 0]
    points = [r["metrics"]["data_points_collected"] for r in results]
    qualities = [r["metrics"]["source_quality_score"] for r in results]

    return {
        "methods_tested": len(results),
        "methods_succeeded": sum(1 for r in results if r["metrics"]["data_points_collected"] > 0),
        "avg_speed_seconds": round(sum(speeds) / max(len(speeds), 1), 2),
        "total_data_points": sum(points),
        "avg_quality_score": round(sum(qualities) / max(len(qualities), 1), 1),
        "best_quality": max(qualities) if qualities else 0,
        "fastest_method": min(results, key=lambda r: r["metrics"]["speed_seconds"] if r["metrics"]["speed_seconds"] > 0 else 999)["method_name"] if speeds else "N/A",
        "highest_quality_method": max(results, key=lambda r: r["metrics"]["source_quality_score"])["method_name"] if results else "N/A",
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"test query\" [output_file]", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Running iteration 2 benchmarks for: \"{query}\"", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    benchmark_results = run_benchmark(query)

    output = json.dumps(benchmark_results, indent=2, ensure_ascii=False)

    if output_file:
        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        with open(output_file, "w") as f:
            f.write(output)
        print(f"\nResults saved to: {output_file}", file=sys.stderr)
    else:
        print(output)

    # Print comparison table
    print("\n" + "=" * 60, file=sys.stderr)
    print(f"{'Method':<20} {'Time':>8} {'Points':>8} {'Quality':>8}", file=sys.stderr)
    print("-" * 50, file=sys.stderr)
    for r in benchmark_results["benchmarks"]:
        m = r["metrics"]
        print(f"{r['method_name']:<20} {m['speed_seconds']:>7.1f}s {m['data_points_collected']:>7} {m['source_quality_score']:>7}",
              file=sys.stderr)
    print("=" * 60, file=sys.stderr)
