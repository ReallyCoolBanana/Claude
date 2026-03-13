#!/usr/bin/env python3
"""Archive Formatter - Formats raw findings into knowledge-base entries.

Takes raw findings (JSON/JSONL) and formats them into properly structured
knowledge-base entries with YAML frontmatter, following the template at
knowledge-base/TEMPLATE.md. Generates properly named files in
knowledge-base/entries/.

Usage:
    python archive_formatter.py [OPTIONS] FILE [FILE...]
    python archive_formatter.py -o /path/to/kb/entries/ findings.json
    python archive_formatter.py --team TEAM-0042 --category tool-usage data.json

Options:
    -o, --output-dir DIR    Output directory (default: knowledge-base/entries/)
    --team TEAM             Team name for frontmatter
    --role ROLE             Agent role for frontmatter
    --category CAT          Category (methodology|tool-usage|debugging|optimization|integration|market-research)
    --status STATUS         Status (validated|experimental|deprecated) default: experimental
    --confidence CONF       Confidence (high|medium|low) default: medium
    --builds-on IDS         Comma-separated KB IDs this builds on
    --dry-run               Show what would be created without writing
    --id-start N            Starting KB ID number (default: auto-detect)
    -h, --help              Show this help message
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

# Repository root (relative to this script's location)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_ENTRIES_DIR = os.path.join(REPO_ROOT, 'knowledge-base', 'entries')
KB_INDEX = os.path.join(REPO_ROOT, 'knowledge-base', 'index.json')

VALID_CATEGORIES = {
    'methodology', 'tool-usage', 'debugging', 'optimization',
    'integration', 'market-research',
}
VALID_STATUSES = {'validated', 'experimental', 'deprecated'}
VALID_CONFIDENCES = {'high', 'medium', 'low'}


def get_next_kb_id(start: int | None = None) -> int:
    """Determine the next available KB ID number."""
    if start is not None:
        return start

    max_id = 0

    # Check existing entries directory
    if os.path.isdir(KB_ENTRIES_DIR):
        for name in os.listdir(KB_ENTRIES_DIR):
            match = re.match(r'KB-(\d+)', name)
            if match:
                max_id = max(max_id, int(match.group(1)))

    # Check index.json
    if os.path.isfile(KB_INDEX):
        try:
            with open(KB_INDEX, 'r') as f:
                data = json.load(f)
            for entry in data.get('entries', []):
                match = re.match(r'KB-(\d+)', entry.get('id', ''))
                if match:
                    max_id = max(max_id, int(match.group(1)))
        except (IOError, json.JSONDecodeError):
            pass

    return max_id + 1


def extract_title(finding: dict) -> str:
    """Extract a suitable title from a finding."""
    for key in ('title', 'name', 'heading', 'key_finding', 'summary'):
        val = finding.get(key)
        if isinstance(val, str) and len(val) > 3:
            # Clean and truncate
            title = val.strip().split('\n')[0][:120]
            return title

    # Fallback: use first significant text field
    for val in finding.values():
        if isinstance(val, str) and len(val) > 10:
            return val.strip().split('\n')[0][:80]

    return "Untitled Finding"


def extract_tags(finding: dict) -> list[str]:
    """Extract tags from a finding."""
    tags = []

    # Direct tag fields
    for key in ('tags', 'keywords', 'topics', 'labels'):
        val = finding.get(key)
        if isinstance(val, list):
            tags.extend(str(t).strip().lower() for t in val if t)
        elif isinstance(val, str):
            tags.extend(t.strip().lower() for t in val.split(',') if t.strip())

    # Extract from category
    cat = finding.get('category', finding.get('type', ''))
    if isinstance(cat, str) and cat:
        tags.append(cat.lower().strip())

    # Deduplicate
    seen = set()
    unique = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)

    return unique[:10]  # Cap at 10 tags


def extract_section(finding: dict, section_keys: list[str],
                    fallback: str = "") -> str:
    """Extract content for a KB section from finding fields."""
    for key in section_keys:
        val = finding.get(key)
        if isinstance(val, str) and len(val) > 5:
            return val.strip()
        elif isinstance(val, list):
            items = [str(v) for v in val if v]
            if items:
                return '\n'.join(f"- {item}" for item in items)
        elif isinstance(val, dict):
            items = [f"- **{k}**: {v}" for k, v in val.items() if v]
            if items:
                return '\n'.join(items)
    return fallback


def infer_category(finding: dict) -> str:
    """Try to infer the KB category from finding content."""
    text = ' '.join(str(v) for v in finding.values() if isinstance(v, str)).lower()

    if any(w in text for w in ('debug', 'error', 'fix', 'bug', 'traceback')):
        return 'debugging'
    if any(w in text for w in ('tool', 'library', 'framework', 'api', 'sdk')):
        return 'tool-usage'
    if any(w in text for w in ('method', 'approach', 'technique', 'algorithm')):
        return 'methodology'
    if any(w in text for w in ('perf', 'speed', 'optim', 'cache', 'fast')):
        return 'optimization'
    if any(w in text for w in ('integrat', 'connect', 'interop', 'bridge')):
        return 'integration'
    if any(w in text for w in ('market', 'stock', 'invest', 'financ', 'trading')):
        return 'market-research'

    return 'methodology'  # Default


def format_kb_entry(finding: dict, kb_id: str, team: str, role: str,
                    category: str, status: str, confidence: str,
                    builds_on: list[str]) -> str:
    """Format a finding into a knowledge-base Markdown entry with YAML frontmatter."""
    title = extract_title(finding)
    tags = extract_tags(finding)
    date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Auto-detect category if not specified
    if not category:
        category = finding.get('category', infer_category(finding))
    if category not in VALID_CATEGORIES:
        category = 'methodology'

    # Build frontmatter
    tags_str = '[' + ', '.join(tags) + ']' if tags else '[]'
    builds_on_str = '[' + ', '.join(builds_on) + ']' if builds_on else '[]'

    frontmatter = f"""---
