#!/usr/bin/env python3
"""tool_tester.py - Generic test harness for repository scripts.

Runs correctness tests against any CLI tool. Supports test suites defined
in JSON, built-in test profiles for known tools, output validation
(exit codes, stdout patterns, stderr patterns, JSON schema checks),
and machine-readable results.

Usage:
    # Run built-in tests for a specific tool
    python tool_tester.py auto smart_read

    # Run a test suite from JSON definition
    python tool_tester.py suite tests.json

    # Run a single test
    python tool_tester.py run "python3 smart_read.py KB-0008 --fields title" \\
        --expect-exit 0 --expect-stdout "title:"

    # Run all built-in tests
    python tool_tester.py auto --all

    # List available test profiles
    python tool_tester.py auto --list

Suite JSON format:
    {
        "name": "My Tests",
        "tests": [
            {
                "name": "basic read",
                "command": "python3 smart_read.py KB-0008 --fields title",
                "expect_exit": 0,
                "expect_stdout": ["title:"],
                "reject_stdout": ["Error"],
                "expect_stderr": [],
                "timeout": 10
            }
        ]
    }
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "storage" / "scripts"


# ---------------------------------------------------------------------------
# Core test runner
# ---------------------------------------------------------------------------

def run_test(command: str, expect_exit: int = None, expect_stdout: list = None,
             reject_stdout: list = None, expect_stderr: list = None,
             reject_stderr: list = None, expect_json: bool = False,
             expect_json_keys: list = None, expect_csv: bool = False,
             min_lines: int = None, max_lines: int = None,
             cwd: str = None, timeout: int = 30, env_vars: dict = None) -> dict:
    """Run a command and validate its output.

    Returns dict with: command, passed, exit_code, failures, stdout, stderr, duration_ms.
    """
    if cwd is None:
        cwd = str(REPO_ROOT)

    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)

    failures = []

    start = time.perf_counter()
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True,
            text=True, timeout=timeout, env=env
        )
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        duration_ms = timeout * 1000
        stdout = ""
        stderr = ""
        exit_code = -1
        failures.append(f"TIMEOUT after {timeout}s")

    # Check exit code
    if expect_exit is not None and exit_code != expect_exit:
        failures.append(f"exit_code={exit_code}, expected={expect_exit}")

    # Check stdout patterns
    if expect_stdout:
        for pattern in expect_stdout:
            if not re.search(pattern, stdout, re.IGNORECASE):
                failures.append(f"stdout missing pattern: {pattern!r}")

    if reject_stdout:
        for pattern in reject_stdout:
            if re.search(pattern, stdout, re.IGNORECASE):
                failures.append(f"stdout contains rejected pattern: {pattern!r}")

    # Check stderr patterns
    if expect_stderr:
        for pattern in expect_stderr:
            if not re.search(pattern, stderr, re.IGNORECASE):
                failures.append(f"stderr missing pattern: {pattern!r}")

    if reject_stderr:
        for pattern in reject_stderr:
            if re.search(pattern, stderr, re.IGNORECASE):
                failures.append(f"stderr contains rejected pattern: {pattern!r}")

    # Check JSON validity
    if expect_json:
        try:
            parsed = json.loads(stdout)
            if expect_json_keys:
                if isinstance(parsed, dict):
                    for key in expect_json_keys:
                        if key not in parsed:
                            failures.append(f"JSON missing key: {key!r}")
                elif isinstance(parsed, list) and len(parsed) > 0:
                    for key in expect_json_keys:
                        if key not in parsed[0]:
                            failures.append(f"JSON[0] missing key: {key!r}")
        except json.JSONDecodeError as e:
            failures.append(f"stdout is not valid JSON: {e}")

    # Check CSV validity
    if expect_csv:
        lines = stdout.strip().split("\n")
        if len(lines) < 1:
            failures.append("CSV output is empty")
        elif len(lines) >= 2:
            header_cols = len(lines[0].split(","))
            for i, line in enumerate(lines[1:], 2):
                cols = len(line.split(","))
                if cols != header_cols:
                    failures.append(f"CSV line {i}: {cols} columns, expected {header_cols}")
                    break

    # Check line counts
    line_count = len(stdout.strip().split("\n")) if stdout.strip() else 0
    if min_lines is not None and line_count < min_lines:
        failures.append(f"output has {line_count} lines, expected >= {min_lines}")
    if max_lines is not None and line_count > max_lines:
        failures.append(f"output has {line_count} lines, expected <= {max_lines}")

    return {
        "command": command,
        "passed": len(failures) == 0,
        "exit_code": exit_code,
        "failures": failures,
        "stdout_lines": line_count,
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "duration_ms": duration_ms,
        "stdout_sample": stdout[:300] if len(stdout) > 300 else stdout,
        "stderr_sample": stderr[:200] if len(stderr) > 200 else stderr,
    }


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

def run_suite(suite_path: str) -> dict:
    """Run all tests defined in a suite JSON file."""
    with open(suite_path) as f:
        suite = json.load(f)

    return _run_test_list(suite.get("name", os.path.basename(suite_path)),
                          suite.get("tests", []))


def _run_test_list(suite_name: str, tests: list) -> dict:
    """Run a list of test definitions and aggregate results."""
    results = []
    passed = 0
    failed = 0

    for test in tests:
        name = test.get("name", test.get("command", "?"))
        result = run_test(
            command=test["command"],
            expect_exit=test.get("expect_exit"),
            expect_stdout=test.get("expect_stdout"),
            reject_stdout=test.get("reject_stdout"),
            expect_stderr=test.get("expect_stderr"),
            reject_stderr=test.get("reject_stderr"),
            expect_json=test.get("expect_json", False),
            expect_json_keys=test.get("expect_json_keys"),
            expect_csv=test.get("expect_csv", False),
            min_lines=test.get("min_lines"),
            max_lines=test.get("max_lines"),
            cwd=test.get("cwd", str(REPO_ROOT)),
            timeout=test.get("timeout", 30),
            env_vars=test.get("env_vars"),
        )
        result["name"] = name
        if result["passed"]:
            passed += 1
        else:
            failed += 1
        results.append(result)

    return {
        "suite": suite_name,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Built-in test profiles
# ---------------------------------------------------------------------------

AUTO_PROFILES = {
    "smart_read": {
        "name": "smart_read tests",
        "tests": [
            # Basic functionality
            {"name": "read specific fields", "command": "python3 storage/scripts/smart_read.py KB-0008 --fields title,tags,summary", "expect_exit": 0, "expect_stdout": ["title:", "tags:"], "reject_stdout": ["Error", "Traceback"]},
            {"name": "read summary mode", "command": "python3 storage/scripts/smart_read.py KB-0008 --summary", "expect_exit": 0, "expect_stdout": ["summary"], "reject_stdout": ["Error"]},
            {"name": "json output format", "command": "python3 storage/scripts/smart_read.py KB-0008 --fields title,tags --format json", "expect_exit": 0, "expect_json": True, "expect_json_keys": ["title", "tags"]},
            # Error handling
            {"name": "invalid ID returns error", "command": "python3 storage/scripts/smart_read.py KB-9999 --fields title", "expect_exit": 1, "expect_stderr": ["Error"], "reject_stderr": ["Traceback"]},
            {"name": "no args shows help", "command": "python3 storage/scripts/smart_read.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
            # Edge cases
            {"name": "single field extraction", "command": "python3 storage/scripts/smart_read.py KB-0001 --fields title", "expect_exit": 0, "expect_stdout": ["title:"]},
            {"name": "all common fields", "command": "python3 storage/scripts/smart_read.py KB-0001 --fields id,title,category,status,date,tags", "expect_exit": 0, "min_lines": 3},
        ]
    },
    "smart_search": {
        "name": "smart_search tests",
        "tests": [
            {"name": "basic search", "command": "python3 storage/scripts/smart_search.py database --types kb --max-results 5", "expect_exit": 0, "reject_stdout": ["Traceback"], "min_lines": 1},
            {"name": "multi-type search", "command": "python3 storage/scripts/smart_search.py coordination --types kb,sop --max-results 3", "expect_exit": 0, "reject_stdout": ["Traceback"]},
            {"name": "json output", "command": "python3 storage/scripts/smart_search.py database --types kb --format json --max-results 3", "expect_exit": 0, "expect_json": True},
            {"name": "csv output", "command": "python3 storage/scripts/smart_search.py database --types kb --format csv --max-results 3", "expect_exit": 0, "min_lines": 1},
            {"name": "token budget", "command": "python3 storage/scripts/smart_search.py database --types kb --max-tokens 500", "expect_exit": 0},
            {"name": "no results graceful", "command": "python3 storage/scripts/smart_search.py xyznonexistent123 --types kb", "expect_exit": 0, "expect_stderr": ["0 results"]},
            {"name": "help flag", "command": "python3 storage/scripts/smart_search.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
        ]
    },
    "query_kb": {
        "name": "query_kb tests",
        "tests": [
            # Summary
            {"name": "summary", "command": "python3 storage/scripts/query_kb.py summary", "expect_exit": 0, "expect_stdout": ["KB", "entry_count"], "reject_stdout": ["Traceback"]},
            {"name": "summary by type", "command": "python3 storage/scripts/query_kb.py summary KB", "expect_exit": 0, "reject_stdout": ["Traceback"]},
            # List
            {"name": "list KB default", "command": "python3 storage/scripts/query_kb.py list KB", "expect_exit": 0, "min_lines": 2},
            {"name": "list KB csv", "command": "python3 storage/scripts/query_kb.py list KB --format csv", "expect_exit": 0, "min_lines": 2},
            {"name": "list KB json", "command": "python3 storage/scripts/query_kb.py list KB --json", "expect_exit": 0, "expect_json": True},
            {"name": "list with fields", "command": "python3 storage/scripts/query_kb.py list KB --fields id,title,created_date", "expect_exit": 0, "min_lines": 2},
            # Search
            {"name": "search basic", "command": "python3 storage/scripts/query_kb.py search database --type KB --limit 5", "expect_exit": 0, "reject_stdout": ["Traceback"]},
            {"name": "search json", "command": "python3 storage/scripts/query_kb.py search database --json", "expect_exit": 0, "expect_json": True},
            {"name": "search no results", "command": "python3 storage/scripts/query_kb.py search xyznonexistent123abc", "expect_exit": 0},
            # Read
            {"name": "read entry", "command": "python3 storage/scripts/query_kb.py read KB-0008 --fields title,summary,tags", "expect_exit": 0, "expect_stdout": ["title"], "reject_stdout": ["Traceback"]},
            {"name": "read invalid ID", "command": "python3 storage/scripts/query_kb.py read KB-9999", "expect_exit": 1, "expect_stderr": ["Error|not found"]},
            # Tags
            {"name": "tags", "command": "python3 storage/scripts/query_kb.py tags --type KB", "expect_exit": 0, "min_lines": 3},
            # Related
            {"name": "related", "command": "python3 storage/scripts/query_kb.py related KB-0005", "expect_exit": 0, "reject_stdout": ["Traceback"]},
            {"name": "related invalid", "command": "python3 storage/scripts/query_kb.py related KB-9999", "expect_exit": 1, "expect_stderr": ["Error|not found"]},
        ]
    },
    "smart_write": {
        "name": "smart_write tests",
        "tests": [
            {"name": "dry-run KB entry", "command": "echo 'test content' | python3 storage/scripts/smart_write.py --type kb --title 'Test Entry' --category testing --tags test --dry-run", "expect_exit": 0, "expect_stdout": ["DRY RUN|dry.run|would create|preview"], "reject_stdout": ["Traceback"]},
            {"name": "dry-run team log", "command": "echo 'team test' | python3 storage/scripts/smart_write.py --type team --title 'Test Log' --dry-run", "expect_exit": 0, "reject_stdout": ["Traceback"]},
            {"name": "help flag", "command": "python3 storage/scripts/smart_write.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
            {"name": "missing required args", "command": "python3 storage/scripts/smart_write.py --type kb 2>&1 || true", "expect_exit": 0},
        ]
    },
    "validate_naming": {
        "name": "validate_naming tests",
        "tests": [
            {"name": "default run", "command": "python3 storage/scripts/validate_naming.py", "reject_stdout": ["Traceback"], "expect_stdout": ["SUMMARY"]},
            {"name": "json output", "command": "python3 storage/scripts/validate_naming.py --json", "expect_json": True, "expect_json_keys": ["files_checked", "violations"]},
            {"name": "help flag", "command": "python3 storage/scripts/validate_naming.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
        ]
    },
    "import_to_db": {
        "name": "import_to_db tests",
        "tests": [
            {"name": "help flag", "command": "python3 storage/scripts/import_to_db.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
        ]
    },
    "auto_index": {
        "name": "auto_index tests",
        "tests": [
            {"name": "dry-run mode", "command": "python3 storage/scripts/auto_index.py --dry-run", "expect_exit": 0, "reject_stdout": ["Traceback"]},
            {"name": "help flag", "command": "python3 storage/scripts/auto_index.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
        ]
    },
    "kb_validator": {
        "name": "kb_validator tests",
        "tests": [
            {"name": "validate all entries", "command": "python3 storage/scripts/kb_validator.py", "reject_stdout": ["Traceback"]},
            {"name": "help flag", "command": "python3 storage/scripts/kb_validator.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
        ]
    },
    "cross_ref_checker": {
        "name": "cross_ref_checker tests",
        "tests": [
            {"name": "default run", "command": "python3 storage/scripts/cross_ref_checker.py", "expect_exit": 0, "reject_stdout": ["Traceback"]},
        ]
    },
    "quality_scorer": {
        "name": "quality_scorer tests",
        "tests": [
            {"name": "help flag", "command": "python3 storage/scripts/quality_scorer.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
        ]
    },
    "finding_dedup": {
        "name": "finding_dedup tests",
        "tests": [
            {"name": "help flag", "command": "python3 storage/scripts/finding_dedup.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
        ]
    },
    "pattern_analyzer": {
        "name": "pattern_analyzer tests",
        "tests": [
            {"name": "help flag", "command": "python3 storage/scripts/pattern_analyzer.py --help", "expect_exit": 0, "expect_stdout": ["usage"]},
            {"name": "scan self", "command": "python3 storage/scripts/pattern_analyzer.py scan --path storage/scripts/pattern_analyzer.py", "expect_exit": 0, "reject_stdout": ["Traceback"]},
        ]
    },
}


def run_auto(tool_name: str) -> dict:
    """Run built-in test profile for a known tool."""
    if tool_name not in AUTO_PROFILES:
        return {"error": f"No test profile for '{tool_name}'. Available: {', '.join(sorted(AUTO_PROFILES))}"}

    profile = AUTO_PROFILES[tool_name]
    return _run_test_list(profile["name"], profile["tests"])


def run_all() -> dict:
    """Run all built-in test profiles."""
    all_results = []
    total_passed = 0
    total_failed = 0

    for tool_name in sorted(AUTO_PROFILES):
        result = run_auto(tool_name)
        if "error" not in result:
            all_results.append(result)
            total_passed += result["passed"]
            total_failed += result["failed"]

    return {
        "suite": "ALL tools",
        "tools_tested": len(all_results),
        "total": total_passed + total_failed,
        "passed": total_passed,
        "failed": total_failed,
        "tool_results": all_results,
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_human(data: dict, verbose: bool = False) -> str:
    """Format test results for human reading."""
    lines = []

    # All-tools summary
    if "tool_results" in data:
        lines.append(f"=== {data['suite']} ===")
        lines.append(f"Tools: {data['tools_tested']}  Tests: {data['total']}  "
                      f"Passed: {data['passed']}  Failed: {data['failed']}")
        lines.append("=" * 70)
        for tr in data["tool_results"]:
            status = "PASS" if tr["failed"] == 0 else "FAIL"
            lines.append(f"\n[{status}] {tr['suite']} ({tr['passed']}/{tr['total']})")
            if verbose or tr["failed"] > 0:
                for r in tr["results"]:
                    mark = "+" if r["passed"] else "X"
                    lines.append(f"  [{mark}] {r.get('name', r['command'][:60])}")
                    if r["failures"]:
                        for f in r["failures"]:
                            lines.append(f"       ! {f}")
        lines.append("\n" + "=" * 70)
        return "\n".join(lines)

    # Single suite
    if "suite" in data and "results" in data:
        status_icon = "PASS" if data["failed"] == 0 else "FAIL"
        lines.append(f"[{status_icon}] {data['suite']}: {data['passed']}/{data['total']} passed")
        lines.append("-" * 60)
        for r in data["results"]:
            mark = "+" if r["passed"] else "X"
            name = r.get("name", r["command"][:60])
            ms = r.get("duration_ms", 0)
            lines.append(f"  [{mark}] {name} ({ms}ms)")
            if r["failures"]:
                for f in r["failures"]:
                    lines.append(f"       ! {f}")
                if verbose and r.get("stdout_sample"):
                    lines.append(f"       stdout: {r['stdout_sample'][:100]}")
                if verbose and r.get("stderr_sample"):
                    lines.append(f"       stderr: {r['stderr_sample'][:100]}")
        lines.append("-" * 60)
        return "\n".join(lines)

    # Single test
    if "command" in data:
        mark = "PASS" if data["passed"] else "FAIL"
        lines.append(f"[{mark}] {data['command']}")
        lines.append(f"  exit={data['exit_code']}  time={data['duration_ms']}ms  "
                      f"stdout={data['stdout_chars']}chars  stderr={data['stderr_chars']}chars")
        if data["failures"]:
            for f in data["failures"]:
                lines.append(f"  ! {f}")
        if verbose and data.get("stdout_sample"):
            lines.append(f"  stdout: {data['stdout_sample'][:200]}")
        return "\n".join(lines)

    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generic test harness for repository scripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s auto smart_read
  %(prog)s auto --all
  %(prog)s auto --list
  %(prog)s suite tests.json
  %(prog)s run "python3 storage/scripts/smart_read.py KB-0008 --fields title" --expect-exit 0 --expect-stdout "title:"
""")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # run
    p_run = sub.add_parser("run", help="Run a single test")
    p_run.add_argument("command", help="Command to test")
    p_run.add_argument("--expect-exit", type=int, help="Expected exit code")
    p_run.add_argument("--expect-stdout", action="append", help="Pattern that must appear in stdout")
    p_run.add_argument("--reject-stdout", action="append", help="Pattern that must NOT appear in stdout")
    p_run.add_argument("--expect-json", action="store_true", help="Stdout must be valid JSON")
    p_run.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")

    # suite
    p_suite = sub.add_parser("suite", help="Run a test suite from JSON")
    p_suite.add_argument("file", help="Suite definition JSON file")

    # auto
    p_auto = sub.add_parser("auto", help="Run built-in test profile")
    p_auto.add_argument("tool", nargs="?", help="Tool name")
    p_auto.add_argument("--list", action="store_true", help="List available test profiles")
    p_auto.add_argument("--all", action="store_true", help="Run all test profiles")

    # Global options
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--output", "-o", help="Write results to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Dispatch
    if args.subcommand == "run":
        data = run_test(
            command=args.command,
            expect_exit=args.expect_exit,
            expect_stdout=args.expect_stdout,
            reject_stdout=args.reject_stdout,
            expect_json=args.expect_json,
            timeout=args.timeout,
        )
    elif args.subcommand == "suite":
        data = run_suite(args.file)
    elif args.subcommand == "auto":
        if args.list:
            profiles = sorted(AUTO_PROFILES.keys())
            print("Available test profiles:")
            for p in profiles:
                info = AUTO_PROFILES[p]
                print(f"  {p:20s} ({len(info['tests'])} tests)")
            sys.exit(0)
        if args.all:
            data = run_all()
        elif args.tool:
            data = run_auto(args.tool)
        else:
            print("Specify a tool name, --list, or --all")
            sys.exit(1)
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

    # Exit code for CI
    if data.get("failed", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
