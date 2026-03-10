#!/usr/bin/env python3
"""
Benchmark Runner for Data Gathering Methods
============================================
Runs all data gathering methods against a test query, times each method,
counts data points and sources, calculates quality scores, and outputs
a benchmark comparison JSON.

Usage:
    python3 benchmark_runner.py "test query"

Output: Writes benchmark results to
    storage/scripts/data-gathering/benchmarks/<timestamp>_benchmark.json
Also prints summary to stdout.
"""

import importlib.util
import json
import os
import sys
import time
from datetime import datetime


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
METHODS_DIR = os.path.join(SCRIPT_DIR, "methods")
BENCHMARKS_DIR = os.path.join(SCRIPT_DIR, "benchmarks")

METHOD_FILES = [
    "method_websearch.py",
    "method_wikipedia.py",
    "method_arxiv.py",
    "method_hybrid.py",
    "method_iterative.py",
]


def load_method(filename):
    """Dynamically load a method module."""
    path = os.path.join(METHODS_DIR, filename)
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def count_unique_urls(results):
    """Count unique URLs from results list."""
    urls = set()
    for r in results:
        for key in ("url", "arxiv_url"):
            val = r.get(key, "")
            if val:
                urls.add(val)
        # Also count external references from Wikipedia
        for ref in r.get("external_references", []):
            if ref:
                urls.add(ref)
    return len(urls)


def calculate_quality_score(result_data):
    """
    Calculate quality score (0-100) based on:
    - Result count (0-30 points): more results = higher score
    - Source diversity (0-30 points): unique URLs
    - Information density (0-40 points): avg snippet/abstract length
    """
    results = result_data.get("results", [])
    metadata = result_data.get("metadata", {})

    # Result count score (0-30): logarithmic scaling, 20+ results = max
    count = len(results)
    count_score = min(30, (count / 20) * 30)

    # Source diversity (0-30): unique URLs
    unique_urls = count_unique_urls(results)
    diversity_score = min(30, (unique_urls / 15) * 30)

    # Information density (0-40): average text length of snippets/abstracts
    text_lengths = []
    for r in results:
        for key in ("snippet", "abstract", "extract"):
            text = r.get(key, "")
            if text:
                text_lengths.append(len(text))
    avg_text_len = sum(text_lengths) / max(len(text_lengths), 1)
    # 200+ chars average = max score
    density_score = min(40, (avg_text_len / 200) * 40)

    total = round(count_score + diversity_score + density_score, 1)
    return {
        "total": total,
        "result_count_score": round(count_score, 1),
        "source_diversity_score": round(diversity_score, 1),
        "information_density_score": round(density_score, 1),
    }


def run_benchmark(query):
    """Run all methods and produce benchmark comparison."""
    os.makedirs(BENCHMARKS_DIR, exist_ok=True)

    benchmark_results = []
    print(f"Benchmarking query: \"{query}\"\n", file=sys.stderr)

    for filename in METHOD_FILES:
        print(f"  Running {filename}...", file=sys.stderr, end=" ", flush=True)
        mod = load_method(filename)
        if mod is None:
            print("SKIPPED (file not found)", file=sys.stderr)
            benchmark_results.append({
                "method_file": filename,
                "error": "File not found",
            })
            continue

        try:
            start = time.time()
            result = mod.run(query)
            wall_time = round(time.time() - start, 2)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            benchmark_results.append({
                "method_file": filename,
                "method_id": getattr(mod, "METHOD_ID", "unknown"),
                "method_name": getattr(mod, "METHOD_NAME", "unknown"),
                "error": str(e),
            })
            continue

        quality = calculate_quality_score(result)
        unique_urls = count_unique_urls(result.get("results", []))

        entry = {
            "method_file": filename,
            "method_id": result.get("method_id", ""),
            "method_name": result.get("method_name", ""),
            "wall_time_seconds": wall_time,
            "reported_duration_seconds": result.get("metadata", {}).get("duration_seconds", 0),
            "result_count": len(result.get("results", [])),
            "unique_urls": unique_urls,
            "data_points": result.get("metadata", {}).get("data_points", 0),
            "quality_score": quality,
        }
        benchmark_results.append(entry)
        print(f"OK ({wall_time}s, {entry['result_count']} results, score={quality['total']})",
              file=sys.stderr)

    # Sort by quality score
    scored = [b for b in benchmark_results if "error" not in b]
    scored.sort(key=lambda x: x["quality_score"]["total"], reverse=True)
    errors = [b for b in benchmark_results if "error" in b]

    # Build final benchmark report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "benchmark_id": f"BM-{timestamp}",
        "query": query,
        "timestamp": datetime.now().isoformat(),
        "rankings": scored,
        "errors": errors if errors else None,
        "summary": {
            "total_methods_tested": len(METHOD_FILES),
            "successful": len(scored),
            "failed": len(errors),
            "best_method": scored[0]["method_name"] if scored else None,
            "best_score": scored[0]["quality_score"]["total"] if scored else None,
            "fastest_method": min(scored, key=lambda x: x["wall_time_seconds"])["method_name"] if scored else None,
            "fastest_time": min(scored, key=lambda x: x["wall_time_seconds"])["wall_time_seconds"] if scored else None,
            "most_results": max(scored, key=lambda x: x["result_count"])["method_name"] if scored else None,
            "most_results_count": max(scored, key=lambda x: x["result_count"])["result_count"] if scored else None,
        },
    }

    # Write to file
    output_path = os.path.join(BENCHMARKS_DIR, f"{timestamp}_benchmark.json")
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nBenchmark saved to: {output_path}", file=sys.stderr)

    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"test query\"", file=sys.stderr)
        sys.exit(1)
    report = run_benchmark(sys.argv[1])
    print(json.dumps(report, indent=2, ensure_ascii=False))
