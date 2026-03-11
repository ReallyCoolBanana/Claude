#!/usr/bin/env python3
"""Quality Scorer - Scores and ranks gathered data entries on multiple dimensions.

Scores data entries on:
- Source authority (official docs > academic > blogs > forums)
- Freshness (how recent the data is)
- Completeness (percentage of fields filled)
- Citation count (references, links, corroborations)

Configurable scoring weights via CLI or JSON config file.

Usage:
    python quality_scorer.py [OPTIONS] FILE [FILE...]
    python quality_scorer.py -o scored.json findings.json
    python quality_scorer.py --weights '{"authority":0.4,"freshness":0.2}' data.json
    python quality_scorer.py --config weights.json --min-score 0.5 data.json

Options:
    -o, --output FILE       Output file (default: stdout)
    --weights JSON          JSON string of scoring weights
    --config FILE           JSON config file for weights and settings
    --min-score FLOAT       Minimum score threshold to include (0.0-1.0)
    --top N                 Only output top N results
    --verbose               Show scoring breakdown
    -h, --help              Show this help message
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any


# Default scoring weights (must sum to ~1.0)
DEFAULT_WEIGHTS = {
    "authority": 0.30,
    "freshness": 0.20,
    "completeness": 0.30,
    "citations": 0.20,
}

# Source authority tiers (score 0.0-1.0)
AUTHORITY_PATTERNS = {
    # Tier 1: Official documentation and standards (1.0)
    1.0: [
        r'\.gov\b', r'rfc\d+', r'official\s+doc', r'specification',
        r'standard', r'ieee', r'w3c', r'ietf', r'iso\s?\d+',
    ],
    # Tier 2: Academic and peer-reviewed (0.85)
    0.85: [
        r'arxiv', r'doi\.org', r'pubmed', r'scholar', r'journal',
        r'proceedings', r'conference', r'peer.review', r'academic',
        r'university', r'\.edu\b', r'semantic\s*scholar', r'openalex',
    ],
    # Tier 3: Major tech companies and established sources (0.7)
    0.7: [
        r'github\.com', r'stackoverflow', r'microsoft', r'google',
        r'mozilla', r'apple', r'aws', r'documentation',
    ],
    # Tier 4: Wikipedia and curated sources (0.6)
    0.6: [
        r'wikipedia', r'wikidata', r'encyclopedia', r'handbook',
    ],
    # Tier 5: News and reporting (0.5)
    0.5: [
        r'reuters', r'bbc', r'guardian', r'nytimes', r'news',
        r'report', r'analysis',
    ],
    # Tier 6: Blogs and forums (0.3)
    0.3: [
        r'blog', r'medium\.com', r'dev\.to', r'tutorial',
        r'hackernews', r'hacker\s*news', r'reddit', r'forum',
    ],
    # Tier 7: Unknown/unverified (0.1)
    0.1: [
        r'unknown', r'unverified', r'rumor',
    ],
}


def score_authority(entry: dict) -> float:
    """Score source authority based on source URLs, types, and metadata."""
    # Collect all text hints about the source
    hints = []
    for key in ('source', 'url', 'source_type', 'source_url', 'origin',
                'publisher', 'domain', 'type'):
        val = entry.get(key, '')
        if isinstance(val, str):
            hints.append(val.lower())
        elif isinstance(val, list):
            hints.extend(str(v).lower() for v in val)

    if not hints:
        return 0.4  # Default: moderate authority for unknown sources

    combined = ' '.join(hints)

    # Check against authority tiers (return highest match)
    for score, patterns in sorted(AUTHORITY_PATTERNS.items(), reverse=True):
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return score

    return 0.4  # Default


def score_freshness(entry: dict) -> float:
    """Score freshness based on dates in the entry.

    More recent = higher score. Scores decay over time.
    """
    now = datetime.now(timezone.utc)

    # Look for date fields
    date_str = None
    for key in ('date', 'published', 'published_date', 'timestamp',
                'created', 'updated', 'last_updated', 'fetched_at'):
        val = entry.get(key)
        if val:
            date_str = str(val)
            break

    if not date_str:
        return 0.5  # Default: moderate freshness for undated entries

    # Try parsing various date formats
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d',
                '%d/%m/%Y', '%B %d, %Y', '%b %d, %Y', '%Y'):
        try:
            dt = datetime.strptime(date_str.strip()[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days_old = (now - dt).days
            if days_old < 0:
                days_old = 0

            # Scoring: <7 days = 1.0, decay to 0.1 over ~3 years
            if days_old <= 7:
                return 1.0
            elif days_old <= 30:
                return 0.9
            elif days_old <= 90:
                return 0.8
            elif days_old <= 180:
                return 0.7
            elif days_old <= 365:
                return 0.6
            elif days_old <= 730:
                return 0.4
            else:
                return max(0.1, 0.3 - (days_old - 730) / 3650)
        except (ValueError, OverflowError):
            continue

    return 0.5  # Couldn't parse date


def score_completeness(entry: dict) -> float:
    """Score completeness based on percentage of fields filled meaningfully."""
    if not entry:
        return 0.0

    total_fields = len(entry)
    filled = 0

    for key, value in entry.items():
        if key.startswith('_'):
            total_fields -= 1  # Don't count internal fields
            continue
        if value is None or value == '' or value == []:
            continue
        if isinstance(value, str) and len(value) >= 2:
            filled += 1
        elif isinstance(value, list) and len(value) > 0:
            filled += 1
        elif isinstance(value, dict) and len(value) > 0:
            filled += 1
        elif isinstance(value, (int, float, bool)):
            filled += 1

    if total_fields == 0:
        return 0.0

    base_score = filled / total_fields

    # Bonus for having key fields
    key_fields = ('title', 'description', 'source', 'date', 'content',
                  'summary', 'url')
    key_filled = sum(1 for k in key_fields if entry.get(k))
    key_bonus = min(0.2, key_filled * 0.03)

    return min(1.0, base_score + key_bonus)


def score_citations(entry: dict) -> float:
    """Score based on citation count, references, and corroboration."""
    score = 0.0

    # Direct citation count
    citation_count = 0
    for key in ('citation_count', 'citations', 'cited_by', 'cite_count',
                'reference_count'):
        val = entry.get(key)
        if isinstance(val, (int, float)):
            citation_count = max(citation_count, int(val))
        elif isinstance(val, list):
            citation_count = max(citation_count, len(val))

    if citation_count > 100:
        score = 1.0
    elif citation_count > 50:
        score = 0.85
    elif citation_count > 20:
        score = 0.7
    elif citation_count > 5:
        score = 0.5
    elif citation_count > 0:
        score = 0.3

    # References/links bonus
    refs = entry.get('references', entry.get('links', entry.get('urls', [])))
    if isinstance(refs, list) and len(refs) > 0:
        score = max(score, min(0.6, len(refs) * 0.1))

    # Corroboration bonus
    corr = entry.get('corroboration_count', entry.get('corroborations', 0))
    if isinstance(corr, (int, float)) and corr > 0:
        score = max(score, min(0.8, corr * 0.2))

    # Sources list bonus
    sources = entry.get('_sources', [])
    if isinstance(sources, list) and len(sources) > 1:
        score = max(score, min(0.7, len(sources) * 0.15))

    return score if score > 0 else 0.2  # Default minimal score


def compute_quality_score(entry: dict, weights: dict | None = None,
                          verbose: bool = False) -> dict:
    """Compute overall quality score for a data entry.

    Returns dict with individual dimension scores and weighted total.
    """
    w = weights or DEFAULT_WEIGHTS

    scores = {
        "authority": score_authority(entry),
        "freshness": score_freshness(entry),
        "completeness": score_completeness(entry),
        "citations": score_citations(entry),
    }

    total = sum(scores[dim] * w.get(dim, 0) for dim in scores)

    result = {
        "total": round(total, 4),
        "dimensions": {k: round(v, 4) for k, v in scores.items()},
        "weights_used": w,
    }

    if verbose:
        title = entry.get('title', entry.get('name', '<untitled>'))
        parts = [f"{k}={v:.2f}*{w.get(k,0):.2f}" for k, v in scores.items()]
        print(f"  {title[:60]}: {' + '.join(parts)} = {total:.3f}",
              file=sys.stderr)

    return result


def load_entries(filepath: str) -> list[dict]:
    """Load data entries from JSON or JSONL file."""
    entries = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        if not content:
            return []

        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                for key in ('findings', 'results', 'entries', 'data', 'items'):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return [data]
        except json.JSONDecodeError:
            pass

        for line in content.split('\n'):
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError) as e:
        print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)
    return entries


def main():
    parser = argparse.ArgumentParser(
        description="Quality Scorer - Score and rank gathered data entries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('files', nargs='+', help='Input data files (JSON/JSONL)')
    parser.add_argument('-o', '--output', help='Output file (default: stdout)')
    parser.add_argument('--weights', help='JSON string of scoring weights')
    parser.add_argument('--config', help='JSON config file for weights')
    parser.add_argument('--min-score', type=float, default=0.0,
                        help='Minimum score threshold (0.0-1.0)')
    parser.add_argument('--top', type=int, default=0,
                        help='Only output top N results')
    parser.add_argument('--verbose', action='store_true',
                        help='Show scoring breakdown')

    args = parser.parse_args()

    # Load weights
    weights = dict(DEFAULT_WEIGHTS)
    if args.config:
        try:
            with open(args.config, 'r') as f:
                config = json.load(f)
            if 'weights' in config:
                weights.update(config['weights'])
            else:
                weights.update(config)
        except (IOError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load config: {e}", file=sys.stderr)

    if args.weights:
        try:
            weights.update(json.loads(args.weights))
        except json.JSONDecodeError as e:
            print(f"Warning: Could not parse weights: {e}", file=sys.stderr)

    # Normalize weights
    total_w = sum(weights.values())
    if total_w > 0:
        weights = {k: v / total_w for k, v in weights.items()}

    # Load entries
    all_entries = []
    for filepath in args.files:
        entries = load_entries(filepath)
        for e in entries:
            if '_source_file' not in e:
                e['_source_file'] = os.path.basename(filepath)
        all_entries.extend(entries)

    print(f"Scoring {len(all_entries)} entries...", file=sys.stderr)

    # Score each entry
    scored = []
    for entry in all_entries:
        score_info = compute_quality_score(entry, weights, args.verbose)
        scored_entry = dict(entry)
        scored_entry['_quality_score'] = score_info
        scored.append(scored_entry)

    # Sort by total score descending
    scored.sort(key=lambda x: x['_quality_score']['total'], reverse=True)

    # Apply filters
    if args.min_score > 0:
        scored = [e for e in scored if e['_quality_score']['total'] >= args.min_score]

    if args.top > 0:
        scored = scored[:args.top]

    output = {
        "metadata": {
            "tool": "quality_scorer.py",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "weights": weights,
            "total_input": len(all_entries),
            "total_output": len(scored),
            "min_score": args.min_score,
        },
        "scored_entries": scored,
    }

    output_json = json.dumps(output, indent=2, default=str)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output_json)
        print(f"Wrote {len(scored)} scored entries to {args.output}",
              file=sys.stderr)
    else:
        print(output_json)

    # Summary stats
    if scored:
        scores = [e['_quality_score']['total'] for e in scored]
        avg = sum(scores) / len(scores)
        print(f"Scores: min={min(scores):.3f} avg={avg:.3f} max={max(scores):.3f}",
              file=sys.stderr)


if __name__ == '__main__':
    main()
