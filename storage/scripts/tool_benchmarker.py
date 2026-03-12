#!/usr/bin/env python3
"""tool_benchmarker.py - Generic benchmark harness for repository scripts.

Measures execution time, output size (chars/tokens), memory usage, and
exit codes for any CLI tool. Supports benchmark suites defined in JSON,
comparative benchmarks (old vs new approach), regression detection, and
machine-readable output.

Usage:
    # Single command benchmark
    python tool_benchmarker.py run "python3 smart_read.py KB-0008 --fields title,tags"

    # Run a benchmark suite from JSON definition
    python tool_benchmarker.py suite benchmarks.json

    # Compare two commands (A/B benchmark)
    python tool_benchmarker.py compare \\
        --old "cat knowledge-base/entries/KB-0008.md" \\
        --new "python3 smart_read.py KB-0008 --fields title,tags"

    # Run all built-in benchmarks for a specific tool
    python tool_benchmarker.py auto smart_read

    # Regression check against a saved baseline
    python tool_benchmarker.py check baseline.json --threshold 20

Suite JSON format:
    {
        "name": "My Suite",
        "benchmarks": [
            {
                "name": "read KB entry",
                "command": "python3 smart_read.py KB-0008 --fields title",
                "expect_exit": 0,
                "max_time_ms": 500,
                "max_tokens": 200
            }
        ]
    }
"""

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "storage" / "scripts"

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Approximate token count: ~4 chars per token."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Core benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(command: str, cwd: str = None, runs: int = 3,
                  warmup: int = 0, timeout: int = 30) -> dict:
    """Run a command multiple times and collect metrics.

    Returns dict with: command, runs, timings, output_chars, output_tokens,
    exit_code, stdout_sample, stderr_sample, stats (min/max/mean/median).
    """
    if cwd is None:
        cwd = str(REPO_ROOT)

    timings = []
    last_stdout = ""
    last_stderr = ""
    last_exit = None
    peak_mem_kb = 0

    # Warmup runs (not measured)
    for _ in range(warmup):
        try:
            subprocess.run(
                command, shell=True, cwd=cwd, capture_output=True,
                text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            pass

    # Measured runs
    for i in range(max(1, runs)):
        start = time.perf_counter()
        try:
            result = subprocess.run(
                command, shell=True, cwd=cwd, capture_output=True,
                text=True, timeout=timeout
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            timings.append(elapsed_ms)
            last_stdout = result.stdout
            last_stderr = result.stderr
            last_exit = result.returncode

            # Try to get peak memory from /proc if available
            try:
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                peak_mem_kb = max(peak_mem_kb, usage.ru_maxrss)
            except Exception:
                pass

        except subprocess.TimeoutExpired:
            elapsed_ms = timeout * 1000
            timings.append(elapsed_ms)
            last_exit = -1
            last_stderr = f"TIMEOUT after {timeout}s"

    # Compute stats
    sorted_t = sorted(timings)
    n = len(sorted_t)
    stats = {
        "min_ms": round(sorted_t[0], 2),
        "max_ms": round(sorted_t[-1], 2),
        "mean_ms": round(sum(sorted_t) / n, 2),
        "median_ms": round(sorted_t[n // 2], 2),
    }
    if n > 1:
        mean = stats["mean_ms"]
        variance = sum((t - mean) ** 2 for t in sorted_t) / (n - 1)
        stats["stddev_ms"] = round(variance ** 0.5, 2)

    output_chars = len(last_stdout)
    output_tokens = estimate_tokens(last_stdout)

    return {
        "command": command,
        "runs": runs,
        "timings_ms": [round(t, 2) for t in timings],
        "stats": stats,
        "output_chars": output_chars,
        "output_tokens": output_tokens,
        "exit_code": last_exit,
        "stdout_sample": last_stdout[:500] if len(last_stdout) > 500 else last_stdout,
        "stderr_sample": last_stderr[:300] if len(last_stderr) > 300 else last_stderr,
        "peak_memory_kb": peak_mem_kb if peak_mem_kb > 0 else None,
    }


# ---------------------------------------------------------------------------
# Comparative benchmark (A/B)
# ---------------------------------------------------------------------------

def compare_benchmarks(old_cmd: str, new_cmd: str, runs: int = 3,
                       cwd: str = None) -> dict:
    """Run two commands and compute relative improvement."""
    old = run_benchmark(old_cmd, cwd=cwd, runs=runs)
    new = run_benchmark(new_cmd, cwd=cwd, runs=runs)

    old_tokens = old["output_tokens"]
    new_tokens = new["output_tokens"]
    old_time = old["stats"]["median_ms"]
    new_time = new["stats"]["median_ms"]

    token_reduction = 0.0
    if old_tokens > 0:
        token_reduction = round((1 - new_tokens / old_tokens) * 100, 1)

    time_change = 0.0
    if old_time > 0:
        time_change = round((new_time - old_time) / old_time * 100, 1)

    return {
        "old": old,
        "new": new,
        "comparison": {
            "token_reduction_pct": token_reduction,
            "old_tokens": old_tokens,
            "new_tokens": new_tokens,
            "speedup_factor": round(old_tokens / max(1, new_tokens), 1),
            "time_change_pct": time_change,
            "old_median_ms": old_time,
            "new_median_ms": new_time,
        }
    }


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

def run_suite(suite_path: str, runs: int = 3) -> dict:
    """Run all benchmarks defined in a suite JSON file."""
    with open(suite_path) as f:
        suite = json.load(f)

    results = []
    passed = 0
    failed = 0

    for bench in suite.get("benchmarks", []):
        name = bench.get("name", bench["command"])
        cmd = bench["command"]
        cwd = bench.get("cwd", str(REPO_ROOT))

        result = run_benchmark(cmd, cwd=cwd, runs=bench.get("runs", runs),
                               timeout=bench.get("timeout", 30))
        result["name"] = name

        # Check assertions
        issues = []
        if "expect_exit" in bench and result["exit_code"] != bench["expect_exit"]:
            issues.append(f"exit_code={result['exit_code']}, expected={bench['expect_exit']}")
        if "max_time_ms" in bench and result["stats"]["median_ms"] > bench["max_time_ms"]:
            issues.append(f"median={result['stats']['median_ms']}ms > max={bench['max_time_ms']}ms")
        if "max_tokens" in bench and result["output_tokens"] > bench["max_tokens"]:
            issues.append(f"tokens={result['output_tokens']} > max={bench['max_tokens']}")
        if "min_tokens" in bench and result["output_tokens"] < bench["min_tokens"]:
            issues.append(f"tokens={result['output_tokens']} < min={bench['min_tokens']}")

        result["issues"] = issues
        result["passed"] = len(issues) == 0
        if result["passed"]:
            passed += 1
        else:
            failed += 1
        results.append(result)

    return {
        "suite": suite.get("name", os.path.basename(suite_path)),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Regression checker
# ---------------------------------------------------------------------------

def check_regression(baseline_path: str, threshold_pct: float = 20.0,
                     runs: int = 3) -> dict:
    """Re-run benchmarks from a baseline and check for regressions.

    A regression is when output tokens increase or time increases
    beyond the threshold percentage.
    """
    with open(baseline_path) as f:
        baseline = json.load(f)

    regressions = []
    ok = []

    for entry in baseline.get("results", baseline.get("benchmarks", [])):
        cmd = entry.get("command", "")
        if not cmd:
            continue

        name = entry.get("name", cmd)
        current = run_benchmark(cmd, runs=runs)

        old_tokens = entry.get("output_tokens", entry.get("new_tokens", 0))
        new_tokens = current["output_tokens"]

        if old_tokens > 0:
            change_pct = (new_tokens - old_tokens) / old_tokens * 100
        else:
            change_pct = 0

        result = {
            "name": name,
            "command": cmd,
            "baseline_tokens": old_tokens,
            "current_tokens": new_tokens,
            "change_pct": round(change_pct, 1),
            "current_median_ms": current["stats"]["median_ms"],
            "exit_code": current["exit_code"],
        }

        if change_pct > threshold_pct:
            result["status"] = "REGRESSION"
            regressions.append(result)
        else:
            result["status"] = "OK"
            ok.append(result)

    return {
        "baseline": baseline_path,
        "threshold_pct": threshold_pct,
        "regressions": len(regressions),
        "ok": len(ok),
        "details": regressions + ok,
    }


# ---------------------------------------------------------------------------
# Auto-benchmark (built-in profiles for known tools)
# ---------------------------------------------------------------------------

AUTO_PROFILES = {
    "smart_read": {
        "name": "smart_read benchmarks",
        "benchmarks": [
            {"name": "read selective fields", "command": "python3 storage/scripts/smart_read.py KB-0008 --fields title,tags,summary", "expect_exit": 0, "max_tokens": 200},
            {"name": "read summary only", "command": "python3 storage/scripts/smart_read.py KB-0008 --summary", "expect_exit": 0, "max_tokens": 300},
            {"name": "read json format", "command": "python3 storage/scripts/smart_read.py KB-0008 --fields title,tags --format json", "expect_exit": 0, "max_tokens": 150},
            {"name": "invalid ID handling", "command": "python3 storage/scripts/smart_read.py KB-9999 --fields title", "expect_exit": 1, "max_tokens": 100},
        ]
    },
    "smart_search": {
        "name": "smart_search benchmarks",
        "benchmarks": [
            {"name": "search database", "command": "python3 storage/scripts/smart_search.py database --types kb --max-results 5", "expect_exit": 0, "max_tokens": 500},
            {"name": "search coordination", "command": "python3 storage/scripts/smart_search.py coordination --types kb,sop --max-results 3", "expect_exit": 0, "max_tokens": 400},
            {"name": "search with token budget", "command": "python3 storage/scripts/smart_search.py database --types kb --max-tokens 200", "expect_exit": 0, "max_tokens": 200},
            {"name": "no results query", "command": "python3 storage/scripts/smart_search.py xyznonexistent --types kb", "expect_exit": 0, "max_tokens": 50},
        ]
    },
    "query_kb": {
        "name": "query_kb benchmarks",
        "benchmarks": [
            {"name": "summary", "command": "python3 storage/scripts/query_kb.py summary", "expect_exit": 0, "max_tokens": 300},
            {"name": "list KB csv", "command": "python3 storage/scripts/query_kb.py list KB --format csv", "expect_exit": 0, "max_tokens": 1000},
            {"name": "search database", "command": "python3 storage/scripts/query_kb.py search database --type KB --limit 5", "expect_exit": 0, "max_tokens": 300},
            {"name": "read entry", "command": "python3 storage/scripts/query_kb.py read KB-0008 --fields title,summary,tags", "expect_exit": 0, "max_tokens": 200},
            {"name": "tags", "command": "python3 storage/scripts/query_kb.py tags --type KB", "expect_exit": 0, "max_tokens": 2000},
            {"name": "related", "command": "python3 storage/scripts/query_kb.py related KB-0005", "expect_exit": 0, "max_tokens": 1000},
            {"name": "invalid ID", "command": "python3 storage/scripts/query_kb.py read KB-9999", "expect_exit": 1, "max_tokens": 50},
        ]
    },
    "smart_write": {
        "name": "smart_write benchmarks",
        "benchmarks": [
            {"name": "dry-run KB", "command": "echo 'benchmark test' | python3 storage/scripts/smart_write.py --type kb --title 'Benchmark Test' --category testing --tags benchmark,test --dry-run", "expect_exit": 0, "max_tokens": 300},
            {"name": "dry-run team", "command": "echo 'team log test' | python3 storage/scripts/smart_write.py --type team --title 'Benchmark Team Log' --dry-run", "expect_exit": 0, "max_tokens": 300},
        ]
    },
    "validate_naming": {
        "name": "validate_naming benchmarks",
        "benchmarks": [
            {"name": "full repo scan", "command": "python3 storage/scripts/validate_naming.py", "expect_exit": 0, "max_time_ms": 2000},
            {"name": "json output", "command": "python3 storage/scripts/validate_naming.py --json", "expect_exit": 0, "max_time_ms": 2000},
        ]
    },
    "kb_validator": {
        "name": "kb_validator benchmarks",
        "benchmarks": [
            {"name": "validate all", "command": "python3 storage/scripts/kb_validator.py", "expect_exit": 0, "max_time_ms": 3000},
        ]
    },
}


def run_auto(tool_name: str, runs: int = 3) -> dict:
    """Run built-in benchmark profile for a known tool."""
    if tool_name not in AUTO_PROFILES:
        return {"error": f"No auto profile for '{tool_name}'. Available: {', '.join(sorted(AUTO_PROFILES))}"}

    profile = AUTO_PROFILES[tool_name]
    results = []
    passed = 0
    failed = 0

    for bench in profile["benchmarks"]:
        result = run_benchmark(bench["command"], runs=runs,
                               timeout=bench.get("timeout", 30))
        result["name"] = bench["name"]

        issues = []
        if "expect_exit" in bench and result["exit_code"] != bench["expect_exit"]:
            issues.append(f"exit_code={result['exit_code']}, expected={bench['expect_exit']}")
        if "max_time_ms" in bench and result["stats"]["median_ms"] > bench["max_time_ms"]:
            issues.append(f"median={result['stats']['median_ms']}ms > max={bench['max_time_ms']}ms")
        if "max_tokens" in bench and result["output_tokens"] > bench["max_tokens"]:
            issues.append(f"tokens={result['output_tokens']} > max={bench['max_tokens']}")

        result["issues"] = issues
        result["passed"] = len(issues) == 0
        if result["passed"]:
            passed += 1
        else:
            failed += 1
        results.append(result)

    return {
        "suite": profile["name"],
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_human(data: dict, verbose: bool = False) -> str:
    """Format benchmark results for human reading."""
    lines = []

    # Suite results
    if "suite" in data and "results" in data:
        lines.append(f"Suite: {data['suite']}")
        lines.append(f"Total: {data['total']}  Passed: {data['passed']}  Failed: {data['failed']}")
        lines.append("-" * 70)
        for r in data["results"]:
            status = "PASS" if r.get("passed", True) else "FAIL"
            name = r.get("name", r.get("command", "?"))
            ms = r.get("stats", {}).get("median_ms", 0)
            tokens = r.get("output_tokens", 0)
            lines.append(f"  [{status}] {name}")
            lines.append(f"         time={ms}ms  tokens={tokens}  exit={r.get('exit_code', '?')}")
            if r.get("issues"):
                for issue in r["issues"]:
                    lines.append(f"         ! {issue}")
        lines.append("-" * 70)
        return "\n".join(lines)

    # Comparison results
    if "comparison" in data:
        c = data["comparison"]
        lines.append("Comparison Results:")
        lines.append(f"  Token reduction: {c['token_reduction_pct']}%  ({c['old_tokens']} -> {c['new_tokens']})")
        lines.append(f"  Speedup factor:  {c['speedup_factor']}x")
        lines.append(f"  Time change:     {c['time_change_pct']}%  ({c['old_median_ms']}ms -> {c['new_median_ms']}ms)")
        if verbose:
            lines.append(f"\n  Old: {data['old']['command']}")
            lines.append(f"  New: {data['new']['command']}")
        return "\n".join(lines)

    # Regression results
    if "regressions" in data and "details" in data:
        lines.append(f"Regression Check (threshold: {data['threshold_pct']}%)")
        lines.append(f"  Regressions: {data['regressions']}  OK: {data['ok']}")
        for d in data["details"]:
            status = d["status"]
            lines.append(f"  [{status}] {d['name']}: {d['baseline_tokens']} -> {d['current_tokens']} ({d['change_pct']:+.1f}%)")
        return "\n".join(lines)

    # Single benchmark
    if "command" in data and "stats" in data:
        lines.append(f"Command: {data['command']}")
        lines.append(f"Runs: {data['runs']}  Exit: {data['exit_code']}")
        s = data["stats"]
        lines.append(f"Time: min={s['min_ms']}ms  median={s['median_ms']}ms  max={s['max_ms']}ms  mean={s['mean_ms']}ms")
        lines.append(f"Output: {data['output_chars']} chars  {data['output_tokens']} tokens")
        if data.get("peak_memory_kb"):
            lines.append(f"Peak memory: {data['peak_memory_kb']} KB")
        if verbose and data.get("stdout_sample"):
            lines.append(f"Stdout sample:\n{data['stdout_sample']}")
        return "\n".join(lines)

    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generic benchmark harness for repository scripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s run "python3 storage/scripts/smart_read.py KB-0008 --fields title"
  %(prog)s suite benchmarks.json
  %(prog)s compare --old "cat file.md" --new "python3 smart_read.py KB-0001 --summary"
  %(prog)s auto smart_read
  %(prog)s auto --list
  %(prog)s check baseline.json --threshold 15
""")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # run
    p_run = sub.add_parser("run", help="Benchmark a single command")
    p_run.add_argument("command", help="Command to benchmark")
    p_run.add_argument("--runs", type=int, default=3, help="Number of runs (default: 3)")
    p_run.add_argument("--warmup", type=int, default=0, help="Warmup runs before measurement")
    p_run.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")

    # suite
    p_suite = sub.add_parser("suite", help="Run a benchmark suite from JSON")
    p_suite.add_argument("file", help="Suite definition JSON file")
    p_suite.add_argument("--runs", type=int, default=3, help="Default runs per benchmark")

    # compare
    p_cmp = sub.add_parser("compare", help="A/B comparison of two commands")
    p_cmp.add_argument("--old", required=True, help="Baseline command")
    p_cmp.add_argument("--new", required=True, help="New command")
    p_cmp.add_argument("--runs", type=int, default=3, help="Runs per command")

    # auto
    p_auto = sub.add_parser("auto", help="Run built-in benchmark profile for a tool")
    p_auto.add_argument("tool", nargs="?", help="Tool name (e.g. smart_read, query_kb)")
    p_auto.add_argument("--list", action="store_true", help="List available auto profiles")
    p_auto.add_argument("--runs", type=int, default=3, help="Runs per benchmark")

    # check
    p_check = sub.add_parser("check", help="Regression check against baseline")
    p_check.add_argument("baseline", help="Baseline results JSON file")
    p_check.add_argument("--threshold", type=float, default=20.0, help="Regression threshold %%")
    p_check.add_argument("--runs", type=int, default=3, help="Runs per benchmark")

    # Global options
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    parser.add_argument("--output", "-o", help="Write results to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Dispatch
    if args.subcommand == "run":
        data = run_benchmark(args.command, runs=args.runs,
                             warmup=args.warmup, timeout=args.timeout)
    elif args.subcommand == "suite":
        data = run_suite(args.file, runs=args.runs)
    elif args.subcommand == "compare":
        data = compare_benchmarks(args.old, args.new, runs=args.runs)
    elif args.subcommand == "auto":
        if args.list or not args.tool:
            profiles = sorted(AUTO_PROFILES.keys())
            print("Available auto profiles:")
            for p in profiles:
                info = AUTO_PROFILES[p]
                print(f"  {p:20s} ({len(info['benchmarks'])} benchmarks)")
            sys.exit(0)
        data = run_auto(args.tool, runs=args.runs)
    elif args.subcommand == "check":
        data = check_regression(args.baseline, threshold_pct=args.threshold,
                                runs=args.runs)
    else:
        parser.print_help()
        sys.exit(1)

    # Output
    if args.json:
        output = json.dumps(data, indent=2)
    else:
        output = format_human(data, verbose=args.verbose)

    if hasattr(args, "output") and args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
        print(f"Results written to {args.output}")
    else:
        print(output)

    # Exit code for CI: non-zero if failures or regressions
    if data.get("failed", 0) > 0 or data.get("regressions", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