id: {kb_id}
date: {date}
team: {team}
role: {role}
category: {category}
tags: {tags_str}
status: {status}
confidence: {confidence}
builds_on: {builds_on_str}
---"""

    # Extract sections
    context = extract_section(finding,
                              ['context', 'background', 'problem', 'question',
                               'motivation'],
                              "Finding from automated data gathering.")

    method = extract_section(finding,
                             ['method', 'approach', 'methodology', 'technique',
                              'process', 'how'],
                             "Data was collected and processed using automated tools.")

    result = extract_section(finding,
                             ['result', 'results', 'finding', 'findings',
                              'conclusion', 'outcome', 'answer', 'content',
                              'summary', 'description', 'abstract', 'text'],
                             "See original finding data.")

    lessons = extract_section(finding,
                              ['lessons', 'lessons_learned', 'takeaways',
                               'insights', 'implications'],
                              "Refer to the result section for key insights.")

    recommendations = extract_section(finding,
                                      ['recommendations', 'next_steps',
                                       'action_items', 'suggestions',
                                       'future_work'],
                                      "Further investigation may be warranted.")

    # Compose entry
    entry = f"""{frontmatter}
# {title}

## Context
{context}

## Method
{method}

## Result
{result}

## Lessons Learned
{lessons}

## Recommendations
{recommendations}
"""

    # Add source attribution if available
    source = finding.get('source', finding.get('url', finding.get('source_url', '')))
    if source:
        entry += f"\n## Source\n{source}\n"

    return entry


def sanitize_filename(title: str) -> str:
    """Create a safe filename from a title."""
    # Remove special chars, replace spaces with hyphens
    name = re.sub(r'[^\w\s-]', '', title.lower())
    name = re.sub(r'[\s_]+', '-', name.strip())
    name = re.sub(r'-+', '-', name)
    return name[:60].rstrip('-')


def load_findings(filepath: str) -> list[dict]:
    """Load findings from JSON or JSONL file."""
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
                for key in ('findings', 'results', 'entries', 'data',
                            'scored_entries', 'items'):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return [data]
        except json.JSONDecodeError:
            pass

        entries = []
        for line in content.split('\n'):
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries
    except (IOError, OSError) as e:
        print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Archive Formatter - Format findings into KB entries",
    )
    parser.add_argument('files', nargs='+', help='Input finding files')
    parser.add_argument('-o', '--output-dir', default=KB_ENTRIES_DIR,
                        help=f'Output directory (default: {KB_ENTRIES_DIR})')
    parser.add_argument('--team', default='PROG-TEAM-3',
                        help='Team name for frontmatter')
    parser.add_argument('--role', default='archive-formatter',
                        help='Agent role for frontmatter')
    parser.add_argument('--category', default='',
                        help='Category for all entries')
    parser.add_argument('--status', default='experimental',
                        choices=list(VALID_STATUSES))
    parser.add_argument('--confidence', default='medium',
                        choices=list(VALID_CONFIDENCES))
    parser.add_argument('--builds-on', default='',
                        help='Comma-separated KB IDs this builds on')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be created')
    parser.add_argument('--id-start', type=int, default=None,
                        help='Starting KB ID number')

    args = parser.parse_args()
    builds_on = [x.strip() for x in args.builds_on.split(',') if x.strip()]

    # Load all findings
    all_findings = []
    for filepath in args.files:
        all_findings.extend(load_findings(filepath))

    if not all_findings:
        print("No findings to format.", file=sys.stderr)
        return

    print(f"Formatting {len(all_findings)} findings into KB entries...",
          file=sys.stderr)

    # Ensure output directory exists
    if not args.dry_run:
        os.makedirs(args.output_dir, exist_ok=True)

    next_id = get_next_kb_id(args.id_start)
    created_files = []

    for i, finding in enumerate(all_findings):
        kb_id = f"KB-{next_id + i:04d}"
        title = extract_title(finding)
        filename_base = sanitize_filename(title)
        filename = f"{kb_id}-{filename_base}.md"
        filepath = os.path.join(args.output_dir, filename)

        entry_text = format_kb_entry(
            finding, kb_id, args.team, args.role,
            args.category, args.status, args.confidence, builds_on,
        )

        if args.dry_run:
            print(f"\n{'='*60}")
            print(f"Would create: {filepath}")
            print(f"{'='*60}")
            print(entry_text[:500])
            if len(entry_text) > 500:
                print(f"  ... ({len(entry_text)} chars total)")
        else:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(entry_text)
            created_files.append({"id": kb_id, "filename": filename,
                                  "title": title})
            print(f"  Created: {filename}", file=sys.stderr)

    if not args.dry_run:
        # Write manifest
        manifest = {
            "tool": "archive_formatter.py",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "team": args.team,
            "entries_created": created_files,
            "output_dir": args.output_dir,
        }
        manifest_path = os.path.join(args.output_dir,
                                     f"_manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Processed {len(all_findings)} findings -> "
          f"{len(all_findings)} KB entries", file=sys.stderr)


if __name__ == '__main__':
    main()
