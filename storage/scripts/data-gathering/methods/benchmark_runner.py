#!/usr/bin/env python3
"""
Benchmark Runner for Data Gathering Methods
=============================================
Runs all data gathering methods against a test query and collects
performance metrics. Saves results to the benchmarks directory.

Supports parallel execution (default) for faster benchmarking and
sequential mode for comparison.

Usage:
    python3 benchmark_runner.py "test query" [output_file]
    python3 benchmark_runner.py "test query" --sequential [output_file]

If output_file is not specified, results are printed to stdout.
"""

import argparse
import glob
import importlib.util
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARKS_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "benchmarks")


# All methods to benchmark — standalone methods first, hybrid last
STANDALONE_METHODS = [
    {"id": "DG-0001", "name": "WebSearch", "file": "method_websearch.py"},
    {"id": "DG-0002", "name": "Wikipedia", "file": "method_wikipedia.py"},
    {"id": "DG-0003", "name": "arXiv", "file": "method_arxiv.py"},
    {"id": "DG-0005", "name": "Iterative", "file": "method_iterative.py"},
    {"id": "DG-0006", "name": "OpenAlex", "file": "method_openalex.py"},
    {"id": "DG-0007", "name": "Wikidata", "file": "method_wikidata.py"},
]

HYBRID_METHOD = {"id": "DG-0004", "name": "Hybrid v2", "file": "method_hybrid.py"}

# Combined list for backward compatibility
METHODS = STANDALONE_METHODS + [HYBRID_METHOD]


