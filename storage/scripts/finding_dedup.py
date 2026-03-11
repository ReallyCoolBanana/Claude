#!/usr/bin/env python3
"""Finding Deduplicator - Detects and merges duplicate findings across team outputs.

Reads findings from multiple team output files (JSON/JSONL), detects duplicates
using fuzzy matching (difflib.SequenceMatcher), merges duplicates keeping the
most complete version, and outputs a deduplicated findings file.

Usage:
    python finding_dedup.py <input_files...> [-o output.json] [-t threshold]
    python finding_dedup.py findings1.json findings2.json -o merged.json
    python finding_dedup.py --dir /path/to/findings/ -t 0.75

Standard library only. No external dependencies.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_findings_from_file(filepath: str) -> List[Dict[str, Any]]:
    """Load findings from a JSON or JSONL file.

    Supports:
    - JSON array of findings
    - JSON object with a 'findings' key
    - JSONL (one JSON object per line)

    Args:
        filepath: Path to the input file.

    Returns:
        List of finding dictionaries, each tagged with source_file.
    """
    findings = []
    path = Path(filepath)

    if not path.exists():
        print(f"Warning: File not found: {filepath}", file=sys.stderr)
        return findings

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return findings

    # Try JSON first
    try:
        data = json.loads(content)
        if isinstance(data, list):
            findings = data
        elif isinstance(data, dict):
            # Look for findings in common keys
            for key in ("findings", "results", "entries", "items", "data"):
                if key in data and isinstance(data[key], list):
                    findings = data[key]
                    break
            if not findings:
                findings = [data]
    except json.JSONDecodeError:
        # Try JSONL
        for line_num, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    findings.append(obj)
            except json.JSONDecodeError:
                print(f"Warning: Skipping malformed line {line_num} in {filepath}",
                      file=sys.stderr)

    # Tag each finding with its source file
    for f in findings:
        if "source_file" not in f:
            f["source_file"] = str(path.name)

    return findings


def get_finding_text(finding: Dict[str, Any]) -> str:
    """Extract the primary text content from a finding for comparison.

    Concatenates title, description, content, summary, and body fields.

    Args:
        finding: A finding dictionary.

    Returns:
        Concatenated text string for comparison.
    """
    parts = []
    for key in ("title", "name", "description", "content", "summary",
                "body", "text", "finding", "detail"):
        val = finding.get(key, "")
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return " ".join(parts)


def compute_similarity(text_a: str, text_b: str) -> float:
    """Compute fuzzy similarity between two text strings.

    Uses difflib.SequenceMatcher for fuzzy matching.

    Args:
        text_a: First text string.
        text_b: Second text string.

    Returns:
        Similarity ratio between 0.0 and 1.0.
    """
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    return SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()


def completeness_score(finding: Dict[str, Any]) -> int:
    """Score how complete a finding is based on filled fields.

    Args:
        finding: A finding dictionary.

    Returns:
        Integer completeness score (higher = more complete).
    """
    score = 0
    for key, value in finding.items():
        if key in ("source_file", "_dedup_cluster"):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            score += 1 + len(value) // 100  # Bonus for longer content
        elif isinstance(value, list) and len(value) > 0:
            score += len(value)
        elif isinstance(value, dict) and len(value) > 0:
            score += len(value)
        elif isinstance(value, (int, float, bool)):
            score += 1
    return score


def merge_findings(cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge a cluster of duplicate findings, keeping the most complete version.

    Starts with the most complete finding as base, then fills in any
    missing fields from less complete duplicates.

    Args:
        cluster: List of duplicate finding dictionaries.

    Returns:
        Single merged finding dictionary.
    """
    if len(cluster) == 1:
        return cluster[0].copy()

    # Sort by completeness, most complete first
    sorted_cluster = sorted(cluster, key=completeness_score, reverse=True)
    merged = sorted_cluster[0].copy()

    # Collect all source files
    sources = set()
    for f in cluster:
        src = f.get("source_file", "unknown")
        if isinstance(src, list):
            sources.update(src)
        else:
            sources.add(src)

    # Fill in missing fields from other findings
    for other in sorted_cluster[1:]:
        for key, value in other.items():
            if key in ("source_file", "_dedup_cluster"):
                continue
            if key not in merged or merged[key] is None:
                merged[key] = value
            elif isinstance(merged[key], str) and not merged[key].strip():
                merged[key] = value
            elif isinstance(merged[key], list) and isinstance(value, list):
                # Merge lists, avoiding duplicates
                existing = set(str(x) for x in merged[key])
                for item in value:
                    if str(item) not in existing:
                        merged[key].append(item)
                        existing.add(str(item))

    merged["source_file"] = sorted(sources)
    merged["_merge_count"] = len(cluster)

    return merged


