#!/usr/bin/env python3
"""Finding Deduplicator - Detects and merges duplicate findings across team outputs.

Reads findings from multiple team output files (JSON/JSONL), detects duplicates
using fuzzy matching (difflib.SequenceMatcher), merges duplicate findings keeping
the most complete version, and outputs a deduplicated findings file.

Usage:
    python finding_dedup.py [OPTIONS] FILE [FILE...]
    python finding_dedup.py --dir /path/to/findings/
    python finding_dedup.py -o deduped.json file1.json file2.json

Options:
    -o, --output FILE       Output file (default: stdout)
    -t, --threshold FLOAT   Similarity threshold 0.0-1.0 (default: 0.75)
    -d, --dir DIR           Scan directory for .json/.jsonl files
    --dry-run               Show duplicates without merging
    --verbose               Show detailed matching info
    -h, --help              Show this help message
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any


def load_findings_from_file(filepath: str) -> list[dict]:
    """Load findings from a JSON or JSONL file.

    Supports:
    - JSON array of findings
    - JSON object with a 'findings' key
    - JSONL (one JSON object per line)
    """
    findings = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        if not content:
            return []

        # Try JSON first
        try:
            data = json.loads(content)
            if isinstance(data, list):
                findings = data
            elif isinstance(data, dict):
                # Look for findings in common keys
                for key in ('findings', 'results', 'entries', 'data', 'items'):
                    if key in data and isinstance(data[key], list):
                        findings = data[key]
                        break
                else:
                    # Single finding object
                    findings = [data]
            return findings
        except json.JSONDecodeError:
            pass

        # Try JSONL
        for line in content.split('\n'):
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        findings.append(obj)
                except json.JSONDecodeError:
                    continue

    except (IOError, OSError) as e:
        print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)

    return findings


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    return ' '.join(text.lower().split())


def extract_text_fingerprint(finding: dict) -> str:
    """Extract a text fingerprint from a finding for comparison."""
    parts = []
    # Priority fields for matching
    for key in ('title', 'name', 'summary', 'finding', 'description',
                'content', 'text', 'key_finding', 'insight'):
        if key in finding and finding[key]:
            parts.append(normalize_text(str(finding[key])))

    if not parts:
        # Fallback: use all string values
        for v in finding.values():
            if isinstance(v, str) and len(v) > 10:
                parts.append(normalize_text(v))

    return ' '.join(parts)


def similarity(text_a: str, text_b: str) -> float:
    """Compute similarity ratio between two texts using SequenceMatcher."""
    if not text_a or not text_b:
        return 0.0
    return SequenceMatcher(None, text_a, text_b).ratio()


def completeness_score(finding: dict) -> int:
    """Score a finding by how complete it is (number of non-empty fields)."""
    score = 0
    for v in finding.values():
        if v is None:
            continue
        if isinstance(v, str) and len(v) > 0:
            score += len(v)  # Longer content = more complete
        elif isinstance(v, list) and len(v) > 0:
            score += len(v) * 10
        elif isinstance(v, dict) and len(v) > 0:
            score += len(v) * 10
        elif isinstance(v, (int, float)):
            score += 1
    return score


def merge_findings(primary: dict, secondary: dict) -> dict:
    """Merge two findings, keeping the most complete version as base.

    Fills in any missing fields from the secondary finding.
    """
    # Start with the more complete finding
    if completeness_score(secondary) > completeness_score(primary):
        primary, secondary = secondary, primary

    merged = dict(primary)

    # Fill in missing fields from secondary
    for key, value in secondary.items():
        if key not in merged or merged[key] is None or merged[key] == '':
            merged[key] = value
        elif isinstance(merged[key], list) and isinstance(value, list):
            # Merge lists, dedup strings
            existing = set(str(x) for x in merged[key])
            for item in value:
                if str(item) not in existing:
                    merged[key].append(item)
                    existing.add(str(item))
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            # Merge dicts recursively (one level)
            for k, v in value.items():
                if k not in merged[key] or merged[key][k] is None:
                    merged[key][k] = v

    # Track merge metadata
    sources = merged.get('_sources', [])
    for f in (primary, secondary):
        src = f.get('_source_file') or f.get('team') or f.get('source', '')
        if src and src not in sources:
            sources.append(src)
    if sources:
        merged['_sources'] = sources
    merged['_merged'] = True

    return merged


def find_duplicates(findings: list[dict], threshold: float = 0.75,
                    verbose: bool = False) -> list[list[int]]:
    """Find groups of duplicate findings.

    Returns list of groups, where each group is a list of indices
    into the findings list.
    """
    n = len(findings)
    fingerprints = [extract_text_fingerprint(f) for f in findings]

    # Track which findings are already in a group
    assigned = set()
    groups = []

    for i in range(n):
        if i in assigned or not fingerprints[i]:
            continue

        group = [i]
        assigned.add(i)

        for j in range(i + 1, n):
            if j in assigned or not fingerprints[j]:
                continue

            sim = similarity(fingerprints[i], fingerprints[j])
            if sim >= threshold:
                group.append(j)
                assigned.add(j)
                if verbose:
                    print(f"  Match ({sim:.2f}): [{i}] <-> [{j}]",
                          file=sys.stderr)

        if len(group) > 1:
            groups.append(group)

    return groups


def deduplicate(findings: list[dict], threshold: float = 0.75,
                verbose: bool = False) -> tuple[list[dict], dict]:
    """Deduplicate findings and return merged results plus stats.

    Returns:
        (deduplicated_findings, stats_dict)
    """
    if not findings:
        return [], {"total_input": 0, "total_output": 0, "duplicates_found": 0,
                    "groups": 0}

    groups = find_duplicates(findings, threshold, verbose)

    # Track which indices are consumed by merging
    consumed = set()
    merged_results = []

    for group in groups:
        # Merge all findings in the group
        base = findings[group[0]]
        for idx in group[1:]:
            base = merge_findings(base, findings[idx])
        merged_results.append(base)
        consumed.update(group)

    # Add non-duplicate findings
    unique = []
    for i, f in enumerate(findings):
        if i not in consumed:
            unique.append(f)

    output = merged_results + unique

    stats = {
        "total_input": len(findings),
        "total_output": len(output),
        "duplicates_found": len(findings) - len(output),
        "groups": len(groups),
        "group_sizes": [len(g) for g in groups],
    }

    return output, stats


def scan_directory(dirpath: str) -> list[str]:
    """Scan directory for JSON/JSONL files."""
    files = []
    for name in sorted(os.listdir(dirpath)):
        if name.endswith(('.json', '.jsonl')):
            files.append(os.path.join(dirpath, name))
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Finding Deduplicator - Detect and merge duplicate findings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('files', nargs='*', help='Input finding files (JSON/JSONL)')
    parser.add_argument('-o', '--output', help='Output file (default: stdout)')
    parser.add_argument('-t', '--threshold', type=float, default=0.75,
                        help='Similarity threshold 0.0-1.0 (default: 0.75)')
    parser.add_argument('-d', '--dir', help='Scan directory for finding files')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show duplicates without merging')
    parser.add_argument('--verbose', action='store_true',
                        help='Show detailed matching info')

    args = parser.parse_args()

    # Collect input files
    input_files = list(args.files)
    if args.dir:
        input_files.extend(scan_directory(args.dir))

    if not input_files:
        parser.error("No input files specified. Use positional args or --dir.")

    # Load all findings
    all_findings = []
    for filepath in input_files:
        findings = load_findings_from_file(filepath)
        # Tag each finding with its source file
        for f in findings:
            if '_source_file' not in f:
                f['_source_file'] = os.path.basename(filepath)
        all_findings.extend(findings)
        if args.verbose:
            print(f"Loaded {len(findings)} findings from {filepath}",
                  file=sys.stderr)

    print(f"Total findings loaded: {len(all_findings)}", file=sys.stderr)

    if args.dry_run:
        groups = find_duplicates(all_findings, args.threshold, args.verbose)
        print(f"\nFound {len(groups)} duplicate groups:", file=sys.stderr)
        for i, group in enumerate(groups):
            print(f"\n--- Group {i+1} ({len(group)} items) ---", file=sys.stderr)
            for idx in group:
                f = all_findings[idx]
                title = (f.get('title') or f.get('name') or
                         f.get('finding', '')[:80])
                src = f.get('_source_file', '?')
                print(f"  [{idx}] {src}: {title}", file=sys.stderr)
        return

    # Deduplicate
    results, stats = deduplicate(all_findings, args.threshold, args.verbose)

    output = {
        "metadata": {
            "tool": "finding_dedup.py",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "threshold": args.threshold,
            "input_files": [os.path.basename(f) for f in input_files],
            "stats": stats,
        },
        "findings": results,
    }

    output_json = json.dumps(output, indent=2, default=str)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output_json)
        print(f"Wrote {len(results)} findings to {args.output}", file=sys.stderr)
    else:
        print(output_json)

    print(f"Stats: {stats['total_input']} input -> {stats['total_output']} output "
          f"({stats['duplicates_found']} duplicates in {stats['groups']} groups)",
          file=sys.stderr)


if __name__ == '__main__':
    main()