def _load_method(filename):
    """Dynamically load a method module."""
    path = os.path.join(SCRIPT_DIR, filename)
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _detect_iteration():
    """Auto-detect iteration number from existing benchmark files.

    Scans the benchmarks directory for files matching the expected naming
    pattern and returns one greater than the highest iteration found.
    Falls back to 1 if no previous benchmarks exist.
    """
    if not os.path.isdir(BENCHMARKS_DIR):
        return 1

    max_iteration = 0
    for fname in os.listdir(BENCHMARKS_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(BENCHMARKS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            it = data.get("iteration", 0)
            if isinstance(it, int) and it > max_iteration:
                max_iteration = it
            # Also check individual benchmark entries
            for entry in data.get("benchmarks", []):
                eit = entry.get("iteration", 0)
                if isinstance(eit, int) and eit > max_iteration:
                    max_iteration = eit
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    return max_iteration + 1 if max_iteration > 0 else 1


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


def _run_single_method(method_info, query, iteration, timestamp):
    """Run a single method and return its benchmark entry.

    Returns a tuple of (method_info, benchmark_entry, raw_result).
    The raw_result is returned so the hybrid method can reuse standalone
    results instead of re-executing them.
    """
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
            "iteration": iteration,
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

        print(f"    Done: {data_points} pts, {duration}s, quality={quality}",
              file=sys.stderr, flush=True)

        return (method_info, benchmark_entry, result)

    except Exception as e:
        benchmark_entry = {
            "method_id": method_id,
            "method_name": method_name,
            "iteration": iteration,
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
        }
        print(f"    FAILED: {e}", file=sys.stderr, flush=True)
        return (method_info, benchmark_entry, None)


def _run_hybrid_with_precomputed(query, standalone_results, iteration, timestamp):
    """Run the hybrid method, passing pre-computed standalone results to avoid re-execution.

    Parameters
    ----------
    query : str
        The search query.
    standalone_results : dict
        Mapping of source name -> raw result data from standalone methods.
    iteration : int
        Current benchmark iteration number.
    timestamp : str
        ISO timestamp for this benchmark run.
    """
    method_info = HYBRID_METHOD
    method_id = method_info["id"]
    method_name = method_info["name"]

    print(f"  Running {method_name} ({method_id}) [using cached standalone results]...",
          file=sys.stderr, flush=True)

    try:
        mod = _load_method(method_info["file"])
        start = time.time()

        # If the hybrid module has a run_with_results function, use it
        # to avoid re-running standalone methods. Otherwise fall back to run().
        if hasattr(mod, "run_with_results"):
            result = mod.run_with_results(query, standalone_results)
        else:
            result = mod.run(query)

        duration = round(time.time() - start, 2)

        metadata = result.get("metadata", {})
        data_points = metadata.get("data_points", len(result.get("results", [])))
        sources_count = metadata.get("sources_count", 0)
        quality = compute_quality_score(result)

        benchmark_entry = {
            "method_id": method_id,
            "method_name": method_name,
            "iteration": iteration,
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

        if "corroborated_findings" in metadata:
            benchmark_entry["notes"] += f"Corroborated: {metadata['corroborated_findings']}. "
        if "avg_corroboration_score" in metadata:
            benchmark_entry["notes"] += f"Avg corroboration: {metadata['avg_corroboration_score']}. "
        if metadata.get("deduplication_applied"):
            benchmark_entry["notes"] += "Deduplication applied. "
        if metadata.get("error"):
            benchmark_entry["notes"] += f"Error: {metadata['error']}. "

        print(f"    Done: {data_points} pts, {duration}s, quality={quality}",
              file=sys.stderr, flush=True)

        return (method_info, benchmark_entry, result)

    except Exception as e:
        benchmark_entry = {
            "method_id": method_id,
            "method_name": method_name,
            "iteration": iteration,
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
        }
        print(f"    FAILED: {e}", file=sys.stderr, flush=True)
        return (method_info, benchmark_entry, None)


def run_benchmark(query, methods_to_run=None, parallel=True):
    """Run benchmarks for specified methods (or all if None).

    Parameters
    ----------
    query : str
        The search query to benchmark.
    methods_to_run : list, optional
        List of method dicts to run. If None, runs all methods.
    parallel : bool
        If True (default), run standalone methods concurrently using
        ThreadPoolExecutor, then pass results into hybrid. If False,
        run all methods sequentially.
    """
    if methods_to_run is None:
        methods_to_run = METHODS

    iteration = _detect_iteration()
    timestamp = datetime.now(timezone.utc).isoformat()
    overall_start = time.time()

    results = []

    # Separate standalone from hybrid
    standalone = [m for m in methods_to_run if m["id"] != HYBRID_METHOD["id"]]
    include_hybrid = any(m["id"] == HYBRID_METHOD["id"] for m in methods_to_run)

    if parallel and len(standalone) > 1:
        # --- Parallel execution of standalone methods ---
        print(f"  [Parallel mode: {len(standalone)} standalone methods]", file=sys.stderr, flush=True)
        standalone_raw = {}

        with ThreadPoolExecutor(max_workers=len(standalone)) as executor:
            futures = {}
            for method_info in standalone:
                future = executor.submit(
                    _run_single_method, method_info, query, iteration, timestamp
                )
                futures[future] = method_info

            for future in as_completed(futures):
                method_info, benchmark_entry, raw_result = future.result()
                results.append(benchmark_entry)
                if raw_result is not None:
                    # Map method file to source name for hybrid reuse
                    name_map = {
                        "method_websearch.py": "web_search",
                        "method_wikipedia.py": "wikipedia",
                        "method_arxiv.py": "arxiv",
                        "method_openalex.py": "openalex",
                        "method_wikidata.py": "wikidata",
                    }
                    source_name = name_map.get(method_info["file"])
                    if source_name:
                        standalone_raw[source_name] = raw_result

        # Now run hybrid with pre-computed results (eliminates double-execution)
        if include_hybrid:
            _, hybrid_entry, _ = _run_hybrid_with_precomputed(
                query, standalone_raw, iteration, timestamp
            )
            results.append(hybrid_entry)
    else:
        # --- Sequential execution ---
        print(f"  [Sequential mode: {len(methods_to_run)} methods]", file=sys.stderr, flush=True)
        for method_info in methods_to_run:
            _, benchmark_entry, _ = _run_single_method(
                method_info, query, iteration, timestamp
            )
            results.append(benchmark_entry)

    overall_duration = round(time.time() - overall_start, 2)

    # Sort results by method order in METHODS for consistent output
    method_order = {m["id"]: i for i, m in enumerate(METHODS)}
    results.sort(key=lambda r: method_order.get(r["method_id"], 999))

    return {
        "version": "2.0",
        "iteration": iteration,
        "timestamp": timestamp,
        "test_query": query,
        "execution_mode": "parallel" if (parallel and len(standalone) > 1) else "sequential",
        "overall_duration_seconds": overall_duration,
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
    parser = argparse.ArgumentParser(
        description="Benchmark data gathering methods.",
        usage='python3 benchmark_runner.py "test query" [output_file] [--sequential]',
    )
    parser.add_argument("query", help="The test query to benchmark")
    parser.add_argument("output_file", nargs="?", default=None,
                        help="Optional output file path")
    parser.add_argument("--sequential", action="store_true", default=False,
                        help="Run methods sequentially instead of in parallel")

    args = parser.parse_args()
    use_parallel = not args.sequential

    iteration = _detect_iteration()
    mode_label = "parallel" if use_parallel else "sequential"
    print(f"Running iteration {iteration} benchmarks ({mode_label}) for: \"{args.query}\"",
          file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    benchmark_results = run_benchmark(args.query, parallel=use_parallel)

    output = json.dumps(benchmark_results, indent=2, ensure_ascii=False)

    if args.output_file:
        os.makedirs(os.path.dirname(args.output_file) or ".", exist_ok=True)
        with open(args.output_file, "w") as f:
            f.write(output)
        print(f"\nResults saved to: {args.output_file}", file=sys.stderr)
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
    print("-" * 50, file=sys.stderr)
    print(f"Overall duration: {benchmark_results['overall_duration_seconds']}s ({mode_label})",
          file=sys.stderr)
    print("=" * 60, file=sys.stderr)