def deduplicate_findings(
    findings: List[Dict[str, Any]],
    threshold: float = 0.8,
    verbose: bool = False
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Deduplicate a list of findings using fuzzy matching.

    Groups findings into clusters of duplicates, then merges each cluster.

    Args:
        findings: List of finding dictionaries.
        threshold: Similarity threshold for duplicate detection (0.0-1.0).
        verbose: Print progress and match details.

    Returns:
        Tuple of (deduplicated findings list, statistics dict).
    """
    if not findings:
        return [], {"total_input": 0, "total_output": 0, "duplicates_removed": 0}

    n = len(findings)
    texts = [get_finding_text(f) for f in findings]
    cluster_ids = list(range(n))  # Union-find parent array

    def find_root(i: int) -> int:
        while cluster_ids[i] != i:
            cluster_ids[i] = cluster_ids[cluster_ids[i]]  # Path compression
            i = cluster_ids[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find_root(i), find_root(j)
        if ri != rj:
            cluster_ids[ri] = rj

    # Compare all pairs
    comparisons = 0
    matches = 0
    for i in range(n):
        for j in range(i + 1, n):
            if find_root(i) == find_root(j):
                continue  # Already in same cluster
            sim = compute_similarity(texts[i], texts[j])
            comparisons += 1
            if sim >= threshold:
                union(i, j)
                matches += 1
                if verbose:
                    title_i = findings[i].get("title", texts[i][:50])
                    title_j = findings[j].get("title", texts[j][:50])
                    print(f"  Match ({sim:.2f}): '{title_i}' <-> '{title_j}'",
                          file=sys.stderr)

    # Build clusters
    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        root = find_root(i)
        clusters.setdefault(root, []).append(i)

    # Merge each cluster
    deduplicated = []
    for indices in clusters.values():
        cluster_findings = [findings[i] for i in indices]
        merged = merge_findings(cluster_findings)
        deduplicated.append(merged)

    stats = {
        "total_input": n,
        "total_output": len(deduplicated),
        "duplicates_removed": n - len(deduplicated),
        "clusters": len(clusters),
        "comparisons_made": comparisons,
        "matches_found": matches,
        "threshold": threshold,
        "timestamp": datetime.now().isoformat(),
    }

    return deduplicated, stats


def main():
    """CLI entry point for finding deduplication."""
    parser = argparse.ArgumentParser(
        description="Deduplicate findings across team output files using fuzzy matching."
    )
    parser.add_argument(
        "files", nargs="*",
        help="Input finding files (JSON or JSONL)"
    )
    parser.add_argument(
        "--dir", "-d",
        help="Directory to scan for finding files"
    )
    parser.add_argument(
        "--output", "-o", default="-",
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--threshold", "-t", type=float, default=0.8,
        help="Similarity threshold for duplicate detection (0.0-1.0, default: 0.8)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print progress and match details to stderr"
    )
    parser.add_argument(
        "--stats-only", action="store_true",
        help="Only output deduplication statistics"
    )

    args = parser.parse_args()

    # Collect input files
    input_files = list(args.files or [])
    if args.dir:
        dir_path = Path(args.dir)
        if dir_path.is_dir():
            for ext in ("*.json", "*.jsonl"):
                input_files.extend(str(p) for p in dir_path.glob(ext))

    if not input_files:
        parser.error("No input files specified. Use positional args or --dir.")

    # Load all findings
    all_findings = []
    for filepath in input_files:
        loaded = load_findings_from_file(filepath)
        if args.verbose:
            print(f"Loaded {len(loaded)} findings from {filepath}", file=sys.stderr)
        all_findings.extend(loaded)

    if args.verbose:
        print(f"\nTotal findings loaded: {len(all_findings)}", file=sys.stderr)
        print(f"Deduplicating with threshold: {args.threshold}...\n", file=sys.stderr)

    # Deduplicate
    deduplicated, stats = deduplicate_findings(
        all_findings, threshold=args.threshold, verbose=args.verbose
    )

    if args.verbose or args.stats_only:
        print(f"\n--- Deduplication Stats ---", file=sys.stderr)
        print(f"Input findings:     {stats['total_input']}", file=sys.stderr)
        print(f"Output findings:    {stats['total_output']}", file=sys.stderr)
        print(f"Duplicates removed: {stats['duplicates_removed']}", file=sys.stderr)
        print(f"Clusters formed:    {stats['clusters']}", file=sys.stderr)
        print(f"Comparisons made:   {stats['comparisons_made']}", file=sys.stderr)

    if args.stats_only:
        output = stats
    else:
        output = {
            "findings": deduplicated,
            "stats": stats,
            "source_files": input_files,
        }

    # Write output
    result_json = json.dumps(output, indent=2, default=str)
    if args.output == "-":
        print(result_json)
    else:
        Path(args.output).write_text(result_json, encoding="utf-8")
        if args.verbose:
            print(f"\nOutput written to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
